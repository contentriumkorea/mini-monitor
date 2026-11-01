#[cfg(windows)]
#[test]
fn native_text_has_transparent_background_and_visible_glyphs() {
    use mini_monitor_native::native_text::raster_text;
    let glyph = raster_text("100%", 18, [255, 255, 255]).unwrap();
    assert!(glyph.width > 0 && glyph.height > 0);
    assert!(glyph.pixels.as_chunks::<4>().0.iter().any(|p| p[3] > 0));
    assert!(glyph.pixels.as_chunks::<4>().0.iter().any(|p| p[3] == 0));
    assert!(glyph
        .pixels
        .as_chunks::<4>()
        .0
        .iter()
        .all(|p| p[..3].iter().all(|c| *c <= p[3])));
    assert!(raster_text("bad\0text", 18, [255, 255, 255]).is_err());
}

#[cfg(windows)]
#[test]
fn native_text_does_not_leak_graphics_handles() {
    use mini_monitor_native::native_text::raster_text;
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, GetGuiResources};
    raster_text("CPU 10%", 18, [255, 255, 255]).unwrap();
    let before = unsafe { GetGuiResources(GetCurrentProcess(), 0) };
    for _ in 0..30 {
        raster_text("GPU 100%", 18, [255, 255, 255]).unwrap();
    }
    let after = unsafe { GetGuiResources(GetCurrentProcess(), 0) };
    assert!(after <= before + 1);
}
