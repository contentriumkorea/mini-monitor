#![cfg(windows)]
#[test]
fn native_frame_exports_a_real_png_with_original_dimensions() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("preview.png");
    let frame = mini_monitor_native::dashboard_render::DashboardFrame {
        width: 2,
        height: 3,
        pixels: vec![255; 24],
        detail_pages: vec![],
    };
    mini_monitor_native::png_output::save(&frame, &path).unwrap();
    let bytes = std::fs::read(path).unwrap();
    assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n");
    assert_eq!(u32::from_be_bytes(bytes[16..20].try_into().unwrap()), 2);
    assert_eq!(u32::from_be_bytes(bytes[20..24].try_into().unwrap()), 3);
}
