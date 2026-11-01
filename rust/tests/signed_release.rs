use mini_monitor_native::update::verify_signature;
use ring::signature::{Ed25519KeyPair, KeyPair};

#[test]
fn signed_update_rejects_tampering_wrong_key_and_malformed_signature() {
    let pair = Ed25519KeyPair::from_seed_unchecked(&[7; 32]).unwrap();
    let wrong = Ed25519KeyPair::from_seed_unchecked(&[8; 32]).unwrap();
    let payload = br#"{"channel":"stable","version":"1.2.3"}"#;
    let signature = pair.sign(payload);
    assert!(verify_signature(payload, signature.as_ref(), pair.public_key().as_ref()).is_ok());
    assert!(verify_signature(b"modified", signature.as_ref(), pair.public_key().as_ref()).is_err());
    assert!(verify_signature(payload, signature.as_ref(), wrong.public_key().as_ref()).is_err());
    assert!(verify_signature(
        payload,
        &signature.as_ref()[1..],
        pair.public_key().as_ref()
    )
    .is_err());
    assert!(verify_signature(&[], signature.as_ref(), pair.public_key().as_ref()).is_err());
}

#[test]
fn signed_manifest_checks_origin_schema_and_safe_unique_paths() {
    use mini_monitor_native::update::verify_manifest;
    let pair = Ed25519KeyPair::from_seed_unchecked(&[19; 32]).unwrap();
    let digest = "a".repeat(64);
    let good = serde_json::json!({"schema_version":1,"version":"1.2.3","channel":"stable", "archive_url":"https://github.com/contentriumkorea/mini-monitor/releases/download/v1.2.3/Mini-Monitor.zip","archive_sha256":digest,"archive_size":123,"files":[["Mini-Monitor/Mini-Monitor.exe", "b".repeat(64)]]});
    let encoded = serde_json::to_vec(&good).unwrap();
    let sig = pair.sign(&encoded);
    let result = verify_manifest(&encoded, sig.as_ref(), pair.public_key().as_ref()).unwrap();
    assert_eq!(result.version, "1.2.3");
    assert_eq!(result.files.len(), 1);
    for bad in [
        serde_json::json!({"archive_url":"https://evil.invalid/payload"}),
        serde_json::json!({"archive_url":"https://github.com:443/contentriumkorea/mini-monitor/releases/download/v1.2.3/Mini-Monitor.zip"}),
        serde_json::json!({"archive_url":"https://github.com/contentriumkorea/mini-monitor/releases/download/v1.2.4/Mini-Monitor.zip"}),
        serde_json::json!({"files":[["Mini-Monitor/../outside.exe", "b".repeat(64)]]}),
        serde_json::json!({"files":[["Mini-Monitor/A.exe", "b".repeat(64)],["Mini-Monitor/a.exe", "c".repeat(64)]]}),
        serde_json::json!({"files":[["Mini-Monitor/CON.txt", "b".repeat(64)]]}),
        serde_json::json!({"archive_size":0}),
        serde_json::json!({"channel":"beta"}),
        serde_json::json!({"unexpected":true}),
    ] {
        let mut mutated = good.clone();
        for (k, v) in bad.as_object().unwrap() {
            mutated[k] = v.clone();
        }
        let payload = serde_json::to_vec(&mutated).unwrap();
        let signature = pair.sign(&payload);
        assert!(verify_manifest(&payload, signature.as_ref(), pair.public_key().as_ref()).is_err());
    }
    let pretty = serde_json::to_vec_pretty(&good).unwrap();
    assert!(verify_manifest(
        &pretty,
        pair.sign(&pretty).as_ref(),
        pair.public_key().as_ref()
    )
    .is_err());
}

#[test]
fn official_verifier_rejects_a_manifest_signed_by_an_untrusted_key() {
    let pair = Ed25519KeyPair::from_seed_unchecked(&[19; 32]).unwrap();
    let payload = br#"{"channel":"stable","version":"1.2.3"}"#;
    assert!(mini_monitor_native::update::verify_official_manifest(
        payload,
        pair.sign(payload).as_ref()
    )
    .is_err());
}

#[test]
#[ignore = "requires a previously downloaded official v0.2.13 manifest and signature"]
fn official_release_signature_and_inventory_are_compatible() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".superpowers/sdd/2026-11-01-rust-migration");
    let manifest = std::fs::read(root.join("update-v2-manifest.json")).unwrap();
    let signature = std::fs::read(root.join("update-v2-manifest.sig")).unwrap();
    let verified =
        mini_monitor_native::update::verify_official_manifest(&manifest, &signature).unwrap();
    assert_eq!(verified.version, "0.2.13");
    assert!(!verified.files.is_empty());
}

#[test]
#[ignore = "requires previously downloaded official v0.2.13 release files"]
fn official_release_archive_matches_its_signed_inventory() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".superpowers/sdd/2026-11-01-rust-migration");
    let payload = std::fs::read(root.join("update-v2-manifest.json")).unwrap();
    let signature = std::fs::read(root.join("update-v2-manifest.sig")).unwrap();
    let manifest =
        mini_monitor_native::update::verify_official_manifest(&payload, &signature).unwrap();
    let archive = std::fs::File::open(root.join("Mini-Monitor.zip")).unwrap();
    mini_monitor_native::update::verify_official_archive(archive, &manifest).unwrap();
}

#[test]
fn archive_is_checked_against_signed_size_hash_and_inventory() {
    use mini_monitor_native::update::{verify_archive, UpdateManifest};
    use sha2::{Digest, Sha256};
    use std::io::{Cursor, Write};
    fn archive(entries: &[(&str, &[u8])]) -> Vec<u8> {
        let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for (name, bytes) in entries {
            writer
                .start_file(*name, zip::write::FileOptions::default())
                .unwrap();
            writer.write_all(bytes).unwrap();
        }
        writer.finish().unwrap().into_inner()
    }
    fn hash(data: &[u8]) -> String {
        format!("{:x}", Sha256::digest(data))
    }
    let zip = archive(&[("Mini-Monitor/Mini-Monitor.exe", b"hello")]);
    let manifest = UpdateManifest {
        version: "1.2.3".into(),
        archive_url: String::new(),
        archive_sha256: hash(&zip),
        archive_size: zip.len() as u64,
        files: vec![("Mini-Monitor/Mini-Monitor.exe".into(), hash(b"hello"))],
    };
    assert!(verify_archive(Cursor::new(&zip), &manifest).is_ok());
    let mut altered = zip.clone();
    altered[0] ^= 1;
    assert!(verify_archive(Cursor::new(&altered), &manifest).is_err());
    let wrong = UpdateManifest {
        files: vec![("Mini-Monitor/Mini-Monitor.exe".into(), hash(b"wrong"))],
        ..manifest
    };
    assert!(verify_archive(Cursor::new(&zip), &wrong).is_err());
    let extra = archive(&[
        ("Mini-Monitor/Mini-Monitor.exe", b"hello"),
        ("Mini-Monitor/extra.dll", b"bad"),
    ]);
    let extra_manifest = UpdateManifest {
        archive_sha256: hash(&extra),
        archive_size: extra.len() as u64,
        files: vec![("Mini-Monitor/Mini-Monitor.exe".into(), hash(b"hello"))],
        ..wrong
    };
    assert!(verify_archive(Cursor::new(extra), &extra_manifest).is_err());
    let missing_exe = archive(&[("Mini-Monitor/data.txt", b"hello")]);
    let missing_manifest = UpdateManifest {
        archive_sha256: hash(&missing_exe),
        archive_size: missing_exe.len() as u64,
        files: vec![("Mini-Monitor/data.txt".into(), hash(b"hello"))],
        ..extra_manifest
    };
    assert!(verify_archive(Cursor::new(missing_exe), &missing_manifest).is_err());
}

#[test]
fn latest_release_metadata_selects_only_matching_stable_signed_assets() {
    use mini_monitor_native::update::select_latest_release;
    let base = "https://github.com/contentriumkorea/mini-monitor/releases/download/v1.2.3/";
    let good = serde_json::json!({"tag_name":"v1.2.3","draft":false,"prerelease":false,"assets":[
      {"name":"update-v2-manifest.json","state":"uploaded","size":1000,"browser_download_url":format!("{base}update-v2-manifest.json")},
      {"name":"update-v2-manifest.sig","state":"uploaded","size":64,"browser_download_url":format!("{base}update-v2-manifest.sig")},
      {"name":"Mini-Monitor.zip","state":"uploaded","size":2000,"browser_download_url":format!("{base}Mini-Monitor.zip")}
    ]});
    let encode = |value: &serde_json::Value| serde_json::to_vec(value).unwrap();
    let selected = select_latest_release(&encode(&good)).unwrap();
    assert_eq!(selected.version, "1.2.3");
    assert_eq!(
        selected.manifest_url,
        format!("{base}update-v2-manifest.json")
    );
    for mutate in [
        serde_json::json!({"draft":true}),
        serde_json::json!({"prerelease":true}),
        serde_json::json!({"tag_name":"v1.2.4"}),
    ] {
        let mut bad = good.clone();
        for (key, value) in mutate.as_object().unwrap() {
            bad[key] = value.clone();
        }
        assert!(select_latest_release(&encode(&bad)).is_err());
    }
    let mut bad = good.clone();
    bad["assets"][1]["size"] = serde_json::json!(63);
    assert!(select_latest_release(&encode(&bad)).is_err());
    let mut bad = good.clone();
    bad["assets"][0]["browser_download_url"] = serde_json::json!("https://evil.invalid/manifest");
    assert!(select_latest_release(&encode(&bad)).is_err());
    let mut bad = good.clone();
    bad["assets"]
        .as_array_mut()
        .unwrap()
        .push(good["assets"][0].clone());
    assert!(select_latest_release(&encode(&bad)).is_err());
}

#[test]
#[ignore = "reads current public GitHub release metadata using the configured GitHub CLI"]
fn real_latest_release_metadata_is_compatible() {
    let output = std::process::Command::new("gh")
        .args(["api", "repos/contentriumkorea/mini-monitor/releases/latest"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let latest = mini_monitor_native::update::select_latest_release(&output.stdout).unwrap();
    assert!(!latest.version.is_empty());
}

#[test]
fn update_download_redirects_are_limited_to_trusted_https_hosts() {
    use mini_monitor_native::update::trusted_download_redirect;
    assert!(trusted_download_redirect(
        "https://release-assets.githubusercontent.com/path?token=example"
    ));
    assert!(trusted_download_redirect(
        "https://github.com/contentriumkorea/mini-monitor"
    ));
    for bad in [
        "http://github.com/path",
        "https://github.com.evil.invalid/path",
        "https://user@github.com/path",
        "https://github.com:443/path",
        "https://github.com:444/path",
        "https://example.invalid/path",
        "https://github.com/path#fragment",
    ] {
        assert!(!trusted_download_redirect(bad));
    }
}

#[test]
fn release_download_reader_rejects_oversized_and_incomplete_reads() {
    use mini_monitor_native::update::read_bounded;
    use std::io::{self, Cursor, Read};
    assert_eq!(read_bounded(Cursor::new(b"1234"), 4).unwrap(), b"1234");
    assert!(read_bounded(Cursor::new(b"12345"), 4).is_err());
    struct Broken;
    impl Read for Broken {
        fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
            Err(io::Error::other("private transport detail"))
        }
    }
    assert_eq!(
        read_bounded(Broken, 4).unwrap_err(),
        "release download failed"
    );
}

#[test]
fn update_client_rejects_unapproved_asset_addresses_before_network_access() {
    use mini_monitor_native::update::{LatestRelease, UpdateHttp};
    let client = UpdateHttp::new().unwrap();
    let bad = LatestRelease {
        version: "1.2.3".into(),
        manifest_url: "https://evil.invalid/manifest".into(),
        signature_url: "https://evil.invalid/signature".into(),
    };
    assert!(client.fetch_verified_assets(&bad).is_err());
}

#[test]
#[ignore = "contacts the official public release endpoint; no installation or writes"]
fn rust_update_client_reads_and_verifies_live_stable_manifest() {
    let client = mini_monitor_native::update::UpdateHttp::new().unwrap();
    let release = client.latest().unwrap();
    let manifest = client.fetch_verified_assets(&release).unwrap();
    assert_eq!(release.version, manifest.version);
}

#[test]
fn update_version_comparison_is_numeric_and_never_offers_a_downgrade() {
    use mini_monitor_native::update::is_newer_release;
    assert!(is_newer_release("0.2.13", "0.2.14").unwrap());
    assert!(is_newer_release("0.9.9", "0.10.0").unwrap());
    assert!(!is_newer_release("1.0.0", "0.99.99").unwrap());
    assert!(!is_newer_release("1.0.0", "1.0.0").unwrap());
    assert!(is_newer_release("1.0.0", "1.0.0-beta").is_err());
    assert!(is_newer_release("1.02.3", "1.2.4").is_err());
}

#[test]
fn archive_stream_checks_signed_size_hash_and_cancellation() {
    use mini_monitor_native::update::copy_archive_bytes;
    use sha2::{Digest, Sha256};
    use std::io::Cursor;
    let payload = b"verified bytes";
    let hash = format!("{:x}", Sha256::digest(payload));
    let mut target = Vec::new();
    copy_archive_bytes(
        Cursor::new(payload),
        &mut target,
        payload.len() as u64,
        &hash,
        || false,
    )
    .unwrap();
    assert_eq!(target, payload);
    assert!(copy_archive_bytes(
        Cursor::new(b"short"),
        Vec::new(),
        payload.len() as u64,
        &hash,
        || false
    )
    .is_err());
    assert!(copy_archive_bytes(
        Cursor::new(b"verified bytes extra"),
        Vec::new(),
        payload.len() as u64,
        &hash,
        || false
    )
    .is_err());
    assert!(copy_archive_bytes(
        Cursor::new(payload),
        Vec::new(),
        payload.len() as u64,
        &"0".repeat(64),
        || false
    )
    .is_err());
    let mut empty = Vec::new();
    assert!(copy_archive_bytes(
        Cursor::new(payload),
        &mut empty,
        payload.len() as u64,
        &hash,
        || true
    )
    .is_err());
    assert!(empty.is_empty());
}

#[test]
#[ignore = "uses an already downloaded official signed manifest"]
fn archive_download_rejects_invalid_stage_before_connecting() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".superpowers/sdd/2026-11-01-rust-migration");
    let payload = std::fs::read(root.join("update-v2-manifest.json")).unwrap();
    let sig = std::fs::read(root.join("update-v2-manifest.sig")).unwrap();
    let manifest = mini_monitor_native::update::verify_official_manifest(&payload, &sig).unwrap();
    let temp = tempfile::NamedTempFile::new().unwrap();
    let client = mini_monitor_native::update::UpdateHttp::new().unwrap();
    assert!(client
        .download_archive(&manifest, temp.path(), || false)
        .is_err());
    assert!(client
        .download_archive(&manifest, temp.path().parent().unwrap(), || true)
        .is_err());
}

#[test]
#[ignore = "downloads the official current release archive to an isolated temporary folder"]
fn rust_update_client_downloads_and_verifies_real_archive_without_installing() {
    let parent = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".superpowers/sdd/2026-11-01-rust-migration");
    let directory = tempfile::tempdir_in(parent).unwrap();
    let client = mini_monitor_native::update::UpdateHttp::new().unwrap();
    let release = client.latest().unwrap();
    let manifest = client.fetch_verified_assets(&release).unwrap();
    let file = client
        .download_archive(&manifest, directory.path(), || false)
        .unwrap();
    assert_eq!(
        file.as_file().metadata().unwrap().len(),
        manifest.archive_size
    );
}

#[test]
#[ignore = "stages the previously downloaded official release in a temporary directory"]
fn official_archive_can_be_staged_without_touching_installed_app() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".superpowers/sdd/2026-11-01-rust-migration");
    let payload = std::fs::read(root.join("update-v2-manifest.json")).unwrap();
    let sig = std::fs::read(root.join("update-v2-manifest.sig")).unwrap();
    let manifest = mini_monitor_native::update::verify_official_manifest(&payload, &sig).unwrap();
    let parent = tempfile::tempdir_in(&root).unwrap();
    let mut archive = std::fs::File::open(root.join("Mini-Monitor.zip")).unwrap();
    assert!(mini_monitor_native::update::stage_official_archive(
        &mut archive,
        &manifest,
        parent.path(),
        || true
    )
    .is_err());
    assert_eq!(std::fs::read_dir(parent.path()).unwrap().count(), 0);
    let calls = std::cell::Cell::new(0);
    assert!(mini_monitor_native::update::stage_official_archive(
        &mut archive,
        &manifest,
        parent.path(),
        || {
            calls.set(calls.get() + 1);
            calls.get() > 40
        }
    )
    .is_err());
    assert_eq!(std::fs::read_dir(parent.path()).unwrap().count(), 0);
    let stage = mini_monitor_native::update::stage_official_archive(
        &mut archive,
        &manifest,
        parent.path(),
        || false,
    )
    .unwrap();
    assert!(stage
        .path()
        .file_name()
        .unwrap()
        .to_string_lossy()
        .starts_with(".mini-monitor-update-"));
    assert!(stage.path().join("Mini-Monitor/Mini-Monitor.exe").is_file());
}
