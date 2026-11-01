//! Interactive, time-limited integration probe. Never saves settings or logs account data.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        account_worker::{AccountStatus, AccountWorker},
        application::{ApplicationCore, OutputOptions},
        config::Settings,
        dashboard_layout::Orientation as Layout,
        desktop_runtime::DesktopRuntime,
        device::Orientation,
        device_discovery::discover,
        device_worker::DeviceWorker,
        settings_window::{SettingsAction, SettingsWindow},
        taskbar_render::{MetricKind, TaskbarStyle},
        taskbar_shell::query_primary_taskbar,
        windows_serial,
    };
    use std::{
        path::PathBuf,
        time::{Duration, Instant},
    };
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE, WM_QUIT,
    };
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let data_dir = PathBuf::from(
        std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| std::io::Error::other("local data unavailable"))?,
    )
    .join("AI-Mini-Monitor");
    let config = Settings::load(&data_dir.join("config.json")).map_err(std::io::Error::other)?;
    let values: serde_json::Value = serde_json::from_str(&config.to_json()?)?;
    let device = values
        .get("device")
        .ok_or_else(|| std::io::Error::other("device settings unavailable"))?;
    let brightness = device
        .get("brightness")
        .and_then(|v| v.as_u64())
        .ok_or_else(|| std::io::Error::other("brightness unavailable"))? as u8;
    let (orientation, layout) = match device.get("rotation").and_then(|v| v.as_str()) {
        Some("landscape") => (Orientation::Landscape, Layout::Landscape),
        Some("landscape_inverted") => (Orientation::ReverseLandscape, Layout::Landscape),
        Some("portrait") => (Orientation::Portrait, Layout::Portrait),
        Some("portrait_inverted") => (Orientation::ReversePortrait, Layout::Portrait),
        _ => return Err(std::io::Error::other("invalid orientation")),
    };
    let manual = device
        .get("manual_port")
        .and_then(|v| v.as_str())
        .map(str::to_owned);
    let account = AccountWorker::start_verified(
        root.join("third_party/codex-app-server/codex-app-server.exe"),
        data_dir.join("codex-home"),
        Duration::from_secs(60),
    )?;
    let runtime = DesktopRuntime::start(Duration::from_secs(1), Some(account))?;
    let shell = query_primary_taskbar()?;
    let options = OutputOptions {
        overlay_x: 180,
        overlay_y: 180,
        overlay_scale: 100,
        overlay_opacity: 180,
        orientation: layout,
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
    let mut app = ApplicationCore::new(runtime, settings, options)?;
    app.set_device_factory(move || {
        let port = manual.clone();
        DeviceWorker::start(
            brightness,
            orientation,
            move |gate| windows_serial::connect(port.as_deref(), gate),
            discover,
        )
    })?;
    app.settings().set_device_options(orientation, brightness)?;
    app.apply(SettingsAction::DeviceOptionsChanged)?;
    app.settings().show();
    let mut account_reported = false;
    let mut ready_reported = false;
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
                Ok(false) => (),
                Err(_) => eprintln!("probe action unavailable"),
            }
        }
        if app
            .process_account_events(|url| {
                let operation: Vec<u16> = "open\0".encode_utf16().collect();
                let target: Vec<u16> = url.encode_utf16().chain(std::iter::once(0)).collect();
                let result = unsafe {
                    ShellExecuteW(
                        std::ptr::null_mut(),
                        operation.as_ptr(),
                        target.as_ptr(),
                        std::ptr::null(),
                        std::ptr::null(),
                        1,
                    )
                } as isize;
                if result <= 32 {
                    Err(std::io::Error::other("login browser unavailable"))
                } else {
                    Ok(())
                }
            })
            .is_err()
        {
            eprintln!("login launch failed");
        }
        if let Err(error) = app.tick() {
            eprintln!("probe output error: {:?}", error.kind());
        }
        if !account_reported
            && app
                .runtime()
                .account_snapshot()
                .is_some_and(|snapshot| snapshot.status == AccountStatus::Ready)
        {
            println!("account_worker=ready");
            account_reported = true;
        }
        if !ready_reported
            && app.runtime().device_status()
                == Some(mini_monitor_native::device_worker::WorkerStatus::Ready)
        {
            println!("device_worker=ready");
            ready_reported = true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
