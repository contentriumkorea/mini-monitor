#![cfg(windows)]
use mini_monitor_native::overlay_window::OverlayWindow;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetClassLongPtrW, IsWindowVisible, LoadCursorW, GCLP_HCURSOR, IDC_ARROW,
};

#[test]
fn hidden_overlay_has_arrow_cursor_and_accepts_transparent_frame() {
    let window = OverlayWindow::new_hidden().unwrap();
    assert_eq!(unsafe { IsWindowVisible(window.handle()) }, 0);
    let arrow = unsafe { LoadCursorW(std::ptr::null_mut(), IDC_ARROW) };
    assert_eq!(
        unsafe { GetClassLongPtrW(window.handle(), GCLP_HCURSOR) } as isize,
        arrow as isize
    );
    window
        .present(
            -30000,
            -30000,
            2,
            2,
            &[0, 0, 0, 0, 255, 255, 255, 255, 0, 0, 0, 0, 0, 0, 0, 0],
        )
        .unwrap();
    assert_eq!(unsafe { IsWindowVisible(window.handle()) }, 0);
    assert!(window.present(-30000, -30000, 2, 2, &[0; 3]).is_err());
}
