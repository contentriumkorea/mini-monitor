//! Modeless, thread-affine native preferences form; values never enter logs.
use crate::settings_theme as theme;
use std::{
    cell::UnsafeCell,
    io,
    marker::PhantomData,
    ptr,
    rc::Rc,
    sync::atomic::{AtomicU64, Ordering},
};
use windows_sys::Win32::{
    Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, RECT, WPARAM},
    Graphics::Gdi::{
        CreateFontW, CreateSolidBrush, DeleteObject, SetBkColor, SetTextColor, HBRUSH, HFONT,
    },
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Controls::{
            InitCommonControlsEx, ICC_BAR_CLASSES, INITCOMMONCONTROLSEX, TBM_SETPOS, TBM_SETRANGE,
            TRACKBAR_CLASSW,
        },
        HiDpi::{
            GetDpiForWindow, SetThreadDpiAwarenessContext,
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        },
        WindowsAndMessaging::*,
    },
};
pub enum Field {
    Check(&'static str, bool),
    Edit(&'static str, String, bool),
    Choice(&'static str, Vec<&'static str>, usize),
    Slider(&'static str, u16, u16, u16),
}
struct State {
    apply: bool,
    brush: HBRUSH,
    font: HFONT,
    fields: Vec<Field>,
    dpi: u32,
}
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
fn scale(v: i32, dpi: u32) -> i32 {
    ((i64::from(v) * i64::from(dpi) + 48) / 96) as i32
}
unsafe fn layout(hwnd: HWND, state: &mut State) {
    let mut rect: RECT = std::mem::zeroed();
    GetClientRect(hwnd, &mut rect);
    let s = |v| scale(v, state.dpi);
    let width = rect.right;
    let checks = state
        .fields
        .iter()
        .take_while(|f| matches!(f, Field::Check(..)))
        .count();
    for (i, field) in state.fields.iter().enumerate() {
        let row = if i < checks {
            i / 2
        } else {
            checks.div_ceil(2) + i - checks
        };
        let y = s(18 + row as i32 * 52);
        let label = GetDlgItem(hwnd, 200 + i as i32);
        let input = GetDlgItem(hwnd, 100 + i as i32);
        if matches!(field, Field::Check(..)) {
            let cell = if checks > 1 {
                (width - s(44)) / 2
            } else {
                width - s(36)
            };
            MoveWindow(
                input,
                s(18) + (i as i32 % 2) * (cell + s(8)),
                y,
                cell,
                s(32),
                1,
            );
        } else {
            MoveWindow(label, s(18), y, width - s(36), s(20), 1);
            MoveWindow(
                input,
                s(18),
                y + s(21),
                width - s(36),
                s(if matches!(field, Field::Choice(..)) {
                    180
                } else {
                    26
                }),
                1,
            );
        }
    }
    let bottom = rect.bottom;
    MoveWindow(
        GetDlgItem(hwnd, 1),
        width - s(206),
        bottom - s(46),
        s(88),
        s(30),
        1,
    );
    MoveWindow(
        GetDlgItem(hwnd, 2),
        width - s(106),
        bottom - s(46),
        s(88),
        s(30),
        1,
    );
    MoveWindow(
        GetDlgItem(hwnd, 3),
        s(18),
        bottom - s(90),
        width - s(36),
        s(36),
        1,
    );
}
unsafe fn font(hwnd: HWND, state: &mut State) {
    let next = CreateFontW(
        -((9 * state.dpi as i32 + 36) / 72),
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
    if next.is_null() {
        return;
    }
    for id in (1..=3)
        .chain(100..100 + state.fields.len() as i32)
        .chain(200..200 + state.fields.len() as i32)
    {
        let child = GetDlgItem(hwnd, id);
        if !child.is_null() {
            SendMessageW(child, WM_SETFONT, next as usize, 1);
        }
    }
    let previous = std::mem::replace(&mut state.font, next);
    if !previous.is_null() {
        DeleteObject(previous as _);
    }
}
unsafe extern "system" fn proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    let state = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State;
    if state.is_null() {
        return DefWindowProcW(hwnd, msg, wp, lp);
    }
    match msg {
        WM_NOTIFY => theme::slider_notify(lp).unwrap_or_else(|| DefWindowProcW(hwnd, msg, wp, lp)),
        WM_COMMAND if wp & 0xffff == 1 => {
            (*state).apply = true;
            0
        }
        WM_COMMAND if wp & 0xffff == 2 => {
            ShowWindow(hwnd, SW_HIDE);
            0
        }
        WM_CLOSE => {
            ShowWindow(hwnd, SW_HIDE);
            0
        }
        WM_SIZE => {
            layout(hwnd, &mut *state);
            windows_sys::Win32::Graphics::Gdi::InvalidateRect(hwnd, ptr::null(), 0);
            0
        }
        WM_PAINT => {
            let mut paint: windows_sys::Win32::Graphics::Gdi::PAINTSTRUCT = std::mem::zeroed();
            let dc = windows_sys::Win32::Graphics::Gdi::BeginPaint(hwnd, &mut paint);
            if !dc.is_null() {
                let mut r: RECT = std::mem::zeroed();
                GetClientRect(hwnd, &mut r);
                theme::gradient(dc, r, 27, 18);
                let inset = scale(10, (*state).dpi);
                r.left += inset;
                r.top += inset;
                r.right -= inset;
                r.bottom -= scale(96, (*state).dpi);
                theme::paint_panel(dc, r, scale(12, (*state).dpi));
                windows_sys::Win32::Graphics::Gdi::EndPaint(hwnd, &paint);
            }
            0
        }
        WM_DRAWITEM if lp != 0 => {
            let item = &*(lp as *const windows_sys::Win32::UI::Controls::DRAWITEMSTRUCT);
            if item.CtlType == windows_sys::Win32::UI::Controls::ODT_COMBOBOX {
                theme::combo(item, (*state).font, (*state).dpi);
            } else {
                let mut client: RECT = std::mem::zeroed();
                GetClientRect(hwnd, &mut client);
                let dpi = (*state).dpi;
                let panel = RECT {
                    left: 10,
                    top: 10,
                    right: client.right * 96 / dpi as i32 - 10,
                    bottom: client.bottom * 96 / dpi as i32 - 96,
                };
                theme::label_background(item.hDC, item.hwndItem, hwnd, &[panel], dpi);
                theme::button(item, (*state).font, (*state).dpi, None, wp == 1);
            }
            1
        }
        WM_MEASUREITEM if lp != 0 => {
            let item = &mut *(lp as *mut windows_sys::Win32::UI::Controls::MEASUREITEMSTRUCT);
            item.itemHeight = scale(24, (*state).dpi) as u32;
            1
        }
        WM_DPICHANGED => {
            (*state).dpi = (wp as u32 & 0xffff).max(96);
            font(hwnd, &mut *state);
            let r = &*(lp as *const RECT);
            SetWindowPos(
                hwnd,
                ptr::null_mut(),
                r.left,
                r.top,
                r.right - r.left,
                r.bottom - r.top,
                SWP_NOZORDER | SWP_NOACTIVATE,
            );
            layout(hwnd, &mut *state);
            0
        }
        WM_CTLCOLORSTATIC | WM_CTLCOLORBTN | WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX => {
            if theme::high_contrast() {
                use windows_sys::Win32::Graphics::Gdi::*;
                SetTextColor(wp as _, GetSysColor(COLOR_WINDOWTEXT));
                SetBkColor(wp as _, GetSysColor(COLOR_WINDOW));
                return GetSysColorBrush(COLOR_WINDOW) as LRESULT;
            }
            if msg == WM_CTLCOLORSTATIC || msg == WM_CTLCOLORBTN {
                let mut client: RECT = std::mem::zeroed();
                GetClientRect(hwnd, &mut client);
                let dpi = (*state).dpi;
                let panel = RECT {
                    left: 10,
                    top: 10,
                    right: client.right * 96 / dpi as i32 - 10,
                    bottom: client.bottom * 96 / dpi as i32 - 96,
                };
                theme::label_background(wp as _, lp as HWND, hwnd, &[panel], dpi);
                SetTextColor(wp as _, theme::TEXT);
                return windows_sys::Win32::Graphics::Gdi::GetStockObject(
                    windows_sys::Win32::Graphics::Gdi::NULL_BRUSH,
                ) as LRESULT;
            }
            SetTextColor(wp as _, theme::TEXT);
            SetBkColor(wp as _, theme::PANEL);
            (*state).brush as LRESULT
        }
        WM_NCDESTROY => {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
            DefWindowProcW(hwnd, msg, wp, lp)
        }
        _ => DefWindowProcW(hwnd, msg, wp, lp),
    }
}
static NEXT: AtomicU64 = AtomicU64::new(1);
pub struct OptionsWindow {
    hwnd: HWND,
    instance: HINSTANCE,
    class: Vec<u16>,
    state: Box<UnsafeCell<State>>,
    _thread: PhantomData<Rc<()>>,
}
impl OptionsWindow {
    // HWND is an opaque OS handle, never dereferenced by this wrapper.
    #[allow(clippy::not_unsafe_ptr_arg_deref)]
    pub fn new(owner: HWND, title: &str, fields: Vec<Field>) -> io::Result<Self> {
        if fields.is_empty() || fields.len() > 10 {
            return Err(io::Error::other("invalid preferences form"));
        }
        let previous =
            unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
        struct Guard(isize);
        impl Drop for Guard {
            fn drop(&mut self) {
                unsafe {
                    SetThreadDpiAwarenessContext(self.0 as _);
                }
            }
        }
        if previous.is_null() {
            return Err(io::Error::other("DPI unavailable"));
        }
        let _guard = Guard(previous as isize);
        unsafe {
            let controls = INITCOMMONCONTROLSEX {
                dwSize: std::mem::size_of::<INITCOMMONCONTROLSEX>() as u32,
                dwICC: ICC_BAR_CLASSES,
            };
            InitCommonControlsEx(&controls);
        }
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let class = wide(&format!(
            "MiniMonitorOptions_{}_{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let brush = unsafe { CreateSolidBrush(theme::PANEL) };
        if brush.is_null() {
            return Err(io::Error::last_os_error());
        }
        let wc = WNDCLASSW {
            lpfnWndProc: Some(proc),
            hInstance: instance,
            hCursor: unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) },
            hbrBackground: brush,
            lpszClassName: class.as_ptr(),
            ..unsafe { std::mem::zeroed() }
        };
        if unsafe { RegisterClassW(&wc) } == 0 {
            unsafe {
                DeleteObject(brush as _);
            }
            return Err(io::Error::last_os_error());
        }
        let dpi = unsafe { GetDpiForWindow(owner) }.max(96);
        let s = |v| scale(v, dpi);
        let checks = fields
            .iter()
            .take_while(|f| matches!(f, Field::Check(..)))
            .count();
        let height = s(160 + 52 * (fields.len() - checks + checks.div_ceil(2)) as i32);
        let mut work: RECT = unsafe { std::mem::zeroed() };
        unsafe {
            use windows_sys::Win32::Graphics::Gdi::{
                GetMonitorInfoW, MonitorFromWindow, MONITORINFO, MONITOR_DEFAULTTONEAREST,
            };
            let mut info: MONITORINFO = std::mem::zeroed();
            info.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
            if GetMonitorInfoW(
                MonitorFromWindow(owner, MONITOR_DEFAULTTONEAREST),
                &mut info,
            ) != 0
            {
                work = info.rcWork;
            } else {
                SystemParametersInfoW(SPI_GETWORKAREA, 0, (&mut work as *mut RECT).cast(), 0);
            }
        }
        let width = s(500).min((work.right - work.left - 24).max(320));
        let height = height.min((work.bottom - work.top - 24).max(300));
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                class.as_ptr(),
                wide(title).as_ptr(),
                WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU,
                work.left + 12,
                work.top + 12,
                width,
                height,
                owner,
                ptr::null_mut(),
                instance,
                ptr::null(),
            )
        };
        if hwnd.is_null() {
            unsafe {
                UnregisterClassW(class.as_ptr(), instance);
                DeleteObject(brush as _);
            }
            return Err(io::Error::last_os_error());
        }
        let state = Box::new(UnsafeCell::new(State {
            apply: false,
            brush,
            font: ptr::null_mut(),
            fields,
            dpi,
        }));
        let form = Self {
            hwnd,
            instance,
            class,
            state,
            _thread: PhantomData,
        };
        unsafe {
            theme::apply_chrome(hwnd);
        }
        unsafe {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, form.state.get() as isize);
        }
        for (i, field) in unsafe { &(*form.state.get()).fields }.iter().enumerate() {
            let label = match field {
                Field::Check(label, _)
                | Field::Edit(label, ..)
                | Field::Choice(label, ..)
                | Field::Slider(label, ..) => *label,
            };
            if !matches!(field, Field::Check(..)) {
                form.control("STATIC", label, 200 + i as i32, 0)?;
            }
            match field {
                Field::Check(_, checked) => {
                    let child =
                        form.control("BUTTON", label, 100 + i as i32, BS_AUTOCHECKBOX as u32)?;
                    unsafe {
                        SendMessageW(child, BM_SETCHECK, usize::from(*checked), 0);
                        theme::skin_check(child, theme::PANEL);
                    }
                }
                Field::Edit(_, value, password) => {
                    form.control(
                        "EDIT",
                        value,
                        100 + i as i32,
                        WS_BORDER
                            | ES_AUTOHSCROLL as u32
                            | if *password { ES_PASSWORD as u32 } else { 0 },
                    )?;
                }
                Field::Choice(_, choices, index) => {
                    let child = form.control(
                        "COMBOBOX",
                        "",
                        100 + i as i32,
                        CBS_DROPDOWNLIST as u32
                            | CBS_OWNERDRAWFIXED as u32
                            | CBS_HASSTRINGS as u32
                            | WS_VSCROLL,
                    )?;
                    for choice in choices {
                        unsafe {
                            SendMessageW(child, CB_ADDSTRING, 0, wide(choice).as_ptr() as isize);
                        }
                    }
                    unsafe {
                        SendMessageW(child, CB_SETCURSEL, *index, 0);
                        theme::skin_combo(child);
                    }
                }
                Field::Slider(_, min, max, value) => {
                    let child = unsafe {
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
                            (100 + i) as _,
                            instance,
                            ptr::null(),
                        )
                    };
                    if child.is_null() {
                        return Err(io::Error::last_os_error());
                    }
                    unsafe {
                        SendMessageW(
                            child,
                            TBM_SETRANGE,
                            1,
                            (u32::from(*min) | (u32::from(*max) << 16)) as isize,
                        );
                        SendMessageW(child, TBM_SETPOS, 1, *value as isize);
                    }
                }
            }
        }
        form.control("BUTTON", "적용", 1, BS_OWNERDRAW as u32)?;
        form.control("BUTTON", "닫기", 2, BS_OWNERDRAW as u32)?;
        form.control("STATIC", "", 3, 0)?;
        unsafe {
            font(hwnd, &mut *form.state.get());
            layout(hwnd, &mut *form.state.get());
        }
        Ok(form)
    }
    fn control(&self, class: &str, text: &str, id: i32, style: u32) -> io::Result<HWND> {
        let child = unsafe {
            CreateWindowExW(
                0,
                wide(class).as_ptr(),
                wide(text).as_ptr(),
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | style,
                0,
                0,
                1,
                1,
                self.hwnd,
                id as _,
                self.instance,
                ptr::null(),
            )
        };
        if child.is_null() {
            Err(io::Error::last_os_error())
        } else {
            Ok(child)
        }
    }
    pub fn handle(&self) -> HWND {
        self.hwnd
    }
    pub fn translate_message(&self, message: &mut MSG) -> bool {
        unsafe {
            IsWindowVisible(self.hwnd) != 0
                && (message.hwnd == self.hwnd || IsChild(self.hwnd, message.hwnd) != 0)
                && IsDialogMessageW(self.hwnd, message) != 0
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
    pub fn clear_edit(&self, index: usize) {
        unsafe {
            SetWindowTextW(GetDlgItem(self.hwnd, 100 + index as i32), wide("").as_ptr());
        }
    }
    pub fn message(&self, message: &str) {
        unsafe {
            SetWindowTextW(GetDlgItem(self.hwnd, 3), wide(message).as_ptr());
        }
    }
    pub fn take_apply(&self) -> io::Result<Option<Vec<String>>> {
        let state = unsafe { &mut *self.state.get() };
        if !std::mem::take(&mut state.apply) {
            return Ok(None);
        }
        let mut values = Vec::new();
        for (i, field) in state.fields.iter().enumerate() {
            let child = unsafe { GetDlgItem(self.hwnd, 100 + i as i32) };
            let value = match field {
                Field::Check(..) => {
                    (unsafe { SendMessageW(child, BM_GETCHECK, 0, 0) } == 1).to_string()
                }
                Field::Choice(..) => unsafe { SendMessageW(child, CB_GETCURSEL, 0, 0) }.to_string(),
                Field::Slider(..) => unsafe { SendMessageW(child, WM_USER, 0, 0) }.to_string(),
                Field::Edit(..) => {
                    let len = unsafe { GetWindowTextLengthW(child) };
                    if !(0..=4096).contains(&len) {
                        return Err(io::Error::other("input too long"));
                    }
                    let mut data = vec![0u16; len as usize + 1];
                    let count =
                        unsafe { GetWindowTextW(child, data.as_mut_ptr(), data.len() as i32) };
                    String::from_utf16(&data[..count as usize])
                        .map_err(|_| io::Error::other("invalid input"))?
                }
            };
            values.push(value);
        }
        Ok(Some(values))
    }
}
impl Drop for OptionsWindow {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.hwnd);
            let state = &mut *self.state.get();
            if !state.font.is_null() {
                DeleteObject(state.font as _);
            }
            DeleteObject(state.brush as _);
            UnregisterClassW(self.class.as_ptr(), self.instance);
        }
    }
}
