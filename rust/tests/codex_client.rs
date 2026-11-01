use mini_monitor_native::codex_client::{AccountClient, AccountState, RpcTransport};
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
