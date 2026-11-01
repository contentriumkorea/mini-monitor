//! Isolated synthetic dashboard rendering benchmark; no user data or device access.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        dashboard_layout::{CardKind as K, Orientation},
        dashboard_render::{DashboardEntry, DashboardInput, DashboardRenderer},
    };
    use std::time::{Duration, Instant};
    let entry = |kind, value: &str, details: Vec<&str>, gauge| DashboardEntry {
        kind,
        value: value.into(),
        details: details.into_iter().map(str::to_owned).collect(),
        gauge,
    };
    let mut input = DashboardInput {
        port: "COM3".into(),
        entries: vec![
            entry(
                K::Cpu,
                "16%",
                vec!["Intel Core i7-14700K", "45 C"],
                Some(16.0),
            ),
            entry(
                K::Ram,
                "27%",
                vec!["34.8 / 127.8 GiB", "93.0 GiB FREE"],
                Some(27.0),
            ),
            entry(
                K::Gpu,
                "2%",
                vec!["NVIDIA GeForce RTX 5070 Ti", "42 C", "44 W"],
                Some(2.0),
            ),
            entry(K::Vram, "18%", vec!["2.8 / 15.9 GiB"], Some(18.0)),
            entry(
                K::Codex,
                "85%",
                vec!["7D LEFT", "CREDITS 60,518"],
                Some(85.0),
            ),
        ],
    };
    let mut renderer = DashboardRenderer::new();
    let started = Instant::now();
    let mut frame = 0u64;
    while started.elapsed() < Duration::from_secs(45) {
        let value = frame % 101;
        input.entries[0].value = format!("{value}%");
        input.entries[0].gauge = Some(value as f64);
        std::hint::black_box(renderer.render(&input, Orientation::Landscape, false, None)?);
        frame += 1;
        let next = started + Duration::from_millis(frame * 200);
        std::thread::sleep(next.saturating_duration_since(Instant::now()));
    }
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
