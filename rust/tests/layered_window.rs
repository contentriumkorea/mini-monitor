use mini_monitor_native::layered_window::rgba_to_bgra;

#[test]
fn premultiplied_pixels_keep_opaque_text_and_transparent_background() {
    let rgba = [255, 255, 255, 255, 100, 50, 200, 0, 200, 100, 50, 128];
    let result = rgba_to_bgra(3, 1, &rgba).unwrap();
    assert_eq!(&result[0..4], &[255, 255, 255, 255]);
    assert_eq!(&result[4..8], &[0, 0, 0, 0]);
    assert_eq!(&result[8..12], &[25, 50, 100, 128]);
    assert!(rgba_to_bgra(3, 2, &rgba).is_err());
    assert!(rgba_to_bgra(0, 2, &[]).is_err());
}

#[cfg(windows)]
#[test]
fn repeated_hidden_presentations_release_gdi_resources() {
    use mini_monitor_native::{layered_window::present, taskbar_window::TaskbarWindow};
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetGuiResources};
    let window = TaskbarWindow::new_hidden().unwrap();
    let pixels = rgba_to_bgra(8, 8, &[255; 8 * 8 * 4]).unwrap();
    present(&window, -30000, -30000, 8, 8, &pixels).unwrap();
    let before = unsafe { GetGuiResources(GetCurrentProcess(), 0) };
    for _ in 0..40 {
        present(&window, -30000, -30000, 8, 8, &pixels).unwrap();
    }
    let after = unsafe { GetGuiResources(GetCurrentProcess(), 0) };
    assert!(after <= before + 1, "GDI resource count increased");
}
