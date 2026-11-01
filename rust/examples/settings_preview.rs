//! Temporary development preview: no account, configuration, device, registry or network access.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        dashboard_data::dashboard_input,
        dashboard_layout::Orientation,
        dashboard_render::DashboardRenderer,
        settings_window::{SettingsAction, SettingsWindow},
    };
    use std::time::{Duration, Instant};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE,
    };
    let window = SettingsWindow::new_hidden()?;
    let input = dashboard_input(
        None,
        None,
        None,
        time::OffsetDateTime::now_utc().date(),
        None,
        "",
    );
    let frame = DashboardRenderer::new().render(&input, Orientation::Landscape, false, None)?;
    window.set_preview(frame)?;
    let (mut monitor, mut overlay, mut taskbar) = (false, false, false);
    window.set_output_state(monitor, overlay, taskbar)?;
    window.show();
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(90) {
        let mut message: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        while let Some(action) = window.take_action() {
            match action {
                SettingsAction::ToggleMonitor => monitor = !monitor,
                SettingsAction::ToggleOverlay => overlay = !overlay,
                SettingsAction::ToggleTaskbar => taskbar = !taskbar,
                SettingsAction::Quit => return Ok(()),
                _ => {}
            }
            window.set_output_state(monitor, overlay, taskbar)?;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
