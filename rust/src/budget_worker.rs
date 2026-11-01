//! Bounded background costs polling. Neither credentials nor values are logged.
use crate::{
    budget::monthly_period,
    budget_cycle::{BudgetCycle, BudgetSnapshot},
    costs_http::CostsHttpClient,
};
use std::{
    io,
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use time::{Date, OffsetDateTime};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BudgetStatus {
    Pending,
    Ready,
    Error,
    Stopped,
}
struct State {
    anchor: Date,
    total: u64,
    generation: u64,
    status: BudgetStatus,
    value: Option<BudgetSnapshot>,
    due: bool,
    stopped: bool,
}
type Shared = Arc<(Mutex<State>, Condvar)>;
type Clock = Arc<dyn Fn() -> OffsetDateTime + Send + Sync>;
pub struct BudgetWorker {
    state: Shared,
    clock: Clock,
    thread: Option<JoinHandle<()>>,
}

impl BudgetWorker {
    pub fn start<F, C>(
        anchor: Date,
        total: u64,
        interval: Duration,
        mut fetch: F,
        clock: C,
    ) -> io::Result<Self>
    where
        F: FnMut(i64, i64, &dyn Fn() -> bool) -> Result<u128, &'static str> + Send + 'static,
        C: Fn() -> OffsetDateTime + Send + Sync + 'static,
    {
        BudgetCycle::new(anchor, total).map_err(io::Error::other)?;
        let clock: Clock = Arc::new(clock);
        monthly_period(anchor, clock().date()).map_err(io::Error::other)?;
        if interval < Duration::from_secs(60) || Instant::now().checked_add(interval).is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid poll interval",
            ));
        }
        let state = Arc::new((
            Mutex::new(State {
                anchor,
                total,
                generation: 0,
                status: BudgetStatus::Pending,
                value: None,
                due: true,
                stopped: false,
            }),
            Condvar::new(),
        ));
        let shared = state.clone();
        let time = clock.clone();
        let thread = thread::Builder::new()
            .name("mini-monitor-costs".into())
            .spawn(move || {
                let (lock, wake) = &*shared;
                loop {
                    let (anchor, total, generation) = {
                        let mut state = lock.lock().unwrap_or_else(|e| e.into_inner());
                        while !state.stopped && !state.due {
                            let (next, timeout) = wake
                                .wait_timeout(state, interval)
                                .unwrap_or_else(|e| e.into_inner());
                            state = next;
                            if timeout.timed_out() {
                                state.due = true;
                            }
                        }
                        if state.stopped {
                            break;
                        }
                        state.due = false;
                        state.status = BudgetStatus::Pending;
                        state.value = None;
                        (state.anchor, state.total, state.generation)
                    };
                    let now = time();
                    let result = (|| -> Result<BudgetSnapshot, &'static str> {
                        let mut cycle = BudgetCycle::new(anchor, total)?;
                        let pending = cycle.begin(now.date())?;
                        let (start, _) = monthly_period(anchor, now.date())?;
                        let start = start.midnight().assume_utc().unix_timestamp();
                        let end = now.unix_timestamp();
                        let cancelled = || {
                            let state = lock.lock().unwrap_or_else(|e| e.into_inner());
                            state.stopped || state.generation != generation
                        };
                        let amount = if end <= start {
                            0
                        } else {
                            fetch(start, end, &cancelled)?
                        };
                        cycle.complete(pending, time().date(), amount)
                    })();
                    let mut state = lock.lock().unwrap_or_else(|e| e.into_inner());
                    if state.stopped {
                        break;
                    }
                    if state.generation != generation {
                        continue;
                    }
                    match result {
                        Ok(value) => {
                            state.value = Some(value);
                            state.status = BudgetStatus::Ready;
                        }
                        Err("period changed") => {
                            state.value = None;
                            state.status = BudgetStatus::Pending;
                            state.due = true;
                        }
                        Err(_) => {
                            state.value = None;
                            state.status = BudgetStatus::Error;
                        }
                    }
                }
                let mut state = lock.lock().unwrap_or_else(|e| e.into_inner());
                state.value = None;
                state.status = BudgetStatus::Stopped;
            })?;
        Ok(Self {
            state,
            clock,
            thread: Some(thread),
        })
    }
    /// This constructs a client but does not send a request on the calling thread.
    pub fn start_http(anchor: Date, total: u64, interval: Duration, key: &str) -> io::Result<Self> {
        let client = CostsHttpClient::new(key).map_err(io::Error::other)?;
        Self::start(
            anchor,
            total,
            interval,
            move |start, end, cancelled| client.fetch_spend(start, end, cancelled),
            OffsetDateTime::now_utc,
        )
    }
    fn check_period(&self, state: &mut State) {
        let current = monthly_period(state.anchor, (self.clock)().date());
        if state.value.as_ref().is_some_and(|v| {
            !current
                .as_ref()
                .is_ok_and(|(start, end)| (*start, *end) == (v.period_start, v.period_end))
        }) {
            state.value = None;
            state.status = BudgetStatus::Pending;
            state.generation = state.generation.wrapping_add(1);
            state.due = true;
            self.state.1.notify_one();
        }
    }
    pub fn status(&self) -> BudgetStatus {
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        self.check_period(&mut s);
        s.status
    }
    pub fn snapshot(&self) -> Option<BudgetSnapshot> {
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        self.check_period(&mut s);
        s.value.clone()
    }
    pub fn reconfigure(&self, anchor: Date, total: u64) -> io::Result<()> {
        BudgetCycle::new(anchor, total).map_err(io::Error::other)?;
        monthly_period(anchor, (self.clock)().date()).map_err(io::Error::other)?;
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        if s.stopped {
            return Err(io::Error::other("worker stopped"));
        }
        let generation = s
            .generation
            .checked_add(1)
            .ok_or_else(|| io::Error::other("generation exhausted"))?;
        s.anchor = anchor;
        s.total = total;
        s.generation = generation;
        s.value = None;
        s.status = BudgetStatus::Pending;
        s.due = true;
        self.state.1.notify_one();
        Ok(())
    }
    pub fn request_stop(&self) {
        let mut s = self.state.0.lock().unwrap_or_else(|e| e.into_inner());
        s.stopped = true;
        s.value = None;
        self.state.1.notify_all();
    }
    /// May wait for a bounded HTTP operation; call off the UI thread.
    pub fn join(&mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
impl Drop for BudgetWorker {
    fn drop(&mut self) {
        self.request_stop();
    }
}
