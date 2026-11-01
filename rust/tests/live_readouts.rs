#[cfg(windows)]
#[test]
fn desktop_readouts_work_without_usb_and_hide_stale_or_failed_account_data() {
    use mini_monitor_native::{
        account_worker::{AccountSnapshot, AccountStatus},
        budget::USD_SCALE,
        budget_cycle::BudgetSnapshot,
        live_readouts::LiveReadouts,
        sensor_worker::SensorSnapshot,
        sensors::MemorySnapshot,
        taskbar_render::MetricKind as K,
    };
    use time::{Date, Month};
    let day = Date::from_calendar_date(2026, Month::January, 15).unwrap();
    let sensor = SensorSnapshot {
        cpu_percent: Some(12.0),
        memory: Some(MemorySnapshot {
            total_bytes: 100,
            available_bytes: 70,
            used_percent: 30.0,
        }),
        gpu: None,
    };
    let account = AccountSnapshot {
        status: AccountStatus::Error,
        email: None,
        plan: None,
        limits: None,
    };
    let budget = BudgetSnapshot {
        period_start: Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        period_end: Date::from_calendar_date(2026, Month::February, 1).unwrap(),
        total_cents: 100,
        spent_units: USD_SCALE / 2,
        used_percent: 50.0,
    };
    let values = LiveReadouts::new(Some(&sensor), Some(&account), Some(&budget), day);
    assert_eq!(
        values
            .selected(&[K::Cpu, K::Ram, K::Gpu, K::Codex, K::Api])
            .unwrap()
            .iter()
            .map(|(_, s)| *s)
            .collect::<Vec<_>>(),
        ["12%", "30%", "--", "--", "50%"]
    );
    assert!(values.selected(&[K::Cpu, K::Cpu]).is_err());
    let stale = LiveReadouts::new(
        Some(&sensor),
        Some(&account),
        Some(&budget),
        Date::from_calendar_date(2026, Month::February, 1).unwrap(),
    );
    assert_eq!(stale.selected(&[K::Api]).unwrap()[0].1, "--");
}
