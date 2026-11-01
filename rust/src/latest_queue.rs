//! Single-slot queue: new display frames replace stale unsent frames.
use std::{
    sync::{Condvar, Mutex},
    time::Duration,
};

struct State<T> {
    pending: Option<T>,
    closed: bool,
}
pub struct LatestQueue<T> {
    state: Mutex<State<T>>,
    wake: Condvar,
}

impl<T> Default for LatestQueue<T> {
    fn default() -> Self {
        Self::new()
    }
}
impl<T> LatestQueue<T> {
    pub fn new() -> Self {
        Self {
            state: Mutex::new(State {
                pending: None,
                closed: false,
            }),
            wake: Condvar::new(),
        }
    }
    pub fn submit(&self, value: T) -> Result<(), T> {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.closed {
            return Err(value);
        }
        state.pending = Some(value);
        self.wake.notify_one();
        Ok(())
    }
    pub fn take_timeout(&self, duration: Duration) -> Option<T> {
        let state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let (mut state, _) = self
            .wake
            .wait_timeout_while(state, duration, |s| !s.closed && s.pending.is_none())
            .unwrap_or_else(|e| e.into_inner());
        state.pending.take()
    }
    pub fn close(&self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        state.pending = None;
        state.closed = true;
        self.wake.notify_all();
    }
}
