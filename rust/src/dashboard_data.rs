//! Combine real snapshots for the physical screen and desktop overlay.
use crate::{
    account_worker::{AccountSnapshot, AccountStatus},
    budget::USD_SCALE,
    budget_cycle::BudgetSnapshot,
    codex::Credit,
    dashboard_layout::CardKind,
    dashboard_render::{DashboardEntry, DashboardInput},
    readouts::{api_readout, codex_readout, percent_readout},
    sensor_worker::SensorSnapshot,
};
use time::Date;

/// Render an opaque dashboard and package exactly one full USB frame.
pub fn render_usb_frame(
    renderer: &mut crate::dashboard_render::DashboardRenderer,
    input: &DashboardInput,
    orientation: crate::dashboard_layout::Orientation,
    with_api: bool,
) -> std::io::Result<crate::device::BitmapTransfer> {
    let frame = renderer.render(input, orientation, with_api, None)?;
    let rgb = frame.to_opaque_rgb()?;
    let pixels =
        crate::device::rgb888_to_rgb565le(&rgb, frame.width as usize, frame.height as usize)
            .map_err(std::io::Error::other)?;
    crate::device::bitmap_transfer(
        0,
        0,
        frame.width,
        frame.height,
        &pixels,
        frame.width,
        frame.height,
    )
    .map_err(std::io::Error::other)
}

fn clean(value: &str) -> String {
    let mut out = String::new();
    for c in value.chars().filter(|c| !c.is_control()) {
        if out.len() + c.len_utf8() > 128 {
            break;
        }
        out.push(c);
    }
    out
}
fn gib(bytes: u64) -> f64 {
    bytes as f64 / 1_073_741_824.0
}
fn grouped(value: &str) -> Option<String> {
    let mut pieces = value.split('.');
    let digits = pieces.next()?;
    if digits.is_empty() || digits.len() > 12 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    if let Some(fraction) = pieces.next() {
        if fraction.is_empty()
            || fraction.len() > 32
            || !fraction.bytes().all(|b| b.is_ascii_digit())
        {
            return None;
        }
    }
    if pieces.next().is_some() {
        return None;
    }
    let raw = digits.parse::<u64>().ok()?.to_string();
    let mut result = String::new();
    for (i, c) in raw.chars().enumerate() {
        if i > 0 && (raw.len() - i).is_multiple_of(3) {
            result.push(',');
        }
        result.push(c);
    }
    Some(result)
}
fn usd_cents(units: u128) -> u128 {
    let unit = USD_SCALE / 100;
    units / unit + u128::from(units % unit >= unit / 2)
}
fn usd(cents: u128) -> String {
    format!("${}.{:02}", cents / 100, cents % 100)
}

pub fn dashboard_input(
    sensor: Option<&SensorSnapshot>,
    account: Option<&AccountSnapshot>,
    budget: Option<&BudgetSnapshot>,
    today: Date,
    cpu_model: Option<&str>,
    port: &str,
) -> DashboardInput {
    let cpu = sensor.and_then(|v| v.cpu_percent);
    let memory = sensor.and_then(|v| v.memory);
    let gpu = sensor.and_then(|v| v.gpu.as_ref());
    let gpu_pct = gpu.and_then(|v| v.gpu_percent).map(f64::from);
    let vram = gpu.and_then(|v| v.vram);
    let limits = account
        .filter(|a| a.status == AccountStatus::Ready)
        .and_then(|a| a.limits.as_ref());
    let budget = budget.filter(|v| today >= v.period_start && today < v.period_end);
    let mut cpu_details = Vec::new();
    if let Some(model) = cpu_model {
        let model = clean(model);
        if !model.is_empty() {
            cpu_details.push(model);
        }
    }
    let memory_details = memory
        .filter(|m| m.available_bytes <= m.total_bytes)
        .map(|m| {
            vec![
                format!(
                    "{:.1} / {:.1} GiB",
                    gib(m.total_bytes - m.available_bytes),
                    gib(m.total_bytes)
                ),
                format!("{:.1} GiB FREE", gib(m.available_bytes)),
            ]
        })
        .unwrap_or_default();
    let mut gpu_details = Vec::new();
    if let Some(g) = gpu {
        let name = clean(&g.name);
        if !name.is_empty() {
            gpu_details.push(name);
        }
        if let Some(temp) = g.temperature_c {
            gpu_details.push(format!("{temp} °C"));
        }
        if let Some(power) = g.power_watts {
            if power.is_finite() && power >= 0.0 {
                gpu_details.push(format!("{power:.0} W"));
            }
        }
    }
    let vram_details = vram
        .map(|v| {
            vec![format!(
                "{:.1} / {:.1} GiB",
                gib(v.used_bytes),
                gib(v.total_bytes)
            )]
        })
        .unwrap_or_default();
    let codex = codex_readout(limits);
    let codex_gauge = limits
        .and_then(|v| v.windows.iter().max_by_key(|w| w.duration_minutes))
        .map(|v| v.remaining_percent)
        .filter(|v| v.is_finite() && *v >= 0.0)
        .map(|value| {
            if limits.is_some_and(|v| {
                v.windows
                    .iter()
                    .any(|w| w.remaining_percent.is_finite() && w.remaining_percent <= 0.0)
            }) {
                0.0
            } else {
                value
            }
        });
    let codex_details = limits
        .map(|v| match &v.credit {
            Credit::Unknown => vec!["CREDITS --".into()],
            Credit::Unlimited => vec!["CREDITS UNLIMITED".into()],
            Credit::Balance(n) => vec![format!(
                "CREDITS {}",
                grouped(n).unwrap_or_else(|| "--".into())
            )],
        })
        .unwrap_or_default();
    let api_details = budget
        .map(|v| {
            vec![
                format!("USED {}", usd(usd_cents(v.spent_units))),
                format!("TOTAL {}", usd(u128::from(v.total_cents))),
            ]
        })
        .unwrap_or_default();
    DashboardInput {
        port: clean(port),
        entries: vec![
            DashboardEntry {
                kind: CardKind::Cpu,
                value: percent_readout(cpu),
                details: cpu_details,
                gauge: cpu,
            },
            DashboardEntry {
                kind: CardKind::Ram,
                value: percent_readout(memory.map(|v| v.used_percent)),
                details: memory_details,
                gauge: memory.map(|v| v.used_percent),
            },
            DashboardEntry {
                kind: CardKind::Gpu,
                value: percent_readout(gpu_pct),
                details: gpu_details,
                gauge: gpu_pct,
            },
            DashboardEntry {
                kind: CardKind::Vram,
                value: percent_readout(vram.map(|v| v.used_percent)),
                details: vram_details,
                gauge: vram.map(|v| v.used_percent),
            },
            DashboardEntry {
                kind: CardKind::Codex,
                value: codex,
                details: codex_details,
                gauge: codex_gauge,
            },
            DashboardEntry {
                kind: CardKind::Api,
                value: api_readout(budget),
                details: api_details,
                gauge: budget.map(|v| (100.0 - v.used_percent).clamp(0.0, 100.0)),
            },
        ],
    }
}
