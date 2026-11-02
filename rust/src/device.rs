//! USB35INCHIPSV2 audited rev-A command generation; this module does no I/O.
//! Command format derived from turing-smart-screen-python, GPL-3.0-or-later.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Orientation {
    Portrait = 0,
    ReversePortrait = 1,
    Landscape = 2,
    ReverseLandscape = 3,
}

pub fn identity_matches(vid: u16, pid: u16, serial: &str) -> bool {
    vid == 0x1a86 && pid == 0x5722 && serial == "USB35INCHIPSV2"
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DeviceInfo {
    pub port: String,
    pub vid: u16,
    pub pid: u16,
    pub serial: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeviceError {
    InvalidPort,
    NotFound,
    IdentityMismatch,
    Ambiguous,
}

/// Only this type can represent a selected, identity-checked display.
pub struct SelectedDevice(DeviceInfo);

fn valid_com_port(value: &str) -> bool {
    let Some(prefix) = value.get(..3) else {
        return false;
    };
    let Some(number) = value.get(3..) else {
        return false;
    };
    prefix.eq_ignore_ascii_case("COM")
        && !number.is_empty()
        && number.bytes().all(|b| b.is_ascii_digit())
        && number.parse::<u16>().is_ok_and(|n| n > 0)
}

pub fn select_device(
    devices: &[DeviceInfo],
    manual: Option<&str>,
) -> Result<SelectedDevice, DeviceError> {
    if manual.is_some_and(|port| !valid_com_port(port)) {
        return Err(DeviceError::InvalidPort);
    }
    let mut matches = devices.iter().filter(|item| {
        valid_com_port(&item.port)
            && match manual {
                Some(port) => item.port.eq_ignore_ascii_case(port),
                None => identity_matches(item.vid, item.pid, &item.serial),
            }
    });
    let selected = matches.next().ok_or(DeviceError::NotFound)?;
    if matches.next().is_some() {
        return Err(DeviceError::Ambiguous);
    }
    if !identity_matches(selected.vid, selected.pid, &selected.serial) {
        return Err(DeviceError::IdentityMismatch);
    }
    Ok(SelectedDevice(selected.clone()))
}

impl SelectedDevice {
    pub fn port(&self) -> &str {
        &self.0.port
    }

    /// A real transport must rediscover and call this immediately before opening and writing.
    pub fn revalidate(&self, devices: &[DeviceInfo]) -> Result<(), DeviceError> {
        let found = select_device(devices, Some(&self.0.port))?;
        if !found.0.port.eq_ignore_ascii_case(&self.0.port)
            || found.0.vid != self.0.vid
            || found.0.pid != self.0.pid
            || found.0.serial != self.0.serial
        {
            return Err(DeviceError::IdentityMismatch);
        }
        Ok(())
    }
}

pub fn screen_on() -> [u8; 6] {
    [0, 0, 0, 0, 0, 0x6d]
}

pub fn brightness_command(percent: u8) -> Result<[u8; 6], &'static str> {
    if !(1..=50).contains(&percent) {
        return Err("brightness must be 1..50");
    }
    // Match the reference's truncation of the inverted 8-bit percentage.
    let raw = ((25500u32 - u32::from(percent) * 255) / 100) as u8;
    Ok([raw >> 2, (raw & 3) << 6, 0, 0, 0, 0x6e])
}

pub fn orientation_command(orientation: Orientation) -> [u8; 16] {
    let (width, height): (u16, u16) = match orientation {
        Orientation::Portrait | Orientation::ReversePortrait => (320, 480),
        _ => (480, 320),
    };
    let mut command = [0; 16];
    command[5] = 0x79;
    command[6] = 100 + orientation as u8;
    command[7..9].copy_from_slice(&width.to_be_bytes());
    command[9..11].copy_from_slice(&height.to_be_bytes());
    command
}

pub fn bitmap_command(
    x: u32,
    y: u32,
    width: u32,
    height: u32,
    display_width: u32,
    display_height: u32,
) -> Result<[u8; 6], &'static str> {
    if !(1..=1024).contains(&display_width)
        || !(1..=1024).contains(&display_height)
        || width == 0
        || height == 0
    {
        return Err("invalid dimensions");
    }
    let right = x.checked_add(width).ok_or("invalid rectangle")?;
    let bottom = y.checked_add(height).ok_or("invalid rectangle")?;
    if right > display_width || bottom > display_height {
        return Err("rectangle out of bounds");
    }
    let xe = right - 1;
    let ye = bottom - 1;
    Ok([
        (x >> 2) as u8,
        (((x & 3) << 6) | (y >> 4)) as u8,
        (((y & 15) << 4) | (xe >> 6)) as u8,
        (((xe & 63) << 2) | (ye >> 8)) as u8,
        ye as u8,
        0xc5,
    ])
}

pub fn rgb888_to_rgb565le(
    rgb: &[u8],
    width: usize,
    height: usize,
) -> Result<Vec<u8>, &'static str> {
    let count = width
        .checked_mul(height)
        .filter(|n| *n > 0)
        .ok_or("invalid size")?;
    if count.checked_mul(3) != Some(rgb.len()) {
        return Err("invalid RGB length");
    }
    let mut result = Vec::with_capacity(count.checked_mul(2).ok_or("invalid size")?);
    for pixel in rgb.as_chunks::<3>().0 {
        let packed = (u16::from(pixel[0] >> 3) << 11)
            | (u16::from(pixel[1] >> 2) << 5)
            | u16::from(pixel[2] >> 3);
        result.extend_from_slice(&packed.to_le_bytes());
    }
    Ok(result)
}

/// One validated command and exact pixel buffer, owned together to prevent mismatches.
pub struct BitmapTransfer {
    command: [u8; 6],
    payload: Vec<u8>,
    rect: (u32, u32, u32, u32),
    display: (u32, u32),
}

pub enum FrameDelta {
    Unchanged,
    Full,
    Region(BitmapTransfer),
    Regions(Vec<BitmapTransfer>),
}

pub fn bitmap_transfer(
    x: u32,
    y: u32,
    width: u32,
    height: u32,
    pixels: &[u8],
    display_width: u32,
    display_height: u32,
) -> Result<BitmapTransfer, &'static str> {
    let command = bitmap_command(x, y, width, height, display_width, display_height)?;
    let expected = (width as usize)
        .checked_mul(height as usize)
        .and_then(|n| n.checked_mul(2))
        .ok_or("invalid size")?;
    if pixels.len() != expected {
        return Err("invalid RGB565 payload length");
    }
    Ok(BitmapTransfer {
        command,
        payload: pixels.to_vec(),
        rect: (x, y, width, height),
        display: (display_width, display_height),
    })
}

impl BitmapTransfer {
    /// Compare complete RGB565 frames against the last frame successfully transmitted.
    pub fn delta_from(&self, previous: &BitmapTransfer) -> Result<FrameDelta, &'static str> {
        let (width, height) = self.display;
        if !self.is_full_for(width, height) || !previous.is_full_for(width, height) {
            return Err("full matching frames required");
        }
        let crate::dirty::Dirty::Regions(rects) =
            crate::dirty::calculate_rgb565(width, height, &previous.payload, &self.payload)?
        else {
            return Ok(FrameDelta::Full);
        };
        if rects.is_empty() {
            return Ok(FrameDelta::Unchanged);
        }
        let bytes: u64 = rects
            .iter()
            .map(|r| u64::from(r.width) * u64::from(r.height) * 2 + 6)
            .sum();
        if bytes >= u64::from(width) * u64::from(height) * 2 + 6 {
            return Ok(FrameDelta::Full);
        }
        let mut regions = Vec::with_capacity(rects.len());
        for r in rects {
            let mut pixels = Vec::with_capacity(r.width as usize * r.height as usize * 2);
            for y in r.y..r.y + r.height {
                let start = (y as usize * width as usize + r.x as usize) * 2;
                pixels.extend_from_slice(&self.payload[start..start + r.width as usize * 2]);
            }
            regions.push(bitmap_transfer(
                r.x, r.y, r.width, r.height, &pixels, width, height,
            )?);
        }
        if regions.len() == 1 {
            Ok(FrameDelta::Region(regions.pop().unwrap()))
        } else {
            Ok(FrameDelta::Regions(regions))
        }
    }
    pub fn matches_display(&self, width: u32, height: u32) -> bool {
        self.display == (width, height)
    }
    pub fn is_full_for(&self, width: u32, height: u32) -> bool {
        self.matches_display(width, height) && self.rect == (0, 0, width, height)
    }
    pub fn write_to(&self, output: &mut impl std::io::Write, chunk: usize) -> std::io::Result<()> {
        self.write_to_cancellable(output, chunk, || false)
    }
    pub fn write_to_cancellable(
        &self,
        output: &mut impl std::io::Write,
        chunk: usize,
        cancelled: impl Fn() -> bool,
    ) -> std::io::Result<()> {
        if chunk == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "zero chunk size",
            ));
        }
        if cancelled() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::Interrupted,
                "display transfer cancelled",
            ));
        }
        output.write_all(&self.command)?;
        for part in self.payload.chunks(chunk) {
            if cancelled() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Interrupted,
                    "display transfer cancelled",
                ));
            }
            output.write_all(part)?;
        }
        Ok(())
    }
}
