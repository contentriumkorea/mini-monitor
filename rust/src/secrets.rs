//! Windows DPAPI compatibility for the existing application's app-owned key.
use std::{ffi::c_void, io, io::Write, path::PathBuf, ptr, slice};

const ENTROPY: &[u8] = b"AI-Mini-Monitor:v1:OpenAI-Admin-Key";
const UI_FORBIDDEN: u32 = 1;

#[repr(C)]
struct DataBlob {
    len: u32,
    data: *mut u8,
}

#[link(name = "Crypt32")]
extern "system" {
    fn CryptProtectData(
        input: *const DataBlob,
        description: *const u16,
        entropy: *const DataBlob,
        reserved: *mut c_void,
        prompt: *mut c_void,
        flags: u32,
        output: *mut DataBlob,
    ) -> i32;
    fn CryptUnprotectData(
        input: *const DataBlob,
        description: *mut *mut u16,
        entropy: *const DataBlob,
        reserved: *mut c_void,
        prompt: *mut c_void,
        flags: u32,
        output: *mut DataBlob,
    ) -> i32;
}
#[link(name = "Kernel32")]
extern "system" {
    fn LocalFree(memory: *mut c_void) -> *mut c_void;
}

fn crypt(input: &[u8], decrypt: bool) -> io::Result<Vec<u8>> {
    let size = u32::try_from(input.len())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "input too large"))?;
    let source = DataBlob {
        len: size,
        data: input.as_ptr().cast_mut(),
    };
    let entropy = DataBlob {
        len: ENTROPY.len() as u32,
        data: ENTROPY.as_ptr().cast_mut(),
    };
    let mut output = DataBlob {
        len: 0,
        data: ptr::null_mut(),
    };
    let mut description = ptr::null_mut();
    let name: Vec<u16> = "AI Mini Monitor OpenAI Admin Key\0"
        .encode_utf16()
        .collect();
    let success = unsafe {
        if decrypt {
            CryptUnprotectData(
                &source,
                &mut description,
                &entropy,
                ptr::null_mut(),
                ptr::null_mut(),
                UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptProtectData(
                &source,
                name.as_ptr(),
                &entropy,
                ptr::null_mut(),
                ptr::null_mut(),
                UI_FORBIDDEN,
                &mut output,
            )
        }
    };
    if success == 0 {
        return Err(io::Error::last_os_error());
    }
    let value = unsafe {
        let result = if output.len == 0 {
            Vec::new()
        } else if output.data.is_null() {
            if !description.is_null() {
                LocalFree(description.cast());
            }
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "DPAPI returned invalid buffer",
            ));
        } else {
            slice::from_raw_parts(output.data, output.len as usize).to_vec()
        };
        if decrypt && output.len > 0 {
            ptr::write_bytes(output.data, 0, output.len as usize);
        }
        LocalFree(output.data.cast());
        if !description.is_null() {
            LocalFree(description.cast());
        }
        result
    };
    Ok(value)
}

pub fn protect(plaintext: &[u8]) -> io::Result<Vec<u8>> {
    crypt(plaintext, false)
}
pub fn unprotect(ciphertext: &[u8]) -> io::Result<Vec<u8>> {
    crypt(ciphertext, true)
}

pub struct SecretStore {
    path: PathBuf,
}

impl SecretStore {
    pub fn at(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn get(&self) -> io::Result<Option<String>> {
        let ciphertext = match std::fs::read(&self.path) {
            Ok(value) => value,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        };
        let plaintext = unprotect(&ciphertext)?;
        String::from_utf8(plaintext)
            .map(Some)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid encrypted text"))
    }

    pub fn set(&self, value: &str) -> io::Result<()> {
        let text = value.trim();
        if text.is_empty() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "empty key"));
        }
        let ciphertext = protect(text.as_bytes())?;
        let parent = self
            .path
            .parent()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing directory"))?;
        std::fs::create_dir_all(parent)?;
        let mut temp = tempfile::NamedTempFile::new_in(parent)?;
        temp.write_all(&ciphertext)?;
        temp.as_file().sync_all()?;
        temp.persist(&self.path).map_err(|e| e.error)?;
        Ok(())
    }
}
