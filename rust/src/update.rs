//! Fail-closed primitives for validating signed update metadata.
use ring::signature::{UnparsedPublicKey, ED25519};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Read, Seek, SeekFrom, Write},
};

pub struct UpdateManifest {
    pub version: String,
    pub archive_url: String,
    pub archive_sha256: String,
    pub archive_size: u64,
    pub files: Vec<(String, String)>,
}

/// Caller must write to a disposable staging file, never the live installation.
pub fn copy_archive_bytes(
    mut source: impl Read,
    mut target: impl Write,
    size: u64,
    digest: &str,
    cancelled: impl Fn() -> bool,
) -> Result<(), &'static str> {
    if !(1..=1024 * 1024 * 1024).contains(&size) || !sha256_text(digest) {
        return Err("invalid archive metadata");
    }
    let mut written = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    let mut hash = Sha256::new();
    loop {
        if cancelled() {
            return Err("download cancelled");
        }
        let n = source
            .read(&mut buffer)
            .map_err(|_| "archive download failed")?;
        if n == 0 {
            break;
        }
        written = written
            .checked_add(n as u64)
            .filter(|n| *n <= size)
            .ok_or("archive size mismatch")?;
        if cancelled() {
            return Err("download cancelled");
        }
        target
            .write_all(&buffer[..n])
            .map_err(|_| "archive write failed")?;
        hash.update(&buffer[..n]);
    }
    if written != size || format!("{:x}", hash.finalize()) != digest {
        return Err("archive mismatch");
    }
    Ok(())
}
pub struct LatestRelease {
    pub version: String,
    pub manifest_url: String,
    pub signature_url: String,
}

pub struct UpdateHttp {
    client: reqwest::blocking::Client,
}
impl UpdateHttp {
    pub fn new() -> Result<Self, &'static str> {
        let policy = reqwest::redirect::Policy::custom(|attempt| {
            if attempt.previous().len() >= 5 || !trusted_download_redirect(attempt.url().as_str()) {
                attempt.stop()
            } else {
                attempt.follow()
            }
        });
        let client = reqwest::blocking::Client::builder()
            .https_only(true)
            .redirect(policy)
            .connect_timeout(std::time::Duration::from_secs(7))
            .timeout(std::time::Duration::from_secs(20))
            .user_agent("Mini-Monitor-Update")
            .build()
            .map_err(|_| "release client unavailable")?;
        Ok(Self { client })
    }
    fn get(&self, url: &str, limit: usize) -> Result<Vec<u8>, &'static str> {
        let response = self
            .client
            .get(url)
            .send()
            .map_err(|_| "release download failed")?;
        if response.status() != reqwest::StatusCode::OK
            || response.content_length().is_some_and(|n| n > limit as u64)
        {
            return Err("release download failed");
        }
        read_bounded(response, limit)
    }
    pub fn latest(&self) -> Result<LatestRelease, &'static str> {
        let payload = self.get(
            "https://api.github.com/repos/contentriumkorea/mini-monitor/releases/latest",
            1024 * 1024,
        )?;
        select_latest_release(&payload)
    }
    pub fn fetch_verified_assets(
        &self,
        release: &LatestRelease,
    ) -> Result<VerifiedManifest, &'static str> {
        if !stable_version(&release.version) {
            return Err("invalid release version");
        }
        let base = format!(
            "https://github.com/contentriumkorea/mini-monitor/releases/download/v{}/",
            release.version
        );
        if release.manifest_url != format!("{base}update-v2-manifest.json")
            || release.signature_url != format!("{base}update-v2-manifest.sig")
        {
            return Err("invalid release asset");
        }
        let payload = self.get(&release.manifest_url, 1024 * 1024)?;
        let signature = self.get(&release.signature_url, 64)?;
        let manifest = verify_official_manifest(&payload, &signature)?;
        if manifest.version != release.version {
            return Err("release version mismatch");
        }
        Ok(manifest)
    }
    /// Return an automatically cleaned temporary archive only after complete verification.
    pub fn download_archive(
        &self,
        manifest: &VerifiedManifest,
        directory: &std::path::Path,
        cancelled: impl Fn() -> bool,
    ) -> Result<tempfile::NamedTempFile, &'static str> {
        if cancelled() {
            return Err("download cancelled");
        }
        if !directory.is_absolute() {
            return Err("invalid staging directory");
        }
        for component in directory.ancestors() {
            let meta =
                std::fs::symlink_metadata(component).map_err(|_| "invalid staging directory")?;
            if !meta.is_dir() || meta.file_type().is_symlink() {
                return Err("invalid staging directory");
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes() & 0x400 != 0 {
                    return Err("invalid staging directory");
                }
            }
        }
        let response = self
            .client
            .get(&manifest.archive_url)
            .timeout(std::time::Duration::from_secs(120))
            .send()
            .map_err(|_| "archive download failed")?;
        if response.status() != reqwest::StatusCode::OK
            || response
                .content_length()
                .is_some_and(|n| n != manifest.archive_size)
        {
            return Err("archive download failed");
        }
        let mut temporary = tempfile::Builder::new()
            .prefix(".mini-monitor-download-")
            .tempfile_in(directory)
            .map_err(|_| "staging unavailable")?;
        copy_archive_bytes(
            response,
            temporary.as_file_mut(),
            manifest.archive_size,
            &manifest.archive_sha256,
            &cancelled,
        )?;
        temporary
            .as_file_mut()
            .sync_all()
            .map_err(|_| "staging write failed")?;
        if cancelled() {
            return Err("download cancelled");
        }
        verify_official_archive(temporary.as_file_mut(), manifest)?;
        Ok(temporary)
    }
}

pub fn trusted_download_redirect(text: &str) -> bool {
    let Ok(url) = reqwest::Url::parse(text) else {
        return false;
    };
    url.as_str() == text
        && url.scheme() == "https"
        && matches!(
            url.host_str(),
            Some(
                "github.com"
                    | "release-assets.githubusercontent.com"
                    | "objects.githubusercontent.com"
            )
        )
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
        && url.fragment().is_none()
}
pub fn read_bounded(reader: impl Read, limit: usize) -> Result<Vec<u8>, &'static str> {
    let upper = u64::try_from(limit)
        .map_err(|_| "release size invalid")?
        .checked_add(1)
        .ok_or("release size invalid")?;
    let mut bytes = Vec::new();
    reader
        .take(upper)
        .read_to_end(&mut bytes)
        .map_err(|_| "release download failed")?;
    if bytes.len() > limit {
        return Err("release too large");
    }
    Ok(bytes)
}

/// Select only the stable signed v2 asset names from GitHub's latest-release response.
pub fn select_latest_release(payload: &[u8]) -> Result<LatestRelease, &'static str> {
    if payload.is_empty() || payload.len() > 1024 * 1024 {
        return Err("invalid release metadata");
    }
    let root: serde_json::Value =
        serde_json::from_slice(payload).map_err(|_| "invalid release metadata")?;
    if root.get("draft").and_then(|v| v.as_bool()) != Some(false)
        || root.get("prerelease").and_then(|v| v.as_bool()) != Some(false)
    {
        return Err("not a stable release");
    }
    let version = root
        .get("tag_name")
        .and_then(|v| v.as_str())
        .and_then(|v| v.strip_prefix('v'))
        .filter(|v| stable_version(v))
        .ok_or("invalid release version")?;
    let assets = root
        .get("assets")
        .and_then(|v| v.as_array())
        .filter(|v| v.len() <= 200)
        .ok_or("invalid release assets")?;
    let mut manifest = None;
    let mut signature = None;
    let mut archive = false;
    for asset in assets {
        let Some(name) = asset.get("name").and_then(|v| v.as_str()) else {
            continue;
        };
        if !matches!(
            name,
            "update-v2-manifest.json" | "update-v2-manifest.sig" | "Mini-Monitor.zip"
        ) {
            continue;
        }
        let url = asset
            .get("browser_download_url")
            .and_then(|v| v.as_str())
            .ok_or("invalid asset")?;
        let expected = format!(
            "https://github.com/contentriumkorea/mini-monitor/releases/download/v{version}/{name}"
        );
        let size = asset
            .get("size")
            .and_then(|v| v.as_u64())
            .ok_or("invalid asset")?;
        if url != expected || asset.get("state").and_then(|v| v.as_str()) != Some("uploaded") {
            return Err("invalid asset");
        }
        match name {
            "update-v2-manifest.json"
                if manifest.is_none() && (1..=1024 * 1024).contains(&size) =>
            {
                manifest = Some(url.to_owned())
            }
            "update-v2-manifest.sig" if signature.is_none() && size == 64 => {
                signature = Some(url.to_owned())
            }
            "Mini-Monitor.zip" if !archive && (1..=1024 * 1024 * 1024).contains(&size) => {
                archive = true
            }
            _ => return Err("invalid asset"),
        }
    }
    if !archive {
        return Err("missing release archive");
    }
    Ok(LatestRelease {
        version: version.to_owned(),
        manifest_url: manifest.ok_or("missing release manifest")?,
        signature_url: signature.ok_or("missing release signature")?,
    })
}
/// Can only be constructed after verification against the pinned release key.
pub struct VerifiedManifest(UpdateManifest);
impl std::ops::Deref for VerifiedManifest {
    type Target = UpdateManifest;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

pub struct PreparedHandoff {
    stage: tempfile::TempDir,
    journal_sha256: String,
    helper_sha256: String,
}

#[cfg(windows)]
fn validate_config_path(
    install: &std::path::Path,
    path: &std::path::Path,
) -> Result<(), &'static str> {
    use std::os::windows::fs::MetadataExt;
    if !path.is_absolute() {
        return Err("invalid config path");
    }
    for (index, part) in path.ancestors().enumerate() {
        let meta = std::fs::symlink_metadata(part).map_err(|_| "invalid config path")?;
        if meta.file_type().is_symlink()
            || meta.file_attributes() & 0x400 != 0
            || (index == 0 && !meta.is_file())
            || (index != 0 && !meta.is_dir())
        {
            return Err("invalid config path");
        }
    }
    let resolved = std::fs::canonicalize(path).map_err(|_| "invalid config path")?;
    let root = std::fs::canonicalize(install).map_err(|_| "invalid install root")?;
    let resolved = resolved.to_string_lossy().to_lowercase();
    let root = root.to_string_lossy().to_lowercase();
    if resolved == root || resolved.starts_with(&(root + "\\")) {
        return Err("config inside install");
    }
    Ok(())
}
impl PreparedHandoff {
    /// Starts the existing hash-pinned helper, retaining staging only after spawn succeeds.
    #[cfg(windows)]
    pub fn launch(self) -> Result<std::path::PathBuf, &'static str> {
        use std::os::windows::process::CommandExt;
        use windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW;
        let hash = |path: &std::path::Path| -> Result<String, &'static str> {
            let file = std::fs::File::open(path).map_err(|_| "handoff unavailable")?;
            let bytes = read_bounded(file, 1024 * 1024)?;
            Ok(format!("{:x}", Sha256::digest(bytes)))
        };
        if hash(&self.journal_path())? != self.journal_sha256
            || hash(&self.stage_path().join("Apply-Update.ps1"))? != self.helper_sha256
        {
            return Err("handoff changed");
        }
        let mut buffer = [0u16; 32768];
        let length =
            unsafe { GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
        if length == 0 || length >= buffer.len() {
            return Err("system runtime unavailable");
        }
        let system = std::path::PathBuf::from(
            String::from_utf16(&buffer[..length]).map_err(|_| "system runtime unavailable")?,
        );
        let powershell = system.join("WindowsPowerShell/v1.0/powershell.exe");
        std::process::Command::new(&powershell)
            .args([
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ])
            .arg(self.stage_path().join("Apply-Update.ps1"))
            .arg("-JournalPath")
            .arg(self.journal_path())
            .arg("-JournalSha256")
            .arg(&self.journal_sha256)
            .arg("-HelperSha256")
            .arg(&self.helper_sha256)
            .arg("-ParentPid")
            .arg(std::process::id().to_string())
            .current_dir(self.stage_path())
            .env(
                "PSModulePath",
                system.join("WindowsPowerShell/v1.0/Modules"),
            )
            .env_remove("PYTEST_CURRENT_TEST")
            .env_remove("MINI_MONITOR_UPDATE_TEST_FAIL_AFTER_SWAP")
            .env_remove("MINI_MONITOR_UPDATE_TEST_NO_NOTICE")
            .creation_flags(0x08000000)
            .spawn()
            .map_err(|_| "update helper unavailable")?;
        Ok(self.stage.into_path())
    }
    pub fn stage_path(&self) -> &std::path::Path {
        self.stage.path()
    }
    pub fn journal_path(&self) -> std::path::PathBuf {
        self.stage.path().join("update-journal.json")
    }
    pub fn journal_sha256(&self) -> &str {
        &self.journal_sha256
    }
    pub fn helper_sha256(&self) -> &str {
        &self.helper_sha256
    }
}

/// Prepare but do not launch an update helper or modify the existing installation.
#[cfg(windows)]
pub fn prepare_official_handoff(
    stage: tempfile::TempDir,
    install: &std::path::Path,
    manifest: &VerifiedManifest,
    archive: &std::path::Path,
    helper: &std::path::Path,
    config: Option<&std::path::Path>,
) -> Result<PreparedHandoff, &'static str> {
    use ring::rand::{SecureRandom, SystemRandom};
    let stage_path = stage.path();
    let parent = install.parent().ok_or("invalid install root")?;
    if !stage_path
        .file_name()
        .and_then(|v| v.to_str())
        .is_some_and(|v| v.starts_with(".mini-monitor-update-"))
        || stage_path
            .parent()
            .and_then(|p| std::fs::canonicalize(p).ok())
            != std::fs::canonicalize(parent).ok()
    {
        return Err("invalid stage location");
    }
    let old = verify_install_inventory(install)?;
    let staged_root = stage_path.join("Mini-Monitor");
    let staged = verify_install_inventory(&staged_root)?;
    fn file_hash(path: &std::path::Path) -> Result<String, &'static str> {
        let mut file = std::fs::File::open(path).map_err(|_| "file unavailable")?;
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 64 * 1024];
        loop {
            let n = file.read(&mut buffer).map_err(|_| "file unavailable")?;
            if n == 0 {
                break;
            }
            hash.update(&buffer[..n]);
        }
        Ok(format!("{:x}", hash.finalize()))
    }
    let inventory_hash = file_hash(&staged_root.join("SHA256SUMS.txt"))?;
    let mut tree: BTreeMap<String, String> = staged
        .into_iter()
        .map(|(path, digest)| {
            (
                format!("mini-monitor/{}", path.to_ascii_lowercase()),
                digest,
            )
        })
        .collect();
    tree.insert("mini-monitor/sha256sums.txt".into(), inventory_hash);
    let expected: BTreeMap<String, String> = manifest
        .files
        .iter()
        .map(|(path, digest)| (path.to_ascii_lowercase(), digest.clone()))
        .collect();
    if tree != expected {
        return Err("staged files differ from signed inventory");
    }
    let relative = helper
        .strip_prefix(install)
        .map_err(|_| "helper outside install")?;
    let components = relative
        .components()
        .map(|c| match c {
            std::path::Component::Normal(v) => v.to_str().ok_or("invalid helper"),
            _ => Err("invalid helper"),
        })
        .collect::<Result<Vec<_>, _>>()?;
    let relative = components.join("/");
    let expected_helper = old
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case(&relative))
        .map(|(_, hash)| hash)
        .ok_or("unverified helper")?;
    let helper_bytes = read_bounded(
        std::fs::File::open(helper).map_err(|_| "helper unavailable")?,
        1024 * 1024,
    )?;
    let helper_hash = format!("{:x}", Sha256::digest(&helper_bytes));
    if &helper_hash != expected_helper {
        return Err("helper changed");
    }
    if let Some(path) = config {
        validate_config_path(install, path)?;
    }
    let mut random = [0u8; 16];
    SystemRandom::new()
        .fill(&mut random)
        .map_err(|_| "random unavailable")?;
    let nonce = random
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    let backup = parent.join(format!(".mini-monitor-backup-{nonce}"));
    if backup.exists() {
        return Err("backup target exists");
    }
    let mut source = std::fs::File::open(archive).map_err(|_| "archive unavailable")?;
    let mut staged_archive = std::fs::OpenOptions::new()
        .write(true)
        .read(true)
        .create_new(true)
        .open(stage_path.join("release.zip"))
        .map_err(|_| "stage write failed")?;
    copy_archive_bytes(
        &mut source,
        &mut staged_archive,
        manifest.archive_size,
        &manifest.archive_sha256,
        || false,
    )?;
    staged_archive
        .sync_all()
        .map_err(|_| "stage write failed")?;
    verify_official_archive(&mut staged_archive, manifest)?;
    let mut staged_helper = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(stage_path.join("Apply-Update.ps1"))
        .map_err(|_| "stage write failed")?;
    staged_helper
        .write_all(&helper_bytes)
        .and_then(|_| staged_helper.sync_all())
        .map_err(|_| "stage write failed")?;
    let stringify =
        |p: &std::path::Path| p.to_str().map(str::to_owned).ok_or("invalid update path");
    let record = serde_json::json!({"schema_version":1,"state":"prepared","install_root":stringify(install)?,"staged_root":stringify(&staged_root)?,"backup_root":stringify(&backup)?,"ack_path":stringify(&stage_path.join("startup-ack.json"))?,"nonce":nonce,"old_files":old,"new_files":manifest.files,"helper_sha256":helper_hash,"archive_sha256":manifest.archive_sha256,"version":manifest.version,"config_path":config.map(stringify).transpose()?,"restart_args":[],"old_inventory_sha256":file_hash(&install.join("SHA256SUMS.txt"))?});
    let bytes = serde_json::to_vec(&record).map_err(|_| "journal encoding failed")?;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(stage_path.join("update-journal.json"))
        .map_err(|_| "stage write failed")?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|_| "stage write failed")?;
    let journal_sha256 = format!("{:x}", Sha256::digest(&bytes));
    Ok(PreparedHandoff {
        stage,
        journal_sha256,
        helper_sha256: helper_hash,
    })
}

/// Acknowledge only an update-owned handoff after the application is actually ready.
#[cfg(windows)]
pub fn acknowledge_startup(
    executable: &std::path::Path,
    ack: &std::path::Path,
    nonce: &str,
    version: &str,
) -> Result<bool, &'static str> {
    use std::os::windows::fs::MetadataExt;
    use std::path::Path;
    if nonce.len() != 32
        || !nonce
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || !stable_version(version)
    {
        return Ok(false);
    }
    let Some(install) = executable.parent() else {
        return Ok(false);
    };
    let Some(stage) = ack.parent() else {
        return Ok(false);
    };
    let Some(parent) = install.parent() else {
        return Ok(false);
    };
    if !executable.is_absolute()
        || !ack.is_absolute()
        || executable.file_name().and_then(|v| v.to_str()) != Some("Mini-Monitor.exe")
        || install.file_name().and_then(|v| v.to_str()) != Some("Mini-Monitor")
        || ack.file_name().and_then(|v| v.to_str()) != Some("startup-ack.json")
        || !stage
            .file_name()
            .and_then(|v| v.to_str())
            .is_some_and(|v| v.starts_with(".mini-monitor-update-"))
    {
        return Ok(false);
    }
    let regular = |path: &Path, dir: bool| -> bool {
        std::fs::symlink_metadata(path).is_ok_and(|m| {
            (if dir { m.is_dir() } else { m.is_file() })
                && !m.file_type().is_symlink()
                && m.file_attributes() & 0x400 == 0
        })
    };
    let canonical = |p: &Path| std::fs::canonicalize(p).ok();
    let stage_parent = stage.parent().and_then(canonical);
    if !regular(executable, false)
        || !regular(install, true)
        || !regular(stage, true)
        || !regular(parent, true)
        || stage_parent.is_none()
        || stage_parent != canonical(parent)
    {
        return Ok(false);
    }
    let journal = stage.join("update-journal.json");
    if !regular(&journal, false) || ack.exists() {
        return Ok(false);
    }
    let meta = std::fs::metadata(&journal).map_err(|_| "update context unavailable")?;
    if meta.len() > 1024 * 1024 {
        return Ok(false);
    }
    let file = std::fs::File::open(&journal).map_err(|_| "update context unavailable")?;
    let bytes = read_bounded(file, 1024 * 1024).map_err(|_| "update context invalid")?;
    #[derive(serde::Deserialize)]
    struct Handoff {
        schema_version: u64,
        state: String,
        nonce: String,
        version: String,
        install_root: std::path::PathBuf,
        ack_path: std::path::PathBuf,
        backup_root: std::path::PathBuf,
    }
    let Ok(record) = serde_json::from_slice::<Handoff>(&bytes) else {
        return Ok(false);
    };
    if record.schema_version != 1
        || record.state != "new_installed"
        || record.nonce != nonce
        || record.version != version
    {
        return Ok(false);
    }
    let record_install = record.install_root;
    let record_ack = record.ack_path;
    let backup = record.backup_root;
    if canonical(&record_install) != canonical(install)
        || record_ack.file_name() != ack.file_name()
        || record_ack.parent().and_then(canonical) != canonical(stage)
        || !regular(&backup, true)
        || backup.parent().and_then(canonical) != canonical(parent)
        || !backup
            .file_name()
            .and_then(|v| v.to_str())
            .is_some_and(|v| v.starts_with(".mini-monitor-backup-"))
    {
        return Ok(false);
    }
    let payload =
        serde_json::to_vec(&serde_json::json!({"nonce":nonce,"ready":true,"version":version}))
            .map_err(|_| "update context invalid")?;
    let mut temp = tempfile::Builder::new()
        .prefix("startup-ack-")
        .tempfile_in(stage)
        .map_err(|_| "update acknowledgement failed")?;
    temp.write_all(&payload)
        .and_then(|_| temp.as_file().sync_all())
        .map_err(|_| "update acknowledgement failed")?;
    temp.persist_noclobber(ack)
        .map_err(|_| "update acknowledgement failed")?;
    Ok(true)
}

// SubjectPublicKeyInfo in assets/update-public-key.pem (Ed25519 raw public key).
const RELEASE_PUBLIC_KEY: [u8; 32] = [
    0xd3, 0x71, 0xf7, 0x0b, 0x85, 0x2b, 0xca, 0x41, 0xd9, 0xab, 0xc8, 0xaf, 0x18, 0xc1, 0xd5, 0x57,
    0x44, 0x30, 0x39, 0x9c, 0xa1, 0xb8, 0x72, 0x9f, 0x65, 0x89, 0x30, 0xa3, 0x8f, 0x11, 0x1f, 0x45,
];
pub fn verify_official_manifest(
    payload: &[u8],
    signature: &[u8],
) -> Result<VerifiedManifest, &'static str> {
    verify_manifest(payload, signature, &RELEASE_PUBLIC_KEY).map(VerifiedManifest)
}
pub fn verify_official_archive<R: Read + Seek>(
    input: R,
    manifest: &VerifiedManifest,
) -> Result<(), &'static str> {
    verify_archive(input, &manifest.0)
}

fn ensure_staging_directory(
    root: &std::path::Path,
    relative: &std::path::Path,
) -> Result<(), &'static str> {
    let mut current = root.to_path_buf();
    for part in relative.components() {
        let std::path::Component::Normal(name) = part else {
            return Err("unsafe staging path");
        };
        current.push(name);
        match std::fs::create_dir(&current) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("staging write failed"),
        }
        let meta = std::fs::symlink_metadata(&current).map_err(|_| "staging write failed")?;
        if !meta.is_dir() || meta.file_type().is_symlink() {
            return Err("unsafe staging path");
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if meta.file_attributes() & 0x400 != 0 {
                return Err("unsafe staging path");
            }
        }
    }
    Ok(())
}

/// Read-only validation of the existing installation before any replacement.
pub fn verify_install_inventory(
    root: &std::path::Path,
) -> Result<Vec<(String, String)>, &'static str> {
    if !root.is_absolute() || root.file_name().and_then(|v| v.to_str()) != Some("Mini-Monitor") {
        return Err("invalid install root");
    }
    verify_inventory_tree(root)
}

pub(crate) fn verify_backup_inventory(
    root: &std::path::Path,
) -> Result<Vec<(String, String)>, &'static str> {
    let name = root
        .file_name()
        .and_then(|v| v.to_str())
        .ok_or("invalid backup root")?;
    let id = name
        .strip_prefix(".mini-monitor-backup-")
        .ok_or("invalid backup root")?;
    if !root.is_absolute() || id.len() != 32 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("invalid backup root");
    }
    verify_inventory_tree(root)
}

fn verify_inventory_tree(root: &std::path::Path) -> Result<Vec<(String, String)>, &'static str> {
    fn regular_path(path: &std::path::Path, dir: bool) -> bool {
        std::fs::symlink_metadata(path).is_ok_and(|m| {
            let valid = (if dir { m.is_dir() } else { m.is_file() }) && !m.file_type().is_symlink();
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                valid && m.file_attributes() & 0x400 == 0
            }
            #[cfg(not(windows))]
            {
                valid
            }
        })
    }
    for path in root.ancestors() {
        if !regular_path(path, true) {
            return Err("unsafe install root");
        }
    }
    let inventory = root.join("SHA256SUMS.txt");
    if !regular_path(&inventory, false) {
        return Err("install inventory unavailable");
    }
    let bytes = read_bounded(
        std::fs::File::open(&inventory).map_err(|_| "install inventory unavailable")?,
        4 * 1024 * 1024,
    )?;
    let text = std::str::from_utf8(&bytes).map_err(|_| "invalid install inventory")?;
    let mut expected = BTreeMap::new();
    for line in text.lines() {
        let (hash, relative) = line.split_once("  ").ok_or("invalid install inventory")?;
        let key =
            safe_member(&format!("Mini-Monitor/{relative}")).ok_or("invalid install inventory")?;
        if !sha256_text(hash)
            || expected
                .insert(key, (relative.to_owned(), hash.to_owned()))
                .is_some()
        {
            return Err("invalid install inventory");
        }
        if expected.len() > 20_000 {
            return Err("install inventory too large");
        }
    }
    if !expected.contains_key("mini-monitor/mini-monitor.exe") {
        return Err("missing executable");
    }
    let mut pending = vec![root.to_path_buf()];
    let mut seen = BTreeSet::new();
    let mut buffer = [0u8; 64 * 1024];
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory).map_err(|_| "install inventory unavailable")? {
            let entry = entry.map_err(|_| "install inventory unavailable")?;
            let path = entry.path();
            let meta =
                std::fs::symlink_metadata(&path).map_err(|_| "install inventory unavailable")?;
            if meta.file_type().is_symlink() {
                return Err("unsafe install path");
            }
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if meta.file_attributes() & 0x400 != 0 {
                    return Err("unsafe install path");
                }
            }
            if meta.is_dir() {
                let relative = path
                    .strip_prefix(root)
                    .map_err(|_| "unsafe install path")?
                    .to_string_lossy()
                    .replace('\\', "/")
                    .to_lowercase();
                let prefix = format!("mini-monitor/{relative}/");
                if !expected.keys().any(|key| key.starts_with(&prefix)) {
                    return Err("unexpected installed directory");
                }
                pending.push(path);
                if pending.len() > 20_000 {
                    return Err("install inventory too large");
                }
                continue;
            }
            if !meta.is_file() {
                return Err("unsafe install path");
            }
            if path == inventory {
                continue;
            }
            let rel = path.strip_prefix(root).map_err(|_| "unsafe install path")?;
            let parts = rel
                .components()
                .map(|c| match c {
                    std::path::Component::Normal(v) => v.to_str().ok_or("unsafe install path"),
                    _ => Err("unsafe install path"),
                })
                .collect::<Result<Vec<_>, _>>()?;
            let name = parts.join("/");
            let key = safe_member(&format!("Mini-Monitor/{name}")).ok_or("unsafe install path")?;
            let (_, hash) = expected.get(&key).ok_or("unexpected installed file")?;
            if !seen.insert(key) {
                return Err("duplicate installed file");
            }
            let mut file = std::fs::File::open(&path).map_err(|_| "installed file unavailable")?;
            let mut digest = Sha256::new();
            loop {
                let n = file
                    .read(&mut buffer)
                    .map_err(|_| "installed file unavailable")?;
                if n == 0 {
                    break;
                }
                digest.update(&buffer[..n]);
            }
            if format!("{:x}", digest.finalize()) != *hash {
                return Err("installed file changed");
            }
        }
    }
    if seen.len() != expected.len() {
        return Err("installed files missing");
    }
    let mut result: Vec<_> = expected.into_values().collect();
    result.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(result)
}

/// Build an isolated staged tree; no live installation path is written or replaced.
pub fn stage_official_archive<R: Read + Seek>(
    input: &mut R,
    manifest: &VerifiedManifest,
    parent: &std::path::Path,
    cancelled: impl Fn() -> bool,
) -> Result<tempfile::TempDir, &'static str> {
    if cancelled() {
        return Err("staging cancelled");
    }
    if !parent.is_absolute() {
        return Err("invalid staging directory");
    }
    for component in parent.ancestors() {
        let meta = std::fs::symlink_metadata(component).map_err(|_| "invalid staging directory")?;
        if !meta.is_dir() || meta.file_type().is_symlink() {
            return Err("invalid staging directory");
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if meta.file_attributes() & 0x400 != 0 {
                return Err("invalid staging directory");
            }
        }
    }
    verify_official_archive(&mut *input, manifest)?;
    if cancelled() {
        return Err("staging cancelled");
    }
    input
        .seek(SeekFrom::Start(0))
        .map_err(|_| "archive read failed")?;
    let mut zip = zip::ZipArchive::new(input).map_err(|_| "invalid archive")?;
    if zip.len() > 40_000 {
        return Err("archive too large");
    }
    let stage = tempfile::Builder::new()
        .prefix(".mini-monitor-update-")
        .tempdir_in(parent)
        .map_err(|_| "staging unavailable")?;
    let root = stage.path().join("Mini-Monitor");
    std::fs::create_dir(&root).map_err(|_| "staging unavailable")?;
    let expected: BTreeMap<String, &str> = manifest
        .files
        .iter()
        .map(|(name, hash)| (name.to_ascii_lowercase(), hash.as_str()))
        .collect();
    let mut seen = BTreeSet::new();
    let mut matched = 0usize;
    let mut total = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    for index in 0..zip.len() {
        if cancelled() {
            return Err("staging cancelled");
        }
        let mut file = zip.by_index(index).map_err(|_| "invalid archive")?;
        let name = file.name().trim_end_matches('/');
        let key = safe_member(name).ok_or("unsafe archive path")?;
        if !seen.insert(key.clone()) {
            return Err("duplicate archive path");
        }
        let kind = file.unix_mode().unwrap_or(0) & 0o170000;
        if !matches!(kind, 0 | 0o100000 | 0o040000) {
            return Err("archive link forbidden");
        }
        let relative = name
            .strip_prefix("Mini-Monitor/")
            .ok_or("unsafe archive path")?;
        let relative = std::path::Path::new(relative);
        let target = root.join(relative);
        if file.is_dir() {
            if expected.contains_key(&key) {
                return Err("archive path conflict");
            }
            ensure_staging_directory(&root, relative)?;
            continue;
        }
        if kind == 0o040000 {
            return Err("archive path conflict");
        }
        let expected_hash = expected.get(&key).ok_or("unsigned archive file")?;
        total = total
            .checked_add(file.size())
            .filter(|n| *n <= 3 * 1024 * 1024 * 1024)
            .ok_or("archive too large")?;
        ensure_staging_directory(&root, relative.parent().ok_or("unsafe archive path")?)?;
        let mut output = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
            .map_err(|_| "staging write failed")?;
        let mut hash = Sha256::new();
        let mut written = 0u64;
        loop {
            if cancelled() {
                return Err("staging cancelled");
            }
            let n = file.read(&mut buffer).map_err(|_| "archive read failed")?;
            if n == 0 {
                break;
            }
            written = written
                .checked_add(n as u64)
                .filter(|n| *n <= file.size())
                .ok_or("archive size mismatch")?;
            output
                .write_all(&buffer[..n])
                .map_err(|_| "staging write failed")?;
            hash.update(&buffer[..n]);
        }
        if written != file.size() || format!("{:x}", hash.finalize()) != **expected_hash {
            return Err("file hash mismatch");
        }
        output.sync_all().map_err(|_| "staging write failed")?;
        matched += 1;
    }
    if matched != expected.len() {
        return Err("archive inventory mismatch");
    }
    Ok(stage)
}

/// Checks bytes and inventory without extracting or changing the installed application.
pub fn verify_archive<R: Read + Seek>(
    mut input: R,
    manifest: &UpdateManifest,
) -> Result<(), &'static str> {
    if input
        .seek(SeekFrom::End(0))
        .map_err(|_| "archive read failed")?
        != manifest.archive_size
    {
        return Err("archive size mismatch");
    }
    input
        .seek(SeekFrom::Start(0))
        .map_err(|_| "archive read failed")?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let n = input.read(&mut buffer).map_err(|_| "archive read failed")?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    if format!("{:x}", hash.finalize()) != manifest.archive_sha256 {
        return Err("archive hash mismatch");
    }
    input
        .seek(SeekFrom::Start(0))
        .map_err(|_| "archive read failed")?;
    let mut zip = zip::ZipArchive::new(input).map_err(|_| "invalid archive")?;
    if zip.len() > 40_000 {
        return Err("archive too large");
    }
    let expected: BTreeMap<String, &str> = manifest
        .files
        .iter()
        .map(|(name, hash)| (name.to_ascii_lowercase(), hash.as_str()))
        .collect();
    if expected.len() != manifest.files.len()
        || !expected.contains_key("mini-monitor/mini-monitor.exe")
    {
        return Err("invalid inventory");
    }
    let mut seen = BTreeSet::new();
    let mut matched = 0usize;
    let mut total = 0u64;
    for index in 0..zip.len() {
        let mut file = zip.by_index(index).map_err(|_| "invalid archive")?;
        let name = file.name().trim_end_matches('/');
        let key = safe_member(name).ok_or("unsafe archive path")?;
        if !seen.insert(key.clone()) {
            return Err("duplicate archive path");
        }
        let kind = file.unix_mode().unwrap_or(0) & 0o170000;
        if !matches!(kind, 0 | 0o100000 | 0o040000) {
            return Err("archive link forbidden");
        }
        if file.is_dir() {
            if expected.contains_key(&key) {
                return Err("archive path conflict");
            }
            continue;
        }
        if kind == 0o040000 {
            return Err("archive path conflict");
        }
        let expected_hash = expected.get(&key).ok_or("unsigned archive file")?;
        total = total
            .checked_add(file.size())
            .filter(|n| *n <= 3 * 1024 * 1024 * 1024)
            .ok_or("archive too large")?;
        let mut written = 0u64;
        let mut hash = Sha256::new();
        loop {
            let n = file.read(&mut buffer).map_err(|_| "archive read failed")?;
            if n == 0 {
                break;
            }
            written = written
                .checked_add(n as u64)
                .filter(|n| *n <= file.size())
                .ok_or("archive size mismatch")?;
            hash.update(&buffer[..n]);
        }
        if written != file.size() || format!("{:x}", hash.finalize()) != **expected_hash {
            return Err("file hash mismatch");
        }
        matched += 1;
    }
    if matched != expected.len() {
        return Err("archive inventory mismatch");
    }
    Ok(())
}

fn sha256_text(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn stable_version(s: &str) -> bool {
    let parts: Vec<_> = s.split('.').collect();
    parts.len() == 3
        && parts.iter().all(|p| {
            !p.is_empty()
                && (p.len() == 1 || !p.starts_with('0'))
                && p.bytes().all(|b| b.is_ascii_digit())
                && p.parse::<u64>().is_ok()
        })
}
pub fn is_newer_release(current: &str, candidate: &str) -> Result<bool, &'static str> {
    fn parse(s: &str) -> Result<[u64; 3], &'static str> {
        if !stable_version(s) {
            return Err("invalid stable version");
        }
        let mut values = s
            .split('.')
            .map(|v| v.parse::<u64>().map_err(|_| "invalid stable version"));
        Ok([
            values.next().ok_or("invalid stable version")??,
            values.next().ok_or("invalid stable version")??,
            values.next().ok_or("invalid stable version")??,
        ])
    }
    Ok(parse(candidate)? > parse(current)?)
}
fn safe_member(path: &str) -> Option<String> {
    if !path.is_ascii() || !path.starts_with("Mini-Monitor/") {
        return None;
    }
    let mut parts = path.split('/');
    parts.next()?;
    for part in parts {
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.ends_with([' ', '.'])
            || part
                .bytes()
                .any(|b| !(0x20..=0x7e).contains(&b) || b"<>:\\|?*\"~".contains(&b))
        {
            return None;
        }
        let stem = part.split('.').next()?.to_ascii_uppercase();
        if ["CON", "PRN", "AUX", "NUL"].contains(&stem.as_str())
            || ((stem.starts_with("COM") || stem.starts_with("LPT"))
                && stem.len() == 4
                && matches!(stem.as_bytes()[3], b'1'..=b'9'))
        {
            return None;
        }
    }
    Some(path.to_ascii_lowercase())
}

/// Parse only canonical, authenticated metadata for the existing stable release channel.
pub fn verify_manifest(
    payload: &[u8],
    signature: &[u8],
    public_key: &[u8],
) -> Result<UpdateManifest, &'static str> {
    verify_signature(payload, signature, public_key)?;
    let doc: serde_json::Value = serde_json::from_slice(payload).map_err(|_| "invalid metadata")?;
    if serde_json::to_vec(&doc).map_err(|_| "invalid metadata")? != payload {
        return Err("noncanonical metadata");
    }
    let obj = doc.as_object().ok_or("invalid metadata")?;
    if obj.len() != 7
        || obj.get("schema_version").and_then(|v| v.as_u64()) != Some(1)
        || obj.get("channel").and_then(|v| v.as_str()) != Some("stable")
    {
        return Err("invalid schema");
    }
    let version = obj
        .get("version")
        .and_then(|v| v.as_str())
        .filter(|s| stable_version(s))
        .ok_or("invalid version")?;
    let archive_url = obj
        .get("archive_url")
        .and_then(|v| v.as_str())
        .ok_or("invalid url")?;
    let url = reqwest::Url::parse(archive_url).map_err(|_| "invalid url")?;
    let expected =
        format!("/contentriumkorea/mini-monitor/releases/download/v{version}/Mini-Monitor.zip");
    if url.as_str() != archive_url
        || url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != expected
    {
        return Err("invalid url");
    }
    let digest = obj
        .get("archive_sha256")
        .and_then(|v| v.as_str())
        .filter(|s| sha256_text(s))
        .ok_or("invalid hash")?;
    let size = obj
        .get("archive_size")
        .and_then(|v| v.as_u64())
        .filter(|n| (1..=1024 * 1024 * 1024).contains(n))
        .ok_or("invalid size")?;
    let records = obj
        .get("files")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty() && a.len() <= 20_000)
        .ok_or("invalid inventory")?;
    let mut files = Vec::with_capacity(records.len());
    let mut paths = BTreeSet::new();
    for record in records {
        let pair = record
            .as_array()
            .filter(|a| a.len() == 2)
            .ok_or("invalid inventory")?;
        let path = pair[0].as_str().ok_or("invalid path")?;
        let hash = pair[1]
            .as_str()
            .filter(|s| sha256_text(s))
            .ok_or("invalid hash")?;
        let key = safe_member(path).ok_or("invalid path")?;
        if !paths.insert(key)
            || files
                .last()
                .is_some_and(|(last, _): &(String, String)| last.as_str() >= path)
        {
            return Err("duplicate or unordered path");
        }
        files.push((path.to_owned(), hash.to_owned()));
    }
    for path in &paths {
        let mut prefix = path.as_str();
        while let Some((parent, _)) = prefix.rsplit_once('/') {
            if paths.contains(parent) {
                return Err("conflicting path");
            }
            prefix = parent;
        }
    }
    Ok(UpdateManifest {
        version: version.to_owned(),
        archive_url: archive_url.to_owned(),
        archive_sha256: digest.to_owned(),
        archive_size: size,
        files,
    })
}

/// Verify the exact bytes that were signed, before parsing or using any metadata.
pub fn verify_signature(
    payload: &[u8],
    signature: &[u8],
    public_key: &[u8],
) -> Result<(), &'static str> {
    if payload.is_empty()
        || payload.len() > 1024 * 1024
        || signature.len() != 64
        || public_key.len() != 32
    {
        return Err("invalid signed metadata");
    }
    UnparsedPublicKey::new(&ED25519, public_key)
        .verify(payload, signature)
        .map_err(|_| "invalid update signature")
}

#[cfg(all(test, windows))]
mod config_path_tests {
    #[test]
    fn changed_handoff_never_launches_a_helper() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().to_path_buf();
        std::fs::write(path.join("update-journal.json"), b"changed").unwrap();
        let prepared = super::PreparedHandoff {
            stage: temp,
            journal_sha256: "0".repeat(64),
            helper_sha256: "0".repeat(64),
        };
        assert!(prepared.launch().is_err());
        assert!(!path.exists());
    }
    #[test]
    fn config_must_be_a_regular_file_outside_install_tree() {
        let parent = tempfile::tempdir().unwrap();
        let install = parent.path().join("Mini-Monitor");
        std::fs::create_dir(&install).unwrap();
        let inside = install.join("settings.json");
        std::fs::write(&inside, b"{}").unwrap();
        let outside = parent.path().join("settings.json");
        std::fs::write(&outside, b"{}").unwrap();
        assert!(super::validate_config_path(&install, &inside).is_err());
        assert!(super::validate_config_path(&install, &install).is_err());
        assert!(super::validate_config_path(&install, &outside).is_ok());
        assert!(
            super::validate_config_path(&install, std::path::Path::new("relative.json")).is_err()
        );
    }
}

#[cfg(all(test, windows))]
mod handoff_success_tests {
    use super::*;

    #[test]
    #[ignore = "runs a built native host with a deliberately invalid local runtime only inside a temporary installation"]
    fn native_host_with_broken_local_runtime_is_rolled_back_without_ack() {
        use crate::single_instance::{InstanceGuard, APP_MUTEX};
        let guard = InstanceGuard::try_acquire(APP_MUTEX)
            .unwrap()
            .expect("close the development app before this isolated test");
        drop(guard);
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let parent = tempfile::tempdir().unwrap();
        let install = parent.path().join("Mini-Monitor");
        std::fs::create_dir_all(install.join("scripts")).unwrap();
        let helper_bytes =
            std::fs::read(root.parent().unwrap().join("scripts/Apply-Update.ps1")).unwrap();
        let helper = install.join("scripts/Apply-Update.ps1");
        std::fs::write(&helper, &helper_bytes).unwrap();
        std::fs::write(install.join("Mini-Monitor.exe"), b"old fixture").unwrap();
        let hash = |bytes: &[u8]| format!("{:x}", Sha256::digest(bytes));
        std::fs::write(
            install.join("SHA256SUMS.txt"),
            format!(
                "{}  Mini-Monitor.exe\n{}  scripts/Apply-Update.ps1\n",
                hash(b"old fixture"),
                hash(&helper_bytes)
            ),
        )
        .unwrap();
        let config = parent.path().join("config.json");
        std::fs::write(&config, b"{}").unwrap();
        let executable = std::fs::read(root.join("target/release/mini-monitor-native.exe"))
            .expect("build release host first");
        let icon = std::fs::read(root.parent().unwrap().join("assets/app-icon.ico")).unwrap();
        let mut files = vec![
            ("Mini-Monitor.exe", executable),
            ("assets/app-icon.ico", icon),
            (
                "third_party/codex-app-server/codex-app-server.exe",
                b"deliberately invalid runtime".to_vec(),
            ),
        ];
        let inventory = files
            .iter()
            .map(|(name, bytes)| format!("{}  {name}\n", hash(bytes)))
            .collect::<String>();
        files.push(("SHA256SUMS.txt", inventory.into_bytes()));
        let archive_path = parent.path().join("fixture.zip");
        let mut zip = zip::ZipWriter::new(std::fs::File::create(&archive_path).unwrap());
        for (name, bytes) in &files {
            zip.start_file(
                format!("Mini-Monitor/{name}"),
                zip::write::FileOptions::default(),
            )
            .unwrap();
            zip.write_all(bytes).unwrap();
        }
        zip.finish().unwrap();
        let archive_bytes = std::fs::read(&archive_path).unwrap();
        let manifest = VerifiedManifest(UpdateManifest {
            version: env!("CARGO_PKG_VERSION").into(),
            archive_url: String::new(),
            archive_sha256: hash(&archive_bytes),
            archive_size: archive_bytes.len() as u64,
            files: files
                .iter()
                .map(|(name, bytes)| (format!("Mini-Monitor/{name}"), hash(bytes)))
                .collect(),
        });
        let mut archive = std::fs::File::open(&archive_path).unwrap();
        let stage =
            stage_official_archive(&mut archive, &manifest, parent.path(), || false).unwrap();
        let prepared = prepare_official_handoff(
            stage,
            &install,
            &manifest,
            &archive_path,
            &helper,
            Some(&config),
        )
        .unwrap();
        let powershell = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
            .join("System32/WindowsPowerShell/v1.0/powershell.exe");
        let result = std::process::Command::new(&powershell)
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
            .env("PYTEST_CURRENT_TEST", "isolated-native-fault-test")
            .env("MINI_MONITOR_UPDATE_TEST_NO_NOTICE", "1")
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .output()
            .unwrap();
        // A regressed host can ACK and remain alive; stop only this exact disposable fixture.
        let cleanup=std::process::Command::new(&powershell).args(["-NoProfile","-NonInteractive","-Command", "$p = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $env:MM_FIXTURE_EXE }); foreach ($item in $p) { Stop-Process -Id $item.ProcessId -ErrorAction SilentlyContinue; Wait-Process -Id $item.ProcessId -Timeout 5 -ErrorAction SilentlyContinue }"]).env("MM_FIXTURE_EXE",install.join("Mini-Monitor.exe")).output().unwrap();
        assert!(cleanup.status.success());
        assert_eq!(
            result.status.code(),
            Some(5),
            "broken local runtime was incorrectly acknowledged"
        );
        let journal: serde_json::Value =
            serde_json::from_slice(&std::fs::read(prepared.journal_path()).unwrap()).unwrap();
        assert_eq!(
            journal["state"],
            "rolled_back_no_ack",
            "recovery failure type={} code={} old_restored={}",
            journal["recovery_failure_type"],
            journal["recovery_failure_hresult"],
            std::fs::read(install.join("Mini-Monitor.exe")).is_ok_and(|v| v == b"old fixture")
        );
        assert_eq!(
            std::fs::read(install.join("Mini-Monitor.exe")).unwrap(),
            b"old fixture"
        );
        assert!(!prepared.stage_path().join("startup-ack.json").exists());
    }

    #[test]
    #[ignore = "requires a separately built test fixture and performs a swap only within a temporary directory"]
    fn helper_accepts_rust_startup_ack_and_keeps_previous_backup() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let example = root.join("target/debug/examples/ack_fixture.exe");
        let executable = std::fs::read(example).expect("build ack_fixture example first");
        let helper_bytes =
            std::fs::read(root.parent().unwrap().join("scripts/Apply-Update.ps1")).unwrap();
        let temp = tempfile::tempdir().unwrap();
        let install = temp.path().join("Mini-Monitor");
        std::fs::create_dir(&install).unwrap();
        std::fs::create_dir(install.join("scripts")).unwrap();
        std::fs::write(install.join("Mini-Monitor.exe"), b"old app").unwrap();
        let helper = install.join("scripts/Apply-Update.ps1");
        std::fs::write(&helper, &helper_bytes).unwrap();
        let hash = |bytes: &[u8]| format!("{:x}", Sha256::digest(bytes));
        std::fs::write(
            install.join("SHA256SUMS.txt"),
            format!(
                "{}  Mini-Monitor.exe\n{}  scripts/Apply-Update.ps1\n",
                hash(b"old app"),
                hash(&helper_bytes)
            ),
        )
        .unwrap();
        let inventory = format!("{}  Mini-Monitor.exe\n", hash(&executable));
        let archive_path = temp.path().join("fixture.zip");
        let archive_file = std::fs::File::create(&archive_path).unwrap();
        let mut zip = zip::ZipWriter::new(archive_file);
        let options = zip::write::FileOptions::default();
        zip.start_file("Mini-Monitor/Mini-Monitor.exe", options)
            .unwrap();
        zip.write_all(&executable).unwrap();
        zip.start_file("Mini-Monitor/SHA256SUMS.txt", options)
            .unwrap();
        zip.write_all(inventory.as_bytes()).unwrap();
        zip.finish().unwrap();
        let archive_bytes = std::fs::read(&archive_path).unwrap();
        // Test-only construction: production can obtain this wrapper only through signature verification.
        let manifest = VerifiedManifest(UpdateManifest {
            version: "1.2.3".into(),
            archive_url: String::new(),
            archive_sha256: hash(&archive_bytes),
            archive_size: archive_bytes.len() as u64,
            files: vec![
                ("Mini-Monitor/Mini-Monitor.exe".into(), hash(&executable)),
                (
                    "Mini-Monitor/SHA256SUMS.txt".into(),
                    hash(inventory.as_bytes()),
                ),
            ],
        });
        let mut archive = std::fs::File::open(&archive_path).unwrap();
        let stage = stage_official_archive(&mut archive, &manifest, temp.path(), || false).unwrap();
        let prepared =
            prepare_official_handoff(stage, &install, &manifest, &archive_path, &helper, None)
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
            .env("PYTEST_CURRENT_TEST", "isolated-rust-success-test")
            .env("MINI_MONITOR_UPDATE_TEST_NO_NOTICE", "1")
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "helper did not receive valid Rust acknowledgement: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        let journal: serde_json::Value =
            serde_json::from_slice(&std::fs::read(prepared.journal_path()).unwrap()).unwrap();
        assert_eq!(journal["state"], "healthy_backup_retained");
        let backup = std::path::PathBuf::from(journal["backup_root"].as_str().unwrap());
        assert_eq!(
            std::fs::read(backup.join("Mini-Monitor.exe")).unwrap(),
            b"old app"
        );
        assert_eq!(
            std::fs::read(install.join("Mini-Monitor.exe")).unwrap(),
            executable
        );
        std::thread::sleep(std::time::Duration::from_secs(3));
    }
}
