#[cfg(windows)]
#[test]
fn bundled_codex_icon_decodes_at_taskbar_size_with_transparency() {
    use mini_monitor_native::native_icon::{load_icon, IconKind};
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("assets/icons");
    let icon = load_icon(&root, IconKind::Codex, 36).unwrap();
    assert_eq!(icon.width, 36);
    assert_eq!(icon.height, 36);
    assert!(icon.pixels.as_chunks::<4>().0.iter().any(|p| p[3] > 0));
    assert!(icon.pixels.as_chunks::<4>().0.iter().any(|p| p[3] == 0));
    assert!(icon
        .pixels
        .as_chunks::<4>()
        .0
        .iter()
        .all(|p| p[..3].iter().all(|c| *c <= p[3])));
}

#[cfg(windows)]
#[test]
fn invalid_icon_paths_and_sizes_do_not_crash_or_allocate_large_images() {
    use mini_monitor_native::native_icon::{load_icon, IconKind};
    let root = tempfile::tempdir().unwrap();
    assert!(load_icon(root.path(), IconKind::Cpu, 0).is_err());
    assert!(load_icon(root.path(), IconKind::Cpu, 512).is_err());
    assert!(load_icon(root.path(), IconKind::Cpu, 32).is_err());
}
