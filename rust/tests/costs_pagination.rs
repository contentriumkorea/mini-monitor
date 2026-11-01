use mini_monitor_native::budget::{collect_costs, USD_SCALE};

#[test]
fn pages_are_accumulated_exactly_and_cursors_followed() {
    let mut cursors = Vec::new();
    let amount = collect_costs(1000, 2000, |cursor| {
        cursors.push(cursor.map(str::to_owned));
        let page = if cursor.is_none() { r#"{"object":"page","data":[{"object":"bucket","start_time":1000,"end_time":2000,"results":[{"object":"organization.costs.result","amount":{"value":0.1,"currency":"usd"}}]}],"has_more":true,"next_page":"second"}"# }
            else { r#"{"object":"page","data":[{"object":"bucket","start_time":1000,"end_time":2000,"results":[{"object":"organization.costs.result","amount":{"value":0.2,"currency":"usd"}}]}],"has_more":false}"# };
        Ok(page.to_string())
    }, || false).unwrap();
    assert_eq!(amount, 3 * (USD_SCALE / 10));
    assert_eq!(cursors, vec![None, Some("second".to_owned())]);
}

#[test]
fn repeated_cursor_cancellation_and_failed_fetch_are_not_zero() {
    let looping = r#"{"object":"page","data":[],"has_more":true,"next_page":"same"}"#;
    assert!(collect_costs(1000, 2000, |_| Ok(looping.to_owned()), || false).is_err());
    assert!(collect_costs(1000, 2000, |_| Err("failed"), || false).is_err());
    let mut calls = 0;
    assert!(collect_costs(
        1000,
        2000,
        |_| {
            calls += 1;
            Ok(looping.to_owned())
        },
        || true
    )
    .is_err());
    assert_eq!(calls, 0);
}

#[test]
fn authorization_and_rate_limit_errors_are_actionable_without_exposing_other_errors() {
    assert_eq!(
        collect_costs(1000, 2000, |_| Err("unauthorized"), || false),
        Err("unauthorized")
    );
    assert_eq!(
        collect_costs(1000, 2000, |_| Err("forbidden"), || false),
        Err("forbidden")
    );
    assert_eq!(
        collect_costs(1000, 2000, |_| Err("rate limited"), || false),
        Err("rate limited")
    );
    assert_eq!(
        collect_costs(
            1000,
            2000,
            |_| Err("private-response-must-not-escape"),
            || false
        ),
        Err("fetch failed")
    );
}
