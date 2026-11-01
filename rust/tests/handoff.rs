#![cfg(windows)]
use mini_monitor_native::update::acknowledge_startup;

#[test]
fn updated_app_acknowledges_only_matching_owned_handoff() {
    let base = tempfile::tempdir().unwrap();
    let install = base.path().join("Mini-Monitor");
    let stage = base.path().join(".mini-monitor-update-example");
    let backup = base.path().join(".mini-monitor-backup-example");
    std::fs::create_dir(&install).unwrap();
    std::fs::create_dir(&stage).unwrap();
    std::fs::create_dir(&backup).unwrap();
    let executable = install.join("Mini-Monitor.exe");
    std::fs::write(&executable, b"fixture").unwrap();
    let ack = stage.join("startup-ack.json");
    let nonce = "1234567890abcdef1234567890abcdef";
    let journal = serde_json::json!({"schema_version":1,"state":"new_installed","nonce":nonce,"version":"1.2.3","install_root":install,"ack_path":ack,"backup_root":backup});
    std::fs::write(
        stage.join("update-journal.json"),
        serde_json::to_vec(&journal).unwrap(),
    )
    .unwrap();
    assert!(!acknowledge_startup(
        &executable,
        &ack,
        "00000000000000000000000000000000",
        "1.2.3"
    )
    .unwrap());
    assert!(!acknowledge_startup(&executable, &ack, nonce, "1.2.4").unwrap());
    assert!(!ack.exists());
    let original = serde_json::to_string(&journal).unwrap();
    let duplicate = format!(
        "{},\"nonce\":\"{}\"}}",
        &original[..original.len() - 1],
        nonce
    );
    std::fs::write(stage.join("update-journal.json"), duplicate).unwrap();
    assert!(!acknowledge_startup(&executable, &ack, nonce, "1.2.3").unwrap());
    assert!(!ack.exists());
    std::fs::write(stage.join("update-journal.json"), original).unwrap();
    assert!(acknowledge_startup(&executable, &ack, nonce, "1.2.3").unwrap());
    let value: serde_json::Value = serde_json::from_slice(&std::fs::read(&ack).unwrap()).unwrap();
    assert_eq!(
        value,
        serde_json::json!({"nonce":nonce,"ready":true,"version":"1.2.3"})
    );
}

#[test]
#[ignore = "stages the official release inside an isolated temporary installation"]
fn prepared_handoff_is_hash_pinned_and_never_replaces_the_existing_install() {
    use sha2::{Digest, Sha256};
    let fixtures = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".superpowers/sdd/2026-11-01-rust-migration");
    let temp = tempfile::tempdir_in(&fixtures).unwrap();
    let install = temp.path().join("Mini-Monitor");
    std::fs::create_dir(&install).unwrap();
    std::fs::create_dir(install.join("scripts")).unwrap();
    std::fs::write(install.join("Mini-Monitor.exe"), b"old app").unwrap();
    let helper = install.join("scripts/Apply-Update.ps1");
    std::fs::write(&helper, b"fixture helper").unwrap();
    let hash = |v: &[u8]| format!("{:x}", Sha256::digest(v));
    let inventory = format!(
        "{}  Mini-Monitor.exe\n{}  scripts/Apply-Update.ps1\n",
        hash(b"old app"),
        hash(b"fixture helper")
    );
    std::fs::write(install.join("SHA256SUMS.txt"), inventory).unwrap();
    let payload = std::fs::read(fixtures.join("update-v2-manifest.json")).unwrap();
    let sig = std::fs::read(fixtures.join("update-v2-manifest.sig")).unwrap();
    let manifest = mini_monitor_native::update::verify_official_manifest(&payload, &sig).unwrap();
    let archive_path = fixtures.join("Mini-Monitor.zip");
    let mut archive = std::fs::File::open(&archive_path).unwrap();
    let stage = mini_monitor_native::update::stage_official_archive(
        &mut archive,
        &manifest,
        temp.path(),
        || false,
    )
    .unwrap();
    let prepared = mini_monitor_native::update::prepare_official_handoff(
        stage,
        &install,
        &manifest,
        &archive_path,
        &helper,
        None,
    )
    .unwrap();
    assert_eq!(
        std::fs::read(install.join("Mini-Monitor.exe")).unwrap(),
        b"old app"
    );
    let journal: serde_json::Value =
        serde_json::from_slice(&std::fs::read(prepared.journal_path()).unwrap()).unwrap();
    assert_eq!(journal["state"], "prepared");
    assert_eq!(journal["version"], "0.2.13");
    assert!(prepared
        .stage_path()
        .join("Mini-Monitor/Mini-Monitor.exe")
        .is_file());
}

#[test]
#[ignore = "performs a controlled rollback on an isolated synthetic installation"]
fn prepared_journal_is_accepted_and_helper_restores_old_install_after_swap_failure() {
    use sha2::{Digest, Sha256};
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap();
    let fixtures = root.join(".superpowers/sdd/2026-11-01-rust-migration");
    let temp = tempfile::tempdir_in(&fixtures).unwrap();
    let install = temp.path().join("Mini-Monitor");
    std::fs::create_dir(&install).unwrap();
    std::fs::create_dir(install.join("scripts")).unwrap();
    std::fs::write(install.join("Mini-Monitor.exe"), b"old app").unwrap();
    let helper = install.join("scripts/Apply-Update.ps1");
    let helper_bytes = std::fs::read(root.join("scripts/Apply-Update.ps1")).unwrap();
    std::fs::write(&helper, &helper_bytes).unwrap();
    let hash = |v: &[u8]| format!("{:x}", Sha256::digest(v));
    std::fs::write(
        install.join("SHA256SUMS.txt"),
        format!(
            "{}  Mini-Monitor.exe\n{}  scripts/Apply-Update.ps1\n",
            hash(b"old app"),
            hash(&helper_bytes)
        ),
    )
    .unwrap();
    let payload = std::fs::read(fixtures.join("update-v2-manifest.json")).unwrap();
    let signature = std::fs::read(fixtures.join("update-v2-manifest.sig")).unwrap();
    let manifest =
        mini_monitor_native::update::verify_official_manifest(&payload, &signature).unwrap();
    let archive_path = fixtures.join("Mini-Monitor.zip");
    let mut archive = std::fs::File::open(&archive_path).unwrap();
    let stage = mini_monitor_native::update::stage_official_archive(
        &mut archive,
        &manifest,
        temp.path(),
        || false,
    )
    .unwrap();
    let prepared = mini_monitor_native::update::prepare_official_handoff(
        stage,
        &install,
        &manifest,
        &archive_path,
        &helper,
        None,
    )
    .unwrap();
    let powershell = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let result = std::process::Command::new(powershell)
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ])
        .arg(prepared.stage_path().join("Apply-Update.ps1"))
        .arg("-JournalPath")
        .arg(prepared.journal_path())
        .arg("-JournalSha256")
        .arg(prepared.journal_sha256())
        .arg("-HelperSha256")
        .arg(prepared.helper_sha256())
        .args(["-ParentPid", "99999999"])
        .env("PYTEST_CURRENT_TEST", "isolated-rust-update-test")
        .env("MINI_MONITOR_UPDATE_TEST_NO_NOTICE", "1")
        .env("MINI_MONITOR_UPDATE_TEST_FAIL_AFTER_SWAP", "1")
        .output()
        .unwrap();
    assert_eq!(
        result.status.code(),
        Some(3),
        "helper failed before expected controlled rollback: {}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(
        std::fs::read(install.join("Mini-Monitor.exe")).unwrap(),
        b"old app"
    );
    let journal: serde_json::Value =
        serde_json::from_slice(&std::fs::read(prepared.journal_path()).unwrap()).unwrap();
    assert_eq!(journal["state"], "rolled_back_start_failed");
}
