#![cfg(windows)]
use mini_monitor_native::{
    account_worker::{AccountSnapshot, AccountStatus},
    budget::USD_SCALE,
    budget_cycle::BudgetSnapshot,
    codex::{Credit, LimitWindow, Limits},
    dashboard_data::dashboard_input,
    dashboard_layout::CardKind,
    gpu::{GpuSnapshot, VramSnapshot},
    sensor_worker::SensorSnapshot,
    sensors::MemorySnapshot,
};
use time::{Date, Month};

#[test]
fn live_dashboard_uses_real_values_and_preserves_exact_codex_credit() {
    let sensor = SensorSnapshot {
        cpu_percent: Some(16.0),
        memory: Some(MemorySnapshot {
            total_bytes: 16 * 1024 * 1024 * 1024,
            available_bytes: 8 * 1024 * 1024 * 1024,
            used_percent: 50.0,
        }),
        gpu: Some(GpuSnapshot {
            name: "NVIDIA Test GPU".into(),
            gpu_percent: Some(42),
            vram: Some(VramSnapshot {
                total_bytes: 8 * 1024 * 1024 * 1024,
                used_bytes: 2 * 1024 * 1024 * 1024,
                used_percent: 25.0,
            }),
            temperature_c: Some(52),
            power_watts: Some(110.0),
        }),
    };
    let account = AccountSnapshot {
        status: AccountStatus::Ready,
        email: None,
        plan: None,
        limits: Some(Limits {
            windows: vec![LimitWindow {
                duration_minutes: 10080,
                remaining_percent: 83.0,
                resets_at: None,
            }],
            credit: Credit::Balance("60518.43".into()),
        }),
    };
    let budget = BudgetSnapshot {
        period_start: Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        period_end: Date::from_calendar_date(2026, Month::February, 1).unwrap(),
        total_cents: 10000,
        spent_units: USD_SCALE * 10,
        used_percent: 10.0,
    };
    let data = dashboard_input(
        Some(&sensor),
        Some(&account),
        Some(&budget),
        Date::from_calendar_date(2026, Month::January, 15).unwrap(),
        Some("Test CPU"),
        "COM3",
    );
    let get = |kind| data.entries.iter().find(|e| e.kind == kind).unwrap();
    assert_eq!(get(CardKind::Cpu).value, "16%");
    assert!(get(CardKind::Cpu).details.contains(&"Test CPU".into()));
    assert_eq!(get(CardKind::Ram).value, "50%");
    assert!(get(CardKind::Ram)
        .details
        .contains(&"8.0 / 16.0 GiB".into()));
    assert_eq!(get(CardKind::Gpu).value, "42%");
    assert!(get(CardKind::Gpu)
        .details
        .contains(&"NVIDIA Test GPU".into()));
    assert_eq!(get(CardKind::Vram).value, "25%");
    assert_eq!(get(CardKind::Codex).value, "83%");
    assert!(get(CardKind::Codex)
        .details
        .contains(&"CREDITS 60,518".into()));
    assert_eq!(get(CardKind::Api).value, "90%");
    assert_eq!(get(CardKind::Api).gauge, Some(90.0));
    assert_eq!(get(CardKind::Api).details, ["TOTAL $100.00", "USED $10.00"]);
}

#[test]
fn failed_account_and_expired_budget_do_not_show_stale_values() {
    let account = AccountSnapshot {
        status: AccountStatus::Error,
        email: None,
        plan: None,
        limits: Some(Limits {
            windows: vec![LimitWindow {
                duration_minutes: 10080,
                remaining_percent: 83.0,
                resets_at: None,
            }],
            credit: Credit::Balance("60518".into()),
        }),
    };
    let budget = BudgetSnapshot {
        period_start: Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        period_end: Date::from_calendar_date(2026, Month::February, 1).unwrap(),
        total_cents: 10000,
        spent_units: USD_SCALE * 10,
        used_percent: 10.0,
    };
    let data = dashboard_input(
        None,
        Some(&account),
        Some(&budget),
        Date::from_calendar_date(2026, Month::February, 1).unwrap(),
        None,
        "",
    );
    for kind in [
        CardKind::Cpu,
        CardKind::Ram,
        CardKind::Gpu,
        CardKind::Vram,
        CardKind::Codex,
        CardKind::Api,
    ] {
        let item = data.entries.iter().find(|e| e.kind == kind).unwrap();
        assert_eq!(item.value, "--");
        assert!(item.gauge.is_none());
    }
}

#[test]
fn dashboard_pixels_become_a_valid_full_frame_for_each_screen_orientation() {
    use mini_monitor_native::{
        dashboard_data::render_usb_frame, dashboard_layout::Orientation,
        dashboard_render::DashboardRenderer,
    };
    let data = dashboard_input(
        None,
        None,
        None,
        Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        None,
        "COM3",
    );
    let mut renderer = DashboardRenderer::new();
    let landscape = render_usb_frame(&mut renderer, &data, Orientation::Landscape, false).unwrap();
    assert!(landscape.is_full_for(480, 320));
    assert!(!landscape.is_full_for(320, 480));
    let portrait = render_usb_frame(&mut renderer, &data, Orientation::Portrait, false).unwrap();
    assert!(portrait.is_full_for(320, 480));
}

#[test]
fn exhausted_short_limit_and_positive_credit_never_show_a_full_codex_gauge() {
    let account = AccountSnapshot {
        status: AccountStatus::Ready,
        email: None,
        plan: None,
        limits: Some(Limits {
            windows: vec![
                LimitWindow {
                    duration_minutes: 10080,
                    remaining_percent: 80.0,
                    resets_at: None,
                },
                LimitWindow {
                    duration_minutes: 300,
                    remaining_percent: 0.0,
                    resets_at: None,
                },
            ],
            credit: Credit::Balance("60518".into()),
        }),
    };
    let data = dashboard_input(
        None,
        Some(&account),
        None,
        Date::from_calendar_date(2026, Month::January, 1).unwrap(),
        None,
        "",
    );
    let codex = data
        .entries
        .iter()
        .find(|e| e.kind == CardKind::Codex)
        .unwrap();
    assert_eq!(codex.value, "60,518 Credit");
    assert_eq!(codex.gauge, Some(0.0));
}
