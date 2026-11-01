use mini_monitor_native::dirty::{calculate, Dirty, Rect};

fn rect(x: u32, y: u32, w: u32, h: u32) -> Rect {
    Rect {
        x,
        y,
        width: w,
        height: h,
    }
}
fn set_pixel(frame: &mut [u8], width: usize, x: usize, y: usize) {
    frame[(y * width + x) * 3] = 255;
}

#[test]
fn connected_pixels_and_padding_are_clipped_to_their_card() {
    let before = vec![0; 6 * 6 * 3];
    let mut after = before.clone();
    set_pixel(&mut after, 6, 2, 2);
    set_pixel(&mut after, 6, 3, 3);
    let result = calculate(6, 6, Some(&before), &after, &[rect(1, 1, 4, 4)], 1, 3).unwrap();
    assert_eq!(result, Dirty::Regions(vec![rect(1, 1, 4, 4)]));
}

#[test]
fn unchanged_separate_and_first_frames_are_distinguished() {
    let before = vec![0; 8 * 8 * 3];
    let mut after = before.clone();
    set_pixel(&mut after, 8, 0, 0);
    set_pixel(&mut after, 8, 7, 7);
    assert_eq!(
        calculate(8, 8, Some(&before), &before, &[rect(0, 0, 8, 8)], 0, 0).unwrap(),
        Dirty::Regions(vec![])
    );
    assert_eq!(
        calculate(8, 8, None, &after, &[rect(0, 0, 8, 8)], 0, 0).unwrap(),
        Dirty::Full
    );
    assert_eq!(
        calculate(8, 8, Some(&before), &after, &[rect(0, 0, 8, 8)], 0, 0).unwrap(),
        Dirty::Regions(vec![rect(0, 0, 1, 1), rect(7, 7, 1, 1)])
    );
}

#[test]
fn malformed_frames_and_out_of_bounds_cards_never_reach_a_writer() {
    let bytes = vec![0; 6 * 6 * 3];
    assert!(calculate(6, 6, None, &bytes[..5], &[rect(0, 0, 6, 6)], 1, 3).is_err());
    assert!(calculate(6, 6, Some(&bytes), &bytes, &[rect(5, 5, 2, 2)], 1, 3).is_err());
    assert!(calculate(
        6,
        6,
        Some(&bytes),
        &bytes,
        &[rect(0, 0, 4, 4), rect(3, 3, 3, 3)],
        1,
        3
    )
    .is_err());
}

#[test]
fn pathological_fragmentation_falls_back_to_full_frame() {
    let before = vec![0; 100 * 100 * 3];
    let mut after = before.clone();
    for y in (0..100).step_by(3) {
        for x in (0..100).step_by(3) {
            set_pixel(&mut after, 100, x, y);
        }
    }
    assert_eq!(
        calculate(
            100,
            100,
            Some(&before),
            &after,
            &[rect(0, 0, 100, 100)],
            0,
            0
        )
        .unwrap(),
        Dirty::Full
    );
}
