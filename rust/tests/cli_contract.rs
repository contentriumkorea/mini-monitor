#[test]
#[cfg(windows)]
fn preview_and_diagnostics_contracts_remain_available_without_a_serial_connection() {
    let temporary = tempfile::tempdir().unwrap();
    let image = temporary.path().join("preview.png");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_cli"))
        .args(["--preview", "zero", "--no-window", "--preview-output"])
        .arg(&image)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(&std::fs::read(image).unwrap()[..8], b"\x89PNG\r\n\x1a\n");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_cli"))
        .args(["--diagnose", "-"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let data: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(data["read_only"], true);
    assert_eq!(data["serial_port_opened"], false);
    for args in [
        vec!["--preview", "not-a-state", "--no-window"],
        vec!["--desktop-smoke", "1"],
        vec!["--headless-run", "NaN"],
    ] {
        let result = std::process::Command::new(env!("CARGO_BIN_EXE_cli"))
            .args(args)
            .output()
            .unwrap();
        assert_eq!(result.status.code(), Some(2));
    }
}

#[test]
fn fixed_online_installer_can_verify_the_native_version() {
    let result = std::process::Command::new(env!("CARGO_BIN_EXE_cli"))
        .arg("--version")
        .output()
        .unwrap();
    assert!(result.status.success());
    assert_eq!(
        String::from_utf8(result.stdout).unwrap().trim(),
        format!("Mini Monitor {}", env!("CARGO_PKG_VERSION"))
    );
    assert!(!std::process::Command::new(env!("CARGO_BIN_EXE_cli"))
        .arg("--unknown")
        .output()
        .unwrap()
        .status
        .success());
}
