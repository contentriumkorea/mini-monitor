#[cfg(windows)]
#[test]
fn taskbar_window_has_explicit_arrow_cursor() {
    use mini_monitor_native::taskbar_window::TaskbarWindow;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetClassLongPtrW, LoadCursorW, GCLP_HCURSOR, IDC_ARROW,
    };
    let window = TaskbarWindow::new_hidden().unwrap();
    let arrow = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_ARROW) };
    assert!(!arrow.is_null());
    let actual = unsafe { GetClassLongPtrW(window.handle(), GCLP_HCURSOR) };
    assert_eq!(actual as usize, arrow as usize);
}
