use mini_monitor_native::codex_runtime::{is_sensitive_environment_key, verify_runtime};
use std::io::Write;

#[test]
fn runtime_requires_matching_pinned_digest_and_regular_file() {
    let mut file = tempfile::NamedTempFile::new().unwrap();
    file.write_all(b"abc").unwrap();
    let digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    assert!(verify_runtime(file.path(), digest).is_ok());
    assert!(verify_runtime(file.path(), &"0".repeat(64)).is_err());
    assert!(verify_runtime(file.path(), "bad digest").is_err());
    let dir = tempfile::tempdir().unwrap();
    assert!(verify_runtime(dir.path(), digest).is_err());
    assert!(verify_runtime(&dir.path().join("missing"), digest).is_err());
}

#[test]
fn process_environment_removes_inherited_credentials_case_insensitively() {
    for key in [
        "openai_api_key",
        "CODEX_ACCESS_TOKEN",
        "ChatGpt_Auth",
        "OPENAI_SECRET",
    ] {
        assert!(is_sensitive_environment_key(key));
    }
    for key in ["PATH", "SystemRoot", "OPENAI_PROJECT", "CODEX_HOME"] {
        assert!(!is_sensitive_environment_key(key));
    }
}
