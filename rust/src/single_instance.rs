//! Session-local guard compatible with the existing Mini Monitor process.
use std::{io, ptr};
use windows_sys::Win32::{
    Foundation::{CloseHandle, GetLastError, SetLastError, ERROR_ALREADY_EXISTS, HANDLE},
    System::Threading::CreateMutexW,
};

pub const APP_MUTEX: &str = r"Local\AI-Mini-Monitor-7C39A56E-8751-48D5-8D20-4A9E65007FB1";
pub struct InstanceGuard(HANDLE);
impl InstanceGuard {
    pub fn try_acquire(name: &str) -> io::Result<Option<Self>> {
        if name.trim().is_empty() || name.contains('\0') || name.len() > 240 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid mutex name",
            ));
        }
        let name: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
        unsafe {
            SetLastError(0);
            let handle = CreateMutexW(ptr::null(), 0, name.as_ptr());
            let code = GetLastError();
            if handle.is_null() {
                return Err(io::Error::from_raw_os_error(code as i32));
            }
            if code == ERROR_ALREADY_EXISTS {
                CloseHandle(handle);
                return Ok(None);
            }
            Ok(Some(Self(handle)))
        }
    }
}
impl Drop for InstanceGuard {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
