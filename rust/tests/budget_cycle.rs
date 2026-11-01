use mini_monitor_native::budget::USD_SCALE;
use mini_monitor_native::budget_cycle::BudgetCycle;
use time::{Date, Month};

fn date(year: i32, month: Month, day: u8) -> Date {
    Date::from_calendar_date(year, month, day).unwrap()
}

#[test]
fn rollover_discards_result_started_in_previous_month() {
    let mut cycle = BudgetCycle::new(date(2026, Month::January, 31), 10_000).unwrap();
    let pending = cycle.begin(date(2026, Month::February, 27)).unwrap();
    assert!(cycle
        .complete(pending, date(2026, Month::February, 28), 10 * USD_SCALE)
        .is_err());
    let next = cycle.begin(date(2026, Month::February, 28)).unwrap();
    let snapshot = cycle
        .complete(next, date(2026, Month::February, 28), 25 * USD_SCALE)
        .unwrap();
    assert_eq!(snapshot.period_start, date(2026, Month::February, 28));
    assert_eq!(snapshot.used_percent, 25.0);
}

#[test]
fn new_configuration_or_newer_request_invalidates_old_results() {
    let mut cycle = BudgetCycle::new(date(2026, Month::January, 1), 100).unwrap();
    let stale = cycle.begin(date(2026, Month::January, 2)).unwrap();
    let latest = cycle.begin(date(2026, Month::January, 2)).unwrap();
    assert!(cycle
        .complete(stale, date(2026, Month::January, 2), 0)
        .is_err());
    cycle
        .reconfigure(date(2026, Month::January, 1), 200)
        .unwrap();
    assert!(cycle
        .complete(latest, date(2026, Month::January, 2), 0)
        .is_err());
    assert!(BudgetCycle::new(date(2026, Month::January, 1), 0).is_err());
}
