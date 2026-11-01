//! Test-only process for an isolated update acknowledgement test.
#[cfg(windows)]
fn main() {
    let Some(path) = std::env::var_os("MINI_MONITOR_UPDATE_ACK_PATH") else {
        return;
    };
    let Ok(nonce) = std::env::var("MINI_MONITOR_UPDATE_ACK_NONCE") else {
        return;
    };
    let Ok(exe) = std::env::current_exe() else {
        return;
    };
    if mini_monitor_native::update::acknowledge_startup(
        &exe,
        &std::path::PathBuf::from(path),
        &nonce,
        "1.2.3",
    ) == Ok(true)
    {
        std::thread::sleep(std::time::Duration::from_secs(2));
    }
}
#[cfg(not(windows))]
fn main() {}
