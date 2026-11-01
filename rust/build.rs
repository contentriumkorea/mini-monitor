fn main() {
    println!("cargo:rerun-if-changed=app.rc");
    println!("cargo:rerun-if-changed=app.manifest");
    println!("cargo:rerun-if-changed=../assets/app-icon.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let kits = std::path::Path::new(r"C:\Program Files (x86)\Windows Kits\10\bin");
    let mut candidates = std::fs::read_dir(kits)
        .expect("Windows SDK resource compiler is required")
        .filter_map(Result::ok)
        .map(|e| e.path().join("x64/rc.exe"))
        .filter(|p| p.is_file())
        .collect::<Vec<_>>();
    candidates.sort();
    let compiler = candidates
        .pop()
        .expect("Windows SDK x64 resource compiler is required");
    let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap()).join("app.res");
    let status = std::process::Command::new(compiler)
        .arg("/nologo")
        .arg("/fo")
        .arg(&output)
        .arg("app.rc")
        .status()
        .expect("resource compiler failed");
    assert!(status.success(), "resource compilation failed");
    println!(
        "cargo:rustc-link-arg-bin=mini-monitor-native={}",
        output.display()
    );
    println!("cargo:rustc-link-arg-bin=mini-monitor-native=/MANIFEST:NO");
}
