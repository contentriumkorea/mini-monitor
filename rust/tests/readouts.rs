use mini_monitor_native::{
    codex::{Credit, LimitWindow, Limits},
    readouts::{codex_readout, percent_readout},
};

fn limits(remaining: f64, credit: Credit) -> Limits {
    Limits {
        windows: vec![LimitWindow {
            duration_minutes: 10080,
            remaining_percent: remaining,
            resets_at: None,
        }],
        credit,
    }
}

#[test]
fn percent_never_turns_unknown_or_invalid_into_zero() {
    assert_eq!(percent_readout(None), "--");
    assert_eq!(percent_readout(Some(f64::NAN)), "--");
    assert_eq!(percent_readout(Some(-1.0)), "--");
    assert_eq!(percent_readout(Some(0.0)), "0%");
    assert_eq!(percent_readout(Some(100.0)), "100%");
}

#[test]
fn credits_replace_only_actually_exhausted_usage_with_positive_balance() {
    assert_eq!(codex_readout(None), "--");
    assert_eq!(
        codex_readout(Some(&limits(85.0, Credit::Balance("60518.43".into())))),
        "85%"
    );
    assert_eq!(
        codex_readout(Some(&limits(0.0, Credit::Balance("60518.43".into())))),
        "60,518 Credit"
    );
    assert_eq!(
        codex_readout(Some(&limits(0.0, Credit::Balance("0".into())))),
        "0%"
    );
    assert_eq!(codex_readout(Some(&limits(0.0, Credit::Unknown))), "0%");
    assert_eq!(
        codex_readout(Some(&limits(0.0, Credit::Unlimited))),
        "UNLTD Credit"
    );
    assert_eq!(
        codex_readout(Some(&limits(0.4, Credit::Balance("60518".into())))),
        "0%"
    );
}
