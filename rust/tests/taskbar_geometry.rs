use mini_monitor_native::taskbar_geometry::{
    covers_monitor, place_horizontal, taskbar_visible, Rect,
};

fn r(left: i32, top: i32, right: i32, bottom: i32) -> Rect {
    Rect {
        left,
        top,
        right,
        bottom,
    }
}

#[test]
fn position_is_clamped_inside_bar_and_outside_notification_area_at_any_scale() {
    let bar = r(-3840, 2100, 0, 2160);
    let tray = r(-500, 2100, 0, 2160);
    let placed = place_horizontal(bar, Some(tray), 640, -100, 8).unwrap();
    assert_eq!(placed.right, -508);
    assert!(placed.left >= bar.left && placed.right <= tray.left);
    let left = place_horizontal(bar, Some(tray), 640, -10000, 8).unwrap();
    assert_eq!(left.left, bar.left + 8);
    assert!(place_horizontal(bar, Some(tray), 3500, -100, 8).is_none());
}

#[test]
fn fullscreen_and_autohide_detection_use_the_target_monitor() {
    let monitor = r(0, 0, 1920, 1080);
    assert!(taskbar_visible(r(0, 1040, 1920, 1080), monitor));
    assert!(!taskbar_visible(r(0, 1079, 1920, 1119), monitor));
    assert!(covers_monitor(r(0, 0, 1920, 1080), monitor));
    assert!(!covers_monitor(r(1920, 0, 3840, 1080), monitor));
    assert!(!covers_monitor(r(0, 0, 1920, 1040), monitor));
}

#[test]
fn invalid_or_vertical_bars_are_not_misplaced() {
    assert!(place_horizontal(r(0, 0, 0, 20), None, 5, 0, 0).is_none());
    assert!(place_horizontal(r(0, 0, 40, 1080), None, 5, 0, 0).is_none());
    assert!(place_horizontal(r(0, 1000, 1920, 1080), None, 500, 1000, 4).is_none());
    assert!(!taskbar_visible(
        r(i32::MIN, 0, i32::MAX, 0),
        r(0, 0, 100, 100)
    ));
}
