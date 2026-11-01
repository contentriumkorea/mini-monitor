//! Explicit opt-in startup operations; this module never mutates the registry on its own.
use std::io;

pub trait StartupStore {
    fn read(&self) -> io::Result<Option<String>>;
    fn write(&mut self, command: &str) -> io::Result<()>;
    fn remove(&mut self) -> io::Result<()>;
}
fn valid_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    let drive = bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && matches!(bytes[2], b'\\' | b'/');
    let unc = value.starts_with("\\\\") && value.len() > 3;
    (drive || unc)
        && value == value.trim()
        && !value.trim().is_empty()
        && !value.chars().any(|c| c == '"' || c.is_control())
        && !value.ends_with('\\')
        && !value.ends_with('/')
}
pub fn build_command(executable: &str, config: Option<&str>) -> io::Result<String> {
    if !valid_path(executable) || config.is_some_and(|value| !valid_path(value)) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid startup path",
        ));
    }
    let mut output = format!("\"{executable}\"");
    if let Some(path) = config {
        output.push_str(" --config ");
        if path.contains([' ', '\t']) {
            output.push('"');
            output.push_str(path);
            output.push('"');
        } else {
            output.push_str(path);
        }
    }
    output.push_str(" --minimized");
    Ok(output)
}
fn validate_command(command: &str) -> io::Result<()> {
    if command.is_empty() || command.chars().any(char::is_control) {
        Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid startup command",
        ))
    } else {
        Ok(())
    }
}

pub fn equivalent(current: &str, expected: &str) -> bool {
    #[cfg(not(windows))]
    {
        current == expected
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::{Foundation::LocalFree, UI::Shell::CommandLineToArgvW};
        fn parse(command: &str) -> Option<(String, Option<String>, bool)> {
            if command.len() > 32768 || command.is_empty() || command.chars().any(char::is_control)
            {
                return None;
            }
            let value: Vec<u16> = command.encode_utf16().chain(Some(0)).collect();
            let mut count = 0;
            let raw = unsafe { CommandLineToArgvW(value.as_ptr(), &mut count) };
            if raw.is_null() {
                return None;
            }
            let mut args = Vec::new();
            if (1..=8).contains(&count) {
                for index in 0..count as usize {
                    let p = unsafe { *raw.add(index) };
                    let mut len = 0;
                    while unsafe { *p.add(len) } != 0 {
                        len += 1;
                    }
                    args.push(String::from_utf16_lossy(unsafe {
                        std::slice::from_raw_parts(p, len)
                    }));
                }
            }
            unsafe {
                LocalFree(raw.cast());
            }
            if args.is_empty() || !valid_path(&args[0]) {
                return None;
            }
            let normalize = |text: &str| text.replace('/', "\\").to_lowercase();
            let exe = normalize(&args[0]);
            let mut config = None;
            let mut minimized = false;
            let mut index = 1;
            while index < args.len() {
                match args[index].as_str() {
                    "--minimized" if !minimized => minimized = true,
                    "--config" if config.is_none() && index + 1 < args.len() => {
                        index += 1;
                        if !valid_path(&args[index]) {
                            return None;
                        }
                        config = Some(normalize(&args[index]));
                    }
                    _ => return None,
                }
                index += 1;
            }
            Some((exe, config, minimized))
        }
        match (parse(current), parse(expected)) {
            (Some(a), Some(b)) => a == b,
            _ => false,
        }
    }
}
/// Never overwrite a customized or unrelated value under the same registry name.
pub fn enable(store: &mut (impl StartupStore + ?Sized), expected: &str) -> io::Result<bool> {
    validate_command(expected)?;
    match store.read()? {
        Some(current) if equivalent(&current, expected) => Ok(false),
        Some(_) => Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "startup value differs",
        )),
        None => {
            store.write(expected)?;
            Ok(true)
        }
    }
}
/// Never delete a customized or unrelated value under the same registry name.
pub fn disable(store: &mut (impl StartupStore + ?Sized), expected: &str) -> io::Result<bool> {
    validate_command(expected)?;
    match store.read()? {
        Some(current) if equivalent(&current, expected) => {
            store.remove()?;
            Ok(true)
        }
        Some(_) => Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "startup value differs",
        )),
        None => Ok(false),
    }
}

#[cfg(windows)]
mod windows {
    use super::{io, StartupStore};
    use std::ptr;
    use windows_sys::Win32::{
        Foundation::ERROR_FILE_NOT_FOUND,
        System::Registry::{
            RegCloseKey, RegCreateKeyExW, RegDeleteValueW, RegOpenKeyExW, RegQueryValueExW,
            RegSetValueExW, HKEY, HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE, REG_EXPAND_SZ,
            REG_OPTION_NON_VOLATILE, REG_SZ,
        },
    };
    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }
    fn error(code: u32) -> io::Error {
        io::Error::from_raw_os_error(code as i32)
    }
    struct Key(HKEY);
    impl Drop for Key {
        fn drop(&mut self) {
            unsafe {
                RegCloseKey(self.0);
            }
        }
    }
    /// Fixed current-user location and value name, compatible with the existing app.
    pub struct WindowsStartupStore;
    impl Default for WindowsStartupStore {
        fn default() -> Self {
            Self::new()
        }
    }
    impl WindowsStartupStore {
        pub fn new() -> Self {
            Self
        }
        fn open(&self, write: bool) -> io::Result<Option<Key>> {
            let path = wide(r"Software\Microsoft\Windows\CurrentVersion\Run");
            let mut key = ptr::null_mut();
            let code = unsafe {
                RegOpenKeyExW(
                    HKEY_CURRENT_USER,
                    path.as_ptr(),
                    0,
                    if write {
                        KEY_SET_VALUE
                    } else {
                        KEY_QUERY_VALUE
                    },
                    &mut key,
                )
            };
            if code == ERROR_FILE_NOT_FOUND {
                return Ok(None);
            }
            if code != 0 {
                return Err(error(code));
            }
            Ok(Some(Key(key)))
        }
    }
    impl StartupStore for WindowsStartupStore {
        fn read(&self) -> io::Result<Option<String>> {
            let Some(key) = self.open(false)? else {
                return Ok(None);
            };
            let name = wide("AI Mini Monitor");
            let mut kind = 0u32;
            let mut size = 0u32;
            let code = unsafe {
                RegQueryValueExW(
                    key.0,
                    name.as_ptr(),
                    ptr::null(),
                    &mut kind,
                    ptr::null_mut(),
                    &mut size,
                )
            };
            if code == ERROR_FILE_NOT_FOUND {
                return Ok(None);
            }
            if code != 0 {
                return Err(error(code));
            }
            if !matches!(kind, REG_SZ | REG_EXPAND_SZ)
                || size == 0
                || size > 65536
                || !size.is_multiple_of(2)
            {
                return Err(io::Error::other("invalid startup value"));
            }
            let mut data = vec![0u16; (size / 2) as usize];
            let code = unsafe {
                RegQueryValueExW(
                    key.0,
                    name.as_ptr(),
                    ptr::null(),
                    &mut kind,
                    data.as_mut_ptr().cast(),
                    &mut size,
                )
            };
            if code != 0 {
                return Err(error(code));
            }
            if !matches!(kind, REG_SZ | REG_EXPAND_SZ)
                || size == 0
                || !size.is_multiple_of(2)
                || size as usize > data.len() * 2
            {
                return Err(io::Error::other("invalid startup value"));
            }
            data.truncate((size / 2) as usize);
            let Some(end) = data.iter().position(|v| *v == 0) else {
                return Err(io::Error::other("unterminated startup value"));
            };
            if data[end..].iter().any(|v| *v != 0) {
                return Err(io::Error::other("invalid startup value"));
            }
            let value = String::from_utf16(&data[..end])
                .map_err(|_| io::Error::other("invalid startup value"))?;
            if value.is_empty() {
                return Err(io::Error::other("empty startup value"));
            }
            Ok(Some(value))
        }
        fn write(&mut self, command: &str) -> io::Result<()> {
            super::validate_command(command)?;
            let command = wide(command);
            let size = u32::try_from(
                command
                    .len()
                    .checked_mul(2)
                    .ok_or_else(|| io::Error::other("startup value too long"))?,
            )
            .map_err(|_| io::Error::other("startup value too long"))?;
            if size > 65536 {
                return Err(io::Error::other("startup value too long"));
            }
            let path = wide(r"Software\Microsoft\Windows\CurrentVersion\Run");
            let name = wide("AI Mini Monitor");
            let mut key = ptr::null_mut();
            let code = unsafe {
                RegCreateKeyExW(
                    HKEY_CURRENT_USER,
                    path.as_ptr(),
                    0,
                    ptr::null(),
                    REG_OPTION_NON_VOLATILE,
                    KEY_SET_VALUE,
                    ptr::null(),
                    &mut key,
                    ptr::null_mut(),
                )
            };
            if code != 0 {
                return Err(error(code));
            }
            let key = Key(key);
            let code = unsafe {
                RegSetValueExW(
                    key.0,
                    name.as_ptr(),
                    0,
                    REG_SZ,
                    command.as_ptr().cast(),
                    size,
                )
            };
            if code != 0 {
                return Err(error(code));
            }
            Ok(())
        }
        fn remove(&mut self) -> io::Result<()> {
            let Some(key) = self.open(true)? else {
                return Ok(());
            };
            let name = wide("AI Mini Monitor");
            let code = unsafe { RegDeleteValueW(key.0, name.as_ptr()) };
            if code != 0 && code != ERROR_FILE_NOT_FOUND {
                return Err(error(code));
            }
            Ok(())
        }
    }
}
#[cfg(windows)]
pub use windows::WindowsStartupStore;
