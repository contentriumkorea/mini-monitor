//! Native application host. Release packaging remains gated on feature parity.
#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(windows)]
fn main() {
    if run().is_err() {
        notice("프로그램을 시작하지 못했습니다. 설치 파일과 설정을 확인해 주세요.");
    }
}
#[cfg(not(windows))]
fn main() {}

#[cfg(windows)]
fn notice(text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONWARNING, MB_OK};
    let text: Vec<u16> = text.encode_utf16().chain(Some(0)).collect();
    let title: Vec<u16> = "Mini Monitor".encode_utf16().chain(Some(0)).collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONWARNING,
        );
    }
}

#[cfg(windows)]
fn confirm_update() -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, IDYES, MB_ICONQUESTION, MB_YESNO,
    };
    let text: Vec<u16> = "최신 버전을 내려받아 검증한 뒤 프로그램을 다시 시작할까요?"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let title: Vec<u16> = "Mini Monitor 업데이트"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            MB_YESNO | MB_ICONQUESTION,
        ) == IDYES
    }
}
#[cfg(windows)]
fn run() -> std::io::Result<()> {
    use mini_monitor_native::{
        account_worker::AccountWorker,
        application::{ApplicationCore, OutputOptions},
        budget_worker::BudgetWorker,
        config::{Settings, SettingsRepository},
        dashboard_layout::Orientation as Layout,
        desktop_runtime::DesktopRuntime,
        device::Orientation,
        device_discovery::discover,
        device_worker::DeviceWorker,
        secrets::SecretStore,
        settings_window::SettingsWindow,
        single_instance::{InstanceGuard, APP_MUTEX},
        taskbar_shell::query_primary_taskbar,
        tray::NativeTray,
        windows_serial,
    };
    use std::{
        io,
        path::PathBuf,
        time::{Duration, Instant},
    };
    use windows_sys::Win32::UI::{
        Shell::ShellExecuteW,
        WindowsAndMessaging::{
            DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE, WM_QUIT,
        },
    };
    let options = HostOptions::parse(std::env::args().skip(1)).map_err(io::Error::other)?;
    let Some(_instance) = InstanceGuard::try_acquire(APP_MUTEX)? else {
        return Ok(());
    };
    let exe = std::env::current_exe()?;
    let root = options
        .resources
        .unwrap_or_else(|| exe.parent().unwrap().to_path_buf());
    let data = PathBuf::from(
        std::env::var_os("LOCALAPPDATA")
            .ok_or_else(|| io::Error::other("data directory unavailable"))?,
    )
    .join("AI-Mini-Monitor");
    let config_path = options
        .config
        .or_else(|| std::env::var_os("MINI_MONITOR_UPDATE_CONFIG_PATH").map(PathBuf::from))
        .unwrap_or_else(|| data.join("config.json"));
    let startup_command = mini_monitor_native::startup::build_command(
        exe.to_str()
            .ok_or_else(|| io::Error::other("invalid executable path"))?,
        if config_path == data.join("config.json") {
            None
        } else {
            Some(
                config_path
                    .to_str()
                    .ok_or_else(|| io::Error::other("invalid settings path"))?,
            )
        },
    )?;
    let config = Settings::load_or_default(&config_path)
        .map_err(|_| io::Error::other("settings unavailable"))?;
    let mut startup = config
        .startup_settings()
        .map_err(|_| io::Error::other("settings unavailable"))?;
    if options.smoke_outputs {
        startup.overlay_enabled = true;
        startup.taskbar_enabled = true;
    }
    let layout = match startup.rotation {
        Orientation::Landscape | Orientation::ReverseLandscape => Layout::Landscape,
        _ => Layout::Portrait,
    };
    // Show the window before potentially expensive resource verification or network work.
    let window = SettingsWindow::new_hidden()?;
    window.set_app_icon(&root.join("assets/app-icon.ico"))?;
    let identity: Vec<u16> = "contentriumkorea.MiniMonitor"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(identity.as_ptr());
    }
    window.show();
    let account = if options.no_account {
        None
    } else {
        Some(AccountWorker::start_verified(
            root.join("third_party/codex-app-server/codex-app-server.exe"),
            data.join("codex-home"),
            Duration::from_secs(startup.usage_refresh_seconds),
        )?)
    };
    let mut runtime =
        DesktopRuntime::start(Duration::from_millis(startup.sensor_interval_ms), account)?;
    if options.smoke_seconds.is_none() {
        runtime.attach_release(
            mini_monitor_native::release_worker::ReleaseWorker::start_http(
                env!("CARGO_PKG_VERSION"),
                Duration::from_secs(3600),
            )?,
        );
    }
    if !options.no_account {
        if let Some(budget) = startup.budget.as_ref() {
            if let Some(key) = SecretStore::at(data.join("secrets/openai_admin_key.dpapi")).get()? {
                runtime.attach_budget(BudgetWorker::start_http(
                    budget.anchor,
                    budget.total_cents,
                    Duration::from_secs(startup.cost_refresh_seconds),
                    &key,
                )?);
            }
        }
    }
    let shell = query_primary_taskbar()?;
    let (x, y) = startup.overlay_position.unwrap_or((
        shell.monitor.left.saturating_add(80),
        shell.monitor.top.saturating_add(80),
    ));
    let output = OutputOptions {
        overlay_x: x,
        overlay_y: y,
        overlay_scale: startup.overlay_scale,
        overlay_opacity: startup.overlay_opacity,
        orientation: layout,
        with_api: startup.budget.is_some(),
        taskbar_x: startup.taskbar_position.map_or(shell.bar.left, |p| p.0),
        taskbar_style: startup.taskbar_style,
        taskbar_items: startup.taskbar_items.clone(),
        icon_dir: Some(root.join("assets/icons")),
    };
    let mut app = ApplicationCore::new(runtime, window, output)?;
    if !options.no_serial {
        let brightness = startup.brightness;
        let rotation = startup.rotation;
        let port = startup.manual_port.clone();
        app.set_device_factory(move || {
            let port = port.clone();
            DeviceWorker::start(
                brightness,
                rotation,
                move |gate| windows_serial::connect(port.as_deref(), gate),
                discover,
            )
        })?;
    }
    app.restore_preferences(&startup, Some(root.join("assets/icons")))?;
    if options.smoke_outputs && !options.no_serial {
        app.apply(mini_monitor_native::settings_window::SettingsAction::ToggleMonitor)?;
    }
    // A smoke launch is read-only, including close and output toggles.
    if options.smoke_seconds.is_none() {
        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        if !config_path.exists() {
            config
                .save(&config_path)
                .map_err(|_| io::Error::other("initial settings save failed"))?;
        }
        app.attach_repository(SettingsRepository::new(config_path.clone(), config));
        app.attach_secret_store(SecretStore::at(data.join("secrets/openai_admin_key.dpapi")))?;
        app.attach_startup_controller(
            Box::new(mini_monitor_native::startup::WindowsStartupStore::new()),
            startup_command,
        )?;
    }
    let tray = NativeTray::new(&root.join("assets/app-icon.ico"))?;
    app.settings().set_tray_available(true);
    if options.minimized && options.smoke_seconds.is_none() {
        app.settings().hide();
    }
    let deadline = options
        .smoke_seconds
        .map(|s| Instant::now() + Duration::from_secs(s));
    let mut running = true;
    let mut update_task: Option<mini_monitor_native::update_task::UpdateTask> = None;
    let mut ack_done = false;
    let healthy_started = Instant::now();
    let mut cleanup_scheduled = false;
    let mut maintenance: Option<std::thread::JoinHandle<std::io::Result<usize>>> = None;
    while running && deadline.is_none_or(|d| Instant::now() < d) {
        let mut message: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            if message.message == WM_QUIT {
                running = false;
                break;
            }
            if app.translate_message(&mut message) {
                continue;
            }
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        while let Some(action) = app.settings().take_action() {
            if action == mini_monitor_native::settings_window::SettingsAction::CheckUpdate {
                if update_task.is_none() {
                    let available = app.runtime().release_snapshot().is_some_and(|s| {
                        s.status == mini_monitor_native::release_worker::ReleaseStatus::Available
                    });
                    if available && confirm_update() {
                        if maintenance.as_ref().is_some_and(|job| !job.is_finished()) {
                            notice("구버전 정리 중입니다. 잠시 뒤 업데이트를 다시 눌러 주세요.");
                            continue;
                        }
                        update_task = Some(mini_monitor_native::update_task::UpdateTask::start(
                            root.clone(),
                            Some(config_path.clone()),
                            env!("CARGO_PKG_VERSION").into(),
                        )?);
                        app.settings().set_action_enabled(action, false)?;
                    } else {
                        app.runtime().request_update_check();
                    }
                }
                continue;
            }
            match app.apply(action) {
                Ok(true) => running = false,
                Ok(false) => (),
                Err(_) => notice(
                    "요청한 작업을 완료하지 못했습니다. 현재 설정과 연결 상태를 확인해 주세요.",
                ),
            }
        }
        while let Some(action) = tray.take_action() {
            match app.apply_tray_action(action) {
                Ok(true) => running = false,
                Ok(false) => (),
                Err(_) => notice("트레이 작업을 완료하지 못했습니다. 연결 상태를 확인해 주세요."),
            }
        }
        let _ = app.process_account_events(|url| {
            let operation: Vec<u16> = "open\0".encode_utf16().collect();
            let target: Vec<u16> = url.encode_utf16().chain(Some(0)).collect();
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
            if result > 32 {
                Ok(())
            } else {
                Err(io::Error::other("login browser unavailable"))
            }
        });
        let tick = app.tick();
        if !ack_done
            && app.runtime().local_services_failed()
            && std::env::var_os("MINI_MONITOR_UPDATE_ACK_PATH").is_some()
        {
            running = false;
        }
        if tick.is_ok()
            && !ack_done
            && app.runtime().local_services_ready()
            && app.runtime().sensor_snapshot().memory.is_some()
        {
            if let (Some(ack), Ok(nonce)) = (
                std::env::var_os("MINI_MONITOR_UPDATE_ACK_PATH"),
                std::env::var("MINI_MONITOR_UPDATE_ACK_NONCE"),
            ) {
                mini_monitor_native::update::acknowledge_startup(
                    &exe,
                    &PathBuf::from(ack),
                    &nonce,
                    env!("CARGO_PKG_VERSION"),
                )
                .map_err(io::Error::other)?;
            }
            ack_done = true;
        }
        if let Some(outcome) = update_task.as_ref().and_then(|task| task.poll()) {
            update_task = None;
            match outcome.and_then(|prepared| prepared.launch()) {
                Ok(_) => running = false,
                Err(_) => {
                    app.settings().set_action_enabled(
                        mini_monitor_native::settings_window::SettingsAction::CheckUpdate,
                        true,
                    )?;
                    notice("업데이트 준비에 실패했습니다. 기존 프로그램은 변경하지 않았습니다. 잠시 뒤 다시 시도해 주세요.");
                }
            }
        }
        if maintenance
            .as_ref()
            .is_some_and(std::thread::JoinHandle::is_finished)
        {
            if let Some(job) = maintenance.take() {
                let _ = job.join();
            }
        }
        if !cleanup_scheduled
            && options.smoke_seconds.is_none()
            && update_task.is_none()
            && app.runtime().local_services_ready()
            && healthy_started.elapsed() >= Duration::from_secs(60)
        {
            let root = root.clone();
            maintenance = Some(
                std::thread::Builder::new()
                    .name("mini-monitor-retirement".into())
                    .spawn(move || mini_monitor_native::cleanup::cleanup(&root))?,
            );
            cleanup_scheduled = true;
        }
        // Sleep without starving accessibility/COM and window messages.
        unsafe {
            windows_sys::Win32::UI::WindowsAndMessaging::MsgWaitForMultipleObjectsEx(
                0,
                std::ptr::null(),
                100,
                windows_sys::Win32::UI::WindowsAndMessaging::QS_ALLINPUT,
                windows_sys::Win32::UI::WindowsAndMessaging::MWMO_INPUTAVAILABLE,
            );
        }
    }
    Ok(())
}

#[cfg(windows)]
#[derive(Default)]
struct HostOptions {
    resources: Option<std::path::PathBuf>,
    config: Option<std::path::PathBuf>,
    smoke_seconds: Option<u64>,
    smoke_outputs: bool,
    no_account: bool,
    no_serial: bool,
    minimized: bool,
}
#[cfg(windows)]
impl HostOptions {
    fn parse(args: impl Iterator<Item = String>) -> Result<Self, &'static str> {
        let mut options = Self::default();
        let mut args = args;
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--resources" | "--config" => {
                    let path = std::path::PathBuf::from(args.next().ok_or("missing path")?);
                    if !path.is_absolute() {
                        return Err("absolute path required");
                    }
                    if arg == "--resources" {
                        options.resources = Some(path)
                    } else {
                        options.config = Some(path)
                    }
                }
                "--smoke-seconds" => {
                    let seconds = args
                        .next()
                        .ok_or("missing duration")?
                        .parse::<u64>()
                        .map_err(|_| "invalid duration")?;
                    if !(1..=3600).contains(&seconds) {
                        return Err("invalid duration");
                    }
                    options.smoke_seconds = Some(seconds)
                }
                "--no-account" => options.no_account = true,
                "--smoke-outputs" => options.smoke_outputs = true,
                "--no-serial" => options.no_serial = true,
                "--minimized" => options.minimized = true,
                _ => return Err("unsupported option"),
            }
        }
        if options.smoke_outputs && options.smoke_seconds.is_none() {
            return Err("output smoke requires bounded duration");
        }
        Ok(options)
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    #[test]
    fn host_options_reject_bad_paths_unknown_arguments_and_unbounded_smoke() {
        for args in [
            vec!["--resources", "relative"],
            vec!["--smoke-seconds", "0"],
            vec!["--smoke-seconds", "3601"],
            vec!["--unknown"],
        ] {
            assert!(HostOptions::parse(args.into_iter().map(str::to_owned)).is_err());
        }
        let options = HostOptions::parse(
            ["--no-account", "--no-serial", "--smoke-seconds", "1"]
                .into_iter()
                .map(str::to_owned),
        )
        .unwrap();
        assert!(options.no_account && options.no_serial && options.smoke_seconds == Some(1));
    }
}
