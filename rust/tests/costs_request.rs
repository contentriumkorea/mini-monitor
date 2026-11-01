use mini_monitor_native::costs_http::CostsHttpClient;

#[test]
fn request_is_https_official_endpoint_and_key_is_marked_sensitive() {
    let client = CostsHttpClient::new("synthetic-admin-key-only").unwrap();
    let request = client.build_request(1000, 2000, Some("page+next")).unwrap();
    assert_eq!(request.url().scheme(), "https");
    assert_eq!(request.url().host_str(), Some("api.openai.com"));
    assert_eq!(request.url().path(), "/v1/organization/costs");
    let query: Vec<_> = request.url().query_pairs().collect();
    assert!(query.iter().any(|(k, v)| k == "start_time" && v == "1000"));
    assert!(query.iter().any(|(k, v)| k == "end_time" && v == "2000"));
    assert!(query.iter().any(|(k, v)| k == "page" && v == "page+next"));
    assert!(request
        .headers()
        .get("authorization")
        .unwrap()
        .is_sensitive());
    assert!(!format!("{request:?}").contains("synthetic-admin-key-only"));
}

#[test]
fn invalid_credentials_and_query_do_not_produce_a_request() {
    assert!(CostsHttpClient::new("").is_err());
    assert!(CostsHttpClient::new("contains\nnewline").is_err());
    let client = CostsHttpClient::new("synthetic-admin-key-only").unwrap();
    assert!(client.build_request(2000, 1000, None).is_err());
    assert!(client.build_request(1000, 2000, Some("")).is_err());
}
