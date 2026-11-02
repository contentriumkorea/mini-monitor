#![cfg(windows)]
use mini_monitor_native::settings_theme::{paint_panel, MainLayout};
use windows_sys::Win32::System::Threading::GetGuiResources;
use windows_sys::Win32::{Foundation::RECT, Graphics::Gdi::*};

#[test]
fn combo_palette_keeps_system_pairs_and_selection_readable() {
    use mini_monitor_native::settings_theme::combo_palette;
    let light_hc = Some([0xffffff, 0x000000, 0x000080, 0xffffff, 0x555555]);
    assert_eq!(combo_palette(light_hc, false, false), (0xffffff, 0));
    assert_eq!(combo_palette(light_hc, false, true), (0x000080, 0xffffff));
    assert_eq!(combo_palette(light_hc, true, false), (0xffffff, 0x555555));
}

#[test]
fn initial_settings_bounds_fit_real_work_areas_without_dpi_double_scaling() {
    use mini_monitor_native::settings_theme::initial_bounds;
    for (work, dpi) in [
        (
            RECT {
                left: 0,
                top: 0,
                right: 1920,
                bottom: 1040,
            },
            96,
        ),
        (
            RECT {
                left: 0,
                top: 0,
                right: 2560,
                bottom: 1400,
            },
            144,
        ),
        (
            RECT {
                left: 0,
                top: 0,
                right: 3840,
                bottom: 2120,
            },
            240,
        ),
        (
            RECT {
                left: -1920,
                top: 0,
                right: 0,
                bottom: 1040,
            },
            96,
        ),
    ] {
        let frame = initial_bounds(work, dpi);
        assert!(frame.left >= work.left + 12 && frame.top >= work.top + 12);
        assert!(frame.right <= work.right - 12 && frame.bottom <= work.bottom - 12);
        assert!(frame.right - frame.left <= 960 * dpi as i32 / 96);
    }
}

#[test]
fn grouped_layout_preserves_space_for_inputs_preview_and_footer() {
    for (w, h) in [(684, 480), (890, 454), (684, 581), (944, 641), (1280, 850)] {
        let layout = MainLayout::new(w, h);
        assert!(layout.device.bottom + 8 <= layout.account.top);
        assert!(layout.account.bottom + 8 <= layout.api.top);
        assert!(layout.api.bottom + 8 <= layout.footer_top);
        assert!(layout.preview.left > layout.device.right);
        assert!(layout.preview.right <= w - 16);
        assert!(layout.preview.bottom <= layout.footer_top - 8);
        assert!(
            layout.api.bottom - layout.api.top >= 44,
            "API controls must fit a short work area"
        );
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

#[test]
fn rounded_shapes_have_subpixel_coverage_instead_of_binary_stair_steps() {
    unsafe {
        let dc = CreateCompatibleDC(std::ptr::null_mut());
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = 48;
        info.bmiHeader.biHeight = -32;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        let mut bits = std::ptr::null_mut();
        let bmp = CreateDIBSection(
            dc,
            &info,
            DIB_RGB_COLORS,
            &mut bits,
            std::ptr::null_mut(),
            0,
        );
        let old = SelectObject(dc, bmp as _);
        let black = CreateSolidBrush(0);
        FillRect(
            dc,
            &RECT {
                left: 0,
                top: 0,
                right: 48,
                bottom: 32,
            },
            black,
        );
        DeleteObject(black as _);
        mini_monitor_native::settings_theme::rounded_surface(
            dc,
            RECT {
                left: 4,
                top: 4,
                right: 44,
                bottom: 28,
            },
            12,
            Some((255, 255)),
            None,
        );
        GdiFlush();
        let bytes = std::slice::from_raw_parts(bits as *const u8, 48 * 32 * 4);
        let soft = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[0] > 8 && p[0] < 247)
            .count();
        let mut vertical_difference = 0u32;
        for y in 0..16 {
            for x in 0..48 {
                vertical_difference +=
                    u32::from(bytes[(y * 48 + x) * 4].abs_diff(bytes[((31 - y) * 48 + x) * 4]));
            }
        }
        SelectObject(dc, old);
        DeleteObject(bmp as _);
        DeleteDC(dc);
        assert!(
            soft > 20,
            "curved edges need fractional coverage, found {soft}"
        );
        assert!(
            vertical_difference < 48 * 16 * 2,
            "lower rounded edge must be as complete as upper edge: {vertical_difference}"
        );
    }
}
