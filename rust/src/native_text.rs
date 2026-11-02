//! Native antialiased text as premultiplied pixels for transparent windows.
use std::{ffi::c_void, io, ptr};
use windows_sys::Win32::{
    Foundation::SIZE,
    Graphics::Gdi::{
        CreateCompatibleDC, CreateDIBSection, CreateFontW, DeleteDC, DeleteObject, GdiFlush, GetDC,
        GetTextExtentPoint32W, ReleaseDC, SelectObject, SetBkMode, SetTextColor, TextOutW,
        BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS, HBITMAP, HDC, HFONT, HGDIOBJ,
    },
};

// Pixels may encode private on-screen usage; no Debug implementation.
pub struct TextRaster {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
}
impl TextRaster {
    /// Exclusive bottom of the visible glyph, excluding the font cell's padding.
    pub fn ink_bottom(&self) -> u32 {
        (0..self.height)
            .rev()
            .find(|&y| {
                self.pixels[(y * self.width * 4) as usize..((y + 1) * self.width * 4) as usize]
                    .as_chunks::<4>()
                    .0
                    .iter()
                    .any(|p| p[3] >= 135)
            })
            .map_or(self.height, |y| y + 1)
    }
}

struct Gdi {
    screen: HDC,
    memory: HDC,
    font: HFONT,
    bitmap: HBITMAP,
    old_font: HGDIOBJ,
    old_bitmap: HGDIOBJ,
}
impl Drop for Gdi {
    fn drop(&mut self) {
        unsafe {
            if !self.old_bitmap.is_null() && self.old_bitmap as isize != -1 {
                SelectObject(self.memory, self.old_bitmap);
            }
            if !self.old_font.is_null() && self.old_font as isize != -1 {
                SelectObject(self.memory, self.old_font);
            }
            if !self.bitmap.is_null() {
                DeleteObject(self.bitmap);
            }
            if !self.font.is_null() {
                DeleteObject(self.font);
            }
            if !self.memory.is_null() {
                DeleteDC(self.memory);
            }
            if !self.screen.is_null() {
                ReleaseDC(ptr::null_mut(), self.screen);
            }
        }
    }
}

pub fn raster_text(text: &str, font_pixels: u32, color: [u8; 3]) -> io::Result<TextRaster> {
    let utf16: Vec<u16> = text.encode_utf16().collect();
    if utf16.is_empty()
        || utf16.len() > 128
        || text.chars().any(char::is_control)
        || !(8..=96).contains(&font_pixels)
    {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid text"));
    }
    let mut gdi = Gdi {
        screen: ptr::null_mut(),
        memory: ptr::null_mut(),
        font: ptr::null_mut(),
        bitmap: ptr::null_mut(),
        old_font: ptr::null_mut(),
        old_bitmap: ptr::null_mut(),
    };
    gdi.screen = unsafe { GetDC(ptr::null_mut()) };
    if gdi.screen.is_null() {
        return Err(io::Error::last_os_error());
    }
    gdi.memory = unsafe { CreateCompatibleDC(gdi.screen) };
    if gdi.memory.is_null() {
        return Err(io::Error::last_os_error());
    }
    let face: Vec<u16> = "Segoe UI\0".encode_utf16().collect();
    gdi.font = unsafe {
        CreateFontW(
            -(font_pixels as i32 * 2),
            0,
            0,
            0,
            700,
            0,
            0,
            0,
            1,
            0,
            0,
            4,
            0,
            face.as_ptr(),
        )
    };
    if gdi.font.is_null() {
        return Err(io::Error::last_os_error());
    }
    gdi.old_font = unsafe { SelectObject(gdi.memory, gdi.font) };
    if gdi.old_font.is_null() || gdi.old_font as isize == -1 {
        return Err(io::Error::last_os_error());
    }
    let mut size = SIZE { cx: 0, cy: 0 };
    if unsafe { GetTextExtentPoint32W(gdi.memory, utf16.as_ptr(), utf16.len() as i32, &mut size) }
        == 0
        || size.cx <= 0
        || size.cy <= 0
        || size.cx > 8192
        || size.cy > 512
    {
        return Err(io::Error::other("text dimensions unavailable"));
    }
    let mut info: BITMAPINFO = unsafe { std::mem::zeroed() };
    info.bmiHeader = BITMAPINFOHEADER {
        biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
        biWidth: size.cx,
        biHeight: -size.cy,
        biPlanes: 1,
        biBitCount: 32,
        biCompression: BI_RGB,
        biSizeImage: 0,
        biXPelsPerMeter: 0,
        biYPelsPerMeter: 0,
        biClrUsed: 0,
        biClrImportant: 0,
    };
    let mut bits: *mut c_void = ptr::null_mut();
    gdi.bitmap = unsafe {
        CreateDIBSection(
            gdi.screen,
            &info,
            DIB_RGB_COLORS,
            &mut bits,
            ptr::null_mut(),
            0,
        )
    };
    if gdi.bitmap.is_null() || bits.is_null() {
        return Err(io::Error::last_os_error());
    }
    let count = size.cx as usize * size.cy as usize * 4;
    unsafe {
        ptr::write_bytes(bits.cast::<u8>(), 0, count);
    }
    gdi.old_bitmap = unsafe { SelectObject(gdi.memory, gdi.bitmap) };
    if gdi.old_bitmap.is_null() || gdi.old_bitmap as isize == -1 {
        return Err(io::Error::last_os_error());
    }
    if unsafe { SetBkMode(gdi.memory, 1) } == 0
        || unsafe { SetTextColor(gdi.memory, 0x00ff_ffff) } == u32::MAX
        || unsafe { TextOutW(gdi.memory, 0, 0, utf16.as_ptr(), utf16.len() as i32) } == 0
    {
        return Err(io::Error::other("text rasterization failed"));
    }
    if unsafe { GdiFlush() } == 0 {
        return Err(io::Error::other("text flush failed"));
    }
    let source = unsafe { std::slice::from_raw_parts(bits.cast::<u8>(), count) };
    // Supersample cached glyphs: GDI's native 16 coverage levels at small sizes
    // are coarse on transparent readouts. Box-filter 2x coverage, not RGB subpixels.
    let width = (size.cx as u32).div_ceil(2);
    let height = (size.cy as u32).div_ceil(2);
    let mut pixels = Vec::with_capacity((width * height * 4) as usize);
    for y in 0..height {
        for x in 0..width {
            let mut coverage = 0u32;
            let mut samples = 0;
            for dy in 0..2 {
                for dx in 0..2 {
                    let sx = x * 2 + dx;
                    let sy = y * 2 + dy;
                    if sx < size.cx as u32 && sy < size.cy as u32 {
                        let p = &source[((sy * size.cx as u32 + sx) * 4) as usize..][..3];
                        coverage += u32::from(*p.iter().max().unwrap_or(&0));
                        samples += 1;
                    }
                }
            }
            let alpha = ((coverage + samples / 2) / samples) as u8;
            let multiply = |value: u8| ((u16::from(value) * u16::from(alpha) + 127) / 255) as u8;
            pixels.extend_from_slice(&[
                multiply(color[2]),
                multiply(color[1]),
                multiply(color[0]),
                alpha,
            ]);
        }
    }
    Ok(TextRaster {
        width,
        height,
        pixels,
    })
}
