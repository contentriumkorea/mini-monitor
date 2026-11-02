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

/// # Safety
/// `dc` must be a valid paint context on the calling thread.
pub unsafe fn rounded_surface(
    dc: HDC,
    r: RECT,
    radius: i32,
    shades: Option<(u8, u8)>,
    border: Option<u32>,
) -> bool {
    use windows_sys::Win32::Graphics::GdiPlus::*;
    // One bounded process-wide GDI+ session; shapes themselves own no retained resources.
    static SESSION: std::sync::OnceLock<Option<usize>> = std::sync::OnceLock::new();
    if SESSION
        .get_or_init(|| {
            let mut token = 0;
            let input = GdiplusStartupInput {
                GdiplusVersion: 1,
                DebugEventCallback: 0,
                SuppressBackgroundThread: 0,
                SuppressExternalCodecs: 1,
            };
            (GdiplusStartup(&mut token, &input, std::ptr::null_mut()) == 0).then_some(token)
        })
        .is_none()
    {
        return false;
    }
    struct Shape {
        graphics: *mut GpGraphics,
        path: *mut GpPath,
        brush: *mut GpBrush,
        pen: *mut GpPen,
    }
    impl Drop for Shape {
        fn drop(&mut self) {
            unsafe {
                if !self.pen.is_null() {
                    GdipDeletePen(self.pen);
                }
                if !self.brush.is_null() {
                    GdipDeleteBrush(self.brush);
                }
                if !self.path.is_null() {
                    GdipDeletePath(self.path);
                }
                if !self.graphics.is_null() {
                    GdipDeleteGraphics(self.graphics);
                }
            }
        }
    }
    let mut shape = Shape {
        graphics: std::ptr::null_mut(),
        path: std::ptr::null_mut(),
        brush: std::ptr::null_mut(),
        pen: std::ptr::null_mut(),
    };
    if GdipCreateFromHDC(dc, &mut shape.graphics) != 0
        || GdipCreatePath(FillModeWinding, &mut shape.path) != 0
    {
        return false;
    }
    if GdipSetPageUnit(shape.graphics, UnitPixel) != 0
        || GdipSetSmoothingMode(shape.graphics, SmoothingModeAntiAlias8x8) != 0
        || GdipSetPixelOffsetMode(shape.graphics, PixelOffsetModeHalf) != 0
    {
        return false;
    }
    let mut clip: RECT = std::mem::zeroed();
    GetClipBox(dc, &mut clip);
    if GdipSetClipRectI(
        shape.graphics,
        clip.left,
        clip.top,
        clip.right - clip.left,
        clip.bottom - clip.top,
        CombineModeIntersect,
    ) != 0
    {
        return false;
    }
    let x = r.left as f32 + 0.5;
    let y = r.top as f32 + 0.5;
    let w = (r.right - r.left - 1).max(1) as f32;
    let h = (r.bottom - r.top - 1).max(1) as f32;
    let d = (radius * 2).max(1) as f32;
    let d = d.min(w).min(h);
    for (ax, ay, start) in [
        (x, y, 180.0),
        (x + w - d, y, 270.0),
        (x + w - d, y + h - d, 0.0),
        (x, y + h - d, 90.0),
    ] {
        if GdipAddPathArc(shape.path, ax, ay, d, d, start, 90.0) != 0 {
            return false;
        }
    }
    if GdipClosePathFigure(shape.path) != 0 {
        return false;
    }
    if let Some((top, bottom)) = shades {
        let area = Rect {
            X: r.left,
            Y: r.top,
            Width: r.right - r.left,
            Height: r.bottom - r.top,
        };
        let mut brush = std::ptr::null_mut();
        if GdipCreateLineBrushFromRectI(
            &area,
            0xff000000 | (u32::from(top) * 0x010101),
            0xff000000 | (u32::from(bottom) * 0x010101),
            LinearGradientModeVertical,
            WrapModeTile,
            &mut brush,
        ) != 0
        {
            return false;
        }
        shape.brush = brush.cast();
        if GdipFillPath(shape.graphics, shape.brush, shape.path) != 0 {
            return false;
        }
    }
    if let Some(color) = border {
        // COLORREF is BGR; GDI+ expects ARGB.
        let argb = 0xff000000 | ((color & 255) << 16) | (color & 0xff00) | ((color >> 16) & 255);
        if GdipCreatePen1(argb, 1.0, UnitPixel, &mut shape.pen) != 0
            || GdipDrawPath(shape.graphics, shape.pen, shape.path) != 0
        {
            return false;
        }
    }
    true
}

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
    if hc {
        fill(dc, r, GetSysColor(COLOR_WINDOW));
    } else {
        let parent = GetParent(hwnd);
        if parent.is_null() {
            fill(dc, r, background);
        } else {
            // Ask the same parent paint path as native labels, rather than
            // stamping a flat rectangle over its gradient surface.
            let brush = SendMessageW(parent, WM_CTLCOLORBTN, dc as usize, hwnd as isize) as HBRUSH;
            if !brush.is_null() && !std::ptr::eq(brush, GetStockObject(NULL_BRUSH)) {
                FillRect(dc, &r, brush);
            }
        }
    }
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
        let footer_top = height - if compact { 32 } else { 48 };
        let top = if compact { 112 } else { 166 };
        let space = footer_top - 16 - top;
        let device_h = if compact {
            108
        } else {
            (space * 42 / 100).clamp(136, 164)
        };
        let account_h = if compact {
            90
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
    if !high_contrast() && rounded_surface(dc, r, radius, None, Some(color)) {
        return;
    }
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
    if rounded_surface(dc, r, radius, Some((43, 31)), Some(0x464646)) {
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
    if flags & DT_VCENTER != 0 {
        let shift = caption_ink_offset(dc, font, &value, r.bottom - r.top);
        r.top += shift;
        r.bottom += shift;
    }
    DrawTextW(dc, value.as_ptr(), value.len() as i32, &mut r, flags);
    SelectObject(dc, previous);
}

unsafe fn caption_ink_offset(dc: HDC, font: HFONT, value: &[u16], height: i32) -> i32 {
    use std::{cell::RefCell, collections::VecDeque};
    type Entry = ((usize, i32, i32, Vec<u16>), i32);
    thread_local! {static CACHE: RefCell<VecDeque<Entry>>=const {RefCell::new(VecDeque::new())};}
    let mut metrics: TEXTMETRICW = std::mem::zeroed();
    if GetTextMetricsW(dc, &mut metrics) == 0 {
        return 0;
    }
    let key = (font as usize, metrics.tmHeight, height, value.to_vec());
    if let Some(offset) =
        CACHE.with(|c| c.borrow().iter().find(|(k, _)| *k == key).map(|(_, v)| *v))
    {
        return offset;
    }
    let mut extent: windows_sys::Win32::Foundation::SIZE = std::mem::zeroed();
    if GetTextExtentPoint32W(dc, value.as_ptr(), value.len() as i32, &mut extent) == 0
        || extent.cx <= 0
        || extent.cx > 8192
        || extent.cy <= 0
        || extent.cy > 512
    {
        return 0;
    }
    let memory = CreateCompatibleDC(dc);
    if memory.is_null() {
        return 0;
    }
    let mut info: BITMAPINFO = std::mem::zeroed();
    info.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: extent.cx,
        biHeight: -extent.cy,
        biPlanes: 1,
        biBitCount: 32,
        ..std::mem::zeroed()
    };
    let mut bits = std::ptr::null_mut();
    let bitmap = CreateDIBSection(
        memory,
        &info,
        DIB_RGB_COLORS,
        &mut bits,
        std::ptr::null_mut(),
        0,
    );
    if bitmap.is_null() || bits.is_null() {
        if !bitmap.is_null() {
            DeleteObject(bitmap as _);
        }
        DeleteDC(memory);
        return 0;
    }
    let old_bitmap = SelectObject(memory, bitmap as _);
    let old_font = SelectObject(memory, font as _);
    let count = (extent.cx * extent.cy * 4) as usize;
    std::ptr::write_bytes(bits as *mut u8, 0, count);
    SetTextColor(memory, 0xffffff);
    SetBkMode(memory, TRANSPARENT as i32);
    let mut line = RECT {
        left: 0,
        top: 0,
        right: extent.cx,
        bottom: extent.cy,
    };
    DrawTextW(
        memory,
        value.as_ptr(),
        value.len() as i32,
        &mut line,
        DT_LEFT | DT_TOP | DT_SINGLELINE,
    );
    GdiFlush();
    let bytes = std::slice::from_raw_parts(bits as *const u8, count);
    let mut top = extent.cy;
    let mut bottom = -1;
    for y in 0..extent.cy {
        let row = &bytes[(y * extent.cx * 4) as usize..((y + 1) * extent.cx * 4) as usize];
        if row
            .as_chunks::<4>()
            .0
            .iter()
            .any(|p| p[..3].iter().any(|v| *v > 160))
        {
            top = top.min(y);
            bottom = y;
        }
    }
    SelectObject(memory, old_font);
    SelectObject(memory, old_bitmap);
    DeleteObject(bitmap as _);
    DeleteDC(memory);
    // Center visible ink, not the font cell's asymmetric ascent/descent padding.
    // At a half-pixel tie use the lower integer baseline rather than biasing up.
    let offset = if bottom >= top {
        (height - top - bottom) / 2 - (height - extent.cy) / 2
    } else {
        0
    };
    CACHE.with(|c| {
        let mut c = c.borrow_mut();
        if c.len() >= 64 {
            c.pop_front();
        }
        c.push_back((key, offset));
    });
    offset
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
        paint_panel(dc, r, s(if output.is_some() { 14 } else { 8 }));
        if pressed {
            let inset = RECT {
                left: r.left + 2,
                top: r.top + 2,
                right: r.right - 2,
                bottom: r.bottom - 2,
            };
            rounded_surface(dc, inset, s(7), Some((56, 56)), None);
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
        if !rounded_surface(
            dc,
            switch,
            s(11),
            Some(if enabled { (220, 220) } else { (80, 80) }),
            Some(if enabled { 0xf0f0f0 } else { 0x646464 }),
        ) {
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
        }
        let knob_x = if enabled {
            switch.right - s(19)
        } else {
            switch.left + s(3)
        };
        if !rounded_surface(
            dc,
            RECT {
                left: knob_x,
                top: switch.top + s(3),
                right: knob_x + s(16),
                bottom: switch.bottom - s(3),
            },
            s(8),
            Some(if enabled { (32, 32) } else { (186, 186) }),
            None,
        ) {
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
        }
    } else {
        if primary && !disabled && !high_contrast() {
            let inner = RECT {
                left: r.left + 1,
                top: r.top + 1,
                right: r.right - 1,
                bottom: r.bottom - 1,
            };
            if !rounded_surface(dc, inner, s(8), Some((239, 210)), None) {
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
