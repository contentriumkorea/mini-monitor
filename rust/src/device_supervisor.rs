//! Retry a disconnected display on a bounded cadence without changing other outputs.
use crate::device_worker::{DeviceWorker, WorkerStatus};
use std::{
    io,
    time::{Duration, Instant},
};
pub struct DeviceSupervisor<F> {
    factory: F,
    worker: Option<DeviceWorker>,
    interval: Duration,
    retry_at: Option<Instant>,
    generation: u64,
    stopped: bool,
}
impl<F: FnMut() -> io::Result<DeviceWorker>> DeviceSupervisor<F> {
    /// Factory must return promptly; port discovery and opening belong in the worker.
    pub fn new(interval: Duration, factory: F) -> io::Result<Self> {
        if !(Duration::from_secs(1)..=Duration::from_secs(60)).contains(&interval) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid reconnect interval",
            ));
        }
        Ok(Self {
            factory,
            worker: None,
            interval,
            retry_at: None,
            generation: 0,
            stopped: false,
        })
    }
    pub fn tick(&mut self, now: Instant) {
        if self.stopped {
            return;
        }
        if self
            .worker
            .as_ref()
            .is_some_and(|w| matches!(w.status(), WorkerStatus::Error | WorkerStatus::Stopped))
        {
            self.worker.take();
            self.retry_at = now.checked_add(self.interval);
        }
        if self.worker.is_some() || self.retry_at.is_some_and(|retry| now < retry) {
            return;
        }
        match (self.factory)() {
            Ok(worker) => {
                self.generation = self.generation.saturating_add(1);
                self.worker = Some(worker);
                self.retry_at = None;
            }
            Err(_) => {
                self.retry_at = now.checked_add(self.interval);
            }
        }
    }
    pub fn status(&self) -> Option<WorkerStatus> {
        self.worker.as_ref().map(DeviceWorker::status)
    }
    pub fn generation(&self) -> u64 {
        self.generation
    }
    pub fn worker(&self) -> Option<&DeviceWorker> {
        self.worker.as_ref()
    }
    pub fn stop(&mut self) {
        self.stopped = true;
        if let Some(worker) = self.worker.take() {
            worker.request_stop();
        }
    }
}
impl<F> Drop for DeviceSupervisor<F> {
    fn drop(&mut self) {
        if let Some(worker) = self.worker.take() {
            worker.request_stop();
        }
    }
}
