//! Bounded, off-UI-thread checks of signed stable releases.
use crate::update::{is_newer_release, UpdateHttp};
use std::{
    io,
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReleaseStatus {
    Checking,
    Current,
    Available,
    Error,
    Stopped,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReleaseSnapshot {
    pub status: ReleaseStatus,
    pub version: Option<String>,
}
struct State {
    snapshot: ReleaseSnapshot,
    due: bool,
    stopped: bool,
    last_check: Option<Instant>,
}
pub struct ReleaseWorker {
    shared: Arc<(Mutex<State>, Condvar)>,
    thread: Option<JoinHandle<()>>,
}
impl ReleaseWorker {
    /// The supplied source must return a version only after authenticating metadata.
    pub fn start<F>(current: &str, interval: Duration, mut source: F) -> io::Result<Self>
    where
        F: FnMut() -> Result<String, &'static str> + Send + 'static,
    {
        if is_newer_release(current, current).is_err()
            || interval < Duration::from_secs(60)
            || Instant::now().checked_add(interval).is_none()
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid update configuration",
            ));
        }
        let current = current.to_owned();
        let shared = Arc::new((
            Mutex::new(State {
                snapshot: ReleaseSnapshot {
                    status: ReleaseStatus::Checking,
                    version: None,
                },
                due: true,
                stopped: false,
                last_check: None,
            }),
            Condvar::new(),
        ));
        let other = shared.clone();
        let thread = thread::Builder::new()
            .name("mini-monitor-release".into())
            .spawn(move || {
                let (lock, wake) = &*other;
                loop {
                    {
                        let mut state = lock.lock().unwrap_or_else(|e| e.into_inner());
                        while !state.stopped {
                            let delay = state.last_check.map(|at| at.elapsed()).unwrap_or(interval);
                            if state.due && delay >= Duration::from_secs(10) {
                                break;
                            }
                            let wait = if state.due {
                                Duration::from_secs(10).saturating_sub(delay)
                            } else {
                                interval.saturating_sub(delay)
                            };
                            let (next, result) = wake
                                .wait_timeout(state, wait)
                                .unwrap_or_else(|e| e.into_inner());
                            state = next;
                            if result.timed_out() && !state.due {
                                state.due = true;
                            }
                        }
                        if state.stopped {
                            break;
                        }
                        state.due = false;
                        state.last_check = Some(Instant::now());
                        state.snapshot = ReleaseSnapshot {
                            status: ReleaseStatus::Checking,
                            version: None,
                        };
                    }
                    let result = source().and_then(|version| {
                        is_newer_release(&current, &version).map(|newer| (version, newer))
                    });
                    let mut state = lock.lock().unwrap_or_else(|e| e.into_inner());
                    if state.stopped {
                        break;
                    }
                    state.snapshot = match result {
                        Ok((version, true)) => ReleaseSnapshot {
                            status: ReleaseStatus::Available,
                            version: Some(version),
                        },
                        Ok((_, false)) => ReleaseSnapshot {
                            status: ReleaseStatus::Current,
                            version: None,
                        },
                        Err(_) => ReleaseSnapshot {
                            status: ReleaseStatus::Error,
                            version: None,
                        },
                    };
                }
            })?;
        Ok(Self {
            shared,
            thread: Some(thread),
        })
    }
    pub fn start_http(current: &str, interval: Duration) -> io::Result<Self> {
        let client = UpdateHttp::new().map_err(io::Error::other)?;
        Self::start(current, interval, move || {
            let release = client.latest()?;
            let verified = client.fetch_verified_assets(&release)?;
            Ok(verified.version.clone())
        })
    }
    pub fn snapshot(&self) -> ReleaseSnapshot {
        self.shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .snapshot
            .clone()
    }
    pub fn request_check(&self) -> bool {
        let mut state = self.shared.0.lock().unwrap_or_else(|e| e.into_inner());
        if state.stopped || state.due {
            return false;
        }
        state.due = true;
        self.shared.1.notify_one();
        true
    }
    pub fn request_stop(&self) {
        let mut state = self.shared.0.lock().unwrap_or_else(|e| e.into_inner());
        state.stopped = true;
        state.snapshot = ReleaseSnapshot {
            status: ReleaseStatus::Stopped,
            version: None,
        };
        self.shared.1.notify_all();
    }
    /// Explicit joining may wait for network; call off the UI thread.
    pub fn stop(&mut self) {
        self.request_stop();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
impl Drop for ReleaseWorker {
    fn drop(&mut self) {
        self.request_stop();
    }
}
