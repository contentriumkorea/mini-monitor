use mini_monitor_native::drag_state::DragState;

#[test]
fn drag_tracks_signed_screen_movement_and_cancels_when_capture_is_lost() {
    let mut drag = DragState::default();
    drag.begin(-1200);
    drag.motion(-1175, true, true);
    drag.motion(-1190, true, true);
    assert_eq!(drag.take_delta(), 10);
    drag.motion(-1100, false, true);
    drag.motion(-1000, true, true);
    assert_eq!(drag.take_delta(), 0);
}

#[test]
fn new_drag_and_extreme_coordinates_do_not_overflow() {
    let mut drag = DragState::default();
    drag.begin(i32::MIN);
    drag.motion(i32::MAX, true, true);
    assert_eq!(drag.take_delta(), i32::MAX);
    drag.end();
    drag.begin(10);
    drag.motion(5, true, true);
    assert_eq!(drag.take_delta(), -5);
}
