//! Decode only known bundled PNG assets using Windows' built-in image codec.
use std::{io, os::windows::ffi::OsStrExt, path::Path, ptr};
use windows_sys::Win32::Graphics::GdiPlus::{
    BitmapData, CompositingModeSourceCopy, GdipBitmapLockBits, GdipBitmapUnlockBits,
    GdipCreateBitmapFromScan0, GdipDeleteGraphics, GdipDisposeImage, GdipDrawImageRectI,
    GdipGetImageGraphicsContext, GdipGetImageHeight, GdipGetImageWidth, GdipLoadImageFromFile,
    GdipSetCompositingMode, GdipSetInterpolationMode, GdiplusShutdown, GdiplusStartup,
    GdiplusStartupInput, GpBitmap, GpGraphics, GpImage, ImageLockModeRead,
    InterpolationModeHighQualityBicubic, Rect,
};

// PixelFormat32bppARGB from Windows SDK gdipluspixelformats.h.
const ARGB32: i32 = 10 | (32 << 8) | 0x0004_0000 | 0x0002_0000 | 0x0020_0000;

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
pub enum IconKind {
    Cpu,
    Ram,
    Gpu,
    Vram,
    Codex,
}
impl IconKind {
    fn filename(self) -> &'static str {
        match self {
            Self::Cpu => "cpu.png",
            Self::Ram => "ram.png",
            Self::Gpu => "gpu.png",
            Self::Vram => "vram.png",
            Self::Codex => "codex.png",
        }
    }
}

pub struct IconRaster {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
}
struct Startup(usize);
impl Drop for Startup {
    fn drop(&mut self) {
        unsafe {
            GdiplusShutdown(self.0);
        }
    }
}
struct Image(*mut GpImage);
impl Drop for Image {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                GdipDisposeImage(self.0);
            }
        }
    }
}
struct Graphics(*mut GpGraphics);
impl Drop for Graphics {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                GdipDeleteGraphics(self.0);
            }
        }
    }
}
struct Lock {
    bitmap: *mut GpBitmap,
    data: BitmapData,
}
impl Drop for Lock {
    fn drop(&mut self) {
        unsafe {
            GdipBitmapUnlockBits(self.bitmap, &mut self.data);
        }
    }
}
fn check(status: i32) -> io::Result<()> {
    if status == 0 {
        Ok(())
    } else {
        Err(io::Error::other("icon decoding failed"))
    }
}

pub fn load_icon(root: &Path, kind: IconKind, side: u32) -> io::Result<IconRaster> {
    if !root.is_absolute() || !(8..=256).contains(&side) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid icon request",
        ));
    }
    let path = root.join(kind.filename());
    let metadata = std::fs::symlink_metadata(&path)?;
    use std::os::windows::fs::MetadataExt;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.file_attributes() & 0x400 != 0
        || metadata.len() > 1_000_000
    {
        return Err(io::Error::other("invalid icon file"));
    }
    let filename: Vec<u16> = path
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    if filename[..filename.len() - 1].contains(&0) {
        return Err(io::Error::other("invalid icon path"));
    }
    let mut token = 0usize;
    let input = GdiplusStartupInput {
        GdiplusVersion: 1,
        DebugEventCallback: 0,
        SuppressBackgroundThread: 0,
        SuppressExternalCodecs: 1,
    };
    check(unsafe { GdiplusStartup(&mut token, &input, ptr::null_mut()) })?;
    let _startup = Startup(token);
    let mut source = Image(ptr::null_mut());
    check(unsafe { GdipLoadImageFromFile(filename.as_ptr(), &mut source.0) })?;
    if source.0.is_null() {
        return Err(io::Error::other("icon unavailable"));
    }
    let (mut width, mut height) = (0u32, 0u32);
    check(unsafe { GdipGetImageWidth(source.0, &mut width) })?;
    check(unsafe { GdipGetImageHeight(source.0, &mut height) })?;
    if width == 0 || height == 0 || width > 2048 || height > 2048 {
        return Err(io::Error::other("invalid icon dimensions"));
    }
    let mut bitmap: *mut GpBitmap = ptr::null_mut();
    check(unsafe {
        GdipCreateBitmapFromScan0(
            side as i32,
            side as i32,
            0,
            ARGB32,
            ptr::null(),
            &mut bitmap,
        )
    })?;
    if bitmap.is_null() {
        return Err(io::Error::other("icon unavailable"));
    }
    let output = Image(bitmap.cast());
    let mut graphics = Graphics(ptr::null_mut());
    check(unsafe { GdipGetImageGraphicsContext(output.0, &mut graphics.0) })?;
    if graphics.0.is_null() {
        return Err(io::Error::other("icon graphics unavailable"));
    }
    check(unsafe { GdipSetCompositingMode(graphics.0, CompositingModeSourceCopy) })?;
    check(unsafe { GdipSetInterpolationMode(graphics.0, InterpolationModeHighQualityBicubic) })?;
    check(unsafe { GdipDrawImageRectI(graphics.0, source.0, 0, 0, side as i32, side as i32) })?;
    drop(graphics);
    let area = Rect {
        X: 0,
        Y: 0,
        Width: side as i32,
        Height: side as i32,
    };
    let mut data: BitmapData = unsafe { std::mem::zeroed() };
    check(unsafe {
        GdipBitmapLockBits(bitmap, &area, ImageLockModeRead as u32, ARGB32, &mut data)
    })?;
    let locked = Lock { bitmap, data };
    if locked.data.Scan0.is_null()
        || locked.data.Stride.unsigned_abs() < side * 4
        || locked.data.Width != side
        || locked.data.Height != side
    {
        return Err(io::Error::other("invalid icon pixels"));
    }
    let mut pixels = Vec::with_capacity(side as usize * side as usize * 4);
    for y in 0..side {
        let row = unsafe {
            (locked.data.Scan0 as *const u8).offset(y as isize * locked.data.Stride as isize)
        };
        let bytes = unsafe { std::slice::from_raw_parts(row, side as usize * 4) };
        for pixel in bytes.as_chunks::<4>().0 {
            let alpha = u16::from(pixel[3]);
            for component in &pixel[..3] {
                let value = if kind == IconKind::Codex {
                    255
                } else {
                    *component
                };
                pixels.push(((u16::from(value) * alpha + 127) / 255) as u8);
            }
            pixels.push(pixel[3]);
        }
    }
    Ok(IconRaster {
        width: side,
        height: side,
        pixels,
    })
}
