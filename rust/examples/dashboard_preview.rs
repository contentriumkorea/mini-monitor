//! Synthetic physical-display preview; does not read user settings or account data.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        dashboard_layout::{CardKind as K, Orientation},
        dashboard_render::{DashboardEntry, DashboardInput, DashboardRenderer},
    };
    use std::io::Write;
    let output = std::env::args_os()
        .nth(1)
        .ok_or_else(|| std::io::Error::other("missing output path"))?;
    let orientation = if std::env::args().nth(2).as_deref() == Some("portrait") {
        Orientation::Portrait
    } else {
        Orientation::Landscape
    };
    let entry = |kind, value: &str, details: Vec<&str>, gauge| DashboardEntry {
        kind,
        value: value.into(),
        details: details.into_iter().map(str::to_owned).collect(),
        gauge,
    };
    let with_api = std::env::args().any(|arg| arg == "--api");
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
    if with_api {
        input.entries.push(entry(
            K::Api,
            "90%",
            vec!["TOTAL $25.00", "USED $2.50"],
            Some(90.0),
        ));
    }
    let frame = DashboardRenderer::new().render(&input, orientation, with_api, None)?;
    let bytes = frame.pixels.len() as u32;
    let mut file = std::fs::File::create(output)?;
    file.write_all(b"BM")?;
    file.write_all(&(54 + bytes).to_le_bytes())?;
    file.write_all(&[0; 4])?;
    file.write_all(&54u32.to_le_bytes())?;
    file.write_all(&40u32.to_le_bytes())?;
    file.write_all(&(frame.width as i32).to_le_bytes())?;
    file.write_all(&(-(frame.height as i32)).to_le_bytes())?;
    file.write_all(&1u16.to_le_bytes())?;
    file.write_all(&32u16.to_le_bytes())?;
    file.write_all(&0u32.to_le_bytes())?;
    file.write_all(&bytes.to_le_bytes())?;
    file.write_all(&[0; 16])?;
    file.write_all(&frame.pixels)?;
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
