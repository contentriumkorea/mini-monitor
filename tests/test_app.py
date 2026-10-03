# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import threading
import time

import pytest
from PIL import Image

from ai_mini_monitor import app
from ai_mini_monitor.ai.codex_usage import CodexUsageSnapshot, CodexUsageStatus
from ai_mini_monitor.ai.codex_account import CodexAccountSnapshot, CodexAccountEvent
from ai_mini_monitor.updater import UpdateSnapshot
from ai_mini_monitor.config import AppConfig, load_config
from ai_mini_monitor.desktop_session import SerialTaskWorker, SessionResult, SessionState
from ai_mini_monitor.update_recovery import RecoveryNotice
from ai_mini_monitor.models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    DisplaySnapshot,
    SyncStatus,
)
from ai_mini_monitor.state import RuntimeValues
from ai_mini_monitor.ui.overlay import OverlayState
from ai_mini_monitor.ui.setup import OverlaySettings, SetupSelection


class FakeSecretStore:
    def __init__(self, configured: bool = False) -> None:
        self.value: str | None = "stored" if configured else None

    def configured(self) -> bool:
        return self.value is not None

    def set(self, value: str) -> None:
        self.value = value


class FakeSession:
    def __init__(
        self,
        *,
        enable_serial: bool = False,
        start_ok: bool = True,
        start_reason: str = "synthetic",
        running: bool = False,
    ) -> None:
        self.enable_serial = enable_serial
        self.start_ok = start_ok
        self.start_reason = start_reason
        self.shutting_down = False
        self.running = running
        self.started: list[AppConfig] = []
        self.brightness_requests: list[int] = []

    def start(self, config: AppConfig, *, prepare=None) -> SessionResult:
        self.started.append(config)
        if prepare is not None:
            prepare()
        return SessionResult(
            self.start_ok,
            SessionState.RUNNING if self.start_ok else SessionState.ERROR,
            None if self.start_ok else self.start_reason,
        )

    def request_brightness(self, brightness: int) -> bool:
        self.brightness_requests.append(brightness)
        return True


def test_no_serial_device_probe_never_enumerates_ports(monkeypatch) -> None:
    class ForbiddenDetector:
        def __init__(self) -> None:
            raise AssertionError("no-serial must not enumerate a device")

    monkeypatch.setattr(app, "DeviceDetector", ForbiddenDetector)
    result = app._detect_device(AppConfig(), enable_serial=False)
    assert result.ok
    assert "COM을 열지" in result.detail


def test_draft_selection_does_not_mutate_active_config() -> None:
    base = AppConfig()
    selection = SetupSelection(
        AIProviderKind.CODEX_LOCAL.value,
        True,
        usage_refresh_seconds=120,
        cost_refresh_seconds=900,
        daily_budget_usd=4.0,
        monthly_budget_usd=40.0,
        rotation="portrait_inverted",
        brightness=43,
    )
    draft = app._draft_config(base, selection)
    assert base.ai.provider == AIProviderKind.NOT_CONFIGURED.value
    assert base.ai.codex_local_consent is False
    assert draft.ai.provider == AIProviderKind.CODEX_LOCAL.value
    assert draft.ai.codex_local_consent is True
    assert draft.ai.usage_refresh_seconds == 120
    assert draft.ai.cost_refresh_seconds == 900
    assert draft.ai.daily_budget_usd == 4.0
    assert draft.ai.monthly_budget_usd == 40.0
    assert base.device.rotation == "landscape"
    assert draft.device.rotation == "portrait_inverted"
    assert base.device.brightness == 25
    assert draft.device.brightness == 43


def test_minimized_selection_preserves_saved_openai_options() -> None:
    config = AppConfig()
    config.ai.provider = AIProviderKind.OPENAI_API.value
    config.ai.usage_refresh_seconds = 120
    config.ai.cost_refresh_seconds = 900
    config.ai.daily_budget_usd = 3.5
    config.ai.monthly_budget_usd = 35.0
    config.device.rotation = "portrait"
    config.device.brightness = 36

    selection = app._selection_from_config(config)

    assert selection.openai_admin_key == ""
    assert selection.usage_refresh_seconds == 120
    assert selection.cost_refresh_seconds == 900
    assert selection.daily_budget_usd == 3.5
    assert selection.monthly_budget_usd == 35.0
    assert selection.rotation == "portrait"
    assert selection.brightness == 36


def test_brightness_change_persists_while_stopped_without_touching_com(
    tmp_path,
) -> None:
    path = tmp_path / "config.json"
    session = FakeSession(enable_serial=True, running=False)
    base = AppConfig()

    result = app._change_brightness(session, base, 34, path)

    assert result.action.ok
    assert result.brightness == 34
    assert result.config is not None
    assert result.config.device.brightness == 34
    assert load_config(path).device.brightness == 34
    assert base.device.brightness == 25
    assert session.brightness_requests == []


def test_brightness_change_persists_then_routes_to_active_session(
    tmp_path,
) -> None:
    path = tmp_path / "config.json"
    session = FakeSession(enable_serial=True, running=True)

    result = app._change_brightness(session, AppConfig(), 47, path)

    assert result.action.ok
    assert session.brightness_requests == [47]
    assert load_config(path).device.brightness == 47
    assert "writer" in result.action.detail


@pytest.mark.parametrize(
    "brightness",
    [True, False, "25", 25.0, float("nan"), 0, 51],
)
def test_brightness_change_rejects_invalid_values_before_save_or_route(
    tmp_path,
    brightness: object,
) -> None:
    path = tmp_path / "config.json"
    session = FakeSession(enable_serial=True, running=True)

    with pytest.raises(ValueError, match="brightness"):
        app._change_brightness(  # type: ignore[arg-type]
            session,
            AppConfig(),
            brightness,
            path,
        )

    assert not path.exists()
    assert session.brightness_requests == []


def test_shutdown_rejects_brightness_persistence_and_live_route(tmp_path) -> None:
    path = tmp_path / "config.json"
    session = FakeSession(enable_serial=True, running=True)
    session.shutting_down = True

    result = app._change_brightness(session, AppConfig(), 33, path)

    assert not result.action.ok
    assert result.config is None
    assert not path.exists()
    assert session.brightness_requests == []


def test_live_route_failure_keeps_the_already_persisted_desired_value(
    tmp_path,
) -> None:
    class RaisingSession(FakeSession):
        def request_brightness(self, brightness: int) -> bool:
            self.brightness_requests.append(brightness)
            raise RuntimeError("synthetic serial route failure")

    path = tmp_path / "config.json"
    session = RaisingSession(enable_serial=True, running=True)

    result = app._change_brightness(session, AppConfig(), 39, path)

    assert result.action.ok
    assert result.config is not None
    assert result.config.device.brightness == 39
    assert load_config(path).device.brightness == 39
    assert session.brightness_requests == [39]
    assert "다음 시작" in result.action.detail


def test_minimized_not_configured_provider_is_not_silently_recommended() -> None:
    config = AppConfig()
    config.ai.provider = AIProviderKind.NOT_CONFIGURED.value

    assert (
        app._initial_setup_provider(config, minimized=True)
        == AIProviderKind.NOT_CONFIGURED.value
    )
    assert (
        app._initial_setup_provider(config, minimized=False)
        == AIProviderKind.CODEX_ACCOUNT.value
    )


def test_start_requires_explicit_codex_consent_before_saving(tmp_path) -> None:
    path = tmp_path / "config.json"
    draft = AppConfig()
    draft.ai.provider = AIProviderKind.CODEX_LOCAL.value
    session = FakeSession(enable_serial=False)

    result = app._start_session(
        session,
        draft,
        SetupSelection(AIProviderKind.CODEX_LOCAL.value, False),
        FakeSecretStore(),
        path,
    )

    assert not result.action.ok
    assert not path.exists()
    assert session.started == []


def test_successful_local_start_atomically_saves_consent_and_starts(tmp_path) -> None:
    path = tmp_path / "config.json"
    draft = AppConfig()
    draft.ai.provider = AIProviderKind.CODEX_LOCAL.value
    draft.ai.codex_local_consent = True
    session = FakeSession(enable_serial=False)

    result = app._start_session(
        session,
        draft,
        SetupSelection(AIProviderKind.CODEX_LOCAL.value, True),
        FakeSecretStore(),
        path,
    )

    assert result.action.ok
    assert len(session.started) == 1
    saved = load_config(path)
    assert saved.ai.provider == AIProviderKind.CODEX_LOCAL.value
    assert saved.ai.codex_local_consent is True


def test_openai_key_is_one_shot_and_dpapi_store_is_used(tmp_path) -> None:
    path = tmp_path / "config.json"
    draft = AppConfig()
    draft.ai.provider = AIProviderKind.OPENAI_API.value
    store = FakeSecretStore()
    session = FakeSession(enable_serial=False)

    result = app._start_session(
        session,
        draft,
        SetupSelection(
            AIProviderKind.OPENAI_API.value,
            False,
            openai_admin_key="synthetic-key",
        ),
        store,
        path,
    )

    assert result.action.ok
    assert store.value == "synthetic-key"
    assert "synthetic-key" not in path.read_text(encoding="utf-8")


def test_port_in_use_start_failure_is_actionable_and_never_exposes_raw_error(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeDetector:
        def select(self, _manual_port):
            return type("Port", (), {"device": "COM3"})()

    monkeypatch.setattr(app, "DeviceDetector", FakeDetector)
    draft = AppConfig()
    session = FakeSession(
        enable_serial=True,
        start_ok=False,
        start_reason="port_in_use",
    )

    result = app._start_session(
        session,
        draft,
        SetupSelection(AIProviderKind.NOT_CONFIGURED.value, False),
        FakeSecretStore(),
        tmp_path / "config.json",
    )

    assert not result.action.ok
    assert result.action.title == "PORT IN USE · COM3"
    assert "UsbMonitor" in result.action.detail
    assert "트레이까지 완전히 종료" in result.action.detail
    assert "모니터 시작" in result.action.detail
    assert "PermissionError" not in result.action.detail


def test_post_prepare_start_failure_keeps_new_settings_and_latest_overlay(
    tmp_path,
) -> None:
    path = tmp_path / "config.json"
    persisted = AppConfig()
    persisted.overlay.enabled = True
    persisted.overlay.opacity = 0.55
    persisted.overlay.scale_percent = 150
    persisted.overlay.x = -1200
    persisted.overlay.y = 72
    app.save_config(persisted, path)

    draft = AppConfig()
    draft.ai.provider = AIProviderKind.CHATGPT_ACTIVITY.value
    draft.device.rotation = "portrait_inverted"
    draft.device.brightness = 44
    session = FakeSession(
        enable_serial=False,
        start_ok=False,
        start_reason="start_failed",
    )

    result = app._start_session(
        session,
        draft,
        SetupSelection(
            AIProviderKind.CHATGPT_ACTIVITY.value,
            False,
            rotation="portrait_inverted",
            brightness=44,
        ),
        FakeSecretStore(),
        path,
    )

    assert not result.action.ok
    assert result.config is not None
    assert result.config.ai.provider == AIProviderKind.CHATGPT_ACTIVITY.value
    assert result.config.device.rotation == "portrait_inverted"
    assert result.config.device.brightness == 44
    assert result.config.overlay == persisted.overlay
    assert load_config(path) == result.config


def test_shutdown_gate_prevents_openai_secret_and_config_writes(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.json"
    draft = AppConfig()
    draft.ai.provider = AIProviderKind.OPENAI_API.value
    store = FakeSecretStore()
    session = FakeSession(enable_serial=False)
    session.shutting_down = True
    save_calls = 0

    def forbidden_save(*_args, **_kwargs) -> None:
        nonlocal save_calls
        save_calls += 1

    monkeypatch.setattr(app, "save_config", forbidden_save)
    result = app._start_session(
        session,
        draft,
        SetupSelection(
            AIProviderKind.OPENAI_API.value,
            False,
            openai_admin_key="synthetic-key",
        ),
        store,
        path,
    )

    assert not result.action.ok
    assert store.value is None
    assert save_calls == 0
    assert not path.exists()
    assert session.started == []


def test_running_usage_check_wakes_active_provider_and_returns_lcd_value() -> None:
    before_ai = AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.OK,
        primary_value="10%",
        primary_label="5H LEFT",
    )
    after_ai = AIData(
        provider=AIProviderKind.CODEX_LOCAL,
        title="CODEX LIMITS",
        status=SyncStatus.OK,
        primary_value="12%",
        primary_label="5H LEFT",
        fields=(
            ("7D LEFT", "34%"),
            ("7D RESET", "5D 13H"),
            ("UPDATED", "21:59"),
        ),
        last_sync=datetime.now(timezone.utc),
    )

    class RefreshSession:
        def __init__(self) -> None:
            self.refreshed = False

        def runtime_values(self) -> RuntimeValues:
            return RuntimeValues(
                None,
                after_ai if self.refreshed else before_ai,
                ConnectionData(),
                0,
            )

        def request_ai_refresh(self) -> bool:
            self.refreshed = True
            return True

    session = RefreshSession()
    result = app._refresh_active_usage(session, timeout=0.1, poll_interval=0.001)  # type: ignore[arg-type]
    assert session.refreshed
    assert result.ai == after_ai
    assert result.action.ok
    assert "12%" in result.action.title
    assert "새 값" in result.action.detail
    assert "7D LEFT 34%" in result.action.detail
    assert "7D RESET 5D 13H" in result.action.detail
    assert "UPDATED" not in result.action.detail


def test_setup_usage_check_labels_seven_day_remaining_fallback(
    monkeypatch,
) -> None:
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.OK,
        seven_day_remaining_percent=82.0,
        updated_at=datetime.now(timezone.utc),
    )

    class FakeCodexProvider:
        def __init__(self, *, consent_granted: bool) -> None:
            assert consent_granted is True

        def refresh(self) -> CodexUsageSnapshot:
            return snapshot

    monkeypatch.setattr(app, "CodexUsageProvider", FakeCodexProvider)

    result = app._check_usage(
        SetupSelection(AIProviderKind.CODEX_LOCAL.value, True),
        FakeSecretStore(),
    )

    assert result.action.ok
    assert result.action.title == "7D LEFT 82%"
    assert result.ai.primary_label == "7D LEFT"
    assert result.ai.primary_value == "82%"


def test_preview_uses_owned_setup_frame_before_controller_start() -> None:
    fallback = Image.new("RGB", (480, 320), "#123456")
    selected = app._select_preview_frame(None, fallback)
    assert selected is not None
    assert selected.size == (480, 320)
    assert selected.getpixel((0, 0)) == (18, 52, 86)
    assert selected is not fallback


def test_pre_start_preview_renderer_uses_selected_native_dimensions() -> None:
    for rotation, dimensions in (
        ("landscape", (480, 320)),
        ("landscape_inverted", (480, 320)),
        ("portrait", (320, 480)),
        ("portrait_inverted", (320, 480)),
    ):
        image = app._renderer_for_rotation(rotation).render(DisplaySnapshot())
        assert image.size == dimensions


def test_overlay_settings_persist_without_touching_saved_position(tmp_path) -> None:
    path = tmp_path / "config.json"
    base = AppConfig()
    base.overlay.x = -800
    base.overlay.y = 120

    saved = app._apply_overlay_settings_config(
        base,
        OverlaySettings(True, 0.64, 135),
        path,
    )

    assert saved.overlay.enabled is True
    assert saved.overlay.opacity == pytest.approx(0.64)
    assert saved.overlay.scale_percent == 135
    assert (saved.overlay.x, saved.overlay.y) == (-800, 120)
    assert load_config(path).overlay == saved.overlay
    assert base.overlay.enabled is False


def test_overlay_quick_toggle_can_atomically_persist_fully_clear_background(
    tmp_path,
) -> None:
    path = tmp_path / "config.json"

    shown = app._apply_overlay_settings_config(
        AppConfig(),
        OverlaySettings(True, 0.0, 100),
        path,
    )
    hidden = app._apply_overlay_settings_config(
        shown,
        OverlaySettings(False, 0.0, 100),
        path,
    )

    assert shown.overlay.enabled is True
    assert shown.overlay.opacity == 0.0
    assert hidden.overlay.enabled is False
    assert hidden.overlay.opacity == 0.0
    assert load_config(path).overlay == hidden.overlay


class _FailingOverlayRuntime:
    def __init__(self, base: AppConfig, *, fail_once: str) -> None:
        self.opacity = base.overlay.opacity
        self.scale_percent = base.overlay.scale_percent
        self.visible = base.overlay.enabled
        self.fail_once = fail_once
        self.image: Image.Image | None = None

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_once == operation:
            self.fail_once = ""
            raise RuntimeError("synthetic secret native presenter detail")

    def set_opacity(self, value: float, *, notify: bool) -> None:
        self.opacity = value
        self._maybe_fail("set_opacity")

    def set_scale(self, value: int, *, notify: bool) -> None:
        self.scale_percent = value
        self._maybe_fail("set_scale")

    def update_image(self, image: Image.Image) -> None:
        self.image = image.copy()
        self._maybe_fail("update_image")

    def show(self, *, notify: bool) -> None:
        self.visible = True
        self._maybe_fail("show")

    def hide(self, *, notify: bool) -> None:
        self.visible = False
        self._maybe_fail("hide")


@pytest.mark.parametrize("failure", ["set_scale", "update_image", "show"])
def test_native_overlay_failure_rolls_back_runtime_and_does_not_save(
    tmp_path,
    caplog,
    failure: str,
) -> None:
    path = tmp_path / "config.json"
    base = AppConfig()
    overlay = _FailingOverlayRuntime(base, fail_once=failure)

    result = app._apply_overlay_request(
        overlay,
        base,
        OverlaySettings(True, 0.0, 150),
        path,
        Image.new("RGB", (480, 320), "#123456"),
    )

    assert not result.action.ok
    assert result.config is base
    assert overlay.opacity == base.overlay.opacity
    assert overlay.scale_percent == base.overlay.scale_percent
    assert overlay.visible is base.overlay.enabled
    assert not path.exists()
    assert "RuntimeError" in caplog.text
    assert "synthetic secret" not in caplog.text


def test_overlay_request_does_not_swallow_process_control_exceptions(tmp_path) -> None:
    class InterruptingOverlay(_FailingOverlayRuntime):
        def show(self, *, notify: bool) -> None:
            raise KeyboardInterrupt

    base = AppConfig()
    overlay = InterruptingOverlay(base, fail_once="")

    with pytest.raises(KeyboardInterrupt):
        app._apply_overlay_request(
            overlay,
            base,
            OverlaySettings(True, 0.0, 100),
            tmp_path / "config.json",
            Image.new("RGB", (480, 320)),
        )


def test_persistent_native_failure_continues_every_rollback_step(tmp_path) -> None:
    class PersistentScaleFailure(_FailingOverlayRuntime):
        def set_scale(self, value: int, *, notify: bool) -> None:
            self.scale_percent = value
            raise RuntimeError("synthetic persistent native detail")

    base = AppConfig()
    base.overlay.enabled = True
    overlay = PersistentScaleFailure(base, fail_once="")

    result = app._apply_overlay_request(
        overlay,
        base,
        OverlaySettings(True, 0.0, 150),
        tmp_path / "config.json",
        Image.new("RGB", (480, 320)),
    )

    assert not result.action.ok
    assert result.action.state_uncertain
    assert overlay.opacity == base.overlay.opacity
    assert overlay.scale_percent == base.overlay.scale_percent
    assert overlay.visible is True


def test_disable_save_failure_and_failed_restore_reports_actual_hidden_state(
    tmp_path,
    monkeypatch,
) -> None:
    class PersistentShowFailure(_FailingOverlayRuntime):
        def show(self, *, notify: bool) -> None:
            raise RuntimeError("synthetic persistent presenter detail")

    base = AppConfig()
    base.overlay.enabled = True
    overlay = PersistentShowFailure(base, fail_once="")
    monkeypatch.setattr(
        app,
        "_apply_overlay_settings_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk detail")),
    )

    result = app._apply_overlay_request(
        overlay,
        base,
        OverlaySettings(False, 0.0, 100),
        tmp_path / "config.json",
        None,
    )

    assert not result.action.ok
    assert result.action.state_uncertain
    assert result.action.overlay_settings == OverlaySettings(False, 0.85, 100)
    assert overlay.visible is False
    assert result.config is base


def test_overlay_drag_state_persists_negative_multimonitor_position(tmp_path) -> None:
    path = tmp_path / "config.json"
    state = OverlayState(-1440, 72, 480, 320, 0.85, 100, True)

    saved = app._persist_overlay_state_config(AppConfig(), state, path)

    assert saved.overlay.enabled is True
    assert (saved.overlay.x, saved.overlay.y) == (-1440, 72)
    assert load_config(path).overlay == saved.overlay


def test_already_hidden_overlay_state_is_reflected_before_disk_retry() -> None:
    base = AppConfig()
    base.overlay.enabled = True
    state = OverlayState(-1440, 72, 480, 320, 0.0, 100, False)

    current = app._overlay_config_from_state(base, state)

    assert current.overlay.enabled is False
    assert current.overlay.opacity == 0.0
    assert (current.overlay.x, current.overlay.y) == (-1440, 72)
    assert base.overlay.enabled is True


def test_optional_overlay_presentation_failure_recovers_hidden_and_persists_off(
    tmp_path,
) -> None:
    class Runtime(_FailingOverlayRuntime):
        @property
        def state(self) -> OverlayState:
            return OverlayState(
                10,
                20,
                480,
                320,
                self.opacity,
                self.scale_percent,
                self.visible,
            )

    path = tmp_path / "config.json"
    base = AppConfig()
    base.overlay.enabled = True
    app.save_config(base, path)
    overlay = Runtime(base, fail_once="")

    recovered, settings = app._recover_overlay_hidden(overlay, base, path)

    assert overlay.visible is False
    assert settings == OverlaySettings(False, 0.85, 100)
    assert recovered.overlay.enabled is False
    assert load_config(path).overlay.enabled is False


def test_failed_optional_overlay_hide_reports_actual_visible_state(tmp_path) -> None:
    class Runtime(_FailingOverlayRuntime):
        @property
        def state(self) -> OverlayState:
            return OverlayState(
                10,
                20,
                480,
                320,
                self.opacity,
                self.scale_percent,
                self.visible,
            )

        def hide(self, *, notify: bool) -> None:
            raise RuntimeError("synthetic hidden-path detail")

    path = tmp_path / "config.json"
    base = AppConfig()
    base.overlay.enabled = True
    app.save_config(base, path)
    overlay = Runtime(base, fail_once="")

    recovered, settings = app._recover_overlay_hidden(overlay, base, path)

    assert overlay.visible is True
    assert settings.enabled is True
    assert recovered.overlay.enabled is True
    assert load_config(path).overlay.enabled is True


def test_late_worker_config_cannot_revert_newer_overlay_settings(tmp_path) -> None:
    path = tmp_path / "config.json"
    stale_worker = AppConfig()
    current = AppConfig()
    current.overlay.enabled = True
    current.overlay.opacity = 0.55
    current.overlay.scale_percent = 150
    current.overlay.x = 2100
    current.overlay.y = 90

    merged = app._merge_current_overlay_config(stale_worker, current, path)

    assert merged.overlay == current.overlay
    assert load_config(path).overlay == current.overlay


def test_shutdown_overlay_merge_preserves_latest_worker_settings(tmp_path) -> None:
    path = tmp_path / "config.json"
    worker = AppConfig()
    worker.ai.provider = AIProviderKind.CHATGPT_ACTIVITY.value
    worker.device.rotation = "portrait"
    worker.device.brightness = 41
    app.save_config(worker, path)

    current = AppConfig()
    current.overlay.enabled = True
    current.overlay.opacity = 0.62
    current.overlay.scale_percent = 125
    current.overlay.x = -840
    current.overlay.y = 50

    merged = app._persist_latest_overlay_on_shutdown(current, path)

    assert merged.ai.provider == AIProviderKind.CHATGPT_ACTIVITY.value
    assert merged.device.rotation == "portrait"
    assert merged.device.brightness == 41
    assert merged.overlay == current.overlay
    assert load_config(path) == merged


@pytest.mark.parametrize("auto_exit_seconds", [0.0, -1.0, float("nan"), float("inf")])
def test_desktop_smoke_timeout_is_validated_before_mutex(
    monkeypatch,
    auto_exit_seconds: float,
) -> None:
    mutex_calls = 0

    def forbidden_mutex():
        nonlocal mutex_calls
        mutex_calls += 1
        raise AssertionError("mutex must not be acquired for invalid input")

    monkeypatch.setattr(app, "acquire_single_instance", forbidden_mutex)

    with pytest.raises(ValueError, match="positive"):
        app.run_desktop(AppConfig(), auto_exit_seconds=auto_exit_seconds)

    assert mutex_calls == 0


def test_desktop_smoke_rejects_serial_before_mutex(monkeypatch) -> None:
    monkeypatch.setattr(app, "acquire_single_instance", lambda *_args: (_ for _ in ()).throw(AssertionError("mutex")))
    with pytest.raises(ValueError, match="no serial"):
        app.run_desktop(AppConfig(), enable_serial=True, auto_exit_seconds=1.0)


def test_early_service_initialization_failure_releases_mutex(monkeypatch) -> None:
    class Guard:
        released = False

        def release(self) -> None:
            self.released = True

    guard = Guard()
    monkeypatch.setattr(app, "acquire_single_instance", lambda: guard)
    monkeypatch.setattr(
        app,
        "TrayController",
        lambda _commands: (_ for _ in ()).throw(RuntimeError("synthetic init")),
    )

    assert app.run_desktop(AppConfig(), enable_serial=False) == 1
    assert guard.released is True


def test_no_serial_desktop_smoke_uses_only_scoped_distinct_mutex(monkeypatch) -> None:
    names = []

    class Guard:
        def release(self):
            pass

    def acquire(name):
        names.append(name)
        return Guard()

    monkeypatch.setattr(app, "acquire_single_instance", acquire)
    monkeypatch.setattr(app, "TrayController", lambda _commands: (_ for _ in ()).throw(RuntimeError("stop before Tk")))
    assert app.run_desktop(AppConfig(), enable_serial=False, auto_exit_seconds=1.0) == 1
    assert names == [app.DEFAULT_MUTEX_NAME + "-DesktopSmoke"]


def test_read_autostart_state_preserves_custom_config_and_exact_match(
    monkeypatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "custom settings.json"
    expected = r'"C:\Apps\AI-Mini-Monitor.exe" --config "C:\custom.json" --minimized'
    monkeypatch.setattr(
        app.autostart,
        "build_app_command",
        lambda path: expected if path == config_path else "unexpected",
    )
    monkeypatch.setattr(
        app.autostart,
        "read_state",
        lambda command: app.autostart.AutostartState(command, command),
    )

    assert app._read_autostart_state(config_path) == (expected, True, False, False)


def test_read_autostart_error_is_reported_as_unknown(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "settings.json"
    expected = r'"C:\Apps\AI-Mini-Monitor.exe" --minimized'
    monkeypatch.setattr(app.autostart, "build_app_command", lambda _path: expected)

    def fail_read(_command):
        raise OSError("registry unavailable")

    monkeypatch.setattr(app.autostart, "read_state", fail_read)

    assert app._read_autostart_state(config_path) == (
        expected,
        False,
        False,
        True,
    )


def test_one_autostart_action_enables_and_disables_with_exact_readback(
    monkeypatch,
) -> None:
    expected = r'"C:\Apps\AI-Mini-Monitor.exe" --minimized'
    configured: dict[str, str] = {}

    def enable(*, command: str) -> str:
        configured["command"] = command
        return command

    def disable() -> bool:
        return configured.pop("command", None) is not None

    def read_state(command: str):
        return app.autostart.AutostartState(command, configured.get("command"))

    monkeypatch.setattr(app.autostart, "enable", enable)
    monkeypatch.setattr(app.autostart, "disable", disable)
    monkeypatch.setattr(app.autostart, "read_state", read_state)

    enabled = app._change_autostart(True, expected)
    assert enabled.ok
    assert configured == {"command": expected}

    disabled = app._change_autostart(False, expected)
    assert disabled.ok
    assert configured == {}


@pytest.mark.parametrize("helper_success", [False, True])
@pytest.mark.parametrize("manual_refresh", [False, True])
@pytest.mark.parametrize("disconnect_after_stop", [False, True])
@pytest.mark.parametrize("running_legacy", [False, True])
@pytest.mark.parametrize("setup_visible_for_notice", [False, True])
def test_desktop_account_login_refresh_and_stop_do_not_require_serial_or_restart_service(
    monkeypatch, tmp_path, helper_success, manual_refresh, disconnect_after_stop, running_legacy,
    setup_visible_for_notice,
) -> None:
    events = []
    actions = []
    rendered_ai = []
    accounts = []
    updaters = []
    clock = [1000.0]

    class Guard:
        def release(self):
            events.append("guard_close")

    class Account:
        def __init__(self, **kwargs):
            events.append("account_create")
            accounts.append(self)
            self.generation = 0
            self.state = "signed_out"

        def snapshot(self):
            value = "85%" if self.state == "ready" else "LOGIN"
            ai = AIData(AIProviderKind.CODEX_ACCOUNT, "CODEX", SyncStatus.DELAYED, value, "7D LEFT")
            return CodexAccountSnapshot(self.generation, self.state, "user@example.com" if self.state == "ready" else None,
                                        None, self.state == "login_pending", (), None, None, ai)

        def drain_events(self):
            return ()

        def begin_login(self):
            events.append("login")
            self.generation += 1
            self.state = "login_pending"
            return True

        def refresh(self):
            events.append("refresh")
            self.state = "ready"
            return True

        def set_polling(self, enabled):
            events.append(("polling", enabled))

        def close(self):
            events.append("account_close")

    class Session:
        def __init__(self, **kwargs):
            events.append("session_create")
            self.enable_serial = kwargs["enable_serial"]
            self.shutting_down = False
            self.running = False
            self.state = SessionState.STOPPED
            assert kwargs["codex_account_snapshot"] is not None

        def start(self, config, *, prepare=None):
            events.append("session_start")
            if prepare:
                prepare()
            self.running = True
            self.state = SessionState.RUNNING
            return SessionResult(True, self.state)

        def stop(self, timeout=20.0):
            events.append("session_stop")
            self.running = False
            self.state = SessionState.STOPPED
            return SessionResult(True, self.state)

        def begin_shutdown(self):
            self.shutting_down = True

        def latest_image(self):
            return Image.new("RGB", (480, 320), "red") if not self.running else None

        def runtime_values(self):
            return None

        def suspend_active(self, timeout=2.0):
            return True

        def resume_active(self):
            return True

    class Worker:
        def __init__(self):
            self.results = []

        def submit(self, kind, callback):
            events.append(("work", kind))
            self.results.append(app.WorkResult(kind, callback()))
            return True

        def poll(self):
            results, self.results = self.results, []
            return results

        def begin_shutdown(self):
            pass

        def close(self, timeout=10.0):
            return True

    class Window:
        def __init__(self, setup):
            self.setup = setup
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

        def mainloop(self):
            callbacks = self.setup.callbacks
            if running_legacy:
                callbacks["on_start"](SetupSelection(AIProviderKind.CODEX_LOCAL.value, True))
                self._poll_once()
            callbacks["on_codex_login"]()
            if manual_refresh:
                callbacks["on_check_usage"](SetupSelection(AIProviderKind.CODEX_ACCOUNT.value, False))
            if running_legacy:
                assert load_config(tmp_path / "settings.json").ai.provider == AIProviderKind.CODEX_LOCAL.value
                assert self.setup.selected_provider() == AIProviderKind.CODEX_LOCAL.value
                callbacks["on_stop"]()
                self._poll_once()
                callbacks["on_check_usage"](SetupSelection(AIProviderKind.CODEX_ACCOUNT.value, False))
            else:
                assert "session_start" not in events
            assert not any(item == ("work", "usage") for item in events)
            callbacks["on_start"](SetupSelection(AIProviderKind.CODEX_ACCOUNT.value, False))
            self._poll_once()
            callbacks["on_stop"]()
            self._poll_once()
            if not manual_refresh and not running_legacy:
                assert accounts[0].snapshot().login_pending
            if disconnect_after_stop:
                callbacks["on_codex_disconnect"]()
                assert self.setup.selected_provider() == AIProviderKind.NOT_CONFIGURED.value
                self._poll_once()
                assert self.setup.images[-1].getpixel((0, 0)) == (0, 0, 0)
                callbacks["on_start"](self.setup.selection)
                self._poll_once()
                callbacks["on_stop"]()
                self._poll_once()
            self.setup.hide()
            if setup_visible_for_notice:
                self.setup.show()
            updaters[0].state = "available"
            self._poll_once()
            assert not updaters[0].delivered
            self._poll_once()
            assert events.count(("update_notice", "0.2.0")) == 1
            assert not updaters[0].delivered
            clock[0] += 31.0
            self._poll_once()
            assert updaters[0].delivered
            if not setup_visible_for_notice:
                self.setup.show()
            callbacks["on_update_apply"]()
            self._poll_once()
            if not helper_success:
                assert "window_quit" not in events
                callbacks["on_exit"]()

        def _poll_once(self):
            callback = next(callback for delay, callback in self.scheduled if delay in (50, 100))
            self.scheduled.clear()
            callback()

        def quit(self):
            events.append("window_quit")

    class Setup:
        def __init__(self, **kwargs):
            self.callbacks = kwargs
            self.provider = kwargs["provider"]
            self.window = Window(self)
            self.visible = False
            self.closed = False
            self.images = []
            self.accounts = []

        def __getattr__(self, name):
            if name.startswith(("complete_", "set_", "update_", "clear_")):
                def invoke(*args, **kwargs):
                    if name == "update_image":
                        self.images.append(args[0])
                        events.append("image")
                return invoke
            raise AttributeError(name)

        def show(self):
            self.visible = True

        def hide(self):
            self.visible = False

        def close(self):
            self.closed = True

        def selected_provider(self):
            return self.provider

        @property
        def selection(self):
            return SetupSelection(self.provider, False)

        def set_provider(self, provider):
            self.provider = provider

    class Overlay:
        visible = False
        closed = False

        def __init__(self, *_args, **_kwargs):
            pass

        def hide(self, **_kwargs):
            pass

        def destroy(self):
            self.closed = True

    class Tray:
        def __init__(self, *_args, **_kwargs):
            pass

        def notify_update(self, version):
            events.append(("update_notice", version))
            return events.count(("update_notice", version)) > 1

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    class Power:
        def __init__(self, *_args, **_kwargs):
            pass

        def install(self):
            pass

        def close(self):
            pass

    class Updater:
        def __init__(self, **_kwargs):
            events.append("updater_create")
            updaters.append(self)
            self.state = "idle"
            self.delivered = False

        def check(self, **_kwargs):
            return True

        def snapshot(self):
            version = "0.2.0" if self.state != "idle" else None
            return UpdateSnapshot(self.state, version, None, "", object() if self.state == "ready" else None,
                                  self.state == "available" and not self.delivered)

        def mark_notification_delivered(self, version):
            assert version == "0.2.0"
            self.delivered = True
            events.append(("notice_delivered", version))
            return True

        def prepare(self):
            events.append("update_prepare")
            self.state = "ready"
            return True

        def dismiss(self):
            pass

        def close(self):
            events.append("updater_close")

    monkeypatch.setattr(app, "acquire_single_instance", lambda: Guard())
    monkeypatch.setattr(app, "CodexAccountService", Account, raising=False)
    monkeypatch.setattr(app, "UpdateService", Updater, raising=False)
    monkeypatch.setattr(app, "DesktopSession", Session)
    monkeypatch.setattr(app, "SerialTaskWorker", Worker)
    monkeypatch.setattr(app, "SetupWindow", Setup)
    monkeypatch.setattr(app, "OverlayWindow", Overlay)
    monkeypatch.setattr(app, "TrayController", Tray)
    monkeypatch.setattr(app, "WindowsPowerEventHook", Power)
    monkeypatch.setattr(app, "_read_autostart_state", lambda _path: ("", False, False, False))
    monkeypatch.setattr(app, "_persist_latest_overlay_on_shutdown", lambda config, _path: config)
    class Renderer:
        def render(self, snapshot):
            rendered_ai.append(snapshot.ai)
            return Image.new("RGB", (480, 320))

    monkeypatch.setattr(app, "_renderer_for_rotation", lambda _rotation: Renderer())
    monkeypatch.setattr(app, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(app.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(app, "acknowledge_update_startup", lambda **_kwargs: actions.append("ack") or events.append("ack") or True)
    monkeypatch.setattr(app, "launch_update_helper", lambda *_args, **_kwargs: events.append("helper_launch") or helper_success)
    config = AppConfig()
    config.ai.provider = AIProviderKind.CODEX_LOCAL.value
    assert app.run_desktop(config, enable_serial=False, config_path=tmp_path / "settings.json") == 0
    assert events.count("account_create") == 1
    assert events.count("account_close") == 1
    assert "login" in events
    assert ("refresh" in events) is (manual_refresh or running_legacy)
    assert ("polling", True) in events and ("polling", False) in events
    expected_provider = AIProviderKind.NOT_CONFIGURED.value if disconnect_after_stop else AIProviderKind.CODEX_ACCOUNT.value
    assert load_config(tmp_path / "settings.json").ai.provider == expected_provider
    if disconnect_after_stop:
        assert rendered_ai[-1].provider is AIProviderKind.NOT_CONFIGURED
        assert events.count(("polling", True)) == 1
    assert any(ai.provider is AIProviderKind.CODEX_ACCOUNT and ai.primary_value == ("85%" if manual_refresh or running_legacy else "LOGIN") for ai in rendered_ai)
    assert events.count("helper_launch") == 1
    assert events.count(("update_notice", "0.2.0")) == 2
    assert events.count(("notice_delivered", "0.2.0")) == 1
    assert events.count("window_quit") == 1
    assert actions == ["ack"]
    assert "image" in events
    assert events.index("image") < events.index("ack")


def test_autostart_action_reports_failed_readback_without_claiming_success(
    monkeypatch,
) -> None:
    expected = r'"C:\Apps\AI-Mini-Monitor.exe" --minimized'
    monkeypatch.setattr(app.autostart, "enable", lambda *, command: command)
    monkeypatch.setattr(
        app.autostart,
        "read_state",
        lambda command: app.autostart.AutostartState(command, None),
    )

    result = app._change_autostart(True, expected)
    assert not result.ok
    assert "확인 실패" in result.title


@pytest.mark.parametrize("enabled", [False, True])
def test_autostart_write_success_then_read_error_is_uncertain(
    monkeypatch,
    enabled: bool,
) -> None:
    expected = r'"C:\Apps\AI-Mini-Monitor.exe" --minimized'
    configured: dict[str, str] = (
        {"command": expected} if not enabled else {}
    )

    def enable(*, command: str) -> str:
        configured["command"] = command
        return command

    def disable() -> bool:
        return configured.pop("command", None) is not None

    def fail_read(_command):
        raise OSError("verification unavailable")

    monkeypatch.setattr(app.autostart, "enable", enable)
    monkeypatch.setattr(app.autostart, "disable", disable)
    monkeypatch.setattr(app.autostart, "read_state", fail_read)

    result = app._change_autostart(enabled, expected)

    assert not result.ok
    assert result.state_uncertain
    assert ("command" in configured) is enabled


def test_recovery_discovery_is_worker_only_and_dialog_is_presented_on_tk_thread(monkeypatch, tmp_path) -> None:
    main_ident = threading.get_ident()
    scanned_on: list[int] = []
    shown_on: list[int] = []
    install = tmp_path / "Mini-Monitor"
    install.mkdir()
    notice = RecoveryNotice("needs_manual_recovery_alive", tmp_path / "journal.json", tmp_path / "backup")

    def discover(_install):
        scanned_on.append(threading.get_ident())
        assert _install == install
        return notice

    monkeypatch.setattr(app, "discover_update_recovery", discover, raising=False)
    monkeypatch.setattr(
        app,
        "messagebox",
        SimpleNamespace(showwarning=lambda _title, _message, **kwargs:
                        shown_on.append(threading.get_ident()) or kwargs["parent"]),
        raising=False,
    )

    class Setup:
        visible = False
        window = object()

        def show(self):
            self.visible = True

    setup = Setup()
    worker = SerialTaskWorker()
    try:
        assert app._queue_update_recovery(worker, install_root=install, acknowledged=False, frozen=True)
        deadline = time.monotonic() + 2
        results = []
        while not results and time.monotonic() < deadline:
            results = worker.poll()
            time.sleep(0.01)
        assert len(results) == 1 and results[0].kind == "update_recovery"
        assert results[0].value is notice
        app._present_update_recovery(results[0].value, setup)
        assert scanned_on and scanned_on[0] != main_ident
        assert shown_on == [main_ident]
        assert setup.visible
        assert not app._queue_update_recovery(worker, install_root=install, acknowledged=True, frozen=True)
        assert not app._queue_update_recovery(worker, install_root=install, acknowledged=False, frozen=False)
        assert len(scanned_on) == 1
    finally:
        worker.close()
