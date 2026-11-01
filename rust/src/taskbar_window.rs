//! Thread-affine hidden Win32 taskbar surface foundation. Not a complete taskbar UI.
use std::{
    io,
    marker::PhantomData,
    ptr,
    rc::Rc,
    sync::atomic::{AtomicU64, Ordering},
};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND},
    System::LibraryLoader::GetModuleHandleW,
    UI::WindowsAndMessaging::{
        CreateWindowExW, DefWindowProcW, DestroyWindow, LoadCursorW, RegisterClassW,
        UnregisterClassW, IDC_ARROW, WNDCLASSW, WS_POPUP,
    },
};

static NEXT: AtomicU64 = AtomicU64::new(1);

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
            lpfnWndProc: Some(DefWindowProcW),
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
                0,
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
