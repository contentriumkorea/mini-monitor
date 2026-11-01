//! Opt-in smoke test: only an empty temporary CODEX_HOME is ever used.
#[cfg(windows)]
#[test]
#[ignore = "requires the pinned bundled Codex runtime and takes time to hash it"]
fn pinned_runtime_initializes_with_empty_temporary_account_home() {
    use mini_monitor_native::codex_client::{AccountClient, AccountState};
    use mini_monitor_native::codex_process::RpcProcess;
    use serde_json::json;
    use std::{path::Path, time::Duration};

    let runtime = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("third_party/codex-app-server/codex-app-server.exe");
    let home = tempfile::tempdir().unwrap();
    let mut process = RpcProcess::spawn_verified(&runtime, home.path()).unwrap();
    process.initialize(Duration::from_secs(15)).unwrap();
    let account = process
        .request(
            2,
            "account/read",
            json!({"refreshToken":false}),
            Duration::from_secs(10),
            |_, _| {},
        )
        .unwrap();
    assert!(account.get("account").is_none_or(|value| value.is_null()));
    let mut client = AccountClient::new(process);
    assert!(matches!(
        client.refresh(|_, _| {}).unwrap(),
        AccountState::SignedOut
    ));
}
