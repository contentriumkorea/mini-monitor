//! Guarded connection workflow; no protocol bytes are emitted during connect.
use crate::{
    device::{select_device, DeviceInfo},
    device_session::DeviceSession,
};
use std::io::{self, Write};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex,
};

/// Cancellation admission is serialized with opening. Call cancel from a worker, not the UI thread.
#[derive(Default)]
pub struct ConnectGate {
    cancelled: AtomicBool,
    opening: Mutex<()>,
}
impl ConnectGate {
    /// Signal cancellation without waiting for an in-flight blocking OS operation.
    pub fn request_cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }
    /// Wait until any admitted operation has returned. Do not call from a UI thread.
    pub fn cancel(&self) {
        self.request_cancel();
        drop(self.opening.lock().unwrap_or_else(|e| e.into_inner()));
    }
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }
    pub fn perform_if_active<W>(&self, open: impl FnOnce() -> io::Result<W>) -> io::Result<W> {
        let _guard = self.opening.lock().unwrap_or_else(|e| e.into_inner());
        if self.is_cancelled() {
            return Err(io::Error::other("connection cancelled"));
        }
        open()
    }
    #[cfg(windows)]
    pub(crate) fn configure_if_allowed<W>(
        &self,
        configure: impl FnOnce() -> io::Result<W>,
    ) -> io::Result<W> {
        self.perform_if_active(configure)
    }
}

pub fn connect_with_gate<W, D, O>(
    manual: Option<&str>,
    discover: D,
    mut open: O,
    gate: &ConnectGate,
) -> io::Result<DeviceSession<W>>
where
    W: Write,
    D: FnMut() -> io::Result<Vec<DeviceInfo>>,
    O: FnMut(&str) -> io::Result<W>,
{
    connect_with(
        manual,
        discover,
        |port| gate.perform_if_active(|| open(port)),
        || gate.is_cancelled(),
    )
}

pub struct SerialSettings {
    pub baud: u32,
    pub data_bits: u8,
    pub dcb_flags: u32,
    pub read_timeout_ms: u32,
    pub write_timeout_ms: u32,
}
pub fn serial_settings() -> SerialSettings {
    SerialSettings {
        baud: 115_200,
        data_bits: 8,
        dcb_flags: 1 | (1 << 4) | (1 << 12),
        read_timeout_ms: 1000,
        write_timeout_ms: 1000,
    }
}

/// Discovery and opener are injected for deterministic, hardware-free verification.
pub fn connect_with<W, D, O, C>(
    manual: Option<&str>,
    mut discover: D,
    mut open: O,
    cancelled: C,
) -> io::Result<DeviceSession<W>>
where
    W: Write,
    D: FnMut() -> io::Result<Vec<DeviceInfo>>,
    O: FnMut(&str) -> io::Result<W>,
    C: Fn() -> bool,
{
    if cancelled() {
        return Err(io::Error::other("connection cancelled"));
    }
    let initial = discover()?;
    let selected = select_device(&initial, manual)
        .map_err(|_| io::Error::new(io::ErrorKind::PermissionDenied, "display selection failed"))?;
    let before = discover()?;
    selected
        .revalidate(&before)
        .map_err(|_| io::Error::new(io::ErrorKind::PermissionDenied, "display identity changed"))?;
    if cancelled() {
        return Err(io::Error::other("connection cancelled"));
    }
    let writer = open(selected.port())?;
    if cancelled() {
        return Err(io::Error::other("connection cancelled"));
    }
    let after = discover()?;
    if cancelled() {
        return Err(io::Error::other("connection cancelled"));
    }
    DeviceSession::verified(selected, &after, writer)
}
