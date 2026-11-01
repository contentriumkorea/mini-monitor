use mini_monitor_native::device_discovery::matches_usb_chain;

#[test]
fn only_exact_usb_identity_or_matching_composite_parent_is_accepted() {
    assert!(matches_usb_chain(&[
        r"USB\VID_1A86&PID_5722\USB35INCHIPSV2"
    ]));
    assert!(matches_usb_chain(&[
        r"USB\VID_1A86&PID_5722&MI_00\6&123&0&0000",
        r"USB\VID_1A86&PID_5722\USB35INCHIPSV2"
    ]));
    assert!(!matches_usb_chain(&[
        r"USB\VID_1A86&PID_5723\USB35INCHIPSV2"
    ]));
    assert!(!matches_usb_chain(&[
        r"USB\VID_1A86&PID_5722\OTHER",
        r"USB\VID_1A86&PID_5722\USB35INCHIPSV2"
    ]));
    assert!(!matches_usb_chain(&[
        r"USB\VID_1A86&PID_5722&MI_00\6&123",
        r"USB\VID_1A86&PID_5723\USB35INCHIPSV2"
    ]));
    assert!(!matches_usb_chain(&[
        r"USB\VID_1A86&PID_5722\USB35INCHIPSV2\extra"
    ]));
}

#[cfg(windows)]
#[test]
#[ignore = "requires a connected supported USB display; discovery is read-only"]
fn real_supported_display_is_discovered_without_opening_port() {
    use mini_monitor_native::{device::select_device, device_discovery::discover};
    let devices = discover().unwrap();
    assert!(select_device(&devices, None).is_ok());
}
