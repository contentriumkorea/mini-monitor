//! Native console commands. Diagnostics never open USB or inspect account tokens.
use crate::{
    config::Settings,
    dashboard_layout::{CardKind, Orientation},
    dashboard_render::{DashboardEntry, DashboardFrame, DashboardInput, DashboardRenderer},
};
use std::{
    io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
pub const PREVIEW_STATES: [&str; 12] = [
    "normal",
    "zero",
    "hundred",
    "temperature_warning",
    "memory_99",
    "ai_not_configured",
    "ai_delayed",
    "ai_error",
    "unknown",
    "vram_max",
    "reconnecting",
    "disconnected",
];
fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "invalid command options")
}
fn absolute(path: impl Into<PathBuf>) -> io::Result<PathBuf> {
    let path = path.into();
    Ok(if path.is_absolute() {
        path
    } else {
        std::env::current_dir()?.join(path)
    })
}
fn data_dir() -> io::Result<PathBuf> {
    Ok(
        PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or_else(invalid)?)
            .join("AI-Mini-Monitor"),
    )
}
fn orientation(settings: &Settings) -> io::Result<Orientation> {
    Ok(
        match settings.startup_settings().map_err(|_| invalid())?.rotation {
            crate::device::Orientation::Landscape
            | crate::device::Orientation::ReverseLandscape => Orientation::Landscape,
            _ => Orientation::Portrait,
        },
    )
}
fn demo(state: &str, layout: Orientation) -> io::Result<DashboardFrame> {
    if !PREVIEW_STATES.contains(&state) {
        return Err(invalid());
    }
    let values = match state {
        "zero" => [0; 5],
        "hundred" => [100; 5],
        "memory_99" => [42, 99, 30, 20, 85],
        "vram_max" => [42, 55, 30, 100, 85],
        _ => [42, 55, 30, 20, 85],
    };
    let entries = [
        CardKind::Cpu,
        CardKind::Ram,
        CardKind::Gpu,
        CardKind::Vram,
        CardKind::Codex,
    ]
    .into_iter()
    .enumerate()
    .map(|(index, kind)| {
        let unknown = state == "unknown"
            || state == "disconnected"
            || (index == 4 && ["ai_not_configured", "ai_delayed", "ai_error"].contains(&state));
        DashboardEntry {
            kind,
            value: if unknown {
                "--".into()
            } else {
                format!("{}%", values[index])
            },
            details: vec![if state == "temperature_warning" && index < 3 {
                "DEMO · 95 °C".into()
            } else {
                format!("DEMO · {state}")
            }],
            gauge: (!unknown).then_some(values[index] as f64),
        }
    })
    .collect();
    DashboardRenderer::new().render(
        &DashboardInput {
            port: format!("DEMO · {state}"),
            entries,
        },
        layout,
        false,
        None,
    )
}
fn preview_window(frame: DashboardFrame) -> io::Result<()> {
    use crate::settings_window::{SettingsAction as A, SettingsWindow};
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    let window = SettingsWindow::new_hidden()?;
    for action in [
        A::ToggleMonitor,
        A::ToggleOverlay,
        A::ToggleTaskbar,
        A::FindDevice,
        A::Login,
        A::CancelLogin,
        A::RefreshAccount,
        A::Logout,
        A::ApiSettings,
        A::CheckUpdate,
        A::OverlaySettings,
        A::TaskbarSettings,
        A::DeviceOptionsChanged,
        A::AutostartChanged,
    ] {
        window.set_action_enabled(action, false)?;
    }
    let title: Vec<u16> = "Mini Monitor · DEMO 미리보기"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        SetWindowTextW(window.handle(), title.as_ptr());
    }
    window.set_preview(frame)?;
    window.show();
    loop {
        let mut message: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            if message.message == WM_QUIT {
                return Ok(());
            }
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        if window.take_action() == Some(A::Quit) {
            return Ok(());
        }
        unsafe {
            MsgWaitForMultipleObjectsEx(0, std::ptr::null(), 100, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }
    }
}
fn diagnostic() -> io::Result<serde_json::Value> {
    let worker = crate::sensor_worker::SensorWorker::start_auto(Duration::from_millis(250))?;
    let first = crate::sensors::cpu_times().ok();
    std::thread::sleep(Duration::from_millis(300));
    let second = crate::sensors::cpu_times().ok();
    let data = worker.snapshot();
    let gpu = data.gpu.as_ref();
    let ports = crate::device_discovery::discover()
        .unwrap_or_default()
        .into_iter()
        .map(|d| d.port)
        .collect::<Vec<_>>();
    Ok(
        serde_json::json!({"read_only":true,"serial_port_opened":false,"runtime":{"version":env!("CARGO_PKG_VERSION"),"platform":"Windows x64"},"device":{"exact_targets":ports},"sensors":{"cpu_model":crate::sensors::cpu_model_name().ok(),"cpu_percent":first.zip(second).and_then(|(a,b)|crate::sensors::cpu_usage(a,b)),"memory_percent":data.memory.map(|v|v.used_percent),"memory_total_bytes":data.memory.map(|v|v.total_bytes),"gpu_model":gpu.map(|v|&v.name),"gpu_percent":gpu.and_then(|v|v.gpu_percent),"gpu_temperature":gpu.and_then(|v|v.temperature_c),"vram_used_bytes":gpu.and_then(|v|v.vram).map(|v|v.used_bytes),"vram_total_bytes":gpu.and_then(|v|v.vram).map(|v|v.total_bytes)}}),
    )
}
fn headless(settings: Settings, seconds: f64, no_serial: bool, root: &Path) -> io::Result<()> {
    let startup = settings.startup_settings().map_err(|_| invalid())?;
    let data = data_dir()?;
    let account = crate::account_worker::AccountWorker::start_verified(
        root.join("third_party/codex-app-server/codex-app-server.exe"),
        data.join("codex-home"),
        Duration::from_secs(startup.usage_refresh_seconds),
    )?;
    let mut runtime = crate::desktop_runtime::DesktopRuntime::start(
        Duration::from_millis(startup.sensor_interval_ms),
        Some(account),
    )?;
    if let Some(budget) = startup.budget.as_ref() {
        if let Some(key) =
            crate::secrets::SecretStore::at(data.join("secrets/openai_admin_key.dpapi")).get()?
        {
            runtime.attach_budget(crate::budget_worker::BudgetWorker::start_http(
                budget.anchor,
                budget.total_cents,
                Duration::from_secs(startup.cost_refresh_seconds),
                &key,
            )?);
        }
    }
    let layout = orientation(&settings)?;
    if !no_serial {
        let port = startup.manual_port.clone();
        let rotation = startup.rotation;
        let brightness = startup.brightness;
        runtime.attach_reconnecting_device(
            Duration::from_secs(5),
            move || {
                let port = port.clone();
                crate::device_worker::DeviceWorker::start(
                    brightness,
                    rotation,
                    move |gate| crate::windows_serial::connect(port.as_deref(), gate),
                    crate::device_discovery::discover,
                )
            },
            layout,
            startup.budget.is_some(),
            String::new(),
        )?;
    }
    let deadline = Instant::now() + Duration::from_secs_f64(seconds);
    while Instant::now() < deadline {
        runtime.tick(None)?;
        std::thread::sleep(Duration::from_millis(100));
    }
    let input = runtime.dashboard_input(None, "");
    let frame = DashboardRenderer::new().render(&input, layout, startup.budget.is_some(), None)?;
    crate::png_output::save(&frame, &data.join("last-headless-frame.png"))?;
    Ok(())
}
pub fn run(args: Vec<String>) -> io::Result<i32> {
    let mut config = None;
    let mut preview = None;
    let mut preview_output = None;
    let mut no_window = false;
    let mut previews = None;
    let mut diagnose = None;
    let mut enable = false;
    let mut disable = false;
    let mut no_serial = false;
    let mut minimized = false;
    let mut headless_seconds = None;
    let mut desktop_smoke = None;
    let mut arguments = args.into_iter().peekable();
    while let Some(arg) = arguments.next() {
        match arg.as_str() {
            "--version" => {
                println!("Mini Monitor {}", env!("CARGO_PKG_VERSION"));
                return Ok(0);
            }
            "--help" | "-h" => {
                println!("Mini Monitor: --config PATH, --preview [STATE], --preview-output PNG, --no-window, --render-previews DIR, --diagnose [JSON], --enable-autostart, --disable-autostart, --minimized, --no-serial");
                return Ok(0);
            }
            "--config" => config = Some(absolute(arguments.next().ok_or_else(invalid)?)?),
            "--preview" => {
                preview = Some(if arguments.peek().is_some_and(|a| !a.starts_with("--")) {
                    arguments.next().unwrap()
                } else {
                    "normal".into()
                })
            }
            "--preview-output" => {
                preview_output = Some(absolute(arguments.next().ok_or_else(invalid)?)?)
            }
            "--render-previews" => {
                previews = Some(absolute(arguments.next().ok_or_else(invalid)?)?)
            }
            "--diagnose" => {
                diagnose = Some(if arguments.peek().is_some_and(|a| !a.starts_with("--")) {
                    arguments.next().unwrap()
                } else {
                    "-".into()
                })
            }
            "--enable-autostart" => enable = true,
            "--disable-autostart" => disable = true,
            "--no-window" => no_window = true,
            "--no-serial" => no_serial = true,
            "--minimized" => minimized = true,
            "--headless-run" | "--desktop-smoke" => {
                let value = arguments
                    .next()
                    .ok_or_else(invalid)?
                    .parse::<f64>()
                    .map_err(|_| invalid())?;
                if !value.is_finite() || !(0.0..=3600.0).contains(&value) || value == 0.0 {
                    return Err(invalid());
                }
                if arg == "--headless-run" {
                    headless_seconds = Some(value)
                } else {
                    desktop_smoke = Some(value)
                }
            }
            _ => return Err(invalid()),
        }
    }
    if desktop_smoke.is_some() && !no_serial {
        return Err(invalid());
    }
    let settings =
        Settings::load_or_default(&config.clone().unwrap_or(data_dir()?.join("config.json")))
            .map_err(|_| invalid())?;
    let root = std::env::current_exe()?
        .parent()
        .ok_or_else(invalid)?
        .to_path_buf();
    let gui = if root.join("Mini-Monitor.exe").is_file() {
        root.join("Mini-Monitor.exe")
    } else {
        root.join("mini-monitor-native.exe")
    };
    if enable || disable {
        let command = crate::startup::build_command(
            gui.to_str().ok_or_else(invalid)?,
            config.as_deref().and_then(Path::to_str),
        )?;
        let mut store = crate::startup::WindowsStartupStore::new();
        if enable {
            crate::startup::enable(&mut store, &command)?;
        } else {
            crate::startup::disable(&mut store, &command)?;
        }
        println!("Autostart settings updated.");
        return Ok(0);
    }
    if let Some(destination) = diagnose {
        let text = serde_json::to_string_pretty(&diagnostic()?)?;
        if destination == "-" {
            println!("{text}");
        } else {
            let path = absolute(destination)?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(path, text)?;
        }
        return Ok(0);
    }
    if let Some(directory) = previews {
        std::fs::create_dir_all(&directory)?;
        for state in PREVIEW_STATES {
            crate::png_output::save(
                &demo(state, Orientation::Landscape)?,
                &directory.join(format!("{state}.png")),
            )?;
        }
        std::fs::write(
            directory.join("manifest.json"),
            serde_json::to_vec_pretty(
                &serde_json::json!({"native_size":[480,320],"demo_data":true,"files":PREVIEW_STATES.map(|state|format!("{state}.png"))}),
            )?,
        )?;
        return Ok(0);
    }
    if let Some(state) = preview {
        let output =
            preview_output.unwrap_or(data_dir()?.join("previews").join(format!("{state}.png")));
        let frame = demo(&state, orientation(&settings)?)?;
        crate::png_output::save(&frame, &output)?;
        if !no_window {
            preview_window(frame)?;
        }
        return Ok(0);
    }
    if let Some(seconds) = headless_seconds {
        headless(settings, seconds, no_serial, &root)?;
        return Ok(0);
    }
    let mut command = std::process::Command::new(gui);
    if let Some(config) = config {
        command.arg("--config").arg(config);
    }
    if minimized {
        command.arg("--minimized");
    }
    if no_serial {
        command.arg("--no-serial");
    }
    if let Some(seconds) = desktop_smoke {
        command
            .arg("--smoke-seconds")
            .arg((seconds.ceil() as u64).to_string());
    }
    Ok(command.status()?.code().unwrap_or(1))
}
