//! Isolated CPU/RAM worker benchmark; not representative of the complete application.
use mini_monitor_native::sensor_worker::SensorWorker;
use std::{thread, time::Duration};

fn main() -> std::io::Result<()> {
    let use_nvidia = std::env::args().nth(1).as_deref() == Some("nvidia");
    let worker = if use_nvidia {
        SensorWorker::start_with_nvidia(Duration::from_secs(1), 0)?
    } else {
        SensorWorker::start(Duration::from_secs(1))?
    };
    thread::sleep(Duration::from_secs(45));
    let snapshot = worker.snapshot();
    if snapshot.cpu_percent.is_none()
        || snapshot.memory.is_none()
        || (use_nvidia && snapshot.gpu.is_none())
    {
        return Err(std::io::Error::other("sensor reading unavailable"));
    }
    Ok(())
}
