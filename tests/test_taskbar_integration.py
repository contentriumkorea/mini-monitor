"""Taskbar settings must survive unrelated desktop settings writes."""
from dataclasses import replace

import pytest

from ai_mini_monitor import app
from ai_mini_monitor.config import AppConfig, load_config, save_config
from ai_mini_monitor.ui.overlay import OverlayState


def test_taskbar_state_survives_worker_save_and_does_not_change_dashboard(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig()
    config.overlay.enabled = True
    config.overlay.opacity = 0.4
    save_config(config, path)
    state = OverlayState(x=-290, y=1050, width=258, height=32,
                         opacity=0.65, scale_percent=100, visible=True)
    changed = app._persist_taskbar_state_config(config, state, path)
    assert changed.overlay.taskbar_enabled is True
    assert (changed.overlay.taskbar_x, changed.overlay.taskbar_y) == (-290, 1050)
    assert changed.overlay.enabled is True
    assert changed.overlay.opacity == 0.4
    # A slow worker still carries the old snapshot; saving it must not undo a drag.
    app._save_worker_config_preserving_overlay(config, path)
    saved = load_config(path)
    assert saved.overlay.taskbar_enabled is True
    assert saved.overlay.taskbar_x == -290
    app._persist_taskbar_state_config(saved, replace(state, visible=False), path)
    assert load_config(path).overlay.taskbar_enabled is False


def test_taskbar_options_persist_canonically_without_losing_latest_overlay_state(tmp_path):
    path = tmp_path / "config.json"
    old = AppConfig()
    save_config(old, path)
    latest = load_config(path)
    latest.overlay.enabled = True
    latest.overlay.opacity = 0.4
    latest.overlay.taskbar_x = 1200
    latest.overlay.taskbar_y = 1040
    save_config(latest, path)

    persist = getattr(app, "_persist_taskbar_options_config", None)
    assert callable(persist)
    changed = persist(old, ("codex", "cpu"), "both", path)
    assert changed.overlay.taskbar_items == ["cpu", "codex"]
    assert changed.overlay.taskbar_style == "both"
    assert changed.overlay.enabled is True
    assert changed.overlay.opacity == 0.4
    assert (changed.overlay.taskbar_x, changed.overlay.taskbar_y) == (1200, 1040)
    app._save_worker_config_preserving_overlay(old, path)
    assert load_config(path).overlay == changed.overlay


@pytest.mark.parametrize("field,value", [
    ("taskbar_enabled", 1), ("taskbar_x", True), ("taskbar_y", 2**35),
])
def test_taskbar_settings_reject_invalid_values(field, value):
    config = AppConfig()
    setattr(config.overlay, field, value)
    with pytest.raises(ValueError):
        config.validate()
