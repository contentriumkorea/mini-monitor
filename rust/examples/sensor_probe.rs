//! Isolated CPU/RAM worker benchmark; not representative of the complete application.
use mini_monitor_native::sensor_worker::SensorWorker;
use std::{thread, time::Duration};

fn main() -> std::io::Result<()> {
    let worker = SensorWorker::start(Duration::from_secs(1))?;
    thread::sleep(Duration::from_secs(45));
    let snapshot = worker.snapshot();
    if snapshot.cpu_percent.is_none() || snapshot.memory.is_none() {
        return Err(std::io::Error::other("sensor reading unavailable"));
    }
    Ok(())
}
