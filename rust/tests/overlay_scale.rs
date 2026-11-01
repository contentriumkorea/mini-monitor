use mini_monitor_native::overlay_scale::scale_premultiplied_bgra;

#[test]
fn nearest_scaling_preserves_transparent_background_and_opaque_text() {
    let pixels = [0, 0, 0, 0, 60, 90, 120, 255, 1, 1, 1, 1, 8, 12, 16, 128];
    let (width, height, out) = scale_premultiplied_bgra(2, 2, &pixels, 200).unwrap();
    assert_eq!((width, height), (4, 4));
    for y in 0..4 {
        for x in 0..4 {
            let expected = 4 * ((y / 2) * 2 + x / 2);
            let actual = 4 * (y * 4 + x);
            assert_eq!(&out[actual..actual + 4], &pixels[expected..expected + 4]);
        }
    }
}

#[test]
fn malformed_or_excessive_images_are_refused_before_allocating() {
    assert!(scale_premultiplied_bgra(1, 1, &[0; 3], 100).is_err());
    assert!(scale_premultiplied_bgra(1, 1, &[0; 4], 49).is_err());
    assert!(scale_premultiplied_bgra(1, 1, &[0; 4], 201).is_err());
    assert!(scale_premultiplied_bgra(u32::MAX, u32::MAX, &[], 300).is_err());
    assert!(scale_premultiplied_bgra(1, 1, &[3, 0, 0, 1], 100).is_err());
}
