use mini_monitor_native::device::{
    bitmap_command, bitmap_transfer, brightness_command, identity_matches, orientation_command,
    rgb888_to_rgb565le, screen_on, Orientation,
};

#[test]
fn exact_usb_identity_is_required() {
    assert!(identity_matches(0x1a86, 0x5722, "USB35INCHIPSV2"));
    assert!(!identity_matches(0x1a86, 0x5721, "USB35INCHIPSV2"));
    assert!(!identity_matches(0x1a87, 0x5722, "USB35INCHIPSV2"));
    assert!(!identity_matches(0x1a86, 0x5722, "OTHER"));
}

#[test]
fn transfer_is_constructed_only_after_bounds_and_payload_are_verified() {
    assert!(bitmap_transfer(0, 0, 2, 2, &[0; 7], 480, 320).is_err());
    assert!(bitmap_transfer(479, 319, 2, 2, &[0; 8], 480, 320).is_err());
    let transfer = bitmap_transfer(0, 0, 2, 2, &[1; 8], 480, 320).unwrap();
    let mut writes = Vec::new();
    transfer.write_to(&mut writes, 3).unwrap();
    assert_eq!(&writes[0..6], &[0, 0, 0, 4, 1, 0xc5]);
    assert_eq!(&writes[6..], &[1; 8]);
    assert!(transfer.write_to(&mut Vec::new(), 0).is_err());
}

#[test]
fn rgb565_delta_sends_only_changed_pixels_and_rejects_wrong_display_size() {
    use mini_monitor_native::device::{bitmap_transfer, FrameDelta};
    let before = bitmap_transfer(0, 0, 4, 3, &[0; 24], 4, 3).unwrap();
    let unchanged = bitmap_transfer(0, 0, 4, 3, &[0; 24], 4, 3).unwrap();
    assert!(matches!(
        unchanged.delta_from(&before).unwrap(),
        FrameDelta::Unchanged
    ));
    let mut pixels = [0u8; 24];
    pixels[12] = 0x5a; // x=2, y=1 in a 4-wide RGB565 frame
    pixels[13] = 0xa5;
    let after = bitmap_transfer(0, 0, 4, 3, &pixels, 4, 3).unwrap();
    let FrameDelta::Region(region) = after.delta_from(&before).unwrap() else {
        panic!("expected one small region")
    };
    let mut bytes = Vec::new();
    region.write_to(&mut bytes, 16).unwrap();
    assert_eq!(bytes.len(), 8);
    assert_eq!(&bytes[6..], &[0x5a, 0xa5]);
    let wrong = bitmap_transfer(0, 0, 3, 4, &[0; 24], 3, 4).unwrap();
    assert!(after.delta_from(&wrong).is_err());
}

#[test]
fn audited_command_bytes_match_existing_upstream_vectors() {
    assert_eq!(screen_on(), [0, 0, 0, 0, 0, 0x6d]);
    assert_eq!(brightness_command(25).unwrap(), [0x2f, 0xc0, 0, 0, 0, 0x6e]);
    assert_eq!(brightness_command(1).unwrap(), [0x3f, 0, 0, 0, 0, 0x6e]);
    assert_eq!(brightness_command(50).unwrap(), [0x1f, 0xc0, 0, 0, 0, 0x6e]);
    assert!(brightness_command(0).is_err());
    assert!(brightness_command(51).is_err());
    let expected = [0, 0, 0, 0, 0, 0x79, 102, 1, 224, 1, 64, 0, 0, 0, 0, 0];
    assert_eq!(orientation_command(Orientation::Landscape), expected);
    assert_eq!(
        bitmap_command(0, 0, 480, 320, 480, 320).unwrap(),
        [0, 0, 7, 0x7d, 0x3f, 0xc5]
    );
    assert_eq!(
        bitmap_command(10, 20, 100, 200, 480, 320).unwrap(),
        [2, 0x81, 0x41, 0xb4, 0xdb, 0xc5]
    );
}

#[test]
fn frame_length_and_bounds_are_checked_before_creating_a_transfer() {
    assert!(bitmap_command(479, 319, 2, 1, 480, 320).is_err());
    assert!(bitmap_command(0, 0, 0, 1, 480, 320).is_err());
    assert!(bitmap_command(1024, 0, 1, 1, 1024, 1024).is_err());
    assert!(rgb888_to_rgb565le(&[255, 0], 1, 1).is_err());
    assert_eq!(
        rgb888_to_rgb565le(&[255, 0, 0, 0, 255, 0, 0, 0, 255], 3, 1).unwrap(),
        [0x00, 0xf8, 0xe0, 0x07, 0x1f, 0]
    );
}
