use mini_monitor_native::release_worker::{ReleaseStatus, ReleaseWorker};
use std::time::{Duration, Instant};

#[test]
fn checked_update_is_reported_without_blocking_the_calling_thread() {
    let start = Instant::now();
    let mut worker = ReleaseWorker::start("0.2.13", Duration::from_secs(60), || {
        std::thread::sleep(Duration::from_millis(250));
        Ok("0.2.14".into())
    })
    .unwrap();
    assert!(start.elapsed() < Duration::from_millis(150));
    let deadline = Instant::now() + Duration::from_secs(3);
    while worker.snapshot().status == ReleaseStatus::Checking && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    let result = worker.snapshot();
    assert_eq!(result.status, ReleaseStatus::Available);
    assert_eq!(result.version.as_deref(), Some("0.2.14"));
    worker.stop();
}

#[test]
fn rejected_version_is_error_not_a_fake_update() {
    let mut worker = ReleaseWorker::start("0.2.13", Duration::from_secs(60), || {
        Ok("0.2.14-beta".into())
    })
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while worker.snapshot().status == ReleaseStatus::Checking && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(worker.snapshot().status, ReleaseStatus::Error);
    assert!(worker.snapshot().version.is_none());
    worker.stop();
}

#[test]
fn dropping_update_worker_does_not_block_on_a_slow_network_check() {
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };
    let entered = Arc::new(AtomicBool::new(false));
    let signal = entered.clone();
    let worker = ReleaseWorker::start("0.2.13", Duration::from_secs(60), move || {
        signal.store(true, Ordering::Release);
        std::thread::sleep(Duration::from_millis(700));
        Ok("0.2.14".into())
    })
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    while !entered.load(Ordering::Acquire) {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(worker.request_check());
    assert!(!worker.request_check());
    let start = Instant::now();
    drop(worker);
    assert!(start.elapsed() < Duration::from_millis(150));
}
