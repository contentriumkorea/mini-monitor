//! At most one bounded background download/staging operation, never on the UI thread.
use crate::update::{self, PreparedHandoff, UpdateHttp};
use std::{
    io,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver},
        Arc,
    },
    thread,
};
pub struct UpdateTask {
    cancelled: Arc<AtomicBool>,
    result: Receiver<Result<PreparedHandoff, &'static str>>,
}
impl UpdateTask {
    pub fn start(install: PathBuf, config: Option<PathBuf>, current: String) -> io::Result<Self> {
        Self::start_job(move |cancelled| {
            let client = UpdateHttp::new()?;
            let release = client.latest()?;
            let manifest = client.fetch_verified_assets(&release)?;
            if !update::is_newer_release(&current, &manifest.version)? {
                return Err("already current");
            }
            let parent = install.parent().ok_or("invalid install root")?;
            let mut archive =
                client.download_archive(&manifest, parent, || cancelled.load(Ordering::Acquire))?;
            let stage =
                update::stage_official_archive(archive.as_file_mut(), &manifest, parent, || {
                    cancelled.load(Ordering::Acquire)
                })?;
            if cancelled.load(Ordering::Acquire) {
                return Err("update cancelled");
            }
            update::prepare_official_handoff(
                stage,
                &install,
                &manifest,
                archive.path(),
                &install.join("scripts/Apply-Update.ps1"),
                config.as_deref(),
            )
        })
    }
    pub fn start_job(
        job: impl FnOnce(Arc<AtomicBool>) -> Result<PreparedHandoff, &'static str> + Send + 'static,
    ) -> io::Result<Self> {
        let cancelled = Arc::new(AtomicBool::new(false));
        let flag = cancelled.clone();
        let (send, result) = mpsc::sync_channel(1);
        thread::Builder::new()
            .name("mini-monitor-update".into())
            .spawn(move || {
                let outcome = job(flag.clone());
                if !flag.load(Ordering::Acquire) {
                    let _ = send.send(outcome);
                }
            })?;
        Ok(Self { cancelled, result })
    }
    pub fn poll(&self) -> Option<Result<PreparedHandoff, &'static str>> {
        self.result.try_recv().ok()
    }
}
impl Drop for UpdateTask {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn staging_failure_is_reported_without_blocking_the_ui() {
        let task = UpdateTask::start_job(|_| Err("fixture failure")).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        loop {
            if let Some(value) = task.poll() {
                assert!(matches!(value, Err("fixture failure")));
                break;
            }
            assert!(std::time::Instant::now() < deadline);
            thread::yield_now();
        }
    }
}
