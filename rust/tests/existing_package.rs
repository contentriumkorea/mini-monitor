use mini_monitor_native::update::verify_install_inventory;
use sha2::{Digest, Sha256};

#[test]
fn installed_inventory_detects_modified_missing_and_extra_files() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("Mini-Monitor");
    std::fs::create_dir(&root).unwrap();
    std::fs::create_dir(root.join("assets")).unwrap();
    std::fs::write(root.join("Mini-Monitor.exe"), b"app").unwrap();
    std::fs::write(root.join("assets/icon.png"), b"icon").unwrap();
    let hash = |bytes: &[u8]| format!("{:x}", Sha256::digest(bytes));
    let text = format!(
        "{}  Mini-Monitor.exe\n{}  assets/icon.png\n",
        hash(b"app"),
        hash(b"icon")
    );
    std::fs::write(root.join("SHA256SUMS.txt"), text).unwrap();
    assert_eq!(verify_install_inventory(&root).unwrap().len(), 2);
    std::fs::write(root.join("assets/icon.png"), b"changed").unwrap();
    assert!(verify_install_inventory(&root).is_err());
    std::fs::write(root.join("assets/icon.png"), b"icon").unwrap();
    std::fs::write(root.join("unknown.txt"), b"keep").unwrap();
    assert!(verify_install_inventory(&root).is_err());
    std::fs::remove_file(root.join("unknown.txt")).unwrap();
    std::fs::remove_file(root.join("assets/icon.png")).unwrap();
    assert!(verify_install_inventory(&root).is_err());
}

#[test]
#[ignore = "read-only verification of the current installed Mini Monitor files"]
fn current_installation_matches_its_inventory() {
    let root = std::path::PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap())
        .join("Programs/Mini-Monitor");
    assert!(!verify_install_inventory(&root).unwrap().is_empty());
}
