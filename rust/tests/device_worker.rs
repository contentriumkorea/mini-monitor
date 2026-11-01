use mini_monitor_native::{
    device::{bitmap_transfer, select_device, DeviceInfo, Orientation},
    device_session::DeviceSession,
    device_worker::{DeviceWorker, WorkerStatus},
};
use std::{
    io::{self, Write},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

#[cfg(windows)]
#[test]
fn invalid_brightness_rejects_physical_start_before_port_open() {
    assert!(DeviceWorker::start_physical(51, Orientation::Landscape, None).is_err());
}

struct Writer(Arc<Mutex<Vec<u8>>>);
impl Write for Writer {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn target() -> DeviceInfo {
    DeviceInfo {
        port: "COM3".into(),
        vid: 0x1a86,
        pid: 0x5722,
        serial: "USB35INCHIPSV2".into(),
    }
}
fn wait_until(mut f: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(3);
    while !f() {
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(5));
    }
}
#[test]
fn worker_transmits_full_frame_and_closes_on_removed_identity() {
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let exists = Arc::new(std::sync::atomic::AtomicBool::new(true));
    let output = bytes.clone();
    let identity = exists.clone();
    let mut worker = DeviceWorker::start(
        25,
        Orientation::Landscape,
        move |_| {
            let target = target();
            let selected = select_device(std::slice::from_ref(&target), None).unwrap();
            DeviceSession::verified(selected, &[target], Writer(output))
        },
        move || {
            if identity.load(std::sync::atomic::Ordering::Acquire) {
                Ok(vec![target()])
            } else {
                Ok(vec![])
            }
        },
    )
    .unwrap();
    wait_until(|| worker.status() == WorkerStatus::Ready);
    let frame = bitmap_transfer(0, 0, 480, 320, &vec![0; 480 * 320 * 2], 480, 320).unwrap();
    worker.submit_full(frame).unwrap();
    wait_until(|| bytes.lock().unwrap().len() >= 34 + 6 + 480 * 320 * 2);
    exists.store(false, std::sync::atomic::Ordering::Release);
    wait_until(|| worker.status() == WorkerStatus::Error);
    let count = bytes.lock().unwrap().len();
    assert!(worker
        .submit_full(bitmap_transfer(0, 0, 480, 320, &vec![0; 480 * 320 * 2], 480, 320).unwrap())
        .is_err());
    worker.join();
    assert_eq!(count, bytes.lock().unwrap().len());
}

#[test]
fn stopping_during_startup_does_not_write_orientation_or_frames() {
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let output = bytes.clone();
    let mut worker = DeviceWorker::start(
        25,
        Orientation::Landscape,
        move |_| {
            let target = target();
            let selected = select_device(std::slice::from_ref(&target), None).unwrap();
            DeviceSession::verified(selected, &[target], Writer(output))
        },
        || Ok(vec![target()]),
    )
    .unwrap();
    wait_until(|| bytes.lock().unwrap().len() >= 12);
    worker.request_stop();
    worker.join();
    assert_eq!(bytes.lock().unwrap().len(), 12);
    assert_eq!(worker.status(), WorkerStatus::Stopped);
}

#[test]
fn brightness_and_orientation_changes_are_serialized_by_worker() {
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let output = bytes.clone();
    let mut worker = DeviceWorker::start(
        25,
        Orientation::Landscape,
        move |_| {
            let target = target();
            let selected = select_device(std::slice::from_ref(&target), None).unwrap();
            DeviceSession::verified(selected, &[target], Writer(output))
        },
        || Ok(vec![target()]),
    )
    .unwrap();
    wait_until(|| worker.status() == WorkerStatus::Ready);
    assert!(worker.set_display(51, Orientation::Landscape).is_err());
    worker.set_display(30, Orientation::Landscape).unwrap();
    wait_until(|| bytes.lock().unwrap().len() >= 40);
    worker.set_display(30, Orientation::Portrait).unwrap();
    wait_until(|| bytes.lock().unwrap().len() >= 62);
    let invalid = bitmap_transfer(0, 0, 480, 320, &vec![0; 480 * 320 * 2], 480, 320).unwrap();
    worker.submit_full(invalid).unwrap();
    thread::sleep(Duration::from_millis(600));
    assert_eq!(worker.status(), WorkerStatus::Ready);
    let valid = bitmap_transfer(0, 0, 320, 480, &vec![0; 320 * 480 * 2], 320, 480).unwrap();
    worker.submit_full(valid).unwrap();
    wait_until(|| bytes.lock().unwrap().len() >= 62 + 6 + 320 * 480 * 2);
    worker.request_stop();
    worker.join();
}

#[test]
fn worker_sends_small_delta_against_last_successfully_transmitted_frame() {
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let output = bytes.clone();
    let mut worker = DeviceWorker::start(
        25,
        Orientation::Landscape,
        move |_| {
            let target = target();
            let selected = select_device(std::slice::from_ref(&target), None).unwrap();
            DeviceSession::verified(selected, &[target], Writer(output))
        },
        || Ok(vec![target()]),
    )
    .unwrap();
    wait_until(|| worker.status() == WorkerStatus::Ready);
    let mut pixels = vec![0; 480 * 320 * 2];
    worker
        .submit_full(bitmap_transfer(0, 0, 480, 320, &pixels, 480, 320).unwrap())
        .unwrap();
    let initial = 34 + 6 + 480 * 320 * 2;
    wait_until(|| bytes.lock().unwrap().len() >= initial);
    pixels[(30 * 480 + 20) * 2] = 0x5a;
    pixels[(30 * 480 + 20) * 2 + 1] = 0xa5;
    worker
        .submit_full(bitmap_transfer(0, 0, 480, 320, &pixels, 480, 320).unwrap())
        .unwrap();
    wait_until(|| bytes.lock().unwrap().len() >= initial + 8);
    assert_eq!(bytes.lock().unwrap().len(), initial + 8);
    assert_eq!(
        &bytes.lock().unwrap()[initial + 6..initial + 8],
        &[0x5a, 0xa5]
    );
    worker
        .submit_full(bitmap_transfer(0, 0, 480, 320, &pixels, 480, 320).unwrap())
        .unwrap();
    thread::sleep(Duration::from_millis(600));
    assert_eq!(bytes.lock().unwrap().len(), initial + 8);
    worker.request_stop();
    worker.join();
}
