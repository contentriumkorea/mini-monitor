//! Explicit physical display test; uses the existing orientation/brightness without saving settings.
#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use mini_monitor_native::{
        config::Settings, dashboard_data::render_usb_frame,
        dashboard_layout::Orientation as Layout, dashboard_render::DashboardRenderer,
        desktop_runtime::DesktopRuntime, device::Orientation, device_connect::ConnectGate,
        windows_serial,
    };
    use std::{path::PathBuf, thread, time::Duration};
    let config = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("local data unavailable")?)
        .join("AI-Mini-Monitor")
        .join("config.json");
    let parsed = Settings::load(&config)?;
    let data: serde_json::Value = serde_json::from_str(&parsed.to_json()?)?;
    let device = data.get("device").ok_or("device settings unavailable")?;
    let brightness = device
        .get("brightness")
        .and_then(|v| v.as_u64())
        .ok_or("brightness unavailable")? as u8;
    let (orientation, layout) = match device.get("rotation").and_then(|v| v.as_str()) {
        Some("landscape") => (Orientation::Landscape, Layout::Landscape),
        Some("landscape_inverted") => (Orientation::ReverseLandscape, Layout::Landscape),
        Some("portrait") => (Orientation::Portrait, Layout::Portrait),
        Some("portrait_inverted") => (Orientation::ReversePortrait, Layout::Portrait),
        _ => return Err("invalid orientation".into()),
    };
    let manual = device.get("manual_port").and_then(|v| v.as_str());
    let gate = ConnectGate::default();
    let mut session = windows_serial::connect(manual, &gate)?;
    println!("stage=connected");
    session.start(brightness, orientation, |duration| {
        thread::sleep(duration);
        true
    })?;
    println!("stage=initialized");
    let runtime = DesktopRuntime::start(Duration::from_secs(1), None)?;
    thread::sleep(Duration::from_millis(1100));
    let frame = render_usb_frame(
        &mut DashboardRenderer::new(),
        &runtime.dashboard_input(None, ""),
        layout,
        false,
    )?;
    session.send_full(&frame).inspect_err(|error| {
        eprintln!(
            "stage=frame_write kind={:?} code={:?}",
            error.kind(),
            error.raw_os_error()
        );
    })?;
    println!("validated_device_connected=true full_frame_write=true");
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
