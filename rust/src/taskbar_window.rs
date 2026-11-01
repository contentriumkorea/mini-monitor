//! Thread-affine hidden Win32 taskbar surface foundation. Not a complete taskbar UI.
use std::{
    io,
    marker::PhantomData,
    ptr,
    rc::Rc,
    sync::atomic::{AtomicU64, Ordering},
};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, WPARAM},
    System::LibraryLoader::GetModuleHandleW,
    UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, LoadCursorW, RegisterClassW, SetCursor,
        UnregisterClassW, IDC_ARROW, WM_MOUSEACTIVATE, WM_SETCURSOR, WNDCLASSW, WS_EX_LAYERED,
        WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_POPUP,
    },
};

static NEXT: AtomicU64 = AtomicU64::new(1);

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_MOUSEACTIVATE => 3, // MA_NOACTIVATE: preserve click without taking focus
        WM_SETCURSOR if (lparam & 0xffff) == 1 => {
            // HTCLIENT
            let arrow = LoadCursorW(ptr::null_mut(), IDC_ARROW);
            if !arrow.is_null() {
                SetCursor(arrow);
                return 1;
            }
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}

pub struct TaskbarWindow {
    hwnd: HWND,
    instance: HINSTANCE,
    class_name: Vec<u16>,
    _thread_affinity: PhantomData<Rc<()>>,
}

impl TaskbarWindow {
    pub fn new_hidden() -> io::Result<Self> {
        let class_name: Vec<u16> = format!(
            "MiniMonitorRust_{}_{}\0",
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
            }
            return Err(error);
        }
        Ok(Self {
            hwnd,
            instance,
            class_name,
            _thread_affinity: PhantomData,
        })
    }

    pub fn handle(&self) -> HWND {
        self.hwnd
    }
}

impl Drop for TaskbarWindow {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.hwnd);
            UnregisterClassW(self.class_name.as_ptr(), self.instance);
        }
    }
}
