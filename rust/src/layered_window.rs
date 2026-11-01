//! Per-pixel transparent Win32 presenter. Does not show or reposition Explorer.
use std::io;

fn byte_count(width: u32, height: u32) -> Option<usize> {
    let pixels = (width as usize).checked_mul(height as usize)?;
    (width > 0 && height > 0 && pixels <= 16_777_216).then_some(pixels.checked_mul(4)?)
}

pub fn rgba_to_bgra(width: u32, height: u32, rgba: &[u8]) -> io::Result<Vec<u8>> {
    if byte_count(width, height) != Some(rgba.len()) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid frame"));
    }
    let mut output = Vec::with_capacity(rgba.len());
    for px in rgba.as_chunks::<4>().0 {
        let alpha = u16::from(px[3]);
        let premultiply = |component: u8| ((u16::from(component) * alpha + 127) / 255) as u8;
        output.extend_from_slice(&[
            premultiply(px[2]),
            premultiply(px[1]),
            premultiply(px[0]),
            px[3],
        ]);
    }
    Ok(output)
}

#[cfg(windows)]
mod native {
    use super::{byte_count, io};
    use crate::taskbar_window::TaskbarWindow;
    use std::{ffi::c_void, ptr};
    use windows_sys::Win32::{
        Foundation::{POINT, SIZE},
        Graphics::Gdi::{
            CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, GetDC, ReleaseDC,
            SelectObject, AC_SRC_ALPHA, AC_SRC_OVER, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
            BLENDFUNCTION, DIB_RGB_COLORS, HBITMAP, HDC, HGDIOBJ,
        },
        UI::WindowsAndMessaging::{UpdateLayeredWindow, ULW_ALPHA},
    };

    struct Gdi {
        screen: HDC,
        memory: HDC,
        bitmap: HBITMAP,
        previous: HGDIOBJ,
    }
    impl Drop for Gdi {
        fn drop(&mut self) {
            unsafe {
                if !self.previous.is_null() && self.previous as isize != -1 {
                    SelectObject(self.memory, self.previous);
                }
                if !self.bitmap.is_null() {
                    DeleteObject(self.bitmap);
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

    /// `pixels` must be top-down premultiplied BGRA. The window remains hidden unless explicitly shown elsewhere.
    pub fn present(
        window: &TaskbarWindow,
        x: i32,
        y: i32,
        width: u32,
        height: u32,
        pixels: &[u8],
    ) -> io::Result<()> {
        if byte_count(width, height) != Some(pixels.len())
            || pixels
                .as_chunks::<4>()
                .0
                .iter()
                .any(|p| p[..3].iter().any(|c| *c > p[3]))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid premultiplied frame",
            ));
        }
        let mut gdi = Gdi {
            screen: ptr::null_mut(),
            memory: ptr::null_mut(),
            bitmap: ptr::null_mut(),
            previous: ptr::null_mut(),
        };
        gdi.screen = unsafe { GetDC(ptr::null_mut()) };
        if gdi.screen.is_null() {
            return Err(io::Error::last_os_error());
        }
        gdi.memory = unsafe { CreateCompatibleDC(gdi.screen) };
        if gdi.memory.is_null() {
            return Err(io::Error::last_os_error());
        }
        let mut info: BITMAPINFO = unsafe { std::mem::zeroed() };
        info.bmiHeader = BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width as i32,
            biHeight: -(height as i32),
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
        unsafe {
            ptr::copy_nonoverlapping(pixels.as_ptr(), bits.cast(), pixels.len());
        }
        gdi.previous = unsafe { SelectObject(gdi.memory, gdi.bitmap) };
        if gdi.previous.is_null() || gdi.previous as isize == -1 {
            return Err(io::Error::last_os_error());
        }
        let destination = POINT { x, y };
        let source = POINT { x: 0, y: 0 };
        let size = SIZE {
            cx: width as i32,
            cy: height as i32,
        };
        let blend = BLENDFUNCTION {
            BlendOp: AC_SRC_OVER as u8,
            BlendFlags: 0,
            SourceConstantAlpha: 255,
            AlphaFormat: AC_SRC_ALPHA as u8,
        };
        if unsafe {
            UpdateLayeredWindow(
                window.handle(),
                gdi.screen,
                &destination,
                &size,
                gdi.memory,
                &source,
                0,
                &blend,
                ULW_ALPHA,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

#[cfg(windows)]
pub use native::present;
