use mini_monitor_native::device::{bitmap_transfer, select_device, DeviceInfo, Orientation};
use mini_monitor_native::device_session::DeviceSession;
use std::{
    io::{self, Write},
    sync::{Arc, Mutex},
    time::Duration,
};

struct FakeWriter {
    bytes: Arc<Mutex<Vec<u8>>>,
    fail_after: Option<usize>,
}
impl Write for FakeWriter {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        let mut bytes = self.bytes.lock().unwrap();
        if self.fail_after.is_some_and(|limit| bytes.len() >= limit) {
            return Err(io::Error::other("injected failure"));
        }
        let count = self
            .fail_after
            .map_or(data.len(), |limit| data.len().min(limit - bytes.len()));
        bytes.extend_from_slice(&data[..count]);
        Ok(count)
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
fn identity_mismatch_never_writes_any_bytes() {
    let expected = target();
    let selected = select_device(std::slice::from_ref(&expected), None).unwrap();
    let wrong = DeviceInfo {
        serial: "wrong".into(),
        ..expected
    };
    let bytes = Arc::new(Mutex::new(Vec::new()));
    assert!(DeviceSession::verified(
        selected,
        &[wrong],
        FakeWriter {
            bytes: bytes.clone(),
            fail_after: None
        }
    )
    .is_err());
    assert!(bytes.lock().unwrap().is_empty());
}

#[test]
fn startup_matches_vendor_order_and_partial_requires_full_frame() {
    let target = target();
    let selected = select_device(std::slice::from_ref(&target), None).unwrap();
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let mut session = DeviceSession::verified(
        selected,
        &[target],
        FakeWriter {
            bytes: bytes.clone(),
            fail_after: None,
        },
    )
    .unwrap();
    let mut waits = Vec::new();
    session
        .start(25, Orientation::Landscape, |duration| {
            waits.push(duration);
            true
        })
        .unwrap();
    assert_eq!(
        waits,
        [Duration::from_millis(100), Duration::from_millis(500)]
    );
    let command = bytes.lock().unwrap().clone();
    assert_eq!(command.len(), 34);
    assert_eq!(
        (command[5], command[11], command[17], command[33]),
        (0x6e, 0x6d, 0x79, 0x6e)
    );
    let partial = bitmap_transfer(0, 0, 2, 2, &[0; 8], 480, 320).unwrap();
    assert!(session.send_partial(&partial).is_err());
    assert!(session.send_full(&partial).is_err());
    let wrong_orientation =
        bitmap_transfer(0, 0, 320, 480, &vec![0; 320 * 480 * 2], 320, 480).unwrap();
    assert!(session.send_full(&wrong_orientation).is_err());
    let full = bitmap_transfer(0, 0, 480, 320, &vec![0; 480 * 320 * 2], 480, 320).unwrap();
    session.send_full(&full).unwrap();
    session.send_partial(&partial).unwrap();
}

#[test]
fn interrupted_write_poison_closes_session_and_never_retries_partial_data() {
    let target = target();
    let selected = select_device(std::slice::from_ref(&target), None).unwrap();
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let mut session = DeviceSession::verified(
        selected,
        &[target],
        FakeWriter {
            bytes: bytes.clone(),
            fail_after: Some(2),
        },
    )
    .unwrap();
    assert!(session.start(25, Orientation::Landscape, |_| true).is_err());
    let written = bytes.lock().unwrap().len();
    assert!(session.start(25, Orientation::Landscape, |_| true).is_err());
    assert_eq!(bytes.lock().unwrap().len(), written);
}

#[test]
fn live_brightness_change_is_safety_capped_before_any_write() {
    let target = target();
    let selected = select_device(std::slice::from_ref(&target), None).unwrap();
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let mut session = DeviceSession::verified(
        selected,
        &[target],
        FakeWriter {
            bytes: bytes.clone(),
            fail_after: None,
        },
    )
    .unwrap();
    assert!(session.set_brightness(25).is_err());
    session.start(25, Orientation::Landscape, |_| true).unwrap();
    let before = bytes.lock().unwrap().len();
    assert!(session.set_brightness(51).is_err());
    assert_eq!(bytes.lock().unwrap().len(), before);
    session.set_brightness(50).unwrap();
    assert_eq!(bytes.lock().unwrap().len(), before + 6);
}

#[test]
fn cancel_during_frame_closes_session_before_next_chunk() {
    use std::cell::Cell;
    let target = target();
    let selected = select_device(std::slice::from_ref(&target), None).unwrap();
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let mut session = DeviceSession::verified(
        selected,
        &[target],
        FakeWriter {
            bytes: bytes.clone(),
            fail_after: None,
        },
    )
    .unwrap();
    session.start(25, Orientation::Landscape, |_| true).unwrap();
    let full = bitmap_transfer(0, 0, 480, 320, &vec![0; 480 * 320 * 2], 480, 320).unwrap();
    let checks = Cell::new(0);
    assert!(session
        .send_full_cancellable(&full, || {
            checks.set(checks.get() + 1);
            checks.get() >= 3
        })
        .is_err());
    let count = bytes.lock().unwrap().len();
    assert!(session.send_full(&full).is_err());
    assert_eq!(bytes.lock().unwrap().len(), count);
    assert!(count < 34 + 6 + 480 * 320 * 2);
}

#[test]
fn changing_orientation_requires_fresh_full_frame() {
    let target = target();
    let selected = select_device(std::slice::from_ref(&target), None).unwrap();
    let bytes = Arc::new(Mutex::new(Vec::new()));
    let mut session = DeviceSession::verified(
        selected,
        &[target],
        FakeWriter {
            bytes,
            fail_after: None,
        },
    )
    .unwrap();
    session.start(25, Orientation::Landscape, |_| true).unwrap();
    let landscape = bitmap_transfer(0, 0, 480, 320, &vec![0; 480 * 320 * 2], 480, 320).unwrap();
    session.send_full(&landscape).unwrap();
    session
        .set_orientation(Orientation::Portrait, 25, |_| true)
        .unwrap();
    assert!(session.send_full(&landscape).is_err());
    let partial = bitmap_transfer(0, 0, 2, 2, &[0; 8], 320, 480).unwrap();
    assert!(session.send_partial(&partial).is_err());
    let portrait = bitmap_transfer(0, 0, 320, 480, &vec![0; 320 * 480 * 2], 320, 480).unwrap();
    session.send_full(&portrait).unwrap();
    session.send_partial(&partial).unwrap();
}

#[test]
fn cancellation_before_or_during_start_closes_without_further_commands() {
    use std::cell::Cell;
    for stop_at in [1, 2, 3, 4, 5] {
        let target = target();
        let selected = select_device(std::slice::from_ref(&target), None).unwrap();
        let bytes = Arc::new(Mutex::new(Vec::new()));
        let mut session = DeviceSession::verified(
            selected,
            &[target],
            FakeWriter {
                bytes: bytes.clone(),
                fail_after: None,
            },
        )
        .unwrap();
        let checks = Cell::new(0);
        assert!(session
            .start_cancellable(
                25,
                Orientation::Landscape,
                |_| true,
                || {
                    checks.set(checks.get() + 1);
                    checks.get() >= stop_at
                }
            )
            .is_err());
        let length = bytes.lock().unwrap().len();
        assert!(session.start(25, Orientation::Landscape, |_| true).is_err());
        assert_eq!(bytes.lock().unwrap().len(), length);
        if stop_at == 1 {
            assert_eq!(length, 0);
        }
    }
}
