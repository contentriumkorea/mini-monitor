use mini_monitor_native::{
    device::{select_device, DeviceInfo, Orientation},
    device_session::DeviceSession,
    device_supervisor::DeviceSupervisor,
    device_worker::{DeviceWorker, WorkerStatus},
};
use std::{
    io::{self, Write},
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
struct Sink;
impl Write for Sink {
    fn write(&mut self, b: &[u8]) -> io::Result<usize> {
        Ok(b.len())
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

#[test]
fn disconnected_device_retries_after_backoff_without_blocking_other_workers() {
    let attempts = Arc::new(AtomicUsize::new(0));
    let track = attempts.clone();
    let mut supervisor = DeviceSupervisor::new(Duration::from_secs(5), move || {
        let attempt = track.fetch_add(1, Ordering::SeqCst);
        DeviceWorker::start(
            25,
            Orientation::Landscape,
            move |_| {
                if attempt == 0 {
                    return Err(io::Error::other("unplugged"));
                }
                let target = target();
                let selected = select_device(std::slice::from_ref(&target), None).unwrap();
                DeviceSession::verified(selected, &[target], Sink)
            },
            || Ok(vec![target()]),
        )
    })
    .unwrap();
    let start = Instant::now();
    supervisor.tick(start);
    let deadline = Instant::now() + Duration::from_secs(3);
    while supervisor.status() != Some(WorkerStatus::Error) {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    supervisor.tick(start + Duration::from_secs(1));
    supervisor.tick(start + Duration::from_secs(5));
    assert_eq!(attempts.load(Ordering::SeqCst), 1);
    supervisor.tick(start + Duration::from_secs(6));
    assert_eq!(attempts.load(Ordering::SeqCst), 2);
    assert_eq!(supervisor.generation(), 2);
    while supervisor.status() != Some(WorkerStatus::Ready) {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(10));
    }
    supervisor.stop();
    supervisor.tick(start + Duration::from_secs(60));
    assert_eq!(attempts.load(Ordering::SeqCst), 2);
}
