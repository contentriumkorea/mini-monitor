# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone
import sys
import tkinter as tk
from tkinter import messagebox
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_mini_monitor.models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    ConnectionStatus,
    SyncStatus,
)
from ai_mini_monitor.ui import setup as setup_ui_module
from ai_mini_monitor.ui.setup import ActionResult, SetupSelection, SetupWindow
from ai_mini_monitor.ui.setup import OverlaySettings


@pytest.fixture(scope="module")
def _shared_tk_interpreter():
    """Use one Tcl interpreter; repeated Tk roots are flaky on Windows 3.13."""

    host = tk.Tk(className="AIMiniMonitorTestHost")
    host.withdraw()
    try:
        yield host
    finally:
        host.destroy()


@pytest.fixture(autouse=True)
def _setup_windows_are_toplevels(monkeypatch, _shared_tk_interpreter) -> None:
    def create_window(*_args, **kwargs):
        class_name = kwargs.pop("className", "AIMiniMonitorSetupTest")
        return tk.Toplevel(_shared_tk_interpreter, class_=class_name, **kwargs)

    monkeypatch.setattr(setup_ui_module.tk, "Tk", create_window)


def make_window(**overrides) -> SetupWindow:
    callbacks = {
        "on_detect_device": lambda: ActionResult(True, "ONLINE · COM3", "verified"),
        "on_check_usage": lambda _selection: ActionResult(True, "CODEX · 12%", "7D 34%"),
        "on_start": lambda _selection: ActionResult(True, "모니터 시작됨", "COM3"),
        "on_stop": lambda: ActionResult(True, "모니터 중지됨"),
        "on_reconnect": lambda: ActionResult(True, "다시 연결 요청됨"),
        "on_brightness_change": lambda value: ActionResult(
            True,
            f"화면 밝기 {value}%",
        ),
        "on_exit": lambda: None,
        "on_autostart_change": lambda enabled: ActionResult(
            True,
            "자동 실행 켜짐" if enabled else "자동 실행 꺼짐",
        ),
    }
    callbacks.update(overrides)
    return SetupWindow(
        provider=AIProviderKind.CODEX_LOCAL.value,
        codex_local_consent=False,
        **callbacks,
    )


def test_codex_account_actions_do_not_start_serial_and_updates_keep_fixed_banner() -> None:
    called: list[str] = []
    window = make_window(
        on_codex_login=lambda: called.append("login") or ActionResult(True, "로그인 대기"),
        on_codex_cancel=lambda: called.append("cancel") or ActionResult(True, "취소됨"),
        on_codex_logout=lambda: called.append("logout") or ActionResult(True, "로그아웃됨"),
        on_codex_disconnect=lambda: called.append("disconnect") or ActionResult(True, "연결 해제됨"),
        on_codex_install_guide=lambda: called.append("guide") or ActionResult(True, "안내 열림"),
        on_update_check=lambda: called.append("check") or ActionResult(True, "확인 중"),
        on_update_apply=lambda: called.append("apply") or ActionResult(True, "적용 준비"),
        on_update_dismiss=lambda: called.append("dismiss") or ActionResult(True, "나중에"),
        on_update_open_release=lambda: called.append("release") or ActionResult(True, "릴리스 열림"),
        on_start=lambda _selection: called.append("serial") or ActionResult(True, "시작됨"),
    )
    try:
        account = SimpleNamespace(state="signed_out", email=None, plan_type=None, login_pending=False, windows=(), updated_at=None, error_detail=None)
        window.update_codex_account(account)
        window._codex_login_button.invoke()
        account = SimpleNamespace(state="login_pending", email=None, plan_type=None, login_pending=True, windows=(), updated_at=None, error_detail=None)
        window.update_codex_account(account)
        window._codex_cancel_button.invoke()
        account = SimpleNamespace(state="ready", email="demo@example.invalid", plan_type="plus", login_pending=False, windows=(), updated_at=None, error_detail=None)
        window.update_codex_account(account)
        window._codex_disconnect_button.invoke()
        window._codex_guide_button.invoke()
        banner_height = window._update_banner.winfo_reqheight()
        window.update_update_status(SimpleNamespace(state="available", version="2.0.0", message="업데이트가 있습니다", prepared=None, release_url="https://example.invalid"))
        window._update_check_button.invoke()
        window._update_apply_button.invoke()
        window._update_dismiss_button.invoke()
        window.update_update_status(SimpleNamespace(state="manual_required", version="2.0.0", message="수동 설치 필요", prepared=None, release_url="https://example.invalid"))
        window._update_release_button.invoke()
        assert window._update_banner.winfo_reqheight() == banner_height
        window.show()
        window.window.update()
        assert window._update_dismiss_button.winfo_y() + window._update_dismiss_button.winfo_height() <= window._update_banner.winfo_height()
        assert window._update_apply_button.winfo_x() + window._update_apply_button.winfo_width() <= window._update_banner.winfo_width()
        assert called == ["login", "cancel", "disconnect", "guide", "check", "apply", "dismiss", "release"]
        assert "demo@example.invalid" in window._codex_identity.cget("text")
    finally:
        window.close()


def test_codex_logout_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    window = make_window(on_codex_logout=lambda: called.append("logout") or ActionResult(True, "로그아웃됨"))
    try:
        window.update_codex_account(SimpleNamespace(state="ready", email="demo@example.invalid", plan_type="plus", login_pending=False, windows=(), updated_at=None, error_detail=None))
        monkeypatch.setattr(messagebox, "askyesno", lambda *_args, **_kwargs: False)
        window._codex_logout_button.invoke()
        assert called == []
        monkeypatch.setattr(messagebox, "askyesno", lambda *_args, **_kwargs: True)
        window._codex_logout_button.invoke()
        assert called == ["logout"]
    finally:
        window.close()


def test_codex_refresh_is_independent_of_legacy_selection_and_serial_start() -> None:
    selected: list[str] = []
    window = make_window(
        on_check_usage=lambda selection: selected.append(selection.provider) or ActionResult(True, "갱신 요청"),
        on_start=lambda _selection: selected.append("serial") or ActionResult(True, "시작됨"),
    )
    try:
        window._provider.set(AIProviderKind.CODEX_LOCAL.value)
        window._codex_refresh_button.invoke()
        assert selected == [AIProviderKind.CODEX_ACCOUNT.value]
        assert "disabled" not in window._start_button.state()
    finally:
        window.close()


def test_public_provider_switch_accepts_official_account_and_rejects_unknown() -> None:
    window = make_window()
    try:
        window.set_provider(AIProviderKind.CODEX_ACCOUNT.value)
        assert window.selected_provider == AIProviderKind.CODEX_ACCOUNT.value
        assert window.selection.provider == AIProviderKind.CODEX_ACCOUNT.value
        with pytest.raises(ValueError, match="provider"):
            window.set_provider("invalid-provider")
        assert window.selected_provider == AIProviderKind.CODEX_ACCOUNT.value
    finally:
        window.close()


@pytest.mark.parametrize("scaling", [1.66625, 2.00075])
def test_account_and_update_controls_fit_short_negative_monitor_at_high_dpi(
    scaling: float, monkeypatch: pytest.MonkeyPatch, _shared_tk_interpreter,
) -> None:
    interpreter = _shared_tk_interpreter.tk
    original_scaling = float(interpreter.call("tk", "scaling"))
    interpreter.call("tk", "scaling", scaling)
    monkeypatch.setattr(setup_ui_module, "_window_bounds", lambda _window: setup_ui_module.WindowBounds(-1600, 0, -576, 720, 16, 39))
    window = make_window(on_codex_login=lambda: ActionResult(True, "대기"), on_update_check=lambda: ActionResult(True, "확인"))
    try:
        window.show()
        window.window.update()
        assert -1600 <= window.window.winfo_x()
        assert window.window.winfo_x() + window.window.winfo_width() + 16 <= -576
        for control in (window._codex_login_button, window._codex_cli_button, window._update_check_button, window._update_dismiss_button):
            control.focus_force()
            window.window.update()
            assert control.winfo_x() + control.winfo_width() <= control.master.winfo_width()
            assert control.winfo_rooty() >= window._left_canvas.winfo_rooty()
            assert control.winfo_rooty() + control.winfo_height() <= window._left_canvas.winfo_rooty() + window._left_canvas.winfo_height()
    finally:
        window.close()
        interpreter.call("tk", "scaling", original_scaling)


@pytest.mark.parametrize("size", [(480, 320), (320, 480)])
def test_preview_fits_right_pane_on_short_high_dpi_monitor(
    size: tuple[int, int], monkeypatch: pytest.MonkeyPatch, _shared_tk_interpreter,
) -> None:
    interpreter = _shared_tk_interpreter.tk
    original_scaling = float(interpreter.call("tk", "scaling"))
    interpreter.call("tk", "scaling", 2.0)
    monkeypatch.setattr(
        setup_ui_module,
        "_window_bounds",
        lambda _window: setup_ui_module.WindowBounds(0, 0, 1024, 720, 16, 39),
    )
    window = make_window()
    try:
        window.set_provider(AIProviderKind.CODEX_ACCOUNT.value)
        window.update_codex_account(SimpleNamespace(
            state="delayed",
            email="synthetic.long.account.name@example.invalid",
            plan_type="ChatGPT Plus synthetic",
            login_pending=False,
            windows=(),
            updated_at=None,
            error_detail="long synthetic status",
        ))
        fixed_banner_height = window._update_banner.winfo_reqheight()
        for message in (
            "합성 수동 설치 안내: 아주 긴 설명이 들어와도 배너 높이와 하단 조작 위치는 그대로 유지되어야 합니다.",
            "W" * 70,
            "가" * 70,
        ):
            window.update_update_status(SimpleNamespace(
                state="manual_required",
                version="2026.10.4",
                message=message,
                prepared=None,
                release_url="https://example.invalid/synthetic",
            ))
            window.window.update_idletasks()
            assert window._update_banner.winfo_reqheight() == fixed_banner_height
            for control in (window._update_release_button, window._update_dismiss_button):
                assert control.winfo_y() + control.winfo_height() <= window._update_banner.winfo_height()
                assert control.winfo_x() + control.winfo_width() <= window._update_banner.winfo_width()
        window.update_image(Image.new("RGB", size, "black"))
        window.show()
        window.window.update()
        right = window._preview_detail.master
        preview_border = window._preview_label.master
        assert preview_border.winfo_x() >= 0
        assert preview_border.winfo_x() + preview_border.winfo_width() <= right.winfo_width()
        assert (
            preview_border.winfo_rootx() + preview_border.winfo_width()
            <= window.window.winfo_rootx() + window.window.winfo_width() - 24
        )
        assert window._photo.width() <= 480
        assert window._photo.width() <= preview_border.winfo_width() - 2
        assert window._photo.height() <= 320
        assert window._photo.width() / window._photo.height() == pytest.approx(size[0] / size[1], rel=0.01)
        assert window._right_scrollbar_visible
        window._right_canvas.yview_moveto(1.0)
        window.window.update()
        assert window._reconnect_button.winfo_rooty() >= window._right_canvas.winfo_rooty()
        assert (
            window._reconnect_button.winfo_rooty() + window._reconnect_button.winfo_height()
            <= window._right_canvas.winfo_rooty() + window._right_canvas.winfo_height()
        )
        previous_photo = window._photo
        window.update_image(Image.new("RGB", size, "white"))
        assert window._photo is not previous_photo
    finally:
        window.close()
        interpreter.call("tk", "scaling", original_scaling)


def test_overlay_options_are_modal_live_and_keep_physical_monitor_controls_separate() -> None:
    submitted: list[OverlaySettings] = []
    resets: list[bool] = []

    def apply_overlay(settings: OverlaySettings) -> ActionResult:
        submitted.append(settings)
        return ActionResult(True, "상태창 설정됨", "즉시 저장됨")

    window = make_window(
        overlay_enabled=False,
        overlay_opacity=0.85,
        overlay_scale_percent=100,
        on_overlay_change=apply_overlay,
        on_overlay_reset_position=lambda: (
            resets.append(True) or ActionResult(True, "위치 초기화됨")
        ),
    )
    try:
        dialog = window._overlay_dialog
        assert dialog is not None
        assert dialog.state() == "withdrawn"
        assert "불투명도" in str(
            window._overlay_opacity_value.master.grid_slaves(row=3, column=0)[0].cget("text")
        )
        opacity_copy = str(
            window._overlay_opacity_value.master.grid_slaves(row=3, column=0)[0].cget("text")
        )
        assert "카드 불투명도" in opacity_copy
        assert "전체 격자 배경은 표시하지 않으며" in opacity_copy
        assert "0~100%" in opacity_copy
        assert "글자·숫자·게이지·그래프는 선명하게 유지" in opacity_copy

        window._show_overlay_options()
        window.window.update_idletasks()
        assert dialog.grab_current() is dialog
        assert bool(window._overlay_toggle.cget("takefocus"))

        window._overlay_toggle.invoke()
        assert submitted[-1] == OverlaySettings(True, 0.85, 100)
        window._overlay_scale_changed("150")
        window._overlay_opacity_changed("60")
        window._cancel_overlay_schedule()
        window._commit_overlay_settings()
        assert submitted[-1] == OverlaySettings(True, 0.60, 150)

        window._reset_overlay_position()
        assert resets == [True]
        window.set_running(True)
        assert "disabled" not in window._overlay_options_button.state()
        assert "disabled" not in window._overlay_toggle.state()
    finally:
        window.close()


def test_main_pc_status_button_immediately_tracks_committed_visibility() -> None:
    submitted: list[OverlaySettings] = []

    def apply_overlay(settings: OverlaySettings) -> ActionResult:
        submitted.append(settings)
        return ActionResult(
            True,
            "상태창 표시" if settings.enabled else "상태창 숨김",
        )

    window = make_window(
        overlay_enabled=False,
        overlay_opacity=0.85,
        overlay_scale_percent=100,
        on_overlay_change=apply_overlay,
    )
    try:
        button = window._overlay_quick_button
        assert button.cget("text") == "PC 상태창 켜기"
        assert bool(button.cget("takefocus"))

        button.invoke()
        assert submitted[-1] == OverlaySettings(True, 0.85, 100)
        assert window.overlay_settings.enabled is True
        assert button.cget("text") == "PC 상태창 끄기"

        button.invoke()
        assert submitted[-1] == OverlaySettings(False, 0.85, 100)
        assert window.overlay_settings.enabled is False
        assert button.cget("text") == "PC 상태창 켜기"
    finally:
        window.close()


def test_main_pc_status_button_restores_label_and_shows_recovery_on_failure() -> None:
    window = make_window(
        overlay_enabled=False,
        on_overlay_change=lambda _settings: ActionResult(
            False,
            "상태창 설정 실패",
            "저장 공간을 확인할 수 없습니다",
        ),
    )
    try:
        window._overlay_quick_button.invoke()

        assert window.overlay_settings.enabled is False
        assert window._overlay_quick_button.cget("text") == "PC 상태창 켜기"
        assert window._overlay_quick_status.winfo_manager() == "grid"
        assert "다시 시도" in window._overlay_quick_status.cget("text")
    finally:
        window.close()


def test_uncertain_pc_status_failure_uses_observed_hidden_state() -> None:
    observed = OverlaySettings(False, 0.0, 150)
    window = make_window(
        overlay_enabled=True,
        on_overlay_change=lambda _settings: ActionResult(
            False,
            "상태창 상태 확인 필요",
            "실제 상태창은 숨겨졌습니다",
            state_uncertain=True,
            overlay_settings=observed,
        ),
    )
    try:
        assert window._overlay_quick_button.cget("text") == "PC 상태창 끄기"
        window._overlay_quick_button.invoke()

        assert window.overlay_settings == observed
        assert window._overlay_quick_button.cget("text") == "PC 상태창 켜기"
    finally:
        window.close()


def test_automatic_overlay_failure_uses_reserved_actionable_feedback() -> None:
    window = make_window(overlay_enabled=True)
    try:
        window.show_overlay_recovery()
        window.window.update_idletasks()

        assert "그래픽 오류" in window._overlay_quick_status.cget("text")
        assert "PC 상태창 버튼" in window._overlay_quick_status.cget("text")
        window.clear_overlay_recovery()
        assert window._overlay_quick_status.cget("text") == " "
    finally:
        window.close()


def test_overlay_background_opacity_exact_zero_is_selectable_and_applied() -> None:
    submitted: list[OverlaySettings] = []
    window = make_window(
        overlay_opacity=0.85,
        on_overlay_change=lambda settings: (
            submitted.append(settings)
            or ActionResult(True, "카드 불투명도 저장됨")
        ),
    )
    try:
        assert float(window._overlay_opacity_scale.cget("from")) == 0.0
        window._overlay_opacity_changed("0")
        window._cancel_overlay_schedule()
        window._commit_overlay_settings()

        assert submitted[-1].opacity == 0.0
        assert window.overlay_settings.opacity == 0.0
        assert window._overlay_opacity_value.cget("text") == "0%"
    finally:
        window.close()


def test_overlay_dialog_clamps_to_parent_negative_monitor_work_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounds = setup_ui_module.WindowBounds(
        -1600,
        40,
        -600,
        740,
        16,
        48,
    )
    monkeypatch.setattr(setup_ui_module, "_window_bounds", lambda _window: bounds)
    placements: list[tuple[object, int, int, int, int]] = []

    def record_geometry(window, width, height, x, y) -> None:
        placements.append((window, width, height, x, y))

    monkeypatch.setattr(setup_ui_module, "_set_window_geometry", record_geometry)
    window = make_window()
    try:
        window._show_overlay_options()
        dialog = window._overlay_dialog
        assert dialog is not None
        target, width, height, x, y = placements[-1]
        assert target is dialog
        assert x >= bounds.left
        assert y >= bounds.top
        assert x + width + bounds.frame_width <= bounds.right
        assert y + height + bounds.frame_height <= bounds.bottom
    finally:
        window.close()


def test_preview_action_buttons_wrap_without_horizontal_overflow_at_high_dpi(
    monkeypatch: pytest.MonkeyPatch,
    _shared_tk_interpreter,
) -> None:
    interpreter = _shared_tk_interpreter.tk
    original_scaling = float(interpreter.call("tk", "scaling"))
    interpreter.call("tk", "scaling", 2.00075)
    monkeypatch.setattr(
        setup_ui_module,
        "_window_bounds",
        lambda _window: setup_ui_module.WindowBounds(0, 0, 960, 640, 16, 48),
    )
    window = make_window(
        on_overlay_change=lambda _settings: ActionResult(
            False,
            "상태창 설정 실패",
            "그래픽 출력을 적용하지 못했습니다",
        )
    )
    try:
        window.show()
        window.window.update_idletasks()
        actions = window._overlay_options_button.master
        initial_positions = tuple(
            (button.winfo_y(), button.winfo_height())
            for button in (
                window._overlay_quick_button,
                window._overlay_options_button,
                window._reconnect_button,
            )
        )
        window._overlay_quick_button.invoke()
        window.window.update_idletasks()
        assert window._overlay_quick_button.grid_info()["row"] == 0
        assert window._overlay_options_button.grid_info()["row"] == 2
        assert window._reconnect_button.grid_info()["row"] == 3
        assert actions.winfo_reqwidth() <= actions.winfo_width()
        buttons = (
            window._overlay_quick_button,
            window._overlay_options_button,
            window._reconnect_button,
        )
        assert tuple(
            (button.winfo_y(), button.winfo_height()) for button in buttons
        ) == initial_positions
        for button in buttons:
            assert button.winfo_x() + button.winfo_width() <= actions.winfo_width()
            visible_top = button.winfo_rooty()
            visible_bottom = visible_top + button.winfo_height()
            ancestor = button.master
            while ancestor is not None:
                visible_top = max(visible_top, ancestor.winfo_rooty())
                visible_bottom = min(
                    visible_bottom,
                    ancestor.winfo_rooty() + ancestor.winfo_height(),
                )
                if ancestor is window.window:
                    break
                ancestor = ancestor.master
            assert max(0, visible_bottom - visible_top) == button.winfo_height()
    finally:
        window.close()
        interpreter.call("tk", "scaling", original_scaling)


def test_setup_constructor_destroys_partial_root_when_build_fails(
    monkeypatch: pytest.MonkeyPatch,
    _shared_tk_interpreter,
) -> None:
    before = set(_shared_tk_interpreter.winfo_children())
    monkeypatch.setattr(
        SetupWindow,
        "_build",
        lambda _self: (_ for _ in ()).throw(RuntimeError("synthetic build")),
    )

    with pytest.raises(RuntimeError, match="synthetic build"):
        make_window()

    _shared_tk_interpreter.update_idletasks()
    assert set(_shared_tk_interpreter.winfo_children()) == before


def test_overlay_callback_failure_restores_last_committed_controls() -> None:
    window = make_window(
        overlay_enabled=False,
        overlay_opacity=0.85,
        overlay_scale_percent=100,
        on_overlay_change=lambda _settings: ActionResult(
            False, "상태창 설정 실패", "저장 실패"
        ),
    )
    try:
        window._overlay_enabled.set(True)
        window._overlay_scale_percent.set(175)
        window._overlay_opacity_percent.set(55)
        window._commit_overlay_settings()

        assert window.overlay_settings == OverlaySettings(False, 0.85, 100)
        assert window._overlay_status.cget("text") == "저장 실패"
    finally:
        window.close()


def test_setup_window_has_separate_controls_and_native_preview() -> None:
    window = make_window()
    try:
        assert not window.visible
        assert window.window.title() == "Mini Monitor 설정"
        assert window.selection == SetupSelection(AIProviderKind.CODEX_LOCAL.value, False, "")
        window.update_image(Image.new("RGB", (480, 320), "black"))
        with pytest.raises(ValueError, match="480x320"):
            window.update_image(Image.new("RGB", (320, 240), "black"))
        assert window._start_button.cget("text") == "모니터 시작"
        assert window._stop_button.cget("text") == "모니터 중지"
        assert window._overlay_quick_button.cget("text") == "PC 상태창 켜기"
    finally:
        window.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows work-area API")
def test_real_windows_work_area_and_frame_metrics_are_available() -> None:
    window = make_window()
    try:
        window.window.update_idletasks()
        bounds = setup_ui_module._window_bounds(window.window)
        assert bounds.right > bounds.left
        assert bounds.bottom > bounds.top
        assert bounds.frame_width >= 0
        assert bounds.frame_height >= 0
    finally:
        window.close()


@pytest.mark.parametrize("monitor_info_succeeds", [False, True])
def test_windows_work_area_fallback_keeps_taskbar_and_frame_clearance(
    monkeypatch: pytest.MonkeyPatch,
    monitor_info_succeeds: bool,
) -> None:
    class FakeFunction:
        def __init__(self, callback):
            self._callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self._callback(*args)

    class FakeUser32:
        def __init__(self) -> None:
            metrics = {4: 23, 32: 8, 33: 8, 92: 4}
            self.GetSystemMetrics = FakeFunction(
                lambda metric: metrics.get(int(metric), 0)
            )

            def system_parameters_info(_action, _param, pointer, _flags):
                rect = pointer._obj
                rect.left, rect.top, rect.right, rect.bottom = 4, 8, 1004, 688
                return 1

            self.SystemParametersInfoW = FakeFunction(system_parameters_info)
            self.GetAncestor = FakeFunction(lambda _hwnd, _flags: 1)
            self.MonitorFromWindow = FakeFunction(lambda _hwnd, _flags: 1)

            def get_monitor_info(_monitor, pointer):
                if not monitor_info_succeeds:
                    return 0
                work = pointer._obj.rcWork
                work.left, work.top, work.right, work.bottom = -1200, 20, -200, 700
                return 1

            self.GetMonitorInfoW = FakeFunction(get_monitor_info)
            self.GetWindowRect = FakeFunction(lambda _hwnd, _pointer: 0)
            self.GetClientRect = FakeFunction(lambda _hwnd, _pointer: 0)

    class FakeWindow:
        @staticmethod
        def winfo_screenwidth() -> int:
            return 1024

        @staticmethod
        def winfo_screenheight() -> int:
            return 720

        @staticmethod
        def winfo_id() -> int:
            return 42

    monkeypatch.setattr(setup_ui_module.sys, "platform", "win32")
    monkeypatch.setattr(
        setup_ui_module.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: FakeUser32(),
        raising=False,
    )

    bounds = setup_ui_module._window_bounds(FakeWindow())

    if monitor_info_succeeds:
        assert (bounds.left, bounds.top, bounds.right, bounds.bottom) == (
            -1200,
            20,
            -200,
            700,
        )
    else:
        assert (bounds.left, bounds.top, bounds.right, bounds.bottom) == (
            4,
            8,
            1004,
            688,
        )
    assert bounds.frame_width == 24
    assert bounds.frame_height == 48


def test_absolute_window_position_uses_win32_for_negative_virtual_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int]] = []

    class FakeFunction:
        def __init__(self, callback):
            self._callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self._callback(*args)

    class FakeUser32:
        GetAncestor = FakeFunction(lambda _hwnd, _flags: 99)
        SetWindowPos = FakeFunction(
            lambda _hwnd, _after, x, y, _cx, _cy, flags: (
                calls.append((int(x), int(y), int(flags))) or 1
            )
        )

    class FakeWindow:
        geometries: list[str] = []

        def geometry(self, value: str) -> None:
            self.geometries.append(value)

        @staticmethod
        def update_idletasks() -> None:
            return None

        @staticmethod
        def winfo_id() -> int:
            return 42

    monkeypatch.setattr(setup_ui_module.sys, "platform", "win32")
    monkeypatch.setattr(
        setup_ui_module.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: FakeUser32(),
        raising=False,
    )
    window = FakeWindow()

    setup_ui_module._set_window_geometry(window, 480, 390, -1200, 40)

    assert window.geometries == ["480x390"]
    assert calls == [(-1200, 40, 0x0015)]


def test_orientation_controls_compose_four_modes_and_portrait_preview_is_fitted() -> None:
    changes: list[str] = []
    window = make_window(
        rotation="landscape_inverted",
        on_orientation_change=changes.append,
    )
    try:
        assert window.selected_rotation == "landscape_inverted"
        assert window.selection.rotation == "landscape_inverted"

        window._orientation_view.set("세로")
        assert window.selected_rotation == "portrait_inverted"
        window._orientation_inverted.set(False)
        assert window.selected_rotation == "portrait"
        assert changes[-2:] == ["portrait_inverted", "portrait"]

        window.update_image(Image.new("RGB", (320, 480), "black"))
        assert (window._photo.width(), window._photo.height()) == (213, 320)
        assert "320×480" in window._preview_detail.cget("text")

        window.set_running(True)
        assert "disabled" in window._orientation_combo.state()
        assert "disabled" in window._orientation_flip.state()
        assert "disabled" not in window._brightness_scale.state()
        window.set_running(False)
        assert "disabled" not in window._orientation_combo.state()
        assert "readonly" in window._orientation_combo.state()
        assert "disabled" not in window._orientation_flip.state()
        assert "disabled" not in window._brightness_scale.state()
    finally:
        window.close()


def test_brightness_scale_shows_current_percent_and_returns_strict_selection() -> None:
    applied: list[int] = []
    window = make_window(
        brightness=37,
        on_brightness_change=lambda value: (
            applied.append(value)
            or ActionResult(True, f"화면 밝기 {value}%")
        ),
    )
    try:
        assert window.selected_brightness == 37
        assert window.selection.brightness == 37
        assert window._brightness_value.cget("text") == "37%"
        assert window._brightness_scale.cget("from") == 1.0
        assert window._brightness_scale.cget("to") == 50.0

        window._brightness_scale.set(42.0)
        window.window.update_idletasks()
        window._cancel_brightness_schedule()
        window._commit_brightness()

        assert applied == [42]
        assert window.selection.brightness == 42
        assert window._brightness_value.cget("text") == "42%"
    finally:
        window.close()


@pytest.mark.parametrize(
    "brightness",
    [True, False, "25", 25.0, float("nan"), 0, 51],
)
def test_setup_rejects_noncanonical_brightness(brightness: object) -> None:
    with pytest.raises(ValueError, match="brightness"):
        make_window(brightness=brightness)


def test_brightness_busy_freezes_other_settings_and_stale_completion_never_jumps() -> None:
    requested: list[int] = []

    def request(value: int) -> ActionResult:
        requested.append(value)
        return ActionResult(True, f"{value}% 적용 중", pending=True)

    window = make_window(on_brightness_change=request)
    try:
        window._brightness_changed("31.0")
        window._cancel_brightness_schedule()
        window._commit_brightness()
        assert requested == [31]
        assert "disabled" in window._start_button.state()
        assert "disabled" in window._usage_button.state()
        assert "disabled" in window._orientation_combo.state()
        assert "disabled" not in window._brightness_scale.state()

        window._brightness_changed("44.0")
        window.complete_brightness(
            ActionResult(True, "31% 적용됨"),
            brightness=31,
        )
        assert window.selected_brightness == 44
        assert window._brightness_value.cget("text") == "44%"
        assert "disabled" in window._start_button.state()
        assert "disabled" in window._orientation_combo.state()

        window._cancel_brightness_schedule()
        window._commit_brightness()
        assert requested == [31, 44]
        window.complete_brightness(
            ActionResult(True, "44% 적용됨"),
            brightness=44,
        )
        assert "disabled" not in window._start_button.state()
        assert "disabled" not in window._usage_button.state()
        assert "disabled" not in window._orientation_combo.state()
        assert "disabled" not in window._brightness_scale.state()
    finally:
        window.close()


def test_failed_brightness_change_reverts_to_last_committed_value() -> None:
    window = make_window(
        brightness=25,
        on_brightness_change=lambda _value: ActionResult(
            False,
            "밝기 적용 실패",
        ),
    )
    try:
        window._brightness_changed("40.0")
        window._cancel_brightness_schedule()
        window._commit_brightness()
        assert window.selected_brightness == 25
        assert window._brightness_value.cget("text") == "25%"
        assert "disabled" not in window._start_button.state()
    finally:
        window.close()


def test_brightness_is_disabled_during_lifecycle_and_usage_busy_states() -> None:
    window = make_window(
        on_start=lambda _selection: ActionResult(True, "시작 중", pending=True),
        on_check_usage=lambda _selection: ActionResult(
            True,
            "확인 중",
            pending=True,
        ),
        on_stop=lambda: ActionResult(True, "중지 중", pending=True),
    )
    try:
        window._start()
        assert "disabled" in window._brightness_scale.state()
        window.complete_start(ActionResult(False, "시작 실패"), running=False)
        assert "disabled" not in window._brightness_scale.state()

        window._check_usage()
        assert "disabled" in window._brightness_scale.state()
        window.complete_usage(ActionResult(True, "확인 완료"))
        assert "disabled" not in window._brightness_scale.state()

        window.set_running(True)
        assert "disabled" not in window._brightness_scale.state()
        window._stop()
        assert "disabled" in window._brightness_scale.state()
        window.complete_stop(ActionResult(True, "중지됨"), running=False)
        assert "disabled" not in window._brightness_scale.state()
    finally:
        window.close()


def test_consent_and_orientation_use_checkmarks_with_native_widget_states() -> None:
    window = make_window()
    try:
        style = setup_ui_module.ttk.Style(window.window)
        layout = str(style.layout(window._checkbutton_style))
        assert window._checkmark_element in layout
        assert "Checkbutton.indicator" not in layout
        assert window._consent.cget("style") == window._checkbutton_style
        assert window._orientation_flip.cget("style") == window._checkbutton_style

        checked = window._checkmark_bitmaps["checked"]
        unchecked = window._checkmark_bitmaps["unchecked"]
        size = checked.height
        tick_point = (round(size * 0.43), round(size * 0.72))
        checked_pixel = checked.getpixel(tick_point)
        unchecked_pixel = unchecked.getpixel(tick_point)
        assert checked_pixel[1] > 180 and checked_pixel[2] > 180
        assert max(unchecked_pixel[:3]) < 80

        assert "selected" not in window._consent.state()
        assert "selected" not in window._orientation_flip.state()
        window._consent.invoke()
        window._orientation_flip.invoke()
        assert window._codex_consent.get() is True
        assert window._orientation_inverted.get() is True
        assert "selected" in window._consent.state()
        assert "selected" in window._orientation_flip.state()

        window.window.deiconify()
        window.window.update()
        window._consent.focus_force()
        window.window.update()
        window._consent.event_generate("<space>")
        window._orientation_flip.focus_force()
        window.window.update()
        window._orientation_flip.event_generate("<space>")
        assert window._codex_consent.get() is False
        assert window._orientation_inverted.get() is False
        assert "selected" not in window._consent.state()
        assert "selected" not in window._orientation_flip.state()

        window._consent.invoke()
        window._orientation_flip.invoke()

        window.set_running(True)
        assert "disabled" in window._consent.state()
        assert "disabled" in window._orientation_flip.state()
        window._consent.invoke()
        window._orientation_flip.invoke()
        assert window._codex_consent.get() is True
        assert window._orientation_inverted.get() is True
    finally:
        window.close()


def test_one_autostart_checkbutton_enables_and_disables_with_verified_feedback() -> None:
    requested: list[bool] = []

    def change(enabled: bool) -> ActionResult:
        requested.append(enabled)
        return ActionResult(True, "자동 실행 켜짐" if enabled else "자동 실행 꺼짐")

    window = make_window(on_autostart_change=change)
    try:
        assert window._autostart_enabled.get() is False
        assert window._autostart_status.cget("text") == "꺼짐"

        window._autostart_toggle.invoke()
        assert requested == [True]
        assert window._autostart_enabled.get() is True
        assert "selected" in window._autostart_toggle.state()
        assert window._autostart_status.cget("text") == "켜짐"

        window._autostart_toggle.invoke()
        assert requested == [True, False]
        assert window._autostart_enabled.get() is False
        assert "selected" not in window._autostart_toggle.state()
        assert window._autostart_status.cget("text") == "꺼짐"
    finally:
        window.close()


def test_failed_autostart_change_reverts_the_single_checkbox() -> None:
    window = make_window(
        autostart_needs_repair=True,
        on_autostart_change=lambda _enabled: ActionResult(
            False,
            "자동 실행 변경 실패",
        ),
    )
    try:
        assert window._autostart_status.cget("text") == "재설정 필요"
        window._autostart_toggle.invoke()
        assert window._autostart_enabled.get() is False
        assert "selected" not in window._autostart_toggle.state()
        assert window._autostart_status.cget("text") == "변경 실패"
    finally:
        window.close()


def test_uncertain_autostart_readback_keeps_requested_value_without_claiming_success() -> None:
    window = make_window(
        on_autostart_change=lambda _enabled: ActionResult(
            False,
            "자동 실행 상태 확인 필요",
            state_uncertain=True,
        ),
    )
    try:
        window._autostart_toggle.invoke()
        assert window._autostart_enabled.get() is True
        assert "selected" in window._autostart_toggle.state()
        assert "disabled" not in window._autostart_toggle.state()
        assert window._autostart_status.cget("text") == "확인 필요"
    finally:
        window.close()


@pytest.mark.parametrize(
    "callback",
    [
        lambda _enabled: (_ for _ in ()).throw(OSError("registry unavailable")),
        lambda _enabled: None,
    ],
)
def test_autostart_callback_failure_always_restores_checkbox(
    callback,
) -> None:
    window = make_window(on_autostart_change=callback)
    try:
        window._autostart_toggle.invoke()
        assert window._autostart_enabled.get() is False
        assert "selected" not in window._autostart_toggle.state()
        assert "disabled" not in window._autostart_toggle.state()
        assert window._autostart_status.cget("text") == "변경 실패"
    finally:
        window.close()


def test_unknown_autostart_state_is_not_presented_as_off() -> None:
    window = make_window(autostart_state_unknown=True)
    try:
        assert window._autostart_status.cget("text") == "확인 필요"
    finally:
        window.close()


def test_hidden_admin_key_is_never_forwarded_for_codex() -> None:
    captured: list[SetupSelection] = []

    def start(selection: SetupSelection) -> ActionResult:
        captured.append(selection)
        return ActionResult(True, "모니터 시작됨", "COM3")

    window = make_window(on_start=start)
    try:
        window.hide()
        window._codex_consent.set(True)
        window._admin_key.set("not-a-real-key")
        window._start_button.invoke()
        assert captured == [
            SetupSelection(
                provider=AIProviderKind.CODEX_LOCAL.value,
                codex_local_consent=True,
                openai_admin_key="",
            )
        ]
        assert window._admin_key.get() == ""
        assert window._running is True
    finally:
        window.close()


def test_runtime_status_is_presented_without_opening_serial() -> None:
    window = make_window(enable_serial=False)
    try:
        window.hide()
        ai = AIData(
            provider=AIProviderKind.CODEX_LOCAL,
            title="CODEX LIMITS",
            status=SyncStatus.OK,
            primary_value="12%",
            primary_label="5H LEFT",
            fields=(("7D LEFT", "34%"),),
            last_sync=datetime.now(timezone.utc),
        )
        window.update_runtime(
            ConnectionData(ConnectionStatus.DISCONNECTED, detail="PREVIEW ONLY"),
            ai,
            running=True,
        )
        assert window._run_status.cget("text") == "실행 중"
        assert "CODEX LIMITS" in window._usage_status.cget("text")
        assert "7D LEFT 34%" in window._usage_detail.cget("text")
        assert "disabled" in window._reconnect_button.state()
    finally:
        window.close()


def test_pending_start_does_not_claim_running_before_worker_completion() -> None:
    window = make_window(
        on_start=lambda _selection: ActionResult(
            True,
            "모니터 시작 중…",
            pending=True,
        )
    )
    try:
        window._codex_consent.set(True)
        window._start_button.invoke()
        assert window._running is False
        assert "disabled" in window._start_button.state()
        window.complete_start(ActionResult(True, "모니터 시작됨", "COM3"), running=True)
        assert window._running is True
        assert "disabled" not in window._stop_button.state()
    finally:
        window.close()


def test_port_in_use_start_failure_stays_retryable_and_is_not_generic_reconnecting() -> None:
    window = make_window()
    try:
        window._codex_consent.set(True)
        window.complete_start(
            ActionResult(
                False,
                "PORT IN USE · COM3",
                "UsbMonitor를 트레이까지 완전히 종료한 뒤 ‘모니터 시작’을 다시 누르세요.",
            ),
            running=False,
        )

        assert window._running is False
        assert window._device_title.cget("text") == "PORT IN USE · COM3"
        assert "트레이까지 완전히 종료" in window._device_detail.cget("text")
        assert window._run_status.cget("text") == "포트 사용 중"
        assert "disabled" not in window._start_button.state()
        assert "disabled" in window._reconnect_button.state()
    finally:
        window.close()


def test_runtime_port_in_use_exposes_one_reconnect_action() -> None:
    window = make_window()
    try:
        ai = AIData(
            provider=AIProviderKind.NOT_CONFIGURED,
            title="AI USAGE",
            status=SyncStatus.SETUP_REQUIRED,
            primary_value="SETUP",
            primary_label="REQUIRED",
        )
        window.update_runtime(
            ConnectionData(
                ConnectionStatus.DISCONNECTED,
                port="COM3",
                detail="PORT IN USE",
            ),
            ai,
            running=True,
        )

        assert window._device_title.cget("text") == "PORT IN USE · COM3"
        assert "UsbMonitor" in window._device_detail.cget("text")
        assert window._run_status.cget("text") == "연결 복구 필요"
        assert "disabled" not in window._reconnect_button.state()
        assert "disabled" in window._start_button.state()
    finally:
        window.close()


def test_pending_openai_start_clears_one_shot_key_immediately() -> None:
    captured: list[SetupSelection] = []

    def start(selection: SetupSelection) -> ActionResult:
        captured.append(selection)
        return ActionResult(True, "모니터 시작 중…", pending=True)

    window = make_window(on_start=start)
    try:
        window._provider.set(AIProviderKind.OPENAI_API.value)
        window._admin_key.set("synthetic-one-shot")
        window._start_button.invoke()
        assert captured[0].openai_admin_key == "synthetic-one-shot"
        assert window._admin_key.get() == ""
        window.complete_start(ActionResult(True, "모니터 시작됨", "COM3"), running=True)
        assert window._admin_key.get() == ""
    finally:
        window.close()


def test_incomplete_stop_blocks_both_lifecycle_buttons() -> None:
    window = make_window()
    try:
        window.set_running(True)
        window.complete_stop(
            ActionResult(False, "완전히 중지되지 않았습니다", "프로그램을 종료하세요."),
            running=False,
            blocked=True,
        )
        assert "disabled" in window._start_button.state()
        assert "disabled" in window._stop_button.state()
        assert "다시 실행" in window._run_status.cget("text")
    finally:
        window.close()


def test_admin_key_is_only_returned_for_openai_and_cleared_on_provider_change() -> None:
    window = make_window()
    try:
        window._provider.set(AIProviderKind.OPENAI_API.value)
        window._admin_key.set("one-shot-secret")
        assert window.selection.openai_admin_key == "one-shot-secret"
        window._provider.set(AIProviderKind.CODEX_LOCAL.value)
        assert window._admin_key.get() == ""
        assert window.selection.openai_admin_key == ""
    finally:
        window.close()


def test_openai_budget_and_polling_options_are_explicit_and_validated() -> None:
    invoked = False

    def start(_selection: SetupSelection) -> ActionResult:
        nonlocal invoked
        invoked = True
        return ActionResult(True, "started")

    window = make_window(
        on_start=start,
        usage_refresh_seconds=120,
        cost_refresh_seconds=900,
        daily_budget_usd=5.5,
        monthly_budget_usd=50.0,
    )
    try:
        window._provider.set(AIProviderKind.OPENAI_API.value)
        selection = window.selection
        assert selection.usage_refresh_seconds == 120
        assert selection.cost_refresh_seconds == 900
        assert selection.daily_budget_usd == 5.5
        assert selection.monthly_budget_usd == 50.0

        window._usage_refresh.set("59")
        window._start_button.invoke()
        assert not invoked
        assert window._running is False
        assert "60초" in window._device_detail.cget("text")

        window._provider.set(AIProviderKind.CODEX_LOCAL.value)
        assert window.selection.usage_refresh_seconds == 120
        assert window.selection.cost_refresh_seconds == 900
    finally:
        window.close()


def test_running_state_freezes_provider_consent_and_secret_settings() -> None:
    window = make_window()
    try:
        window._provider.set(AIProviderKind.OPENAI_API.value)
        window._show_openai_options()
        assert window._openai_dialog is not None
        assert window._openai_dialog.grab_current() is window._openai_dialog
        window.set_running(True)
        assert all("disabled" in button.state() for button in window._provider_buttons)
        assert "disabled" in window._consent.state()
        assert "disabled" in window._key_entry.state()
        assert "disabled" in window._openai_options_button.state()
        assert all("disabled" in entry.state() for entry in window._option_entries)

        window.set_running(False)
        assert all("disabled" not in button.state() for button in window._provider_buttons)
        assert "disabled" not in window._key_entry.state()
        assert all("disabled" not in entry.state() for entry in window._option_entries)
    finally:
        window.close()


def test_pending_usage_keeps_settings_locked_across_idle_state_updates() -> None:
    window = make_window(
        on_check_usage=lambda _selection: ActionResult(
            True,
            "사용량 확인 중…",
            pending=True,
        )
    )
    try:
        window._usage_button.invoke()
        window.set_running(False)
        assert all("disabled" in button.state() for button in window._provider_buttons)
        assert "disabled" in window._consent.state()

        window.complete_usage(ActionResult(True, "확인 완료"))
        assert all("disabled" not in button.state() for button in window._provider_buttons)
    finally:
        window.close()


@pytest.mark.parametrize("scaling", [1.333333, 1.66625, 2.00075])
def test_usage_feedback_never_reflows_controls_or_pushes_exit_outside_window(
    scaling: float,
    monkeypatch: pytest.MonkeyPatch,
    _shared_tk_interpreter,
) -> None:
    interpreter = _shared_tk_interpreter.tk
    original_scaling = float(interpreter.call("tk", "scaling"))
    interpreter.call("tk", "scaling", scaling)
    monkeypatch.setattr(
        setup_ui_module,
        "_window_bounds",
        lambda _window: setup_ui_module.WindowBounds(
            0,
            0,
            1024,
            720,
            16,
            39,
        ),
    )
    window = make_window(
        on_check_usage=lambda _selection: ActionResult(
            True,
            "사용량 확인 중…",
            "로컬 기록은 이 PC 밖으로 전송되지 않습니다.",
            pending=True,
        )
    )

    def geometry_snapshot() -> tuple[
        tuple[int, int],
        tuple[int, int, int, int],
        tuple[int, int, int, int],
        int,
        int,
    ]:
        window.window.update_idletasks()
        button_geometry = (
            window._usage_button.winfo_x(),
            window._usage_button.winfo_y(),
            window._usage_button.winfo_width(),
            window._usage_button.winfo_height(),
        )
        exit_bottom = (
            window._exit_button.winfo_rooty()
            - window.window.winfo_rooty()
            + window._exit_button.winfo_height()
        )
        footer_geometry = (
            window._controls.winfo_rootx(),
            window._controls.winfo_rooty(),
            window._controls.winfo_width(),
            window._controls.winfo_height(),
        )
        return (
            (window.window.winfo_reqwidth(), window.window.winfo_reqheight()),
            button_geometry,
            footer_geometry,
            exit_bottom,
            window.window.winfo_height(),
        )

    def visible_height(widget: tk.Widget) -> int:
        top = widget.winfo_rooty()
        bottom = top + widget.winfo_height()
        ancestor = widget.master
        while ancestor is not None:
            top = max(top, ancestor.winfo_rooty())
            bottom = min(bottom, ancestor.winfo_rooty() + ancestor.winfo_height())
            if ancestor is window.window:
                break
            ancestor = ancestor.master
        return max(0, bottom - top)

    def assert_feedback_fits_reserved_area() -> None:
        window.window.update_idletasks()
        feedback = window._usage_detail.master
        feedback_bottom = feedback.winfo_rooty() + feedback.winfo_height()
        assert (
            window._usage_status.winfo_rooty()
            + window._usage_status.winfo_reqheight()
            <= feedback_bottom
        )
        assert (
            window._usage_detail.winfo_rooty()
            + window._usage_detail.winfo_reqheight()
            <= feedback_bottom
        )

    try:
        expected_icon_size = max(
            16,
            min(28, round(16 * scaling / (96.0 / 72.0))),
        )
        assert window._checkmark_bitmaps["checked"].height == expected_icon_size
        window.show()
        window.window.update()
        initial = geometry_snapshot()
        assert_feedback_fits_reserved_area()
        assert window.window.winfo_width() <= 1008
        assert window.window.winfo_height() <= 681
        assert window.window.winfo_x() >= 0
        assert window.window.winfo_y() >= 0
        assert window.window.winfo_x() + window.window.winfo_width() + 16 <= 1024
        assert window.window.winfo_y() + window.window.winfo_height() + 39 <= 720
        assert window._left_scrollbar_visible
        assert window._left_canvas.yview()[1] < 1.0

        window._usage_button.invoke()
        pending = geometry_snapshot()
        assert_feedback_fits_reserved_area()

        window.complete_usage(
            ActionResult(
                True,
                "CODEX LIMITS · 82% 7D LEFT",
                "7D LEFT 82% · 7D RESET 5D 13H · 안전한 최소 주기 내 현재 값",
            )
        )
        complete = geometry_snapshot()
        assert_feedback_fits_reserved_area()

        assert pending[:3] == initial[:3]
        assert complete[:3] == initial[:3]
        assert initial[3] <= initial[4]
        assert pending[3] <= pending[4]
        assert complete[3] <= complete[4]
        for button in (
            window._start_button,
            window._stop_button,
            window._exit_button,
            window._autostart_toggle,
        ):
            assert button.winfo_height() == button.winfo_reqheight()
            assert visible_height(button) == button.winfo_height()

        footer_before_scroll = complete[2]
        window._left_canvas.yview_moveto(1.0)
        window.window.update()
        assert geometry_snapshot()[2] == footer_before_scroll
        assert (
            window._left_scroll_content.winfo_rooty()
            + window._left_scroll_content.winfo_height()
            <= window._left_canvas.winfo_rooty()
            + window._left_canvas.winfo_height()
            + 1
        )

        window._device_button.focus_force()
        window.window.update()
        top_view = window._left_canvas.yview()[0]
        assert visible_height(window._device_button) == window._device_button.winfo_height()

        window._usage_button.focus_force()
        window.window.update()
        usage_view = window._left_canvas.yview()[0]
        assert usage_view > top_view
        assert visible_height(window._usage_button) == window._usage_button.winfo_height()

        window._device_button.focus_force()
        window.window.update()
        returned_view = window._left_canvas.yview()[0]
        assert returned_view < usage_view
        assert visible_height(window._device_button) == window._device_button.winfo_height()

        window._device_button.event_generate("<MouseWheel>", delta=-120)
        window.window.update()
        assert window._left_canvas.yview()[0] > returned_view
    finally:
        window.close()
        interpreter.call("tk", "scaling", original_scaling)


def test_provider_radio_value_is_safe_during_partial_advanced_input() -> None:
    window = make_window()
    try:
        window._provider.set(AIProviderKind.OPENAI_API.value)
        window._usage_refresh.set("")
        assert window.selected_provider == AIProviderKind.OPENAI_API.value
        with pytest.raises(ValueError):
            _ = window.selection
    finally:
        window.close()
