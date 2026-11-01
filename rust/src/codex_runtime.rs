//! Integrity and environment guards for the bundled Codex app-server.
use sha2::{Digest, Sha256};
use std::{fs::File, io::Read, path::Path};

pub const BUNDLED_SHA256: &str = "bbc4400446037926e2446f36ccc74d7fa01577084e517c5f43ae2c7c7c82346b";

pub fn is_sensitive_environment_key(key: &str) -> bool {
    let key = key.to_ascii_uppercase();
    ["OPENAI_", "CODEX_", "CHATGPT_"]
        .iter()
        .any(|prefix| key.starts_with(prefix))
        && ["KEY", "TOKEN", "SECRET", "AUTH"]
            .iter()
            .any(|part| key.contains(part))
}

/// Error messages intentionally contain no paths or data from the target.
pub fn verify_runtime(path: &Path, expected_digest: &str) -> Result<(), &'static str> {
    if expected_digest.len() != 64 || !expected_digest.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("invalid digest");
    }
    let meta = std::fs::symlink_metadata(path).map_err(|_| "runtime unavailable")?;
    if !meta.is_file() || meta.file_type().is_symlink() {
        return Err("runtime integrity");
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err("runtime integrity");
        }
    }
    let mut file = File::open(path).map_err(|_| "runtime unavailable")?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|_| "runtime unavailable")?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    if format!("{:x}", hash.finalize()).eq_ignore_ascii_case(expected_digest) {
        Ok(())
    } else {
        Err("runtime integrity")
    }
}
