//! Strict parsing of the app-server's Codex-specific rate limit response.
use serde_json::Value;

#[derive(Debug, PartialEq)]
pub struct LimitWindow {
    pub duration_minutes: u64,
    pub remaining_percent: f64,
    pub resets_at: Option<u64>,
}
#[derive(Debug, PartialEq)]
pub enum Credit {
    Unknown,
    Unlimited,
    Balance(String),
}
#[derive(Debug, PartialEq)]
pub struct Limits {
    pub windows: Vec<LimitWindow>,
    pub credit: Credit,
}

pub fn parse_limits(input: &str) -> Result<Limits, &'static str> {
    if input.len() > 4_000_000 {
        return Err("response too large");
    }
    let root: Value = serde_json::from_str(input).map_err(|_| "invalid JSON")?;
    let bucket = root
        .pointer("/rateLimitsByLimitId/codex")
        .filter(|v| v.is_object())
        .or_else(|| root.get("rateLimits").filter(|v| v.is_object()))
        .ok_or("missing Codex limits")?;
    if bucket.get("limitId").and_then(Value::as_str) != Some("codex") {
        return Err("wrong limit bucket");
    }
    let mut windows = Vec::new();
    for key in ["primary", "secondary"] {
        if let Some(window) = bucket.get(key) {
            let duration = window
                .get("windowDurationMins")
                .and_then(Value::as_u64)
                .filter(|n| *n > 0);
            let used = window
                .get("usedPercent")
                .and_then(Value::as_f64)
                .filter(|n| n.is_finite() && (0.0..=100.0).contains(n));
            if let (Some(duration_minutes), Some(used)) = (duration, used) {
                windows.push(LimitWindow {
                    duration_minutes,
                    remaining_percent: 100.0 - used,
                    resets_at: window
                        .get("resetsAt")
                        .and_then(Value::as_u64)
                        .filter(|n| *n <= 253_402_300_799),
                });
            }
        }
    }
    windows.sort_by_key(|a| std::cmp::Reverse(a.duration_minutes));
    let credit = match bucket.get("credits") {
        Some(value)
            if value.get("hasCredits").and_then(Value::as_bool).is_some()
                && value.get("unlimited").and_then(Value::as_bool).is_some() =>
        {
            if value["unlimited"].as_bool() == Some(true) {
                Credit::Unlimited
            } else if let Some(balance) = value.get("balance").and_then(Value::as_str) {
                let parts: Vec<_> = balance.split('.').collect();
                if parts.len() <= 2
                    && !parts[0].is_empty()
                    && parts[0].len() <= 12
                    && parts[0].bytes().all(|b| b.is_ascii_digit())
                    && (parts.len() == 1
                        || (!parts[1].is_empty()
                            && parts[1].len() <= 32
                            && parts[1].bytes().all(|b| b.is_ascii_digit())))
                {
                    Credit::Balance(balance.into())
                } else {
                    Credit::Unknown
                }
            } else {
                Credit::Unknown
            }
        }
        _ => Credit::Unknown,
    };
    Ok(Limits { windows, credit })
}
