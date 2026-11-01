#![cfg(windows)]
use mini_monitor_native::{
    dashboard_data::dashboard_input, dashboard_layout::Orientation,
    overlay_controller::OverlayController,
};
use time::{Date, Month};

#[test]
fn hidden_overlay_renders_transparently_at_requested_scale_without_usb() {
    let input = dashboard_input(
        None,
        None,
        None,
        Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        None,
        "",
    );
    let mut overlay = OverlayController::new(-30000, -30000, 150, 0).unwrap();
    overlay
        .render(&input, Orientation::Landscape, false, 0)
        .unwrap();
    assert_eq!(overlay.bounds(), Some((-30000, -30000, 720, 480)));
    overlay
        .render(&input, Orientation::Portrait, false, 1)
        .unwrap();
    assert_eq!(overlay.bounds(), Some((-30000, -30000, 480, 720)));
    assert!(OverlayController::new(0, 0, 201, 0).is_err());
    assert!(OverlayController::new(i32::MAX, 0, 100, 0)
        .unwrap()
        .render(&input, Orientation::Landscape, false, 0)
        .is_err());
}

#[test]
fn identical_overlay_tick_does_not_redraw_and_changed_scale_does() {
    let input = dashboard_input(
        None,
        None,
        None,
        Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        None,
        "",
    );
    let mut overlay = OverlayController::new(-30000, -30000, 100, 0).unwrap();
    assert!(overlay
        .render(&input, Orientation::Landscape, false, 0)
        .unwrap());
    assert!(!overlay
        .render(&input, Orientation::Landscape, false, 0)
        .unwrap());
    overlay.set_options(150, 0).unwrap();
    assert!(overlay
        .render(&input, Orientation::Landscape, false, 0)
        .unwrap());
    assert!(!overlay
        .render(&input, Orientation::Landscape, false, 0)
        .unwrap());
}
