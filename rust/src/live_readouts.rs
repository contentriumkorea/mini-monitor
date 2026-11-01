//! Shared desktop readouts; no dependency on the USB display state.
use crate::{
    account_worker::{AccountSnapshot, AccountStatus},
    budget_cycle::BudgetSnapshot,
    readouts::{api_readout, codex_readout, percent_readout},
    sensor_worker::SensorSnapshot,
    taskbar_render::MetricKind,
};
use std::collections::HashSet;
use time::Date;

// No Debug: strings may contain private usage values.
pub struct LiveReadouts {
    cpu: String,
    ram: String,
    gpu: String,
    vram: String,
    codex: String,
    api: String,
}
impl LiveReadouts {
    pub fn new(
        sensor: Option<&SensorSnapshot>,
        account: Option<&AccountSnapshot>,
        budget: Option<&BudgetSnapshot>,
        today_utc: Date,
    ) -> Self {
        let gpu = sensor.and_then(|s| s.gpu.as_ref());
        let limits = account
            .filter(|a| a.status == AccountStatus::Ready)
            .and_then(|a| a.limits.as_ref());
        let budget = budget.filter(|b| today_utc >= b.period_start && today_utc < b.period_end);
        Self {
            cpu: percent_readout(sensor.and_then(|s| s.cpu_percent)),
            ram: percent_readout(sensor.and_then(|s| s.memory).map(|m| m.used_percent)),
            gpu: percent_readout(gpu.and_then(|g| g.gpu_percent).map(f64::from)),
            vram: percent_readout(gpu.and_then(|g| g.vram).map(|v| v.used_percent)),
            codex: codex_readout(limits),
            api: api_readout(budget),
        }
    }

    pub fn selected(&self, items: &[MetricKind]) -> Result<Vec<(MetricKind, &str)>, &'static str> {
        if items.is_empty()
            || items.len() > 6
            || items.iter().collect::<HashSet<_>>().len() != items.len()
        {
            return Err("invalid items");
        }
        Ok(items
            .iter()
            .map(|item| {
                (
                    *item,
                    match item {
                        MetricKind::Cpu => self.cpu.as_str(),
                        MetricKind::Ram => self.ram.as_str(),
                        MetricKind::Gpu => self.gpu.as_str(),
                        MetricKind::Vram => self.vram.as_str(),
                        MetricKind::Codex => self.codex.as_str(),
                        MetricKind::Api => self.api.as_str(),
                    },
                )
            })
            .collect())
    }
}
