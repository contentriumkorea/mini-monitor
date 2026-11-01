//! Synthetic screenshot for visual QA; does not read user settings or account data.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::taskbar_render::{MetricKind as K, TaskbarRenderer, TaskbarStyle};
    use std::io::Write;
    let output = std::env::args_os()
        .nth(1)
        .ok_or_else(|| std::io::Error::other("missing output path"))?;
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/icons");
    let style = match std::env::args().nth(2).as_deref() {
        Some("icon") => TaskbarStyle::Icon,
        Some("both") => TaskbarStyle::Both,
        _ => TaskbarStyle::Text,
    };
    let frame = TaskbarRenderer::new().render(
        &[
            (K::Cpu, "7%"),
            (K::Ram, "29%"),
            (K::Gpu, "1%"),
            (K::Vram, "18%"),
            (K::Codex, "85%"),
        ],
        192,
        style,
        Some(&root),
    )?;
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
