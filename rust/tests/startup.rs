use mini_monitor_native::startup::{build_command, disable, enable, StartupStore};
use std::io;
#[derive(Default)]
struct FakeStore(Option<String>);
impl StartupStore for FakeStore {
    fn read(&self) -> io::Result<Option<String>> {
        Ok(self.0.clone())
    }
    fn write(&mut self, command: &str) -> io::Result<()> {
        self.0 = Some(command.into());
        Ok(())
    }
    fn remove(&mut self) -> io::Result<()> {
        self.0 = None;
        Ok(())
    }
}
#[test]
fn quoted_startup_command_preserves_custom_config_and_minimized_flag() {
    assert_eq!(
        build_command(r"C:\Program Files\Mini-Monitor\Mini-Monitor.exe", None).unwrap(),
        r#""C:\Program Files\Mini-Monitor\Mini-Monitor.exe" --minimized"#
    );
    assert_eq!(
        build_command(
            r"C:\Mini Monitor\Mini-Monitor.exe",
            Some(r"D:\My Config\settings.json")
        )
        .unwrap(),
        r#""C:\Mini Monitor\Mini-Monitor.exe" --config "D:\My Config\settings.json" --minimized"#
    );
    assert!(build_command("", None).is_err());
    assert!(build_command("C:\\bad\"file.exe", None).is_err());
    assert!(build_command("relative.exe", None).is_err());
    assert!(build_command(r"C:\Mini-Monitor.exe", Some("relative.json")).is_err());
}

#[cfg(windows)]
#[test]
fn equivalent_legacy_quoting_is_owned_but_extra_arguments_and_other_apps_are_not() {
    let expected = build_command(r"C:\Mini-Monitor\Mini-Monitor.exe", None).unwrap();
    let legacy = r"C:\Mini-Monitor\Mini-Monitor.exe --minimized";
    assert!(mini_monitor_native::startup::equivalent(legacy, &expected));
    let mut store = FakeStore(Some(legacy.into()));
    assert!(!enable(&mut store, &expected).unwrap());
    assert!(disable(&mut store, &expected).unwrap());
    assert!(!mini_monitor_native::startup::equivalent(
        r"C:\Other\Mini-Monitor.exe --minimized",
        &expected
    ));
    assert!(!mini_monitor_native::startup::equivalent(
        r"C:\Mini-Monitor\Mini-Monitor.exe --minimized --no-serial",
        &expected
    ));
    let custom = build_command(
        r"C:\Mini-Monitor\Mini-Monitor.exe",
        Some(r"D:\My Config\settings.json"),
    )
    .unwrap();
    assert!(mini_monitor_native::startup::equivalent(
        r#"C:\Mini-Monitor\Mini-Monitor.exe --minimized --config "D:\My Config\settings.json""#,
        &custom
    ));
}
#[test]
fn only_the_exact_owned_startup_command_can_be_removed_or_replaced() {
    let expected = build_command(r"C:\Mini Monitor\Mini-Monitor.exe", None).unwrap();
    let mut store = FakeStore::default();
    assert!(enable(&mut store, &expected).unwrap());
    assert!(!enable(&mut store, &expected).unwrap());
    assert!(disable(&mut store, &expected).unwrap());
    assert!(!disable(&mut store, &expected).unwrap());
    store.0 = Some("unrelated custom entry".into());
    assert!(enable(&mut store, &expected).is_err());
    assert!(disable(&mut store, &expected).is_err());
    assert_eq!(store.0.as_deref(), Some("unrelated custom entry"));
}

#[cfg(windows)]
#[test]
fn current_user_startup_value_can_be_read_without_changing_it() {
    let store = mini_monitor_native::startup::WindowsStartupStore::new();
    assert!(store.read().is_ok());
}
