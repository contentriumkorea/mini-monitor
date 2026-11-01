//! Development integration window: live sensors, no USB, no account, no settings writes.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        application::{ApplicationCore, OutputOptions},
        dashboard_layout::Orientation,
        desktop_runtime::DesktopRuntime,
        settings_window::SettingsWindow,
        taskbar_render::{MetricKind, TaskbarStyle},
        taskbar_shell::query_primary_taskbar,
        tray::{NativeTray, TrayAction},
    };
    use std::{
        path::Path,
        time::{Duration, Instant},
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE, WM_QUIT,
    };
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let shell = query_primary_taskbar()?;
    let options = OutputOptions {
        overlay_x: 180,
        overlay_y: 180,
        overlay_scale: 100,
        overlay_opacity: 180,
        orientation: Orientation::Landscape,
        with_api: false,
        taskbar_x: shell
            .bar
            .left
            .saturating_add((shell.bar.right - shell.bar.left) / 2),
        taskbar_style: TaskbarStyle::Text,
        taskbar_items: vec![MetricKind::Cpu, MetricKind::Ram],
        icon_dir: None,
    };
    let settings = SettingsWindow::new_hidden()?;
    let runtime = DesktopRuntime::start(Duration::from_secs(1), None)?;
    let mut app = ApplicationCore::new(runtime, settings, options)?;
    let tray = NativeTray::new(&root.join("assets/app-icon.ico")).ok();
    app.settings().set_tray_available(tray.is_some());
    app.settings().show();
    let started = Instant::now();
    let mut running = true;
    while running && started.elapsed() < Duration::from_secs(120) {
        let mut msg: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut msg, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            if msg.message == WM_QUIT {
                running = false;
                break;
            }
            unsafe {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
        while let Some(action) = app.settings().take_action() {
            match app.apply(action) {
                Ok(true) => running = false,
                Ok(false) => {}
                Err(_) => eprintln!("Development action is not available"),
            }
        }
        if let Some(tray) = tray.as_ref() {
            while let Some(action) = tray.take_action() {
                match action {
                    TrayAction::OpenSettings => app.settings().show(),
                    TrayAction::ToggleOverlay => {
                        let _ = app.apply(
                            mini_monitor_native::settings_window::SettingsAction::ToggleOverlay,
                        );
                    }
                    TrayAction::Exit => running = false,
                    TrayAction::ReconnectDisplay => {}
                }
            }
        }
        let _ = app.tick();
        std::thread::sleep(Duration::from_millis(250));
    }
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
