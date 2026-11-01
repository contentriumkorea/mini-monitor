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

#[cfg(windows)]
#[test]
fn unavailable_explicit_gpu_does_not_turn_into_zero_or_stop_cpu_ram() {
    use mini_monitor_native::sensor_worker::SensorWorker;
    use std::time::Duration;
    let worker = SensorWorker::start_with_nvidia(Duration::from_millis(250), u32::MAX).unwrap();
    std::thread::sleep(Duration::from_millis(700));
    let sample = worker.snapshot();
    assert!(sample.cpu_percent.is_some());
    assert!(sample.memory.is_some());
    assert!(sample.gpu.is_none());
}

#[cfg(windows)]
#[test]
#[ignore = "requires an NVIDIA GPU and its trusted system driver"]
fn explicitly_selected_nvidia_device_is_sampled_in_background() {
    use mini_monitor_native::sensor_worker::SensorWorker;
    use std::time::{Duration, Instant};
    let worker = SensorWorker::start_with_nvidia(Duration::from_millis(250), 0).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while worker.snapshot().gpu.is_none() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(worker.snapshot().gpu.is_some_and(|gpu| gpu.vram.is_some()));
}
