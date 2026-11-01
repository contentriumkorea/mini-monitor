use mini_monitor_native::budget::monthly_period;
use time::{Date, Month};

fn day(year: i32, month: u8, day: u8) -> Date {
    Date::from_calendar_date(year, Month::try_from(month).unwrap(), day).unwrap()
}

#[test]
fn periods_use_original_anchor_and_exclusive_reset_date() {
    assert_eq!(
        monthly_period(day(2024, 11, 5), day(2024, 12, 4)).unwrap(),
        (day(2024, 11, 5), day(2024, 12, 5))
    );
    assert_eq!(
        monthly_period(day(2024, 11, 5), day(2024, 12, 5)).unwrap(),
        (day(2024, 12, 5), day(2025, 1, 5))
    );
    assert_eq!(
        monthly_period(day(2024, 1, 31), day(2024, 2, 29)).unwrap(),
        (day(2024, 2, 29), day(2024, 3, 31))
    );
    assert_eq!(
        monthly_period(day(2024, 1, 31), day(2025, 2, 28)).unwrap(),
        (day(2025, 2, 28), day(2025, 3, 31))
    );
    assert_eq!(
        monthly_period(day(2024, 2, 29), day(2025, 3, 29)).unwrap(),
        (day(2025, 3, 29), day(2025, 4, 29))
    );
}

#[test]
fn future_anchors_cannot_fabricate_a_prior_period() {
    assert!(monthly_period(day(2026, 11, 5), day(2026, 11, 1)).is_err());
}
