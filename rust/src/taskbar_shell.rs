//! Read-only snapshot of Explorer's primary taskbar in a per-monitor DPI context.
use crate::taskbar_geometry::{taskbar_visible, Rect};
use std::{io, ptr};
use windows_sys::Win32::{
    Foundation::{HWND, RECT},
    Graphics::Gdi::{GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST},
    UI::{
        HiDpi::{
            GetDpiForWindow, SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT,
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        },
        WindowsAndMessaging::{FindWindowExW, FindWindowW, GetWindowRect, IsWindowVisible},
    },
};

pub struct TaskbarSnapshot {
    pub bar: Rect,
    pub notification: Option<Rect>,
    pub monitor: Rect,
    pub dpi: u32,
    pub visible: bool,
}

struct DpiGuard(DPI_AWARENESS_CONTEXT);
impl Drop for DpiGuard {
    fn drop(&mut self) {
        unsafe {
            SetThreadDpiAwarenessContext(self.0);
        }
    }
}

fn rect(value: RECT) -> Rect {
    Rect {
        left: value.left,
        top: value.top,
        right: value.right,
        bottom: value.bottom,
    }
}

fn window_rect(hwnd: HWND) -> Option<Rect> {
    let mut result: RECT = unsafe { std::mem::zeroed() };
    if unsafe { GetWindowRect(hwnd, &mut result) } == 0 {
        None
    } else {
        Some(rect(result))
    }
}

/// Reads the primary Explorer bar only; callers must not infer secondary-monitor placement.
pub fn query_primary_taskbar() -> io::Result<TaskbarSnapshot> {
    let previous =
        unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
    if previous.is_null() {
        return Err(io::Error::other("DPI context unavailable"));
    }
    let _guard = DpiGuard(previous);
    let class: Vec<u16> = "Shell_TrayWnd\0".encode_utf16().collect();
    let tray_class: Vec<u16> = "TrayNotifyWnd\0".encode_utf16().collect();
    let shell = unsafe { FindWindowW(class.as_ptr(), ptr::null()) };
    if shell.is_null() {
        return Err(io::Error::other("taskbar unavailable"));
    }
    let bar = window_rect(shell).ok_or_else(|| io::Error::other("taskbar bounds unavailable"))?;
    let notify = unsafe { FindWindowExW(shell, ptr::null_mut(), tray_class.as_ptr(), ptr::null()) };
    let notification = if notify.is_null() {
        None
    } else {
        window_rect(notify)
    };
    let display = unsafe { MonitorFromWindow(shell, MONITOR_DEFAULTTONEAREST) };
    let mut info: MONITORINFO = unsafe { std::mem::zeroed() };
    info.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
    if display.is_null() || unsafe { GetMonitorInfoW(display, &mut info) } == 0 {
        return Err(io::Error::other("monitor bounds unavailable"));
    }
    let monitor = rect(info.rcMonitor);
    let dpi = unsafe { GetDpiForWindow(shell) };
    if !(96..=768).contains(&dpi) {
        return Err(io::Error::other("taskbar DPI unavailable"));
    }
    let visible = unsafe { IsWindowVisible(shell) } != 0 && taskbar_visible(bar, monitor);
    Ok(TaskbarSnapshot {
        bar,
        notification,
        monitor,
        dpi,
        visible,
    })
}
