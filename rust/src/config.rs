use serde_json::Value;

/// Retain unknown fields so reading a newer configuration is not destructive.
pub struct Settings(Value);

impl Settings {
    pub fn parse(json: &str) -> Result<Self, String> {
        let value: Value = serde_json::from_str(json).map_err(|e| e.to_string())?;
        if !value.is_object() {
            return Err("settings must be a JSON object".into());
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
        }
        Ok(Self(value))
    }

    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string_pretty(&self.0)
    }
}
