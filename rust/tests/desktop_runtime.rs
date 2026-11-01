#[cfg(windows)]
#[test]
fn taskbar_runs_and_stops_independently_of_usb_monitor() {
    use mini_monitor_native::{
        desktop_runtime::DesktopRuntime,
        taskbar_controller::ShellState,
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    use std::time::Duration;
    let shell = ShellState {
        bar: Rect {
            left: -30000,
            top: -30050,
            right: -29000,
            bottom: -30010,
        },
        notification: Some(Rect {
            left: -29200,
            top: -30050,
            right: -29000,
            bottom: -30010,
        }),
        monitor: Rect {
            left: -30000,
            top: -31000,
            right: -29000,
            bottom: -30010,
        },
        dpi: 96,
        visible: true,
        fullscreen: FullscreenState::NotCovered,
    };
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    runtime
        .enable_taskbar(TaskbarStyle::Text, None, -29900, vec![K::Cpu, K::Ram])
        .unwrap();
    std::thread::sleep(Duration::from_millis(600));
    runtime.tick_with_shell(shell, None).unwrap();
    assert!(runtime.taskbar_bounds().is_some());
    runtime.disable_taskbar();
    assert!(runtime.taskbar_bounds().is_none());
    assert!(runtime.sensor_snapshot().cpu_percent.is_some());
}

#[cfg(windows)]
#[test]
fn duplicate_taskbar_items_are_rejected_without_opening_a_window() {
    use mini_monitor_native::{
        desktop_runtime::DesktopRuntime,
        taskbar_render::{MetricKind as K, TaskbarStyle},
    };
    use std::time::Duration;
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    assert!(runtime
        .enable_taskbar(TaskbarStyle::Text, None, 0, vec![K::Cpu, K::Cpu])
        .is_err());
    assert!(runtime.taskbar_bounds().is_none());
}
