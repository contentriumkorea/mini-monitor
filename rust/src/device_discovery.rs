//! Read-only discovery of the supported USB serial display; never opens a COM port.

fn instance_parts(value: &str) -> Option<(u16, u16, &str)> {
    let mut parts = value.split('\\');
    if !parts.next()?.eq_ignore_ascii_case("USB") {
        return None;
    }
    let hardware = parts.next()?;
    let serial = parts.next()?;
    if parts.next().is_some() {
        return None;
    }
    let mut vid = None;
    let mut pid = None;
    for field in hardware.split('&') {
        let upper = field.to_ascii_uppercase();
        if let Some(text) = upper.strip_prefix("VID_") {
            if vid.is_some() || text.len() != 4 {
                return None;
            }
            vid = Some(u16::from_str_radix(text, 16).ok()?);
        } else if let Some(text) = upper.strip_prefix("PID_") {
            if pid.is_some() || text.len() != 4 {
                return None;
            }
            pid = Some(u16::from_str_radix(text, 16).ok()?);
        }
    }
    Some((vid?, pid?, serial))
}

/// Traversal stops if an identity changes or a concrete nonmatching serial is found.
pub fn matches_usb_chain(chain: &[&str]) -> bool {
    if chain.is_empty() || chain.len() > 6 {
        return false;
    }
    for item in chain {
        let Some((vid, pid, serial)) = instance_parts(item) else {
            return false;
        };
        if vid != 0x1a86 || pid != 0x5722 {
            return false;
        }
        if serial == "USB35INCHIPSV2" {
            return true;
        }
        if !serial.is_empty()
            && serial
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        {
            return false;
        }
    }
    false
}

#[cfg(windows)]
mod native {
    use super::{instance_parts, matches_usb_chain};
    use crate::device::{select_device, DeviceInfo};
    use std::{io, ptr};
    use windows_sys::{
        core::GUID,
        Win32::{
            Devices::DeviceAndDriverInstallation::{
                CM_Get_Device_IDW, CM_Get_Parent, SetupDiClassGuidsFromNameW,
                SetupDiDestroyDeviceInfoList, SetupDiEnumDeviceInfo, SetupDiGetClassDevsW,
                SetupDiGetDeviceInstanceIdW, SetupDiOpenDevRegKey, DICS_FLAG_GLOBAL, DIGCF_PRESENT,
                DIREG_DEV, HDEVINFO, SP_DEVINFO_DATA,
            },
            Foundation::{GetLastError, ERROR_NO_MORE_ITEMS, INVALID_HANDLE_VALUE},
            System::Registry::{RegCloseKey, RegQueryValueExW, HKEY, KEY_READ, REG_SZ},
        },
    };

    struct DeviceSet(HDEVINFO);
    impl Drop for DeviceSet {
        fn drop(&mut self) {
            unsafe {
                SetupDiDestroyDeviceInfoList(self.0);
            }
        }
    }
    struct Key(HKEY);
    impl Drop for Key {
        fn drop(&mut self) {
            unsafe {
                RegCloseKey(self.0);
            }
        }
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }
    fn decode(buffer: &[u16]) -> Option<String> {
        let end = buffer.iter().position(|c| *c == 0)?;
        String::from_utf16(&buffer[..end]).ok()
    }

    /// Enumerate present Windows Ports-class devices and their USB parents.
    pub fn discover() -> io::Result<Vec<DeviceInfo>> {
        let mut guids: [GUID; 8] = unsafe { std::mem::zeroed() };
        let mut count = 0u32;
        if unsafe {
            SetupDiClassGuidsFromNameW(
                wide("Ports").as_ptr(),
                guids.as_mut_ptr(),
                guids.len() as u32,
                &mut count,
            )
        } == 0
            || count as usize > guids.len()
        {
            return Err(io::Error::other("port class unavailable"));
        }
        let mut found = Vec::new();
        for guid in &guids[..count as usize] {
            let raw =
                unsafe { SetupDiGetClassDevsW(guid, ptr::null(), ptr::null_mut(), DIGCF_PRESENT) };
            if raw == -1 {
                return Err(io::Error::other("device enumeration failed"));
            }
            let set = DeviceSet(raw);
            let mut exhausted = false;
            for index in 0..4096u32 {
                let mut info: SP_DEVINFO_DATA = unsafe { std::mem::zeroed() };
                info.cbSize = std::mem::size_of::<SP_DEVINFO_DATA>() as u32;
                if unsafe { SetupDiEnumDeviceInfo(set.0, index, &mut info) } == 0 {
                    if unsafe { GetLastError() } == ERROR_NO_MORE_ITEMS {
                        exhausted = true;
                        break;
                    }
                    return Err(io::Error::other("device enumeration failed"));
                }
                let mut instance = [0u16; 512];
                if unsafe {
                    SetupDiGetDeviceInstanceIdW(
                        set.0,
                        &info,
                        instance.as_mut_ptr(),
                        instance.len() as u32,
                        ptr::null_mut(),
                    )
                } == 0
                {
                    return Err(io::Error::other("device identity unavailable"));
                }
                let Some(initial) = decode(&instance) else {
                    return Err(io::Error::other("device identity unavailable"));
                };
                if !instance_parts(&initial).is_some_and(|(v, p, _)| v == 0x1a86 && p == 0x5722) {
                    continue;
                }
                let mut chain = vec![initial];
                let mut current = info.DevInst;
                for _ in 0..5 {
                    if matches_usb_chain(&chain.iter().map(String::as_str).collect::<Vec<_>>()) {
                        break;
                    }
                    let mut parent = 0u32;
                    if unsafe { CM_Get_Parent(&mut parent, current, 0) } != 0 {
                        break;
                    }
                    let mut id = [0u16; 512];
                    if unsafe { CM_Get_Device_IDW(parent, id.as_mut_ptr(), id.len() as u32, 0) }
                        != 0
                    {
                        break;
                    }
                    let Some(text) = decode(&id) else {
                        break;
                    };
                    chain.push(text);
                    current = parent;
                }
                if !matches_usb_chain(&chain.iter().map(String::as_str).collect::<Vec<_>>()) {
                    let Some((_, _, serial)) = instance_parts(&chain[0]) else {
                        return Err(io::Error::other("device identity unavailable"));
                    };
                    if !serial.is_empty()
                        && serial
                            .bytes()
                            .all(|c| c.is_ascii_alphanumeric() || c == b'_')
                    {
                        continue;
                    }
                    return Err(io::Error::other("USB parent identity unavailable"));
                }
                let raw_key = unsafe {
                    SetupDiOpenDevRegKey(set.0, &info, DICS_FLAG_GLOBAL, 0, DIREG_DEV, KEY_READ)
                };
                if raw_key.is_null() || raw_key == INVALID_HANDLE_VALUE {
                    return Err(io::Error::other("display port unavailable"));
                }
                let key = Key(raw_key);
                let mut buffer = [0u16; 64];
                let mut bytes = std::mem::size_of_val(&buffer) as u32;
                let mut kind = 0u32;
                let result = unsafe {
                    RegQueryValueExW(
                        key.0,
                        wide("PortName").as_ptr(),
                        ptr::null(),
                        &mut kind,
                        buffer.as_mut_ptr().cast(),
                        &mut bytes,
                    )
                };
                if result != 0
                    || kind != REG_SZ
                    || bytes as usize > std::mem::size_of_val(&buffer)
                    || !bytes.is_multiple_of(2)
                {
                    return Err(io::Error::other("display port unavailable"));
                }
                let Some(port) = decode(&buffer[..bytes as usize / 2]) else {
                    return Err(io::Error::other("display port unavailable"));
                };
                let device = DeviceInfo {
                    port,
                    vid: 0x1a86,
                    pid: 0x5722,
                    serial: "USB35INCHIPSV2".into(),
                };
                if select_device(std::slice::from_ref(&device), None).is_err() {
                    return Err(io::Error::other("invalid display port"));
                }
                found.push(device);
            }
            if !exhausted {
                return Err(io::Error::other("too many devices"));
            }
        }
        Ok(found)
    }
}

#[cfg(windows)]
pub use native::discover;
