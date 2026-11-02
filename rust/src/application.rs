//! Connects settings intents to independent runtime outputs without touching installation state.
use crate::{
    dashboard_layout::Orientation,
    dashboard_render::DashboardRenderer,
    desktop_runtime::DesktopRuntime,
    device_worker::DeviceWorker,
    settings_window::{SettingsAction, SettingsWindow},
    taskbar_controller::ShellState,
    taskbar_render::{MetricKind, TaskbarStyle},
};
use std::{
    io,
    path::PathBuf,
    rc::Rc,
    time::{Duration, Instant},
};

/// Do not expose login URLs in errors or logs; validate again at the OS boundary.
pub fn dispatch_account_event(
    event: crate::account_worker::AccountEvent,
    mut open: impl FnMut(&str) -> io::Result<()>,
) -> io::Result<()> {
    match event {
        crate::account_worker::AccountEvent::OpenBrowser(url) => {
            if !crate::codex::trusted_login_url(&url) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "untrusted login address",
                ));
            }
            open(&url).map_err(|_| io::Error::other("could not open login"))
        }
    }
}

pub struct OutputOptions {
    pub overlay_x: i32,
    pub overlay_y: i32,
    pub overlay_scale: u32,
    pub overlay_opacity: u8,
    pub orientation: Orientation,
    pub with_api: bool,
    pub taskbar_x: i32,
    pub taskbar_style: TaskbarStyle,
    pub taskbar_items: Vec<MetricKind>,
    pub icon_dir: Option<PathBuf>,
}
type DeviceFactory = Rc<dyn Fn() -> io::Result<DeviceWorker>>;
pub struct ApplicationCore {
    runtime: DesktopRuntime,
    settings: SettingsWindow,
    options: OutputOptions,
    monitor: bool,
    overlay: bool,
    taskbar: bool,
    factory: Option<DeviceFactory>,
    repository: Option<crate::config::SettingsRepository>,
    overlay_form: Option<crate::options_window::OptionsWindow>,
    taskbar_form: Option<crate::options_window::OptionsWindow>,
    api_form: Option<crate::options_window::OptionsWindow>,
    secret_store: Option<crate::secrets::SecretStore>,
    startup_controller: Option<(Box<dyn crate::startup::StartupStore>, String)>,
    pending_overlay_position: Option<(i32, i32)>,
    pending_taskbar_position: Option<(i32, i32)>,
    next_position_save: Instant,
    device_options: (crate::device::Orientation, u8),
    preview: DashboardRenderer,
    next_preview: Instant,
    discovery: Option<std::sync::mpsc::Receiver<io::Result<usize>>>,
    detected: Option<usize>,
}
impl ApplicationCore {
    pub fn new(
        runtime: DesktopRuntime,
        settings: SettingsWindow,
        options: OutputOptions,
    ) -> io::Result<Self> {
        settings.set_output_state(false, false, false)?;
        let device_orientation = match options.orientation {
            Orientation::Landscape => crate::device::Orientation::Landscape,
            Orientation::Portrait => crate::device::Orientation::Portrait,
        };
        settings.set_device_options(device_orientation, 25)?;
        for action in [
            SettingsAction::ToggleMonitor,
            SettingsAction::FindDevice,
            SettingsAction::ApiSettings,
            SettingsAction::AutostartChanged,
            SettingsAction::DeviceOptionsChanged,
        ] {
            settings.set_action_enabled(action, false)?;
        }
        let account_available = runtime.account_snapshot().is_some();
        settings.set_action_enabled(
            SettingsAction::CheckUpdate,
            runtime.release_snapshot().is_some(),
        )?;
        for action in [
            SettingsAction::Login,
            SettingsAction::CancelLogin,
            SettingsAction::RefreshAccount,
            SettingsAction::Logout,
        ] {
            settings.set_action_enabled(action, account_available)?;
        }
        Ok(Self {
            runtime,
            settings,
            options,
            monitor: false,
            overlay: false,
            taskbar: false,
            factory: None,
            repository: None,
            overlay_form: None,
            taskbar_form: None,
            api_form: None,
            secret_store: None,
            startup_controller: None,
            pending_overlay_position: None,
            pending_taskbar_position: None,
            next_position_save: Instant::now(),
            device_options: (device_orientation, 25),
            preview: DashboardRenderer::new(),
            next_preview: Instant::now(),
            discovery: None,
            detected: None,
        })
    }
    /// The factory may start only the validated, explicitly selected display worker.
    pub fn set_device_factory<F>(&mut self, factory: F) -> io::Result<()>
    where
        F: Fn() -> io::Result<DeviceWorker> + 'static,
    {
        self.factory = Some(Rc::new(factory));
        self.settings
            .set_action_enabled(SettingsAction::FindDevice, true)?;
        self.settings
            .set_action_enabled(SettingsAction::ToggleMonitor, true)?;
        self.settings
            .set_action_enabled(SettingsAction::DeviceOptionsChanged, true)
    }
    pub fn runtime(&self) -> &DesktopRuntime {
        &self.runtime
    }
    pub fn process_account_events(
        &self,
        mut open: impl FnMut(&str) -> io::Result<()>,
    ) -> io::Result<()> {
        while let Some(event) = self.runtime.poll_account_event() {
            if let Err(error) = dispatch_account_event(event, &mut open) {
                let _ = self.runtime.cancel_login();
                return Err(error);
            }
        }
        Ok(())
    }
    pub fn settings(&self) -> &SettingsWindow {
        &self.settings
    }
    pub fn translate_message(
        &self,
        message: &mut windows_sys::Win32::UI::WindowsAndMessaging::MSG,
    ) -> bool {
        use windows_sys::Win32::UI::{
            Input::KeyboardAndMouse::{GetFocus, VK_ESCAPE, VK_RETURN, VK_TAB},
            WindowsAndMessaging::*,
        };
        for form in [&self.overlay_form, &self.taskbar_form, &self.api_form]
            .into_iter()
            .flatten()
        {
            if form.translate_message(message) {
                return true;
            }
        }
        let hwnd = self.settings.handle();
        if message.message == WM_KEYDOWN
            && (message.hwnd == hwnd || unsafe { IsChild(hwnd, message.hwnd) } != 0)
        {
            if message.wParam == VK_TAB as usize {
                return unsafe { IsDialogMessageW(hwnd, message) } != 0;
            }
            if message.wParam == VK_ESCAPE as usize {
                unsafe {
                    SendMessageW(hwnd, WM_CLOSE, 0, 0);
                }
                return true;
            }
            if message.wParam == VK_RETURN as usize {
                let focus = unsafe { GetFocus() };
                let mut class = [0u16; 32];
                let length = unsafe { GetClassNameW(focus, class.as_mut_ptr(), 32) };
                if String::from_utf16_lossy(&class[..length.max(0) as usize])
                    .eq_ignore_ascii_case("BUTTON")
                {
                    unsafe {
                        SendMessageW(focus, BM_CLICK, 0, 0);
                    }
                    return true;
                }
            }
        }
        false
    }
    /// Attach after restoring startup preferences to avoid rewriting the config during startup.
    pub fn attach_repository(&mut self, repository: crate::config::SettingsRepository) {
        self.repository = Some(repository);
    }
    pub fn attach_startup_controller(
        &mut self,
        store: Box<dyn crate::startup::StartupStore>,
        command: String,
    ) -> io::Result<()> {
        self.settings.set_autostart(
            store
                .read()?
                .as_deref()
                .is_some_and(|value| crate::startup::equivalent(value, &command)),
        )?;
        self.startup_controller = Some((store, command));
        self.settings
            .set_action_enabled(SettingsAction::AutostartChanged, true)
    }
    pub fn attach_secret_store(&mut self, store: crate::secrets::SecretStore) -> io::Result<()> {
        self.secret_store = Some(store);
        self.settings
            .set_action_enabled(SettingsAction::ApiSettings, true)
    }

    fn record_position_changes(
        &mut self,
        overlay: Option<(i32, i32)>,
        taskbar: Option<(i32, i32)>,
    ) -> io::Result<()> {
        if let Some((x, y)) = overlay {
            self.options.overlay_x = x;
            self.options.overlay_y = y;
            self.pending_overlay_position = Some((x, y));
        }
        if let Some((x, y)) = taskbar {
            self.options.taskbar_x = x;
            self.pending_taskbar_position = Some((x, y));
        }
        if self.pending_overlay_position.is_none() && self.pending_taskbar_position.is_none() {
            return Ok(());
        }
        if Instant::now() < self.next_position_save {
            return Ok(());
        }
        let Some(repository) = self.repository.as_mut() else {
            return Ok(());
        };
        let result = repository.update(|settings| {
            settings.with_display_positions(
                self.pending_overlay_position,
                self.pending_taskbar_position,
            )
        });
        if result.is_err() {
            self.next_position_save = Instant::now() + Duration::from_secs(5);
            return Err(io::Error::other("display position could not be saved"));
        }
        self.pending_overlay_position = None;
        self.pending_taskbar_position = None;
        Ok(())
    }

    fn save_drag_positions(&mut self) -> io::Result<()> {
        let overlay = self.runtime.take_overlay_position_change();
        let taskbar = self.runtime.take_taskbar_position_change();
        self.record_position_changes(overlay, taskbar)
    }

    pub fn configure_overlay(&mut self, scale: u32, opacity_percent: u8) -> io::Result<()> {
        if !(50..=200).contains(&scale) || opacity_percent > 100 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid overlay appearance",
            ));
        }
        let opacity = ((u32::from(opacity_percent) * 255 + 50) / 100) as u8;
        if self.overlay {
            self.runtime.set_overlay_options(
                scale,
                opacity,
                self.options.orientation,
                self.options.with_api,
            )?;
        }
        self.options.overlay_scale = scale;
        self.options.overlay_opacity = opacity;
        if let Some(repository) = self.repository.as_mut() {
            repository
                .update(|s| s.with_overlay_appearance(scale, opacity_percent))
                .map_err(|_| io::Error::other("settings could not be saved"))?;
        }
        Ok(())
    }

    pub fn configure_taskbar(
        &mut self,
        style: TaskbarStyle,
        icon_dir: Option<PathBuf>,
        items: Vec<MetricKind>,
    ) -> io::Result<()> {
        if !crate::desktop_runtime::valid_items(&items) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid taskbar items",
            ));
        }
        if self.taskbar {
            self.runtime
                .set_taskbar_options(style, icon_dir.clone(), items.clone())?;
        }
        self.options.taskbar_style = style;
        self.options.icon_dir = icon_dir;
        self.options.taskbar_items = items;
        if let Some(repository) = self.repository.as_mut() {
            let names: Vec<&str> = self
                .options
                .taskbar_items
                .iter()
                .map(|item| match item {
                    MetricKind::Cpu => "cpu",
                    MetricKind::Ram => "ram",
                    MetricKind::Gpu => "gpu",
                    MetricKind::Vram => "vram",
                    MetricKind::Codex => "codex",
                    MetricKind::Api => "api",
                })
                .collect();
            let style = match style {
                TaskbarStyle::Text => "text",
                TaskbarStyle::Icon => "icon",
                TaskbarStyle::Both => "both",
            };
            repository
                .update(|s| s.with_taskbar_options(&names, style))
                .map_err(|_| io::Error::other("settings could not be saved"))?;
        }
        Ok(())
    }
    /// Restore saved desktop views; the physical display is never started implicitly.
    pub fn restore_preferences(
        &mut self,
        startup: &crate::config::StartupSettings,
        icon_dir: Option<PathBuf>,
    ) -> io::Result<()> {
        if self.monitor || self.overlay || self.taskbar {
            return Err(io::Error::other("startup already applied"));
        }
        self.settings
            .set_device_options(startup.rotation, startup.brightness)?;
        self.device_options = (startup.rotation, startup.brightness);
        self.options.orientation = match startup.rotation {
            crate::device::Orientation::Portrait | crate::device::Orientation::ReversePortrait => {
                Orientation::Portrait
            }
            crate::device::Orientation::Landscape
            | crate::device::Orientation::ReverseLandscape => Orientation::Landscape,
        };
        self.options.with_api = startup.budget.is_some();
        self.options.overlay_scale = startup.overlay_scale;
        self.options.overlay_opacity = startup.overlay_opacity;
        if let Some((x, y)) = startup.overlay_position {
            self.options.overlay_x = x;
            self.options.overlay_y = y;
        }
        if let Some((x, _)) = startup.taskbar_position {
            self.options.taskbar_x = x;
        }
        self.configure_taskbar(
            startup.taskbar_style,
            icon_dir,
            startup.taskbar_items.clone(),
        )?;
        let overlay = if startup.overlay_enabled {
            self.apply(SettingsAction::ToggleOverlay).map(|_| ())
        } else {
            Ok(())
        };
        let taskbar = if startup.taskbar_enabled {
            self.apply(SettingsAction::ToggleTaskbar).map(|_| ())
        } else {
            Ok(())
        };
        overlay.and(taskbar)
    }
    pub fn monitor_enabled(&self) -> bool {
        self.monitor
    }

    fn preferences(&mut self) -> io::Result<()> {
        if let Some(result) = self.discovery.as_ref().and_then(|rx| rx.try_recv().ok()) {
            self.discovery = None;
            self.detected = result.ok();
            self.settings
                .set_action_enabled(SettingsAction::FindDevice, true)?;
        }
        if let Some(mut values) = self
            .api_form
            .as_ref()
            .map(|f| f.take_apply())
            .transpose()?
            .flatten()
        {
            let result = self.save_api_settings(&values);
            values[3].clear();
            let form = self.api_form.as_ref().unwrap();
            form.clear_edit(3);
            form.message(if result.is_ok() {
                "저장했습니다. 공식 API에서 자동 조회합니다."
            } else {
                "저장 실패: 총액·시작일·관리자 API 키를 확인하세요."
            });
        }
        if let Some(values) = self
            .overlay_form
            .as_ref()
            .map(|f| f.take_apply())
            .transpose()?
            .flatten()
        {
            let result = values[0]
                .parse::<u32>()
                .ok()
                .zip(values[1].parse::<u8>().ok())
                .ok_or_else(|| io::Error::other("invalid overlay settings"))
                .and_then(|(scale, opacity)| self.configure_overlay(scale, opacity));
            self.overlay_form
                .as_ref()
                .unwrap()
                .message(if result.is_ok() {
                    "저장했습니다."
                } else {
                    "설정을 저장하지 못했습니다. 값을 확인해 주세요."
                });
        }
        if let Some(values) = self
            .taskbar_form
            .as_ref()
            .map(|f| f.take_apply())
            .transpose()?
            .flatten()
        {
            let kinds = [
                MetricKind::Cpu,
                MetricKind::Ram,
                MetricKind::Gpu,
                MetricKind::Vram,
                MetricKind::Codex,
                MetricKind::Api,
            ];
            let items = kinds
                .into_iter()
                .zip(&values)
                .filter_map(|(kind, value)| (value == "true").then_some(kind))
                .collect();
            let style = match values[6].as_str() {
                "0" => TaskbarStyle::Text,
                "1" => TaskbarStyle::Icon,
                _ => TaskbarStyle::Both,
            };
            let result = self.configure_taskbar(style, self.options.icon_dir.clone(), items);
            let result = result.and_then(|_| {
                let shell = crate::taskbar_shell::query_primary_taskbar()?;
                let width = self
                    .runtime
                    .taskbar_bounds()
                    .map_or(0, |r| r.right - r.left);
                let right = shell
                    .notification
                    .map_or(shell.bar.right, |r| r.left)
                    .saturating_sub(width);
                let span = (right - shell.bar.left).max(0);
                let percent = values[7]
                    .parse::<i64>()
                    .map_err(|_| io::Error::other("invalid position"))?
                    .clamp(0, 100);
                let x = (i64::from(shell.bar.left) + i64::from(span) * percent / 100) as i32;
                self.runtime.set_taskbar_x(x);
                self.record_position_changes(None, Some((x, shell.bar.top)))
            });
            self.taskbar_form
                .as_ref()
                .unwrap()
                .message(if result.is_ok() {
                    "저장했습니다."
                } else {
                    "한 개 이상의 항목을 선택하고 다시 적용해 주세요."
                });
        }
        Ok(())
    }

    fn save_api_settings(&mut self, values: &[String]) -> io::Result<()> {
        let repository = self
            .repository
            .as_mut()
            .ok_or_else(|| io::Error::other("settings unavailable"))?;
        let store = self
            .secret_store
            .as_ref()
            .ok_or_else(|| io::Error::other("secure storage unavailable"))?;
        crate::budget_settings::save(
            repository,
            store,
            values[0] == "true",
            &values[1],
            &values[2],
            &values[3],
        )?;
        let startup = repository
            .settings()
            .startup_settings()
            .map_err(|_| io::Error::other("settings unavailable"))?;
        let enabled = startup.budget.is_some();
        if let Some(budget) = startup.budget {
            let key = store
                .get()?
                .ok_or_else(|| io::Error::other("key unavailable"))?;
            let worker = crate::budget_worker::BudgetWorker::start_http(
                budget.anchor,
                budget.total_cents,
                Duration::from_secs(startup.cost_refresh_seconds),
                &key,
            )?;
            self.runtime.attach_budget(worker);
        } else {
            self.runtime.disable_budget();
        }
        self.options.with_api = enabled;
        self.runtime.set_api_enabled(enabled);
        self.next_preview = Instant::now();
        Ok(())
    }

    /// Tray commands share the same output state and persistence as settings.
    pub fn apply_tray_action(&mut self, action: crate::tray::TrayAction) -> io::Result<bool> {
        match action {
            crate::tray::TrayAction::OpenSettings => {
                self.settings.show();
                Ok(false)
            }
            crate::tray::TrayAction::ToggleOverlay => self.apply(SettingsAction::ToggleOverlay),
            crate::tray::TrayAction::ReconnectDisplay => {
                if !self.monitor {
                    return Err(io::Error::other("display is not running"));
                }
                self.apply(SettingsAction::ToggleMonitor)?;
                self.apply(SettingsAction::ToggleMonitor)
            }
            crate::tray::TrayAction::Exit => self.apply(SettingsAction::Quit),
        }
    }

    /// Returns true only when application exit was explicitly requested.
    pub fn apply(&mut self, action: SettingsAction) -> io::Result<bool> {
        match action {
            SettingsAction::FindDevice => {
                if self.discovery.is_none() {
                    let (send, receive) = std::sync::mpsc::sync_channel(1);
                    std::thread::Builder::new()
                        .name("mini-monitor-discovery".into())
                        .spawn(move || {
                            let _ = send.send(
                                crate::device_discovery::discover().map(|devices| devices.len()),
                            );
                        })?;
                    self.discovery = Some(receive);
                    self.settings
                        .set_action_enabled(SettingsAction::FindDevice, false)?;
                }
            }
            SettingsAction::AutostartChanged => {
                let enabled = self.settings.autostart_checked()?;
                let (store, command) = self
                    .startup_controller
                    .as_mut()
                    .ok_or_else(|| io::Error::other("startup settings unavailable"))?;
                let result = if enabled {
                    crate::startup::enable(store.as_mut(), command)
                } else {
                    crate::startup::disable(store.as_mut(), command)
                };
                self.settings.set_autostart(
                    store
                        .read()?
                        .as_deref()
                        .is_some_and(|value| crate::startup::equivalent(value, command)),
                )?;
                result?;
            }
            SettingsAction::ApiSettings => {
                use crate::options_window::{Field, OptionsWindow};
                let repository = self
                    .repository
                    .as_ref()
                    .ok_or_else(|| io::Error::other("settings unavailable"))?;
                let value: serde_json::Value =
                    serde_json::from_str(&repository.settings().to_json()?)
                        .map_err(|_| io::Error::other("settings unavailable"))?;
                let api = &value["openai"];
                self.api_form = Some(OptionsWindow::new(
                    self.settings.handle(),
                    "OpenAI API 사용량 설정",
                    vec![
                        Field::Check(
                            "OpenAI API 모니터링",
                            api["enabled"].as_bool().unwrap_or(false),
                        ),
                        Field::Edit(
                            "총액 (USD)",
                            api["total_usd"].as_str().unwrap_or("").into(),
                            false,
                        ),
                        Field::Edit(
                            "시작일 (YYYY-MM-DD, 매월 자동 갱신)",
                            api["start_date"].as_str().unwrap_or("").into(),
                            false,
                        ),
                        Field::Edit("관리자 API 키 (비워두면 기존 키 유지)", String::new(), true),
                    ],
                )?);
                self.api_form.as_ref().unwrap().show();
            }
            SettingsAction::OverlaySettings => {
                use crate::options_window::{Field, OptionsWindow};
                self.overlay_form = Some(OptionsWindow::new(
                    self.settings.handle(),
                    "PC 상태창 설정",
                    vec![
                        Field::Slider(
                            "화면 크기 (50~200%)",
                            50,
                            200,
                            self.options.overlay_scale as u16,
                        ),
                        Field::Slider(
                            "배경 불투명도 (0~100%, 글자는 유지)",
                            0,
                            100,
                            ((u32::from(self.options.overlay_opacity) * 100 + 127) / 255) as u16,
                        ),
                    ],
                )?);
                self.overlay_form.as_ref().unwrap().show();
            }
            SettingsAction::TaskbarSettings => {
                use crate::options_window::{Field, OptionsWindow};
                let choices = [
                    (MetricKind::Cpu, "CPU"),
                    (MetricKind::Ram, "RAM"),
                    (MetricKind::Gpu, "GPU"),
                    (MetricKind::Vram, "VRAM"),
                    (MetricKind::Codex, "Codex"),
                    (MetricKind::Api, "OpenAI API"),
                ];
                let mut fields: Vec<Field> = choices
                    .into_iter()
                    .map(|(kind, label)| {
                        Field::Check(label, self.options.taskbar_items.contains(&kind))
                    })
                    .collect();
                fields.push(Field::Choice(
                    "표시 방식",
                    vec!["텍스트 + %", "아이콘 + %", "아이콘 + 텍스트 + %"],
                    match self.options.taskbar_style {
                        TaskbarStyle::Text => 0,
                        TaskbarStyle::Icon => 1,
                        TaskbarStyle::Both => 2,
                    },
                ));
                let shell = crate::taskbar_shell::query_primary_taskbar()?;
                let width = self
                    .runtime
                    .taskbar_bounds()
                    .map_or(0, |r| r.right - r.left);
                let span = (shell.notification.map_or(shell.bar.right, |r| r.left)
                    - width
                    - shell.bar.left)
                    .max(1);
                let position = ((i64::from(self.options.taskbar_x) - i64::from(shell.bar.left))
                    * 100
                    / i64::from(span))
                .clamp(0, 100) as u16;
                fields.push(Field::Slider(
                    "좌우 위치 (왼쪽 0 ~ 오른쪽 100)",
                    0,
                    100,
                    position,
                ));
                self.taskbar_form = Some(OptionsWindow::new(
                    self.settings.handle(),
                    "작업표시줄 바 설정",
                    fields,
                )?);
                self.taskbar_form.as_ref().unwrap().show();
            }
            SettingsAction::ToggleMonitor => {
                if self.monitor {
                    self.runtime.detach_device();
                    self.monitor = false;
                } else {
                    let factory = self
                        .factory
                        .clone()
                        .ok_or_else(|| io::Error::other("display setup unavailable"))?;
                    self.runtime.attach_reconnecting_device(
                        Duration::from_secs(5),
                        move || factory(),
                        self.options.orientation,
                        self.options.with_api,
                        String::new(),
                    )?;
                    self.runtime
                        .set_device_options(self.device_options.0, self.device_options.1)?;
                    self.monitor = true;
                }
            }
            SettingsAction::ToggleOverlay => {
                if self.overlay {
                    self.runtime.disable_overlay();
                    self.overlay = false;
                } else {
                    self.runtime.enable_overlay(
                        self.options.overlay_x,
                        self.options.overlay_y,
                        self.options.overlay_scale,
                        self.options.overlay_opacity,
                        self.options.orientation,
                        self.options.with_api,
                    )?;
                    if let Err(error) = self.runtime.prime_overlay() {
                        self.runtime.disable_overlay();
                        return Err(error);
                    }
                    self.overlay = true;
                }
            }
            SettingsAction::ToggleTaskbar => {
                if self.taskbar {
                    self.runtime.disable_taskbar();
                    self.taskbar = false;
                } else {
                    self.runtime.enable_taskbar(
                        self.options.taskbar_style,
                        self.options.icon_dir.clone(),
                        self.options.taskbar_x,
                        self.options.taskbar_items.clone(),
                    )?;
                    self.taskbar = true;
                }
            }
            SettingsAction::Login => {
                if !self.runtime.request_login() {
                    return Err(io::Error::other("login unavailable"));
                }
            }
            SettingsAction::CancelLogin => {
                if !self.runtime.cancel_login() {
                    return Err(io::Error::other("login cancellation unavailable"));
                }
            }
            SettingsAction::RefreshAccount => {
                if !self.runtime.refresh_account() {
                    return Err(io::Error::other("account refresh unavailable"));
                }
            }
            SettingsAction::Logout => {
                if !self.runtime.request_logout() {
                    return Err(io::Error::other("logout unavailable"));
                }
            }
            SettingsAction::DeviceOptionsChanged => {
                if self.factory.is_none() {
                    return Err(io::Error::other("display setup unavailable"));
                }
                let (orientation, brightness) = self.settings.device_options()?;
                if self.monitor {
                    self.runtime.set_device_options(orientation, brightness)?;
                }
                self.device_options = (orientation, brightness);
                self.options.orientation = match orientation {
                    crate::device::Orientation::Portrait
                    | crate::device::Orientation::ReversePortrait => Orientation::Portrait,
                    crate::device::Orientation::Landscape
                    | crate::device::Orientation::ReverseLandscape => Orientation::Landscape,
                };
                if self.overlay {
                    self.runtime.set_overlay_options(
                        self.options.overlay_scale,
                        self.options.overlay_opacity,
                        self.options.orientation,
                        self.options.with_api,
                    )?;
                }
                self.next_preview = Instant::now();
            }
            SettingsAction::Quit => return Ok(true),
            _ => {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "settings action not yet implemented",
                ))
            }
        }
        self.settings
            .set_output_state(self.monitor, self.overlay, self.taskbar)?;
        if let Some(repository) = self.repository.as_mut() {
            let result = match action {
                SettingsAction::ToggleTaskbar => {
                    repository.update(|s| s.with_taskbar_enabled(self.taskbar))
                }
                SettingsAction::ToggleOverlay => {
                    repository.update(|s| s.with_overlay_enabled(self.overlay))
                }
                SettingsAction::DeviceOptionsChanged => {
                    let rotation = match self.device_options.0 {
                        crate::device::Orientation::Landscape => "landscape",
                        crate::device::Orientation::ReverseLandscape => "landscape_inverted",
                        crate::device::Orientation::Portrait => "portrait",
                        crate::device::Orientation::ReversePortrait => "portrait_inverted",
                    };
                    repository.update(|s| s.with_device_options(rotation, self.device_options.1))
                }
                _ => Ok(()),
            };
            result.map_err(|_| io::Error::other("settings could not be saved"))?;
        }
        Ok(false)
    }
    fn update_preview(&mut self) -> io::Result<()> {
        if !self.settings.is_visible() {
            return Ok(());
        }
        let now = Instant::now();
        if now < self.next_preview {
            return Ok(());
        }
        let input = self.runtime.dashboard_input(None, "");
        if let (false, Some(count)) = (self.monitor, self.detected) {
            self.settings.set_device_detection(count)?;
        } else {
            self.settings
                .set_device_status(self.monitor, self.runtime.device_status())?;
        }
        let status = self
            .runtime
            .account_snapshot()
            .map(|a| a.status)
            .unwrap_or(crate::account_worker::AccountStatus::SignedOut);
        self.settings.set_account_status(status)?;
        if let Some(snapshot) = self.runtime.release_snapshot() {
            self.settings.set_release_status(&snapshot)?;
        }
        let frame = self.preview.render(
            &input,
            self.options.orientation,
            self.options.with_api,
            None,
        )?;
        self.settings.set_preview(frame)?;
        self.next_preview = now + Duration::from_secs(1);
        Ok(())
    }
    pub fn tick(&mut self) -> io::Result<()> {
        self.preferences()?;
        let output = self.runtime.tick(None);
        let preview = self.update_preview();
        let positions = self.save_drag_positions();
        output.and(preview).and(positions)
    }
    pub fn tick_with_shell(&mut self, shell: ShellState) -> io::Result<()> {
        self.preferences()?;
        let output = self.runtime.tick_with_shell(shell, None);
        let preview = self.update_preview();
        let positions = self.save_drag_positions();
        output.and(preview).and(positions)
    }
}

#[cfg(test)]
mod position_tests {
    use super::*;

    #[test]
    fn preview_keeps_detected_device_caption_stable_when_monitor_is_off() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use windows_sys::Win32::{
            Foundation::HWND,
            UI::{
                Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
                WindowsAndMessaging::*,
            },
        };
        unsafe extern "system" fn count(
            hwnd: HWND,
            msg: u32,
            wp: usize,
            lp: isize,
            _id: usize,
            data: usize,
        ) -> isize {
            if msg == WM_SETTEXT {
                (*(data as *const AtomicUsize)).fetch_add(1, Ordering::Relaxed);
            }
            DefSubclassProc(hwnd, msg, wp, lp)
        }
        let runtime = DesktopRuntime::start(Duration::from_millis(250), None).unwrap();
        let settings = SettingsWindow::new_hidden().unwrap();
        let options = OutputOptions {
            overlay_x: -30000,
            overlay_y: -30000,
            overlay_scale: 100,
            overlay_opacity: 0,
            orientation: Orientation::Landscape,
            with_api: false,
            taskbar_x: -30000,
            taskbar_style: TaskbarStyle::Text,
            taskbar_items: vec![MetricKind::Cpu],
            icon_dir: None,
        };
        let mut app = ApplicationCore::new(runtime, settings, options).unwrap();
        app.detected = Some(1);
        unsafe {
            ShowWindow(app.settings.handle(), SW_SHOWNOACTIVATE);
        }
        app.update_preview().unwrap();
        let calls = AtomicUsize::new(0);
        let label = unsafe { GetDlgItem(app.settings.handle(), 102) };
        unsafe {
            SetWindowSubclass(label, Some(count), 124, &calls as *const _ as usize);
        }
        app.next_preview = Instant::now();
        app.update_preview().unwrap();
        unsafe {
            RemoveWindowSubclass(label, Some(count), 124);
        }
        assert_eq!(
            calls.load(Ordering::Relaxed),
            0,
            "discovery and stopped status must not alternate every second"
        );
    }

    #[test]
    fn failed_position_save_is_retried_without_losing_latest_drag() {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().join("later");
        let path = directory.join("config.json");
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
        app.attach_repository(crate::config::SettingsRepository::new(
            path.clone(),
            crate::config::Settings::parse("{}").unwrap(),
        ));
        assert!(app
            .record_position_changes(Some((10, 20)), Some((30, 40)))
            .is_err());
        std::fs::create_dir(directory).unwrap();
        app.next_position_save = Instant::now();
        app.record_position_changes(None, Some((50, 60))).unwrap();
        let settings = crate::config::Settings::load(&path).unwrap();
        let value: serde_json::Value = serde_json::from_str(&settings.to_json().unwrap()).unwrap();
        assert_eq!(value["overlay"]["x"], 10);
        assert_eq!(value["overlay"]["y"], 20);
        assert_eq!(value["overlay"]["taskbar_x"], 50);
        assert_eq!(value["overlay"]["taskbar_y"], 60);
    }

    #[test]
    fn taskbar_drag_reaches_persistent_settings_only_on_release() {
        use crate::{taskbar_geometry::Rect, taskbar_shell::FullscreenState};
        use windows_sys::Win32::UI::WindowsAndMessaging::{
            SendMessageW, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE,
        };
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
        app.attach_repository(crate::config::SettingsRepository::new(
            path.clone(),
            crate::config::Settings::parse("{}").unwrap(),
        ));
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
        let old = app.runtime().taskbar_bounds().unwrap();
        let hwnd = app.runtime().test_taskbar_handle().unwrap();
        unsafe {
            SendMessageW(hwnd, WM_LBUTTONDOWN, 1, 10);
            SendMessageW(hwnd, WM_MOUSEMOVE, 1, 20);
        }
        app.tick_with_shell(shell).unwrap();
        let before = crate::config::Settings::load(&path)
            .unwrap()
            .to_json()
            .unwrap();
        assert!(
            serde_json::from_str::<serde_json::Value>(&before).unwrap()["overlay"]["taskbar_x"]
                .is_null()
        );
        unsafe {
            SendMessageW(hwnd, WM_LBUTTONUP, 0, 20);
        }
        app.tick_with_shell(shell).unwrap();
        let after = crate::config::Settings::load(&path)
            .unwrap()
            .to_json()
            .unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&after).unwrap()["overlay"]["taskbar_x"],
            old.left + 10
        );
    }
}
