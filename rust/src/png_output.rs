//! Native-size opaque PNG export through Windows' built-in codec.
use crate::dashboard_render::DashboardFrame;
use std::{io, os::windows::ffi::OsStrExt, path::Path, ptr};
use windows_sys::Win32::Graphics::GdiPlus::{
    GdipCreateBitmapFromScan0, GdipDisposeImage, GdipGetImageEncoders, GdipGetImageEncodersSize,
    GdipSaveImageToFile, GdiplusShutdown, GdiplusStartup, GdiplusStartupInput, GpBitmap,
    ImageCodecInfo,
};
struct Startup(usize);
impl Drop for Startup {
    fn drop(&mut self) {
        unsafe {
            GdiplusShutdown(self.0);
        }
    }
}
struct Image(*mut GpBitmap);
impl Drop for Image {
    fn drop(&mut self) {
        unsafe {
            GdipDisposeImage(self.0.cast());
        }
    }
}
pub fn save(frame: &DashboardFrame, path: &Path) -> io::Result<()> {
    frame.to_opaque_rgb()?;
    if !path.is_absolute() {
        return Err(io::Error::other("absolute output path required"));
    }
    if let Ok(meta) = std::fs::symlink_metadata(path) {
        use std::os::windows::fs::MetadataExt;
        if !meta.is_file() || meta.file_type().is_symlink() || meta.file_attributes() & 0x400 != 0 {
            return Err(io::Error::other("invalid output file"));
        }
    }
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("invalid output path"))?;
    std::fs::create_dir_all(parent)?;
    let temporary = tempfile::Builder::new()
        .suffix(".png")
        .tempfile_in(parent)?
        .into_temp_path();
    let input = GdiplusStartupInput {
        GdiplusVersion: 1,
        DebugEventCallback: 0,
        SuppressBackgroundThread: 0,
        SuppressExternalCodecs: 1,
    };
    let mut token = 0;
    if unsafe { GdiplusStartup(&mut token, &input, ptr::null_mut()) } != 0 {
        return Err(io::Error::other("image codec unavailable"));
    }
    let _startup = Startup(token);
    let mut count = 0;
    let mut size = 0;
    if unsafe { GdipGetImageEncodersSize(&mut count, &mut size) } != 0
        || count > 128
        || size > 1024 * 1024
        || size == 0
    {
        return Err(io::Error::other("image codec unavailable"));
    }
    let mut buffer = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
    if count as usize * std::mem::size_of::<ImageCodecInfo>() > size as usize
        || unsafe { GdipGetImageEncoders(count, size, buffer.as_mut_ptr().cast()) } != 0
    {
        return Err(io::Error::other("image codec unavailable"));
    }
    let codecs = unsafe {
        std::slice::from_raw_parts(buffer.as_ptr().cast::<ImageCodecInfo>(), count as usize)
    };
    let encoder = codecs
        .iter()
        .find(|codec| {
            if codec.MimeType.is_null() {
                return false;
            }
            let mut len = 0;
            while len < 32 && unsafe { *codec.MimeType.add(len) } != 0 {
                len += 1;
            }
            String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(codec.MimeType, len) })
                == "image/png"
        })
        .ok_or_else(|| io::Error::other("PNG codec unavailable"))?;
    let mut image = ptr::null_mut();
    const ARGB32: i32 = 10 | (32 << 8) | 0x00040000 | 0x00020000 | 0x00200000;
    if unsafe {
        GdipCreateBitmapFromScan0(
            frame.width as i32,
            frame.height as i32,
            (frame.width * 4) as i32,
            ARGB32,
            frame.pixels.as_ptr(),
            &mut image,
        )
    } != 0
    {
        return Err(io::Error::other("image creation failed"));
    }
    let image = Image(image);
    let target: Vec<u16> = temporary.as_os_str().encode_wide().chain(Some(0)).collect();
    if unsafe { GdipSaveImageToFile(image.0.cast(), target.as_ptr(), &encoder.Clsid, ptr::null()) }
        != 0
    {
        return Err(io::Error::other("PNG write failed"));
    }
    std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&temporary)?
        .sync_all()?;
    temporary.persist(path).map_err(|e| e.error)?;
    Ok(())
}
