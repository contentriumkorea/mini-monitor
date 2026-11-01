use mini_monitor_native::codex::trusted_login_url;

#[test]
fn login_url_accepts_only_official_https_hosts() {
    assert!(trusted_login_url(
        "https://auth.openai.com/authorize?example=1"
    ));
    assert!(trusted_login_url("https://chatgpt.com:443/auth"));
    for unsafe_url in [
        "http://auth.openai.com/authorize",
        "https://auth.openai.com.evil.invalid/",
        "https://auth.openai.com@evil.invalid/",
        "https://user@auth.openai.com/",
        "https://auth.openai.com:444/",
        "https://auth.openai.com/#fragment",
        "https://auth.openai.com/\nexample",
        "not a url",
    ] {
        assert!(!trusted_login_url(unsafe_url), "untrusted URL accepted");
    }
    assert!(!trusted_login_url(&format!(
        "https://auth.openai.com/{}",
        "x".repeat(8192)
    )));
}
