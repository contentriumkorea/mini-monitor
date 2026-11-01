use mini_monitor_native::codex::{parse_limits, Credit};

#[test]
fn selects_only_codex_bucket_and_reports_remaining_not_used() {
    let input = r#"{"rateLimitsByLimitId":{"codex_other":{"limitId":"codex_other","primary":{"usedPercent":0,"windowDurationMins":10080}},"codex":{"limitId":"codex","primary":{"usedPercent":15,"windowDurationMins":10080,"resetsAt":1791000000},"credits":{"hasCredits":true,"unlimited":false,"balance":"60518.43"}}}}"#;
    let limits = parse_limits(input).unwrap();
    assert_eq!(limits.windows.len(), 1);
    assert_eq!(limits.windows[0].remaining_percent, 85.0);
    assert_eq!(limits.credit, Credit::Balance("60518.43".into()));
}

#[test]
fn no_valid_measurement_does_not_become_one_hundred_percent() {
    let input = r#"{"rateLimits":{"limitId":"codex","primary":{"usedPercent":null,"windowDurationMins":10080},"credits":{"hasCredits":false,"unlimited":false,"balance":null}}}"#;
    let limits = parse_limits(input).unwrap();
    assert!(limits.windows.is_empty());
    assert_eq!(limits.credit, Credit::Unknown);
    assert!(parse_limits(r#"{"rateLimits":{"limitId":"other","primary":{"usedPercent":0,"windowDurationMins":10080}}}"#).is_err());
}
