use mini_monitor_native::{
    budget::USD_SCALE,
    budget_worker::{BudgetStatus, BudgetWorker},
};
use std::{
    sync::{mpsc, Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use time::{Date, Month, OffsetDateTime};

fn date(day: u8) -> Date {
    Date::from_calendar_date(2026, Month::January, day).unwrap()
}
fn now(day: u8) -> OffsetDateTime {
    date(day).midnight().assume_utc() + time::Duration::hours(12)
}
fn wait_until(mut predicate: impl FnMut() -> bool) {
    let until = Instant::now() + Duration::from_secs(3);
    while !predicate() {
        assert!(Instant::now() < until);
        thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn changed_configuration_rejects_the_inflight_cost_response() {
    let (entered_tx, entered_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let mut calls = 0;
    let mut worker = BudgetWorker::start(
        date(1),
        10000,
        Duration::from_secs(60),
        move |_, _, _| {
            calls += 1;
            if calls == 1 {
                entered_tx.send(()).unwrap();
                release_rx.recv().unwrap();
                Ok(USD_SCALE * 80)
            } else {
                Ok(USD_SCALE * 10)
            }
        },
        || now(20),
    )
    .unwrap();
    entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    worker.reconfigure(date(1), 20000).unwrap();
    assert_eq!(worker.status(), BudgetStatus::Pending);
    assert!(worker.snapshot().is_none());
    release_tx.send(()).unwrap();
    wait_until(|| worker.status() == BudgetStatus::Ready);
    let snapshot = worker.snapshot().unwrap();
    assert_eq!(snapshot.total_cents, 20000);
    assert_eq!(snapshot.spent_units, USD_SCALE * 10);
    worker.request_stop();
    worker.join();
}

#[test]
fn errors_and_month_rollover_never_leave_stale_values_visible() {
    let clock = Arc::new(Mutex::new(now(20)));
    let clock_read = clock.clone();
    let mut worker = BudgetWorker::start(
        date(1),
        10000,
        Duration::from_secs(60),
        |_, _, _| Ok(USD_SCALE * 10),
        move || *clock_read.lock().unwrap(),
    )
    .unwrap();
    wait_until(|| worker.status() == BudgetStatus::Ready);
    *clock.lock().unwrap() = Date::from_calendar_date(2026, Month::February, 2)
        .unwrap()
        .midnight()
        .assume_utc();
    assert!(worker.snapshot().is_none());
    wait_until(|| worker.status() == BudgetStatus::Ready && worker.snapshot().is_some());
    assert_eq!(
        worker.snapshot().unwrap().period_start.month(),
        Month::February
    );
    worker.request_stop();
    worker.join();

    let mut failed = BudgetWorker::start(
        date(1),
        10000,
        Duration::from_secs(60),
        |_, _, _| Err("unauthorized"),
        || now(20),
    )
    .unwrap();
    wait_until(|| failed.status() == BudgetStatus::Error);
    assert!(failed.snapshot().is_none());
    failed.request_stop();
    failed.join();
}
