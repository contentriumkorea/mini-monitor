//! Thread-affine transparent, independently draggable desktop overlay window.
use crate::overlay_drag::OverlayDrag;
use std::{
    cell::UnsafeCell,
    io,
    marker::PhantomData,
    ptr,
    rc::Rc,
    sync::atomic::{AtomicU64, Ordering},
};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, WPARAM},
    Graphics::Gdi::ClientToScreen,
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Input::KeyboardAndMouse::{GetCapture, ReleaseCapture, SetCapture},
        WindowsAndMessaging::{
            CreateWindowExW, DefWindowProcW, DestroyWindow, GetWindowLongPtrW, LoadCursorW,
            RegisterClassW, SetCursor, SetWindowLongPtrW, SetWindowPos, ShowWindow,
            UnregisterClassW, GWLP_USERDATA, HWND_TOPMOST, IDC_ARROW, SWP_NOACTIVATE, SWP_NOMOVE,
            SWP_NOSIZE, SWP_SHOWWINDOW, SW_HIDE, WM_CANCELMODE, WM_CAPTURECHANGED, WM_LBUTTONDOWN,
            WM_LBUTTONUP, WM_MOUSEACTIVATE, WM_MOUSEMOVE, WM_NCDESTROY, WM_SETCURSOR, WNDCLASSW,
            WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_POPUP,
        },
    },
};

static NEXT: AtomicU64 = AtomicU64::new(1);
unsafe fn screen_point(hwnd: HWND, lparam: LPARAM) -> Option<(i32, i32)> {
    let bits = lparam as u32;
    let mut p = POINT {
        x: (bits as u16 as i16) as i32,
        y: ((bits >> 16) as u16 as i16) as i32,
    };
    (ClientToScreen(hwnd, &mut p) != 0).then_some((p.x, p.y))
}
unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut OverlayDrag;
    match message {
        WM_MOUSEACTIVATE => 3,
        WM_SETCURSOR if (lparam & 0xffff) == 1 => {
            let arrow = LoadCursorW(ptr::null_mut(), IDC_ARROW);
            if !arrow.is_null() {
                SetCursor(arrow);
                1
            } else {
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
        }
        WM_LBUTTONDOWN if !state.is_null() => {
            if let Some((x, y)) = screen_point(hwnd, lparam) {
                SetCapture(hwnd);
                if GetCapture() == hwnd {
                    (*state).begin(x, y);
                }
            }
            0
        }
        WM_MOUSEMOVE if !state.is_null() => {
            let captured = GetCapture() == hwnd;
            if let Some((x, y)) = screen_point(hwnd, lparam) {
                (*state).motion(x, y, wparam & 1 != 0, captured);
            } else {
                (*state).end();
            }
            if captured && wparam & 1 == 0 {
                ReleaseCapture();
            }
            0
        }
        WM_LBUTTONUP | WM_CANCELMODE if !state.is_null() => {
            (*state).end();
            if GetCapture() == hwnd {
                ReleaseCapture();
            }
            0
        }
        WM_CAPTURECHANGED if !state.is_null() => {
            (*state).end();
            0
        }
        WM_NCDESTROY if !state.is_null() => {
            (*state).end();
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}
pub struct OverlayWindow {
    hwnd: HWND,
    instance: HINSTANCE,
    class_name: Vec<u16>,
    drag: Box<UnsafeCell<OverlayDrag>>,
    _thread: PhantomData<Rc<()>>,
}
impl OverlayWindow {
    pub fn new_hidden() -> io::Result<Self> {
        let class_name: Vec<u16> = format!(
            "MiniMonitorOverlay_{}_{}\0",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        )
        .encode_utf16()
        .collect();
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        if instance.is_null() {
            return Err(io::Error::last_os_error());
        }
        let cursor = unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) };
        if cursor.is_null() {
            return Err(io::Error::last_os_error());
        }
        let class = WNDCLASSW {
            style: 0,
            lpfnWndProc: Some(window_proc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: instance,
            hIcon: ptr::null_mut(),
            hCursor: cursor,
            hbrBackground: ptr::null_mut(),
            lpszMenuName: ptr::null(),
            lpszClassName: class_name.as_ptr(),
        };
        if unsafe { RegisterClassW(&class) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                class_name.as_ptr(),
                ptr::null(),
                WS_POPUP,
                0,
                0,
                1,
                1,
                ptr::null_mut(),
                ptr::null_mut(),
                instance,
                ptr::null(),
            )
        };
        if hwnd.is_null() {
            let error = io::Error::last_os_error();
            unsafe {
                UnregisterClassW(class_name.as_ptr(), instance);
            };
            return Err(error);
        }
        let drag = Box::new(UnsafeCell::new(OverlayDrag::default()));
        let state = drag.get() as isize;
        unsafe {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, state);
        }
        if unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } != state {
            let error = io::Error::last_os_error();
            unsafe {
                DestroyWindow(hwnd);
                UnregisterClassW(class_name.as_ptr(), instance);
            };
            return Err(error);
        }
        Ok(Self {
            hwnd,
            instance,
            class_name,
            drag,
            _thread: PhantomData,
        })
    }
    pub fn handle(&self) -> HWND {
        self.hwnd
    }
    pub fn present(
        &self,
        x: i32,
        y: i32,
        width: u32,
        height: u32,
        pixels: &[u8],
    ) -> io::Result<()> {
        crate::layered_window::present_handle(self.hwnd, x, y, width, height, pixels)
    }
    pub fn take_drag_delta(&self) -> (i32, i32) {
        unsafe { (*self.drag.get()).take_delta() }
    }
    pub fn take_drag_finished(&self) -> bool {
        unsafe { (*self.drag.get()).take_finished() }
    }
    pub fn show_topmost(&self) -> io::Result<()> {
        if unsafe {
            SetWindowPos(
                self.hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    pub fn hide(&self) {
        unsafe {
            (*self.drag.get()).end();
            if GetCapture() == self.hwnd {
                ReleaseCapture();
            }
            ShowWindow(self.hwnd, SW_HIDE);
        }
    }
}
impl Drop for OverlayWindow {
    fn drop(&mut self) {
        unsafe {
            (*self.drag.get()).end();
            if GetCapture() == self.hwnd {
                ReleaseCapture();
            }
            DestroyWindow(self.hwnd);
            UnregisterClassW(self.class_name.as_ptr(), self.instance);
        }
    }
}
