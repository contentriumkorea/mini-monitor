#![cfg(windows)]
use mini_monitor_native::{
    budget_settings::save,
    config::{Settings, SettingsRepository},
    secrets::SecretStore,
};
#[test]
fn failed_secret_write_restores_budget_settings_without_changing_other_fields() {
    let temp = tempfile::tempdir().unwrap();
    let config = temp.path().join("config.json");
    let settings = Settings::parse("{}").unwrap();
    settings.save(&config).unwrap();
    let mut repository = SettingsRepository::new(config.clone(), settings);
    let blocked = temp.path().join("not-a-directory");
    std::fs::write(&blocked, b"fixture").unwrap();
    let store = SecretStore::at(blocked.join("secret"));
    assert!(save(
        &mut repository,
        &store,
        true,
        "100",
        "2026-01-01",
        "synthetic-key-123456"
    )
    .is_err());
    let value: serde_json::Value =
        serde_json::from_str(&Settings::load(&config).unwrap().to_json().unwrap()).unwrap();
    assert_eq!(value["openai"]["enabled"], false);
}
