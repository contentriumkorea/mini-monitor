//! Bounded CPU and RAM primitives; a missing measurement is never guessed as zero.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CpuTimes {
    pub idle: u64,
    pub kernel: u64,
    pub user: u64,
}

pub fn cpu_usage(previous: CpuTimes, current: CpuTimes) -> Option<f64> {
    let idle = current.idle.checked_sub(previous.idle)?;
    let kernel = current.kernel.checked_sub(previous.kernel)?;
    let user = current.user.checked_sub(previous.user)?;
    let total = kernel.checked_add(user)?;
    if total == 0 || idle > total {
        return None;
    }
    Some((total - idle) as f64 * 100.0 / total as f64)
}

#[derive(Clone, Copy, Debug)]
pub struct MemorySnapshot {
    pub total_bytes: u64,
    pub available_bytes: u64,
    pub used_percent: f64,
}

#[cfg(windows)]
pub fn memory_snapshot() -> std::io::Result<MemorySnapshot> {
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
    let mut status: MEMORYSTATUSEX = unsafe { std::mem::zeroed() };
    status.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
    if unsafe { GlobalMemoryStatusEx(&mut status) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    if status.ullTotalPhys == 0 || status.ullAvailPhys > status.ullTotalPhys {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "invalid memory counters",
        ));
    }
    Ok(MemorySnapshot {
        total_bytes: status.ullTotalPhys,
        available_bytes: status.ullAvailPhys,
        used_percent: (status.ullTotalPhys - status.ullAvailPhys) as f64 * 100.0
            / status.ullTotalPhys as f64,
    })
}

#[cfg(windows)]
pub fn cpu_times() -> std::io::Result<CpuTimes> {
    use windows_sys::Win32::{Foundation::FILETIME, System::Threading::GetSystemTimes};
    let mut idle: FILETIME = unsafe { std::mem::zeroed() };
    let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
    let mut user: FILETIME = unsafe { std::mem::zeroed() };
    if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    fn value(time: FILETIME) -> u64 {
        (u64::from(time.dwHighDateTime) << 32) | u64::from(time.dwLowDateTime)
    }
    Ok(CpuTimes {
        idle: value(idle),
        kernel: value(kernel),
        user: value(user),
    })
}
