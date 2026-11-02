#![cfg(windows)]
use mini_monitor_native::options_window::{Field, OptionsWindow};

#[test]
fn preference_actions_have_dark_rounded_corners_and_passwords_stay_masked() {
    use windows_sys::Win32::{Graphics::Gdi::*, UI::WindowsAndMessaging::*};
    let form = OptionsWindow::new(
        std::ptr::null_mut(),
        "Smoke form",
        vec![Field::Edit("API key", "synthetic secret".into(), true)],
    )
    .unwrap();
    unsafe {
        assert_ne!(
            GetWindowLongPtrW(GetDlgItem(form.handle(), 100), GWL_STYLE) & ES_PASSWORD as isize,
            0
        );
        let button = GetDlgItem(form.handle(), 1);
        let mut r = std::mem::zeroed();
        GetClientRect(button, &mut r);
        let dc = CreateCompatibleDC(std::ptr::null_mut());
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = r.right;
        info.bmiHeader.biHeight = -r.bottom;
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
        let previous = SelectObject(dc, bmp as _);
        SendMessageW(button, WM_PRINTCLIENT, dc as usize, PRF_CLIENT as isize);
        let corner = GetPixel(dc, 0, 0);
        let mut ink_top = r.bottom;
        let mut ink_bottom = 0;
        for y in 3..r.bottom - 3 {
            for x in 10..r.right - 10 {
                let c = GetPixel(dc, x, y);
                if c & 255 < 80 && (c >> 8) & 255 < 80 && (c >> 16) & 255 < 80 {
                    ink_top = ink_top.min(y);
                    ink_bottom = ink_bottom.max(y);
                }
            }
        }
        let ink_center = (ink_top + ink_bottom) as f64 / 2.0;
        let button_center = (r.bottom - 1) as f64 / 2.0;
        assert!(
            corner & 255 < 80,
            "action corners must blend with the dark form rather than a native white button"
        );
        SelectObject(dc, previous);
        DeleteObject(bmp as _);
        DeleteDC(dc);
        assert!(
            (ink_center - button_center).abs() <= 1.0,
            "visible caption ink must be centered, ink={ink_top}..{ink_bottom}, height={}",
            r.bottom
        );
    }
}

#[test]
fn checked_fields_are_neutral_without_losing_native_toggle_behavior() {
    use windows_sys::Win32::{Graphics::Gdi::*, UI::WindowsAndMessaging::*};
    let form = OptionsWindow::new(
        std::ptr::null_mut(),
        "Neutral checks",
        vec![Field::Check("CPU", true)],
    )
    .unwrap();
    unsafe {
        let checkbox = GetDlgItem(form.handle(), 100);
        let mut r = std::mem::zeroed();
        GetClientRect(checkbox, &mut r);
        let dc = CreateCompatibleDC(std::ptr::null_mut());
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = r.right;
        info.bmiHeader.biHeight = -r.bottom;
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
        let previous = SelectObject(dc, bmp as _);
        SendMessageW(checkbox, WM_PRINTCLIENT, dc as usize, PRF_CLIENT as isize);
        let mut blue = 0;
        for y in 0..r.bottom {
            for x in 0..r.right.min(16) {
                let c = GetPixel(dc, x, y);
                if ((c >> 16) & 255) > (c & 255) + 20 {
                    blue += 1
                }
            }
        }
        let interior = GetPixel(dc, 5, (r.bottom - 15) / 2 + 4) & 255;
        SelectObject(dc, previous);
        DeleteObject(bmp as _);
        DeleteDC(dc);
        assert_eq!(
            blue, 0,
            "standard blue checkmark must use the neutral settings palette"
        );
        assert!(
            interior < 120,
            "checked glyph interior must remain charcoal instead of a light native square"
        );
        SendMessageW(checkbox, BM_CLICK, 0, 0);
        SendMessageW(form.handle(), WM_COMMAND, 1, 0);
    }
    assert_eq!(form.take_apply().unwrap().unwrap(), vec!["false"]);
}

#[test]
fn escape_closes_the_modeless_form_without_applying() {
    use windows_sys::Win32::UI::{
        Input::KeyboardAndMouse::VK_ESCAPE,
        WindowsAndMessaging::{IsWindowVisible, MSG, WM_KEYDOWN},
    };
    let form = OptionsWindow::new(
        std::ptr::null_mut(),
        "Keyboard test",
        vec![Field::Check("CPU", true)],
    )
    .unwrap();
    unsafe {
        windows_sys::Win32::UI::WindowsAndMessaging::ShowWindow(
            form.handle(),
            windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNOACTIVATE,
        );
    }
    let mut message: MSG = unsafe { std::mem::zeroed() };
    message.hwnd = form.handle();
    message.message = WM_KEYDOWN;
    message.wParam = VK_ESCAPE as usize;
    assert!(form.translate_message(&mut message));
    assert_eq!(unsafe { IsWindowVisible(form.handle()) }, 0);
    assert!(form.take_apply().unwrap().is_none());
}
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetDlgItem, SendMessageW, BM_SETCHECK, WM_COMMAND,
};

#[test]
fn taskbar_choices_reflow_in_two_columns_and_fit_a_high_dpi_work_area() {
    use windows_sys::Win32::{
        Foundation::RECT,
        UI::WindowsAndMessaging::{GetWindowRect, WM_DPICHANGED},
    };
    let form = OptionsWindow::new(
        std::ptr::null_mut(),
        "DPI test",
        vec![
            Field::Check("CPU", true),
            Field::Check("RAM", true),
            Field::Check("GPU", true),
            Field::Check("VRAM", true),
            Field::Check("Codex", true),
            Field::Check("API", false),
            Field::Choice("Style", vec!["Text", "Icon"], 0),
            Field::Slider("Position", 0, 100, 50),
        ],
    )
    .unwrap();
    let suggested = RECT {
        left: 0,
        top: 0,
        right: 1000,
        bottom: 900,
    };
    unsafe {
        SendMessageW(
            form.handle(),
            WM_DPICHANGED,
            192 | (192 << 16),
            &suggested as *const RECT as isize,
        );
    }
    let bounds = |id| {
        let mut r: RECT = unsafe { std::mem::zeroed() };
        unsafe {
            GetWindowRect(GetDlgItem(form.handle(), id), &mut r);
        }
        r
    };
    assert_eq!(bounds(100).top, bounds(101).top);
    assert!(bounds(101).left > bounds(100).left);
    assert!(bounds(107).bottom < bounds(1).top);
}
#[test]
fn modeless_form_reads_checked_choices_and_edits_only_on_apply() {
    let form = OptionsWindow::new(
        std::ptr::null_mut(),
        "Test",
        vec![
            Field::Check("CPU", true),
            Field::Edit("Total", "100".into(), false),
            Field::Choice("Style", vec!["Text", "Icon"], 1),
            Field::Slider("Opacity", 0, 100, 85),
        ],
    )
    .unwrap();
    assert!(form.take_apply().unwrap().is_none());
    unsafe {
        SendMessageW(GetDlgItem(form.handle(), 100), BM_SETCHECK, 0, 0);
        SendMessageW(form.handle(), WM_COMMAND, 1, 0);
    }
    assert_eq!(
        form.take_apply().unwrap().unwrap(),
        vec!["false", "100", "1", "85"]
    );
    assert!(form.take_apply().unwrap().is_none());
}
