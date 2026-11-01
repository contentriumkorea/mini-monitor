//! Persist budget options and DPAPI credentials without exposing their values.
use crate::{
    config::{Settings, SettingsRepository},
    secrets::SecretStore,
};
use std::io;
pub fn save(
    repository: &mut SettingsRepository,
    store: &SecretStore,
    enabled: bool,
    total: &str,
    date: &str,
    key: &str,
) -> io::Result<()> {
    if !key.is_empty()
        && (!(16..=1024).contains(&key.len())
            || !key.is_ascii()
            || key.chars().any(char::is_whitespace))
    {
        return Err(io::Error::other("invalid API key format"));
    }
    let previous = repository
        .settings()
        .to_json()
        .map_err(|_| io::Error::other("settings unavailable"))?;
    let draft = repository
        .settings()
        .with_openai_budget(enabled, total, date)
        .map_err(|_| io::Error::other("invalid budget settings"))?;
    draft
        .startup_settings()
        .map_err(|_| io::Error::other("invalid budget settings"))?;
    if enabled && key.is_empty() && store.get()?.is_none() {
        return Err(io::Error::other("API key required"));
    }
    repository
        .update(|_| Ok(draft))
        .map_err(|_| io::Error::other("settings save failed"))?;
    if !key.is_empty() && store.set(key).is_err() {
        repository
            .update(|_| Settings::parse(&previous))
            .map_err(|_| io::Error::other("settings recovery required"))?;
        return Err(io::Error::other("credential save failed"));
    }
    Ok(())
}
