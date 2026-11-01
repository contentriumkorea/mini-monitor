use mini_monitor_native::device::{select_device, DeviceError, DeviceInfo};

#[test]
fn manual_port_never_bypasses_identity_or_ambiguity_checks() {
    let expected = DeviceInfo {
        port: "COM3".into(),
        vid: 0x1a86,
        pid: 0x5722,
        serial: "USB35INCHIPSV2".into(),
    };
    let wrong = DeviceInfo {
        port: "COM4".into(),
        vid: 0x1a86,
        pid: 0x5722,
        serial: "wrong".into(),
    };
    assert!(select_device(&[expected.clone(), wrong.clone()], None).is_ok());
    assert_eq!(
        select_device(&[expected.clone(), wrong], Some("com4")).err(),
        Some(DeviceError::IdentityMismatch)
    );
    assert_eq!(
        select_device(&[expected.clone(), expected.clone()], None).err(),
        Some(DeviceError::Ambiguous)
    );
    assert_eq!(
        select_device(std::slice::from_ref(&expected), Some("COM8")).err(),
        Some(DeviceError::NotFound)
    );
    assert_eq!(
        select_device(&[expected], Some("\\\\.\\PhysicalDrive0")).err(),
        Some(DeviceError::InvalidPort)
    );
}

#[test]
fn identity_must_be_revalidated_at_the_chosen_port() {
    let device = DeviceInfo {
        port: "COM3".into(),
        vid: 0x1a86,
        pid: 0x5722,
        serial: "USB35INCHIPSV2".into(),
    };
    let selected = select_device(std::slice::from_ref(&device), None).unwrap();
    let changed = DeviceInfo {
        port: "com3".into(),
        serial: "other".into(),
        ..device.clone()
    };
    assert!(selected.revalidate(std::slice::from_ref(&device)).is_ok());
    assert_eq!(
        selected.revalidate(&[changed]).err(),
        Some(DeviceError::IdentityMismatch)
    );
    assert_eq!(selected.revalidate(&[]).err(), Some(DeviceError::NotFound));
}
