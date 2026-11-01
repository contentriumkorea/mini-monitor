#[cfg(windows)]
#[test]
fn controller_shows_only_inside_safe_shell_area_and_hides_on_fullscreen() {
    use mini_monitor_native::{
        taskbar_controller::{ShellState, TaskbarController},
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::IsWindowVisible;
    let mut controller = TaskbarController::new(TaskbarStyle::Text, None, -29900).unwrap();
    let mut shell = ShellState {
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
    controller
        .tick_with_shell(&[(K::Cpu, "25%"), (K::Gpu, "1%")], shell)
        .unwrap();
    assert!(unsafe { IsWindowVisible(controller.handle()) } != 0);
    let bounds = controller.last_bounds().unwrap();
    assert!(bounds.right <= shell.notification.unwrap().left);
    shell.fullscreen = FullscreenState::Covered;
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert_eq!(unsafe { IsWindowVisible(controller.handle()) }, 0);
    shell.fullscreen = FullscreenState::Unknown;
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert_eq!(unsafe { IsWindowVisible(controller.handle()) }, 0);
}

#[cfg(windows)]
#[test]
fn controller_reasserts_own_topmost_order_without_activating_other_windows() {
    use mini_monitor_native::{
        taskbar_controller::{ShellState, TaskbarController},
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowPos, GWL_EXSTYLE, HWND_NOTOPMOST, SWP_NOACTIVATE, SWP_NOMOVE,
        SWP_NOSIZE, WS_EX_TOPMOST,
    };
    let mut controller = TaskbarController::new(TaskbarStyle::Text, None, -29900).unwrap();
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
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert_ne!(
        unsafe {
            SetWindowPos(
                controller.handle(),
                HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        },
        0
    );
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert_ne!(
        (unsafe { GetWindowLongPtrW(controller.handle(), GWL_EXSTYLE) } as u32) & WS_EX_TOPMOST,
        0
    );
}

#[cfg(windows)]
#[test]
fn drag_is_applied_without_crashing_or_crossing_notification_area() {
    use mini_monitor_native::{
        taskbar_controller::{ShellState, TaskbarController},
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SendMessageW, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE,
    };
    let mut controller = TaskbarController::new(TaskbarStyle::Text, None, -29900).unwrap();
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
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    let previous = controller.last_bounds().unwrap();
    unsafe {
        SendMessageW(controller.handle(), WM_LBUTTONDOWN, 1, 10);
        SendMessageW(controller.handle(), WM_MOUSEMOVE, 1, 20);
    }
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert_eq!(controller.last_bounds().unwrap().left, previous.left + 10);
    assert!(controller.take_position_change().is_none());
    unsafe {
        SendMessageW(controller.handle(), WM_LBUTTONUP, 0, 20);
    }
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert_eq!(
        controller.take_position_change(),
        Some((previous.left + 10, controller.last_bounds().unwrap().top))
    );
    assert!(controller.last_bounds().unwrap().right <= shell.notification.unwrap().left);
    controller.set_desired_x(i32::MAX);
    controller
        .tick_with_shell(&[(K::Cpu, "25%")], shell)
        .unwrap();
    assert!(controller.last_bounds().unwrap().right <= shell.notification.unwrap().left);
}
