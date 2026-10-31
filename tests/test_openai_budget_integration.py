import copy
from decimal import Decimal
import threading
import tkinter as tk
import pytest

from ai_mini_monitor import app
from ai_mini_monitor.ai.openai_budget import BudgetSnapshot
from ai_mini_monitor.config import AppConfig, OpenAIBudgetConfig, load_config, save_config
from ai_mini_monitor.controller import MonitorController
from ai_mini_monitor.desktop_session import DesktopSession


def test_worker_config_writes_preserve_new_api_settings(tmp_path):
    old = AppConfig()
    current = copy.deepcopy(old)
    current.openai = OpenAIBudgetConfig(True, "100", "2026-10-01")
    path = save_config(current, tmp_path / "config.json")
    old.device.brightness = 30
    app._save_worker_config_preserving_overlay(old, path)
    assert load_config(path).openai == current.openai
    app._merge_current_overlay_config(old, current, path)
    assert load_config(path).openai == current.openai


def test_api_settings_are_separate_from_secret_store(tmp_path):
    assert hasattr(app, "_save_api_configuration"), "missing API config callback"
    from ai_mini_monitor.security.dpapi import DPAPISecretStore
    store = DPAPISecretStore(tmp_path / "secret.dpapi")
    new = OpenAIBudgetConfig(True, "100", "2026-10-01")
    key = "test-only-not-a-real-admin-key"
    path = tmp_path / "config.json"
    updated = app._save_api_configuration(AppConfig(), new, key, store, path)
    assert updated.openai == new
    assert store.get() == key
    assert key.encode() not in store.path.read_bytes()
    assert key not in path.read_text()
    assert updated.ai.provider == "codex_account"


def test_config_save_failure_never_replaces_existing_key(tmp_path, monkeypatch):
    class Store:
        value = "old-organization-key"
        def configured(self): return True
        def set(self, value): self.value = value
    store = Store()
    def fail(*args, **kwargs): raise OSError("fixture failure")
    monkeypatch.setattr(app, "save_config", fail)
    with pytest.raises(OSError):
        app._save_api_configuration(AppConfig(), OpenAIBudgetConfig(True, "100", "2026-10-01"),
                                    "new-organization-test-key", store, tmp_path / "config.json")
    assert store.value == "old-organization-key"


def test_secret_store_failure_restores_previous_config(tmp_path):
    class Store:
        def configured(self): return True
        def set(self, value): raise OSError("fixture failure")
    original = AppConfig()
    path = save_config(original, tmp_path / "config.json")
    with pytest.raises(OSError):
        app._save_api_configuration(original, OpenAIBudgetConfig(True, "100", "2026-10-01"),
                                   "new-organization-test-key", Store(), path)
    assert load_config(path) == original


def test_api_snapshot_updates_even_without_codex_callback_or_usb():
    snap = BudgetSnapshot(OpenAIBudgetConfig(True, "100", "2026-10-01"), Decimal("23.50"), "ok")
    import inspect
    assert "api_snapshot" in inspect.signature(MonitorController).parameters
    controller = MonitorController(AppConfig(), enable_serial=False, api_snapshot=lambda: snap.ai)
    thread = threading.Thread(target=controller._run_codex_account)
    thread.start()
    try:
        for _ in range(100):
            if controller.store.read().api is not None:
                break
            controller._ai_wake.wait(.01)
        assert controller.store.read().api.primary_value == "23.5%"
        assert controller.store.read().ai.provider.value == "codex_account"
    finally:
        controller._stop.set()
        controller._ai_wake.set()
        thread.join(2)


def test_session_injects_budget_callback_without_usb_start():
    seen = []
    class Controller:
        def __init__(self, config, *, enable_serial, api_snapshot):
            seen.append((enable_serial, api_snapshot()))
        def start(self): pass
        def stop(self, timeout=20): return True
    session = DesktopSession(enable_serial=False, controller_factory=Controller, api_snapshot=lambda: None)
    try:
        assert session.start_desktop(AppConfig()).ok
        assert seen == [(False, None)]
    finally:
        session.stop()


def test_budget_dialog_masks_key_clears_entry_and_validates_before_save():
    from ai_mini_monitor.ui import openai_budget
    root = tk.Tk()
    root.withdraw()
    saved = []
    dialog = openai_budget.OpenAIBudgetDialog(root, on_save=lambda config,key: saved.append((config,key)), on_refresh=lambda: True, checkbutton_style="BudgetTest.TCheckbutton")
    try:
        dialog.show(OpenAIBudgetConfig(), key_configured=False)
        assert dialog.key_entry.cget("show")
        assert dialog.enabled_check.cget("style") == "BudgetTest.TCheckbutton"
        dialog.enabled.set(True)
        dialog.total.set("0")
        dialog.start.set("2026-10-01")
        dialog.key.set("test-only")
        dialog.save()
        assert not saved
        dialog.total.set("100")
        dialog.save()
        assert saved[0][0].total_usd == "100"
        assert dialog.key.get() == ""
        dialog.key.set("not-a-real-key")
        dialog.hide()
        assert dialog.key.get() == ""
    finally:
        root.destroy()
