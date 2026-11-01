//! Bounded sensor worker shared by desktop and USB outputs.
use crate::gpu::{GpuSnapshot, NvmlGpu};
use crate::sensors::{cpu_times, cpu_usage, memory_snapshot, MemorySnapshot};
use std::{
    io,
    sync::{Arc, Condvar, Mutex},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Clone, Debug, Default)]
pub struct SensorSnapshot {
    pub cpu_percent: Option<f64>,
    pub memory: Option<MemorySnapshot>,
    pub gpu: Option<GpuSnapshot>,
}

pub struct SensorWorker {
    data: Arc<Mutex<SensorSnapshot>>,
    stop: Arc<(Mutex<bool>, Condvar)>,
    thread: Option<JoinHandle<()>>,
}

impl SensorWorker {
    pub fn start(interval: Duration) -> io::Result<Self> {
        Self::start_inner(interval, None)
    }

    /// Select the NVIDIA adapter explicitly; do not silently guess on multi-GPU PCs.
    pub fn start_with_nvidia(interval: Duration, index: u32) -> io::Result<Self> {
        Self::start_inner(interval, Some(index))
    }

    fn start_inner(interval: Duration, gpu_index: Option<u32>) -> io::Result<Self> {
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
                let gpu = gpu_index.and_then(|index| NvmlGpu::open(index).ok());
                let mut gpu_snapshot = None;
                let mut gpu_due = Instant::now();
                loop {
                    let current = cpu_times().ok();
                    let cpu_percent = previous
                        .zip(current)
                        .and_then(|(before, after)| cpu_usage(before, after));
                    previous = current;
                    let now = Instant::now();
                    if now >= gpu_due {
                        gpu_snapshot = gpu.as_ref().map(NvmlGpu::sample);
                        gpu_due = now + Duration::from_secs(1);
                    }
                    let snapshot = SensorSnapshot {
                        cpu_percent,
                        memory: memory_snapshot().ok(),
                        gpu: gpu_snapshot.clone(),
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
        self.data.lock().unwrap_or_else(|e| e.into_inner()).clone()
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
