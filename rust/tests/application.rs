#![cfg(windows)]
use mini_monitor_native::{
    application::{ApplicationCore, OutputOptions},
    dashboard_layout::Orientation,
    desktop_runtime::DesktopRuntime,
    settings_window::{SettingsAction, SettingsWindow},
    taskbar_controller::ShellState,
    taskbar_geometry::Rect,
    taskbar_render::{MetricKind, TaskbarStyle},
    taskbar_shell::FullscreenState,
};
use std::time::Duration;

#[test]
fn overlay_appearance_updates_live_and_saves_without_changing_position() {
    use mini_monitor_native::config::{Settings, SettingsRepository};
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("config.json");
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.attach_repository(SettingsRepository::new(
        path.clone(),
        Settings::parse("{}").unwrap(),
    ));
    assert!(!app.apply(SettingsAction::OverlaySettings).unwrap());
    assert!(!app.apply(SettingsAction::TaskbarSettings).unwrap());
    app.apply(SettingsAction::ToggleOverlay).unwrap();
    let before = app.runtime().overlay_bounds().unwrap();
    app.configure_overlay(150, 0).unwrap();
    app.tick().unwrap();
    let after = app.runtime().overlay_bounds().unwrap();
    assert!(after.2 > before.2);
    let value: serde_json::Value =
        serde_json::from_str(&Settings::load(&path).unwrap().to_json().unwrap()).unwrap();
    assert_eq!(value["overlay"]["scale_percent"], 150);
    assert_eq!(value["overlay"]["opacity"], 0.0);
    assert!(value["overlay"]["x"].is_null());
    assert!(app.configure_overlay(201, 0).is_err());
    assert!(app.configure_overlay(100, 101).is_err());
}

#[test]
fn tray_commands_operate_the_same_outputs_and_never_start_disabled_usb() {
    use mini_monitor_native::tray::TrayAction;
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.set_device_factory(|| Err(std::io::Error::other("test device offline")))
        .unwrap();
    assert!(!app.apply(SettingsAction::FindDevice).unwrap());
    assert!(app.apply_tray_action(TrayAction::ReconnectDisplay).is_err());
    assert!(!app.monitor_enabled());
    assert!(!app.apply_tray_action(TrayAction::ToggleOverlay).unwrap());
    assert!(app.runtime().overlay_bounds().is_some());
    assert!(!app.apply_tray_action(TrayAction::ToggleOverlay).unwrap());
    assert!(app.runtime().overlay_bounds().is_none());
    app.apply(SettingsAction::ToggleMonitor).unwrap();
    assert!(!app.apply_tray_action(TrayAction::ReconnectDisplay).unwrap());
    assert!(app.monitor_enabled());
    assert!(app.apply_tray_action(TrayAction::Exit).unwrap());
}

#[test]
fn account_login_opens_only_a_validated_openai_url() {
    use mini_monitor_native::{account_worker::AccountEvent, application::dispatch_account_event};
    let mut received = None;
    dispatch_account_event(
        AccountEvent::OpenBrowser("https://auth.openai.com/authorize?request=test".into()),
        |url| {
            received = Some(url.to_owned());
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(
        received.as_deref(),
        Some("https://auth.openai.com/authorize?request=test")
    );
    let mut called = false;
    let result = dispatch_account_event(
        AccountEvent::OpenBrowser("https://example.invalid/authorize".into()),
        |_| {
            called = true;
            Ok(())
        },
    );
    assert!(result.is_err());
    assert!(!called);
}

#[test]
fn taskbar_item_choices_apply_live_and_reject_invalid_selection() {
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.apply(SettingsAction::ToggleTaskbar).unwrap();
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
    app.tick_with_shell(shell).unwrap();
    let before = app.runtime().taskbar_bounds().unwrap();
    app.configure_taskbar(
        TaskbarStyle::Text,
        None,
        vec![MetricKind::Cpu, MetricKind::Ram],
    )
    .unwrap();
    app.tick_with_shell(shell).unwrap();
    let after = app.runtime().taskbar_bounds().unwrap();
    assert!(after.right - after.left > before.right - before.left);
    assert!(app
        .configure_taskbar(
            TaskbarStyle::Text,
            None,
            vec![MetricKind::Cpu, MetricKind::Cpu]
        )
        .is_err());
    app.tick_with_shell(shell).unwrap();
    assert_eq!(app.runtime().taskbar_bounds(), Some(after));
}

#[test]
fn startup_restores_desktop_displays_without_starting_usb_monitor() {
    use mini_monitor_native::{config::Settings, device::Orientation as DeviceOrientation};
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    let startup = Settings::parse(r#"{"device":{"brightness":33,"rotation":"portrait_inverted"},"overlay":{"enabled":true,"opacity":0.25,"scale_percent":125,"x":-30000,"y":-30000,"taskbar_enabled":true,"taskbar_x":-29900,"taskbar_y":-30030,"taskbar_style":"text","taskbar_items":["cpu","ram"]}}"#).unwrap().startup_settings().unwrap();
    app.restore_preferences(&startup, None).unwrap();
    assert_eq!(
        app.settings().device_options().unwrap(),
        (DeviceOrientation::ReversePortrait, 33)
    );
    assert!(!app.monitor_enabled());
    assert!(app.runtime().device_status().is_none());
    assert!(app.runtime().overlay_bounds().is_some());
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
    app.tick_with_shell(shell).unwrap();
    assert!(app.runtime().taskbar_bounds().is_some());
}

#[test]
fn startup_overlay_failure_does_not_prevent_taskbar_restoration() {
    use mini_monitor_native::config::Settings;
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    let startup=Settings::parse(r#"{"overlay":{"enabled":true,"x":2147483647,"y":0,"taskbar_enabled":true,"taskbar_x":-29900,"taskbar_y":-30030,"taskbar_style":"text","taskbar_items":["cpu"]}}"#).unwrap().startup_settings().unwrap();
    assert!(app.restore_preferences(&startup, None).is_err());
    assert!(app.runtime().overlay_bounds().is_none());
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
    app.tick_with_shell(shell).unwrap();
    assert!(app.runtime().taskbar_bounds().is_some());
}

#[test]
fn changed_device_and_taskbar_preferences_persist_without_erasing_other_settings() {
    use mini_monitor_native::{
        config::{Settings, SettingsRepository},
        device::Orientation as DeviceOrientation,
    };
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("config.json");
    let base = Settings::parse(r#"{"future":{"keep":42}}"#).unwrap();
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.set_device_factory(|| Err(std::io::Error::other("fixture")))
        .unwrap();
    app.attach_repository(SettingsRepository::new(file.clone(), base));
    app.settings()
        .set_device_options(DeviceOrientation::ReversePortrait, 33)
        .unwrap();
    app.apply(SettingsAction::DeviceOptionsChanged).unwrap();
    app.apply(SettingsAction::ToggleTaskbar).unwrap();
    let saved: serde_json::Value =
        serde_json::from_str(&Settings::load(&file).unwrap().to_json().unwrap()).unwrap();
    assert_eq!(saved["device"]["rotation"], "portrait_inverted");
    assert_eq!(saved["device"]["brightness"], 33);
    assert_eq!(saved["overlay"]["taskbar_enabled"], true);
    assert_eq!(saved["future"]["keep"], 42);
}

#[test]
fn settings_save_failure_reports_error_but_keeps_live_status_accurate() {
    use mini_monitor_native::config::{Settings, SettingsRepository};
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetDlgItem, GetWindowTextW};
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("missing").join("settings.json");
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.attach_repository(SettingsRepository::new(
        file,
        Settings::parse("{}").unwrap(),
    ));
    assert!(app.apply(SettingsAction::ToggleTaskbar).is_err());
    let mut text = [0u16; 128];
    let n = unsafe {
        GetWindowTextW(
            GetDlgItem(app.settings().handle(), 3),
            text.as_mut_ptr(),
            128,
        )
    };
    assert_eq!(
        String::from_utf16(&text[..n as usize]).unwrap(),
        "작업표시줄 바 · 켜짐"
    );
}

#[test]
fn taskbar_item_and_style_preferences_are_saved() {
    use mini_monitor_native::config::{Settings, SettingsRepository};
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("config.json");
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.attach_repository(SettingsRepository::new(
        file.clone(),
        Settings::parse("{}").unwrap(),
    ));
    app.configure_taskbar(
        TaskbarStyle::Both,
        None,
        vec![MetricKind::Gpu, MetricKind::Cpu],
    )
    .unwrap();
    let saved: serde_json::Value =
        serde_json::from_str(&Settings::load(&file).unwrap().to_json().unwrap()).unwrap();
    assert_eq!(saved["overlay"]["taskbar_style"], "both");
    assert_eq!(
        saved["overlay"]["taskbar_items"],
        serde_json::json!(["cpu", "gpu"])
    );
}

#[test]
fn available_signed_release_is_shown_in_settings_without_blocking_ui() {
    use mini_monitor_native::release_worker::{ReleaseStatus, ReleaseWorker};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetDlgItem, GetWindowTextW, SetWindowPos, ShowWindow, SWP_NOACTIVATE, SWP_NOSIZE,
        SWP_NOZORDER, SW_SHOWNOACTIVATE,
    };
    let worker =
        ReleaseWorker::start("0.2.13", Duration::from_secs(60), || Ok("0.2.14".into())).unwrap();
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    runtime.attach_release(worker);
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    while app.runtime().release_snapshot().unwrap().status != ReleaseStatus::Available {
        assert!(std::time::Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    unsafe {
        SetWindowPos(
            app.settings().handle(),
            std::ptr::null_mut(),
            -30000,
            -30000,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        );
        ShowWindow(app.settings().handle(), SW_SHOWNOACTIVATE);
    }
    app.tick().unwrap();
    let mut text = [0u16; 128];
    let n = unsafe {
        GetWindowTextW(
            GetDlgItem(app.settings().handle(), 10),
            text.as_mut_ptr(),
            128,
        )
    };
    assert_eq!(
        String::from_utf16(&text[..n as usize]).unwrap(),
        "업데이트 가능 · 0.2.14"
    );
    assert_eq!(
        unsafe {
            windows_sys::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled(GetDlgItem(
                app.settings().handle(),
                10,
            ))
        },
        1
    );
    app.settings().hide();
}

#[test]
fn settings_actions_toggle_independent_outputs_and_refresh_preview() {
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    assert!(app.apply(SettingsAction::ToggleMonitor).is_err());
    assert!(!app.monitor_enabled());
    app.apply(SettingsAction::ToggleOverlay).unwrap();
    app.apply(SettingsAction::ToggleTaskbar).unwrap();
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
    app.tick_with_shell(shell).unwrap();
    assert!(app.runtime().overlay_bounds().is_some());
    assert!(app.runtime().taskbar_bounds().is_some());
    assert_eq!(
        app.settings().preview_size(),
        None,
        "hidden settings must not render an invisible preview"
    );
    app.apply(SettingsAction::ToggleOverlay).unwrap();
    assert!(app.runtime().overlay_bounds().is_none());
    assert!(app.runtime().taskbar_bounds().is_some());
    app.apply(SettingsAction::ToggleTaskbar).unwrap();
    assert!(app.runtime().taskbar_bounds().is_none());
}

#[test]
fn overlay_failure_never_reports_enabled_or_leaves_an_unusable_window() {
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: i32::MAX,
        overlay_y: 0,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: 0,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    assert!(app.apply(SettingsAction::ToggleOverlay).is_err());
    assert!(app.runtime().overlay_bounds().is_none());
}

#[test]
fn visible_settings_receive_live_preview_without_enabling_the_usb_display() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SetWindowPos, ShowWindow, SWP_NOACTIVATE, SWP_NOSIZE, SWP_NOZORDER, SW_SHOWNOACTIVATE,
    };
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    unsafe {
        SetWindowPos(
            app.settings().handle(),
            std::ptr::null_mut(),
            -30000,
            -30000,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        );
        ShowWindow(app.settings().handle(), SW_SHOWNOACTIVATE);
    }
    app.tick().unwrap();
    assert_eq!(app.settings().preview_size(), Some((480, 320)));
    assert!(app.runtime().device_status().is_none());
    app.settings().hide();
}

#[test]
fn unavailable_features_are_visibly_disabled_in_the_development_app() {
    use windows_sys::Win32::UI::{
        Input::KeyboardAndMouse::IsWindowEnabled, WindowsAndMessaging::GetDlgItem,
    };
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let app = ApplicationCore::new(runtime, settings, options).unwrap();
    let enabled = |id| unsafe { IsWindowEnabled(GetDlgItem(app.settings().handle(), id)) != 0 };
    assert!(!enabled(1));
    assert!(enabled(2));
    assert!(enabled(3));
    assert!(!enabled(5));
    assert!(!enabled(9));
    assert!(!enabled(10));
}

#[test]
fn device_options_can_be_selected_before_monitor_start() {
    use mini_monitor_native::device::Orientation as DeviceOrientation;
    use windows_sys::Win32::UI::WindowsAndMessaging::{ShowWindow, SW_SHOWNOACTIVATE};
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    let settings = SettingsWindow::new_hidden().unwrap();
    let options = OutputOptions {
        overlay_x: -30000,
        overlay_y: -30000,
        overlay_scale: 100,
        overlay_opacity: 0,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: -29900,
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu],
        icon_dir: None,
    };
    let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
    app.set_device_factory(|| Err(std::io::Error::other("simulated disconnect")))
        .unwrap();
    app.apply(SettingsAction::ToggleOverlay).unwrap();
    app.settings()
        .set_device_options(DeviceOrientation::ReversePortrait, 33)
        .unwrap();
    app.apply(SettingsAction::DeviceOptionsChanged).unwrap();
    unsafe {
        ShowWindow(app.settings().handle(), SW_SHOWNOACTIVATE);
    }
    app.tick().unwrap();
    assert_eq!(app.settings().preview_size(), Some((320, 480)));
    assert_eq!(
        app.runtime().overlay_bounds().map(|b| (b.2, b.3)),
        Some((320, 480))
    );
    app.apply(SettingsAction::ToggleMonitor).unwrap();
    assert!(app.monitor_enabled());
    app.apply(SettingsAction::ToggleMonitor).unwrap();
    app.settings().hide();
}
