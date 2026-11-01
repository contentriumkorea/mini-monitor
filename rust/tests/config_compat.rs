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
fn legacy_names_are_migrated_without_losing_unrelated_settings() {
    let settings = Settings::parse(r#"{"device":{"rotation":"reverse_portrait"},"ai":{"provider":"codex_local"},"future":{"keep":42}}"#).unwrap();
    let value: serde_json::Value = serde_json::from_str(&settings.to_json().unwrap()).unwrap();
    assert_eq!(value["device"]["rotation"], "portrait_inverted");
    assert_eq!(value["ai"]["provider"], "codex_account");
    assert_eq!(value["future"]["keep"], 42);
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

#[test]
fn invalid_settings_are_rejected_instead_of_silently_used() {
    for input in [
        r#"{"device":{"rotation":"sideways"}}"#,
        r#"{"device":{"usb_fps":9}}"#,
        r#"{"sensors":{"sample_interval_ms":249}}"#,
        r#"{"overlay":{"opacity":1.1}}"#,
        r#"{"overlay":{"taskbar_items":[]}}"#,
        r#"{"overlay":{"x":5,"y":null}}"#,
        r#"{"ai":{"provider":"some-other-provider"}}"#,
        r#"{"openai":false}"#,
        r#"{"openai":{"enabled":true,"total_usd":"0","start_date":"2026-10-05"}}"#,
        r#"{"openai":{"enabled":true,"total_usd":"10","start_date":"not-a-date"}}"#,
        r#"{"overlay":{"scale_percent":50.5}}"#,
        r#"{"overlay":{"taskbar_style":"unknown"}}"#,
        r#"{"overlay":{"taskbar_enabled":"true"}}"#,
        r#"{"device":{"auto_detect":false,"manual_port":null}}"#,
        r#"{"openai":{"enabled":true,"total_usd":"10","start_date":"2025-02-29"}}"#,
        r#"{"openai":{"enabled":true,"total_usd":"10","start_date":"9999-01-01"}}"#,
    ] {
        assert!(Settings::parse(input).is_err(), "accepted {input}");
    }
}

#[test]
fn atomic_save_preserves_existing_file_on_invalid_data_and_round_trips() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.json");
    let first =
        Settings::parse(r#"{"device":{"brightness":25},"future":{"retain":true}}"#).unwrap();
    first.save(&path).unwrap();
    let second = Settings::load(&path).unwrap();
    assert!(second.to_json().unwrap().contains("retain"));
    assert!(Settings::parse(r#"{"device":{"brightness":500}}"#).is_err());
    assert!(Settings::load(&path)
        .unwrap()
        .to_json()
        .unwrap()
        .contains("25"));
    Settings::parse(r#"{"device":{"brightness":30}}"#)
        .unwrap()
        .save(&path)
        .unwrap();
    assert!(Settings::load(&path)
        .unwrap()
        .to_json()
        .unwrap()
        .contains("30"));
}

#[test]
fn legacy_budget_and_cli_path_validation_is_preserved() {
    for input in [
        r#"{"ai":{"codex_cli_path":"   "}}"#,
        r#"{"ai":{"codex_cli_path":"abc\u0000def"}}"#,
        r#"{"ai":{"codex_cli_path":123}}"#,
        r#"{"ai":{"daily_budget_usd":0}}"#,
        r#"{"ai":{"monthly_budget_usd":true}}"#,
        r#"{"ai":{"monthly_budget_usd":-1}}"#,
    ] {
        assert!(Settings::parse(input).is_err(), "accepted invalid setting");
    }
    assert!(Settings::parse(r#"{"ai":{"codex_cli_path":null,"daily_budget_usd":0.5}}"#).is_ok());
}

#[test]
fn taskbar_items_follow_existing_canonical_display_order() {
    let settings =
        Settings::parse(r#"{"overlay":{"taskbar_items":["codex","gpu","cpu"]}}"#).unwrap();
    let value: serde_json::Value = serde_json::from_str(&settings.to_json().unwrap()).unwrap();
    assert_eq!(
        value["overlay"]["taskbar_items"],
        serde_json::json!(["cpu", "gpu", "codex"])
    );
}

#[test]
fn missing_settings_receive_legacy_defaults_without_erasing_unknown_fields() {
    let settings =
        Settings::parse(r#"{"device":{"brightness":20},"future":{"keep":true}}"#).unwrap();
    let value: serde_json::Value = serde_json::from_str(&settings.to_json().unwrap()).unwrap();
    assert_eq!(value["device"]["brightness"], 20);
    assert_eq!(value["device"]["auto_detect"], true);
    assert_eq!(value["device"]["usb_fps"], 5.0);
    assert_eq!(value["sensors"]["sample_interval_ms"], 500);
    assert_eq!(
        value["overlay"]["taskbar_items"],
        serde_json::json!(["cpu", "ram", "gpu", "vram", "codex"])
    );
    assert_eq!(value["ai"]["usage_refresh_seconds"], 60);
    assert_eq!(value["openai"]["enabled"], false);
    assert_eq!(value["future"]["keep"], true);
}

#[test]
fn taskbar_changes_are_validated_and_preserve_unrelated_settings() {
    let settings = Settings::parse(r#"{"future":{"keep":7}}"#).unwrap();
    let settings = settings
        .with_taskbar_enabled(true)
        .unwrap()
        .with_taskbar_position(-200, 1000)
        .unwrap()
        .with_taskbar_options(&["gpu", "cpu"], "both")
        .unwrap();
    let json: serde_json::Value = serde_json::from_str(&settings.to_json().unwrap()).unwrap();
    assert_eq!(json["future"]["keep"], 7);
    assert_eq!(json["overlay"]["taskbar_enabled"], true);
    assert_eq!(json["overlay"]["taskbar_x"], -200);
    assert_eq!(
        json["overlay"]["taskbar_items"],
        serde_json::json!(["cpu", "gpu"])
    );
    assert!(settings
        .with_taskbar_options(&["cpu", "cpu"], "icon")
        .is_err());
    assert!(settings.with_taskbar_options(&["api"], "unknown").is_err());
}
