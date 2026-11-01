//! Synthetic offscreen taskbar + live CPU/RAM/NVIDIA probe. No account or USB access.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        readouts::percent_readout,
        sensor_worker::SensorWorker,
        taskbar_controller::{ShellState, TaskbarController},
        taskbar_geometry::Rect,
        taskbar_render::{MetricKind as K, TaskbarStyle},
        taskbar_shell::FullscreenState,
    };
    use std::{
        path::Path,
        time::{Duration, Instant},
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, PeekMessageW, TranslateMessage, MSG, PM_REMOVE,
    };
    let assets = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/icons");
    let sensor = SensorWorker::start_auto(Duration::from_secs(1))?;
    let mut bar = TaskbarController::new(TaskbarStyle::Icon, Some(assets), -29900)?;
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
        let sample = sensor.snapshot();
        let cpu = percent_readout(sample.cpu_percent);
        let ram = percent_readout(sample.memory.map(|m| m.used_percent));
        let gpu = percent_readout(
            sample
                .gpu
                .as_ref()
                .and_then(|g| g.gpu_percent)
                .map(f64::from),
        );
        let vram = percent_readout(
            sample
                .gpu
                .as_ref()
                .and_then(|g| g.vram)
                .map(|v| v.used_percent),
        );
        bar.tick_with_shell(
            &[
                (K::Cpu, &cpu),
                (K::Ram, &ram),
                (K::Gpu, &gpu),
                (K::Vram, &vram),
                (K::Codex, "85%"),
            ],
            shell,
        )?;
        let mut message: MSG = unsafe { std::mem::zeroed() };
        while unsafe { PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) } != 0 {
            unsafe {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    bar.hide();
    Ok(())
}
#[cfg(not(windows))]
fn main() {}
