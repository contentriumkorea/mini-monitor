//! Apply an explicitly supplied, officially signed local bundle through the normal
//! verified/rollback-capable updater. The running application must be closed first.
use mini_monitor_native::update::*;
use std::{fs::File, path::PathBuf};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let bundle = PathBuf::from(args.next().ok_or("absolute bundle path required")?);
    let install = PathBuf::from(
        args.next()
            .ok_or("absolute existing installation required")?,
    );
    let apply = args.next().as_deref() == Some("--apply");
    if !bundle.is_absolute()
        || !install.is_absolute()
        || install.file_name().and_then(|s| s.to_str()) != Some("Mini-Monitor")
    {
        return Err("invalid explicit installation path".into());
    }
    let manifest = verify_official_manifest(
        &std::fs::read(bundle.join("update-v2-manifest.json"))?,
        &std::fs::read(bundle.join("update-v2-manifest.sig"))?,
    )?;
    verify_install_inventory(&install)?;
    let archive_path = bundle.join("Mini-Monitor.zip");
    let mut archive = File::open(&archive_path)?;
    verify_official_archive(&mut archive, &manifest)?;
    println!(
        "signed_bundle={} current_inventory=valid archive=valid apply={apply}",
        manifest.version
    );
    if !apply {
        return Ok(());
    }
    let parent = install.parent().ok_or("missing install parent")?;
    let stage = stage_official_archive(&mut archive, &manifest, parent, || false)?;
    let handoff = prepare_official_handoff(
        stage,
        &install,
        &manifest,
        &archive_path,
        &install.join("scripts/Apply-Update.ps1"),
        None,
    )?;
    let journal = handoff.launch()?;
    println!("verified_handoff={}", journal.display());
    Ok(())
}
