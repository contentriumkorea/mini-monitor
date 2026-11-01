use mini_monitor_native::tray::{menu_action, TrayAction};

#[test]
fn only_known_menu_commands_trigger_application_actions() {
    assert_eq!(menu_action(1), Some(TrayAction::OpenSettings));
    assert_eq!(menu_action(2), Some(TrayAction::ToggleOverlay));
    assert_eq!(menu_action(3), Some(TrayAction::ReconnectDisplay));
    assert_eq!(menu_action(4), Some(TrayAction::Exit));
    assert_eq!(menu_action(0), None);
    assert_eq!(menu_action(5), None);
    assert_eq!(menu_action(u32::MAX), None);
}
