use mini_monitor_native::telemetry::LatencyWindow;

#[test]
fn latency_storage_is_bounded_and_p95_uses_recent_samples() {
    let mut window = LatencyWindow::new(100).unwrap();
    for value in 0..10_000 {
        window.record(value as f64);
    }
    assert_eq!(window.len(), 100);
    assert_eq!(window.p95(), Some(9994.0));
}

#[test]
fn invalid_values_and_invalid_capacity_are_never_recorded() {
    assert!(LatencyWindow::new(0).is_err());
    assert!(LatencyWindow::new(10_001).is_err());
    let mut window = LatencyWindow::new(5).unwrap();
    for value in [f64::NAN, f64::INFINITY, -1.0] {
        window.record(value);
    }
    assert_eq!(window.len(), 0);
    assert_eq!(window.p95(), None);
}
