//! Explicit, read-only usage probe. Does not print account details or authentication data.
#[cfg(windows)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use mini_monitor_native::{
        account_worker::{AccountStatus, AccountWorker},
        codex::Credit,
    };
    use std::{
        path::PathBuf,
        thread,
        time::{Duration, Instant},
    };
    let runtime = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("missing project root")?
        .join("third_party/codex-app-server/codex-app-server.exe");
    let home = PathBuf::from(std::env::var_os("LOCALAPPDATA").ok_or("local data unavailable")?)
        .join("AI-Mini-Monitor")
        .join("codex-home");
    let worker = AccountWorker::start_verified(runtime, home, Duration::from_secs(60))?;
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        let snapshot = worker.snapshot();
        match snapshot.status {
            AccountStatus::Ready => {
                let limits = snapshot.limits.ok_or("missing usage")?;
                println!(
                    "account=ready usage_available={} credit_available={}",
                    !limits.windows.is_empty(),
                    !matches!(limits.credit, Credit::Unknown)
                );
                return Ok(());
            }
            AccountStatus::SignedOut => return Err("account signed out".into()),
            _ if Instant::now() >= deadline => return Err("account refresh timed out".into()),
            _ => thread::sleep(Duration::from_millis(100)),
        }
    }
}
#[cfg(not(windows))]
fn main() {}
