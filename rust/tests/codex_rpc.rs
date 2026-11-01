use mini_monitor_native::codex_rpc::read_frame;
use mini_monitor_native::codex_rpc::{encode_request, parse_message, RpcMessage};
use std::io::{BufReader, Cursor};

#[test]
fn frame_reader_bounds_input_and_rejects_unterminated_or_oversized_messages() {
    let mut input = BufReader::new(Cursor::new(b"one\ntwo\n"));
    assert_eq!(read_frame(&mut input).unwrap().unwrap(), b"one\n");
    assert_eq!(read_frame(&mut input).unwrap().unwrap(), b"two\n");
    assert!(read_frame(&mut input).unwrap().is_none());
    assert!(read_frame(&mut BufReader::new(Cursor::new(b"partial"))).is_err());
    assert!(read_frame(&mut BufReader::new(Cursor::new(vec![
        b'x';
        1024 * 1024 + 1
    ])))
    .is_err());
}

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
