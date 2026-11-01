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

#[cfg(windows)]
#[test]
fn taskbar_mouse_input_does_not_activate_window() {
    use mini_monitor_native::taskbar_window::TaskbarWindow;
    use windows_sys::Win32::UI::WindowsAndMessaging::{SendMessageW, WM_MOUSEACTIVATE};
    let window = TaskbarWindow::new_hidden().unwrap();
    let result = unsafe { SendMessageW(window.handle(), WM_MOUSEACTIVATE, 0, 0) };
    assert_eq!(result, 3); // MA_NOACTIVATE: preserve click without stealing focus
}

#[cfg(windows)]
#[test]
fn cursor_message_replaces_wait_cursor_with_arrow_for_client_area() {
    use mini_monitor_native::taskbar_window::TaskbarWindow;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetCursor, LoadCursorW, SendMessageW, SetCursor, IDC_ARROW, IDC_WAIT, WM_MOUSEMOVE,
        WM_SETCURSOR,
    };
    let window = TaskbarWindow::new_hidden().unwrap();
    let arrow = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_ARROW) };
    let busy = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_WAIT) };
    assert!(!arrow.is_null() && !busy.is_null());
    let previous = unsafe { SetCursor(busy) };
    let handled = unsafe {
        SendMessageW(
            window.handle(),
            WM_SETCURSOR,
            window.handle() as usize,
            ((WM_MOUSEMOVE as isize) << 16) | 1,
        )
    };
    let resulting = unsafe { GetCursor() };
    unsafe {
        SetCursor(previous);
    }
    assert_eq!(handled, 1);
    assert_eq!(resulting, arrow);
}
