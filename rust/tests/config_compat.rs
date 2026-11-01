use mini_monitor_native::config::Settings;

#[test]
fn first_run_defaults_do_not_replace_missing_or_corrupt_user_settings() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("config.json");
    let defaults = Settings::load_or_default(&path).unwrap();
    assert!(!path.exists());
    assert!(defaults.to_json().unwrap().contains("overlay"));
    std::fs::write(&path, b"{not valid json}").unwrap();
    assert!(Settings::load_or_default(&path).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), b"{not valid json}");
}

#[test]
fn drag_positions_update_together_without_changing_other_preferences() {
    let settings = Settings::parse(r#"{"overlay":{"opacity":0.35,"scale_percent":125,"taskbar_style":"text","taskbar_items":["cpu"]}}"#).unwrap();
    let moved = settings
        .with_display_positions(Some((-120, 80)), Some((140, 190)))
        .unwrap();
    let value: serde_json::Value = serde_json::from_str(&moved.to_json().unwrap()).unwrap();
    assert_eq!(value["overlay"]["x"], -120);
    assert_eq!(value["overlay"]["y"], 80);
    assert_eq!(value["overlay"]["taskbar_x"], 140);
    assert_eq!(value["overlay"]["taskbar_y"], 190);
    assert_eq!(value["overlay"]["opacity"], 0.35);
    assert_eq!(value["overlay"]["scale_percent"], 125);
}

#[test]
fn display_settings_round_trip_without_losing_unrelated_fields() {
    let base =
        Settings::parse(r#"{"device":{"manual_port":"COM3"},"future":{"keep":42}}"#).unwrap();
    let changed = base.with_device_options("portrait_inverted", 37).unwrap();
    let changed = changed
        .with_overlay_options(true, 0.25, 125, Some((-200, 100)))
        .unwrap();
    let path = tempfile::tempdir().unwrap();
    let file = path.path().join("settings.json");
    changed.save(&file).unwrap();
    let value: serde_json::Value =
        serde_json::from_str(&Settings::load(&file).unwrap().to_json().unwrap()).unwrap();
    assert_eq!(value["device"]["rotation"], "portrait_inverted");
    assert_eq!(value["device"]["brightness"], 37);
    assert_eq!(value["device"]["manual_port"], "COM3");
    assert_eq!(value["overlay"]["enabled"], true);
    assert_eq!(value["overlay"]["opacity"], 0.25);
    assert_eq!(value["overlay"]["scale_percent"], 125);
    assert_eq!(value["overlay"]["x"], -200);
    assert_eq!(value["overlay"]["y"], 100);
    assert_eq!(value["future"]["keep"], 42);
    assert!(base.with_device_options("portrait", 51).is_err());
    assert!(base.with_overlay_options(true, 1.5, 100, None).is_err());
}

#[test]
fn budget_settings_are_validated_and_do_not_modify_other_fields() {
    let settings = Settings::parse(r#"{"openai":{"enabled":false},"future":{"keep":5}}"#).unwrap();
    let updated = settings
        .with_openai_budget(true, "150.25", "2026-01-31")
        .unwrap();
    let doc: serde_json::Value = serde_json::from_str(&updated.to_json().unwrap()).unwrap();
    assert_eq!(doc["openai"]["enabled"], true);
    assert_eq!(doc["openai"]["total_usd"], "150.25");
    assert_eq!(doc["openai"]["start_date"], "2026-01-31");
    assert_eq!(doc["future"]["keep"], 5);
    assert!(settings
        .with_openai_budget(true, "0", "2026-01-31")
        .is_err());
    assert!(settings
        .with_openai_budget(true, "150", "2026-02-30")
        .is_err());
}

#[test]
fn transactional_setting_changes_keep_old_state_when_disk_write_fails() {
    use mini_monitor_native::config::SettingsRepository;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("settings.json");
    let base = Settings::parse(r#"{"device":{"brightness":25},"future":{"keep":42}}"#).unwrap();
    let mut store = SettingsRepository::new(file.clone(), base);
    store
        .update(|s| s.with_device_options("portrait", 33))
        .unwrap();
    let saved: serde_json::Value =
        serde_json::from_str(&Settings::load(&file).unwrap().to_json().unwrap()).unwrap();
    assert_eq!(saved["device"]["brightness"], 33);
    assert_eq!(saved["future"]["keep"], 42);
    let bad_path = dir.path().join("missing").join("settings.json");
    let initial = Settings::parse(r#"{"device":{"brightness":25}}"#).unwrap();
    let mut failure = SettingsRepository::new(bad_path, initial);
    assert!(failure
        .update(|s| s.with_device_options("portrait", 33))
        .is_err());
    let memory: serde_json::Value =
        serde_json::from_str(&failure.settings().to_json().unwrap()).unwrap();
    assert_eq!(memory["device"]["brightness"], 25);
}

#[cfg(windows)]
#[test]
fn startup_configuration_preserves_existing_monitor_and_api_preferences() {
    use mini_monitor_native::{
        device::Orientation,
        taskbar_render::{MetricKind, TaskbarStyle},
    };
    let config = Settings::parse(r#"{"device":{"brightness":37,"rotation":"portrait_inverted"},"sensors":{"sample_interval_ms":1000},"overlay":{"enabled":true,"opacity":0.25,"scale_percent":125,"x":-200,"y":100,"taskbar_enabled":true,"taskbar_x":-400,"taskbar_y":900,"taskbar_style":"both","taskbar_items":["cpu","vram","codex"]},"openai":{"enabled":true,"total_usd":"150.25","start_date":"2026-01-31"}}"#).unwrap();
    let startup = config.startup_settings().unwrap();
    assert_eq!(startup.rotation, Orientation::ReversePortrait);
    assert_eq!(startup.brightness, 37);
    assert_eq!(startup.sensor_interval_ms, 1000);
    assert!(startup.overlay_enabled && startup.taskbar_enabled);
    assert_eq!(startup.overlay_position, Some((-200, 100)));
    assert_eq!(startup.taskbar_position, Some((-400, 900)));
    assert_eq!(startup.overlay_scale, 125);
    assert_eq!(startup.overlay_opacity, 64);
    assert!(startup.taskbar_style == TaskbarStyle::Both);
    assert!(startup.taskbar_items == vec![MetricKind::Cpu, MetricKind::Vram, MetricKind::Codex]);
    let budget = startup.budget.unwrap();
    assert_eq!(budget.total_cents, 15025);
    assert_eq!(budget.anchor.to_string(), "2026-01-31");
}

#[cfg(windows)]
#[test]
#[ignore = "reads existing app configuration without modifying or printing its contents"]
fn installed_configuration_is_accepted_by_rust_startup_mapping() {
    let root =
        std::path::PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join("AI-Mini-Monitor");
    let settings = Settings::load(&root.join("config.json")).unwrap();
    assert!(settings.startup_settings().is_ok());
}

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
