//! Shared, event-driven neutral smoke surfaces. No polling, animation or desktop capture.
use windows_sys::Win32::{
    Foundation::{HWND, RECT},
    Graphics::Gdi::*,
    UI::{
        Controls::{DRAWITEMSTRUCT, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED},
        WindowsAndMessaging::{GetWindowTextW, SystemParametersInfoW, SPI_GETHIGHCONTRAST},
    },
};
#[repr(C)]
struct HighContrast {
    size: u32,
    flags: u32,
    scheme: *mut u16,
}

pub const BASE: u32 = 0x161616;
pub const PANEL: u32 = 0x242424;
pub const TEXT: u32 = 0xeeeeee;
pub const MUTED: u32 = 0xaaaaaa;

/// Optional system colors: window, text, highlight, highlight text, disabled text.
pub fn combo_palette(system: Option<[u32; 5]>, disabled: bool, selected: bool) -> (u32, u32) {
    let [background, foreground, highlight, highlighted_text, gray] =
        system.unwrap_or([PANEL, TEXT, 0x505050, TEXT, 0x808080]);
    if disabled {
        (background, gray)
    } else if selected {
        (highlight, highlighted_text)
    } else {
        (background, foreground)
    }
}
unsafe fn system_combo_colors() -> Option<[u32; 5]> {
    high_contrast().then(|| {
        [
            GetSysColor(COLOR_WINDOW),
            GetSysColor(COLOR_WINDOWTEXT),
            GetSysColor(COLOR_HIGHLIGHT),
            GetSysColor(COLOR_HIGHLIGHTTEXT),
            GetSysColor(COLOR_GRAYTEXT),
        ]
    })
}

pub fn initial_bounds(work: RECT, dpi: u32) -> RECT {
    let width = scaled(960, dpi).min((work.right - work.left - 24).max(1));
    let height = scaled(680, dpi).min((work.bottom - work.top - 24).max(1));
    let left = work.left + (work.right - work.left - width) / 2;
    let top = work.top + (work.bottom - work.top - height) / 2;
    RECT {
        left,
        top,
        right: left + width,
        bottom: top + height,
    }
}

pub(crate) unsafe fn skin_combo(hwnd: HWND) {
    windows_sys::Win32::UI::Shell::SetWindowSubclass(hwnd, Some(combo_proc), 0x4d4e, 0);
}
unsafe fn paint_combo_control(hwnd: HWND, dc: HDC) {
    use windows_sys::Win32::UI::{
        HiDpi::GetDpiForWindow, Input::KeyboardAndMouse::GetFocus, WindowsAndMessaging::*,
    };
    let mut r: RECT = std::mem::zeroed();
    GetClientRect(hwnd, &mut r);
    let dpi = GetDpiForWindow(hwnd).max(96);
    let s = |v| scaled(v, dpi);
    let (background, foreground) = combo_palette(
        system_combo_colors(),
        windows_sys::Win32::UI::Input::KeyboardAndMouse::IsWindowEnabled(hwnd) == 0,
        false,
    );
    fill(dc, r, background);
    stroke(
        dc,
        r,
        s(7),
        if GetFocus() == hwnd {
            0xb0b0b0
        } else {
            0x686868
        },
    );
    let index = SendMessageW(hwnd, CB_GETCURSEL, 0, 0);
    let mut chars = [0u16; 256];
    if index >= 0 {
        let len = SendMessageW(hwnd, CB_GETLBTEXTLEN, index as usize, 0);
        if (0..255).contains(&len) {
            SendMessageW(
                hwnd,
                CB_GETLBTEXT,
                index as usize,
                chars.as_mut_ptr() as isize,
            );
        }
    }
    let value = String::from_utf16_lossy(
        &chars[..chars.iter().position(|&c| c == 0).unwrap_or(chars.len())],
    );
    raw_text(
        dc,
        SendMessageW(hwnd, WM_GETFONT, 0, 0) as HFONT,
        RECT {
            left: s(8),
            top: 0,
            right: r.right - s(28),
            bottom: r.bottom,
        },
        &value,
        foreground,
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
    );
    let pen = CreatePen(PS_SOLID, s(1).max(1), foreground);
    if !pen.is_null() {
        let old = SelectObject(dc, pen as _);
        let x = r.right - s(17);
        let y = r.bottom / 2;
        MoveToEx(dc, x - s(3), y - s(2), std::ptr::null_mut());
        LineTo(dc, x, y + s(1));
        LineTo(dc, x + s(3), y - s(2));
        SelectObject(dc, old);
        DeleteObject(pen as _);
    }
}
unsafe extern "system" fn combo_proc(
    hwnd: HWND,
    msg: u32,
    wp: usize,
    lp: isize,
    _id: usize,
    _data: usize,
) -> isize {
    use windows_sys::Win32::UI::{
        Shell::{DefSubclassProc, RemoveWindowSubclass},
        WindowsAndMessaging::*,
    };
    match msg {
        WM_PAINT => {
            let mut p: PAINTSTRUCT = std::mem::zeroed();
            let dc = BeginPaint(hwnd, &mut p);
            if !dc.is_null() {
                paint_combo_control(hwnd, dc);
                EndPaint(hwnd, &p);
            }
            0
        }
        WM_PRINTCLIENT => {
            paint_combo_control(hwnd, wp as HDC);
            0
        }
        WM_ERASEBKGND => 1,
        WM_NCDESTROY => {
            RemoveWindowSubclass(hwnd, Some(combo_proc), 0x4d4e);
            DefSubclassProc(hwnd, msg, wp, lp)
        }
        _ => {
            let result = DefSubclassProc(hwnd, msg, wp, lp);
            if matches!(
                msg,
                CB_SETCURSEL | WM_ENABLE | WM_SETFOCUS | WM_KILLFOCUS | WM_SIZE
            ) {
                InvalidateRect(hwnd, std::ptr::null(), 0);
            }
            result
        }
    }
}

/// Repaint a static control's backdrop in parent coordinates instead of opaque label strips.
pub(crate) unsafe fn label_background(
    dc: HDC,
    child: HWND,
    parent: HWND,
    panels: &[RECT],
    dpi: u32,
) {
    use windows_sys::Win32::{
        Foundation::POINT,
        UI::WindowsAndMessaging::{GetClientRect, GetWindowRect},
    };
    let mut bounds: RECT = std::mem::zeroed();
    GetWindowRect(child, &mut bounds);
    let mut point = POINT {
        x: bounds.left,
        y: bounds.top,
    };
    ScreenToClient(parent, &mut point);
    let mut child_rect: RECT = std::mem::zeroed();
    GetClientRect(child, &mut child_rect);
    let mut full: RECT = std::mem::zeroed();
    GetClientRect(parent, &mut full);
    // Static controls pass a child-local DC. Paint only that rectangle; translating
    // and replaying the whole parent can differ between WM_PAINT and PrintWindow.
    let panel = panels
        .iter()
        .map(|r| physical(*r, dpi))
        .find(|r| point.x >= r.left && point.x < r.right && point.y >= r.top && point.y < r.bottom);
    let (area, top, bottom) = panel.map_or((full, 26, 17), |r| (r, 43, 31));
    let shade = |y: i32| {
        (top - (top - bottom) * (y - area.top).clamp(0, area.bottom - area.top)
            / (area.bottom - area.top).max(1)) as u8
    };
    gradient(
        dc,
        child_rect,
        shade(point.y),
        shade(point.y + child_rect.bottom),
    );
    SetBkMode(dc, TRANSPARENT as i32);
}

/// Reuses native checkbox semantics/accessibility and replaces only its paint path.
pub(crate) unsafe fn skin_check(hwnd: HWND, background: u32) {
    windows_sys::Win32::UI::Shell::SetWindowSubclass(
        hwnd,
        Some(check_proc),
        0x4d4d,
        background as usize,
    );
}
unsafe fn paint_check(hwnd: HWND, dc: HDC, background: u32) {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetFocus, IsWindowEnabled};
    use windows_sys::Win32::UI::{HiDpi::GetDpiForWindow, WindowsAndMessaging::*};
    let mut r: RECT = std::mem::zeroed();
    GetClientRect(hwnd, &mut r);
    let dpi = GetDpiForWindow(hwnd).max(96);
    let s = |n| scaled(n, dpi);
    let hc = high_contrast();
    fill(
        dc,
        r,
        if hc {
            GetSysColor(COLOR_WINDOW)
        } else {
            background
        },
    );
    let checked = SendMessageW(hwnd, BM_GETCHECK, 0, 0) == 1;
    let side = s(15);
    let top = (r.bottom - side) / 2;
    let box_rect = RECT {
        left: 1,
        top,
        right: side + 1,
        bottom: top + side,
    };
    stroke(
        dc,
        box_rect,
        s(4),
        if hc {
            GetSysColor(COLOR_WINDOWTEXT)
        } else if checked {
            0xc6c6c6
        } else {
            0x777777
        },
    );
    if checked {
        let pen = CreatePen(
            PS_SOLID,
            s(2).max(1),
            if hc {
                GetSysColor(COLOR_WINDOWTEXT)
            } else {
                TEXT
            },
        );
        if !pen.is_null() {
            let old = SelectObject(dc, pen as _);
            MoveToEx(dc, s(4), top + s(8), std::ptr::null_mut());
            LineTo(dc, s(7), top + s(11));
            LineTo(dc, s(12), top + s(4));
            SelectObject(dc, old);
            DeleteObject(pen as _);
        }
    }
    let mut label = [0u16; 256];
    let n = GetWindowTextW(hwnd, label.as_mut_ptr(), label.len() as i32);
    let value = String::from_utf16_lossy(&label[..n.max(0) as usize]);
    let font = SendMessageW(hwnd, WM_GETFONT, 0, 0) as HFONT;
    text(
        dc,
        font,
        RECT {
            left: s(24),
            top: 0,
            right: r.right,
            bottom: r.bottom,
        },
        &value,
        if hc {
            GetSysColor(COLOR_WINDOWTEXT)
        } else if IsWindowEnabled(hwnd) == 0 {
            MUTED
        } else {
            TEXT
        },
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
    );
    if GetFocus() == hwnd {
        stroke(
            dc,
            RECT {
                left: 0,
                top: 0,
                right: r.right,
                bottom: r.bottom,
            },
            s(4),
            0x969696,
        );
    }
}
unsafe extern "system" fn check_proc(
    hwnd: HWND,
    msg: u32,
    wp: usize,
    lp: isize,
    _id: usize,
    data: usize,
) -> isize {
    use windows_sys::Win32::UI::{
        Shell::{DefSubclassProc, RemoveWindowSubclass},
        WindowsAndMessaging::*,
    };
    match msg {
        WM_PAINT => {
            let mut p: PAINTSTRUCT = std::mem::zeroed();
            let dc = BeginPaint(hwnd, &mut p);
            if !dc.is_null() {
                paint_check(hwnd, dc, data as u32);
                EndPaint(hwnd, &p);
            }
            0
        }
        WM_PRINTCLIENT => {
            paint_check(hwnd, wp as HDC, data as u32);
            0
        }
        WM_ERASEBKGND => 1,
        WM_NCDESTROY => {
            RemoveWindowSubclass(hwnd, Some(check_proc), 0x4d4d);
            DefSubclassProc(hwnd, msg, wp, lp)
        }
        _ => {
            let value = DefSubclassProc(hwnd, msg, wp, lp);
            if matches!(
                msg,
                BM_SETCHECK | BM_CLICK | WM_ENABLE | WM_SETFOCUS | WM_KILLFOCUS
            ) {
                InvalidateRect(hwnd, std::ptr::null(), 0);
            }
            value
        }
    }
}
pub(crate) unsafe fn combo(item: &DRAWITEMSTRUCT, font: HFONT, dpi: u32) {
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    let r = item.rcItem;
    let (background, foreground) = combo_palette(
        system_combo_colors(),
        item.itemState & ODS_DISABLED != 0,
        item.itemState & windows_sys::Win32::UI::Controls::ODS_SELECTED != 0,
    );
    fill(item.hDC, r, background);
    let mut label = [0u16; 256];
    let index = item.itemID as isize;
    if index >= 0 {
        let length = SendMessageW(item.hwndItem, CB_GETLBTEXTLEN, index as usize, 0);
        if (0..255).contains(&length) {
            SendMessageW(
                item.hwndItem,
                CB_GETLBTEXT,
                index as usize,
                label.as_mut_ptr() as isize,
            );
        }
    }
    let label = String::from_utf16_lossy(
        &label[..label.iter().position(|&n| n == 0).unwrap_or(label.len())],
    );
    raw_text(
        item.hDC,
        font,
        RECT {
            left: r.left + scaled(8, dpi),
            top: r.top,
            right: r.right - scaled(6, dpi),
            bottom: r.bottom,
        },
        &label,
        foreground,
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
    );
    if item.itemState & ODS_FOCUS != 0 {
        stroke(item.hDC, r, scaled(4, dpi), 0xaaaaaa);
    }
}
pub(crate) unsafe fn slider_notify(lp: isize) -> Option<isize> {
    use windows_sys::Win32::UI::{Controls::*, WindowsAndMessaging::GetClassNameW};
    if lp == 0 {
        return None;
    }
    let hdr = &*(lp as *const NMHDR);
    if hdr.code != NM_CUSTOMDRAW {
        return None;
    }
    let mut class = [0u16; 40];
    let n = GetClassNameW(hdr.hwndFrom, class.as_mut_ptr(), 40);
    if String::from_utf16_lossy(&class[..n.max(0) as usize]) != "msctls_trackbar32" {
        return None;
    }
    let draw = &*(lp as *const NMCUSTOMDRAW);
    if draw.dwDrawStage == CDDS_PREPAINT {
        return Some(CDRF_NOTIFYITEMDRAW as isize);
    }
    if draw.dwDrawStage == CDDS_ITEMPREPAINT {
        if draw.dwItemSpec == TBCD_CHANNEL as usize {
            let mut r = draw.rc;
            let mid = (r.top + r.bottom) / 2;
            r.top = mid - 2;
            r.bottom = mid + 2;
            fill(draw.hdc, r, 0x747474);
            return Some(CDRF_SKIPDEFAULT as isize);
        }
        if draw.dwItemSpec == TBCD_THUMB as usize {
            fill(draw.hdc, draw.rc, 0xcfcfcf);
            stroke(draw.hdc, draw.rc, 3, 0xeeeeee);
            return Some(CDRF_SKIPDEFAULT as isize);
        }
    }
    Some(CDRF_DODEFAULT as isize)
}

pub struct MainLayout {
    pub device: RECT,
    pub account: RECT,
    pub api: RECT,
    pub preview: RECT,
    pub footer_top: i32,
}
impl MainLayout {
    pub fn new(width: i32, height: i32) -> Self {
        let compact = height < 570;
        let left = ((width - 56) * 40 / 100).clamp(300, 400);
        let footer_top = height - if compact { 40 } else { 48 };
        let top = if compact { 112 } else { 166 };
        let space = footer_top - 16 - top;
        let device_h = if compact {
            96
        } else {
            (space * 42 / 100).clamp(136, 164)
        };
        let account_h = if compact {
            94
        } else {
            (space * 38 / 100).clamp(126, 148)
        };
        let gap = if compact { 8 } else { 12 };
        let device = RECT {
            left: 20,
            top,
            right: 20 + left,
            bottom: top + device_h,
        };
        let account = RECT {
            left: 20,
            top: device.bottom + gap,
            right: 20 + left,
            bottom: device.bottom + gap + account_h,
        };
        let api = RECT {
            left: 20,
            top: account.bottom + gap,
            right: 20 + left,
            bottom: footer_top - 16,
        };
        Self {
            device,
            account,
            api,
            preview: RECT {
                left: 36 + left,
                top,
                right: width - 20,
                bottom: footer_top - 16,
            },
            footer_top,
        }
    }
}
pub fn scaled(v: i32, dpi: u32) -> i32 {
    ((i64::from(v) * i64::from(dpi) + 48) / 96) as i32
}
pub fn physical(r: RECT, dpi: u32) -> RECT {
    RECT {
        left: scaled(r.left, dpi),
        top: scaled(r.top, dpi),
        right: scaled(r.right, dpi),
        bottom: scaled(r.bottom, dpi),
    }
}

#[link(name = "dwmapi")]
extern "system" {
    fn DwmSetWindowAttribute(
        hwnd: HWND,
        attribute: u32,
        value: *const std::ffi::c_void,
        size: u32,
    ) -> i32;
}
pub fn high_contrast() -> bool {
    unsafe {
        let mut hc: HighContrast = std::mem::zeroed();
        hc.size = std::mem::size_of::<HighContrast>() as u32;
        SystemParametersInfoW(
            SPI_GETHIGHCONTRAST,
            hc.size,
            (&mut hc as *mut HighContrast).cast(),
            0,
        ) != 0
            && hc.flags & 1 != 0
    }
}
/// Optional supported Windows chrome. Failure on older OS versions is non-fatal.
pub(crate) unsafe fn apply_chrome(hwnd: HWND) {
    if high_contrast() {
        return;
    }
    for (attribute, value) in [(20, 1u32), (33, 2), (34, 0x404040), (35, BASE)] {
        let _ = DwmSetWindowAttribute(hwnd, attribute, (&value as *const u32).cast(), 4);
    }
    // System backdrop can enhance exposed non-client surfaces; opaque content remains readable.
    let backdrop = 3u32;
    let _ = DwmSetWindowAttribute(hwnd, 38, (&backdrop as *const u32).cast(), 4);
}
pub(crate) unsafe fn fill(dc: HDC, r: RECT, color: u32) {
    let b = CreateSolidBrush(color);
    if !b.is_null() {
        FillRect(dc, &r, b);
        DeleteObject(b as _);
    }
}
pub(crate) unsafe fn gradient(dc: HDC, r: RECT, top: u8, bottom: u8) {
    if high_contrast() {
        fill(dc, r, GetSysColor(COLOR_WINDOW));
        return;
    }
    let vertices = [
        TRIVERTEX {
            x: r.left,
            y: r.top,
            Red: u16::from(top) << 8,
            Green: u16::from(top) << 8,
            Blue: u16::from(top) << 8,
            Alpha: 0xffff,
        },
        TRIVERTEX {
            x: r.right,
            y: r.bottom,
            Red: u16::from(bottom) << 8,
            Green: u16::from(bottom) << 8,
            Blue: u16::from(bottom) << 8,
            Alpha: 0xffff,
        },
    ];
    let mesh = GRADIENT_RECT {
        UpperLeft: 0,
        LowerRight: 1,
    };
    GradientFill(
        dc,
        vertices.as_ptr(),
        2,
        (&mesh as *const GRADIENT_RECT).cast(),
        1,
        GRADIENT_FILL_RECT_V,
    );
}
pub(crate) unsafe fn stroke(dc: HDC, r: RECT, radius: i32, color: u32) {
    let pen = CreatePen(
        PS_SOLID,
        1,
        if high_contrast() {
            GetSysColor(COLOR_WINDOWTEXT)
        } else {
            color
        },
    );
    if pen.is_null() {
        return;
    }
    let old_pen = SelectObject(dc, pen as _);
    let old_brush = SelectObject(dc, GetStockObject(NULL_BRUSH));
    RoundRect(dc, r.left, r.top, r.right, r.bottom, radius * 2, radius * 2);
    SelectObject(dc, old_brush);
    SelectObject(dc, old_pen);
    DeleteObject(pen as _);
}
/// Paints a cached-by-the-OS static smoke panel, with no retained graphics resources.
/// # Safety
/// `dc` must be a valid device context owned by the calling UI/paint thread.
pub unsafe fn paint_panel(dc: HDC, r: RECT, radius: i32) {
    if high_contrast() {
        fill(dc, r, GetSysColor(COLOR_WINDOW));
        return;
    }
    let saved = SaveDC(dc);
    if saved == 0 {
        return;
    }
    let region = CreateRoundRectRgn(
        r.left,
        r.top,
        r.right + 1,
        r.bottom + 1,
        radius * 2,
        radius * 2,
    );
    if !region.is_null() {
        ExtSelectClipRgn(dc, region, RGN_AND);
        gradient(dc, r, 43, 31);
        DeleteObject(region as _);
    }
    RestoreDC(dc, saved);
    stroke(dc, r, radius, 0x464646);
    let top = RECT {
        left: r.left + radius,
        top: r.top,
        right: r.right - radius,
        bottom: r.top + 1,
    };
    fill(dc, top, 0x555555);
}
pub(crate) unsafe fn text(dc: HDC, font: HFONT, r: RECT, label: &str, color: u32, flags: u32) {
    raw_text(
        dc,
        font,
        r,
        label,
        if high_contrast() {
            GetSysColor(COLOR_WINDOWTEXT)
        } else {
            color
        },
        flags,
    );
}
unsafe fn raw_text(dc: HDC, font: HFONT, r: RECT, label: &str, color: u32, flags: u32) {
    let previous = SelectObject(dc, font as _);
    SetBkMode(dc, TRANSPARENT as i32);
    SetTextColor(dc, color);
    let mut r = r;
    let value: Vec<u16> = label.encode_utf16().collect();
    DrawTextW(dc, value.as_ptr(), value.len() as i32, &mut r, flags);
    SelectObject(dc, previous);
}
pub(crate) unsafe fn button(
    item: &DRAWITEMSTRUCT,
    font: HFONT,
    dpi: u32,
    output: Option<(bool, &str)>,
    primary: bool,
) {
    let dc = item.hDC;
    let r = item.rcItem;
    let s = |n| scaled(n, dpi);
    let disabled = item.itemState & ODS_DISABLED != 0;
    let pressed = item.itemState & ODS_SELECTED != 0;
    if high_contrast() {
        fill(dc, r, GetSysColor(COLOR_BTNFACE));
    } else {
        fill(dc, r, BASE);
        paint_panel(dc, r, s(if output.is_some() { 14 } else { 8 }));
        if pressed {
            let inset = RECT {
                left: r.left + 2,
                top: r.top + 2,
                right: r.right - 2,
                bottom: r.bottom - 2,
            };
            fill(dc, inset, 0x383838);
        }
    }
    let mut label = [0u16; 160];
    let n = GetWindowTextW(item.hwndItem, label.as_mut_ptr(), label.len() as i32);
    let label = String::from_utf16_lossy(&label[..n.max(0) as usize]);
    if let Some((enabled, subtitle)) = output {
        let compact = r.bottom - r.top < s(60);
        let title = label.split(" · ").next().unwrap_or(&label);
        text(
            dc,
            font,
            RECT {
                left: s(16),
                top: s(if compact { 3 } else { 12 }),
                right: r.right - s(65),
                bottom: s(if compact { 24 } else { 33 }),
            },
            title,
            if disabled { MUTED } else { TEXT },
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
        );
        text(
            dc,
            font,
            RECT {
                left: s(16),
                top: s(if compact { 25 } else { 37 }),
                right: r.right - s(60),
                bottom: r.bottom - s(if compact { 3 } else { 10 }),
            },
            subtitle,
            MUTED,
            DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS,
        );
        let switch = RECT {
            left: r.right - s(55),
            top: (r.bottom - s(22)) / 2,
            right: r.right - s(15),
            bottom: (r.bottom + s(22)) / 2,
        };
        let brush = CreateSolidBrush(if enabled { 0xdcdcdc } else { 0x505050 });
        let pen = CreatePen(PS_SOLID, 1, if enabled { 0xf0f0f0 } else { 0x646464 });
        if !brush.is_null() && !pen.is_null() {
            let b = SelectObject(dc, brush as _);
            let p = SelectObject(dc, pen as _);
            RoundRect(
                dc,
                switch.left,
                switch.top,
                switch.right,
                switch.bottom,
                s(22),
                s(22),
            );
            SelectObject(dc, p);
            SelectObject(dc, b);
        }
        if !brush.is_null() {
            DeleteObject(brush as _);
        }
        if !pen.is_null() {
            DeleteObject(pen as _);
        }
        let knob = CreateSolidBrush(if enabled { 0x202020 } else { 0xbababa });
        if !knob.is_null() {
            let old = SelectObject(dc, knob as _);
            let pen = SelectObject(dc, GetStockObject(NULL_PEN));
            let x = if enabled {
                switch.right - s(19)
            } else {
                switch.left + s(3)
            };
            Ellipse(dc, x, switch.top + s(3), x + s(16), switch.bottom - s(3));
            SelectObject(dc, pen);
            SelectObject(dc, old);
            DeleteObject(knob as _);
        }
    } else {
        if primary && !disabled && !high_contrast() {
            let inner = RECT {
                left: r.left + 1,
                top: r.top + 1,
                right: r.right - 1,
                bottom: r.bottom - 1,
            };
            let region = CreateRoundRectRgn(
                inner.left,
                inner.top,
                inner.right,
                inner.bottom,
                s(16),
                s(16),
            );
            let save = SaveDC(dc);
            if !region.is_null() {
                ExtSelectClipRgn(dc, region, RGN_AND);
                gradient(dc, inner, 239, 210);
                DeleteObject(region as _);
            }
            RestoreDC(dc, save);
        }
        raw_text(
            dc,
            font,
            RECT {
                left: r.left + s(8),
                top: r.top,
                right: r.right - s(8),
                bottom: r.bottom,
            },
            &label,
            if disabled {
                0x7c7c7c
            } else if high_contrast() {
                GetSysColor(COLOR_BTNTEXT)
            } else if primary {
                0x151515
            } else {
                TEXT
            },
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
        );
    }
    if item.itemState & ODS_FOCUS != 0 {
        stroke(
            dc,
            RECT {
                left: r.left + 2,
                top: r.top + 2,
                right: r.right - 2,
                bottom: r.bottom - 2,
            },
            s(8),
            0xcfcfcf,
        );
    }
}
