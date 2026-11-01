//! Small, explicit command vocabulary for the notification-area menu.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TrayAction {
    OpenSettings,
    ToggleOverlay,
    ReconnectDisplay,
    Exit,
}
pub fn menu_action(id: u32) -> Option<TrayAction> {
    match id {
        1 => Some(TrayAction::OpenSettings),
        2 => Some(TrayAction::ToggleOverlay),
        3 => Some(TrayAction::ReconnectDisplay),
        4 => Some(TrayAction::Exit),
        _ => None,
    }
}

#[cfg(windows)]
mod native;
#[cfg(windows)]
pub use native::NativeTray;
