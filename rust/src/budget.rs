//! Calendar rules for the user-configured monthly API spending period.
use time::{Date, Month};

/// USD units in 10^-24 dollars. Unsupported precision is rejected, never rounded to zero.
pub const USD_SCALE: u128 = 1_000_000_000_000_000_000_000_000;
const MAX_UNITS: u128 = USD_SCALE * 1_000_000_000_000;

#[derive(Debug, PartialEq, Eq)]
pub struct CostsPage {
    pub amount_units: u128,
    pub next_page: Option<String>,
}

/// Fetch all pages with bounded work; caller supplies transport and cancellation.
pub fn collect_costs<F, C>(
    start: i64,
    end: i64,
    mut fetch: F,
    cancelled: C,
) -> Result<u128, &'static str>
where
    F: FnMut(Option<&str>) -> Result<String, &'static str>,
    C: Fn() -> bool,
{
    let mut total = 0u128;
    let mut cursor: Option<String> = None;
    let mut seen = std::collections::HashSet::new();
    for _ in 0..100 {
        if cancelled() {
            return Err("cancelled");
        }
        let page = fetch(cursor.as_deref()).map_err(|error| match error {
            "unauthorized" => "unauthorized",
            "forbidden" => "forbidden",
            "rate limited" => "rate limited",
            _ => "fetch failed",
        })?;
        if cancelled() {
            return Err("cancelled");
        }
        let page = parse_costs_page(&page, start, end)?;
        total = total
            .checked_add(page.amount_units)
            .filter(|n| *n <= MAX_UNITS)
            .ok_or("total exceeds limit")?;
        match page.next_page {
            None => return Ok(total),
            Some(next) if seen.insert(next.clone()) => cursor = Some(next),
            Some(_) => return Err("repeated cursor"),
        }
    }
    Err("too many pages")
}

fn amount_units(text: &str) -> Result<u128, &'static str> {
    if text.is_empty() || text.len() > 40 {
        return Err("invalid amount");
    }
    let (mantissa, exponent) = match text.find(['e', 'E']) {
        Some(index) => (
            &text[..index],
            text[index + 1..]
                .parse::<i32>()
                .map_err(|_| "invalid exponent")?,
        ),
        None => (text, 0),
    };
    if !(-100..=100).contains(&exponent) {
        return Err("exponent out of range");
    }
    let mut number = 0u128;
    let mut fraction = 0i32;
    let mut dot = false;
    let mut digits = 0usize;
    for byte in mantissa.bytes() {
        match byte {
            b'.' if !dot => dot = true,
            b'0'..=b'9' => {
                digits += 1;
                if dot {
                    fraction += 1;
                }
                number = number
                    .checked_mul(10)
                    .and_then(|n| n.checked_add(u128::from(byte - b'0')))
                    .ok_or("amount overflow")?;
            }
            _ => return Err("invalid amount"),
        }
    }
    if digits == 0 {
        return Err("invalid amount");
    }
    let scale = 24 + exponent - fraction;
    if number == 0 {
        return Ok(0);
    }
    if scale >= 0 {
        let factor = 10u128.checked_pow(scale as u32).ok_or("amount overflow")?;
        number = number.checked_mul(factor).ok_or("amount overflow")?;
    } else {
        let factor = 10u128
            .checked_pow((-scale) as u32)
            .ok_or("unsupported amount precision")?;
        if !number.is_multiple_of(factor) {
            return Err("unsupported amount precision");
        }
        number /= factor;
    }
    if number > MAX_UNITS {
        return Err("amount exceeds limit");
    }
    Ok(number)
}

/// Parse one official Costs API page using a caller-provided UTC request interval.
pub fn parse_costs_page(input: &str, start: i64, end: i64) -> Result<CostsPage, &'static str> {
    if input.len() > 4_000_000 || end <= start {
        return Err("invalid request or page size");
    }
    let page: serde_json::Value = serde_json::from_str(input).map_err(|_| "invalid JSON")?;
    if page.get("object").and_then(|v| v.as_str()) != Some("page") {
        return Err("invalid page");
    }
    let data = page
        .get("data")
        .and_then(|v| v.as_array())
        .ok_or("invalid buckets")?;
    let more = page
        .get("has_more")
        .and_then(|v| v.as_bool())
        .ok_or("invalid pagination")?;
    let mut total = 0u128;
    for bucket in data {
        if bucket.get("object").and_then(|v| v.as_str()) != Some("bucket") {
            return Err("invalid bucket");
        }
        let first = bucket
            .get("start_time")
            .and_then(|v| v.as_i64())
            .ok_or("invalid start")?;
        let last = bucket
            .get("end_time")
            .and_then(|v| v.as_i64())
            .ok_or("invalid end")?;
        if first < start
            || first >= end
            || last <= first
            || last.checked_sub(first).is_none_or(|n| n > 86400)
        {
            return Err("invalid bucket interval");
        }
        for entry in bucket
            .get("results")
            .and_then(|v| v.as_array())
            .ok_or("invalid results")?
        {
            if entry.get("object").and_then(|v| v.as_str()) != Some("organization.costs.result") {
                return Err("invalid cost result");
            }
            let amount = entry.get("amount").ok_or("missing amount")?;
            if !amount
                .get("currency")
                .and_then(|v| v.as_str())
                .is_some_and(|c| c.eq_ignore_ascii_case("usd"))
            {
                return Err("unsupported currency");
            }
            let value = amount.get("value").ok_or("missing value")?;
            let text = match value {
                serde_json::Value::String(s) => s.clone(),
                serde_json::Value::Number(n) => n.to_string(),
                _ => return Err("invalid value"),
            };
            total = total
                .checked_add(amount_units(&text)?)
                .ok_or("total overflow")?;
            if total > MAX_UNITS {
                return Err("total exceeds limit");
            }
        }
    }
    let next_page = if more {
        let cursor = page
            .get("next_page")
            .and_then(|v| v.as_str())
            .filter(|c| !c.is_empty() && c.len() <= 2048)
            .ok_or("invalid cursor")?;
        Some(cursor.to_owned())
    } else {
        None
    };
    Ok(CostsPage {
        amount_units: total,
        next_page,
    })
}

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
