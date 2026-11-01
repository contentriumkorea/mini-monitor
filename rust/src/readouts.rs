//! Formatting for small monitoring surfaces; absence stays visibly distinct from zero.
use crate::codex::{Credit, Limits};

pub fn percent_readout(value: Option<f64>) -> String {
    match value {
        Some(value) if value.is_finite() && value >= 0.0 => {
            format!("{}%", value.min(100.0).round_ties_even() as u32)
        }
        _ => "--".into(),
    }
}

fn credit_text(balance: &str) -> Option<String> {
    let parts: Vec<_> = balance.split('.').collect();
    if parts.len() > 2
        || parts[0].is_empty()
        || parts[0].len() > 12
        || !parts[0].bytes().all(|b| b.is_ascii_digit())
        || (parts.len() == 2
            && (parts[1].is_empty()
                || parts[1].len() > 32
                || !parts[1].bytes().all(|b| b.is_ascii_digit())))
        || !balance.bytes().any(|b| (b'1'..=b'9').contains(&b))
    {
        return None;
    }
    let digits = parts[0].parse::<u64>().ok()?.to_string();
    let mut result = String::new();
    for (index, character) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            result.push(',');
        }
        result.push(character);
    }
    Some(result)
}

pub fn codex_readout(limits: Option<&Limits>) -> String {
    let Some(limits) = limits else {
        return "--".into();
    };
    let Some(primary) = limits
        .windows
        .iter()
        .max_by_key(|window| window.duration_minutes)
    else {
        return "--".into();
    };
    if limits
        .windows
        .iter()
        .any(|window| window.remaining_percent.is_finite() && window.remaining_percent <= 0.0)
    {
        return match &limits.credit {
            Credit::Unlimited => "UNLTD Credit".into(),
            Credit::Balance(balance) => credit_text(balance)
                .map(|text| format!("{text} Credit"))
                .unwrap_or_else(|| "0%".into()),
            Credit::Unknown => "0%".into(),
        };
    }
    percent_readout(Some(primary.remaining_percent))
}
