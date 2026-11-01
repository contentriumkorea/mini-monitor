//! Verified update retirement only; never scans or deletes personal folders.
use crate::update::{self, verify_backup_inventory, verify_install_inventory};
use base64::Engine;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    io,
    os::windows::{
        ffi::OsStrExt,
        fs::{MetadataExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
};
#[derive(serde::Deserialize)]
struct Record {
    schema_version: u32,
    state: String,
    install_root: PathBuf,
    staged_root: PathBuf,
    backup_root: PathBuf,
    ack_path: PathBuf,
    nonce: String,
    version: String,
    helper_sha256: String,
    archive_sha256: String,
    old_inventory_sha256: String,
    old_files: Vec<(String, String)>,
    new_files: Vec<(String, String)>,
    config_path: Option<PathBuf>,
}
#[derive(serde::Deserialize)]
struct Ack {
    ready: bool,
    nonce: String,
    version: String,
}
struct Retirement {
    stage: PathBuf,
    backup: PathBuf,
}
fn error() -> io::Error {
    io::Error::other("update cleanup validation failed")
}
fn regular(path: &Path, directory: bool) -> bool {
    std::fs::symlink_metadata(path).is_ok_and(|m| {
        m.file_attributes() & 0x400 == 0
            && !m.file_type().is_symlink()
            && if directory { m.is_dir() } else { m.is_file() }
    })
}
fn hash(path: &Path) -> io::Result<String> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
    }
    Ok(format!("{:x}", digest.finalize()))
}
fn json<T: serde::de::DeserializeOwned>(path: &Path) -> io::Result<T> {
    if !regular(path, false) {
        return Err(error());
    }
    let bytes =
        update::read_bounded(std::fs::File::open(path)?, 1024 * 1024).map_err(|_| error())?;
    serde_json::from_slice(&bytes).map_err(|_| error())
}
fn within(path: &Path, root: &Path) -> bool {
    let path = path.to_string_lossy().to_lowercase();
    let root = root.to_string_lossy().trim_end_matches('\\').to_lowercase();
    path == root || path.starts_with(&(root + "\\"))
}
fn inspect(root: &Path, stage: &Path) -> io::Result<Retirement> {
    if !regular(stage, true) || stage.parent() != root.parent() {
        return Err(error());
    }
    let id = stage
        .file_name()
        .and_then(|v| v.to_str())
        .and_then(|v| v.strip_prefix(".mini-monitor-update-"))
        .ok_or_else(error)?;
    if !(6..=40).contains(&id.len())
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(error());
    }
    let record: Record = json(&stage.join("update-journal.json"))?;
    if record.schema_version != 1
        || !["healthy_backup_retained", "backup_recycled"].contains(&record.state.as_str())
        || record.install_root != root
        || record.staged_root != stage.join("Mini-Monitor")
        || record.ack_path != stage.join("startup-ack.json")
        || record.backup_root.parent() != root.parent()
        || record.nonce.len() != 32
        || !record.nonce.bytes().all(|b| b.is_ascii_hexdigit())
        || update::is_newer_release(&record.version, &record.version).is_err()
    {
        return Err(error());
    }
    let backup_id = record
        .backup_root
        .file_name()
        .and_then(|v| v.to_str())
        .and_then(|v| v.strip_prefix(".mini-monitor-backup-"))
        .ok_or_else(error)?;
    if backup_id.len() != 32 || !backup_id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(error());
    }
    if let Some(config) = &record.config_path {
        if !config.is_absolute()
            || [root, stage, record.backup_root.as_path()]
                .iter()
                .any(|p| within(config, p))
        {
            return Err(error());
        }
    }
    let mut count = 0;
    for entry in std::fs::read_dir(stage)? {
        let path = entry?.path();
        if !regular(&path, false)
            || ![
                "update-journal.json",
                "startup-ack.json",
                "release.zip",
                "Apply-Update.ps1",
            ]
            .iter()
            .any(|name| path.file_name() == Some(std::ffi::OsStr::new(name)))
        {
            return Err(error());
        }
        count += 1;
    }
    if count != 4 {
        return Err(error());
    }
    let ack: Ack = json(&record.ack_path)?;
    if !ack.ready
        || ack.nonce != record.nonce
        || ack.version != record.version
        || hash(&stage.join("Apply-Update.ps1"))? != record.helper_sha256
    {
        return Err(error());
    }
    let archive = stage.join("release.zip");
    let manifest = update::UpdateManifest {
        version: record.version,
        archive_url: String::new(),
        archive_sha256: record.archive_sha256,
        archive_size: std::fs::metadata(&archive)?.len(),
        files: record.new_files,
    };
    update::verify_archive(std::fs::File::open(archive)?, &manifest).map_err(|_| error())?;
    if record.backup_root.exists() {
        if record.state == "backup_recycled" {
            return Err(error());
        }
        let actual = verify_backup_inventory(&record.backup_root).map_err(|_| error())?;
        let map = |files: Vec<(String, String)>| {
            let len = files.len();
            let mapped: BTreeMap<_, _> = files
                .into_iter()
                .map(|(name, digest)| (name.to_lowercase(), digest))
                .collect();
            (len, mapped)
        };
        let (a_len, a) = map(actual);
        let (b_len, b) = map(record.old_files);
        if a_len != b_len
            || b_len != b.len()
            || a != b
            || hash(&record.backup_root.join("SHA256SUMS.txt"))? != record.old_inventory_sha256
        {
            return Err(error());
        }
    }
    Ok(Retirement {
        stage: stage.to_path_buf(),
        backup: record.backup_root,
    })
}
fn candidates(root: &Path) -> io::Result<Vec<Retirement>> {
    verify_install_inventory(root).map_err(|_| error())?;
    let parent = root.parent().ok_or_else(error)?;
    let mut result = Vec::new();
    for entry in std::fs::read_dir(parent)?.take(256) {
        let path = entry?.path();
        if path
            .file_name()
            .and_then(|v| v.to_str())
            .is_some_and(|v| v.starts_with(".mini-monitor-update-"))
        {
            if let Ok(retired) = inspect(root, &path) {
                result.push(retired);
            }
        }
    }
    Ok(result)
}
pub fn eligible_backups(root: &Path) -> io::Result<usize> {
    Ok(candidates(root)?
        .into_iter()
        .filter(|p| p.backup.exists())
        .count())
}
fn repair_links(root: &Path, backup: &Path) -> io::Result<bool> {
    use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;
    let mut directory = [0u16; 32768];
    let n = unsafe { GetSystemDirectoryW(directory.as_mut_ptr(), directory.len() as u32) } as usize;
    if n == 0 || n >= directory.len() {
        return Err(error());
    }
    let system = PathBuf::from(String::from_utf16(&directory[..n]).map_err(|_| error())?);
    let script = root.join("scripts/Repair-UpdateLinks.ps1");
    if !regular(&script, false) {
        return Err(error());
    }
    let data =
        serde_json::to_vec(&serde_json::json!({"install_root":root,"retired_roots":[backup]}))?;
    let mut process =
        std::process::Command::new(system.join("WindowsPowerShell/v1.0/powershell.exe"))
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ])
            .arg(script)
            .current_dir(&system)
            .env(
                "PSModulePath",
                system.join("WindowsPowerShell/v1.0/Modules"),
            )
            .env(
                "MINI_MONITOR_REPAIR_LINKS",
                base64::engine::general_purpose::STANDARD.encode(data),
            )
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(0x08000000)
            .spawn()?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    loop {
        if let Some(status) = process.try_wait()? {
            return Ok(status.success());
        }
        if std::time::Instant::now() >= deadline {
            let _ = process.kill();
            let _ = process.wait();
            return Ok(false);
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}
fn recycle(path: &Path) -> bool {
    use windows_sys::Win32::UI::Shell::*;
    if !path.is_absolute() || !regular(path, true) {
        return false;
    }
    let from: Vec<u16> = path.as_os_str().encode_wide().chain([0, 0]).collect();
    let mut operation: SHFILEOPSTRUCTW = unsafe { std::mem::zeroed() };
    operation.wFunc = FO_DELETE;
    operation.pFrom = from.as_ptr();
    operation.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT) as u16;
    unsafe {
        SHFileOperationW(&mut operation) == 0
            && operation.fAnyOperationsAborted == 0
            && !path.exists()
    }
}
/// Run off the UI thread only after a healthy startup; an active updater's lock blocks retirement.
pub fn cleanup(root: &Path) -> io::Result<usize> {
    verify_install_inventory(root).map_err(|_| error())?;
    let parent = root.parent().ok_or_else(error)?;
    let lock_path = parent.join(".mini-monitor-update.lock");
    if lock_path.exists() && !regular(&lock_path, false) {
        return Err(error());
    }
    let _lock = match std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .share_mode(0)
        .open(lock_path)
    {
        Ok(lock) => lock,
        Err(_) => return Ok(0),
    };
    let mut count = 0;
    let started = std::time::Instant::now();
    for retired in candidates(root)? {
        if started.elapsed() > std::time::Duration::from_secs(30) {
            break;
        }
        if !repair_links(root, &retired.backup)? {
            continue;
        }
        verify_install_inventory(root).map_err(|_| error())?;
        let retired = inspect(root, &retired.stage)?;
        if retired.backup.exists() {
            if !recycle(&retired.backup) {
                continue;
            }
            count += 1;
        }
        if !retired.backup.exists() && inspect(root, &retired.stage).is_ok() {
            recycle(&retired.stage);
        }
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use sha2::{Digest, Sha256};
    use std::{io::Write, path::Path};
    fn hash(bytes: &[u8]) -> String {
        format!("{:x}", Sha256::digest(bytes))
    }
    fn tree(path: &Path, bytes: &[u8]) {
        std::fs::create_dir(path).unwrap();
        std::fs::write(path.join("Mini-Monitor.exe"), bytes).unwrap();
        std::fs::write(
            path.join("SHA256SUMS.txt"),
            format!("{}  Mini-Monitor.exe\n", hash(bytes)),
        )
        .unwrap();
    }
    #[test]
    fn retirement_requires_complete_owned_stage_and_preserves_unknown_backup_content() {
        run_fixture(false);
    }
    #[test]
    #[ignore = "recycles only two generated, verified disposable update folders in the current user's Recycle Bin"]
    fn verified_fixture_can_be_repaired_and_recycled_without_touching_current_program() {
        run_fixture(true);
    }
    fn run_fixture(recycle: bool) {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("Mini-Monitor");
        tree(&root, b"new");
        let backup = temp
            .path()
            .join(".mini-monitor-backup-00000000000000000000000000000001");
        tree(&backup, b"old");
        let stage = temp.path().join(".mini-monitor-update-fixture");
        std::fs::create_dir(&stage).unwrap();
        let mut zip =
            zip::ZipWriter::new(std::fs::File::create(stage.join("release.zip")).unwrap());
        let files = [
            ("Mini-Monitor/Mini-Monitor.exe", b"new".to_vec()),
            (
                "Mini-Monitor/SHA256SUMS.txt",
                std::fs::read(root.join("SHA256SUMS.txt")).unwrap(),
            ),
        ];
        for (name, bytes) in &files {
            zip.start_file(*name, zip::write::FileOptions::default())
                .unwrap();
            zip.write_all(bytes).unwrap();
        }
        zip.finish().unwrap();
        let archive = std::fs::read(stage.join("release.zip")).unwrap();
        std::fs::write(stage.join("Apply-Update.ps1"), b"helper").unwrap();
        let nonce = "00000000000000000000000000000002";
        let record = serde_json::json!({"schema_version":1,"state":"healthy_backup_retained","install_root":root,"staged_root":stage.join("Mini-Monitor"),"backup_root":backup,"ack_path":stage.join("startup-ack.json"),"nonce":nonce,"version":"0.3.0","helper_sha256":hash(b"helper"),"archive_sha256":hash(&archive),"old_inventory_sha256":hash(&std::fs::read(backup.join("SHA256SUMS.txt")).unwrap()),"old_files":[["Mini-Monitor.exe",hash(b"old")]],"new_files":files.iter().map(|(name,bytes)|(*name,hash(bytes))).collect::<Vec<_>>(),"config_path":null});
        std::fs::write(
            stage.join("update-journal.json"),
            serde_json::to_vec(&record).unwrap(),
        )
        .unwrap();
        std::fs::write(
            stage.join("startup-ack.json"),
            serde_json::to_vec(&serde_json::json!({"ready":true,"nonce":nonce,"version":"0.3.0"}))
                .unwrap(),
        )
        .unwrap();
        assert_eq!(super::eligible_backups(&root).unwrap(), 1);
        std::fs::write(backup.join("photo.txt"), b"personal fixture").unwrap();
        assert_eq!(super::eligible_backups(&root).unwrap(), 0);
        std::fs::remove_file(backup.join("photo.txt")).unwrap();
        std::fs::create_dir(backup.join("photos")).unwrap();
        assert_eq!(super::eligible_backups(&root).unwrap(), 0);
        assert!(backup.join("Mini-Monitor.exe").exists());
        if recycle {
            std::fs::remove_dir(backup.join("photos")).unwrap();
            std::fs::create_dir(root.join("scripts")).unwrap();
            let repair = std::fs::read(
                std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .parent()
                    .unwrap()
                    .join("scripts/Repair-UpdateLinks.ps1"),
            )
            .unwrap();
            std::fs::write(root.join("scripts/Repair-UpdateLinks.ps1"), &repair).unwrap();
            std::fs::write(
                root.join("SHA256SUMS.txt"),
                format!(
                    "{}  Mini-Monitor.exe\n{}  scripts/Repair-UpdateLinks.ps1\n",
                    hash(b"new"),
                    hash(&repair)
                ),
            )
            .unwrap();
            assert_eq!(super::cleanup(&root).unwrap(), 1);
            assert!(!backup.exists());
            assert!(!stage.exists());
            assert_eq!(
                std::fs::read(root.join("Mini-Monitor.exe")).unwrap(),
                b"new"
            );
        }
    }
}
