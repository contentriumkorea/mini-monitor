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

#[cfg(windows)]
#[test]
fn budget_worker_runs_with_desktop_sensors_without_usb() {
    use mini_monitor_native::{
        budget::USD_SCALE, budget_worker::BudgetWorker, desktop_runtime::DesktopRuntime,
    };
    use std::{
        thread,
        time::{Duration, Instant},
    };
    use time::{Date, Month, OffsetDateTime};
    let now = OffsetDateTime::now_utc();
    let anchor = Date::from_calendar_date(now.year(), Month::January, 1).unwrap();
    let budget = BudgetWorker::start(
        anchor,
        10000,
        Duration::from_secs(60),
        |_, _, _| Ok(USD_SCALE * 10),
        OffsetDateTime::now_utc,
    )
    .unwrap();
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    runtime.attach_budget(budget);
    let deadline = Instant::now() + Duration::from_secs(3);
    while runtime.budget_snapshot().is_none() {
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(
        runtime.budget_snapshot().unwrap().spent_units,
        USD_SCALE * 10
    );
    runtime.tick(None).unwrap();
    runtime.disable_budget();
    assert!(runtime.budget_snapshot().is_none());
}

#[cfg(windows)]
#[test]
fn running_sensor_snapshot_can_feed_physical_screen_without_starting_usb() {
    use mini_monitor_native::{
        dashboard_data::render_usb_frame,
        dashboard_layout::{CardKind, Orientation},
        dashboard_render::DashboardRenderer,
        desktop_runtime::DesktopRuntime,
    };
    use std::time::Duration;
    let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    std::thread::sleep(Duration::from_millis(600));
    let input = runtime.dashboard_input(Some("Test CPU"), "COM3");
    assert_ne!(
        input
            .entries
            .iter()
            .find(|e| e.kind == CardKind::Cpu)
            .unwrap()
            .value,
        "--"
    );
    assert_ne!(
        input
            .entries
            .iter()
            .find(|e| e.kind == CardKind::Ram)
            .unwrap()
            .value,
        "--"
    );
    let frame = render_usb_frame(
        &mut DashboardRenderer::new(),
        &input,
        Orientation::Landscape,
        false,
    )
    .unwrap();
    assert!(frame.is_full_for(480, 320));
}

#[cfg(windows)]
#[test]
fn dashboard_includes_os_cpu_model_when_no_override_is_supplied() {
    use mini_monitor_native::{
        dashboard_layout::CardKind, desktop_runtime::DesktopRuntime, sensors::cpu_model_name,
    };
    let runtime = DesktopRuntime::start(std::time::Duration::from_millis(250), None).unwrap();
    let input = runtime.dashboard_input(None, "");
    let cpu = input
        .entries
        .iter()
        .find(|e| e.kind == CardKind::Cpu)
        .unwrap();
    assert!(cpu.details.contains(&cpu_model_name().unwrap()));
}

#[cfg(windows)]
#[test]
fn overlay_renders_independently_from_usb_and_taskbar() {
    use mini_monitor_native::{dashboard_layout::Orientation, desktop_runtime::DesktopRuntime};
    let mut runtime = DesktopRuntime::start(std::time::Duration::from_millis(250), None).unwrap();
    runtime
        .enable_overlay(-30000, -30000, 100, 0, Orientation::Landscape, false)
        .unwrap();
    runtime.tick(None).unwrap();
    assert_eq!(runtime.overlay_bounds(), Some((-30000, -30000, 480, 320)));
    assert!(runtime.taskbar_bounds().is_none());
    runtime
        .set_overlay_options(150, 0, Orientation::Portrait, false)
        .unwrap();
    runtime.tick(None).unwrap();
    assert_eq!(runtime.overlay_bounds(), Some((-30000, -30000, 480, 720)));
    assert!(runtime
        .set_overlay_options(201, 0, Orientation::Landscape, false)
        .is_err());
    runtime.tick(None).unwrap();
    assert_eq!(runtime.overlay_bounds(), Some((-30000, -30000, 480, 720)));
    runtime.disable_overlay();
    assert!(runtime.overlay_bounds().is_none());
}

#[cfg(windows)]
#[test]
fn real_sensor_dashboard_reaches_verified_fake_usb_worker() {
    use mini_monitor_native::{
        dashboard_layout::Orientation as Layout,
        desktop_runtime::DesktopRuntime,
        device::{select_device, DeviceInfo, Orientation},
        device_session::DeviceSession,
        device_worker::{DeviceWorker, WorkerStatus},
    };
    use std::{
        io::{self, Write},
        sync::{Arc, Mutex},
        time::{Duration, Instant},
    };
    struct Sink(Arc<Mutex<usize>>);
    impl Write for Sink {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            *self.0.lock().unwrap() += bytes.len();
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    fn target() -> DeviceInfo {
        DeviceInfo {
            port: "COM3".into(),
            vid: 0x1a86,
            pid: 0x5722,
            serial: "USB35INCHIPSV2".into(),
        }
    }
    let count = Arc::new(Mutex::new(0));
    let output = count.clone();
    let worker = DeviceWorker::start(
        25,
        Orientation::Landscape,
        move |_| {
            let dev = target();
            let selected = select_device(std::slice::from_ref(&dev), None).unwrap();
            DeviceSession::verified(selected, &[dev], Sink(output))
        },
        || Ok(vec![target()]),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(4);
    while worker.status() != WorkerStatus::Ready {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(10));
    }
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    runtime.attach_device(worker, Layout::Landscape, false, "COM3".into());
    while *count.lock().unwrap() < 34 + 6 + 480 * 320 * 2 {
        assert!(Instant::now() < deadline);
        runtime.tick(None).unwrap();
        std::thread::sleep(Duration::from_millis(20));
    }
    runtime.detach_device();
}

#[cfg(windows)]
#[test]
fn changing_device_orientation_sends_new_full_frame_after_control_command() {
    use mini_monitor_native::{
        dashboard_layout::Orientation as Layout,
        desktop_runtime::DesktopRuntime,
        device::{select_device, DeviceInfo, Orientation},
        device_session::DeviceSession,
        device_worker::{DeviceWorker, WorkerStatus},
    };
    use std::{
        io::{self, Write},
        sync::{Arc, Mutex},
        time::{Duration, Instant},
    };
    struct Sink(Arc<Mutex<usize>>);
    impl Write for Sink {
        fn write(&mut self, b: &[u8]) -> io::Result<usize> {
            *self.0.lock().unwrap() += b.len();
            Ok(b.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    fn target() -> DeviceInfo {
        DeviceInfo {
            port: "COM3".into(),
            vid: 0x1a86,
            pid: 0x5722,
            serial: "USB35INCHIPSV2".into(),
        }
    }
    let bytes = Arc::new(Mutex::new(0));
    let output = bytes.clone();
    let worker = DeviceWorker::start(
        25,
        Orientation::Landscape,
        move |_| {
            let dev = target();
            let selected = select_device(std::slice::from_ref(&dev), None).unwrap();
            DeviceSession::verified(selected, &[dev], Sink(output))
        },
        || Ok(vec![target()]),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    while worker.status() != WorkerStatus::Ready {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    runtime.attach_device(worker, Layout::Landscape, false, "COM3".into());
    let initial = 34 + 6 + 480 * 320 * 2;
    while *bytes.lock().unwrap() < initial {
        assert!(Instant::now() < deadline);
        runtime.tick(None).unwrap();
        std::thread::sleep(Duration::from_millis(20));
    }
    runtime
        .set_device_options(Orientation::Portrait, 25)
        .unwrap();
    while *bytes.lock().unwrap() < initial + 22 + 6 + 320 * 480 * 2 {
        assert!(Instant::now() < deadline);
        runtime.tick(None).unwrap();
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(runtime.device_status(), Some(WorkerStatus::Ready));
    runtime.detach_device();
}

#[cfg(windows)]
#[test]
fn overlay_render_failure_does_not_stop_taskbar_updates() {
    use mini_monitor_native::{
        dashboard_layout::Orientation,
        desktop_runtime::DesktopRuntime,
        taskbar_controller::ShellState,
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    let mut runtime = DesktopRuntime::start(std::time::Duration::from_millis(250), None).unwrap();
    runtime
        .enable_overlay(i32::MAX, 0, 100, 0, Orientation::Landscape, false)
        .unwrap();
    runtime
        .enable_taskbar(TaskbarStyle::Text, None, -29900, vec![MetricKind::Cpu])
        .unwrap();
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
    assert!(runtime.tick_with_shell(shell, None).is_err());
    assert!(runtime.taskbar_bounds().is_some());
}

#[cfg(windows)]
#[test]
fn usb_reconnection_gets_a_new_full_frame_even_without_a_new_ui_request() {
    use mini_monitor_native::{
        dashboard_layout::Orientation as Layout,
        desktop_runtime::DesktopRuntime,
        device::{select_device, DeviceInfo, Orientation},
        device_session::DeviceSession,
        device_worker::{DeviceWorker, WorkerStatus},
    };
    use std::{
        io::{self, Write},
        sync::{
            atomic::{AtomicBool, AtomicUsize, Ordering},
            Arc, Mutex,
        },
        time::{Duration, Instant},
    };
    struct Sink(Arc<Mutex<usize>>);
    impl Write for Sink {
        fn write(&mut self, b: &[u8]) -> io::Result<usize> {
            *self.0.lock().unwrap() += b.len();
            Ok(b.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    fn target() -> DeviceInfo {
        DeviceInfo {
            port: "COM3".into(),
            vid: 0x1a86,
            pid: 0x5722,
            serial: "USB35INCHIPSV2".into(),
        }
    }
    let alive = Arc::new(AtomicBool::new(true));
    let output = Arc::new(Mutex::new(0));
    let attempts = Arc::new(AtomicUsize::new(0));
    let track = attempts.clone();
    let connected = alive.clone();
    let bytes = output.clone();
    let factory = move || {
        track.fetch_add(1, Ordering::SeqCst);
        let connected = connected.clone();
        let bytes = bytes.clone();
        DeviceWorker::start(
            25,
            Orientation::Landscape,
            move |_| {
                let dev = target();
                let selected = select_device(std::slice::from_ref(&dev), None).unwrap();
                DeviceSession::verified(selected, &[dev], Sink(bytes))
            },
            move || {
                if connected.load(Ordering::SeqCst) {
                    Ok(vec![target()])
                } else {
                    Ok(vec![])
                }
            },
        )
    };
    let mut runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
    runtime
        .attach_reconnecting_device(
            Duration::from_secs(1),
            factory,
            Layout::Landscape,
            false,
            "COM3".into(),
        )
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(8);
    while *output.lock().unwrap() < 34 + 6 + 480 * 320 * 2 {
        assert!(Instant::now() < deadline);
        runtime.tick(None).unwrap();
        std::thread::sleep(Duration::from_millis(30));
    }
    alive.store(false, Ordering::SeqCst);
    while runtime.device_status() == Some(WorkerStatus::Ready) {
        assert!(Instant::now() < deadline);
        runtime.tick(None).unwrap();
        std::thread::sleep(Duration::from_millis(30));
    }
    let before = *output.lock().unwrap();
    alive.store(true, Ordering::SeqCst);
    while *output.lock().unwrap() < before + 34 + 6 + 480 * 320 * 2 {
        assert!(Instant::now() < deadline);
        runtime.tick(None).unwrap();
        std::thread::sleep(Duration::from_millis(30));
    }
    assert!(attempts.load(Ordering::SeqCst) >= 2);
    runtime.detach_device();
}

#[cfg(windows)]
#[test]
fn account_login_intent_is_available_without_usb_or_taskbar() {
    use mini_monitor_native::{
        account_worker::{AccountEvent, AccountStatus, AccountWorker},
        codex_client::RpcTransport,
        desktop_runtime::DesktopRuntime,
    };
    use serde_json::{json, Value};
    use std::time::{Duration, Instant};
    struct LoginMock;
    impl RpcTransport for LoginMock {
        fn request(
            &mut self,
            _: u64,
            method: &str,
            _: Value,
            _: &mut dyn FnMut(&str, &Value),
        ) -> Result<Value, &'static str> {
            match method {
                "account/read" => Ok(json!({"account":null})),
                "account/login/start" => {
                    Ok(json!({"loginId":"fake","authUrl":"https://auth.openai.com/login"}))
                }
                _ => Err("unexpected"),
            }
        }
    }
    let account = AccountWorker::start_with_transport(LoginMock, Duration::from_secs(60)).unwrap();
    let runtime = DesktopRuntime::start(Duration::from_millis(250), Some(account)).unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while runtime.account_snapshot().unwrap().status != AccountStatus::SignedOut {
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(runtime.request_login());
    let event = loop {
        if let Some(event) = runtime.poll_account_event() {
            break event;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(5));
    };
    assert!(matches!(event, AccountEvent::OpenBrowser(_)));
    assert!(runtime.taskbar_bounds().is_none());
    assert!(runtime.device_status().is_none());
}

#[cfg(windows)]
#[test]
fn taskbar_options_update_without_restarting_and_reject_duplicates() {
    use mini_monitor_native::{
        desktop_runtime::DesktopRuntime,
        taskbar_controller::ShellState,
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    let mut runtime = DesktopRuntime::start(std::time::Duration::from_millis(250), None).unwrap();
    runtime
        .enable_taskbar(TaskbarStyle::Text, None, -29900, vec![K::Cpu])
        .unwrap();
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
    runtime.tick_with_shell(shell, None).unwrap();
    let first = runtime.taskbar_bounds().unwrap();
    runtime
        .set_taskbar_options(TaskbarStyle::Text, None, vec![K::Cpu, K::Ram])
        .unwrap();
    runtime.tick_with_shell(shell, None).unwrap();
    let second = runtime.taskbar_bounds().unwrap();
    assert!(second.right - second.left > first.right - first.left);
    assert!(runtime
        .set_taskbar_options(TaskbarStyle::Text, None, vec![K::Cpu, K::Cpu])
        .is_err());
    runtime.tick_with_shell(shell, None).unwrap();
    let after = runtime.taskbar_bounds().unwrap();
    assert_eq!(after.right - after.left, second.right - second.left);
}
