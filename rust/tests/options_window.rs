#![cfg(windows)]
use mini_monitor_native::options_window::{Field, OptionsWindow};

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
    form.show();
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
