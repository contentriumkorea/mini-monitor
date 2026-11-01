//! GPU values must remain unavailable when a driver does not supply them.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VramSnapshot {
    pub total_bytes: u64,
    pub used_bytes: u64,
    pub used_percent: f64,
}

pub fn usage_percent(value: u32) -> Option<u32> {
    (value <= 100).then_some(value)
}

pub fn temperature_c(value: u32) -> Option<u32> {
    (value <= 150).then_some(value)
}
pub fn power_watts(milliwatts: u32) -> Option<f64> {
    (milliwatts <= 2_000_000).then_some(milliwatts as f64 / 1000.0)
}

pub fn vram_snapshot(total: u64, used: u64) -> Option<VramSnapshot> {
    if total == 0 || used > total {
        return None;
    }
    Some(VramSnapshot {
        total_bytes: total,
        used_bytes: used,
        used_percent: used as f64 * 100.0 / total as f64,
    })
}

#[derive(Clone, Debug)]
pub struct GpuSnapshot {
    pub name: String,
    pub gpu_percent: Option<u32>,
    pub vram: Option<VramSnapshot>,
    pub temperature_c: Option<u32>,
    pub power_watts: Option<f64>,
}

#[cfg(windows)]
mod nvidia {
    use super::{power_watts, temperature_c, usage_percent, vram_snapshot, GpuSnapshot};
    use std::{
        ffi::{c_char, c_void, CStr},
        marker::PhantomData,
        ptr,
        rc::Rc,
    };
    use windows_sys::Win32::{
        Foundation::{FreeLibrary, HMODULE},
        System::LibraryLoader::{GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_SYSTEM32},
    };

    type Device = *mut c_void;
    type Initialize = unsafe extern "C" fn() -> i32;
    type Shutdown = unsafe extern "C" fn() -> i32;
    type Handle = unsafe extern "C" fn(u32, *mut Device) -> i32;
    type Count = unsafe extern "C" fn(*mut u32) -> i32;
    type Name = unsafe extern "C" fn(Device, *mut c_char, u32) -> i32;
    type Utilization = unsafe extern "C" fn(Device, *mut RawUtilization) -> i32;
    type Memory = unsafe extern "C" fn(Device, *mut RawMemory) -> i32;
    type Temperature = unsafe extern "C" fn(Device, u32, *mut u32) -> i32;
    type Power = unsafe extern "C" fn(Device, *mut u32) -> i32;

    #[repr(C)]
    #[derive(Default)]
    struct RawUtilization {
        gpu: u32,
        memory: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct RawMemory {
        total: u64,
        free: u64,
        used: u64,
    }

    struct Library(HMODULE);
    impl Drop for Library {
        fn drop(&mut self) {
            unsafe {
                FreeLibrary(self.0);
            }
        }
    }

    /// NVIDIA driver only. The caller chooses the device index explicitly.
    /// The handle stays on its creating thread; multi-adapter selection is a separate concern.
    pub struct NvmlGpu {
        _library: Library,
        shutdown: Shutdown,
        device: Device,
        utilization: Utilization,
        memory: Memory,
        temperature: Option<Temperature>,
        power: Option<Power>,
        name: String,
        _thread_affinity: PhantomData<Rc<()>>,
    }

    impl NvmlGpu {
        pub fn open(index: u32) -> Result<Self, &'static str> {
            Self::open_inner(Some(index))
        }

        /// Automatically choose only when exactly one NVIDIA adapter exists.
        pub fn open_single() -> Result<Self, &'static str> {
            Self::open_inner(None)
        }

        fn open_inner(requested: Option<u32>) -> Result<Self, &'static str> {
            let filename: Vec<u16> = "nvml.dll\0".encode_utf16().collect();
            let handle = unsafe {
                LoadLibraryExW(
                    filename.as_ptr(),
                    ptr::null_mut(),
                    LOAD_LIBRARY_SEARCH_SYSTEM32,
                )
            };
            if handle.is_null() {
                return Err("NVIDIA driver unavailable");
            }
            let library = Library(handle);
            macro_rules! symbol {
                ($name:literal, $kind:ty) => {{
                    let proc = unsafe { GetProcAddress(library.0, concat!($name, "\0").as_ptr()) }
                        .ok_or("NVIDIA driver API unavailable")?;
                    unsafe {
                        std::mem::transmute::<unsafe extern "system" fn() -> isize, $kind>(proc)
                    }
                }};
            }
            let initialize = symbol!("nvmlInit_v2", Initialize);
            let shutdown = symbol!("nvmlShutdown", Shutdown);
            let by_index = symbol!("nvmlDeviceGetHandleByIndex_v2", Handle);
            let count = symbol!("nvmlDeviceGetCount_v2", Count);
            let get_name = symbol!("nvmlDeviceGetName", Name);
            let utilization = symbol!("nvmlDeviceGetUtilizationRates", Utilization);
            let memory = symbol!("nvmlDeviceGetMemoryInfo", Memory);
            let temperature =
                unsafe { GetProcAddress(library.0, c"nvmlDeviceGetTemperature".as_ptr().cast()) }
                    .map(|proc| unsafe {
                        std::mem::transmute::<unsafe extern "system" fn() -> isize, Temperature>(
                            proc,
                        )
                    });
            let power =
                unsafe { GetProcAddress(library.0, c"nvmlDeviceGetPowerUsage".as_ptr().cast()) }
                    .map(|proc| unsafe {
                        std::mem::transmute::<unsafe extern "system" fn() -> isize, Power>(proc)
                    });
            if unsafe { initialize() } != 0 {
                return Err("NVIDIA initialization failed");
            }
            let index = if let Some(index) = requested {
                index
            } else {
                let mut devices = 0;
                if unsafe { count(&mut devices) } != 0 || devices != 1 {
                    unsafe {
                        shutdown();
                    }
                    return Err("NVIDIA adapter ambiguous or unavailable");
                }
                0
            };
            let mut device = ptr::null_mut();
            if unsafe { by_index(index, &mut device) } != 0 || device.is_null() {
                unsafe {
                    shutdown();
                }
                return Err("NVIDIA adapter unavailable");
            }
            let mut name = [0 as c_char; 128];
            if unsafe { get_name(device, name.as_mut_ptr(), name.len() as u32) } != 0 {
                unsafe {
                    shutdown();
                }
                return Err("NVIDIA adapter name unavailable");
            }
            name[name.len() - 1] = 0;
            let name = unsafe { CStr::from_ptr(name.as_ptr()) }
                .to_string_lossy()
                .into_owned();
            if name.is_empty() {
                unsafe {
                    shutdown();
                }
                return Err("NVIDIA adapter name unavailable");
            }
            Ok(Self {
                _library: library,
                shutdown,
                device,
                utilization,
                memory,
                temperature,
                power,
                name,
                _thread_affinity: PhantomData,
            })
        }

        pub fn sample(&self) -> GpuSnapshot {
            let mut utilization = RawUtilization::default();
            let gpu_percent = if unsafe { (self.utilization)(self.device, &mut utilization) } == 0 {
                usage_percent(utilization.gpu)
            } else {
                None
            };
            let mut memory = RawMemory::default();
            let vram = if unsafe { (self.memory)(self.device, &mut memory) } == 0 {
                vram_snapshot(memory.total, memory.used)
            } else {
                None
            };
            let mut temp = 0;
            let temperature_c = self.temperature.and_then(|get| {
                if unsafe { get(self.device, 0, &mut temp) } == 0 {
                    temperature_c(temp)
                } else {
                    None
                }
            });
            let mut milliwatts = 0;
            let power_watts = self.power.and_then(|get| {
                if unsafe { get(self.device, &mut milliwatts) } == 0 {
                    power_watts(milliwatts)
                } else {
                    None
                }
            });
            GpuSnapshot {
                name: self.name.clone(),
                gpu_percent,
                vram,
                temperature_c,
                power_watts,
            }
        }
    }

    impl Drop for NvmlGpu {
        fn drop(&mut self) {
            unsafe {
                (self.shutdown)();
            }
        }
    }
}

#[cfg(windows)]
pub use nvidia::NvmlGpu;
