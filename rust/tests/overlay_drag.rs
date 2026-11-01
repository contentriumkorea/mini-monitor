use mini_monitor_native::overlay_drag::OverlayDrag;

#[test]
fn drag_moves_in_two_dimensions_and_stops_on_release_or_capture_loss() {
    let mut drag = OverlayDrag::default();
    drag.begin(100, 200);
    drag.motion(110, 180, true, true);
    assert_eq!(drag.take_delta(), (10, -20));
    drag.motion(115, 185, true, true);
    drag.end();
    assert_eq!(drag.take_delta(), (5, 5));
    assert!(drag.take_finished());
    drag.motion(200, 200, true, true);
    assert_eq!(drag.take_delta(), (0, 0));
    drag.begin(-20, -20);
    drag.motion(-10, -10, true, false);
    assert!(drag.take_finished());
    assert_eq!(drag.take_delta(), (0, 0));
}
