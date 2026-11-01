#![cfg(windows)]
#[test]
#[ignore = "creates then removes a temporary notification icon on the current Explorer session"]
fn temporary_tray_icon_can_be_registered_and_removed() {
    let icon = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/app-icon.ico");
    let tray = mini_monitor_native::tray::NativeTray::new(&icon).unwrap();
    assert!(tray.take_action().is_none());
    unsafe {
        windows_sys::Win32::UI::WindowsAndMessaging::SendMessageW(
            tray.handle(),
            windows_sys::Win32::UI::WindowsAndMessaging::WM_APP + 42,
            1,
            windows_sys::Win32::UI::WindowsAndMessaging::WM_LBUTTONDBLCLK as isize,
        );
    }
    assert_eq!(
        tray.take_action(),
        Some(mini_monitor_native::tray::TrayAction::OpenSettings)
    );
    unsafe {
        windows_sys::Win32::UI::WindowsAndMessaging::SendMessageW(
            tray.handle(),
            windows_sys::Win32::UI::WindowsAndMessaging::WM_APP + 42,
            2,
            windows_sys::Win32::UI::WindowsAndMessaging::WM_LBUTTONDBLCLK as isize,
        );
    }
    assert!(tray.take_action().is_none());
    drop(tray);
}
