use mini_monitor_native::sensors::{cpu_usage, memory_snapshot, CpuTimes};

#[test]
fn cpu_delta_uses_total_minus_idle_and_rejects_bad_counters() {
    let first = CpuTimes {
        idle: 100,
        kernel: 200,
        user: 100,
    };
    let second = CpuTimes {
        idle: 130,
        kernel: 240,
        user: 160,
    };
    assert_eq!(cpu_usage(first, second), Some(70.0));
    assert_eq!(cpu_usage(first, first), None);
    assert_eq!(cpu_usage(second, first), None);
    assert_eq!(
        cpu_usage(
            first,
            CpuTimes {
                idle: 300,
                kernel: 210,
                user: 110
            }
        ),
        None
    );
}

#[cfg(windows)]
#[test]
fn windows_memory_data_is_sane_without_a_monitor_device() {
    let memory = memory_snapshot().unwrap();
    assert!(memory.total_bytes > 0);
    assert!(memory.available_bytes <= memory.total_bytes);
    assert!((0.0..=100.0).contains(&memory.used_percent));
}
