//! 45-second isolated resource probe: real sensors + offscreen taskbar/overlay, no account or USB.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        dashboard_layout::Orientation,
        desktop_runtime::DesktopRuntime,
        taskbar_controller::ShellState,
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    use std::time::{Duration, Instant};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE,
    };
    let assets = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/icons");
    let mut runtime = DesktopRuntime::start(Duration::from_secs(1), None)?;
    runtime.enable_taskbar(
        TaskbarStyle::Icon,
        Some(assets),
        -29900,
        vec![K::Cpu, K::Ram, K::Gpu, K::Vram, K::Codex],
    )?;
    runtime.enable_overlay(-30000, -30000, 100, 0, Orientation::Landscape, false)?;
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
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(45) {
        runtime.tick_with_shell(shell, None)?;
        let mut message: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    runtime.disable_overlay();
    runtime.disable_taskbar();
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
