//! Windows display-adapter fallback for systems without a sole NVML adapter.
use crate::gpu::{vram_snapshot, GpuSnapshot};
use std::{collections::HashMap, ffi::c_void, mem, ptr};
use windows_sys::Win32::{
    Devices::Display::{
        DisplayConfigGetDeviceInfo, GetDisplayConfigBufferSizes, QueryDisplayConfig,
        DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME, DISPLAYCONFIG_DEVICE_INFO_HEADER,
        DISPLAYCONFIG_MODE_INFO, DISPLAYCONFIG_PATH_INFO, DISPLAYCONFIG_SOURCE_DEVICE_NAME,
        QDC_ONLY_ACTIVE_PATHS,
    },
    Foundation::{ERROR_INSUFFICIENT_BUFFER, LUID},
    Graphics::Gdi::{EnumDisplayDevicesW, DISPLAY_DEVICEW, DISPLAY_DEVICE_PRIMARY_DEVICE},
    System::Performance::{
        PdhAddEnglishCounterW, PdhCloseQuery, PdhCollectQueryData, PdhGetFormattedCounterArrayW,
        PdhOpenQueryW, PDH_CSTATUS_NEW_DATA, PDH_CSTATUS_VALID_DATA, PDH_FMT_COUNTERVALUE_ITEM_W,
        PDH_FMT_DOUBLE, PDH_FMT_LARGE, PDH_MORE_DATA,
    },
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AdapterLuid {
    high: u32,
    low: u32,
}

fn instance_luid(instance: &str) -> Option<AdapterLuid> {
    let lower = instance.to_ascii_lowercase();
    let rest = lower
        .strip_prefix("luid_")
        .or_else(|| lower.split_once("_luid_").map(|(_, rest)| rest))?;
    let mut parts = rest.split('_');
    let high = u32::from_str_radix(parts.next()?.strip_prefix("0x")?, 16).ok()?;
    let low = u32::from_str_radix(parts.next()?.strip_prefix("0x")?, 16).ok()?;
    Some(AdapterLuid { high, low })
}

fn engine_utilization(adapter: AdapterLuid, samples: &[(&str, f64)]) -> Option<u32> {
    let mut engines: HashMap<&str, f64> = HashMap::new();
    for &(name, value) in samples {
        if instance_luid(name) != Some(adapter) {
            continue;
        }
        if !value.is_finite() || !(0.0..=100.0).contains(&value) {
            return None;
        }
        let (_, engine) = name.split_once("_phys_")?;
        if !engine.contains("_eng_") {
            return None;
        }
        let entry = engines.entry(engine).or_default();
        *entry += value;
        if *entry > 100.0 {
            return None;
        }
    }
    let busiest = engines.values().copied().reduce(f64::max)?;
    Some(busiest.round() as u32)
}

fn dedicated_usage(adapter: AdapterLuid, samples: &[(&str, i64)]) -> Option<u64> {
    let mut total = None;
    for &(name, value) in samples {
        if instance_luid(name) != Some(adapter) {
            continue;
        }
        if value < 0 {
            return None;
        }
        total = Some(total.unwrap_or(0u64).checked_add(value as u64)?);
    }
    total
}

#[repr(C)]
struct Guid {
    data1: u32,
    data2: u16,
    data3: u16,
    data4: [u8; 8],
}

// ABI and vtable slots verified against Windows SDK 10.0.22621.0 shared/dxgi.h.
#[repr(C)]
struct DxgiAdapterDesc1 {
    description: [u16; 128],
    vendor_id: u32,
    device_id: u32,
    subsystem_id: u32,
    revision: u32,
    dedicated_video_memory: usize,
    dedicated_system_memory: usize,
    shared_system_memory: usize,
    adapter_luid: LUID,
    flags: u32,
}

#[link(name = "dxgi")]
extern "system" {
    fn CreateDXGIFactory1(iid: *const Guid, factory: *mut *mut c_void) -> i32;
}

unsafe fn vtable_entry(object: *mut c_void, slot: usize) -> *const c_void {
    let vtable = *(object.cast::<*const *const c_void>());
    *vtable.add(slot)
}

unsafe fn release(object: *mut c_void) {
    let call: unsafe extern "system" fn(*mut c_void) -> u32 =
        mem::transmute(vtable_entry(object, 2));
    call(object);
}

fn utf16_text(input: &[u16]) -> String {
    String::from_utf16_lossy(&input[..input.iter().position(|&c| c == 0).unwrap_or(input.len())])
}

fn luid(raw: LUID) -> AdapterLuid {
    AdapterLuid {
        high: raw.HighPart as u32,
        low: raw.LowPart,
    }
}

fn primary_display_luid() -> Result<AdapterLuid, &'static str> {
    let mut primary = None;
    for index in 0..32 {
        let mut device: DISPLAY_DEVICEW = unsafe { mem::zeroed() };
        device.cb = mem::size_of::<DISPLAY_DEVICEW>() as u32;
        if unsafe { EnumDisplayDevicesW(ptr::null(), index, &mut device, 0) } == 0 {
            break;
        }
        if device.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE != 0
            && primary.replace(utf16_text(&device.DeviceName)).is_some()
        {
            return Err("primary display ambiguous");
        }
    }
    let primary = primary.ok_or("primary display unavailable")?;
    for _ in 0..3 {
        let (mut path_count, mut mode_count) = (0, 0);
        if unsafe {
            GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &mut path_count, &mut mode_count)
        } != 0
            || path_count == 0
            || path_count > 64
            || mode_count > 256
        {
            return Err("display configuration unavailable");
        }
        let mut paths: Vec<DISPLAYCONFIG_PATH_INFO> =
            vec![unsafe { mem::zeroed() }; path_count as usize];
        let mut modes: Vec<DISPLAYCONFIG_MODE_INFO> =
            vec![unsafe { mem::zeroed() }; mode_count as usize];
        let status = unsafe {
            QueryDisplayConfig(
                QDC_ONLY_ACTIVE_PATHS,
                &mut path_count,
                paths.as_mut_ptr(),
                &mut mode_count,
                modes.as_mut_ptr(),
                ptr::null_mut(),
            )
        };
        if status == ERROR_INSUFFICIENT_BUFFER {
            continue;
        }
        if status != 0 {
            return Err("display configuration unavailable");
        }
        let mut chosen = None;
        for path in paths.iter().take(path_count as usize) {
            let source = path.sourceInfo;
            let mut name: DISPLAYCONFIG_SOURCE_DEVICE_NAME = unsafe { mem::zeroed() };
            name.header = DISPLAYCONFIG_DEVICE_INFO_HEADER {
                r#type: DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME,
                size: mem::size_of::<DISPLAYCONFIG_SOURCE_DEVICE_NAME>() as u32,
                adapterId: source.adapterId,
                id: source.id,
            };
            if unsafe { DisplayConfigGetDeviceInfo(&mut name.header) } != 0 {
                continue;
            }
            if !utf16_text(&name.viewGdiDeviceName).eq_ignore_ascii_case(&primary) {
                continue;
            }
            let found = luid(source.adapterId);
            if chosen.replace(found).is_some_and(|old| old != found) {
                return Err("primary display adapter ambiguous");
            }
        }
        return chosen.ok_or("primary display adapter unavailable");
    }
    Err("display configuration changed")
}

fn dxgi_adapter(selected: AdapterLuid) -> Result<(String, u64), &'static str> {
    let iid = Guid {
        data1: 0x770aae78,
        data2: 0xf26f,
        data3: 0x4dba,
        data4: [0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87],
    };
    let mut factory = ptr::null_mut();
    if unsafe { CreateDXGIFactory1(&iid, &mut factory) } < 0 || factory.is_null() {
        return Err("DXGI factory unavailable");
    }
    let mut found = None;
    for index in 0..64 {
        let mut adapter = ptr::null_mut();
        let enumerate: unsafe extern "system" fn(*mut c_void, u32, *mut *mut c_void) -> i32 =
            unsafe { mem::transmute(vtable_entry(factory, 12)) };
        if unsafe { enumerate(factory, index, &mut adapter) } < 0 {
            break;
        }
        if adapter.is_null() {
            break;
        }
        let mut desc: DxgiAdapterDesc1 = unsafe { mem::zeroed() };
        let get_desc: unsafe extern "system" fn(*mut c_void, *mut DxgiAdapterDesc1) -> i32 =
            unsafe { mem::transmute(vtable_entry(adapter, 10)) };
        let ok = unsafe { get_desc(adapter, &mut desc) } >= 0;
        unsafe { release(adapter) };
        if !ok || desc.flags & 2 != 0 || luid(desc.adapter_luid) != selected {
            continue;
        }
        let name = utf16_text(&desc.description);
        if name.is_empty() {
            continue;
        }
        if found
            .replace((name, desc.dedicated_video_memory as u64))
            .is_some()
        {
            unsafe { release(factory) };
            return Err("DXGI adapter ambiguous");
        }
    }
    unsafe { release(factory) };
    found.ok_or("DXGI adapter unavailable")
}

struct PdhQuery {
    handle: isize,
    engine: Option<isize>,
    memory: Option<isize>,
}
impl Drop for PdhQuery {
    fn drop(&mut self) {
        unsafe {
            PdhCloseQuery(self.handle);
        }
    }
}

fn wide(input: &str) -> Vec<u16> {
    input.encode_utf16().chain(Some(0)).collect()
}

impl PdhQuery {
    fn open() -> Option<Self> {
        let mut handle = 0;
        if unsafe { PdhOpenQueryW(ptr::null(), 0, &mut handle) } != 0 {
            return None;
        }
        let mut query = Self {
            handle,
            engine: None,
            memory: None,
        };
        let mut counter = 0;
        if unsafe {
            PdhAddEnglishCounterW(
                handle,
                wide(r"\GPU Engine(*)\Utilization Percentage").as_ptr(),
                0,
                &mut counter,
            )
        } == 0
        {
            query.engine = Some(counter);
        }
        counter = 0;
        if unsafe {
            PdhAddEnglishCounterW(
                handle,
                wide(r"\GPU Adapter Memory(*)\Dedicated Usage").as_ptr(),
                0,
                &mut counter,
            )
        } == 0
        {
            query.memory = Some(counter);
        }
        if query.engine.is_none() && query.memory.is_none() {
            return None;
        }
        Some(query)
    }

    fn collect(&self) -> bool {
        (unsafe { PdhCollectQueryData(self.handle) }) == 0
    }

    fn array(&self, counter: isize, format: u32) -> Option<Vec<(String, f64, i64, bool)>> {
        let (mut bytes, mut count) = (0, 0);
        let status = unsafe {
            PdhGetFormattedCounterArrayW(counter, format, &mut bytes, &mut count, ptr::null_mut())
        };
        if status != PDH_MORE_DATA || bytes == 0 || bytes > 1_048_576 {
            return None;
        }
        let mut storage = vec![0u64; (bytes as usize).div_ceil(8)];
        let status = unsafe {
            PdhGetFormattedCounterArrayW(
                counter,
                format,
                &mut bytes,
                &mut count,
                storage.as_mut_ptr().cast::<PDH_FMT_COUNTERVALUE_ITEM_W>(),
            )
        };
        if status != 0
            || count as usize > storage.len() * 8 / mem::size_of::<PDH_FMT_COUNTERVALUE_ITEM_W>()
        {
            return None;
        }
        let items = unsafe {
            std::slice::from_raw_parts(
                storage.as_ptr().cast::<PDH_FMT_COUNTERVALUE_ITEM_W>(),
                count as usize,
            )
        };
        let mut result = Vec::with_capacity(items.len());
        for item in items {
            if item.szName.is_null() {
                return None;
            }
            let mut len = 0;
            while len < 512 && unsafe { *item.szName.add(len) } != 0 {
                len += 1;
            }
            if len == 512 {
                return None;
            }
            let name =
                String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(item.szName, len) });
            let valid = item.FmtValue.CStatus == PDH_CSTATUS_VALID_DATA
                || item.FmtValue.CStatus == PDH_CSTATUS_NEW_DATA;
            let (double, large) = unsafe {
                (
                    item.FmtValue.Anonymous.doubleValue,
                    item.FmtValue.Anonymous.largeValue,
                )
            };
            result.push((name, double, large, valid));
        }
        Some(result)
    }
}

pub struct WindowsGpu {
    name: String,
    adapter: AdapterLuid,
    total_vram: u64,
    query: Option<PdhQuery>,
}
impl WindowsGpu {
    pub fn open_primary() -> Result<Self, &'static str> {
        let adapter = primary_display_luid()?;
        let (name, total_vram) = dxgi_adapter(adapter)?;
        Ok(Self {
            name,
            adapter,
            total_vram,
            query: PdhQuery::open(),
        })
    }

    pub fn sample(&self) -> GpuSnapshot {
        let (mut gpu_percent, mut vram) = (None, None);
        if let Some(query) = &self.query {
            if query.collect() {
                if let Some(counter) = query.engine {
                    if let Some(items) = query.array(counter, PDH_FMT_DOUBLE) {
                        let samples: Vec<_> = items
                            .iter()
                            .map(|(name, value, _, valid)| {
                                (name.as_str(), if *valid { *value } else { f64::NAN })
                            })
                            .collect();
                        gpu_percent = engine_utilization(self.adapter, &samples);
                    }
                }
                if self.total_vram > 0 {
                    if let Some(counter) = query.memory {
                        if let Some(items) = query.array(counter, PDH_FMT_LARGE) {
                            let samples: Vec<_> = items
                                .iter()
                                .map(|(name, _, value, valid)| {
                                    (name.as_str(), if *valid { *value } else { -1 })
                                })
                                .collect();
                            vram = dedicated_usage(self.adapter, &samples)
                                .and_then(|used| vram_snapshot(self.total_vram, used));
                        }
                    }
                }
            }
        }
        GpuSnapshot {
            name: self.name.clone(),
            gpu_percent,
            vram,
            temperature_c: None,
            power_watts: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const A: AdapterLuid = AdapterLuid {
        high: 0,
        low: 0x0001_23ab,
    };

    #[test]
    fn parses_luid_from_windows_instance() {
        assert_eq!(
            instance_luid("pid_123_luid_0x00000000_0x000123AB_phys_0_eng_0_engtype_3D"),
            Some(A)
        );
        assert_eq!(
            instance_luid("pid_123_luid_0x0_0xZZ_phys_0_eng_0_engtype_3D"),
            None
        );
    }

    #[test]
    fn sums_processes_per_engine_then_uses_busiest_engine() {
        let samples = [
            ("pid_1_luid_0x0_0x123ab_phys_0_eng_0_engtype_3D", 20.0),
            ("pid_2_luid_0x0_0x123ab_phys_0_eng_0_engtype_3D", 30.0),
            ("pid_3_luid_0x0_0x123ab_phys_0_eng_1_engtype_Copy", 70.0),
        ];
        assert_eq!(engine_utilization(A, &samples), Some(70));
    }

    #[test]
    fn does_not_mix_other_adapter_or_invalid_engine_data() {
        let samples = [
            ("pid_1_luid_0x0_0x123ab_phys_0_eng_0_engtype_3D", 35.0),
            ("pid_2_luid_0x0_0x99999_phys_0_eng_0_engtype_3D", 90.0),
            ("pid_3_luid_0x0_0x123ab_phys_0_eng_1_engtype_Copy", f64::NAN),
        ];
        assert_eq!(engine_utilization(A, &samples), None);
        assert_eq!(
            engine_utilization(
                A,
                &[("pid_2_luid_0x0_0x99999_phys_0_eng_0_engtype_3D", 90.0)]
            ),
            None
        );
    }

    #[test]
    fn dedicated_memory_requires_exact_adapter_and_valid_counter() {
        assert_eq!(
            dedicated_usage(
                A,
                &[
                    ("luid_0x0_0x99999_phys_0", 9),
                    ("luid_0x0_0x123ab_phys_0", 42)
                ]
            ),
            Some(42)
        );
        assert_eq!(dedicated_usage(A, &[("luid_0x0_0x123ab_phys_0", -1)]), None);
        assert_eq!(dedicated_usage(A, &[("luid_0x0_0x99999_phys_0", 9)]), None);
    }

    #[test]
    #[ignore = "read-only Windows display and performance-counter probe"]
    fn primary_adapter_probe() {
        let gpu = WindowsGpu::open_primary().expect("primary display must match a DXGI adapter");
        let _ = gpu.sample(); // Prime rate counters; do not collect again within one second.
        std::thread::sleep(std::time::Duration::from_millis(1100));
        let sample = gpu.sample();
        assert!(!sample.name.is_empty());
        assert!(
            sample.gpu_percent.is_some(),
            "GPU engine counter unavailable"
        );
        if gpu.total_vram > 0 {
            assert!(
                sample.vram.is_some(),
                "dedicated-memory counter unavailable"
            );
        }
    }
}
