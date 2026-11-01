use mini_monitor_native::account_worker::{AccountStatus, AccountWorker};
use mini_monitor_native::codex_client::RpcTransport;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

struct Mock {
    reads: Arc<AtomicUsize>,
    delay: Duration,
}
impl RpcTransport for Mock {
    fn request(
        &mut self,
        _id: u64,
        _method: &str,
        _params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        std::thread::sleep(self.delay);
        Ok(json!({"account":null}))
    }
}

#[test]
fn background_refresh_runs_independently_and_shuts_down() {
    let reads = Arc::new(AtomicUsize::new(0));
    let mut worker = AccountWorker::start_with_transport(
        Mock {
            reads: reads.clone(),
            delay: Duration::ZERO,
        },
        Duration::from_secs(60),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    while worker.snapshot().status != AccountStatus::SignedOut && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(worker.snapshot().status, AccountStatus::SignedOut);
    assert!(reads.load(Ordering::SeqCst) >= 1);
    worker.stop();
}

#[test]
fn refresh_requests_are_bounded_when_transport_is_slow() {
    let reads = Arc::new(AtomicUsize::new(0));
    let mut worker = AccountWorker::start_with_transport(
        Mock {
            reads,
            delay: Duration::from_millis(300),
        },
        Duration::from_secs(60),
    )
    .unwrap();
    std::thread::sleep(Duration::from_millis(30));
    let rejected = (0..100).filter(|_| !worker.request_refresh()).count();
    assert!(rejected > 0);
    worker.stop();
}

#[test]
fn unsafe_polling_intervals_are_rejected() {
    let reads = Arc::new(AtomicUsize::new(0));
    assert!(AccountWorker::start_with_transport(
        Mock {
            reads,
            delay: Duration::ZERO
        },
        Duration::from_secs(1)
    )
    .is_err());
}

struct ChangingAccount {
    changed: Arc<std::sync::atomic::AtomicBool>,
    sign_out: bool,
}
impl RpcTransport for ChangingAccount {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        _params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        if method == "account/read" {
            Ok(if self.sign_out {
                json!({"account":null})
            } else {
                json!({"account":{"type":"chatgpt"}})
            })
        } else {
            Ok(
                json!({"rateLimits":{"limitId":"codex","primary":{"usedPercent":15,"windowDurationMins":10080}}}),
            )
        }
    }
    fn poll_notification(&mut self) -> Result<Option<(String, Value)>, &'static str> {
        if self.changed.swap(false, Ordering::SeqCst) {
            self.sign_out = true;
            Ok(Some(("account/updated".into(), json!({"authMode":null}))))
        } else {
            Ok(None)
        }
    }
}

#[test]
fn account_change_is_observed_without_waiting_for_minute_poll() {
    let changed = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut worker = AccountWorker::start_with_transport(
        ChangingAccount {
            changed: changed.clone(),
            sign_out: false,
        },
        Duration::from_secs(60),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while worker.snapshot().status != AccountStatus::Ready && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(worker.snapshot().status, AccountStatus::Ready);
    changed.store(true, Ordering::SeqCst);
    while worker.snapshot().status != AccountStatus::SignedOut && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(worker.snapshot().status, AccountStatus::SignedOut);
    assert!(worker.snapshot().limits.is_none());
    worker.stop();
}

#[cfg(windows)]
#[test]
#[ignore = "requires the pinned bundled Codex runtime"]
fn real_worker_runs_with_empty_temporary_account_home() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap();
    let runtime = root.join("third_party/codex-app-server/codex-app-server.exe");
    let home = tempfile::tempdir().unwrap();
    let mut worker =
        AccountWorker::start_verified(runtime, home.path().to_path_buf(), Duration::from_secs(60))
            .unwrap();
    let deadline = Instant::now() + Duration::from_secs(20);
    while worker.snapshot().status == AccountStatus::Pending && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert_eq!(worker.snapshot().status, AccountStatus::SignedOut);
    worker.stop();
}

struct LoginMock {
    complete: Arc<std::sync::atomic::AtomicBool>,
}
impl RpcTransport for LoginMock {
    fn request(
        &mut self,
        _id: u64,
        method: &str,
        _params: Value,
        _notify: &mut dyn FnMut(&str, &Value),
    ) -> Result<Value, &'static str> {
        match method {
            "account/read" => Ok(json!({"account":null})),
            "account/login/start" => {
                Ok(json!({"loginId":"fake", "authUrl":"https://auth.openai.com/login"}))
            }
            "account/login/cancel" => Ok(json!({})),
            _ => Err("unavailable"),
        }
    }
    fn poll_notification(&mut self) -> Result<Option<(String, Value)>, &'static str> {
        if self.complete.swap(false, Ordering::SeqCst) {
            Ok(Some((
                "account/login/completed".into(),
                json!({"loginId":"fake","success":true}),
            )))
        } else {
            Ok(None)
        }
    }
}

#[test]
fn login_is_a_bounded_background_action_and_completion_refreshes() {
    use mini_monitor_native::account_worker::AccountEvent;
    let complete = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut worker = AccountWorker::start_with_transport(
        LoginMock {
            complete: complete.clone(),
        },
        Duration::from_secs(60),
    )
    .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while worker.snapshot().status != AccountStatus::SignedOut && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(worker.request_login());
    let mut opened = false;
    while Instant::now() < deadline {
        if let Some(AccountEvent::OpenBrowser(url)) = worker.poll_event() {
            assert_eq!(url, "https://auth.openai.com/login");
            opened = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(opened);
    assert_eq!(worker.snapshot().status, AccountStatus::LoginPending);
    complete.store(true, Ordering::SeqCst);
    while worker.snapshot().status != AccountStatus::SignedOut && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(worker.snapshot().status, AccountStatus::SignedOut);
    worker.stop();
}

#[test]
fn cancelled_login_never_delivers_queued_authentication_url() {
    let complete = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let mut worker =
        AccountWorker::start_with_transport(LoginMock { complete }, Duration::from_secs(60))
            .unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    while worker.snapshot().status != AccountStatus::SignedOut && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert!(worker.request_login());
    while worker.snapshot().status != AccountStatus::LoginPending && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(worker.snapshot().status, AccountStatus::LoginPending);
    assert!(worker.cancel_login());
    assert!(worker.poll_event().is_none());
    worker.stop();
}
