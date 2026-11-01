//! Protocol state machine; real transport must re-enumerate immediately before verification.
use crate::device::{
    brightness_command, orientation_command, screen_on, BitmapTransfer, DeviceInfo, Orientation,
    SelectedDevice,
};
use std::{
    io::{self, Write},
    time::Duration,
};

pub struct DeviceSession<W: Write> {
    selected: SelectedDevice,
    writer: Option<W>,
    dimensions: Option<(u32, u32)>,
    full_valid: bool,
}
impl<W: Write> DeviceSession<W> {
    #[cfg(windows)]
    pub(crate) fn writer_mut(&mut self) -> io::Result<&mut W> {
        self.writer
            .as_mut()
            .ok_or_else(|| io::Error::other("display session closed"))
    }
    /// Caller opens the exact selected port, then passes a fresh read-only rediscovery here.
    pub fn verified(
        selected: SelectedDevice,
        current: &[DeviceInfo],
        writer: W,
    ) -> io::Result<Self> {
        selected.revalidate(current).map_err(|_| {
            io::Error::new(io::ErrorKind::PermissionDenied, "display identity changed")
        })?;
        Ok(Self {
            selected,
            writer: Some(writer),
            dimensions: None,
            full_valid: false,
        })
    }
    pub fn revalidate(&mut self, current: &[DeviceInfo]) -> io::Result<()> {
        if self.selected.revalidate(current).is_err() {
            self.writer.take();
            self.full_valid = false;
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "display identity changed",
            ));
        }
        Ok(())
    }
    fn write_exact(&mut self, bytes: &[u8]) -> io::Result<()> {
        let result = self
            .writer
            .as_mut()
            .ok_or_else(|| io::Error::other("display session closed"))?
            .write_all(bytes);
        if result.is_err() {
            self.writer.take();
            self.full_valid = false;
        }
        result
    }
    /// Vendor startup sequence. A false wait callback cancels and closes the session.
    pub fn start<F: FnMut(Duration) -> bool>(
        &mut self,
        brightness: u8,
        orientation: Orientation,
        wait: F,
    ) -> io::Result<()> {
        self.start_cancellable(brightness, orientation, wait, || false)
    }
    /// A caller requiring cancellation synchronization must also serialize writes with its gate.
    pub fn start_cancellable<F: FnMut(Duration) -> bool>(
        &mut self,
        brightness: u8,
        orientation: Orientation,
        mut wait: F,
        cancelled: impl Fn() -> bool,
    ) -> io::Result<()> {
        if self.dimensions.is_some() {
            return Err(io::Error::other("display already started"));
        }
        let brightness = brightness_command(brightness)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid brightness"))?;
        self.check_cancelled(&cancelled)?;
        self.write_exact(&brightness)?;
        self.check_cancelled(&cancelled)?;
        self.write_exact(&screen_on())?;
        if !wait(Duration::from_millis(100)) {
            self.writer.take();
            return Err(io::Error::other("display start cancelled"));
        }
        self.check_cancelled(&cancelled)?;
        self.write_exact(&orientation_command(orientation))?;
        if !wait(Duration::from_millis(500)) {
            self.writer.take();
            return Err(io::Error::other("display start cancelled"));
        }
        self.check_cancelled(&cancelled)?;
        self.write_exact(&brightness)?;
        self.check_cancelled(&cancelled)?;
        self.dimensions = Some(match orientation {
            Orientation::Portrait | Orientation::ReversePortrait => (320, 480),
            _ => (480, 320),
        });
        Ok(())
    }
    fn check_cancelled(&mut self, cancelled: &impl Fn() -> bool) -> io::Result<()> {
        if cancelled() {
            self.writer.take();
            self.full_valid = false;
            return Err(io::Error::other("display start cancelled"));
        }
        Ok(())
    }
    pub fn send_full(&mut self, transfer: &BitmapTransfer) -> io::Result<()> {
        self.send_full_cancellable(transfer, || false)
    }
    pub fn send_full_cancellable(
        &mut self,
        transfer: &BitmapTransfer,
        cancelled: impl Fn() -> bool,
    ) -> io::Result<()> {
        let (width, height) = self
            .dimensions
            .ok_or_else(|| io::Error::other("display not started"))?;
        if !transfer.is_full_for(width, height) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "expected full frame",
            ));
        }
        self.send(transfer, cancelled)?;
        self.full_valid = true;
        Ok(())
    }

    pub fn set_brightness(&mut self, brightness: u8) -> io::Result<()> {
        if self.dimensions.is_none() {
            return Err(io::Error::other("display not started"));
        }
        let command = brightness_command(brightness)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid brightness"))?;
        self.write_exact(&command)
    }

    pub fn set_orientation<F: FnMut(Duration) -> bool>(
        &mut self,
        orientation: Orientation,
        brightness: u8,
        mut wait: F,
    ) -> io::Result<()> {
        if self.dimensions.is_none() {
            return Err(io::Error::other("display not started"));
        }
        let brightness = brightness_command(brightness)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid brightness"))?;
        self.full_valid = false;
        self.write_exact(&orientation_command(orientation))?;
        if !wait(Duration::from_millis(500)) {
            self.writer.take();
            return Err(io::Error::other("orientation change cancelled"));
        }
        self.write_exact(&brightness)?;
        self.dimensions = Some(match orientation {
            Orientation::Portrait | Orientation::ReversePortrait => (320, 480),
            _ => (480, 320),
        });
        Ok(())
    }
    pub fn send_partial(&mut self, transfer: &BitmapTransfer) -> io::Result<()> {
        self.send_partial_cancellable(transfer, || false)
    }
    pub fn send_partial_cancellable(
        &mut self,
        transfer: &BitmapTransfer,
        cancelled: impl Fn() -> bool,
    ) -> io::Result<()> {
        let (width, height) = self
            .dimensions
            .ok_or_else(|| io::Error::other("display not started"))?;
        if !self.full_valid || !transfer.matches_display(width, height) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "full frame required",
            ));
        }
        self.send(transfer, cancelled)
    }
    fn send(&mut self, transfer: &BitmapTransfer, cancelled: impl Fn() -> bool) -> io::Result<()> {
        let result = transfer.write_to_cancellable(
            self.writer
                .as_mut()
                .ok_or_else(|| io::Error::other("display session closed"))?,
            480 * 8,
            cancelled,
        );
        if result.is_err() {
            self.writer.take();
            self.full_valid = false;
        }
        result
    }
}
