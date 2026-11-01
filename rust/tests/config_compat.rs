use mini_monitor_native::config::Settings;

#[test]
fn existing_configuration_round_trips_with_unknown_fields_intact() {
    let input = r#"{"device":{"brightness":25,"rotation":"landscape"},"overlay":{"taskbar_enabled":true,"taskbar_items":["cpu","api"]},"openai":{"enabled":true,"total_usd":"100.00","start_date":"2026-10-05"},"future":{"keep":42}}"#;
    let settings = Settings::parse(input).unwrap();
    let saved = settings.to_json().unwrap();
    let value: serde_json::Value = serde_json::from_str(&saved).unwrap();
    assert_eq!(value["future"]["keep"], 42);
    assert_eq!(value["openai"]["start_date"], "2026-10-05");
    assert_eq!(value["overlay"]["taskbar_items"][1], "api");
}

#[test]
fn unsafe_brightness_is_rejected_before_any_use() {
    for input in [
        r#"{"device":{"brightness":51}}"#,
        r#"{"device":{"brightness":0}}"#,
        r#"{"device":{"brightness":true}}"#,
    ] {
        assert!(Settings::parse(input).is_err());
    }
}
