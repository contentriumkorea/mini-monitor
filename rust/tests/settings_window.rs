#![cfg(windows)]
use mini_monitor_native::settings_window::{SettingsAction, SettingsWindow};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetDlgItem, IsWindowVisible, SendMessageW, BM_CLICK, WM_CLOSE,
};

#[test]
fn startup_checkbox_changes_emit_an_explicit_intent() {
    let window = SettingsWindow::new_hidden().unwrap();
    window.set_autostart(false).unwrap();
    unsafe {
        SendMessageW(GetDlgItem(window.handle(), 204), BM_CLICK, 0, 0);
    }
    assert!(window.autostart_checked().unwrap());
    assert_eq!(window.take_action(), Some(SettingsAction::AutostartChanged));
}

#[test]
fn hidden_settings_buttons_emit_intent_and_close_does_not_quit() {
    let window = SettingsWindow::new_hidden().unwrap();
    assert_eq!(unsafe { IsWindowVisible(window.handle()) }, 0);
    let monitor = unsafe { GetDlgItem(window.handle(), 1) };
    assert!(!monitor.is_null());
    let style = unsafe {
        windows_sys::Win32::UI::WindowsAndMessaging::GetWindowLongPtrW(
            monitor,
            windows_sys::Win32::UI::WindowsAndMessaging::GWL_STYLE,
        )
    } as u32;
    assert_eq!(style & 0x0f, 0x0b, "dark owner-drawn button must be used");
    unsafe {
        SendMessageW(monitor, BM_CLICK, 0, 0);
    }
    assert_eq!(window.take_action(), Some(SettingsAction::ToggleMonitor));
    window.set_tray_available(true);
    unsafe {
        SendMessageW(window.handle(), WM_CLOSE, 0, 0);
    }
    assert_eq!(unsafe { IsWindowVisible(window.handle()) }, 0);
    assert!(window.take_action().is_none());
}

#[test]
fn closing_without_a_tray_requests_exit_instead_of_disappearing() {
    let window = SettingsWindow::new_hidden().unwrap();
    unsafe {
        SendMessageW(window.handle(), WM_CLOSE, 0, 0);
    }
    assert_eq!(window.take_action(), Some(SettingsAction::Quit));
}

#[test]
fn settings_preview_accepts_only_a_complete_opaque_dashboard_frame() {
    use mini_monitor_native::{
        dashboard_data::dashboard_input,
        dashboard_layout::Orientation,
        dashboard_render::{DashboardFrame, DashboardRenderer},
    };
    use time::{Date, Month};
    let window = SettingsWindow::new_hidden().unwrap();
    let input = dashboard_input(
        None,
        None,
        None,
        Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        None,
        "",
    );
    let frame = DashboardRenderer::new()
        .render(&input, Orientation::Landscape, false, None)
        .unwrap();
    window.set_preview(frame).unwrap();
    assert_eq!(window.preview_size(), Some((480, 320)));
    let invalid = DashboardFrame {
        width: 480,
        height: 320,
        pixels: vec![0; 4],
        detail_pages: vec![],
    };
    assert!(window.set_preview(invalid).is_err());
    assert_eq!(window.preview_size(), Some((480, 320)));
}

#[test]
fn output_controls_display_current_state_instead_of_ambiguous_toggle_labels() {
    use windows_sys::Win32::UI::WindowsAndMessaging::GetWindowTextW;
    let window = SettingsWindow::new_hidden().unwrap();
    window.set_output_state(true, false, true).unwrap();
    let label = |id| {
        let handle = unsafe { GetDlgItem(window.handle(), id) };
        let mut text = [0u16; 128];
        let n = unsafe { GetWindowTextW(handle, text.as_mut_ptr(), 128) };
        String::from_utf16(&text[..n as usize]).unwrap()
    };
    assert_eq!(label(1), "미니 모니터 · 켜짐");
    assert_eq!(label(2), "PC 오버레이 · 꺼짐");
    assert_eq!(label(3), "작업표시줄 바 · 켜짐");
}

#[test]
fn account_status_never_implies_a_failed_lookup_is_signed_in() {
    use mini_monitor_native::account_worker::AccountStatus;
    use windows_sys::Win32::UI::WindowsAndMessaging::GetWindowTextW;
    let window = SettingsWindow::new_hidden().unwrap();
    let label = || {
        let handle = unsafe { GetDlgItem(window.handle(), 106) };
        let mut text = [0u16; 128];
        let n = unsafe { GetWindowTextW(handle, text.as_mut_ptr(), 128) };
        String::from_utf16(&text[..n as usize]).unwrap()
    };
    window.set_account_status(AccountStatus::Ready).unwrap();
    assert_eq!(label(), "연결됨");
    window.set_account_status(AccountStatus::Error).unwrap();
    assert_eq!(label(), "확인 실패");
    window.set_account_status(AccountStatus::SignedOut).unwrap();
    assert_eq!(label(), "로그인 필요");
}

#[test]
fn monitor_status_distinguishes_a_requested_start_from_a_real_connection() {
    use mini_monitor_native::device_worker::WorkerStatus;
    use windows_sys::Win32::UI::WindowsAndMessaging::GetWindowTextW;
    let window = SettingsWindow::new_hidden().unwrap();
    let label = || {
        let mut text = [0u16; 128];
        let n = unsafe { GetWindowTextW(GetDlgItem(window.handle(), 102), text.as_mut_ptr(), 128) };
        String::from_utf16(&text[..n as usize]).unwrap()
    };
    window.set_device_status(true, None).unwrap();
    assert!(label().contains("연결 대기"));
    window
        .set_device_status(true, Some(WorkerStatus::Ready))
        .unwrap();
    assert!(label().contains("연결됨"));
    window
        .set_device_status(true, Some(WorkerStatus::Error))
        .unwrap();
    assert!(label().contains("연결 실패"));
    window.set_device_status(false, None).unwrap();
    assert!(label().contains("중지됨"));
}

#[test]
fn device_settings_round_trip_rotation_and_reject_unsafe_brightness() {
    use mini_monitor_native::device::Orientation;
    let window = SettingsWindow::new_hidden().unwrap();
    window
        .set_device_options(Orientation::ReversePortrait, 50)
        .unwrap();
    assert_eq!(
        window.device_options().unwrap(),
        (Orientation::ReversePortrait, 50)
    );
    assert!(window
        .set_device_options(Orientation::Landscape, 51)
        .is_err());
    assert_eq!(
        window.device_options().unwrap(),
        (Orientation::ReversePortrait, 50)
    );
}

#[test]
fn settings_use_consistent_nine_point_semibold_font_for_text_controls() {
    use windows_sys::Win32::{
        Graphics::Gdi::{GetObjectW, LOGFONTW},
        UI::{HiDpi::GetDpiForWindow, WindowsAndMessaging::WM_GETFONT},
    };
    let window = SettingsWindow::new_hidden().unwrap();
    let mut expected_font = 0isize;
    for id in [1, 4, 102, 201, 202] {
        let control = unsafe { GetDlgItem(window.handle(), id) };
        let font = unsafe { SendMessageW(control, WM_GETFONT, 0, 0) };
        assert_ne!(font, 0, "control {id} must use an explicit font");
        if expected_font == 0 {
            expected_font = font;
        }
        assert_eq!(
            font, expected_font,
            "all controls should share the settings font"
        );
    }
    let mut font: LOGFONTW = unsafe { std::mem::zeroed() };
    assert_eq!(
        unsafe {
            GetObjectW(
                expected_font as _,
                std::mem::size_of::<LOGFONTW>() as i32,
                (&mut font as *mut LOGFONTW).cast(),
            )
        },
        std::mem::size_of::<LOGFONTW>() as i32
    );
    let dpi = unsafe { GetDpiForWindow(window.handle()) } as i32;
    assert_eq!(font.lfHeight, -((9 * dpi + 36) / 72));
    assert_eq!(font.lfWeight, 600);
    let title = unsafe { SendMessageW(GetDlgItem(window.handle(), 101), WM_GETFONT, 0, 0) };
    let mut title_font: LOGFONTW = unsafe { std::mem::zeroed() };
    unsafe {
        GetObjectW(
            title as _,
            std::mem::size_of::<LOGFONTW>() as i32,
            (&mut title_font as *mut LOGFONTW).cast(),
        );
    }
    assert!(
        title_font.lfHeight.abs() > font.lfHeight.abs(),
        "window title must have a clear hierarchy above compact controls"
    );
}

#[test]
fn compact_output_cards_and_footer_never_overlap_settings() {
    use windows_sys::Win32::{
        Foundation::RECT,
        UI::WindowsAndMessaging::{
            GetWindowRect, SetWindowPos, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOZORDER,
        },
    };
    let window = SettingsWindow::new_hidden().unwrap();
    let bounds = |id| {
        let mut r: RECT = unsafe { std::mem::zeroed() };
        unsafe {
            GetWindowRect(GetDlgItem(window.handle(), id), &mut r);
        };
        r
    };
    for (w, h) in [(960, 680), (700, 620)] {
        unsafe {
            SetWindowPos(
                window.handle(),
                std::ptr::null_mut(),
                0,
                0,
                w,
                h,
                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
            );
        }
        assert!(
            bounds(1).bottom - bounds(1).top >= 60,
            "output should be an integrated compact card, not a plain action row"
        );
        assert!(
            bounds(9).bottom + 8 <= bounds(204).top,
            "API settings must not collide with startup/footer"
        );
        assert_eq!(
            bounds(10).top,
            bounds(11).top,
            "update and exit belong in one compact footer"
        );
        assert!(
            bounds(12).right <= bounds(13).left,
            "preview settings must fit side by side"
        );
    }
    unsafe {
        SetWindowPos(
            window.handle(),
            std::ptr::null_mut(),
            0,
            0,
            900,
            460,
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
        );
    }
    assert!(
        bounds(8).bottom + 8 <= bounds(9).top,
        "short screens must keep account and API actions separate"
    );
    assert!(bounds(9).bottom + 8 <= bounds(204).top);
}

#[test]
fn printing_transparent_labels_never_erases_the_preview_or_other_controls() {
    use windows_sys::Win32::{
        Foundation::HWND,
        Graphics::Gdi::*,
        UI::WindowsAndMessaging::{GetClientRect, ShowWindow, SW_SHOWNOACTIVATE},
    };
    #[link(name = "user32")]
    extern "system" {
        fn PrintWindow(hwnd: HWND, dc: HDC, flags: u32) -> i32;
    }
    let window = SettingsWindow::new_hidden().unwrap();
    window
        .set_preview(mini_monitor_native::dashboard_render::DashboardFrame {
            width: 480,
            height: 320,
            pixels: [0, 0, 255, 255].repeat(480 * 320),
            detail_pages: vec![],
        })
        .unwrap();
    unsafe {
        ShowWindow(window.handle(), SW_SHOWNOACTIVATE);
        UpdateWindow(window.handle());
        let mut r = std::mem::zeroed();
        GetClientRect(window.handle(), &mut r);
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
        let old = SelectObject(dc, bmp as _);
        // PW_CLIENTONLY requests a synchronous native paint; avoid DWM's optional
        // full-content cache for this newly shown, non-activated test window.
        assert_ne!(PrintWindow(window.handle(), dc, 1), 0);
        GdiFlush();
        let bytes =
            std::slice::from_raw_parts(bits as *const u8, (r.right * r.bottom * 4) as usize);
        let red = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[2] > 240 && p[1] < 10 && p[0] < 10)
            .count();
        let label = GetDlgItem(window.handle(), 105);
        let mut label_rect = std::mem::zeroed();
        windows_sys::Win32::UI::WindowsAndMessaging::GetWindowRect(label, &mut label_rect);
        let mut point = windows_sys::Win32::Foundation::POINT {
            x: label_rect.right - 3,
            y: label_rect.top + 2,
        };
        ScreenToClient(window.handle(), &mut point);
        let label_shade = bytes[((point.y * r.right + point.x) * 4) as usize];
        SelectObject(dc, old);
        DeleteObject(bmp as _);
        DeleteDC(dc);
        assert!(
            (30..50).contains(&label_shade),
            "label background must match its panel, not a black stripe: {label_shade}"
        );
        assert!(
            red > 1000,
            "label drawing must preserve the complete preview region, found {red} colored pixels"
        );
    }
}

#[test]
fn settings_window_is_created_per_monitor_dpi_aware() {
    use windows_sys::Win32::UI::HiDpi::{
        GetAwarenessFromDpiAwarenessContext, GetWindowDpiAwarenessContext,
    };
    let window = SettingsWindow::new_hidden().unwrap();
    let context = unsafe { GetWindowDpiAwarenessContext(window.handle()) };
    assert!(!context.is_null());
    assert_eq!(unsafe { GetAwarenessFromDpiAwarenessContext(context) }, 2);
}

#[test]
fn settings_recalculate_font_and_controls_when_dpi_changes() {
    use windows_sys::Win32::{
        Foundation::RECT,
        Graphics::Gdi::{GetObjectW, LOGFONTW},
        UI::WindowsAndMessaging::{GetWindowRect, WM_DPICHANGED, WM_GETFONT},
    };
    let window = SettingsWindow::new_hidden().unwrap();
    let control = unsafe { GetDlgItem(window.handle(), 1) };
    let mut before: RECT = unsafe { std::mem::zeroed() };
    unsafe {
        GetWindowRect(control, &mut before);
        let mut suggested: RECT = std::mem::zeroed();
        GetWindowRect(window.handle(), &mut suggested);
        suggested.right = suggested.left + (suggested.right - suggested.left) * 3 / 2;
        suggested.bottom = suggested.top + (suggested.bottom - suggested.top) * 3 / 2;
        SendMessageW(
            window.handle(),
            WM_DPICHANGED,
            (144usize << 16) | 144,
            (&suggested as *const RECT) as isize,
        );
    }
    let mut after: RECT = unsafe { std::mem::zeroed() };
    unsafe {
        GetWindowRect(control, &mut after);
    }
    assert!(after.bottom - after.top > before.bottom - before.top);
    let handle = unsafe { SendMessageW(control, WM_GETFONT, 0, 0) };
    let mut font: LOGFONTW = unsafe { std::mem::zeroed() };
    assert_ne!(
        unsafe {
            GetObjectW(
                handle as _,
                std::mem::size_of::<LOGFONTW>() as i32,
                (&mut font as *mut LOGFONTW).cast(),
            )
        },
        0
    );
    assert_eq!(font.lfHeight, -18);
}
