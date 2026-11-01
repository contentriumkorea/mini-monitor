use mini_monitor_native::codex_login::{LoginAction, LoginFlow};

#[test]
fn completed_notification_before_response_does_not_open_stale_browser() {
    let mut flow = LoginFlow::default();
    let generation = flow.begin().unwrap();
    assert!(matches!(flow.completed("id-1", true), LoginAction::None));
    assert!(matches!(
        flow.started(generation, "id-1", "https://auth.openai.com/login"),
        LoginAction::Refresh
    ));
    assert!(flow.begin().is_some());
}

#[test]
fn cancellation_invalidates_late_response_and_cancels_server_login() {
    let mut flow = LoginFlow::default();
    let generation = flow.begin().unwrap();
    assert!(matches!(flow.cancel(), LoginAction::None));
    assert!(matches!(
        flow.started(generation, "id-1", "https://auth.openai.com/login"),
        LoginAction::CancelServer(id) if id == "id-1"
    ));
}

#[test]
fn unsafe_url_is_never_opened_and_server_login_is_cancelled() {
    let mut flow = LoginFlow::default();
    let generation = flow.begin().unwrap();
    assert!(matches!(
        flow.started(generation, "id-2", "https://evil.invalid/"),
        LoginAction::CancelServer(id) if id == "id-2"
    ));
    assert!(flow.begin().is_some());
}

#[test]
fn only_matching_completion_updates_pending_login() {
    let mut flow = LoginFlow::default();
    let generation = flow.begin().unwrap();
    assert!(flow.begin().is_none());
    assert!(matches!(
        flow.started(generation, "id-3", "https://chatgpt.com/login"),
        LoginAction::OpenBrowser(_)
    ));
    assert!(matches!(
        flow.completed("different", true),
        LoginAction::None
    ));
    assert!(matches!(flow.completed("id-3", false), LoginAction::Failed));
    assert!(flow.begin().is_some());
}
