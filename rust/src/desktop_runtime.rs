//! Independent desktop monitoring: no USB device is opened by this module.
use crate::{
    account_worker::AccountWorker,
    budget_cycle::BudgetSnapshot,
    budget_worker::BudgetWorker,
    live_readouts::LiveReadouts,
    sensor_worker::{SensorSnapshot, SensorWorker},
    taskbar_controller::{ShellState, TaskbarController},
    taskbar_geometry::Rect,
    taskbar_render::{MetricKind, TaskbarStyle},
};
use std::{collections::HashSet, io, path::PathBuf, time::Duration};

pub struct DesktopRuntime {
    sensor: SensorWorker,
    account: Option<AccountWorker>,
    budget: Option<BudgetWorker>,
    taskbar: Option<TaskbarController>,
    items: Vec<MetricKind>,
}
impl DesktopRuntime {
    pub fn start(interval: Duration, account: Option<AccountWorker>) -> io::Result<Self> {
        Ok(Self {
            sensor: SensorWorker::start_auto(interval)?,
            account,
            budget: None,
            taskbar: None,
            items: Vec::new(),
        })
    }
    pub fn sensor_snapshot(&self) -> SensorSnapshot {
        self.sensor.snapshot()
    }
    pub fn attach_budget(&mut self, worker: BudgetWorker) {
        self.budget = Some(worker);
    }
    pub fn disable_budget(&mut self) {
        self.budget.take();
    }
    pub fn budget_snapshot(&self) -> Option<BudgetSnapshot> {
        self.budget.as_ref().and_then(BudgetWorker::snapshot)
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
        if items.is_empty()
            || items.len() > 6
            || items.iter().collect::<HashSet<_>>().len() != items.len()
        {
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
    pub fn disable_taskbar(&mut self) {
        self.taskbar.take();
        self.items.clear();
    }
    pub fn take_taskbar_position_change(&mut self) -> Option<(i32, i32)> {
        self.taskbar
            .as_mut()
            .and_then(TaskbarController::take_position_change)
    }

    pub fn tick(&mut self, budget: Option<&BudgetSnapshot>) -> io::Result<()> {
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
        if let Some(bar) = self.taskbar.as_mut() {
            bar.tick(&readouts.selected(&self.items).map_err(io::Error::other)?)?;
        }
        Ok(())
    }

    /// Synthetic shell seam for offscreen tests; production callers use tick().
    pub fn tick_with_shell(
        &mut self,
        shell: ShellState,
        budget: Option<&BudgetSnapshot>,
    ) -> io::Result<()> {
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
        if let Some(bar) = self.taskbar.as_mut() {
            bar.tick_with_shell(
                &readouts.selected(&self.items).map_err(io::Error::other)?,
                shell,
            )?;
        }
        Ok(())
    }
}
