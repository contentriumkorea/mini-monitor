use mini_monitor_native::budget::{parse_costs_page, USD_SCALE};

#[test]
fn usd_costs_are_summed_exactly_without_guessing() {
    let page = r#"{"object":"page","data":[{"object":"bucket","start_time":1000,"end_time":2000,"results":[{"object":"organization.costs.result","amount":{"value":0.1,"currency":"usd"}},{"object":"organization.costs.result","amount":{"value":"0.2","currency":"USD"}}]}],"has_more":true,"next_page":"next"}"#;
    let result = parse_costs_page(page, 1000, 2000).unwrap();
    assert_eq!(result.amount_units, 3 * (USD_SCALE / 10));
    assert_eq!(result.next_page.as_deref(), Some("next"));
}

#[test]
fn malformed_non_usd_and_invalid_pagination_are_not_zero() {
    for page in [
        r#"{"object":"error","data":[],"has_more":false}"#,
        r#"{"object":"page","data":[{"object":"bucket","start_time":1000,"end_time":2000,"results":[{"object":"organization.costs.result","amount":{"value":1,"currency":"eur"}}]}],"has_more":false}"#,
        r#"{"object":"page","data":[],"has_more":true,"next_page":""}"#,
        r#"{"object":"page","data":[{"object":"bucket","start_time":1000,"end_time":2000,"results":[{"object":"organization.costs.result","amount":{"value":"NaN","currency":"usd"}}]}],"has_more":false}"#,
    ] {
        assert!(parse_costs_page(page, 1000, 2000).is_err());
    }
    let empty = r#"{"object":"page","data":[],"has_more":false,"next_page":null}"#;
    assert_eq!(parse_costs_page(empty, 1000, 2000).unwrap().amount_units, 0);
}
