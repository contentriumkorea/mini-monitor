//! Independent desktop monitoring: no USB device is opened by this module.
use crate::{
    account_worker::{AccountEvent, AccountSnapshot, AccountWorker},
    budget_cycle::BudgetSnapshot,
    budget_worker::BudgetWorker,
    dashboard_layout::Orientation,
    dashboard_render::{DashboardInput, DashboardRenderer},
    device_supervisor::DeviceSupervisor,
    device_worker::{DeviceWorker, WorkerStatus},
    live_readouts::LiveReadouts,
    overlay_controller::OverlayController,
    sensor_worker::{SensorSnapshot, SensorWorker},
    taskbar_controller::{ShellState, TaskbarController},
    taskbar_geometry::Rect,
    taskbar_render::{MetricKind, TaskbarStyle},
};

type DeviceFactory = Box<dyn FnMut() -> io::Result<DeviceWorker>>;
enum DeviceSource {
    Single(DeviceWorker),
    Reconnecting(DeviceSupervisor<DeviceFactory>),
}
impl DeviceSource {
    fn tick(&mut self) {
        if let Self::Reconnecting(supervisor) = self {
            supervisor.tick(Instant::now());
        }
    }
    fn worker(&self) -> Option<&DeviceWorker> {
        match self {
            Self::Single(worker) => Some(worker),
            Self::Reconnecting(supervisor) => supervisor.worker(),
        }
    }
    fn generation(&self) -> u64 {
        match self {
            Self::Single(_) => 1,
            Self::Reconnecting(supervisor) => supervisor.generation(),
        }
    }
}
struct DeviceOutput {
    source: DeviceSource,
    orientation: Orientation,
    with_api: bool,
    port: String,
    renderer: DashboardRenderer,
    last: Option<(DashboardInput, usize, u64)>,
    started: Instant,
    desired: Option<(u8, crate::device::Orientation)>,
    applied: Option<(u64, u8, crate::device::Orientation)>,
}
use std::{
    collections::HashSet,
    io,
    path::PathBuf,
    time::{Duration, Instant},
};

pub(crate) fn valid_items(items: &[MetricKind]) -> bool {
    !items.is_empty()
        && items.len() <= 6
        && items.iter().collect::<HashSet<_>>().len() == items.len()
}

pub struct DesktopRuntime {
    sensor: SensorWorker,
    account: Option<AccountWorker>,
    budget: Option<BudgetWorker>,
    release: Option<crate::release_worker::ReleaseWorker>,
    cpu_model: Option<String>,
    device: Option<DeviceOutput>,
    overlay: Option<OverlayController>,
    overlay_orientation: Orientation,
    overlay_api: bool,
    overlay_shown: bool,
    overlay_started: Instant,
    taskbar: Option<TaskbarController>,
    items: Vec<MetricKind>,
}
impl DesktopRuntime {
    #[cfg(test)]
    pub(crate) fn test_taskbar_handle(&self) -> Option<windows_sys::Win32::Foundation::HWND> {
        self.taskbar.as_ref().map(TaskbarController::handle)
    }

    pub fn start(interval: Duration, account: Option<AccountWorker>) -> io::Result<Self> {
        Ok(Self {
            sensor: SensorWorker::start_auto(interval)?,
            account,
            budget: None,
            release: None,
            cpu_model: crate::sensors::cpu_model_name().ok(),
            device: None,
            overlay: None,
            overlay_orientation: Orientation::Landscape,
            overlay_api: false,
            overlay_shown: false,
            overlay_started: Instant::now(),
            taskbar: None,
            items: Vec::new(),
        })
    }
    pub fn sensor_snapshot(&self) -> SensorSnapshot {
        self.sensor.snapshot()
    }
    pub fn account_snapshot(&self) -> Option<AccountSnapshot> {
        self.account.as_ref().map(AccountWorker::snapshot)
    }
    pub fn local_services_ready(&self) -> bool {
        self.account
            .as_ref()
            .is_none_or(AccountWorker::runtime_ready)
    }
    pub fn local_services_failed(&self) -> bool {
        self.account.as_ref().is_some_and(|worker| {
            !worker.runtime_ready()
                && worker.snapshot().status == crate::account_worker::AccountStatus::Error
        })
    }
    pub fn request_login(&self) -> bool {
        self.account
            .as_ref()
            .is_some_and(AccountWorker::request_login)
    }
    pub fn cancel_login(&self) -> bool {
        self.account
            .as_ref()
            .is_some_and(AccountWorker::cancel_login)
    }
    pub fn request_logout(&self) -> bool {
        self.account
            .as_ref()
            .is_some_and(AccountWorker::request_logout)
    }
    pub fn refresh_account(&self) -> bool {
        self.account
            .as_ref()
            .is_some_and(AccountWorker::request_refresh)
    }
    pub fn poll_account_event(&self) -> Option<AccountEvent> {
        self.account.as_ref().and_then(AccountWorker::poll_event)
    }
    /// Attaching does not open a device; the caller explicitly constructs a guarded worker.
    pub fn attach_device(
        &mut self,
        worker: DeviceWorker,
        orientation: Orientation,
        with_api: bool,
        port: String,
    ) {
        self.device = Some(DeviceOutput {
            source: DeviceSource::Single(worker),
            orientation,
            with_api,
            port,
            renderer: DashboardRenderer::new(),
            last: None,
            started: Instant::now(),
            desired: None,
            applied: None,
        });
    }
    pub fn attach_reconnecting_device<F>(
        &mut self,
        interval: Duration,
        factory: F,
        orientation: Orientation,
        with_api: bool,
        port: String,
    ) -> io::Result<()>
    where
        F: FnMut() -> io::Result<DeviceWorker> + 'static,
    {
        let factory: DeviceFactory = Box::new(factory);
        self.device = Some(DeviceOutput {
            source: DeviceSource::Reconnecting(DeviceSupervisor::new(interval, factory)?),
            orientation,
            with_api,
            port,
            renderer: DashboardRenderer::new(),
            last: None,
            started: Instant::now(),
            desired: None,
            applied: None,
        });
        Ok(())
    }
    pub fn detach_device(&mut self) {
        self.device.take();
    }
    pub fn set_device_options(
        &mut self,
        orientation: crate::device::Orientation,
        brightness: u8,
    ) -> io::Result<()> {
        if !(1..=50).contains(&brightness) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid brightness",
            ));
        }
        let device = self
            .device
            .as_mut()
            .ok_or_else(|| io::Error::other("display not attached"))?;
        device.orientation = match orientation {
            crate::device::Orientation::Portrait | crate::device::Orientation::ReversePortrait => {
                Orientation::Portrait
            }
            crate::device::Orientation::Landscape
            | crate::device::Orientation::ReverseLandscape => Orientation::Landscape,
        };
        device.desired = Some((brightness, orientation));
        device.last = None;
        Ok(())
    }
    pub fn device_status(&self) -> Option<WorkerStatus> {
        self.device
            .as_ref()
            .and_then(|device| device.source.worker())
            .map(DeviceWorker::status)
    }
    fn tick_device(&mut self) -> io::Result<()> {
        if let Some(device) = self.device.as_mut() {
            device.source.tick();
        }
        let Some(device) = self.device.as_ref() else {
            return Ok(());
        };
        if device
            .source
            .worker()
            .is_none_or(|worker| worker.status() != WorkerStatus::Ready)
        {
            return Ok(());
        }
        let port = device.port.clone();
        let input = self.dashboard_input(None, &port);
        let Some(device) = self.device.as_mut() else {
            return Ok(());
        };
        let page = (device.started.elapsed().as_secs() / 4) as usize;
        let generation = device.source.generation();
        if let Some((brightness, orientation)) = device.desired {
            let requested = (generation, brightness, orientation);
            if device.applied != Some(requested) {
                device
                    .source
                    .worker()
                    .ok_or_else(|| io::Error::other("display not ready"))?
                    .set_display(brightness, orientation)?;
                device.applied = Some(requested);
            }
        }
        if device
            .last
            .as_ref()
            .is_some_and(|(last, last_page, last_generation)| {
                *last == input && *last_page == page && *last_generation == generation
            })
        {
            return Ok(());
        }
        device.renderer.set_detail_page(page);
        let frame = crate::dashboard_data::render_usb_frame(
            &mut device.renderer,
            &input,
            device.orientation,
            device.with_api,
        )?;
        device
            .source
            .worker()
            .ok_or_else(|| io::Error::other("display not ready"))?
            .submit_full(frame)
            .map_err(|_| io::Error::other("display not ready"))?;
        device.last = Some((input, page, generation));
        Ok(())
    }
    pub fn attach_budget(&mut self, worker: BudgetWorker) {
        self.budget = Some(worker);
    }
    pub fn set_api_enabled(&mut self, enabled: bool) {
        self.overlay_api = enabled;
        if let Some(device) = self.device.as_mut() {
            device.with_api = enabled;
            device.last = None;
        }
    }
    pub fn attach_release(&mut self, worker: crate::release_worker::ReleaseWorker) {
        self.release = Some(worker);
    }
    pub fn request_update_check(&self) -> bool {
        self.release
            .as_ref()
            .is_some_and(crate::release_worker::ReleaseWorker::request_check)
    }
    pub fn release_snapshot(&self) -> Option<crate::release_worker::ReleaseSnapshot> {
        self.release
            .as_ref()
            .map(crate::release_worker::ReleaseWorker::snapshot)
    }
    pub fn disable_budget(&mut self) {
        self.budget.take();
    }
    pub fn budget_snapshot(&self) -> Option<BudgetSnapshot> {
        self.budget.as_ref().and_then(BudgetWorker::snapshot)
    }
    pub fn dashboard_input(&self, cpu_model: Option<&str>, port: &str) -> DashboardInput {
        let sensor = self.sensor.snapshot();
        let account = self.account.as_ref().map(AccountWorker::snapshot);
        let budget = self.budget_snapshot();
        crate::dashboard_data::dashboard_input(
            Some(&sensor),
            account.as_ref(),
            budget.as_ref(),
            time::OffsetDateTime::now_utc().date(),
            cpu_model.or(self.cpu_model.as_deref()),
            port,
        )
    }
    pub fn enable_overlay(
        &mut self,
        x: i32,
        y: i32,
        scale: u32,
        opacity: u8,
        orientation: Orientation,
        with_api: bool,
    ) -> io::Result<()> {
        self.overlay = Some(OverlayController::new(x, y, scale, opacity)?);
        self.overlay_orientation = orientation;
        self.overlay_api = with_api;
        self.overlay_shown = false;
        self.overlay_started = Instant::now();
        Ok(())
    }
    pub fn disable_overlay(&mut self) {
        self.overlay.take();
        self.overlay_shown = false;
    }
    pub fn set_overlay_options(
        &mut self,
        scale: u32,
        opacity: u8,
        orientation: Orientation,
        with_api: bool,
    ) -> io::Result<()> {
        let overlay = self
            .overlay
            .as_mut()
            .ok_or_else(|| io::Error::other("overlay not enabled"))?;
        overlay.set_options(scale, opacity)?;
        self.overlay_orientation = orientation;
        self.overlay_api = with_api;
        Ok(())
    }
    pub fn overlay_bounds(&self) -> Option<(i32, i32, u32, u32)> {
        self.overlay.as_ref().and_then(OverlayController::bounds)
    }
    pub fn take_overlay_position_change(&mut self) -> Option<(i32, i32)> {
        self.overlay
            .as_mut()
            .and_then(OverlayController::take_position_change)
    }
    pub fn prime_overlay(&mut self) -> io::Result<()> {
        self.tick_overlay()
    }
    fn tick_overlay(&mut self) -> io::Result<()> {
        if self.overlay.is_none() {
            return Ok(());
        }
        let input = self.dashboard_input(None, "");
        if let Some(overlay) = self.overlay.as_mut() {
            overlay.render(
                &input,
                self.overlay_orientation,
                self.overlay_api,
                (self.overlay_started.elapsed().as_secs() / 4) as usize,
            )?;
            if !self.overlay_shown {
                overlay.show()?;
                self.overlay_shown = true;
            }
        }
        Ok(())
    }
    pub fn taskbar_bounds(&self) -> Option<Rect> {
        self.taskbar
            .as_ref()
            .and_then(TaskbarController::last_bounds)
    }

    pub fn enable_taskbar(
        &mut self,
        style: TaskbarStyle,
        assets: Option<PathBuf>,
        desired_x: i32,
        items: Vec<MetricKind>,
    ) -> io::Result<()> {
        if !valid_items(&items) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid taskbar items",
            ));
        }
        let bar = TaskbarController::new(style, assets, desired_x)?;
        self.taskbar = Some(bar);
        self.items = items;
        Ok(())
    }
    pub fn set_taskbar_options(
        &mut self,
        style: TaskbarStyle,
        assets: Option<PathBuf>,
        items: Vec<MetricKind>,
    ) -> io::Result<()> {
        if !valid_items(&items) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid taskbar items",
            ));
        }
        let bar = self
            .taskbar
            .as_mut()
            .ok_or_else(|| io::Error::other("taskbar not enabled"))?;
        bar.set_style(style, assets);
        self.items = items;
        Ok(())
    }
    pub fn disable_taskbar(&mut self) {
        self.taskbar.take();
        self.items.clear();
    }
    pub fn set_taskbar_x(&mut self, x: i32) {
        if let Some(bar) = self.taskbar.as_mut() {
            bar.set_desired_x(x);
        }
    }
    pub fn take_taskbar_position_change(&mut self) -> Option<(i32, i32)> {
        self.taskbar
            .as_mut()
            .and_then(TaskbarController::take_position_change)
    }

    pub fn tick(&mut self, budget: Option<&BudgetSnapshot>) -> io::Result<()> {
        let overlay_result = self.tick_overlay();
        let sensor = self.sensor.snapshot();
        let account = self.account.as_ref().map(AccountWorker::snapshot);
        let worker_budget = self.budget_snapshot();
        let budget = budget.or(worker_budget.as_ref());
        let readouts = LiveReadouts::new(
            Some(&sensor),
            account.as_ref(),
            budget,
            time::OffsetDateTime::now_utc().date(),
        );
        let bar_result = if let Some(bar) = self.taskbar.as_mut() {
            readouts
                .selected(&self.items)
                .map_err(io::Error::other)
                .and_then(|items| bar.tick(&items))
        } else {
            Ok(())
        };
        let device_result = self.tick_device();
        overlay_result.and(bar_result).and(device_result)
    }

    /// Synthetic shell seam for offscreen tests; production callers use tick().
    pub fn tick_with_shell(
        &mut self,
        shell: ShellState,
        budget: Option<&BudgetSnapshot>,
    ) -> io::Result<()> {
        let overlay_result = self.tick_overlay();
        let sensor = self.sensor.snapshot();
        let account = self.account.as_ref().map(AccountWorker::snapshot);
        let worker_budget = self.budget_snapshot();
        let budget = budget.or(worker_budget.as_ref());
        let readouts = LiveReadouts::new(
            Some(&sensor),
            account.as_ref(),
            budget,
            time::OffsetDateTime::now_utc().date(),
        );
        let bar_result = if let Some(bar) = self.taskbar.as_mut() {
            readouts
                .selected(&self.items)
                .map_err(io::Error::other)
                .and_then(|items| bar.tick_with_shell(&items, shell))
        } else {
            Ok(())
        };
        let device_result = self.tick_device();
        overlay_result.and(bar_result).and(device_result)
    }
}
