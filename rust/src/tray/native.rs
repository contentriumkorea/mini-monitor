//! One thread-affine native notification icon. No app actions run in the window callback.
use super::{menu_action, TrayAction};
use std::{
    cell::UnsafeCell,
    collections::VecDeque,
    io,
    marker::PhantomData,
    os::windows::{ffi::OsStrExt, fs::MetadataExt},
    path::Path,
    ptr,
    rc::Rc,
    sync::atomic::{AtomicU64, Ordering},
};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, WPARAM},
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Shell::{
            Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW,
        },
        WindowsAndMessaging::{
            AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyIcon,
            DestroyMenu, DestroyWindow, GetCursorPos, GetWindowLongPtrW, LoadCursorW, LoadImageW,
            PostMessageW, RegisterClassW, RegisterWindowMessageW, SetForegroundWindow,
            SetWindowLongPtrW, TrackPopupMenu, UnregisterClassW, GWLP_USERDATA, HICON, IDC_ARROW,
            IMAGE_ICON, LR_DEFAULTSIZE, LR_LOADFROMFILE, MF_STRING, TPM_RETURNCMD, TPM_RIGHTBUTTON,
            WM_APP, WM_LBUTTONDBLCLK, WM_NCDESTROY, WM_RBUTTONUP, WNDCLASSW, WS_POPUP,
        },
    },
};
const CALLBACK: u32 = WM_APP + 42;
const OPEN_MENU: u32 = WM_APP + 43;
static NEXT: AtomicU64 = AtomicU64::new(1);
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}
struct State {
    icon: HICON,
    explorer_restart: u32,
    actions: VecDeque<TrayAction>,
}
fn notify(hwnd: HWND, icon: HICON, action: u32) -> bool {
    let mut data: NOTIFYICONDATAW = unsafe { std::mem::zeroed() };
    data.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
    data.hWnd = hwnd;
    data.uID = 1;
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP;
    data.uCallbackMessage = CALLBACK;
    data.hIcon = icon;
    let title = wide("Mini Monitor");
    data.szTip[..title.len()].copy_from_slice(&title);
    unsafe { Shell_NotifyIconW(action, &data) != 0 }
}
unsafe fn popup(hwnd: HWND) -> Option<TrayAction> {
    let menu = CreatePopupMenu();
    if menu.is_null() {
        return None;
    }
    for (id, label) in [
        (1, "설정 열기"),
        (2, "PC 상태창 켜기/끄기"),
        (3, "미니 모니터 다시 연결"),
        (4, "종료"),
    ] {
        let text = wide(label);
        if AppendMenuW(menu, MF_STRING, id, text.as_ptr()) == 0 {
            DestroyMenu(menu);
            return None;
        }
    }
    let mut point: POINT = std::mem::zeroed();
    if GetCursorPos(&mut point) == 0 {
        DestroyMenu(menu);
        return None;
    }
    SetForegroundWindow(hwnd);
    let choice = TrackPopupMenu(
        menu,
        TPM_RETURNCMD | TPM_RIGHTBUTTON,
        point.x,
        point.y,
        0,
        hwnd,
        ptr::null(),
    );
    DestroyMenu(menu);
    menu_action(choice as u32)
}
unsafe extern "system" fn proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State;
    if state.is_null() {
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }
    if message == (*state).explorer_restart {
        let icon = (*state).icon;
        notify(hwnd, icon, NIM_ADD);
        return 0;
    }
    match message {
        CALLBACK if wparam == 1 && lparam as u32 == WM_RBUTTONUP => {
            PostMessageW(hwnd, OPEN_MENU, 0, 0);
            0
        }
        CALLBACK if wparam == 1 && lparam as u32 == WM_LBUTTONDBLCLK => {
            if (*state).actions.len() < 8 {
                (*state).actions.push_back(TrayAction::OpenSettings);
            }
            0
        }
        OPEN_MENU => {
            if let Some(action) = popup(hwnd) {
                if (*state).actions.len() < 8 {
                    (*state).actions.push_back(action);
                }
            }
            0
        }
        WM_NCDESTROY => {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}
pub struct NativeTray {
    hwnd: HWND,
    instance: HINSTANCE,
    class: Vec<u16>,
    state: Box<UnsafeCell<State>>,
    _thread: PhantomData<Rc<()>>,
}
impl NativeTray {
    /// Native owner for message-loop and lifecycle integration.
    pub fn handle(&self) -> HWND {
        self.hwnd
    }
    /// Creates a temporary notification icon; the caller pumps messages on this thread.
    pub fn new(icon_path: &Path) -> io::Result<Self> {
        let metadata = std::fs::symlink_metadata(icon_path)?;
        if !icon_path.is_absolute()
            || !metadata.is_file()
            || metadata.file_type().is_symlink()
            || metadata.file_attributes() & 0x400 != 0
            || metadata.len() > 1_000_000
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid tray icon",
            ));
        }
        let path: Vec<u16> = icon_path
            .as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let icon = unsafe {
            LoadImageW(
                ptr::null_mut(),
                path.as_ptr(),
                IMAGE_ICON,
                0,
                0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
        } as HICON;
        if icon.is_null() {
            return Err(io::Error::last_os_error());
        }
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        if instance.is_null() {
            unsafe {
                DestroyIcon(icon);
            };
            return Err(io::Error::last_os_error());
        }
        let class = wide(&format!(
            "MiniMonitorTray_{}_{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let cursor = unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) };
        let wc = WNDCLASSW {
            style: 0,
            lpfnWndProc: Some(proc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: instance,
            hIcon: icon,
            hCursor: cursor,
            hbrBackground: ptr::null_mut(),
            lpszMenuName: ptr::null(),
            lpszClassName: class.as_ptr(),
        };
        if unsafe { RegisterClassW(&wc) } == 0 {
            let e = io::Error::last_os_error();
            unsafe {
                DestroyIcon(icon);
            };
            return Err(e);
        }
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
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
            let e = io::Error::last_os_error();
            unsafe {
                UnregisterClassW(class.as_ptr(), instance);
                DestroyIcon(icon);
            };
            return Err(e);
        }
        let restart = unsafe { RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()) };
        if restart == 0 {
            let e = io::Error::last_os_error();
            unsafe {
                DestroyWindow(hwnd);
                UnregisterClassW(class.as_ptr(), instance);
                DestroyIcon(icon);
            };
            return Err(e);
        }
        let state = Box::new(UnsafeCell::new(State {
            icon,
            explorer_restart: restart,
            actions: VecDeque::with_capacity(8),
        }));
        let state_ptr = state.get() as isize;
        unsafe {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, state_ptr);
        }
        let tray = Self {
            hwnd,
            instance,
            class,
            state,
            _thread: PhantomData,
        };
        if unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } != state_ptr
            || !notify(hwnd, icon, NIM_ADD)
        {
            return Err(io::Error::other("tray registration failed"));
        }
        Ok(tray)
    }
    pub fn take_action(&self) -> Option<TrayAction> {
        unsafe { (*self.state.get()).actions.pop_front() }
    }
}
impl Drop for NativeTray {
    fn drop(&mut self) {
        unsafe {
            let icon = (*self.state.get()).icon;
            notify(self.hwnd, icon, NIM_DELETE);
            DestroyWindow(self.hwnd);
            UnregisterClassW(self.class.as_ptr(), self.instance);
            DestroyIcon(icon);
        }
    }
}
