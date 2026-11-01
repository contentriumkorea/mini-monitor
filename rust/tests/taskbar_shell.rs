#[cfg(windows)]
#[test]
fn invalid_monitor_never_claims_fullscreen_is_safe() {
    use mini_monitor_native::taskbar_geometry::Rect;
    use mini_monitor_native::taskbar_shell::{fullscreen_on_monitor, FullscreenState};
    assert_eq!(
        fullscreen_on_monitor(
            Rect {
                left: 0,
                top: 0,
                right: 0,
                bottom: 1
            },
            None
        ),
        FullscreenState::Unknown
    );
}

#[cfg(windows)]
#[test]
#[ignore = "requires an interactive Windows Explorer desktop"]
fn live_shell_snapshot_has_consistent_bar_monitor_and_dpi() {
    use mini_monitor_native::taskbar_shell::{fullscreen_on_monitor, query_primary_taskbar};
    let snapshot = query_primary_taskbar().unwrap();
    assert!(snapshot.bar.right > snapshot.bar.left && snapshot.bar.bottom > snapshot.bar.top);
    assert!(
        snapshot.monitor.right > snapshot.monitor.left
            && snapshot.monitor.bottom > snapshot.monitor.top
    );
    assert!((96..=768).contains(&snapshot.dpi));
    let _visibility_state = fullscreen_on_monitor(snapshot.monitor, None);
}
