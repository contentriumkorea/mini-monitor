use mini_monitor_native::{device::DeviceInfo, device_connect::connect_with};
use std::{
    cell::Cell,
    io::{self, Write},
    rc::Rc,
};

struct Writer;
impl Write for Writer {
    fn write(&mut self, b: &[u8]) -> io::Result<usize> {
        Ok(b.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn target() -> DeviceInfo {
    DeviceInfo {
        port: "COM3".into(),
        vid: 0x1a86,
        pid: 0x5722,
        serial: "USB35INCHIPSV2".into(),
    }
}

#[test]
fn mismatched_identity_and_cancellation_prevent_open() {
    let opened = Rc::new(Cell::new(0));
    let tracker = opened.clone();
    let wrong = DeviceInfo {
        serial: "other".into(),
        ..target()
    };
    assert!(connect_with(
        Some("COM3"),
        || Ok(vec![wrong.clone()]),
        |_| {
            tracker.set(tracker.get() + 1);
            Ok(Writer)
        },
        || false
    )
    .is_err());
    assert_eq!(opened.get(), 0);
    let tracker = opened.clone();
    assert!(connect_with(
        None,
        || Ok(vec![target()]),
        |_| {
            tracker.set(tracker.get() + 1);
            Ok(Writer)
        },
        || true
    )
    .is_err());
    assert_eq!(opened.get(), 0);
}

#[test]
fn post_open_identity_change_rejects_session_before_protocol_bytes() {
    let calls = Cell::new(0);
    let result = connect_with(
        None,
        || {
            calls.set(calls.get() + 1);
            if calls.get() < 3 {
                Ok(vec![target()])
            } else {
                Ok(vec![])
            }
        },
        |_| Ok(Writer),
        || false,
    );
    assert!(result.is_err());
    assert_eq!(calls.get(), 3);
}

#[test]
fn serial_settings_match_vendor_protocol_without_handshake() {
    use mini_monitor_native::device_connect::serial_settings;
    let settings = serial_settings();
    assert_eq!(settings.baud, 115200);
    assert_eq!(settings.data_bits, 8);
    assert_eq!(settings.dcb_flags, 0x1011); // binary + DTR enabled + RTS enabled
    assert_eq!(settings.write_timeout_ms, 1000);
}

#[test]
fn cancellation_during_admitted_open_closes_pending_connection() {
    use mini_monitor_native::device_connect::{connect_with_gate, ConnectGate};
    use std::{
        sync::{mpsc, Arc},
        time::Duration,
    };
    let gate = Arc::new(ConnectGate::default());
    let (entered_tx, entered_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let worker_gate = gate.clone();
    let worker = std::thread::spawn(move || {
        connect_with_gate(
            None,
            || Ok(vec![target()]),
            |_| {
                entered_tx.send(()).unwrap();
                release_rx.recv().unwrap();
                Ok(Writer)
            },
            &worker_gate,
        )
        .is_err()
    });
    entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    let cancel_gate = gate.clone();
    let (done_tx, done_rx) = mpsc::channel();
    let cancel = std::thread::spawn(move || {
        cancel_gate.cancel();
        done_tx.send(()).unwrap();
    });
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    while !gate.is_cancelled() && std::time::Instant::now() < deadline {
        std::thread::yield_now();
    }
    assert!(gate.is_cancelled());
    assert!(done_rx.recv_timeout(Duration::from_millis(50)).is_err());
    release_tx.send(()).unwrap();
    done_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    assert!(worker.join().unwrap());
    cancel.join().unwrap();
}

#[test]
fn cancel_request_does_not_wait_for_running_io_and_prevents_new_operations() {
    use mini_monitor_native::device_connect::ConnectGate;
    use std::{
        sync::{mpsc, Arc},
        time::Duration,
    };
    let gate = Arc::new(ConnectGate::default());
    let (entered_tx, entered_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let active = gate.clone();
    let worker = std::thread::spawn(move || {
        active
            .perform_if_active(|| {
                entered_tx.send(()).unwrap();
                release_rx.recv().unwrap();
                Ok(())
            })
            .unwrap();
    });
    entered_rx.recv_timeout(Duration::from_secs(2)).unwrap();
    gate.request_cancel();
    assert!(gate.is_cancelled());
    release_tx.send(()).unwrap();
    worker.join().unwrap();
    assert!(gate.perform_if_active(|| Ok(())).is_err());
}
