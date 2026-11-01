//! Exclusive Windows COM transport. Nothing opens until connect is called explicitly.
use crate::{
    device_connect::{connect_with_gate, serial_settings, ConnectGate},
    device_discovery::discover,
    device_session::DeviceSession,
};
use std::{
    fs::File,
    io,
    os::windows::io::{AsRawHandle, FromRawHandle},
    ptr,
};
use windows_sys::Win32::{
    Devices::Communication::{
        EscapeCommFunction, GetCommState, SetCommState, SetCommTimeouts, SetupComm, COMMTIMEOUTS,
        DCB, NOPARITY, ONESTOPBIT, SETDTR, SETRTS,
    },
    Foundation::{GENERIC_READ, GENERIC_WRITE, INVALID_HANDLE_VALUE},
    Storage::FileSystem::{CreateFileW, FILE_ATTRIBUTE_NORMAL, OPEN_EXISTING},
};

fn open_raw(port: &str) -> io::Result<File> {
    // `port` can only originate from the validated SelectedDevice path.
    let path: Vec<u16> = format!("\\\\.\\{port}\0").encode_utf16().collect();
    let handle = unsafe {
        CreateFileW(
            path.as_ptr(),
            GENERIC_READ | GENERIC_WRITE,
            0,
            ptr::null(),
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            ptr::null_mut(),
        )
    };
    if handle == INVALID_HANDLE_VALUE || handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_handle(handle) })
}

fn configure(file: &File) -> io::Result<()> {
    let handle = file.as_raw_handle();
    if unsafe { SetupComm(handle, 4096, 4096) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut dcb: DCB = unsafe { std::mem::zeroed() };
    dcb.DCBlength = std::mem::size_of::<DCB>() as u32;
    if unsafe { GetCommState(handle, &mut dcb) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let settings = serial_settings();
    dcb.BaudRate = settings.baud;
    dcb.ByteSize = settings.data_bits;
    dcb.Parity = NOPARITY;
    dcb.StopBits = ONESTOPBIT;
    dcb._bitfield = settings.dcb_flags;
    if unsafe { SetCommState(handle, &dcb) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let timeouts = COMMTIMEOUTS {
        ReadIntervalTimeout: 0,
        ReadTotalTimeoutMultiplier: 0,
        ReadTotalTimeoutConstant: settings.read_timeout_ms,
        WriteTotalTimeoutMultiplier: 0,
        WriteTotalTimeoutConstant: settings.write_timeout_ms,
    };
    if unsafe { SetCommTimeouts(handle, &timeouts) } == 0
        || unsafe { EscapeCommFunction(handle, SETDTR) } == 0
        || unsafe { EscapeCommFunction(handle, SETRTS) } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

/// Opens only a rediscovered exact display, configures 115200 8N1, and sends no bytes.
/// This can block and must run off the UI thread.
pub fn connect(manual: Option<&str>, gate: &ConnectGate) -> io::Result<DeviceSession<File>> {
    let mut session = connect_with_gate(manual, discover, open_raw, gate)?;
    if gate.is_cancelled() {
        return Err(io::Error::other("connection cancelled"));
    }
    gate.configure_if_allowed(|| configure(session.writer_mut()?))?;
    if gate.is_cancelled() {
        return Err(io::Error::other("connection cancelled"));
    }
    Ok(session)
}
