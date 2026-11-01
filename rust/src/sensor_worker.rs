//! Bounded sensor worker shared by desktop and USB outputs.
use crate::sensors::{cpu_times, cpu_usage, memory_snapshot, MemorySnapshot};
use std::{
    io,
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::Duration,
};

#[derive(Clone, Copy, Debug, Default)]
pub struct SensorSnapshot {
    pub cpu_percent: Option<f64>,
    pub memory: Option<MemorySnapshot>,
}

pub struct SensorWorker {
    data: Arc<Mutex<SensorSnapshot>>,
    stop: Arc<(Mutex<bool>, Condvar)>,
    thread: Option<JoinHandle<()>>,
}

impl SensorWorker {
    pub fn start(interval: Duration) -> io::Result<Self> {
        if interval < Duration::from_millis(250) || interval > Duration::from_secs(2) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "sensor interval must be 250–2000 ms",
            ));
        }
        let data = Arc::new(Mutex::new(SensorSnapshot::default()));
        let stop = Arc::new((Mutex::new(false), Condvar::new()));
        let result = Arc::clone(&data);
        let signal = Arc::clone(&stop);
        let thread = thread::Builder::new()
            .name("mini-monitor-sensors".into())
            .spawn(move || {
                let mut previous = None;
                loop {
                    let current = cpu_times().ok();
                    let cpu_percent = previous
                        .zip(current)
                        .and_then(|(before, after)| cpu_usage(before, after));
                    previous = current;
                    let snapshot = SensorSnapshot {
                        cpu_percent,
                        memory: memory_snapshot().ok(),
                    };
                    *result.lock().unwrap_or_else(|e| e.into_inner()) = snapshot;
                    let (lock, wake) = &*signal;
                    let should_stop = lock.lock().unwrap_or_else(|e| e.into_inner());
                    let (should_stop, _) = wake
                        .wait_timeout_while(should_stop, interval, |stopped| !*stopped)
                        .unwrap_or_else(|e| e.into_inner());
                    if *should_stop {
                        break;
                    }
                }
            })?;
        Ok(Self {
            data,
            stop,
            thread: Some(thread),
        })
    }

    pub fn snapshot(&self) -> SensorSnapshot {
        *self.data.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn stop(&mut self) {
        let (lock, wake) = &*self.stop;
        *lock.lock().unwrap_or_else(|e| e.into_inner()) = true;
        wake.notify_all();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Drop for SensorWorker {
    fn drop(&mut self) {
        self.stop();
    }
}
