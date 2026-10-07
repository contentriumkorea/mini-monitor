# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
from dataclasses import asdict

import pytest
import ai_mini_monitor.config as config_module

from ai_mini_monitor.config import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_OVERLAY_OPACITY,
    DEFAULT_OVERLAY_SCALE_PERCENT,
    AppConfig,
    load_config,
    save_config,
)
from ai_mini_monitor.models import AIProviderKind
from ai_mini_monitor.orientation import CANONICAL_ROTATIONS


def test_defaults_and_all_documented_boundaries_validate() -> None:
    AppConfig().validate()
    assert AppConfig().overlay.taskbar_items == list(getattr(config_module, "DEFAULT_TASKBAR_ITEMS", ()))
    assert AppConfig().overlay.taskbar_style == "icon"


    for usb_fps in (1.0, 8.0):
        config = AppConfig()
        config.device.usb_fps = usb_fps
        config.validate()
    for sample_ms in (250, 2_000):
        config = AppConfig()
        config.sensors.sample_interval_ms = sample_ms
        config.validate()
    for render_fps in (10.0, 15.0):
        config = AppConfig()
        config.app.render_fps = render_fps
        config.validate()
    assert AppConfig().ai.provider == AIProviderKind.CODEX_ACCOUNT.value
    for rotation in CANONICAL_ROTATIONS:
        config = AppConfig()
        config.device.rotation = rotation
        config.validate()
    for brightness in (1, 50):
        config = AppConfig()
        config.device.brightness = brightness
        config.validate()
    for opacity in (0.0, 0.35, 1.0):
        config = AppConfig()
        config.overlay.opacity = opacity
        config.validate()
    for scale_percent in (50, 200):
        config = AppConfig()
        config.overlay.scale_percent = scale_percent
        config.validate()
    for coordinate in (-(2**31), (2**31) - 1):
        config = AppConfig()
        config.overlay.x = coordinate
        config.overlay.y = coordinate
        config.validate()


def test_taskbar_options_are_canonical_and_persisted(tmp_path) -> None:
    validator = getattr(config_module, "validate_taskbar_options", None)
    assert callable(validator)
    assert validator(["codex", "cpu"], "both") == ("cpu", "codex")
    config = AppConfig()
    config.overlay.taskbar_items = ["codex", "cpu"]
    config.overlay.taskbar_style = "text"
    path = tmp_path / "config.json"
    save_config(config, path)
    assert load_config(path).overlay.taskbar_items == ["cpu", "codex"]
    assert load_config(path).overlay.taskbar_style == "text"


@pytest.mark.parametrize("items,style", [([], "icon"), (["cpu", "cpu"], "icon"), (["disk"], "icon"), ("cpu", "icon"), (["cpu"], "unknown")])
def test_invalid_taskbar_options_are_rejected(items, style) -> None:
    validator = getattr(config_module, "validate_taskbar_options", None)
    assert callable(validator)
    with pytest.raises(ValueError, match="taskbar"):
        validator(items, style)


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("device", "usb_fps", 0.9, "usb_fps"),
        ("device", "usb_fps", 8.1, "usb_fps"),
        ("device", "rotation", "diagonal", "device.rotation"),
        ("device", "brightness", 0, "brightness"),
        ("device", "brightness", 51, "brightness"),
        ("device", "brightness", True, "brightness"),
        ("device", "brightness", False, "brightness"),
        ("device", "brightness", 25.0, "brightness"),
        ("device", "brightness", "25", "brightness"),
        ("device", "brightness", float("nan"), "brightness"),
        ("sensors", "sample_interval_ms", 249, "sample_interval_ms"),
        ("sensors", "sample_interval_ms", 2_001, "sample_interval_ms"),
        ("ai", "usage_refresh_seconds", 59, "usage_refresh_seconds"),
        ("ai", "usage_refresh_seconds", float("nan"), "usage_refresh_seconds"),
        ("ai", "usage_refresh_seconds", float("inf"), "usage_refresh_seconds"),
        ("ai", "usage_refresh_seconds", True, "usage_refresh_seconds"),
        ("ai", "usage_refresh_seconds", "60", "usage_refresh_seconds"),
        ("ai", "cost_refresh_seconds", 599, "cost_refresh_seconds"),
        ("ai", "cost_refresh_seconds", float("nan"), "cost_refresh_seconds"),
        ("ai", "cost_refresh_seconds", float("inf"), "cost_refresh_seconds"),
        ("ai", "cost_refresh_seconds", False, "cost_refresh_seconds"),
        ("ai", "cost_refresh_seconds", "600", "cost_refresh_seconds"),
        ("ai", "provider", "scrape_chatgpt", "provider"),
        ("ai", "codex_local_consent", 1, "codex_local_consent"),
        ("ai", "codex_local_consent", "true", "codex_local_consent"),
        ("ai", "codex_cli_path", "", "codex_cli_path"),
        ("ai", "codex_cli_path", 42, "codex_cli_path"),
        ("ai", "daily_budget_usd", 0, "budgets"),
        ("ai", "monthly_budget_usd", -1, "budgets"),
        ("ai", "daily_budget_usd", float("nan"), "budgets"),
        ("ai", "monthly_budget_usd", float("inf"), "budgets"),
        ("ai", "daily_budget_usd", True, "budgets"),
        ("overlay", "enabled", 1, "overlay.enabled"),
        ("overlay", "enabled", "true", "overlay.enabled"),
        ("overlay", "opacity", -0.001, "overlay.opacity"),
        ("overlay", "opacity", 1.001, "overlay.opacity"),
        ("overlay", "opacity", True, "overlay.opacity"),
        ("overlay", "opacity", "0.85", "overlay.opacity"),
        ("overlay", "opacity", float("nan"), "overlay.opacity"),
        ("overlay", "opacity", float("inf"), "overlay.opacity"),
        ("overlay", "scale_percent", 49, "overlay.scale_percent"),
        ("overlay", "scale_percent", 201, "overlay.scale_percent"),
        ("overlay", "scale_percent", True, "overlay.scale_percent"),
        ("overlay", "scale_percent", 100.0, "overlay.scale_percent"),
        ("overlay", "scale_percent", "100", "overlay.scale_percent"),
        ("overlay", "x", True, "overlay.x"),
        ("overlay", "x", 1.5, "overlay.x"),
        ("overlay", "x", -(2**31) - 1, "overlay.x"),
        ("overlay", "y", False, "overlay.y"),
        ("overlay", "y", "0", "overlay.y"),
        ("overlay", "y", 2**31, "overlay.y"),
        ("app", "render_fps", 9.9, "render_fps"),
        ("app", "render_fps", 15.1, "render_fps"),
    ],
)
def test_invalid_configuration_is_rejected(
    section: str, field: str, value: object, message: str
) -> None:
    config = AppConfig()
    setattr(getattr(config, section), field, value)
    with pytest.raises(ValueError, match=message):
        config.validate()


@pytest.mark.parametrize(("x", "y"), [(10, None), (None, -20)])
def test_overlay_position_requires_a_complete_coordinate_pair(x, y) -> None:
    config = AppConfig()
    config.overlay.x = x
    config.overlay.y = y

    with pytest.raises(ValueError, match="both be set"):
        config.validate()


def test_atomic_save_and_load_round_trip(tmp_path) -> None:
    path = tmp_path / "settings" / "config.json"
    config = AppConfig()
    config.device.manual_port = "COM12"
    config.device.brightness = 37
    config.sensors.cpu_temperature_sensor = "/intelcpu/0/temperature/0"
    config.ai.provider = AIProviderKind.CODEX_ACCOUNT.value
    config.overlay.enabled = True
    config.overlay.opacity = 0.62
    config.overlay.scale_percent = 135
    config.overlay.x = -1_920
    config.overlay.y = 140
    config.app.log_level = "DEBUG"

    assert save_config(config, path) == path
    assert path.read_bytes().endswith(b"\n")
    assert load_config(path) == config
    assert json.loads(path.read_text(encoding="utf-8")) == asdict(config)
    assert list(path.parent.glob("*.tmp")) == []


def test_missing_file_returns_valid_defaults(tmp_path) -> None:
    config = load_config(tmp_path / "missing.json")
    assert config == AppConfig()
    assert config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value


@pytest.mark.parametrize("legacy_provider", [
    "codex_local", "openai_api", "chatgpt_activity", "not_configured",
])
def test_legacy_provider_loads_as_account_without_rewriting_user_file(tmp_path, legacy_provider) -> None:
    path = tmp_path / "legacy.json"
    original = json.dumps({"ai": {"provider": legacy_provider}, "device": {"brightness": 37}})
    path.write_text(original, encoding="utf-8")

    loaded = load_config(path)

    assert loaded.ai.provider == AIProviderKind.CODEX_ACCOUNT.value
    assert loaded.device.brightness == 37
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("legacy_provider", [
    "codex_local", "openai_api", "chatgpt_activity", "not_configured",
])
def test_runtime_rejects_legacy_provider_reactivation(legacy_provider) -> None:
    config = AppConfig()
    config.ai.provider = legacy_provider
    with pytest.raises(ValueError, match="ai.provider"):
        config.validate()


def test_codex_account_contract_preserves_legacy_configuration(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text('{"ai": {"provider": "codex_local"}}', encoding="utf-8")
    legacy = load_config(path)
    assert legacy.ai.codex_cli_path is None
    assert legacy.ai.provider == AIProviderKind.CODEX_ACCOUNT.value

    legacy.ai.provider = AIProviderKind.CODEX_ACCOUNT.value
    legacy.ai.codex_cli_path = r"C:\Program Files\Codex\codex.exe"
    save_config(legacy, path)
    loaded = load_config(path)
    assert loaded.ai.provider == "codex_account"
    assert loaded.ai.codex_cli_path == r"C:\Program Files\Codex\codex.exe"


def test_missing_rotation_uses_landscape_and_legacy_names_migrate_one_way(
    tmp_path,
) -> None:
    missing_path = tmp_path / "missing-rotation.json"
    missing_path.write_text('{"device": {}}', encoding="utf-8")
    assert load_config(missing_path).device.rotation == "landscape"

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        '{"device": {"rotation": "reverse_landscape"}}',
        encoding="utf-8",
    )
    config = load_config(legacy_path)
    assert config.device.rotation == "landscape_inverted"
    save_config(config, legacy_path)
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["device"][
        "rotation"
    ] == "landscape_inverted"


def test_missing_brightness_migrates_to_safe_default_and_saves_canonically(
    tmp_path,
) -> None:
    path = tmp_path / "legacy-config.json"
    path.write_text('{"device": {"rotation": "landscape"}}', encoding="utf-8")

    config = load_config(path)

    assert config.device.brightness == DEFAULT_BRIGHTNESS
    save_config(config, path)
    assert json.loads(path.read_text(encoding="utf-8"))["device"][
        "brightness"
    ] == DEFAULT_BRIGHTNESS


def test_missing_overlay_migrates_disabled_defaults_and_saves_canonically(
    tmp_path,
) -> None:
    path = tmp_path / "legacy-config.json"
    path.write_text('{"app": {"log_level": "INFO"}}', encoding="utf-8")

    config = load_config(path)

    assert config.overlay.enabled is False
    assert config.overlay.opacity == DEFAULT_OVERLAY_OPACITY
    assert config.overlay.scale_percent == DEFAULT_OVERLAY_SCALE_PERCENT
    assert config.overlay.x is None
    assert config.overlay.y is None
    save_config(config, path)
    assert json.loads(path.read_text(encoding="utf-8"))["overlay"] == {
        "enabled": False,
        "opacity": DEFAULT_OVERLAY_OPACITY,
        "scale_percent": DEFAULT_OVERLAY_SCALE_PERCENT,
        "x": None,
        "y": None,
        "taskbar_enabled": False,
        "taskbar_x": None,
        "taskbar_y": None,
        "taskbar_items": ["cpu", "ram", "gpu", "vram", "codex"],
        "taskbar_style": "icon",
    }


def test_programmatic_legacy_rotation_is_rejected_until_explicitly_migrated() -> None:
    config = AppConfig()
    config.device.rotation = "reverse_portrait"
    with pytest.raises(ValueError, match="device.rotation"):
        config.validate()


def test_disabling_auto_detection_requires_a_strict_manual_port() -> None:
    config = AppConfig()
    config.device.auto_detect = False
    with pytest.raises(ValueError, match="manual_port"):
        config.validate()
    config.device.manual_port = "COM12"
    config.validate()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"device": []},
        {"sensors": "bad"},
        {"ai": 1},
        {"overlay": []},
        {"app": None},
    ],
)
def test_non_object_roots_and_sections_are_rejected(tmp_path, payload: object) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        load_config(path)
