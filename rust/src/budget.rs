//! Calendar rules for the user-configured monthly API spending period.
use time::{Date, Month};

fn boundary(index: i32, anchor_day: u8) -> Result<Date, &'static str> {
    let year = index.div_euclid(12);
    let month = Month::try_from((index.rem_euclid(12) + 1) as u8).map_err(|_| "invalid month")?;
    let day = anchor_day.min(month.length(year));
    Date::from_calendar_date(year, month, day).map_err(|_| "date out of range")
}

/// The start is inclusive, the end exclusive; caller supplies today's UTC date.
pub fn monthly_period(anchor: Date, today_utc: Date) -> Result<(Date, Date), &'static str> {
    if anchor > today_utc {
        return Err("start date is in the future");
    }
    let mut index = today_utc.year() * 12 + i32::from(today_utc.month() as u8) - 1;
    if boundary(index, anchor.day())? > today_utc {
        index -= 1;
    }
    Ok((
        boundary(index, anchor.day())?,
        boundary(index + 1, anchor.day())?,
    ))
}
