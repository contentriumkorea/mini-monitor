//! Native settings window foundation; application commands are queued for the UI owner.
use crate::dashboard_render::DashboardFrame;
use std::{
    cell::UnsafeCell,
    collections::VecDeque,
    io,
    marker::PhantomData,
    ptr,
    rc::Rc,
    sync::atomic::{AtomicU64, Ordering},
};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, RECT, WPARAM},
    Graphics::Gdi::{
        BeginPaint, CreateFontW, CreateSolidBrush, DeleteObject, DrawFocusRect, DrawTextW,
        EndPaint, FillRect, FrameRect, InvalidateRect, SelectObject, SetBkColor, SetBkMode,
        SetTextColor, StretchDIBits, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS,
        DT_CENTER, DT_SINGLELINE, DT_VCENTER, HBRUSH, HDC, HFONT, PAINTSTRUCT, SRCCOPY,
        TRANSPARENT,
    },
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Controls::{
            InitCommonControlsEx, BST_CHECKED, BST_UNCHECKED, DRAWITEMSTRUCT, ICC_BAR_CLASSES,
            INITCOMMONCONTROLSEX, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED, TBM_SETPOS, TBM_SETRANGE,
            TRACKBAR_CLASSW,
        },
        HiDpi::{
            GetDpiForSystem, GetDpiForWindow, SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT,
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        },
        Input::KeyboardAndMouse::{EnableWindow, IsWindowEnabled},
        WindowsAndMessaging::{
            CreateWindowExW, DefWindowProcW, DestroyWindow, GetClientRect, GetDlgItem,
            GetWindowLongPtrW, GetWindowTextW, IsWindowVisible, LoadCursorW, MoveWindow,
            RegisterClassW, SendMessageW, SetWindowLongPtrW, SetWindowPos, SetWindowTextW,
            ShowWindow, UnregisterClassW, BM_GETCHECK, BM_SETCHECK, BS_AUTOCHECKBOX, BS_OWNERDRAW,
            CBN_SELCHANGE, CBS_DROPDOWNLIST, CB_ADDSTRING, CB_GETCURSEL, CB_SETCURSEL,
            GWLP_USERDATA, IDC_ARROW, MINMAXINFO, SWP_NOACTIVATE, SWP_NOZORDER, SW_HIDE,
            SW_SHOWNORMAL, WM_CLOSE, WM_COMMAND, WM_CTLCOLORBTN, WM_CTLCOLORSTATIC, WM_DPICHANGED,
            WM_DRAWITEM, WM_GETMINMAXINFO, WM_HSCROLL, WM_NCDESTROY, WM_PAINT, WM_SETFONT, WM_SIZE,
            WM_USER, WNDCLASSW, WS_CHILD, WS_OVERLAPPEDWINDOW, WS_TABSTOP, WS_VISIBLE, WS_VSCROLL,
        },
    },
};
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SettingsAction {
    ToggleMonitor,
    ToggleOverlay,
    ToggleTaskbar,
    FindDevice,
    Login,
    CancelLogin,
    RefreshAccount,
    Logout,
    ApiSettings,
    CheckUpdate,
    Quit,
    OverlaySettings,
    TaskbarSettings,
    DeviceOptionsChanged,
    AutostartChanged,
}
fn action(id: u16) -> Option<SettingsAction> {
    Some(match id {
        1 => SettingsAction::ToggleMonitor,
        2 => SettingsAction::ToggleOverlay,
        3 => SettingsAction::ToggleTaskbar,
        4 => SettingsAction::FindDevice,
        5 => SettingsAction::Login,
        6 => SettingsAction::CancelLogin,
        7 => SettingsAction::RefreshAccount,
        8 => SettingsAction::Logout,
        9 => SettingsAction::ApiSettings,
        10 => SettingsAction::CheckUpdate,
        11 => SettingsAction::Quit,
        12 => SettingsAction::OverlaySettings,
        13 => SettingsAction::TaskbarSettings,
        204 => SettingsAction::AutostartChanged,
        _ => return None,
    })
}
fn action_id(value: SettingsAction) -> i32 {
    match value {
        SettingsAction::ToggleMonitor => 1,
        SettingsAction::ToggleOverlay => 2,
        SettingsAction::ToggleTaskbar => 3,
        SettingsAction::FindDevice => 4,
        SettingsAction::Login => 5,
        SettingsAction::CancelLogin => 6,
        SettingsAction::RefreshAccount => 7,
        SettingsAction::Logout => 8,
        SettingsAction::ApiSettings => 9,
        SettingsAction::CheckUpdate => 10,
        SettingsAction::Quit => 11,
        SettingsAction::OverlaySettings => 12,
        SettingsAction::TaskbarSettings => 13,
        SettingsAction::DeviceOptionsChanged => 201,
        SettingsAction::AutostartChanged => 204,
    }
}
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}
static NEXT: AtomicU64 = AtomicU64::new(1);
struct State {
    actions: VecDeque<SettingsAction>,
    brush: HBRUSH,
    font: HFONT,
    dpi: u32,
    tray_available: bool,
    preview: Option<DashboardFrame>,
    icons: Vec<*mut std::ffi::c_void>,
}
struct DpiGuard(DPI_AWARENESS_CONTEXT);
impl Drop for DpiGuard {
    fn drop(&mut self) {
        unsafe {
            SetThreadDpiAwarenessContext(self.0);
        }
    }
}
fn scaled(value: i32, dpi: u32) -> i32 {
    ((i64::from(value) * i64::from(dpi) + 48) / 96) as i32
}
unsafe fn current_dpi(hwnd: HWND) -> u32 {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State;
    if state.is_null() {
        GetDpiForWindow(hwnd).max(96)
    } else {
        (*state).dpi
    }
}
unsafe fn update_font(hwnd: HWND, state: *mut State, dpi: u32) -> io::Result<()> {
    let height = -((9 * dpi as i32 + 36) / 72);
    let font = CreateFontW(
        height,
        0,
        0,
        0,
        600,
        0,
        0,
        0,
        1,
        0,
        0,
        0,
        0,
        wide("맑은 고딕").as_ptr(),
    );
    if font.is_null() {
        return Err(io::Error::other("settings font unavailable"));
    }
    let old = (*state).font;
    (*state).font = font;
    for id in (1..=13).chain(101..=106).chain([109, 201, 202, 204]) {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            SendMessageW(child, WM_SETFONT, font as usize, 1);
        }
    }
    if !old.is_null() {
        DeleteObject(old as _);
    }
    Ok(())
}
unsafe fn enqueue(state: *mut State, value: SettingsAction) {
    if value == SettingsAction::DeviceOptionsChanged && (*state).actions.back() == Some(&value) {
        return;
    }
    if (*state).actions.len() < 32 {
        (*state).actions.push_back(value);
    }
}
unsafe fn update_brightness_label(hwnd: HWND) {
    let slider = GetDlgItem(hwnd, 203);
    let label = GetDlgItem(hwnd, 109);
    if !slider.is_null() && !label.is_null() {
        let value = SendMessageW(slider, WM_USER, 0, 0);
        SetWindowTextW(label, wide(&format!("밝기 {value}%")).as_ptr());
    }
}
unsafe fn layout(hwnd: HWND) {
    let mut rect = std::mem::zeroed();
    if GetClientRect(hwnd, &mut rect) == 0 {
        return;
    }
    let dpi = current_dpi(hwnd);
    let width = ((rect.right - rect.left) * 96 / dpi as i32).max(360);
    let height = ((rect.bottom - rect.top) * 96 / dpi as i32).max(420);
    let gap = 8;
    let margin = 20;
    let row_width = (width - 2 * margin - 2 * gap) / 3;
    for (id, col) in [(1, 0), (2, 1), (3, 2)] {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            MoveWindow(
                child,
                scaled(margin + col * (row_width + gap), dpi),
                scaled(60, dpi),
                scaled(row_width, dpi),
                scaled(38, dpi),
                1,
            );
        }
    }
    let left = (width * 42 / 100).max(260);
    let right_x = margin + left + gap;
    let right_w = width - right_x - margin;
    let half = (left - gap) / 2;
    for (id, x, y, w, h) in [
        (101, margin, 18, width - 2 * margin, 24),
        (102, margin, 125, left, 24),
        (103, margin, 285, half, 24),
        (106, margin + half + gap, 285, half, 24),
        (104, right_x, 125, right_w, 24),
        (105, margin, 405, left, 24),
        (4, margin, 156, left, 36),
        (201, margin, 201, half, 160),
        (202, margin + half + gap, 201, half, 30),
        (203, margin + 100, 240, left - 100, 30),
        (109, margin, 245, 95, 24),
        (5, margin, 315, half, 34),
        (6, margin + half + gap, 315, half, 34),
        (7, margin, 355, half, 34),
        (8, margin + half + gap, 355, half, 34),
        (9, margin, 435, left, 34),
        (10, margin, 477, left, 34),
        (11, margin, height - 48, left, 34),
        (204, margin, height - 88, left, 28),
        (12, right_x, height - 90, right_w, 34),
        (13, right_x, height - 48, right_w, 34),
    ] {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            MoveWindow(
                child,
                scaled(x, dpi),
                scaled(y, dpi),
                scaled(w, dpi),
                scaled(h, dpi),
                1,
            );
        }
    }
}

unsafe fn draw_preview(hwnd: HWND, state: *mut State) {
    let mut paint: PAINTSTRUCT = std::mem::zeroed();
    let dc = BeginPaint(hwnd, &mut paint);
    if dc.is_null() {
        return;
    }
    let mut client: RECT = std::mem::zeroed();
    GetClientRect(hwnd, &mut client);
    let dpi = current_dpi(hwnd);
    let width = (client.right * 96 / dpi as i32).max(360);
    let height = (client.bottom * 96 / dpi as i32).max(420);
    let left = (width * 42 / 100).max(260);
    let x = 20 + left + 8;
    let available_w = (width - x - 20).max(1);
    let available_h = (height - 156 - 108).max(1);
    let rect = RECT {
        left: scaled(x, dpi),
        top: scaled(156, dpi),
        right: scaled(x + available_w, dpi),
        bottom: scaled(156 + available_h, dpi),
    };
    let background = CreateSolidBrush(0x101010);
    if !background.is_null() {
        FillRect(dc, &rect, background);
        DeleteObject(background);
    }
    if !state.is_null() {
        if let Some(frame) = (*state).preview.as_ref() {
            let mut info: BITMAPINFO = std::mem::zeroed();
            info.bmiHeader = BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: frame.width as i32,
                biHeight: -(frame.height as i32),
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB,
                biSizeImage: 0,
                biXPelsPerMeter: 0,
                biYPelsPerMeter: 0,
                biClrUsed: 0,
                biClrImportant: 0,
            };
            let scale = (scaled(available_w, dpi) as f64 / frame.width as f64)
                .min(scaled(available_h, dpi) as f64 / frame.height as f64);
            let out_w = (frame.width as f64 * scale).round().max(1.0) as i32;
            let out_h = (frame.height as f64 * scale).round().max(1.0) as i32;
            StretchDIBits(
                dc,
                scaled(x, dpi) + (scaled(available_w, dpi) - out_w) / 2,
                scaled(156, dpi) + (scaled(available_h, dpi) - out_h) / 2,
                out_w,
                out_h,
                0,
                0,
                frame.width as i32,
                frame.height as i32,
                frame.pixels.as_ptr().cast(),
                &info,
                DIB_RGB_COLORS,
                SRCCOPY,
            );
        }
    }
    EndPaint(hwnd, &paint);
}
unsafe extern "system" fn proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State;
    match message {
        WM_COMMAND
            if !state.is_null()
                && ((wparam as u16 == 201
                    && ((wparam >> 16) & 0xffff) == CBN_SELCHANGE as usize)
                    || (wparam as u16 == 202 && ((wparam >> 16) & 0xffff) == 0)) =>
        {
            enqueue(state, SettingsAction::DeviceOptionsChanged);
            0
        }
        WM_COMMAND if !state.is_null() && ((wparam >> 16) & 0xffff) == 0 => {
            if let Some(value) = action(wparam as u16) {
                enqueue(state, value);
            }
            0
        }
        WM_HSCROLL if !state.is_null() && lparam as HWND == GetDlgItem(hwnd, 203) => {
            update_brightness_label(hwnd);
            enqueue(state, SettingsAction::DeviceOptionsChanged);
            0
        }
        WM_SIZE => {
            layout(hwnd);
            InvalidateRect(hwnd, ptr::null(), 0);
            0
        }
        WM_DPICHANGED if !state.is_null() => {
            let dpi = (wparam as u32 & 0xffff).clamp(48, 768);
            if update_font(hwnd, state, dpi).is_ok() {
                (*state).dpi = dpi;
            }
            if lparam != 0 {
                let rect = &*(lparam as *const RECT);
                SetWindowPos(
                    hwnd,
                    ptr::null_mut(),
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
            layout(hwnd);
            InvalidateRect(hwnd, ptr::null(), 1);
            0
        }
        WM_PAINT => {
            draw_preview(hwnd, state);
            0
        }
        WM_GETMINMAXINFO if lparam != 0 => {
            let info = &mut *(lparam as *mut MINMAXINFO);
            let dpi = current_dpi(hwnd);
            info.ptMinTrackSize.x = scaled(700, dpi);
            info.ptMinTrackSize.y = scaled(620, dpi);
            0
        }
        WM_CLOSE if !state.is_null() => {
            if (*state).tray_available {
                ShowWindow(hwnd, SW_HIDE);
            } else {
                if (*state).actions.len() >= 32 {
                    (*state).actions.pop_front();
                }
                (*state).actions.push_back(SettingsAction::Quit);
            }
            0
        }
        WM_CTLCOLORBTN | WM_CTLCOLORSTATIC if !state.is_null() => {
            let dc = wparam as HDC;
            SetTextColor(dc, 0xeeeeee);
            SetBkColor(dc, 0x191919);
            (*state).brush as LRESULT
        }
        WM_DRAWITEM if !state.is_null() && lparam != 0 => {
            let item = &*(lparam as *const DRAWITEMSTRUCT);
            let fill = CreateSolidBrush(if item.itemState & ODS_SELECTED != 0 {
                0x454545
            } else {
                0x292929
            });
            let border = CreateSolidBrush(if item.itemState & ODS_FOCUS != 0 {
                0xaaaaaa
            } else {
                0x555555
            });
            if !fill.is_null() && !border.is_null() {
                FillRect(item.hDC, &item.rcItem, fill);
                FrameRect(item.hDC, &item.rcItem, border);
                let mut text = [0u16; 128];
                let count = GetWindowTextW(item.hwndItem, text.as_mut_ptr(), text.len() as i32);
                let mut rect = item.rcItem;
                SetBkMode(item.hDC, TRANSPARENT as i32);
                SetTextColor(
                    item.hDC,
                    if item.itemState & ODS_DISABLED != 0 {
                        0x888888
                    } else {
                        0xeeeeee
                    },
                );
                let previous_font = SelectObject(item.hDC, (*state).font as _);
                DrawTextW(
                    item.hDC,
                    text.as_ptr(),
                    count,
                    &mut rect,
                    DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                );
                if !previous_font.is_null() {
                    SelectObject(item.hDC, previous_font);
                }
                if item.itemState & ODS_FOCUS != 0 {
                    DrawFocusRect(item.hDC, &rect);
                }
            }
            if !fill.is_null() {
                DeleteObject(fill);
            }
            if !border.is_null() {
                DeleteObject(border);
            }
            1
        }
        WM_NCDESTROY if !state.is_null() => {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}
pub struct SettingsWindow {
    hwnd: HWND,
    instance: HINSTANCE,
    class: Vec<u16>,
    state: Box<UnsafeCell<State>>,
    _thread: PhantomData<Rc<()>>,
}
impl SettingsWindow {
    pub fn new_hidden() -> io::Result<Self> {
        let previous =
            unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
        if previous.is_null() {
            return Err(io::Error::other("per-monitor DPI unavailable"));
        }
        let _dpi_guard = DpiGuard(previous);
        let initial_dpi = unsafe { GetDpiForSystem() }.max(96);
        let classes = INITCOMMONCONTROLSEX {
            dwSize: std::mem::size_of::<INITCOMMONCONTROLSEX>() as u32,
            dwICC: ICC_BAR_CLASSES,
        };
        if unsafe { InitCommonControlsEx(&classes) } == 0 {
            return Err(io::Error::other("Windows controls unavailable"));
        }
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        if instance.is_null() {
            return Err(io::Error::last_os_error());
        }
        let class = wide(&format!(
            "MiniMonitorSettings_{}_{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let brush = unsafe { CreateSolidBrush(0x191919) };
        if brush.is_null() {
            return Err(io::Error::last_os_error());
        }
        let wc = WNDCLASSW {
            style: 0,
            lpfnWndProc: Some(proc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: instance,
            hIcon: ptr::null_mut(),
            hCursor: unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) },
            hbrBackground: brush,
            lpszMenuName: ptr::null(),
            lpszClassName: class.as_ptr(),
        };
        if unsafe { RegisterClassW(&wc) } == 0 {
            let e = io::Error::last_os_error();
            unsafe {
                DeleteObject(brush);
            };
            return Err(e);
        }
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
                wide("Mini Monitor 설정").as_ptr(),
                WS_OVERLAPPEDWINDOW,
                scaled(100, initial_dpi),
                scaled(100, initial_dpi),
                scaled(920, initial_dpi),
                scaled(680, initial_dpi),
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
                DeleteObject(brush);
            };
            return Err(e);
        }
        let state = Box::new(UnsafeCell::new(State {
            actions: VecDeque::with_capacity(32),
            brush,
            font: ptr::null_mut(),
            dpi: unsafe { GetDpiForWindow(hwnd) }.max(96),
            tray_available: false,
            preview: None,
            icons: Vec::new(),
        }));
        let state_ptr = state.get() as isize;
        unsafe {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, state_ptr);
        }
        let window = Self {
            hwnd,
            instance,
            class,
            state,
            _thread: PhantomData,
        };
        if unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } != state_ptr {
            return Err(io::Error::other("settings state unavailable"));
        }
        let controls = [
            (1, "미니 모니터 켜기/끄기"),
            (2, "PC 오버레이 켜기/끄기"),
            (3, "작업표시줄 바 켜기/끄기"),
            (4, "장치 찾기"),
            (5, "ChatGPT로 로그인"),
            (6, "로그인 취소"),
            (7, "계정 새로고침"),
            (8, "로그아웃"),
            (9, "OpenAI API 사용량 설정"),
            (10, "업데이트 확인"),
            (11, "프로그램 종료"),
            (12, "PC 상태창 설정"),
            (13, "작업표시줄 바 설정"),
        ];
        for (id, label) in controls {
            let child = unsafe {
                CreateWindowExW(
                    0,
                    wide("BUTTON").as_ptr(),
                    wide(label).as_ptr(),
                    WS_CHILD | WS_VISIBLE | BS_OWNERDRAW as u32,
                    0,
                    0,
                    1,
                    1,
                    hwnd,
                    id as isize as _,
                    instance,
                    ptr::null(),
                )
            };
            if child.is_null() {
                return Err(io::Error::last_os_error());
            }
        }
        for (id, label) in [
            (101, "Mini Monitor · 화면 표시"),
            (102, "미니 모니터 연결"),
            (103, "Codex 계정"),
            (104, "미니 모니터 화면"),
            (105, "OpenAI API · 업데이트"),
            (106, "확인 중"),
            (109, "밝기 25%"),
        ] {
            let child = unsafe {
                CreateWindowExW(
                    0,
                    wide("STATIC").as_ptr(),
                    wide(label).as_ptr(),
                    WS_CHILD | WS_VISIBLE,
                    0,
                    0,
                    1,
                    1,
                    hwnd,
                    id as isize as _,
                    instance,
                    ptr::null(),
                )
            };
            if child.is_null() {
                return Err(io::Error::last_os_error());
            }
        }
        let combo = unsafe {
            CreateWindowExW(
                0,
                wide("COMBOBOX").as_ptr(),
                ptr::null(),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST as u32,
                0,
                0,
                1,
                1,
                hwnd,
                201isize as _,
                instance,
                ptr::null(),
            )
        };
        let flip = unsafe {
            CreateWindowExW(
                0,
                wide("BUTTON").as_ptr(),
                wide("상하 반전 (180°)").as_ptr(),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX as u32,
                0,
                0,
                1,
                1,
                hwnd,
                202isize as _,
                instance,
                ptr::null(),
            )
        };
        let slider = unsafe {
            CreateWindowExW(
                0,
                TRACKBAR_CLASSW,
                ptr::null(),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                0,
                0,
                1,
                1,
                hwnd,
                203isize as _,
                instance,
                ptr::null(),
            )
        };
        let autostart = unsafe {
            CreateWindowExW(
                0,
                wide("BUTTON").as_ptr(),
                wide("Windows 시작 시 자동 실행").as_ptr(),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_AUTOCHECKBOX as u32,
                0,
                0,
                1,
                1,
                hwnd,
                204isize as _,
                instance,
                ptr::null(),
            )
        };
        if autostart.is_null() {
            return Err(io::Error::other("startup control unavailable"));
        }
        if combo.is_null() || flip.is_null() || slider.is_null() {
            return Err(io::Error::other("device controls unavailable"));
        }
        unsafe {
            update_font(hwnd, window.state.get(), (*window.state.get()).dpi)?;
        }
        unsafe {
            for label in ["가로", "세로"] {
                SendMessageW(combo, CB_ADDSTRING, 0, wide(label).as_ptr() as isize);
            }
            SendMessageW(slider, TBM_SETRANGE, 1, (1 | (50 << 16)) as isize);
        }
        window.set_device_options(crate::device::Orientation::Landscape, 25)?;
        unsafe {
            layout(hwnd);
        }
        Ok(window)
    }
    pub fn handle(&self) -> HWND {
        self.hwnd
    }
    pub fn set_app_icon(&self, path: &std::path::Path) -> io::Result<()> {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::UI::WindowsAndMessaging::{
            LoadImageW, IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON,
        };
        if !path.is_absolute() {
            return Err(io::Error::other("invalid icon path"));
        }
        let target: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        for (which, size) in [(0usize, 16), (1usize, 32)] {
            let icon = unsafe {
                LoadImageW(
                    ptr::null_mut(),
                    target.as_ptr(),
                    IMAGE_ICON,
                    size,
                    size,
                    LR_LOADFROMFILE,
                )
            };
            if icon.is_null() {
                return Err(io::Error::other("app icon unavailable"));
            }
            unsafe {
                SendMessageW(self.hwnd, WM_SETICON, which, icon as isize);
                (*self.state.get()).icons.push(icon);
            }
        }
        Ok(())
    }
    pub fn set_autostart(&self, enabled: bool) -> io::Result<()> {
        let child = unsafe { GetDlgItem(self.hwnd, 204) };
        if child.is_null() {
            return Err(io::Error::other("startup control unavailable"));
        }
        unsafe {
            SendMessageW(child, BM_SETCHECK, usize::from(enabled), 0);
        }
        Ok(())
    }
    pub fn autostart_checked(&self) -> io::Result<bool> {
        let child = unsafe { GetDlgItem(self.hwnd, 204) };
        if child.is_null() {
            return Err(io::Error::other("startup control unavailable"));
        }
        Ok(unsafe { SendMessageW(child, BM_GETCHECK, 0, 0) } == 1)
    }
    pub fn is_visible(&self) -> bool {
        unsafe { IsWindowVisible(self.hwnd) != 0 }
    }
    pub fn take_action(&self) -> Option<SettingsAction> {
        unsafe { (*self.state.get()).actions.pop_front() }
    }
    pub fn set_action_enabled(&self, action: SettingsAction, enabled: bool) -> io::Result<()> {
        if action == SettingsAction::DeviceOptionsChanged {
            for id in [201, 202, 203] {
                let child = unsafe { GetDlgItem(self.hwnd, id) };
                if child.is_null() {
                    return Err(io::Error::other("settings control unavailable"));
                }
                unsafe {
                    EnableWindow(child, i32::from(enabled));
                }
            }
            return Ok(());
        }
        let child = unsafe { GetDlgItem(self.hwnd, action_id(action)) };
        if child.is_null() {
            return Err(io::Error::other("settings control unavailable"));
        }
        unsafe {
            EnableWindow(child, i32::from(enabled));
        }
        if (unsafe { IsWindowEnabled(child) } != 0) != enabled {
            return Err(io::Error::other("settings state unavailable"));
        }
        Ok(())
    }
    pub fn set_device_options(
        &self,
        orientation: crate::device::Orientation,
        brightness: u8,
    ) -> io::Result<()> {
        use crate::device::Orientation;
        if !(1..=50).contains(&brightness) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid brightness",
            ));
        }
        let (portrait, flipped) = match orientation {
            Orientation::Landscape => (false, false),
            Orientation::ReverseLandscape => (false, true),
            Orientation::Portrait => (true, false),
            Orientation::ReversePortrait => (true, true),
        };
        let combo = unsafe { GetDlgItem(self.hwnd, 201) };
        let flip = unsafe { GetDlgItem(self.hwnd, 202) };
        let slider = unsafe { GetDlgItem(self.hwnd, 203) };
        if combo.is_null() || flip.is_null() || slider.is_null() {
            return Err(io::Error::other("device controls unavailable"));
        }
        if unsafe { SendMessageW(combo, CB_SETCURSEL, usize::from(portrait), 0) } < 0 {
            return Err(io::Error::other("invalid display orientation"));
        }
        unsafe {
            SendMessageW(
                flip,
                BM_SETCHECK,
                if flipped {
                    BST_CHECKED as usize
                } else {
                    BST_UNCHECKED as usize
                },
                0,
            );
            SendMessageW(slider, TBM_SETPOS, 1, brightness as isize);
            update_brightness_label(self.hwnd);
        }
        Ok(())
    }
    pub fn device_options(&self) -> io::Result<(crate::device::Orientation, u8)> {
        use crate::device::Orientation;
        let combo = unsafe { GetDlgItem(self.hwnd, 201) };
        let flip = unsafe { GetDlgItem(self.hwnd, 202) };
        let slider = unsafe { GetDlgItem(self.hwnd, 203) };
        if combo.is_null() || flip.is_null() || slider.is_null() {
            return Err(io::Error::other("device controls unavailable"));
        }
        let view = unsafe { SendMessageW(combo, CB_GETCURSEL, 0, 0) };
        let checked = unsafe { SendMessageW(flip, BM_GETCHECK, 0, 0) };
        let brightness = unsafe { SendMessageW(slider, WM_USER, 0, 0) };
        if !(1..=50).contains(&brightness)
            || !(0..=1).contains(&view)
            || !(0..=1).contains(&checked)
        {
            return Err(io::Error::other("invalid device controls"));
        }
        let orientation = match (view, checked) {
            (0, 0) => Orientation::Landscape,
            (0, 1) => Orientation::ReverseLandscape,
            (1, 0) => Orientation::Portrait,
            _ => Orientation::ReversePortrait,
        };
        Ok((orientation, brightness as u8))
    }
    pub fn set_output_state(&self, monitor: bool, overlay: bool, taskbar: bool) -> io::Result<()> {
        for (id, name, enabled) in [
            (1, "미니 모니터", monitor),
            (2, "PC 오버레이", overlay),
            (3, "작업표시줄 바", taskbar),
        ] {
            let child = unsafe { GetDlgItem(self.hwnd, id) };
            if child.is_null() {
                return Err(io::Error::other("settings control unavailable"));
            }
            let label = wide(&format!(
                "{name} · {}",
                if enabled { "켜짐" } else { "꺼짐" }
            ));
            if unsafe { SetWindowTextW(child, label.as_ptr()) } == 0 {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(())
    }
    pub fn set_account_status(
        &self,
        status: crate::account_worker::AccountStatus,
    ) -> io::Result<()> {
        use crate::account_worker::AccountStatus;
        let message = match status {
            AccountStatus::Pending => "확인 중",
            AccountStatus::SignedOut => "로그인 필요",
            AccountStatus::Ready => "연결됨",
            AccountStatus::LoginPending => "로그인 진행 중",
            AccountStatus::Error => "확인 실패",
        };
        let child = unsafe { GetDlgItem(self.hwnd, 106) };
        if child.is_null() {
            return Err(io::Error::other("account status unavailable"));
        }
        if unsafe { SetWindowTextW(child, wide(message).as_ptr()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    /// Only displays availability; the update-install control stays disabled until handoff exists.
    pub fn set_release_status(
        &self,
        snapshot: &crate::release_worker::ReleaseSnapshot,
    ) -> io::Result<()> {
        use crate::release_worker::ReleaseStatus;
        let text = match snapshot.status {
            ReleaseStatus::Checking => "업데이트 확인 중".to_owned(),
            ReleaseStatus::Current => "최신 버전".to_owned(),
            ReleaseStatus::Available => match snapshot.version.as_deref() {
                Some(v) => format!("업데이트 가능 · {v}"),
                None => "업데이트 정보 오류".to_owned(),
            },
            ReleaseStatus::Error => "업데이트 확인 실패".to_owned(),
            ReleaseStatus::Stopped => "업데이트 확인 중지".to_owned(),
        };
        let child = unsafe { GetDlgItem(self.hwnd, 10) };
        if child.is_null() {
            return Err(io::Error::other("update status unavailable"));
        }
        if unsafe { SetWindowTextW(child, wide(&text).as_ptr()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    pub fn set_device_detection(&self, count: usize) -> io::Result<()> {
        let message = match count {
            0 => "지원 장치를 찾지 못했습니다.",
            1 => "지원 장치 준비됨 · 미니 모니터를 켜세요.",
            _ => "여러 장치 발견 · 연결할 장치를 확인하세요.",
        };
        if unsafe { SetWindowTextW(GetDlgItem(self.hwnd, 102), wide(message).as_ptr()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    pub fn set_device_status(
        &self,
        enabled: bool,
        status: Option<crate::device_worker::WorkerStatus>,
    ) -> io::Result<()> {
        use crate::device_worker::WorkerStatus;
        let message = if !enabled {
            "중지됨"
        } else {
            match status {
                None => "연결 대기",
                Some(WorkerStatus::Starting) => "연결 중",
                Some(WorkerStatus::Ready) => "연결됨",
                Some(WorkerStatus::Error | WorkerStatus::Stopped) => "연결 실패",
            }
        };
        let child = unsafe { GetDlgItem(self.hwnd, 102) };
        if child.is_null() {
            return Err(io::Error::other("device status unavailable"));
        }
        if unsafe {
            SetWindowTextW(
                child,
                wide(&format!("미니 모니터 연결 · {message}")).as_ptr(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
    pub fn preview_size(&self) -> Option<(u32, u32)> {
        unsafe {
            (*self.state.get())
                .preview
                .as_ref()
                .map(|f| (f.width, f.height))
        }
    }
    pub fn set_preview(&self, frame: DashboardFrame) -> io::Result<()> {
        if !matches!((frame.width, frame.height), (480, 320) | (320, 480))
            || frame.pixels.len() != (frame.width as usize) * (frame.height as usize) * 4
            || frame.pixels.as_chunks::<4>().0.iter().any(|p| p[3] != 255)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid preview frame",
            ));
        }
        unsafe {
            (*self.state.get()).preview = Some(frame);
            InvalidateRect(self.hwnd, ptr::null(), 0);
        }
        Ok(())
    }
    pub fn set_tray_available(&self, available: bool) {
        unsafe {
            (*self.state.get()).tray_available = available;
        }
    }
    pub fn show(&self) {
        unsafe {
            ShowWindow(self.hwnd, SW_SHOWNORMAL);
        }
    }
    pub fn hide(&self) {
        unsafe {
            ShowWindow(self.hwnd, SW_HIDE);
        }
    }
}
impl Drop for SettingsWindow {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.hwnd);
            UnregisterClassW(self.class.as_ptr(), self.instance);
            DeleteObject((*self.state.get()).brush);
            for icon in &(*self.state.get()).icons {
                windows_sys::Win32::UI::WindowsAndMessaging::DestroyIcon(*icon as _);
            }
            if !(*self.state.get()).font.is_null() {
                DeleteObject((*self.state.get()).font as _);
            }
        }
    }
}
