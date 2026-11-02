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

#[cfg(windows)]
#[test]
fn simultaneous_dashboard_values_replay_exactly_without_a_screen_sized_transfer() {
    use mini_monitor_native::{
        dashboard_layout::{CardKind, Orientation as Layout},
        dashboard_render::{DashboardEntry, DashboardInput, DashboardRenderer},
        device::FrameDelta,
    };
    for orientation in [Layout::Landscape, Layout::Portrait] {
        let mut data = DashboardInput {
            port: "COM3".into(),
            entries: [
                CardKind::Cpu,
                CardKind::Ram,
                CardKind::Gpu,
                CardKind::Vram,
                CardKind::Codex,
                CardKind::Api,
            ]
            .into_iter()
            .map(|kind| DashboardEntry {
                kind,
                value: "19%".into(),
                details: vec!["UNCHANGED DETAILS".into()],
                gauge: Some(19.0),
            })
            .collect(),
        };
        let mut renderer = DashboardRenderer::new();
        let before = renderer.render(&data, orientation, true, None).unwrap();
        for entry in &mut data.entries {
            entry.value = "20%".into();
            entry.gauge = Some(20.0);
        }
        let after = renderer.render(&data, orientation, true, None).unwrap();
        let mut replay = rgb888_to_rgb565le(
            &before.to_opaque_rgb().unwrap(),
            before.width as usize,
            before.height as usize,
        )
        .unwrap();
        let want = rgb888_to_rgb565le(
            &after.to_opaque_rgb().unwrap(),
            after.width as usize,
            after.height as usize,
        )
        .unwrap();
        let first = bitmap_transfer(
            0,
            0,
            before.width,
            before.height,
            &replay,
            before.width,
            before.height,
        )
        .unwrap();
        let next = bitmap_transfer(
            0,
            0,
            after.width,
            after.height,
            &want,
            after.width,
            after.height,
        )
        .unwrap();
        let start = std::time::Instant::now();
        let regions = match next.delta_from(&first).unwrap() {
            FrameDelta::Region(r) => vec![r],
            FrameDelta::Regions(r) => r,
            _ => panic!("six changing values must remain small regional updates"),
        };
        let planning_us = start.elapsed().as_micros();
        let mut total = 0;
        for r in &regions {
            let mut bytes = Vec::new();
            r.write_to(&mut bytes, 3840).unwrap();
            total += bytes.len();
            let x = (u32::from(bytes[0]) << 2) | (u32::from(bytes[1]) >> 6);
            let y = ((u32::from(bytes[1]) & 63) << 4) | (u32::from(bytes[2]) >> 4);
            let right = ((u32::from(bytes[2]) & 15) << 6) | (u32::from(bytes[3]) >> 2);
            let bottom = ((u32::from(bytes[3]) & 3) << 8) | u32::from(bytes[4]);
            let row_bytes = ((right - x + 1) * 2) as usize;
            assert!(bottom < after.height && right < after.width);
            for row in 0..=bottom - y {
                let dest = (((y + row) * after.width + x) * 2) as usize;
                let src = 6 + row as usize * row_bytes;
                replay[dest..dest + row_bytes].copy_from_slice(&bytes[src..src + row_bytes]);
            }
        }
        assert_eq!(
            replay, want,
            "every changed pixel, including RGB565 high bytes, must arrive"
        );
        assert!(
            total < want.len() / 3,
            "small value changes must not sweep the screen: {total}"
        );
        println!(
            "usb_delta_fixture regions={} bytes={total} planning_us={planning_us}",
            regions.len()
        );
    }
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
