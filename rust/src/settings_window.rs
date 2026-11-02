//! Native settings window foundation; application commands are queued for the UI owner.
use crate::dashboard_render::DashboardFrame;
use crate::settings_theme::{self as theme, MainLayout};
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
        BeginPaint, CreateFontW, CreateSolidBrush, DeleteObject, EndPaint, FillRect,
        InvalidateRect, SetBkColor, SetTextColor, StretchDIBits, BITMAPINFO, BITMAPINFOHEADER,
        BI_RGB, DIB_RGB_COLORS, DT_SINGLELINE, DT_VCENTER, HBRUSH, HDC, HFONT, PAINTSTRUCT,
        SRCCOPY,
    },
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Controls::{
            InitCommonControlsEx, BST_CHECKED, BST_UNCHECKED, DRAWITEMSTRUCT, ICC_BAR_CLASSES,
            INITCOMMONCONTROLSEX, TBM_SETPOS, TBM_SETRANGE, TRACKBAR_CLASSW,
        },
        HiDpi::{
            GetDpiForSystem, GetDpiForWindow, SetThreadDpiAwarenessContext, DPI_AWARENESS_CONTEXT,
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        },
        Input::KeyboardAndMouse::{EnableWindow, IsWindowEnabled},
        WindowsAndMessaging::{
            CreateWindowExW, DefWindowProcW, DestroyWindow, GetClientRect, GetDlgItem,
            GetWindowLongPtrW, IsWindowVisible, LoadCursorW, MoveWindow, RegisterClassW,
            SendMessageW, SetWindowLongPtrW, SetWindowPos, SetWindowTextW, ShowWindow,
            UnregisterClassW, BM_GETCHECK, BM_SETCHECK, BS_AUTOCHECKBOX, BS_OWNERDRAW,
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
    title_font: HFONT,
    outputs: [bool; 3],
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
    let title = CreateFontW(
        -((18 * dpi as i32 + 36) / 72),
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
    if !title.is_null() {
        let previous = std::mem::replace(&mut (*state).title_font, title);
        if !previous.is_null() {
            DeleteObject(previous as _);
        }
    }
    for id in (1..=13).chain(101..=106).chain([109, 110, 201, 202, 204]) {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            SendMessageW(child, WM_SETFONT, font as usize, 1);
        }
    }
    if !(*state).title_font.is_null() {
        SendMessageW(
            GetDlgItem(hwnd, 101),
            WM_SETFONT,
            (*state).title_font as usize,
            1,
        );
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

unsafe fn set_label_if_changed(child: HWND, value: &str) -> io::Result<bool> {
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetWindowTextLengthW, GetWindowTextW};
    if child.is_null() {
        return Err(io::Error::other("settings control unavailable"));
    }
    let expected = wide(value);
    let length = GetWindowTextLengthW(child).max(0) as usize;
    if length == expected.len() - 1 {
        let mut previous = vec![0; length + 1];
        if GetWindowTextW(child, previous.as_mut_ptr(), previous.len() as i32) as usize == length
            && previous == expected
        {
            return Ok(false);
        }
    }
    if SetWindowTextW(child, expected.as_ptr()) == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(true)
}

fn preview_rect(groups: &MainLayout, dpi: u32) -> RECT {
    theme::physical(
        RECT {
            left: groups.preview.left + 18,
            top: groups.preview.top + 52,
            right: (groups.preview.right - 18).max(groups.preview.left + 19),
            bottom: (groups.preview.bottom - 62).max(groups.preview.top + 53),
        },
        dpi,
    )
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
    let compact = height < 570;
    let gap = 12;
    let margin = 20;
    let row_width = (width - 2 * margin - 2 * gap) / 3;
    for (id, col) in [(1, 0), (2, 1), (3, 2)] {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            MoveWindow(
                child,
                scaled(margin + col * (row_width + gap), dpi),
                scaled(if compact { 56 } else { 76 }, dpi),
                scaled(row_width, dpi),
                scaled(if compact { 46 } else { 70 }, dpi),
                1,
            );
        }
    }
    let groups = MainLayout::new(width, height);
    let left = groups.device.right - groups.device.left;
    let right_x = groups.preview.left;
    let right_w = groups.preview.right - right_x;
    let half = (left - 44) / 2;
    let device_y = groups.device.top;
    let account_y = groups.account.top;
    let footer = groups.footer_top;
    for (id, x, y, w, h) in [
        (101, margin, if compact { 2 } else { 13 }, width - 40, 34),
        (
            110,
            margin + 1,
            if compact { 34 } else { 48 },
            width - 40,
            18,
        ),
        (
            102,
            margin + 18,
            device_y + if compact { 26 } else { 44 },
            left - 36,
            22,
        ),
        (
            103,
            margin + 18,
            account_y + if compact { 5 } else { 14 },
            half + 20,
            22,
        ),
        (
            106,
            margin + left - 96,
            account_y + if compact { 5 } else { 15 },
            78,
            22,
        ),
        (104, right_x + 18, groups.preview.top + 16, right_w - 36, 22),
        (
            105,
            margin + 18,
            (groups.api.top + groups.api.bottom - 22) / 2,
            104,
            22,
        ),
        (
            4,
            margin + left - 104,
            device_y + if compact { 4 } else { 12 },
            86,
            26,
        ),
        (
            201,
            margin + 18,
            device_y + if compact { 48 } else { 76 },
            half,
            160,
        ),
        (
            202,
            margin + 26 + half,
            device_y + if compact { 48 } else { 76 },
            half,
            26,
        ),
        (
            203,
            margin + 110,
            device_y + if compact { 80 } else { 111 },
            left - 128,
            24,
        ),
        (
            109,
            margin + 18,
            device_y + if compact { 79 } else { 114 },
            88,
            22,
        ),
        (
            5,
            margin + 18,
            account_y + if compact { 30 } else { 49 },
            half,
            26,
        ),
        (
            6,
            margin + 26 + half,
            account_y + if compact { 30 } else { 49 },
            half,
            26,
        ),
        (
            7,
            margin + 18,
            account_y + if compact { 62 } else { 88 },
            half,
            26,
        ),
        (
            8,
            margin + 26 + half,
            account_y + if compact { 62 } else { 88 },
            half,
            26,
        ),
        (
            9,
            margin + 128,
            (groups.api.top + groups.api.bottom - 28) / 2,
            left - 146,
            28,
        ),
        (10, width - 282, footer, 150, 28),
        (11, width - 120, footer, 100, 28),
        (204, margin, footer, 270, 28),
        (
            12,
            right_x + 18,
            groups.preview.bottom - 46,
            (right_w - 44) / 2,
            30,
        ),
        (
            13,
            right_x + 26 + (right_w - 44) / 2,
            groups.preview.bottom - 46,
            (right_w - 44) / 2,
            30,
        ),
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
    paint_scene(hwnd, dc, state);
    EndPaint(hwnd, &paint);
}

unsafe fn paint_scene(hwnd: HWND, dc: HDC, state: *mut State) {
    use windows_sys::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, GetClipBox,
        IntersectClipRect, SelectObject, NULLREGION,
    };
    let mut client: RECT = std::mem::zeroed();
    GetClientRect(hwnd, &mut client);
    if client.right <= 0 || client.bottom <= 0 {
        return;
    }
    let mut dirty: RECT = std::mem::zeroed();
    if GetClipBox(dc, &mut dirty) <= NULLREGION {
        return;
    }
    dirty.left = dirty.left.max(0);
    dirty.top = dirty.top.max(0);
    dirty.right = dirty.right.min(client.right);
    dirty.bottom = dirty.bottom.min(client.bottom);
    if dirty.right <= dirty.left || dirty.bottom <= dirty.top {
        return;
    }
    let memory = CreateCompatibleDC(dc);
    if memory.is_null() {
        return;
    }
    let bitmap = CreateCompatibleBitmap(dc, client.right, client.bottom);
    if bitmap.is_null() {
        DeleteDC(memory);
        return;
    }
    let old = SelectObject(memory, bitmap as _);
    IntersectClipRect(memory, dirty.left, dirty.top, dirty.right, dirty.bottom);
    paint_scene_content(hwnd, memory, state);
    // Never expose the gradient, panel fill or empty preview between live frames.
    // The destination retains its child-window/update clipping; only a finished
    // bitmap is copied to it. The temporary GDI buffer is released every paint.
    BitBlt(
        dc,
        dirty.left,
        dirty.top,
        dirty.right - dirty.left,
        dirty.bottom - dirty.top,
        memory,
        dirty.left,
        dirty.top,
        SRCCOPY,
    );
    SelectObject(memory, old);
    DeleteObject(bitmap as _);
    DeleteDC(memory);
}

unsafe fn paint_scene_content(hwnd: HWND, dc: HDC, state: *mut State) {
    let mut client: RECT = std::mem::zeroed();
    GetClientRect(hwnd, &mut client);
    let dpi = current_dpi(hwnd);
    let width = (client.right * 96 / dpi as i32).max(360);
    let height = (client.bottom * 96 / dpi as i32).max(420);
    let groups = MainLayout::new(width, height);
    let full = RECT {
        left: 0,
        top: 0,
        right: client.right,
        bottom: client.bottom,
    };
    theme::gradient(dc, full, 26, 17);
    for panel in [groups.device, groups.account, groups.api, groups.preview] {
        theme::paint_panel(dc, theme::physical(panel, dpi), scaled(14, dpi));
    }
    theme::text(
        dc,
        (*state).font,
        theme::physical(
            RECT {
                left: 38,
                top: groups.device.top + if height < 570 { 5 } else { 14 },
                right: groups.device.right - 112,
                bottom: groups.device.top + if height < 570 { 26 } else { 36 },
            },
            dpi,
        ),
        "디스플레이 연결",
        theme::TEXT,
        windows_sys::Win32::Graphics::Gdi::DT_LEFT | DT_VCENTER | DT_SINGLELINE,
    );
    let x = groups.preview.left + 18;
    let y = groups.preview.top + 52;
    let available_w = (groups.preview.right - x - 18).max(1);
    let available_h = (groups.preview.bottom - y - 62).max(1);
    let rect = RECT {
        left: scaled(x, dpi),
        top: scaled(y, dpi),
        right: scaled(x + available_w, dpi),
        bottom: scaled(y + available_h, dpi),
    };
    let background = CreateSolidBrush(0x161616);
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
            // The physical 480x320 frame is commonly reduced in this preview.
            // Default COLORONCOLOR drops pixel rows/columns and breaks small glyphs.
            let saved = windows_sys::Win32::Graphics::Gdi::SaveDC(dc);
            windows_sys::Win32::Graphics::Gdi::SetStretchBltMode(
                dc,
                windows_sys::Win32::Graphics::Gdi::HALFTONE,
            );
            windows_sys::Win32::Graphics::Gdi::SetBrushOrgEx(dc, 0, 0, ptr::null_mut());
            StretchDIBits(
                dc,
                scaled(x, dpi) + (scaled(available_w, dpi) - out_w) / 2,
                scaled(y, dpi) + (scaled(available_h, dpi) - out_h) / 2,
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
            if saved != 0 {
                windows_sys::Win32::Graphics::Gdi::RestoreDC(dc, saved);
            }
        }
    }
}
unsafe extern "system" fn proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State;
    match message {
        windows_sys::Win32::UI::WindowsAndMessaging::WM_ERASEBKGND if !state.is_null() => 1,
        windows_sys::Win32::UI::WindowsAndMessaging::WM_NOTIFY => theme::slider_notify(lparam)
            .unwrap_or_else(|| DefWindowProcW(hwnd, message, wparam, lparam)),
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
            info.ptMinTrackSize.y = scaled(460, dpi);
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
            SetTextColor(
                dc,
                if theme::high_contrast() {
                    windows_sys::Win32::Graphics::Gdi::GetSysColor(
                        windows_sys::Win32::Graphics::Gdi::COLOR_WINDOWTEXT,
                    )
                } else {
                    theme::TEXT
                },
            );
            SetBkColor(dc, theme::PANEL);
            let mut rect: RECT = std::mem::zeroed();
            GetClientRect(hwnd, &mut rect);
            let dpi = (*state).dpi;
            let groups =
                MainLayout::new(rect.right * 96 / dpi as i32, rect.bottom * 96 / dpi as i32);
            theme::label_background(
                dc,
                lparam as HWND,
                hwnd,
                &[groups.device, groups.account, groups.api, groups.preview],
                dpi,
            );
            windows_sys::Win32::Graphics::Gdi::GetStockObject(
                windows_sys::Win32::Graphics::Gdi::NULL_BRUSH,
            ) as LRESULT
        }
        WM_DRAWITEM if !state.is_null() && lparam != 0 => {
            let item = &*(lparam as *const DRAWITEMSTRUCT);
            if item.CtlType == windows_sys::Win32::UI::Controls::ODT_COMBOBOX {
                theme::combo(item, (*state).font, (*state).dpi);
                return 1;
            }
            let output = if (1..=3).contains(&item.CtlID) {
                Some((
                    (*state).outputs[item.CtlID as usize - 1],
                    match item.CtlID {
                        1 => "USB 디스플레이",
                        2 => "바탕화면 상태창",
                        _ => "작업표시줄 위젯",
                    },
                ))
            } else {
                None
            };
            let mut client: RECT = std::mem::zeroed();
            GetClientRect(hwnd, &mut client);
            let dpi = (*state).dpi;
            let groups = MainLayout::new(
                client.right * 96 / dpi as i32,
                client.bottom * 96 / dpi as i32,
            );
            theme::label_background(
                item.hDC,
                item.hwndItem,
                hwnd,
                &[groups.device, groups.account, groups.api, groups.preview],
                dpi,
            );
            theme::button(item, (*state).font, (*state).dpi, output, item.CtlID == 5);
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
        let brush = unsafe { CreateSolidBrush(theme::PANEL) };
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
        let mut work: RECT = unsafe { std::mem::zeroed() };
        let got_work = unsafe {
            windows_sys::Win32::UI::WindowsAndMessaging::SystemParametersInfoW(
                windows_sys::Win32::UI::WindowsAndMessaging::SPI_GETWORKAREA,
                0,
                (&mut work as *mut RECT).cast(),
                0,
            )
        } != 0;
        if !got_work {
            work = RECT {
                left: 0,
                top: 0,
                right: scaled(1200, initial_dpi),
                bottom: scaled(900, initial_dpi),
            };
        }
        let initial = theme::initial_bounds(work, initial_dpi);
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
                wide("Mini Monitor 설정").as_ptr(),
                WS_OVERLAPPEDWINDOW | windows_sys::Win32::UI::WindowsAndMessaging::WS_CLIPCHILDREN,
                initial.left,
                initial.top,
                initial.right - initial.left,
                initial.bottom - initial.top,
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
            title_font: ptr::null_mut(),
            outputs: [false; 3],
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
        unsafe {
            theme::apply_chrome(hwnd);
        }
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
                    WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW as u32,
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
            (101, "Mini Monitor"),
            (110, "디스플레이와 사용량을 한곳에서 관리하세요"),
            (102, "미니 모니터 연결"),
            (103, "Codex 계정"),
            (104, "미니 모니터 화면"),
            (105, "OpenAI API"),
            (106, "확인 중"),
            (109, "밝기 25%"),
        ] {
            let child = unsafe {
                CreateWindowExW(
                    0,
                    wide("STATIC").as_ptr(),
                    wide(label).as_ptr(),
                    WS_CHILD | WS_VISIBLE | 0x0200, // SS_CENTERIMAGE (Windows SDK static style)
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
                WS_CHILD
                    | WS_VISIBLE
                    | WS_TABSTOP
                    | WS_VSCROLL
                    | CBS_DROPDOWNLIST as u32
                    | windows_sys::Win32::UI::WindowsAndMessaging::CBS_OWNERDRAWFIXED as u32
                    | windows_sys::Win32::UI::WindowsAndMessaging::CBS_HASSTRINGS as u32,
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
            theme::skin_check(flip, theme::PANEL);
            theme::skin_check(autostart, theme::BASE);
            theme::skin_combo(combo);
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
        unsafe {
            (*self.state.get()).outputs = [monitor, overlay, taskbar];
        }
        for (id, name, enabled) in [
            (1, "미니 모니터", monitor),
            (2, "PC 오버레이", overlay),
            (3, "작업표시줄 바", taskbar),
        ] {
            let child = unsafe { GetDlgItem(self.hwnd, id) };
            if child.is_null() {
                return Err(io::Error::other("settings control unavailable"));
            }
            let label = format!("{name} · {}", if enabled { "켜짐" } else { "꺼짐" });
            if unsafe { set_label_if_changed(child, &label)? } {
                unsafe {
                    InvalidateRect(child, ptr::null(), 0);
                }
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
        unsafe {
            set_label_if_changed(child, message)?;
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
        unsafe {
            set_label_if_changed(child, &text)?;
        }
        Ok(())
    }
    pub fn set_device_detection(&self, count: usize) -> io::Result<()> {
        let message = match count {
            0 => "지원 장치를 찾지 못했습니다.",
            1 => "지원 장치 준비됨 · 미니 모니터를 켜세요.",
            _ => "여러 장치 발견 · 연결할 장치를 확인하세요.",
        };
        unsafe {
            set_label_if_changed(GetDlgItem(self.hwnd, 102), message)?;
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
        unsafe {
            set_label_if_changed(child, &format!("미니 모니터 연결 · {message}"))?;
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
            if (*self.state.get()).preview.as_ref().is_some_and(|old| {
                old.width == frame.width && old.height == frame.height && old.pixels == frame.pixels
            }) {
                return Ok(());
            }
            (*self.state.get()).preview = Some(frame);
            let dpi = current_dpi(self.hwnd);
            let mut client: RECT = std::mem::zeroed();
            GetClientRect(self.hwnd, &mut client);
            let groups = MainLayout::new(
                (client.right * 96 / dpi as i32).max(360),
                (client.bottom * 96 / dpi as i32).max(420),
            );
            InvalidateRect(self.hwnd, &preview_rect(&groups, dpi), 0);
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
            if !(*self.state.get()).title_font.is_null() {
                DeleteObject((*self.state.get()).title_font as _);
            }
        }
    }
}

#[cfg(test)]
mod paint_tests {
    use super::*;
    use windows_sys::Win32::Graphics::Gdi::*;

    #[test]
    fn preview_paint_presents_a_completed_bitmap_without_intermediate_background_draws() {
        unsafe extern "system" fn record(
            _: HDC,
            _: *const HANDLETABLE,
            record: *const ENHMETARECORD,
            _: i32,
            data: isize,
        ) -> i32 {
            (*(data as *mut Vec<u32>)).push((*record).iType);
            1
        }
        let window = SettingsWindow::new_hidden().unwrap();
        window
            .set_preview(DashboardFrame {
                width: 480,
                height: 320,
                pixels: [20, 40, 180, 255].repeat(480 * 320),
                detail_pages: vec![],
            })
            .unwrap();
        unsafe {
            let dc = CreateEnhMetaFileW(ptr::null_mut(), ptr::null(), ptr::null(), ptr::null());
            assert!(!dc.is_null());
            paint_scene(window.handle(), dc, window.state.get());
            let metafile = CloseEnhMetaFile(dc);
            let mut records = Vec::<u32>::new();
            assert_ne!(
                EnumEnhMetaFile(
                    ptr::null_mut(),
                    metafile,
                    Some(record),
                    (&mut records as *mut Vec<u32>).cast(),
                    ptr::null()
                ),
                0
            );
            DeleteEnhMetaFile(metafile);
            assert_eq!(
                records.iter().filter(|r| **r == EMR_BITBLT).count(),
                1,
                "the visible DC must receive one completed frame, records={records:?}"
            );
            assert!(
                !records.iter().any(|r| matches!(
                    *r,
                    EMR_RECTANGLE | EMR_ROUNDRECT | EMR_GRADIENTFILL | EMR_STRETCHDIBITS
                )),
                "background/preview construction must remain offscreen, records={records:?}"
            );
        }
    }
}
