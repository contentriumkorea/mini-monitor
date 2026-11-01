use mini_monitor_native::codex_client::{AccountClient, AccountState, RpcTransport};
use mini_monitor_native::codex_login::{LoginAction, LoginFlow};
use serde_json::{json, Value};

struct MockTransport {
    replies: Vec<Value>,
    methods: Vec<String>,
}
impl RpcTransport for MockTransport {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        _params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        self.methods.push(method.to_owned());
        if self.replies.is_empty() {
            return Err("unavailable");
        }
        Ok(self.replies.remove(0))
    }
}

#[test]
fn signed_out_account_never_fabricates_limits() {
    let transport = MockTransport {
        replies: vec![json!({"account":null})],
        methods: vec![],
    };
    let mut client = AccountClient::new(transport);
    assert!(matches!(
        client.refresh(|_, _| {}).unwrap(),
        AccountState::SignedOut
    ));
    assert_eq!(client.into_transport().methods, ["account/read"]);
}

#[test]
fn account_refresh_reads_codex_remaining_and_exact_credit() {
    let transport = MockTransport {
        replies: vec![
            json!({"account":{"type":"chatgpt", "email":"test@example.invalid", "planType":"pro"}}),
            json!({"rateLimits":{"limitId":"codex", "primary":{"usedPercent":15,"windowDurationMins":10080}, "credits":{"hasCredits":true,"unlimited":false,"balance":"60518.43"}}}),
        ],
        methods: vec![],
    };
    let mut client = AccountClient::new(transport);
    let AccountState::Ready { limits, .. } = client.refresh(|_, _| {}).unwrap() else {
        panic!("expected account");
    };
    assert_eq!(limits.windows[0].remaining_percent, 85.0);
    assert!(
        matches!(limits.credit, mini_monitor_native::codex::Credit::Balance(value) if value == "60518.43")
    );
    assert_eq!(
        client.into_transport().methods,
        ["account/read", "account/rateLimits/read"]
    );
}

#[test]
fn wrong_account_type_or_failed_limits_are_errors_not_empty_usage() {
    let mut wrong = AccountClient::new(MockTransport {
        replies: vec![json!({"account":{"type":"apikey"}})],
        methods: vec![],
    });
    assert!(wrong.refresh(|_, _| {}).is_err());
    let mut failed = AccountClient::new(MockTransport {
        replies: vec![json!({"account":{"type":"chatgpt"}})],
        methods: vec![],
    });
    assert!(failed.refresh(|_, _| {}).is_err());
    let mut malformed = AccountClient::new(MockTransport {
        replies: vec![json!({})],
        methods: vec![],
    });
    assert!(malformed.refresh(|_, _| {}).is_err());
}

struct LoginTransport {
    early: bool,
    bad_url: bool,
}
impl RpcTransport for LoginTransport {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        _params: Value,
        notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        assert_eq!(method, "account/login/start");
        if self.early {
            notify(
                "account/login/completed",
                &json!({"loginId":"fake-id", "success":true}),
            );
        }
        Ok(
            json!({"loginId":"fake-id", "authUrl":if self.bad_url { "https://evil.invalid" } else { "https://auth.openai.com/login" }}),
        )
    }
}

#[test]
fn login_start_accounts_for_early_completion_and_rejects_untrusted_url() {
    let mut flow = LoginFlow::default();
    let mut client = AccountClient::new(LoginTransport {
        early: true,
        bad_url: false,
    });
    assert!(matches!(
        client.begin_login(&mut flow, |_, _| {}).unwrap(),
        LoginAction::Refresh
    ));
    let mut client = AccountClient::new(LoginTransport {
        early: false,
        bad_url: true,
    });
    assert!(
        matches!(client.begin_login(&mut flow, |_, _| {}).unwrap(), LoginAction::CancelServer(id) if id == "fake-id")
    );
    assert!(client.begin_login(&mut flow, |_, _| {}).is_err());
}

struct DelayedLoginTransport {
    sent_event: bool,
}
impl RpcTransport for DelayedLoginTransport {
    fn request(
        &mut self,
        _id: u64,
        _method: &str,
        _params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        Ok(json!({"loginId":"fake-id", "authUrl":"https://auth.openai.com/login"}))
    }
    fn poll_notification(&mut self) -> Result<Option<(String, Value)>, &'static str> {
        if self.sent_event {
            return Ok(None);
        }
        self.sent_event = true;
        Ok(Some((
            "account/login/completed".into(),
            json!({"loginId":"fake-id", "success":true}),
        )))
    }
}

#[test]
fn login_completion_arriving_while_idle_triggers_refresh() {
    let mut flow = LoginFlow::default();
    let mut client = AccountClient::new(DelayedLoginTransport { sent_event: false });
    assert!(matches!(
        client.begin_login(&mut flow, |_, _| {}).unwrap(),
        LoginAction::OpenBrowser(_)
    ));
    assert!(matches!(
        client.poll_login(&mut flow, |_, _| {}).unwrap(),
        LoginAction::Refresh
    ));
}

struct CancelTransport {
    methods: Vec<String>,
}
impl RpcTransport for CancelTransport {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        self.methods.push(method.into());
        if method == "account/login/start" {
            Ok(json!({"loginId":"fake-id", "authUrl":"https://auth.openai.com/login"}))
        } else {
            assert_eq!(params["loginId"], "fake-id");
            Ok(json!({}))
        }
    }
}

#[test]
fn cancelling_pending_login_notifies_server_once() {
    let mut flow = LoginFlow::default();
    let mut client = AccountClient::new(CancelTransport { methods: vec![] });
    assert!(matches!(
        client.begin_login(&mut flow, |_, _| {}).unwrap(),
        LoginAction::OpenBrowser(_)
    ));
    client.cancel_login(&mut flow, |_, _| {}).unwrap();
    client.cancel_login(&mut flow, |_, _| {}).unwrap();
    assert_eq!(
        client.into_transport().methods,
        ["account/login/start", "account/login/cancel"]
    );
}

struct RetryCancelTransport {
    failed: bool,
}
impl RpcTransport for RetryCancelTransport {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        _params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        if method == "account/login/start" {
            return Ok(json!({"loginId":"fake-id", "authUrl":"https://auth.openai.com/login"}));
        }
        if !self.failed {
            self.failed = true;
            Err("unavailable")
        } else {
            Ok(json!({}))
        }
    }
}

#[test]
fn failed_server_cancellation_can_be_retried_before_starting_another_login() {
    let mut flow = LoginFlow::default();
    let mut client = AccountClient::new(RetryCancelTransport { failed: false });
    assert!(matches!(
        client.begin_login(&mut flow, |_, _| {}).unwrap(),
        LoginAction::OpenBrowser(_)
    ));
    assert!(client.cancel_login(&mut flow, |_, _| {}).is_err());
    assert!(client.begin_login(&mut flow, |_, _| {}).is_err());
    client.cancel_login(&mut flow, |_, _| {}).unwrap();
    assert!(matches!(
        client.begin_login(&mut flow, |_, _| {}).unwrap(),
        LoginAction::OpenBrowser(_)
    ));
}

struct AccountChangeTransport;
impl RpcTransport for AccountChangeTransport {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        _params: Value,
        notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        if method == "account/read" {
            Ok(json!({"account":{"type":"chatgpt"}}))
        } else {
            notify("account/updated", &json!({"authMode":null}));
            Ok(
                json!({"rateLimits":{"limitId":"codex","primary":{"usedPercent":0,"windowDurationMins":10080}}}),
            )
        }
    }
}

#[test]
fn account_change_during_refresh_never_publishes_stale_usage() {
    let mut client = AccountClient::new(AccountChangeTransport);
    assert!(client.refresh(|_, _| {}).is_err());
}
