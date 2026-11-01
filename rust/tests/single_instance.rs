#![cfg(windows)]
use mini_monitor_native::single_instance::InstanceGuard;

#[test]
fn second_instance_is_rejected_and_lock_is_released_on_exit() {
    let name = format!(r"Local\MiniMonitorTest-{}", std::process::id());
    let first = InstanceGuard::try_acquire(&name).unwrap().unwrap();
    assert!(InstanceGuard::try_acquire(&name).unwrap().is_none());
    drop(first);
    assert!(InstanceGuard::try_acquire(&name).unwrap().is_some());
}

#[test]
fn invalid_mutex_names_are_rejected_before_os_call() {
    assert!(InstanceGuard::try_acquire("").is_err());
    assert!(InstanceGuard::try_acquire("bad\0name").is_err());
}
