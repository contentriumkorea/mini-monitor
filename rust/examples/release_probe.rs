//! Read-only release verification; extraction is confined to automatically cleaned temporary data.
use mini_monitor_native::update::*;
use std::{fs::File, path::PathBuf};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let argument = std::env::args()
        .nth(1)
        .ok_or("bundle directory or --public required")?;
    let temporary = tempfile::tempdir()?;
    let (manifest, archive) = if argument == "--public" {
        let http = UpdateHttp::new()?;
        let latest = http.latest()?;
        let manifest = http.fetch_verified_assets(&latest)?;
        let archive = http.download_archive(&manifest, temporary.path(), || false)?;
        (manifest, archive.into_file())
    } else {
        let directory = PathBuf::from(argument);
        let manifest = verify_official_manifest(
            &std::fs::read(directory.join("update-v2-manifest.json"))?,
            &std::fs::read(directory.join("update-v2-manifest.sig"))?,
        )?;
        (manifest, File::open(directory.join("Mini-Monitor.zip"))?)
    };
    verify_official_archive(archive.try_clone()?, &manifest)?;
    let mut archive = archive;
    let stage = stage_official_archive(&mut archive, &manifest, temporary.path(), || false)?;
    let executable = stage.path().join("Mini-Monitor/Mini-Monitor-CLI.exe");
    let version = std::process::Command::new(executable)
        .arg("--version")
        .output()?;
    if !version.status.success()
        || String::from_utf8(version.stdout)?.trim() != format!("Mini Monitor {}", manifest.version)
    {
        return Err("staged CLI version mismatch".into());
    }
    for previous in ["0.3.0", "0.3.1", "0.3.2", "0.3.3"] {
        assert!(is_newer_release(previous, &manifest.version)?);
    }
    assert!(!is_newer_release(&manifest.version, &manifest.version)?);
    println!("version={} signature=valid archive=valid staging=valid cli=valid update_from_0.3.0_to_0.3.3=available same_version=current", manifest.version);
    Ok(())
}
