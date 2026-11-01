#![cfg(windows)]
use mini_monitor_native::settings_theme::{paint_panel, MainLayout};
use windows_sys::Win32::System::Threading::GetGuiResources;
use windows_sys::Win32::{Foundation::RECT, Graphics::Gdi::*};

#[test]
fn grouped_layout_preserves_space_for_inputs_preview_and_footer() {
    for (w, h) in [(684, 581), (944, 641), (1280, 850)] {
        let layout = MainLayout::new(w, h);
        assert!(layout.device.bottom + 8 <= layout.account.top);
        assert!(layout.account.bottom + 8 <= layout.api.top);
        assert!(layout.api.bottom + 8 <= layout.footer_top);
        assert!(layout.preview.left > layout.device.right);
        assert!(layout.preview.right <= w - 16);
        assert!(layout.preview.bottom <= layout.footer_top - 8);
    }
}

#[test]
fn smoke_panel_is_neutral_rounded_and_releases_its_gdi_objects() {
    unsafe {
        let dc = CreateCompatibleDC(std::ptr::null_mut());
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = 180;
        info.bmiHeader.biHeight = -80;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        let mut bits = std::ptr::null_mut();
        let bitmap = CreateDIBSection(
            dc,
            &info,
            DIB_RGB_COLORS,
            &mut bits,
            std::ptr::null_mut(),
            0,
        );
        assert!(!dc.is_null() && !bitmap.is_null());
        let old = SelectObject(dc, bitmap as _);
        let background = CreateSolidBrush(0x101010);
        FillRect(
            dc,
            &RECT {
                left: 0,
                top: 0,
                right: 180,
                bottom: 80,
            },
            background,
        );
        DeleteObject(background as _);
        paint_panel(
            dc,
            RECT {
                left: 10,
                top: 10,
                right: 170,
                bottom: 70,
            },
            12,
        );
        let before = GetGuiResources(
            windows_sys::Win32::System::Threading::GetCurrentProcess(),
            0,
        );
        for _ in 0..100 {
            paint_panel(
                dc,
                RECT {
                    left: 10,
                    top: 10,
                    right: 170,
                    bottom: 70,
                },
                12,
            );
        }
        let middle = GetPixel(dc, 90, 35);
        let corner = GetPixel(dc, 10, 10);
        assert_eq!(
            corner, 0x101010,
            "rounded corner must not be painted as a square"
        );
        assert_ne!(middle, corner);
        assert_eq!(middle & 255, (middle >> 8) & 255);
        assert_eq!(middle & 255, (middle >> 16) & 255);
        assert_eq!(
            GetGuiResources(
                windows_sys::Win32::System::Threading::GetCurrentProcess(),
                0
            ),
            before
        );
        SelectObject(dc, old);
        DeleteObject(bitmap as _);
        DeleteDC(dc);
    }
}
