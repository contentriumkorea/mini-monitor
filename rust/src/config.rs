use serde_json::Value;
use std::{
    io::Write,
    path::{Path, PathBuf},
};

/// Retain unknown fields so reading a newer configuration is not destructive.
pub struct Settings(Value);

#[cfg(windows)]
pub struct StartupBudget {
    pub anchor: time::Date,
    pub total_cents: u64,
}
#[cfg(windows)]
pub struct StartupSettings {
    pub rotation: crate::device::Orientation,
    pub brightness: u8,
    pub manual_port: Option<String>,
    pub sensor_interval_ms: u64,
    pub usage_refresh_seconds: u64,
    pub cost_refresh_seconds: u64,
    pub overlay_enabled: bool,
    pub overlay_position: Option<(i32, i32)>,
    pub overlay_opacity: u8,
    pub overlay_scale: u32,
    pub taskbar_enabled: bool,
    pub taskbar_position: Option<(i32, i32)>,
    pub taskbar_style: crate::taskbar_render::TaskbarStyle,
    pub taskbar_items: Vec<crate::taskbar_render::MetricKind>,
    pub budget: Option<StartupBudget>,
}

/// Keep the in-memory and on-disk configuration in agreement on write errors.
pub struct SettingsRepository {
    path: PathBuf,
    settings: Settings,
}
impl SettingsRepository {
    pub fn new(path: PathBuf, settings: Settings) -> Self {
        Self { path, settings }
    }
    pub fn settings(&self) -> &Settings {
        &self.settings
    }
    pub fn update(
        &mut self,
        change: impl FnOnce(&Settings) -> Result<Settings, String>,
    ) -> Result<(), String> {
        let next = change(&self.settings)?;
        next.save(&self.path)?;
        self.settings = next;
        Ok(())
    }
}

impl Settings {
    #[cfg(windows)]
    pub fn startup_settings(&self) -> Result<StartupSettings, String> {
        use crate::{
            device::Orientation,
            taskbar_render::{MetricKind, TaskbarStyle},
        };
        let value = &self.0;
        let field = |path: &str| {
            value
                .pointer(path)
                .ok_or_else(|| "missing startup setting".to_owned())
        };
        let rotation = match field("/device/rotation")?.as_str() {
            Some("landscape") => Orientation::Landscape,
            Some("landscape_inverted") => Orientation::ReverseLandscape,
            Some("portrait") => Orientation::Portrait,
            Some("portrait_inverted") => Orientation::ReversePortrait,
            _ => return Err("invalid rotation".into()),
        };
        let integer = |path: &str| -> Result<u64, String> {
            field(path)?
                .as_u64()
                .ok_or_else(|| "invalid integer setting".into())
        };
        let boolean = |path: &str| -> Result<bool, String> {
            field(path)?
                .as_bool()
                .ok_or_else(|| "invalid boolean setting".into())
        };
        let pair = |x: &str, y: &str| -> Result<Option<(i32, i32)>, String> {
            let x = field(x)?;
            let y = field(y)?;
            if x.is_null() && y.is_null() {
                return Ok(None);
            }
            let convert = |v: &Value| {
                v.as_i64()
                    .and_then(|v| i32::try_from(v).ok())
                    .ok_or_else(|| "invalid position".to_owned())
            };
            Ok(Some((convert(x)?, convert(y)?)))
        };
        let taskbar_style = match field("/overlay/taskbar_style")?.as_str() {
            Some("text") => TaskbarStyle::Text,
            Some("icon") => TaskbarStyle::Icon,
            Some("both") => TaskbarStyle::Both,
            _ => return Err("invalid taskbar style".into()),
        };
        let taskbar_items = field("/overlay/taskbar_items")?
            .as_array()
            .ok_or("invalid taskbar items")?
            .iter()
            .map(|v| match v.as_str() {
                Some("cpu") => Ok(MetricKind::Cpu),
                Some("ram") => Ok(MetricKind::Ram),
                Some("gpu") => Ok(MetricKind::Gpu),
                Some("vram") => Ok(MetricKind::Vram),
                Some("codex") => Ok(MetricKind::Codex),
                Some("api") => Ok(MetricKind::Api),
                _ => Err("invalid taskbar item".to_owned()),
            })
            .collect::<Result<Vec<_>, _>>()?;
        let budget = if boolean("/openai/enabled")? {
            let amount = field("/openai/total_usd")?
                .as_str()
                .ok_or("invalid total")?;
            let (dollars, fraction) = amount.split_once('.').unwrap_or((amount, ""));
            let dollars: u64 = dollars.parse().map_err(|_| "invalid total")?;
            let cents = match fraction.len() {
                0 => 0,
                1 => fraction.parse::<u64>().map_err(|_| "invalid total")? * 10,
                2 => fraction.parse::<u64>().map_err(|_| "invalid total")?,
                _ => return Err("invalid total".into()),
            };
            let total_cents = dollars
                .checked_mul(100)
                .and_then(|v| v.checked_add(cents))
                .ok_or("invalid total")?;
            let date = field("/openai/start_date")?
                .as_str()
                .ok_or("invalid start")?;
            let year = date[0..4].parse::<i32>().map_err(|_| "invalid start")?;
            let month = date[5..7].parse::<u8>().map_err(|_| "invalid start")?;
            let day = date[8..10].parse::<u8>().map_err(|_| "invalid start")?;
            let anchor = time::Date::from_calendar_date(
                year,
                time::Month::try_from(month).map_err(|_| "invalid start")?,
                day,
            )
            .map_err(|_| "invalid start")?;
            Some(StartupBudget {
                anchor,
                total_cents,
            })
        } else {
            None
        };
        let opacity = field("/overlay/opacity")?
            .as_f64()
            .ok_or("invalid opacity")?;
        Ok(StartupSettings {
            rotation,
            brightness: u8::try_from(integer("/device/brightness")?)
                .map_err(|_| "invalid brightness")?,
            manual_port: if boolean("/device/auto_detect")? {
                None
            } else {
                field("/device/manual_port")?.as_str().map(str::to_owned)
            },
            sensor_interval_ms: integer("/sensors/sample_interval_ms")?,
            usage_refresh_seconds: integer("/ai/usage_refresh_seconds")?,
            cost_refresh_seconds: integer("/ai/cost_refresh_seconds")?,
            overlay_enabled: boolean("/overlay/enabled")?,
            overlay_position: pair("/overlay/x", "/overlay/y")?,
            overlay_opacity: (opacity * 255.0).round() as u8,
            overlay_scale: u32::try_from(integer("/overlay/scale_percent")?)
                .map_err(|_| "invalid scale")?,
            taskbar_enabled: boolean("/overlay/taskbar_enabled")?,
            taskbar_position: pair("/overlay/taskbar_x", "/overlay/taskbar_y")?,
            taskbar_style,
            taskbar_items,
            budget,
        })
    }
    pub fn parse(json: &str) -> Result<Self, String> {
        let mut value: Value = serde_json::from_str(json).map_err(|e| e.to_string())?;
        if !value.is_object() {
            return Err("settings must be a JSON object".into());
        }
        if let Some(rotation) = value.pointer_mut("/device/rotation") {
            match rotation.as_str() {
                Some("reverse_portrait") => *rotation = Value::String("portrait_inverted".into()),
                Some("reverse_landscape") => *rotation = Value::String("landscape_inverted".into()),
                _ => (),
            }
        }
        if let Some(provider) = value.pointer_mut("/ai/provider") {
            if matches!(
                provider.as_str(),
                Some("codex_local" | "openai_api" | "chatgpt_activity" | "not_configured")
            ) {
                *provider = Value::String("codex_account".into());
            }
        }
        for section in ["device", "sensors", "ai", "overlay", "app", "openai"] {
            if value.get(section).is_some_and(|item| !item.is_object()) {
                return Err(format!("{section} must be an object"));
            }
        }
        let defaults = serde_json::json!({
            "device": {"auto_detect": true, "manual_port": null, "usb_fps": 5.0,
                       "rotation": "landscape", "brightness": 25},
            "sensors": {"sample_interval_ms": 500, "cpu_temperature_sensor": null,
                        "gpu_load_sensor": null, "gpu_temperature_sensor": null},
            "ai": {"provider": "codex_account", "codex_local_consent": false,
                   "codex_cli_path": null, "usage_refresh_seconds": 60, "cost_refresh_seconds": 600,
                   "daily_budget_usd": null, "monthly_budget_usd": null,
                   "activity_processes": ["ChatGPT.exe", "Codex.exe"]},
            "overlay": {"enabled": false, "opacity": 0.85, "scale_percent": 100, "x": null,
                        "y": null, "taskbar_enabled": false, "taskbar_x": null, "taskbar_y": null,
                        "taskbar_items": ["cpu", "ram", "gpu", "vram", "codex"], "taskbar_style": "icon"},
            "app": {"render_fps": 12.0, "start_with_windows": false, "log_level": "INFO"},
            "openai": {"enabled": false, "total_usd": "", "start_date": ""}
        });
        let root = value.as_object_mut().expect("object verified above");
        for (section, fields) in defaults.as_object().expect("static defaults") {
            let target = root.entry(section).or_insert_with(|| serde_json::json!({}));
            let target = target.as_object_mut().expect("sections verified above");
            for (name, default) in fields.as_object().expect("static section") {
                target.entry(name).or_insert_with(|| default.clone());
            }
        }
        if let Some(device) = value.get("device") {
            if !device.is_object() {
                return Err("device must be an object".into());
            }
            if let Some(brightness) = device.get("brightness") {
                if !matches!(brightness.as_u64(), Some(1..=50)) {
                    return Err("brightness must be an integer from 1 to 50".into());
                }
            }
            if let Some(port) = device.get("manual_port").filter(|v| !v.is_null()) {
                if !port.as_str().is_some_and(|s| !s.trim().is_empty()) {
                    return Err("invalid manual port".into());
                }
            }
            if device.get("auto_detect").and_then(Value::as_bool) == Some(false)
                && device.get("manual_port").is_none_or(Value::is_null)
            {
                return Err("manual port required".into());
            }
            if let Some(rotation) = device.get("rotation") {
                if !matches!(
                    rotation.as_str(),
                    Some(
                        "landscape"
                            | "portrait"
                            | "landscape_inverted"
                            | "portrait_inverted"
                            | "reverse_landscape"
                            | "reverse_portrait"
                    )
                ) {
                    return Err("invalid rotation".into());
                }
            }
        }
        fn number(
            value: &Value,
            section: &str,
            key: &str,
            min: f64,
            max: f64,
        ) -> Result<(), String> {
            if let Some(v) = value.get(section).and_then(|s| s.get(key)) {
                if !v.as_f64().is_some_and(|n| n >= min && n <= max) {
                    return Err(format!("invalid {section}.{key}"));
                }
            }
            Ok(())
        }
        number(&value, "device", "usb_fps", 1.0, 8.0)?;
        number(&value, "sensors", "sample_interval_ms", 250.0, 2000.0)?;
        number(&value, "overlay", "opacity", 0.0, 1.0)?;
        number(&value, "overlay", "scale_percent", 50.0, 200.0)?;
        number(&value, "app", "render_fps", 10.0, 15.0)?;
        number(&value, "ai", "usage_refresh_seconds", 60.0, f64::MAX)?;
        number(&value, "ai", "cost_refresh_seconds", 600.0, f64::MAX)?;
        for (section, key) in [
            ("device", "auto_detect"),
            ("ai", "codex_local_consent"),
            ("overlay", "enabled"),
            ("overlay", "taskbar_enabled"),
            ("app", "start_with_windows"),
            ("openai", "enabled"),
        ] {
            if value
                .get(section)
                .and_then(|s| s.get(key))
                .is_some_and(|v| !v.is_boolean())
            {
                return Err(format!("invalid {section}.{key}"));
            }
        }
        if value
            .pointer("/overlay/scale_percent")
            .is_some_and(|v| v.as_u64().is_none())
        {
            return Err("invalid scale percent".into());
        }
        if let Some(style) = value.pointer("/overlay/taskbar_style") {
            if !matches!(style.as_str(), Some("icon" | "text" | "both")) {
                return Err("invalid taskbar style".into());
            }
        }
        if let Some(api) = value.get("openai") {
            let enabled = api.get("enabled").and_then(Value::as_bool).unwrap_or(false);
            let total = api
                .get("total_usd")
                .map(|v| v.as_str().ok_or("invalid total"))
                .transpose()?
                .unwrap_or("");
            let start = api
                .get("start_date")
                .map(|v| v.as_str().ok_or("invalid start"))
                .transpose()?
                .unwrap_or("");
            if enabled || !total.is_empty() || !start.is_empty() {
                let parts: Vec<&str> = total.split('.').collect();
                if parts.len() > 2
                    || parts[0].is_empty()
                    || parts[0].len() > 6
                    || !parts[0].bytes().all(|c| c.is_ascii_digit())
                    || (parts.len() == 2
                        && (parts[1].is_empty()
                            || parts[1].len() > 2
                            || !parts[1].bytes().all(|c| c.is_ascii_digit())))
                    || !total.parse::<f64>().is_ok_and(|n| n > 0.0)
                {
                    return Err("invalid total".into());
                }
                if start.len() != 10
                    || start.as_bytes().get(4) != Some(&b'-')
                    || start.as_bytes().get(7) != Some(&b'-')
                    || !start
                        .bytes()
                        .enumerate()
                        .all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
                {
                    return Err("invalid start".into());
                }
                let year = start[0..4].parse::<i32>().map_err(|_| "invalid start")?;
                let month = start[5..7].parse::<u8>().map_err(|_| "invalid start")?;
                let day = start[8..10].parse::<u8>().map_err(|_| "invalid start")?;
                let date = time::Date::from_calendar_date(
                    year,
                    time::Month::try_from(month).map_err(|_| "invalid start")?,
                    day,
                )
                .map_err(|_| "invalid start")?;
                if date > time::OffsetDateTime::now_utc().date() {
                    return Err("future start".into());
                }
            }
        }
        if let Some(provider) = value.pointer("/ai/provider") {
            if provider.as_str() != Some("codex_account") {
                return Err("unsupported provider".into());
            }
        }
        if let Some(ai) = value.get("ai") {
            if let Some(path) = ai.get("codex_cli_path").filter(|v| !v.is_null()) {
                if !path
                    .as_str()
                    .is_some_and(|s| !s.trim().is_empty() && !s.contains('\0'))
                {
                    return Err("invalid codex CLI path".into());
                }
            }
            for key in ["daily_budget_usd", "monthly_budget_usd"] {
                if let Some(amount) = ai.get(key).filter(|v| !v.is_null()) {
                    if !amount.as_f64().is_some_and(|n| n.is_finite() && n > 0.0) {
                        return Err(format!("invalid ai.{key}"));
                    }
                }
            }
        }
        if let Some(overlay) = value.get("overlay") {
            if let Some(items) = overlay.get("taskbar_items") {
                let items = items.as_array().ok_or("invalid taskbar items")?;
                let mut seen = std::collections::HashSet::new();
                if items.is_empty()
                    || items.iter().any(|v| {
                        !v.as_str().is_some_and(|s| {
                            ["cpu", "gpu", "ram", "vram", "codex", "api"].contains(&s)
                                && seen.insert(s)
                        })
                    })
                {
                    return Err("invalid taskbar items".into());
                }
            }
            for (a, b) in [("x", "y"), ("taskbar_x", "taskbar_y")] {
                let x = overlay.get(a).filter(|v| !v.is_null());
                let y = overlay.get(b).filter(|v| !v.is_null());
                if x.is_some() != y.is_some()
                    || [x, y]
                        .into_iter()
                        .flatten()
                        .any(|v| !v.as_i64().is_some_and(|n| i32::try_from(n).is_ok()))
                {
                    return Err("invalid coordinate pair".into());
                }
            }
        }
        if let Some(items) = value
            .pointer_mut("/overlay/taskbar_items")
            .and_then(Value::as_array_mut)
        {
            const ORDER: [&str; 6] = ["cpu", "ram", "gpu", "vram", "codex", "api"];
            items.sort_by_key(|item| {
                ORDER
                    .iter()
                    .position(|name| Some(*name) == item.as_str())
                    .unwrap_or(ORDER.len())
            });
        }
        Ok(Self(value))
    }

    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string_pretty(&self.0)
    }

    fn section_update(&self, section: &str, fields: &[(&str, Value)]) -> Result<Self, String> {
        let mut value = self.0.clone();
        let overlay = value
            .get_mut(section)
            .and_then(Value::as_object_mut)
            .ok_or("invalid overlay")?;
        for (key, new_value) in fields {
            overlay.insert((*key).into(), new_value.clone());
        }
        let encoded = serde_json::to_string(&value).map_err(|_| "settings encoding failed")?;
        Self::parse(&encoded)
    }

    pub fn with_taskbar_enabled(&self, enabled: bool) -> Result<Self, String> {
        self.section_update("overlay", &[("taskbar_enabled", Value::Bool(enabled))])
    }
    pub fn with_overlay_enabled(&self, enabled: bool) -> Result<Self, String> {
        self.section_update("overlay", &[("enabled", Value::Bool(enabled))])
    }

    pub fn with_overlay_appearance(&self, scale: u32, opacity_percent: u8) -> Result<Self, String> {
        if !(50..=200).contains(&scale) || opacity_percent > 100 {
            return Err("invalid overlay appearance".into());
        }
        self.section_update(
            "overlay",
            &[
                ("scale_percent", Value::from(scale)),
                ("opacity", Value::from(f64::from(opacity_percent) / 100.0)),
            ],
        )
    }

    pub fn with_taskbar_position(&self, x: i32, y: i32) -> Result<Self, String> {
        self.section_update(
            "overlay",
            &[("taskbar_x", Value::from(x)), ("taskbar_y", Value::from(y))],
        )
    }

    /// Persist only positions that actually changed, in one atomic settings write.
    pub fn with_display_positions(
        &self,
        overlay: Option<(i32, i32)>,
        taskbar: Option<(i32, i32)>,
    ) -> Result<Self, String> {
        let mut changes = Vec::new();
        if let Some((x, y)) = overlay {
            changes.push(("x", Value::from(x)));
            changes.push(("y", Value::from(y)));
        }
        if let Some((x, y)) = taskbar {
            changes.push(("taskbar_x", Value::from(x)));
            changes.push(("taskbar_y", Value::from(y)));
        }
        self.section_update("overlay", &changes)
    }

    pub fn with_taskbar_options(&self, items: &[&str], style: &str) -> Result<Self, String> {
        let items = Value::Array(
            items
                .iter()
                .map(|item| Value::String((*item).to_owned()))
                .collect(),
        );
        self.section_update(
            "overlay",
            &[
                ("taskbar_items", items),
                ("taskbar_style", Value::String(style.to_owned())),
            ],
        )
    }
    pub fn with_device_options(&self, rotation: &str, brightness: u8) -> Result<Self, String> {
        self.section_update(
            "device",
            &[
                ("rotation", Value::from(rotation)),
                ("brightness", Value::from(brightness)),
            ],
        )
    }
    pub fn with_openai_budget(
        &self,
        enabled: bool,
        total_usd: &str,
        start_date: &str,
    ) -> Result<Self, String> {
        self.section_update(
            "openai",
            &[
                ("enabled", Value::from(enabled)),
                ("total_usd", Value::from(total_usd)),
                ("start_date", Value::from(start_date)),
            ],
        )
    }
    pub fn with_overlay_options(
        &self,
        enabled: bool,
        opacity: f64,
        scale: u32,
        position: Option<(i32, i32)>,
    ) -> Result<Self, String> {
        let (x, y) = position.map_or((Value::Null, Value::Null), |(x, y)| {
            (Value::from(x), Value::from(y))
        });
        self.section_update(
            "overlay",
            &[
                ("enabled", Value::from(enabled)),
                ("opacity", Value::from(opacity)),
                ("scale_percent", Value::from(scale)),
                ("x", x),
                ("y", y),
            ],
        )
    }

    pub fn load(path: &Path) -> Result<Self, String> {
        let input = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
        Self::parse(&input)
    }

    /// First run is not an error; malformed or unreadable existing data is never overwritten.
    pub fn load_or_default(path: &Path) -> Result<Self, String> {
        match std::fs::read_to_string(path) {
            Ok(input) => Self::parse(&input),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Self::parse("{}"),
            Err(_) => Err("settings unavailable".into()),
        }
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        let parent = path.parent().ok_or("missing settings directory")?;
        let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        temp.write_all(self.to_json().map_err(|e| e.to_string())?.as_bytes())
            .map_err(|e| e.to_string())?;
        temp.as_file().sync_all().map_err(|e| e.to_string())?;
        temp.persist(path).map_err(|e| e.error.to_string())?;
        Ok(())
    }
}
