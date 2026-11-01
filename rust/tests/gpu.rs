use mini_monitor_native::gpu::{power_watts, temperature_c, usage_percent, vram_snapshot};

#[test]
fn unsupported_or_invalid_readings_never_become_fabricated_zero() {
    assert_eq!(usage_percent(101), None);
    assert_eq!(usage_percent(0), Some(0));
    assert!(vram_snapshot(0, 0).is_none());
    assert!(vram_snapshot(100, 101).is_none());
    let snapshot = vram_snapshot(1000, 250).unwrap();
    assert_eq!(snapshot.total_bytes, 1000);
    assert_eq!(snapshot.used_bytes, 250);
    assert_eq!(snapshot.used_percent, 25.0);
    assert_eq!(temperature_c(65), Some(65));
    assert_eq!(temperature_c(999), None);
    assert_eq!(power_watts(42_500), Some(42.5));
    assert_eq!(power_watts(3_000_000), None);
}

#[cfg(windows)]
#[test]
#[ignore = "requires an NVIDIA GPU and its trusted system driver"]
fn nvidia_driver_reports_sane_gpu_and_memory_values() {
    use mini_monitor_native::gpu::NvmlGpu;
    let gpu = NvmlGpu::open(0).unwrap();
    let sample = gpu.sample();
    assert!(!sample.name.is_empty());
    assert!(sample.gpu_percent.is_some_and(|n| n <= 100));
    assert!(sample
        .vram
        .is_some_and(|v| v.total_bytes > 0 && v.used_bytes <= v.total_bytes));
}
