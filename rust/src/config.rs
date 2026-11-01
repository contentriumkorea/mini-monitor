use serde_json::Value;
use std::{io::Write, path::Path};

/// Retain unknown fields so reading a newer configuration is not destructive.
pub struct Settings(Value);

impl Settings {
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

    pub fn load(path: &Path) -> Result<Self, String> {
        let input = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
        Self::parse(&input)
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
