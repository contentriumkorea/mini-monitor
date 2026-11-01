#[cfg(windows)]
#[test]
#[ignore = "requires an interactive Windows Explorer desktop"]
fn live_shell_snapshot_has_consistent_bar_monitor_and_dpi() {
    use mini_monitor_native::taskbar_shell::query_primary_taskbar;
    let snapshot = query_primary_taskbar().unwrap();
    assert!(snapshot.bar.right > snapshot.bar.left && snapshot.bar.bottom > snapshot.bar.top);
    assert!(
        snapshot.monitor.right > snapshot.monitor.left
            && snapshot.monitor.bottom > snapshot.monitor.top
    );
    assert!((96..=768).contains(&snapshot.dpi));
}
