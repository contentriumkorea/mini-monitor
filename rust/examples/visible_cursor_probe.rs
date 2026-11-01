//! Temporary real-taskbar cursor probe; no settings, account, USB or installed files are touched.
#[cfg(windows)]
fn main() -> std::io::Result<()> {
    use mini_monitor_native::{
        readouts::percent_readout,
        sensor_worker::SensorWorker,
        taskbar_controller::TaskbarController,
        taskbar_render::{MetricKind, TaskbarStyle},
        taskbar_shell::query_primary_taskbar,
    };
    use std::time::{Duration, Instant};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DispatchMessageW, GetCursorInfo, LoadCursorW, PeekMessageW, TranslateMessage, CURSORINFO,
        IDC_APPSTARTING, IDC_ARROW, IDC_WAIT, MSG, PM_REMOVE,
    };
    let shell = query_primary_taskbar()?;
    let x = shell
        .bar
        .left
        .saturating_add((shell.bar.right - shell.bar.left) / 2);
    let sensor = SensorWorker::start(Duration::from_secs(1))?;
    let mut bar = TaskbarController::new(TaskbarStyle::Text, None, x)?;
    let started = Instant::now();
    let mut announced = false;
    let mut observed = false;
    while started.elapsed() < Duration::from_secs(90) {
        let cpu = percent_readout(sensor.snapshot().cpu_percent);
        bar.tick(&[(MetricKind::Cpu, &cpu)])?;
        if !announced && bar.last_bounds().is_some() {
            let bounds = bar.last_bounds().unwrap();
            println!(
                "probe_bounds={},{},{},{}",
                bounds.left, bounds.top, bounds.right, bounds.bottom
            );
            announced = true;
        }
        if !observed {
            let mut info: CURSORINFO = unsafe { std::mem::zeroed() };
            info.cbSize = std::mem::size_of::<CURSORINFO>() as u32;
            if unsafe { GetCursorInfo(&mut info) } != 0 {
                if let Some(bounds) = bar.last_bounds() {
                    if info.ptScreenPos.x >= bounds.left
                        && info.ptScreenPos.x < bounds.right
                        && info.ptScreenPos.y >= bounds.top
                        && info.ptScreenPos.y < bounds.bottom
                    {
                        let arrow = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_ARROW) };
                        let wait = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_WAIT) };
                        let busy = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_APPSTARTING) };
                        println!(
                            "cursor_arrow={} cursor_busy={}",
                            info.hCursor == arrow,
                            info.hCursor == wait || info.hCursor == busy
                        );
                        observed = true;
                    }
                }
            }
        }
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
