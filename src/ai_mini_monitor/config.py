from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import AIProviderKind
from .orientation import migrate_rotation, orientation_spec
from .resources import user_data_dir


DEFAULT_BRIGHTNESS = 25
MIN_BRIGHTNESS = 1
MAX_BRIGHTNESS = 50
DEFAULT_OVERLAY_OPACITY = 0.85
MIN_OVERLAY_OPACITY = 0.0
MAX_OVERLAY_OPACITY = 1.0
DEFAULT_OVERLAY_SCALE_PERCENT = 100
MIN_OVERLAY_SCALE_PERCENT = 50
MAX_OVERLAY_SCALE_PERCENT = 200
MIN_OVERLAY_COORDINATE = -(2**31)
MAX_OVERLAY_COORDINATE = (2**31) - 1


def validate_brightness(value: object) -> int:
    """Return one canonical, safety-capped display brightness value."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("device.brightness must be an integer")
    if not MIN_BRIGHTNESS <= value <= MAX_BRIGHTNESS:
        raise ValueError(
            f"device.brightness must be between {MIN_BRIGHTNESS} and {MAX_BRIGHTNESS}"
        )
    return value


@dataclass(slots=True)
class DeviceConfig:
    auto_detect: bool = True
    manual_port: str | None = None
    usb_fps: float = 5.0
    rotation: str = "landscape"
    brightness: int = DEFAULT_BRIGHTNESS


@dataclass(slots=True)
class SensorConfig:
    sample_interval_ms: int = 500
    cpu_temperature_sensor: str | None = None
    gpu_load_sensor: str | None = None
    gpu_temperature_sensor: str | None = None


@dataclass(slots=True)
class AIConfig:
    provider: str = AIProviderKind.NOT_CONFIGURED.value
    codex_local_consent: bool = False
    usage_refresh_seconds: int = 60
    cost_refresh_seconds: int = 600
    daily_budget_usd: float | None = None
    monthly_budget_usd: float | None = None
    activity_processes: list[str] = field(default_factory=lambda: ["ChatGPT.exe", "Codex.exe"])


@dataclass(slots=True)
class OverlayConfig:
    enabled: bool = False
    opacity: float = DEFAULT_OVERLAY_OPACITY
    scale_percent: int = DEFAULT_OVERLAY_SCALE_PERCENT
    x: int | None = None
    y: int | None = None


@dataclass(slots=True)
class AppOptions:
    render_fps: float = 12.0
    start_with_windows: bool = False
    log_level: str = "INFO"


@dataclass(slots=True)
class AppConfig:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    sensors: SensorConfig = field(default_factory=SensorConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    app: AppOptions = field(default_factory=AppOptions)

    def validate(self) -> None:
        if not isinstance(self.device.auto_detect, bool):
            raise ValueError("device.auto_detect must be true or false")
        if self.device.manual_port is not None and (
            not isinstance(self.device.manual_port, str)
            or not self.device.manual_port.strip()
        ):
            raise ValueError("device.manual_port must be a non-empty string or null")
        if not self.device.auto_detect and not self.device.manual_port:
            raise ValueError(
                "device.manual_port is required when device.auto_detect is false"
            )
        if not _finite_number(self.device.usb_fps) or not 1.0 <= self.device.usb_fps <= 8.0:
            raise ValueError("device.usb_fps must be between 1 and 8")
        orientation_spec(self.device.rotation)
        validate_brightness(self.device.brightness)
        if (
            not _finite_number(self.sensors.sample_interval_ms)
            or not 250 <= self.sensors.sample_interval_ms <= 2_000
        ):
            raise ValueError("sensors.sample_interval_ms must be between 250 and 2000")
        if (
            not _finite_number(self.ai.usage_refresh_seconds)
            or self.ai.usage_refresh_seconds < 60
        ):
            raise ValueError("ai.usage_refresh_seconds must be at least 60")
        if (
            not _finite_number(self.ai.cost_refresh_seconds)
            or self.ai.cost_refresh_seconds < 600
        ):
            raise ValueError("ai.cost_refresh_seconds must be at least 600")
        if not _finite_number(self.app.render_fps) or not 10.0 <= self.app.render_fps <= 15.0:
            raise ValueError("app.render_fps must be between 10 and 15")
        allowed = {provider.value for provider in AIProviderKind}
        if self.ai.provider not in allowed:
            raise ValueError(f"ai.provider must be one of {sorted(allowed)}")
        if not isinstance(self.ai.codex_local_consent, bool):
            raise ValueError("ai.codex_local_consent must be true or false")
        for budget in (self.ai.daily_budget_usd, self.ai.monthly_budget_usd):
            if budget is not None and (
                not _finite_number(budget)
                or budget <= 0
            ):
                raise ValueError("budgets must be positive when configured")
        if not isinstance(self.overlay.enabled, bool):
            raise ValueError("overlay.enabled must be true or false")
        if (
            not _finite_number(self.overlay.opacity)
            or not MIN_OVERLAY_OPACITY
            <= self.overlay.opacity
            <= MAX_OVERLAY_OPACITY
        ):
            raise ValueError(
                "overlay.opacity must be between "
                f"{MIN_OVERLAY_OPACITY} and {MAX_OVERLAY_OPACITY}"
            )
        if (
            isinstance(self.overlay.scale_percent, bool)
            or not isinstance(self.overlay.scale_percent, int)
            or not MIN_OVERLAY_SCALE_PERCENT
            <= self.overlay.scale_percent
            <= MAX_OVERLAY_SCALE_PERCENT
        ):
            raise ValueError(
                "overlay.scale_percent must be an integer between "
                f"{MIN_OVERLAY_SCALE_PERCENT} and {MAX_OVERLAY_SCALE_PERCENT}"
            )
        for name, coordinate in (("x", self.overlay.x), ("y", self.overlay.y)):
            if coordinate is not None and (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, int)
                or not MIN_OVERLAY_COORDINATE
                <= coordinate
                <= MAX_OVERLAY_COORDINATE
            ):
                raise ValueError(
                    f"overlay.{name} must be a signed 32-bit integer or null"
                )
        if (self.overlay.x is None) != (self.overlay.y is None):
            raise ValueError("overlay.x and overlay.y must both be set or both be null")


def default_config_path() -> Path:
    return user_data_dir() / "config.json"


def load_config(path: Path | None = None) -> AppConfig:
    target = path or default_config_path()
    if not target.exists():
        config = AppConfig()
        config.validate()
        return config
    payload = json.loads(target.read_text(encoding="utf-8"))
    device_payload = dict(_section(payload, "device"))
    if "rotation" in device_payload:
        device_payload["rotation"] = migrate_rotation(device_payload["rotation"])
    config = AppConfig(
        device=DeviceConfig(**device_payload),
        sensors=SensorConfig(**_section(payload, "sensors")),
        ai=AIConfig(**_section(payload, "ai")),
        overlay=OverlayConfig(**_section(payload, "overlay")),
        app=AppOptions(**_section(payload, "app")),
    )
    config.validate()
    return config


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config.validate()
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return target


def _section(payload: Any, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be an object")
    value = payload.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"configuration section {name!r} must be an object")
    return value


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )
