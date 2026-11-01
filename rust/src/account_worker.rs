//! Bounded account refresh worker. Does not open a browser or interact with USB.
use crate::{
    codex::Limits,
    codex_client::{AccountClient, AccountState, RpcTransport},
    codex_login::{LoginAction, LoginFlow},
    codex_process::RpcProcess,
};
use std::{
    io,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender},
        Arc, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountStatus {
    Pending,
    SignedOut,
    Ready,
    LoginPending,
    Error,
}

// Deliberately no Debug: authentication URLs may contain short-lived secrets.
pub enum AccountEvent {
    OpenBrowser(String),
}

// Deliberately no Debug: snapshots can hold account email and credit balance.
#[derive(Clone)]
pub struct AccountSnapshot {
    pub status: AccountStatus,
    pub email: Option<String>,
    pub plan: Option<String>,
    pub limits: Option<Limits>,
}
impl AccountSnapshot {
    fn empty(status: AccountStatus) -> Self {
        Self {
            status,
            email: None,
            plan: None,
            limits: None,
        }
    }
}

enum Command {
    Refresh,
    Login(u64),
    CancelLogin,
    Logout(u64),
    Wake,
}

pub struct AccountWorker {
    initialized: Arc<AtomicBool>,
    latest: Arc<Mutex<AccountSnapshot>>,
    commands: SyncSender<Command>,
    events: Receiver<(u64, AccountEvent)>,
    login_queued: Arc<AtomicBool>,
    logout_queued: Arc<AtomicBool>,
    login_generation: Arc<AtomicU64>,
    queued: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl AccountWorker {
    /// Called on a worker thread with an application-owned existing account home.
    pub fn start_verified(runtime: PathBuf, home: PathBuf, interval: Duration) -> io::Result<Self> {
        Self::start_factory(
            move || {
                let mut process = RpcProcess::spawn_verified(&runtime, &home)?;
                process.initialize(Duration::from_secs(8))?;
                Ok(process)
            },
            interval,
        )
    }

    pub fn start_with_transport<T: RpcTransport + Send + 'static>(
        transport: T,
        interval: Duration,
    ) -> io::Result<Self> {
        Self::start_factory(move || Ok(transport), interval)
    }

    fn start_factory<T, F>(factory: F, interval: Duration) -> io::Result<Self>
    where
        T: RpcTransport + Send + 'static,
        F: FnOnce() -> Result<T, &'static str> + Send + 'static,
    {
        if interval < Duration::from_secs(60) || Instant::now().checked_add(interval).is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "account interval must be at least 60 seconds",
            ));
        }
        let latest = Arc::new(Mutex::new(AccountSnapshot::empty(AccountStatus::Pending)));
        let initialized = Arc::new(AtomicBool::new(false));
        let initialized_worker = initialized.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let queued = Arc::new(AtomicBool::new(false));
        let login_queued = Arc::new(AtomicBool::new(false));
        let logout_queued = Arc::new(AtomicBool::new(false));
        let login_generation = Arc::new(AtomicU64::new(0));
        let (commands, receiver) = mpsc::sync_channel(4);
        let (event_sender, events) = mpsc::sync_channel(4);
        let (output, stopped, pending) =
            (Arc::clone(&latest), Arc::clone(&stop), Arc::clone(&queued));
        let (login_pending, generation) =
            (Arc::clone(&login_queued), Arc::clone(&login_generation));
        let logout_pending = Arc::clone(&logout_queued);
        let thread = thread::Builder::new()
            .name("mini-monitor-account".into())
            .spawn(move || {
                let Ok(transport) = factory() else {
                    *output.lock().unwrap_or_else(|e| e.into_inner()) =
                        AccountSnapshot::empty(AccountStatus::Error);
                    return;
                };
                initialized_worker.store(true, Ordering::Release);
                let mut client = AccountClient::new(transport);
                let mut login = LoginFlow::default();
                let mut due = Instant::now();
                let mut retried_account_change = false;
                while !stopped.load(Ordering::Acquire) {
                    let now = Instant::now();
                    if now >= due && !login.is_pending() {
                        let refresh_generation = generation.load(Ordering::Acquire);
                        let result = client.refresh(|_, _| {});
                        let retry_changed =
                            matches!(&result, Err("account changed during refresh"))
                                && !retried_account_change;
                        let snapshot = match result {
                            Ok(AccountState::SignedOut) => {
                                AccountSnapshot::empty(AccountStatus::SignedOut)
                            }
                            Ok(AccountState::Ready {
                                email,
                                plan,
                                limits,
                            }) => AccountSnapshot {
                                status: AccountStatus::Ready,
                                email,
                                plan,
                                limits: Some(limits),
                            },
                            Err(_) => AccountSnapshot::empty(AccountStatus::Error),
                        };
                        if stopped.load(Ordering::Acquire) {
                            break;
                        }
                        let retry = if retry_changed {
                            retried_account_change = true;
                            Duration::from_millis(250)
                        } else if snapshot.status == AccountStatus::Error {
                            retried_account_change = false;
                            interval.min(Duration::from_secs(10))
                        } else {
                            retried_account_change = false;
                            interval
                        };
                        if refresh_generation == generation.load(Ordering::Acquire) {
                            *output.lock().unwrap_or_else(|e| e.into_inner()) = snapshot;
                        }
                        due = Instant::now()
                            .checked_add(retry)
                            .unwrap_or_else(Instant::now);
                    }
                    let wait = if login.is_pending() {
                        Duration::from_millis(500)
                    } else {
                        due.saturating_duration_since(Instant::now())
                            .min(Duration::from_millis(500))
                    };
                    match receiver.recv_timeout(wait) {
                        Ok(Command::Refresh) => {
                            pending.store(false, Ordering::Release);
                            due = Instant::now();
                        }
                        Ok(Command::Login(token)) => {
                            if token == generation.load(Ordering::Acquire) && !login.is_pending() {
                                match client.begin_login(&mut login, |_, _| {}) {
                                    Ok(LoginAction::OpenBrowser(url)) => {
                                        if token != generation.load(Ordering::Acquire) {
                                            let _ = client.cancel_login(&mut login, |_, _| {});
                                            due = Instant::now();
                                        } else {
                                            *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                                AccountSnapshot::empty(AccountStatus::LoginPending);
                                            if event_sender
                                                .try_send((token, AccountEvent::OpenBrowser(url)))
                                                .is_err()
                                            {
                                                let _ = client.cancel_login(&mut login, |_, _| {});
                                                *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                                    AccountSnapshot::empty(AccountStatus::Error);
                                            }
                                        }
                                    }
                                    Ok(LoginAction::Refresh) => {
                                        due = Instant::now();
                                    }
                                    Ok(LoginAction::CancelServer(id)) => {
                                        let _ = client.cancel_server_login(&id, |_, _| {});
                                        *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                            AccountSnapshot::empty(AccountStatus::Error);
                                    }
                                    _ => {
                                        *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                            AccountSnapshot::empty(AccountStatus::Error);
                                    }
                                }
                            }
                            login_pending.store(false, Ordering::Release);
                        }
                        Ok(Command::CancelLogin) => {
                            if client.cancel_login(&mut login, |_, _| {}).is_err() {
                                *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                    AccountSnapshot::empty(AccountStatus::Error);
                            } else {
                                *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                    AccountSnapshot::empty(AccountStatus::Pending);
                                due = Instant::now();
                            }
                        }
                        Ok(Command::Logout(token)) => {
                            if token == generation.load(Ordering::Acquire) {
                                let result = client
                                    .cancel_login(&mut login, |_, _| {})
                                    .and_then(|_| client.logout(|_, _| {}));
                                *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                    AccountSnapshot::empty(if result.is_ok() {
                                        AccountStatus::SignedOut
                                    } else {
                                        AccountStatus::Error
                                    });
                                due = Instant::now()
                                    .checked_add(interval)
                                    .unwrap_or_else(Instant::now);
                            }
                            logout_pending.store(false, Ordering::Release);
                        }
                        Ok(Command::Wake) => (),
                        Err(mpsc::RecvTimeoutError::Timeout) => (),
                        Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    }
                    let changed = std::cell::Cell::new(false);
                    let action = client.poll_login(&mut login, |method, _| {
                        if method == "account/updated" || method == "account/rateLimits/updated" {
                            changed.set(true);
                        }
                    });
                    if changed.get() && !login.is_pending() {
                        *output.lock().unwrap_or_else(|e| e.into_inner()) =
                            AccountSnapshot::empty(AccountStatus::Pending);
                        due = Instant::now();
                    }
                    match action {
                        Ok(LoginAction::Refresh) => {
                            generation.fetch_add(1, Ordering::AcqRel);
                            *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                AccountSnapshot::empty(AccountStatus::Pending);
                            due = Instant::now();
                        }
                        Ok(LoginAction::Failed) | Err(_) => {
                            generation.fetch_add(1, Ordering::AcqRel);
                            *output.lock().unwrap_or_else(|e| e.into_inner()) =
                                AccountSnapshot::empty(AccountStatus::Error);
                        }
                        _ => (),
                    }
                }
            })?;
        Ok(Self {
            initialized,
            latest,
            commands,
            events,
            login_queued,
            logout_queued,
            login_generation,
            queued,
            stop,
            thread: Some(thread),
        })
    }

    pub fn snapshot(&self) -> AccountSnapshot {
        self.latest
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }
    pub fn runtime_ready(&self) -> bool {
        self.initialized.load(Ordering::Acquire)
    }

    pub fn request_login(&self) -> bool {
        if self.stop.load(Ordering::Acquire)
            || self.snapshot().status == AccountStatus::LoginPending
            || self.login_queued.swap(true, Ordering::AcqRel)
        {
            return false;
        }
        let token = self
            .login_generation
            .fetch_add(1, Ordering::AcqRel)
            .wrapping_add(1);
        if self.commands.try_send(Command::Login(token)).is_err() {
            self.login_queued.store(false, Ordering::Release);
            return false;
        }
        true
    }

    pub fn cancel_login(&self) -> bool {
        if self.stop.load(Ordering::Acquire) {
            return false;
        }
        self.login_generation.fetch_add(1, Ordering::AcqRel);
        self.commands.try_send(Command::CancelLogin).is_ok()
    }

    pub fn request_logout(&self) -> bool {
        if self.stop.load(Ordering::Acquire) || self.logout_queued.swap(true, Ordering::AcqRel) {
            return false;
        }
        let token = self
            .login_generation
            .fetch_add(1, Ordering::AcqRel)
            .wrapping_add(1);
        let mut latest = self.latest.lock().unwrap_or_else(|e| e.into_inner());
        if self.commands.try_send(Command::Logout(token)).is_err() {
            self.logout_queued.store(false, Ordering::Release);
            return false;
        }
        *latest = AccountSnapshot::empty(AccountStatus::Pending);
        true
    }

    pub fn poll_event(&self) -> Option<AccountEvent> {
        for _ in 0..4 {
            let (token, event) = self.events.try_recv().ok()?;
            if !self.stop.load(Ordering::Acquire)
                && token == self.login_generation.load(Ordering::Acquire)
                && self.snapshot().status == AccountStatus::LoginPending
            {
                return Some(event);
            }
        }
        None
    }

    /// Coalesces duplicate requests; returns false when stopped or one is already queued.
    pub fn request_refresh(&self) -> bool {
        if self.stop.load(Ordering::Acquire) || self.queued.swap(true, Ordering::AcqRel) {
            return false;
        }
        if self.commands.try_send(Command::Refresh).is_err() {
            self.queued.store(false, Ordering::Release);
            return false;
        }
        true
    }

    /// Signal shutdown without waiting for an in-flight app-server request.
    pub fn request_stop(&self) {
        self.stop.store(true, Ordering::Release);
        self.login_generation.fetch_add(1, Ordering::AcqRel);
        let _ = self.commands.try_send(Command::Wake);
    }

    /// Waits for the bounded current request; call off the UI thread.
    pub fn stop(&mut self) {
        self.request_stop();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
impl Drop for AccountWorker {
    fn drop(&mut self) {
        self.request_stop();
    }
}

#[cfg(test)]
mod readiness_tests {
    use super::*;
    #[test]
    #[ignore = "starts the pinned bundled server using only a new temporary app-owned account home"]
    fn first_install_initializes_local_runtime_without_importing_any_login() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path().join("AI-Mini-Monitor/codex-home");
        let runtime = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("third_party/codex-app-server/codex-app-server.exe");
        let mut worker =
            AccountWorker::start_verified(runtime, home.clone(), Duration::from_secs(60)).unwrap();
        let deadline = Instant::now() + Duration::from_secs(25);
        loop {
            let status = worker.snapshot().status;
            if status == AccountStatus::SignedOut {
                break;
            }
            assert_ne!(status, AccountStatus::Error);
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(worker.runtime_ready());
        assert!(home.is_dir());
        worker.stop();
    }
    #[test]
    fn failed_local_initialization_cannot_report_runtime_ready() {
        let worker = AccountWorker::start_factory::<RpcProcess, _>(
            || Err("fixture startup failure"),
            Duration::from_secs(60),
        )
        .unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while worker.snapshot().status != AccountStatus::Error {
            assert!(Instant::now() < deadline);
            std::thread::yield_now();
        }
        assert!(!worker.runtime_ready());
    }
}
