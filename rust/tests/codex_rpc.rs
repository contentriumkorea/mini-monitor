use mini_monitor_native::codex_rpc::{encode_request, parse_message, RpcMessage};

#[test]
fn request_is_newline_delimited_and_keeps_ids() {
    let bytes =
        encode_request(7, "account/read", serde_json::json!({"refreshToken":false})).unwrap();
    assert_eq!(bytes.last(), Some(&b'\n'));
    let value: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(value["id"], 7);
    assert_eq!(value["method"], "account/read");
    assert_eq!(value["params"]["refreshToken"], false);
}

#[test]
fn parses_bounded_responses_and_notifications_but_not_malformed_frames() {
    assert!(matches!(
        parse_message(br#"{"id":7,"result":{"ok":true}}"#).unwrap(),
        RpcMessage::Response { id: 7, .. }
    ));
    assert!(matches!(
        parse_message(br#"{"method":"account/updated","params":{}}"#).unwrap(),
        RpcMessage::Notification { .. }
    ));
    assert!(parse_message(br#"{"id":7,"result":{},"error":{}}"#).is_err());
    assert!(parse_message(br#"{"id":7}"#).is_err());
    assert!(parse_message(&vec![b' '; 1024 * 1024 + 1]).is_err());
}
