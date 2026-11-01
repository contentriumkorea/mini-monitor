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

pub struct MainLayout {
    pub device: RECT,
    pub account: RECT,
    pub api: RECT,
    pub preview: RECT,
    pub footer_top: i32,
}
impl MainLayout {
    pub fn new(width: i32, height: i32) -> Self {
        let left = ((width - 56) * 40 / 100).clamp(300, 400);
        let footer_top = height - 48;
        let top = 166;
        let space = footer_top - 16 - top;
        let device_h = (space * 42 / 100).clamp(136, 164);
        let account_h = (space * 38 / 100).clamp(126, 148);
        let device = RECT {
            left: 20,
            top,
            right: 20 + left,
            bottom: top + device_h,
        };
        let account = RECT {
            left: 20,
            top: device.bottom + 12,
            right: 20 + left,
            bottom: device.bottom + 12 + account_h,
        };
        let api = RECT {
            left: 20,
            top: account.bottom + 12,
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
    let pen = CreatePen(PS_SOLID, 1, color);
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
        SelectClipRgn(dc, region);
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
        let title = label.split(" · ").next().unwrap_or(&label);
        text(
            dc,
            font,
            RECT {
                left: s(16),
                top: s(12),
                right: r.right - s(65),
                bottom: s(33),
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
                top: s(37),
                right: r.right - s(60),
                bottom: r.bottom - s(10),
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
                SelectClipRgn(dc, region);
                gradient(dc, inner, 239, 210);
                DeleteObject(region as _);
            }
            RestoreDC(dc, save);
        }
        text(
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
