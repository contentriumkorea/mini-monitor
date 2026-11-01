#[cfg(windows)]
#[test]
fn synthetic_secret_round_trips_with_existing_entropy_and_is_not_plaintext() {
    use mini_monitor_native::secrets::{protect, unprotect};
    let original = b"synthetic-fixture-not-a-real-secret";
    let protected = protect(original).unwrap();
    assert!(!protected.windows(original.len()).any(|w| w == original));
    assert_eq!(unprotect(&protected).unwrap(), original);
}

#[cfg(windows)]
#[test]
fn secret_file_is_encrypted_replaced_atomically_and_readable_by_python() {
    use mini_monitor_native::secrets::SecretStore;
    use std::process::Command;
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("openai_admin_key.dpapi");
    let store = SecretStore::at(file.clone());
    assert_eq!(store.get().unwrap(), None);
    store.set("fixture-one").unwrap();
    store.set("fixture-two").unwrap();
    assert_eq!(store.get().unwrap().as_deref(), Some("fixture-two"));
    assert!(!std::fs::read(&file)
        .unwrap()
        .windows(11)
        .any(|v| v == b"fixture-two"));
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap();
    let status = Command::new(root.join(".venv/Scripts/python.exe")).current_dir(root).env("PYTHONPATH", root.join("src"))
        .args(["-c", "import sys; from pathlib import Path; from ai_mini_monitor.security.dpapi import DPAPISecretStore; sys.exit(0 if DPAPISecretStore(Path(sys.argv[1])).get() == 'fixture-two' else 1)"])
        .arg(&file).status().unwrap();
    assert!(status.success());
    assert!(store.set(" ").is_err());
    assert_eq!(store.get().unwrap().as_deref(), Some("fixture-two"));
}

#[cfg(windows)]
#[test]
fn python_and_rust_dpapi_are_interoperable_for_synthetic_data() {
    use mini_monitor_native::secrets::{protect, unprotect};
    use std::{
        io::Write,
        process::{Command, Stdio},
    };
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap();
    let python = root.join(".venv/Scripts/python.exe");
    let fixture = b"cross-language-synthetic-only";
    let output = Command::new(&python).current_dir(root).env("PYTHONPATH", root.join("src"))
        .args(["-c", "import sys; from ai_mini_monitor.security.dpapi import protect; sys.stdout.buffer.write(protect(b'cross-language-synthetic-only'))"])
        .output().unwrap();
    assert!(output.status.success());
    assert_eq!(unprotect(&output.stdout).unwrap(), fixture);
    let ciphertext = protect(fixture).unwrap();
    let mut child = Command::new(python).current_dir(root).env("PYTHONPATH", root.join("src"))
        .args(["-c", "import sys; from ai_mini_monitor.security.dpapi import unprotect; sys.exit(0 if unprotect(sys.stdin.buffer.read()) == b'cross-language-synthetic-only' else 1)"])
        .stdin(Stdio::piped()).spawn().unwrap();
    child.stdin.take().unwrap().write_all(&ciphertext).unwrap();
    assert!(child.wait().unwrap().success());
}
