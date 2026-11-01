#[cfg(windows)]
#[test]
fn sensor_worker_updates_and_stops_without_usb() {
    use mini_monitor_native::sensor_worker::SensorWorker;
    use std::time::{Duration, Instant};
    let mut worker = SensorWorker::start(Duration::from_millis(250)).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        let snapshot = worker.snapshot();
        if snapshot.cpu_percent.is_some() && snapshot.memory.is_some() {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let snapshot = worker.snapshot();
    assert!(snapshot.cpu_percent.is_some());
    assert!(snapshot.memory.is_some());
    assert!(snapshot.cpu_percent.unwrap() <= 100.0);
    let start = Instant::now();
    worker.stop();
    assert!(start.elapsed() < Duration::from_secs(2));
}

#[cfg(windows)]
#[test]
fn sensor_worker_rejects_unbounded_polling_rates() {
    use mini_monitor_native::sensor_worker::SensorWorker;
    use std::time::Duration;
    assert!(SensorWorker::start(Duration::ZERO).is_err());
    assert!(SensorWorker::start(Duration::from_millis(249)).is_err());
}
