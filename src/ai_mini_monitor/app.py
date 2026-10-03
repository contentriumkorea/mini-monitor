from __future__ import annotations

import copy
import ctypes
import logging
import math
import os
import queue
import signal
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import __version__
from . import autostart
from .ai.codex_account import CodexAccountService, CodexAccountSnapshot
from .ai.codex_usage import CodexUsageProvider, CodexUsageStatus, to_ai_data as codex_to_ai_data
from .config import (
    AppConfig,
    default_config_path,
    load_config,
    save_config,
    validate_brightness,
)
from .desktop_session import (
    DesktopSession,
    SerialTaskWorker,
    SessionResult,
    SessionState,
    WorkResult,
)
from .models import (
    AIData,
    AIProviderKind,
    ConnectionData,
    ConnectionStatus,
    DisplaySnapshot,
    SyncStatus,
)
from .orientation import orientation_spec
from .power_events import WindowsPowerEventHook
from .rendering.layout import layout_for_dimensions
from .rendering.renderer import DashboardRenderer
from .resources import user_data_dir
from .security.dpapi import DPAPISecretStore
from .single_instance import acquire_single_instance
from .state import not_configured_ai
from .transport.device import DeviceDetector
from .ui.overlay import OverlayState, OverlayWindow
from .ui.setup import (
    ActionResult,
    OverlaySettings,
    SetupSelection,
    SetupWindow,
)
from .ui.tray import TrayCommand, TrayController, create_command_queue
from .updater import (
    UpdateService,
    acknowledge_update_startup,
    cleanup_healthy_update_backup,
    launch_update_helper,
)
from .update_recovery import RecoveryNotice, discover_update_recovery
from .single_instance import DEFAULT_MUTEX_NAME


LOGGER = logging.getLogger(__name__)
_CONFIG_WRITE_LOCK = threading.RLock()
_UPDATE_NOTICE_RETRY_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class UsageWorkResult:
    action: ActionResult
    ai: AIData


@dataclass(frozen=True, slots=True)
class StartWorkResult:
    action: ActionResult
    config: AppConfig | None = None


@dataclass(frozen=True, slots=True)
class BrightnessWorkResult:
    action: ActionResult
    brightness: int
    config: AppConfig | None = None


@dataclass(frozen=True, slots=True)
class OverlayWorkResult:
    action: ActionResult
    config: AppConfig


def _queue_update_recovery(
    worker: SerialTaskWorker, *, install_root: Path, acknowledged: bool, frozen: bool,
) -> bool:
    if not frozen or acknowledged:
        return False
    return worker.submit("update_recovery", lambda: discover_update_recovery(install_root))


def _present_update_recovery(notice: RecoveryNotice, setup: SetupWindow) -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Tk recovery notice must be shown on the main thread")
    if not setup.visible:
        setup.show()
    messagebox.showwarning("Mini Monitor 업데이트 복구 확인", notice.message, parent=setup.window)


def _close_partial_desktop_services(
    tray: TrayController | None,
    session: DesktopSession | None,
    worker: SerialTaskWorker | None,
    account: CodexAccountService | None = None,
    updater: UpdateService | None = None,
) -> None:
    """Best-effort cleanup for failures before the main runtime ``try``."""

    if worker is not None:
        try:
            worker.begin_shutdown()
            worker.close(timeout=2.0)
        except Exception:
            LOGGER.exception("could not close partial desktop worker")
    if session is not None:
        try:
            session.begin_shutdown()
            session.stop(timeout=2.0)
        except Exception:
            LOGGER.exception("could not close partial desktop session")
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            LOGGER.exception("could not close partial tray service")
    if account is not None:
        try:
            account.close()
        except Exception:
            LOGGER.exception("could not close partial account service")
    if updater is not None:
        try:
            updater.close()
        except Exception:
            LOGGER.exception("could not close partial update service")


def run_desktop(
    config: AppConfig,
    *,
    minimized: bool = False,
    enable_serial: bool = True,
    config_path: Path | None = None,
    auto_exit_seconds: float | None = None,
) -> int:
    """Run the setup-first desktop shell.

    The setup window is the sole Tk root. Its embedded preview and optional
    always-on-top PC status window do not open COM until Monitor Start.
    """

    if auto_exit_seconds is not None and (
        not isinstance(auto_exit_seconds, (int, float))
        or not math.isfinite(auto_exit_seconds)
        or auto_exit_seconds <= 0
        or enable_serial
    ):
        raise ValueError("auto_exit_seconds requires no serial and a positive finite timeout")
    current_config = copy.deepcopy(config)
    current_config.validate()

    guard = (
        acquire_single_instance(DEFAULT_MUTEX_NAME + "-DesktopSmoke")
        if auto_exit_seconds is not None else acquire_single_instance()
    )
    if guard is None:
        ctypes.windll.user32.MessageBoxW(
            None,
            "Mini Monitor가 이미 알림 영역에서 실행 중입니다.",
            "Mini Monitor",
            0x40,
        )
        return 3

    tray: TrayController | None = None
    session: DesktopSession | None = None
    worker: SerialTaskWorker | None = None
    account: CodexAccountService | None = None
    updater: UpdateService | None = None
    try:
        commands = create_command_queue()
        tray = TrayController(commands)
        account = CodexAccountService(
            cli_path=Path(current_config.ai.codex_cli_path) if current_config.ai.codex_cli_path else None,
            home=user_data_dir() / "codex-home",
            refresh_seconds=current_config.ai.usage_refresh_seconds,
        )
        updater = UpdateService(
            current_version=__version__,
            install_root=Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
            config_path=config_path,
        )
        session = DesktopSession(enable_serial=enable_serial, codex_account_snapshot=account.snapshot)
        worker = SerialTaskWorker()
        secret_store = DPAPISecretStore()
        renderer = _renderer_for_rotation(current_config.device.rotation)
        preview_ai = _initial_ai(current_config, account.snapshot())
        preview_frame = None
        (
            startup_command,
            startup_enabled,
            startup_needs_repair,
            startup_state_unknown,
        ) = _read_autostart_state(config_path)
    except Exception:
        LOGGER.exception("desktop services failed during initialization")
        try:
            _close_partial_desktop_services(tray, session, worker, account, updater)
        finally:
            guard.release()
        return 1
    pending: set[str] = set()
    brightness_target = current_config.device.brightness
    brightness_inflight: int | None = None
    exiting = False
    overlay_faulted = False
    setup: SetupWindow | None = None
    overlay: OverlayWindow | None = None
    power_events: WindowsPowerEventHook | None = None
    apply_requested = False
    notice_retry_at: dict[str, float] = {}
    last_account_snapshot: CodexAccountSnapshot | None = None
    last_update_snapshot: object | None = None
    next_update_check = time.monotonic() + 3600.0

    def request_exit() -> None:
        nonlocal exiting
        if exiting:
            return
        exiting = True
        try:
            setup.hide()
        except (NameError, tk.TclError):
            pass
        try:
            overlay.hide(notify=False)
        except (NameError, tk.TclError):
            pass
        # Close both admission gates before Tk yields. A currently running
        # device probe may finish later, but it can no longer start a fresh
        # controller or open COM after the user requested Exit.
        worker.begin_shutdown()
        session.begin_shutdown()
        try:
            setup.window.quit()
        except (NameError, tk.TclError):
            pass

    def request_device() -> ActionResult:
        if "device" in pending:
            return ActionResult(True, "장치 확인 중…", pending=True)
        if not worker.submit("device", lambda: _detect_device(current_config, enable_serial)):
            return ActionResult(False, "장치 확인을 시작하지 못했습니다", "작업 큐 종료")
        pending.add("device")
        return ActionResult(True, "장치 확인 중…", "읽기 전용으로 정확한 USB 장치를 찾습니다.", pending=True)

    def submit_brightness(brightness: int) -> ActionResult:
        nonlocal brightness_inflight
        value = validate_brightness(brightness)
        if exiting or session.shutting_down:
            return ActionResult(False, "프로그램 종료 중", "밝기 설정을 저장하지 않습니다.")
        if pending.intersection({"start", "usage"}):
            return ActionResult(
                False,
                "현재 작업 완료 후 조절하세요",
                "시작 또는 사용량 확인이 끝나면 다시 조절할 수 있습니다.",
            )
        draft = copy.deepcopy(current_config)
        if not worker.submit(
            "brightness",
            lambda: _change_brightness(
                session,
                draft,
                value,
                config_path,
            ),
        ):
            return ActionResult(False, "밝기 적용을 시작하지 못했습니다", "작업 큐 종료")
        pending.add("brightness")
        brightness_inflight = value
        return ActionResult(
            True,
            f"화면 밝기 {value}% 적용 중…",
            pending=True,
        )

    def request_brightness(brightness: int) -> ActionResult:
        nonlocal brightness_target
        value = validate_brightness(brightness)
        brightness_target = value
        if "brightness" in pending:
            return ActionResult(
                True,
                f"화면 밝기 {value}% 적용 대기…",
                pending=True,
            )
        return submit_brightness(value)

    def request_usage(selection: SetupSelection) -> ActionResult:
        if selection.provider == AIProviderKind.CODEX_ACCOUNT.value:
            deferred = account_selection_deferred()
            if not deferred:
                selected = select_account_provider()
                if not selected.ok:
                    return selected
            if not account.refresh():
                return ActionResult(False, "Codex 한도 확인 실패", "계정 서비스를 다시 시작해 주세요.")
            return ActionResult(
                True,
                "Codex 계정 한도 확인 중…",
                "현재 모니터의 표시 방식은 중지 후 변경할 수 있습니다." if deferred else None,
            )
        if "usage" in pending:
            return ActionResult(True, "사용량 확인 중…", pending=True)
        if pending.intersection({"start", "stop"}):
            return ActionResult(False, "시작·중지 작업 완료 후 확인하세요", "현재 수명주기 작업이 끝나면 다시 시도할 수 있습니다.")
        if session.running and selection.provider != current_config.ai.provider:
            return ActionResult(
                False,
                "사용량 방식을 바꾸려면 먼저 중지하세요",
                "모니터 중지 후 새 방식을 선택하고 다시 시작하면 적용됩니다.",
            )
        if session.running:
            action = lambda: _refresh_active_usage(session)
        else:
            action = lambda: _check_usage(selection, secret_store)
        if not worker.submit("usage", action):
            return ActionResult(False, "사용량 확인을 시작하지 못했습니다", "작업 큐 종료")
        pending.add("usage")
        return ActionResult(True, "사용량 확인 중…", "로컬 기록은 이 PC 밖으로 전송되지 않습니다.", pending=True)

    def request_start(selection: SetupSelection) -> ActionResult:
        if "usage" in pending:
            return ActionResult(False, "사용량 확인 완료 후 시작하세요", "현재 확인 작업이 끝나면 시작 버튼이 다시 활성화됩니다.")
        if pending.intersection({"start", "stop"}):
            return ActionResult(True, "다른 작업 완료 대기 중…", pending=True)
        draft = _draft_config(current_config, selection)
        if not worker.submit(
            "start",
            lambda: _start_session(
                session,
                draft,
                selection,
                secret_store,
                config_path,
            ),
        ):
            return ActionResult(False, "모니터 시작을 요청하지 못했습니다", "작업 큐 종료")
        pending.add("start")
        return ActionResult(True, "모니터 시작 중…", "장치 식별 후 첫 화면을 전송합니다.", pending=True)

    def request_stop() -> ActionResult:
        if "stop" in pending:
            return ActionResult(True, "모니터 중지 중…", pending=True)
        if not worker.submit("stop", lambda: session.stop(timeout=20.0)):
            return ActionResult(False, "모니터 중지를 요청하지 못했습니다", "작업 큐 종료")
        pending.add("stop")
        return ActionResult(True, "모니터 중지 중…", "COM과 백그라운드 수집을 함께 종료합니다.", pending=True)

    def request_reconnect() -> ActionResult:
        if not enable_serial:
            return ActionResult(False, "직렬 전송 비활성", "--no-serial 모드에서는 연결할 수 없습니다.")
        if not session.request_reconnect():
            return ActionResult(False, "먼저 모니터를 시작하세요", "실행 중인 COM 연결이 없습니다.")
        return ActionResult(True, "다시 연결 요청됨", "현재 writer가 장치를 재검증하고 전체 화면을 복구합니다.")

    def request_autostart(enabled: bool) -> ActionResult:
        return _change_autostart(enabled, startup_command)

    def persist_overlay_state(state: OverlayState) -> None:
        """Persist a user move/show/hide after OverlayWindow has applied it."""

        nonlocal current_config
        if exiting:
            return
        try:
            draft = _persist_overlay_state_config(
                current_config,
                state,
                config_path,
            )
        except Exception as error:
            # The close/Escape/drag action already happened.  Keep the button
            # and in-memory snapshot truthful even if disk persistence must be
            # retried during orderly shutdown.
            LOGGER.warning(
                "could not persist desktop overlay state (%s)",
                type(error).__name__,
            )
            draft = _overlay_config_from_state(current_config, state)
        current_config = draft
        setup.set_overlay_settings(
            OverlaySettings(
                draft.overlay.enabled,
                draft.overlay.opacity,
                draft.overlay.scale_percent,
            )
        )

    def request_overlay(settings: OverlaySettings) -> ActionResult:
        nonlocal current_config, overlay_faulted
        if exiting:
            return ActionResult(False, "프로그램 종료 중", "상태창 설정을 저장하지 않습니다.")
        if pending.intersection({"start", "brightness"}):
            return ActionResult(
                False,
                "현재 설정 저장 완료 후 변경하세요",
                "모니터 시작 또는 밝기 저장이 끝나면 다시 시도할 수 있습니다.",
            )
        image = (
            _select_preview_frame(session.latest_image(), preview_frame)
            if settings.enabled
            else None
        )
        result = _apply_overlay_request(
            overlay,
            current_config,
            settings,
            config_path,
            image,
        )
        if result.action.ok:
            current_config = result.config
            overlay_faulted = False
        elif (
            result.action.state_uncertain
            and result.action.overlay_settings is not None
        ):
            current_config = _overlay_config_from_settings(
                result.config,
                result.action.overlay_settings,
            )
        return result.action

    def account_selection_deferred() -> bool:
        return (
            current_config.ai.provider != AIProviderKind.CODEX_ACCOUNT.value
            and (session.running or bool(pending.intersection({"start", "stop"})))
        )

    def select_account_provider() -> ActionResult:
        nonlocal current_config, preview_ai
        if exiting:
            return ActionResult(False, "프로그램 종료 중")
        if account_selection_deferred():
            return ActionResult(False, "모니터를 먼저 중지하세요", "실행 중인 표시 방식은 바꾸지 않습니다.")
        if pending.intersection({"start", "brightness"}):
            return ActionResult(False, "설정 저장 중", "현재 작업이 끝난 뒤 다시 시도해 주세요.")
        if current_config.ai.provider != AIProviderKind.CODEX_ACCOUNT.value:
            try:
                with _CONFIG_WRITE_LOCK:
                    draft = _latest_persisted_config(current_config, config_path)
                    draft.ai.provider = AIProviderKind.CODEX_ACCOUNT.value
                    draft.validate()
                    save_config(draft, config_path)
                    current_config = draft
            except (OSError, ValueError):
                return ActionResult(False, "계정 방식 저장 실패", "설정 파일을 확인해 주세요.")
        preview_ai = account.snapshot().ai
        if setup is not None:
            setup.set_provider(AIProviderKind.CODEX_ACCOUNT.value)
        return ActionResult(True, "Codex 계정 선택됨")

    def request_codex_login() -> ActionResult:
        deferred = account_selection_deferred()
        if not deferred:
            selected = select_account_provider()
            if not selected.ok:
                return selected
        if not account.begin_login():
            return ActionResult(False, "로그인을 시작하지 못했습니다", "진행 중인 로그인 또는 종료 상태를 확인해 주세요.")
        return ActionResult(
            True,
            "ChatGPT 로그인 준비 중…",
            "현재 모니터의 표시 방식은 중지 후 변경할 수 있습니다." if deferred else None,
        )

    def request_codex_cancel() -> ActionResult:
        return ActionResult(bool(account.cancel_login()), "로그인 취소됨")

    def request_codex_logout() -> ActionResult:
        return ActionResult(bool(account.logout()), "Mini Monitor 전용 계정 로그아웃 중…")

    def request_codex_disconnect() -> ActionResult:
        nonlocal current_config, preview_ai, preview_frame
        if session.running or pending.intersection({"start", "stop"}):
            return ActionResult(False, "모니터를 먼저 중지하세요", "실행 중인 계정 표시를 자동으로 바꾸지 않습니다.")
        if current_config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value:
            try:
                with _CONFIG_WRITE_LOCK:
                    draft = _latest_persisted_config(current_config, config_path)
                    draft.ai.provider = AIProviderKind.NOT_CONFIGURED.value
                    draft.validate()
                    save_config(draft, config_path)
                    current_config = draft
            except (OSError, ValueError):
                return ActionResult(False, "연결 해제 저장 실패", "설정 파일을 확인해 주세요.")
        account.set_polling(False)
        preview_ai = not_configured_ai()
        setup.set_provider(AIProviderKind.NOT_CONFIGURED.value)
        preview_frame = renderer.render(DisplaySnapshot(
            ai=preview_ai,
            connection=ConnectionData(
                ConnectionStatus.DISCONNECTED,
                detail="PRESS MONITOR START" if enable_serial else "PREVIEW ONLY",
            ),
        ))
        setup.update_image(preview_frame)
        update_visible_overlay(preview_frame)
        return ActionResult(True, "표시 연결 해제됨", "Mini Monitor 전용 로그인은 유지됩니다.")

    def request_codex_cli_selected(path: Path) -> ActionResult:
        nonlocal current_config
        if not account.set_cli_path(path):
            return ActionResult(False, "CLI 경로 적용 실패", "경로를 다시 선택해 주세요.")
        try:
            with _CONFIG_WRITE_LOCK:
                draft = _latest_persisted_config(current_config, config_path)
                draft.ai.codex_cli_path = str(path.resolve(strict=True))
                draft.validate()
                save_config(draft, config_path)
                current_config = draft
        except (OSError, ValueError):
            return ActionResult(False, "CLI 경로 저장 실패", "다시 선택해 주세요.")
        return ActionResult(True, "CLI 확인 중…", "서명과 app-server 지원 여부를 확인합니다.")

    def open_codex_install_guide() -> ActionResult:
        return ActionResult(bool(webbrowser.open("https://learn.chatgpt.com/docs/codex/cli")), "Codex CLI 설치 안내 열기")

    def request_update_check() -> ActionResult:
        try:
            accepted = updater.check(force=True)
        except Exception as error:
            LOGGER.warning("update check could not start (%s)", type(error).__name__)
            return ActionResult(False, "업데이트 확인 실패", "나중에 다시 시도해 주세요.")
        return ActionResult(bool(accepted), "업데이트 확인 중…" if accepted else "이미 확인 중입니다")

    def request_update_apply() -> ActionResult:
        nonlocal apply_requested
        snapshot = updater.snapshot()
        if snapshot.state == "ready" and snapshot.prepared is not None:
            apply_requested = True
            return ActionResult(True, "업데이트 적용 준비 완료")
        if not updater.prepare():
            return ActionResult(False, "자동 업데이트 불가", "릴리스 페이지에서 수동 설치를 확인해 주세요.")
        apply_requested = True
        return ActionResult(True, "업데이트 준비 중…")

    def request_update_dismiss() -> ActionResult:
        return ActionResult(bool(updater.dismiss()), "나중에 확인")

    def open_update_release() -> ActionResult:
        return ActionResult(bool(webbrowser.open("https://github.com/contentriumkorea/mini-monitor/releases")), "릴리스 페이지 열기")

    def reset_overlay_position() -> ActionResult:
        nonlocal current_config
        if exiting:
            return ActionResult(False, "프로그램 종료 중")
        if pending.intersection({"start", "brightness"}):
            return ActionResult(
                False,
                "현재 설정 저장 완료 후 초기화하세요",
                "모니터 시작 또는 밝기 저장이 끝나면 다시 시도할 수 있습니다.",
            )
        try:
            overlay.reset_position(notify=False)
            with _CONFIG_WRITE_LOCK:
                draft = _latest_persisted_config(current_config, config_path)
                draft.overlay.x = None
                draft.overlay.y = None
                draft.validate()
                save_config(draft, config_path)
        except (OSError, ValueError, tk.TclError):
            return ActionResult(False, "위치 초기화 실패", "모니터 작업 영역을 확인하지 못했습니다.")
        current_config = draft
        return ActionResult(True, "위치 초기화됨", "주 모니터 오른쪽 위로 이동했습니다.")

    def toggle_overlay() -> None:
        requested = OverlaySettings(
            not overlay.visible,
            current_config.overlay.opacity,
            current_config.overlay.scale_percent,
        )
        result = request_overlay(requested)
        if result.ok:
            setup.set_overlay_settings(requested)
            setup.clear_overlay_recovery()
        elif result.state_uncertain and result.overlay_settings is not None:
            setup.set_overlay_settings(result.overlay_settings)

    def recover_overlay_hidden(error: Exception) -> None:
        """Fail only the optional PC window; keep setup/session/tray alive."""

        nonlocal current_config, overlay_faulted
        overlay_faulted = True
        LOGGER.warning(
            "desktop overlay presentation failed (%s)",
            type(error).__name__,
        )
        current_config, actual = _recover_overlay_hidden(
            overlay,
            current_config,
            config_path,
        )
        setup.set_overlay_settings(actual)
        setup.show_overlay_recovery()

    def update_visible_overlay(image: Image.Image) -> None:
        if overlay_faulted or not overlay.visible:
            return
        try:
            overlay.update_image(image)
        except Exception as error:
            recover_overlay_hidden(error)

    def change_orientation(rotation: str) -> None:
        nonlocal renderer, preview_frame
        if session.running:
            return
        renderer = _renderer_for_rotation(rotation)
        preview_frame = renderer.render(
            DisplaySnapshot(
                ai=preview_ai,
                connection=ConnectionData(
                    ConnectionStatus.DISCONNECTED,
                    detail="PRESS MONITOR START" if enable_serial else "PREVIEW ONLY",
                ),
            )
        )
        setup.update_image(preview_frame)
        update_visible_overlay(preview_frame)

    try:
        initial_provider = _initial_setup_provider(
            current_config,
            minimized=minimized,
        )
        if current_config.ai.provider == AIProviderKind.NOT_CONFIGURED.value and not minimized:
            preview_ai = account.snapshot().ai
        setup = SetupWindow(
            provider=initial_provider,
            codex_local_consent=current_config.ai.codex_local_consent,
            on_detect_device=request_device,
            on_check_usage=request_usage,
            on_start=request_start,
            on_stop=request_stop,
            on_reconnect=request_reconnect,
            on_exit=request_exit,
            enable_serial=enable_serial,
            openai_key_configured=secret_store.configured(),
            usage_refresh_seconds=current_config.ai.usage_refresh_seconds,
            cost_refresh_seconds=current_config.ai.cost_refresh_seconds,
            daily_budget_usd=current_config.ai.daily_budget_usd,
            monthly_budget_usd=current_config.ai.monthly_budget_usd,
            rotation=current_config.device.rotation,
            on_orientation_change=change_orientation,
            brightness=current_config.device.brightness,
            on_brightness_change=request_brightness,
            autostart_enabled=startup_enabled,
            autostart_needs_repair=startup_needs_repair,
            autostart_state_unknown=startup_state_unknown,
            on_autostart_change=request_autostart,
            overlay_enabled=current_config.overlay.enabled,
            overlay_opacity=current_config.overlay.opacity,
            overlay_scale_percent=current_config.overlay.scale_percent,
            on_overlay_change=request_overlay,
            on_overlay_reset_position=reset_overlay_position,
            on_codex_login=request_codex_login,
            on_codex_cancel=request_codex_cancel,
            on_codex_logout=request_codex_logout,
            on_codex_disconnect=request_codex_disconnect,
            on_codex_cli_selected=request_codex_cli_selected,
            on_codex_install_guide=open_codex_install_guide,
            on_update_check=request_update_check,
            on_update_apply=request_update_apply,
            on_update_dismiss=request_update_dismiss,
            on_update_open_release=open_update_release,
        )
        overlay_position = (
            (current_config.overlay.x, current_config.overlay.y)
            if current_config.overlay.x is not None
            and current_config.overlay.y is not None
            else None
        )
        overlay = OverlayWindow(
            setup.window,
            title="Mini Monitor · PC 상태창",
            frame_size=orientation_spec(current_config.device.rotation).dimensions,
            opacity=current_config.overlay.opacity,
            scale_percent=current_config.overlay.scale_percent,
            position=overlay_position,
            on_state_change=persist_overlay_state,
        )
        power_events = WindowsPowerEventHook(
            setup.window,
            on_suspend=lambda: session.suspend_active(timeout=2.0),
            on_resume=session.resume_active,
        )

        initial_frame = renderer.render(
            DisplaySnapshot(
                ai=preview_ai,
                connection=ConnectionData(
                    ConnectionStatus.DISCONNECTED,
                    detail=(
                        "PRESS MONITOR START" if enable_serial else "PREVIEW ONLY"
                    ),
                ),
            )
        )
        preview_frame = initial_frame.copy()
        setup.update_image(initial_frame)
        if current_config.overlay.enabled:
            try:
                overlay.update_image(initial_frame)
                overlay.show(notify=False)
            except Exception as error:
                recover_overlay_hidden(error)
        setup.update_codex_account(account.snapshot())
        setup.update_update_status(updater.snapshot())
        update_acknowledged = acknowledge_update_startup(current_version=__version__)
        if not update_acknowledged and getattr(sys, "frozen", False):
            if not _queue_update_recovery(
                worker, install_root=Path(sys.executable).parent,
                acknowledged=False, frozen=True,
            ):
                LOGGER.warning("update recovery check could not start")
            threading.Thread(
                target=cleanup_healthy_update_backup,
                args=(Path(sys.executable).resolve().parent,),
                name="mini-monitor-update-cleanup",
                daemon=True,
            ).start()
    except Exception:
        LOGGER.exception("desktop windows failed during initialization")
        try:
            if power_events is not None:
                try:
                    power_events.close()
                except Exception:
                    LOGGER.exception("could not close partial power-event hook")
            if overlay is not None:
                try:
                    overlay.destroy()
                except (RuntimeError, tk.TclError):
                    pass
            if setup is not None:
                try:
                    setup.close()
                except (RuntimeError, tk.TclError):
                    pass
            _close_partial_desktop_services(tray, session, worker, account, updater)
        finally:
            guard.release()
        return 1

    def apply_worker_result(result: WorkResult) -> None:
        nonlocal current_config, preview_ai, preview_frame, brightness_inflight
        completed_brightness = (
            brightness_inflight if result.kind == "brightness" else None
        )
        if result.kind == "brightness":
            brightness_inflight = None
        pending.discard(result.kind)
        if result.error_kind:
            action = ActionResult(False, "작업을 완료하지 못했습니다", result.error_kind)
            if result.kind == "device":
                setup.complete_device(action)
            elif result.kind == "usage":
                setup.complete_usage(action)
            elif result.kind == "start":
                setup.complete_start(action, running=session.running)
            elif result.kind == "stop":
                setup.complete_stop(action, running=session.running)
            elif result.kind == "brightness" and completed_brightness is not None:
                if brightness_target != completed_brightness:
                    followup = submit_brightness(brightness_target)
                    if not followup.pending:
                        setup.complete_brightness(
                            followup,
                            brightness=brightness_target,
                        )
                else:
                    setup.complete_brightness(
                        action,
                        brightness=completed_brightness,
                    )
            return

        if result.kind == "update_recovery":
            if isinstance(result.value, RecoveryNotice):
                try:
                    _present_update_recovery(result.value, setup)
                except (RuntimeError, tk.TclError):
                    LOGGER.warning("update recovery notice could not be shown")
            return

        if result.kind == "device" and isinstance(result.value, ActionResult):
            setup.complete_device(result.value)
        elif result.kind == "usage" and isinstance(result.value, UsageWorkResult):
            setup.complete_usage(result.value.action)
            preview_ai = result.value.ai
            if not session.running:
                preview_frame = renderer.render(
                    DisplaySnapshot(
                        ai=preview_ai,
                        connection=ConnectionData(
                            ConnectionStatus.DISCONNECTED,
                            detail="PRESS MONITOR START" if enable_serial else "PREVIEW ONLY",
                        ),
                    )
                )
                setup.update_image(preview_frame)
                update_visible_overlay(preview_frame)
        elif result.kind == "start" and isinstance(result.value, StartWorkResult):
            if result.value.config is not None:
                merged = result.value.config
                try:
                    current_config = _merge_current_overlay_config(
                        merged,
                        current_config,
                        config_path,
                    )
                except OSError:
                    LOGGER.warning("could not re-merge desktop overlay settings")
            setup.complete_start(result.value.action, running=session.running)
            if result.value.action.ok and current_config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value:
                account.set_polling(True)
            setup.set_openai_key_configured(secret_store.configured())
        elif result.kind == "stop" and isinstance(result.value, SessionResult):
            action = ActionResult(
                result.value.ok,
                "모니터 중지됨" if result.value.ok else "모니터가 완전히 중지되지 않았습니다",
                "COM과 수집 작업이 종료되었습니다." if result.value.ok else "프로그램을 종료한 뒤 다시 실행하세요.",
            )
            setup.complete_stop(
                action,
                running=session.running,
                blocked=result.value.state is SessionState.ERROR,
            )
            if result.value.ok:
                account.set_polling(False)
                if current_config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value:
                    preview_ai = account.snapshot().ai
                    preview_frame = renderer.render(DisplaySnapshot(
                        ai=preview_ai,
                        connection=ConnectionData(
                            ConnectionStatus.DISCONNECTED,
                            detail="PRESS MONITOR START" if enable_serial else "PREVIEW ONLY",
                        ),
                    ))
                    setup.update_image(preview_frame)
                    update_visible_overlay(preview_frame)
        elif result.kind == "brightness" and isinstance(
            result.value,
            BrightnessWorkResult,
        ):
            if result.value.config is not None:
                merged = result.value.config
                try:
                    current_config = _merge_current_overlay_config(
                        merged,
                        current_config,
                        config_path,
                    )
                except OSError:
                    LOGGER.warning("could not re-merge desktop overlay settings")
            if brightness_target != result.value.brightness:
                followup = submit_brightness(brightness_target)
                if not followup.pending:
                    setup.complete_brightness(
                        followup,
                        brightness=brightness_target,
                    )
            else:
                setup.complete_brightness(
                    result.value.action,
                    brightness=result.value.brightness,
                )
        elif result.kind == "brightness" and completed_brightness is not None:
            if brightness_target != completed_brightness:
                followup = submit_brightness(brightness_target)
                if not followup.pending:
                    setup.complete_brightness(
                        followup,
                        brightness=brightness_target,
                    )
            else:
                setup.complete_brightness(
                    ActionResult(
                        False,
                        "화면 밝기 적용 실패",
                        "잘못된 내부 응답",
                    ),
                    brightness=completed_brightness,
                )

    def poll() -> None:
        nonlocal preview_frame, preview_ai, last_account_snapshot
        nonlocal last_update_snapshot, apply_requested, next_update_check
        if exiting:
            return
        try:
            while True:
                command = commands.get_nowait()
                if command is TrayCommand.SETUP:
                    setup.show()
                elif command is TrayCommand.OVERLAY:
                    toggle_overlay()
                elif command is TrayCommand.RECONNECT:
                    request_reconnect()
                elif command is TrayCommand.EXIT:
                    request_exit()
        except queue.Empty:
            pass

        for result in worker.poll():
            apply_worker_result(result)

        for event in account.drain_events():
            if event.kind == "auth_url" and event.auth_url and event.generation == account.snapshot().generation:
                try:
                    webbrowser.open(event.auth_url)
                except (OSError, webbrowser.Error):
                    LOGGER.warning("could not open Codex authorization browser")

        account_snapshot = account.snapshot()
        if account_snapshot != last_account_snapshot:
            last_account_snapshot = account_snapshot
            setup.update_codex_account(account_snapshot)
            if not session.running and current_config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value:
                preview_ai = account_snapshot.ai
                preview_frame = renderer.render(DisplaySnapshot(
                    ai=preview_ai,
                    connection=ConnectionData(
                        ConnectionStatus.DISCONNECTED,
                        detail="PRESS MONITOR START" if enable_serial else "PREVIEW ONLY",
                    ),
                ))
                setup.update_image(preview_frame)
                update_visible_overlay(preview_frame)

        now = time.monotonic()
        if now >= next_update_check:
            try:
                updater.check()
            except Exception as error:
                LOGGER.warning("periodic update check failed (%s)", type(error).__name__)
            next_update_check = now + 3600.0
        update_snapshot = updater.snapshot()
        if update_snapshot != last_update_snapshot:
            last_update_snapshot = update_snapshot
            setup.update_update_status(update_snapshot)
        if update_snapshot.notification_pending and update_snapshot.version:
            if now >= notice_retry_at.get(update_snapshot.version, 0.0):
                try:
                    delivered = tray.notify_update(update_snapshot.version)
                except Exception as error:
                    LOGGER.warning("update notification failed (%s)", type(error).__name__)
                    delivered = False
                if delivered:
                    updater.mark_notification_delivered(update_snapshot.version)
                else:
                    notice_retry_at[update_snapshot.version] = now + _UPDATE_NOTICE_RETRY_SECONDS
        if apply_requested and update_snapshot.state == "ready" and update_snapshot.prepared is not None:
            apply_requested = False
            try:
                launched = launch_update_helper(update_snapshot.prepared, parent_pid=os.getpid())
            except Exception as error:
                LOGGER.warning("update helper could not start (%s)", type(error).__name__)
                launched = False
            if launched:
                request_exit()
                return
            LOGGER.warning("update helper launch failed; app remains running")

        image = session.latest_image()
        if image is not None and session.state in {SessionState.STARTING, SessionState.RUNNING}:
            preview_frame = image.copy()
            if setup.visible:
                setup.update_image(image)
            update_visible_overlay(image)

        values = session.runtime_values()
        if values is not None and session.running:
            setup.update_runtime(values.connection, values.ai, running=True)
            status = values.connection.status.value
            if values.connection.port:
                status = f"{status} · {values.connection.port}"
            tray.set_status(status)
            tray.set_provider(values.ai.title)
        elif session.state is SessionState.ERROR:
            setup.set_blocked()
            tray.set_status("RESTART REQUIRED")
            tray.set_provider(_provider_title(setup.selected_provider))
        elif not pending.intersection({"start", "stop", "usage"}):
            # Completion handlers already published the final idle/error text.
            # Reapplying the generic idle state every 100 ms would erase an
            # actionable PORT IN USE/startup failure immediately.
            tray.set_status("SETUP")
            tray.set_provider(_provider_title(setup.selected_provider))
        setup.window.after(100, poll)

    try:
        signal.signal(signal.SIGINT, lambda _signum, _frame: request_exit())
        power_events.install()
        tray.start()
        try:
            updater.check()
        except Exception as error:
            LOGGER.warning("startup update check failed (%s)", type(error).__name__)
        if current_config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value:
            account.refresh()
        request_device()
        if minimized:
            setup.hide()
            # Autostart uses the saved configuration exactly; it never grants
            # local-session consent on the user's behalf.
            request_start(_selection_from_config(current_config))
        else:
            setup.show()
        if auto_exit_seconds is not None:
            setup.window.after(
                max(1, round(auto_exit_seconds * 1000)),
                request_exit,
            )
        setup.window.after(50, poll)
        setup.window.mainloop()
        return 0
    except Exception:
        LOGGER.exception("desktop application failed")
        return 1
    finally:
        worker.begin_shutdown()
        session.begin_shutdown()
        try:
            power_events.close()
        except Exception:
            LOGGER.exception("could not restore the Tk window procedure")
        tray.stop()
        # begin_shutdown() already revokes serial admission. Finish the active
        # controller/COM teardown before waiting on unrelated device or usage
        # work so Exit never keeps a running monitor open behind a slow scan.
        stopped = session.stop(timeout=20.0)
        if not stopped.ok:
            LOGGER.error("application shutdown did not complete before timeout")
        if not worker.close(timeout=10.0):
            LOGGER.error("desktop worker did not stop before timeout")
        try:
            account.close()
        except Exception as error:
            LOGGER.error("account service close failed (%s)", type(error).__name__)
        try:
            updater.close()
        except Exception as error:
            LOGGER.error("update service close failed (%s)", type(error).__name__)
        try:
            # A start/brightness task can finish its atomic save after the Tk
            # loop has stopped consuming results.  Reapply only the newest
            # in-memory overlay fields to that final on-disk snapshot so Exit
            # cannot lose a last drag, visibility, scale, or opacity change.
            current_config = _persist_latest_overlay_on_shutdown(
                current_config,
                config_path,
            )
        except (OSError, ValueError):
            LOGGER.warning("could not persist final desktop overlay state")
        try:
            if overlay is not None and not overlay.closed:
                overlay.destroy()
        except (NameError, tk.TclError):
            pass
        if setup is not None and not setup.closed:
            try:
                setup.close()
            except tk.TclError:
                pass
        try:
            guard.release()
        except Exception:
            LOGGER.exception("could not release the single-instance mutex")


def _detect_device(config: AppConfig, enable_serial: bool) -> ActionResult:
    if not enable_serial:
        return ActionResult(True, "미리보기 전용", "--no-serial: COM을 열지 않습니다.")
    selected = DeviceDetector().select(config.device.manual_port)
    return ActionResult(
        True,
        f"장치 준비됨 · {selected.device}",
        "VID 1A86 · PID 5722 · USB35INCHIPSV2",
    )


def _check_usage(selection: SetupSelection, secret_store: DPAPISecretStore) -> UsageWorkResult:
    if selection.provider == AIProviderKind.CODEX_LOCAL.value:
        if not selection.codex_local_consent:
            ai = codex_to_ai_data(
                CodexUsageProvider(consent_granted=False).refresh()
            )
            return UsageWorkResult(
                ActionResult(False, "동의가 필요합니다", "체크박스를 선택해야 로컬 세션을 확인합니다."),
                ai,
            )
        snapshot = CodexUsageProvider(consent_granted=True).refresh()
        ai = codex_to_ai_data(snapshot)
        if snapshot.status is CodexUsageStatus.OK:
            detail = " · ".join(f"{label} {value}" for label, value in ai.fields[:3])
            return UsageWorkResult(
                ActionResult(
                    True,
                    f"{ai.primary_label} {ai.primary_value}".strip(),
                    detail,
                ),
                ai,
            )
        messages = {
            CodexUsageStatus.STALE: ("오래된 Codex 한도 정보", "최근 15분 안에 새 한도 이벤트가 없어 지연 상태로 표시합니다."),
            CodexUsageStatus.SESSIONS_NOT_FOUND: ("Codex 기록을 찾지 못했습니다", "Codex를 한 번 사용한 뒤 다시 확인하세요."),
            CodexUsageStatus.NO_RATE_LIMITS: ("한도 정보가 아직 없습니다", "Codex가 새 rate-limit 이벤트를 기록한 뒤 갱신됩니다."),
            CodexUsageStatus.UNAVAILABLE: ("로컬 기록을 읽지 못했습니다", "권한과 CODEX_HOME 설정을 확인하세요."),
            CodexUsageStatus.CONSENT_REQUIRED: ("동의가 필요합니다", "로컬 기록은 동의 전에는 열지 않습니다."),
        }
        title, detail = messages[snapshot.status]
        return UsageWorkResult(ActionResult(False, title, detail), ai)

    if selection.provider == AIProviderKind.OPENAI_API.value:
        configured = bool(selection.openai_admin_key) or secret_store.configured()
        ai = not_configured_ai()
        if configured:
            return UsageWorkResult(
                ActionResult(True, "OpenAI API 설정 준비됨", "시작하면 공식 조직 Usage·Costs API를 사용합니다."),
                ai,
            )
        return UsageWorkResult(
            ActionResult(False, "Admin Key가 필요합니다", "키를 입력하거나 이미 저장된 키를 사용하세요."),
            ai,
        )

    if selection.provider == AIProviderKind.CHATGPT_ACTIVITY.value:
        return UsageWorkResult(
            ActionResult(True, "로컬 활동 시간 사용", "창 활성 시간만 집계하며 메시지 내용은 읽지 않습니다."),
            not_configured_ai(),
        )
    return UsageWorkResult(
        ActionResult(True, "AI 사용량 표시 안 함", "하드웨어 모니터만 실행할 수 있습니다."),
        not_configured_ai(),
    )


def _refresh_active_usage(
    session: DesktopSession,
    *,
    timeout: float = 2.0,
    poll_interval: float = 0.05,
) -> UsageWorkResult:
    """Wake the active provider and return the value used by the LCD.

    Provider-level minimum polling intervals remain authoritative; this helper
    never performs a second local scan or bypasses OpenAI API rate controls.
    """

    before = session.runtime_values()
    if before is None or not session.request_ai_refresh():
        return UsageWorkResult(
            ActionResult(False, "실행 중인 사용량 공급자가 없습니다", "모니터를 시작한 뒤 다시 확인하세요."),
            not_configured_ai(),
        )
    baseline = before.ai
    deadline = time.monotonic() + max(0.0, timeout)
    current = before
    changed = False
    while time.monotonic() < deadline:
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        candidate = session.runtime_values()
        if candidate is None:
            break
        current = candidate
        if candidate.ai != baseline:
            changed = True
            break

    ai = current.ai
    title = f"{ai.title} · {ai.primary_value} {ai.primary_label}".strip()
    # The setup card reserves two lines for feedback so async updates never
    # reflow the controls below it. The LCD still keeps every field; this
    # compact summary shows the two most useful fields plus freshness.
    fields = " · ".join(f"{label} {value}" for label, value in ai.fields[:2])
    freshness = "새 값으로 갱신됨" if changed else "안전한 최소 주기 내 현재 값"
    detail = " · ".join(part for part in (fields, freshness) if part)
    return UsageWorkResult(
        ActionResult(ai.status is SyncStatus.OK, title, detail),
        ai,
    )


def _select_preview_frame(
    latest: Image.Image | None,
    fallback: Image.Image | None,
) -> Image.Image | None:
    """Return an owned frame so a pre-Start preview is never blank."""

    source = latest if latest is not None else fallback
    return None if source is None else source.copy()


def _apply_overlay_request(
    overlay: OverlayWindow,
    base: AppConfig,
    settings: OverlaySettings,
    config_path: Path | None,
    image: Image.Image | None,
) -> OverlayWorkResult:
    """Apply one desktop-overlay request atomically, including native output."""

    previous = copy.deepcopy(base.overlay)
    try:
        overlay.set_opacity(settings.opacity, notify=False)
        overlay.set_scale(settings.scale_percent, notify=False)
        if settings.enabled:
            if image is not None:
                overlay.update_image(image)
            overlay.show(notify=False)
        else:
            overlay.hide(notify=False)
        draft = _apply_overlay_settings_config(base, settings, config_path)
    except Exception as error:
        # Native layered-window, Pillow and Tk failures can surface as several
        # Exception subclasses. Never let one escape the Tk callback or leave
        # the saved toggle and the actual topmost window disagreeing.
        LOGGER.warning(
            "desktop overlay request failed (%s)",
            type(error).__name__,
        )
        rollback_failed = False
        rollback_steps = (
            lambda: overlay.set_opacity(previous.opacity, notify=False),
            lambda: overlay.set_scale(previous.scale_percent, notify=False),
            lambda: (
                overlay.show(notify=False)
                if previous.enabled
                else overlay.hide(notify=False)
            ),
        )
        for rollback in rollback_steps:
            try:
                rollback()
            except Exception as rollback_error:
                rollback_failed = True
                LOGGER.warning(
                    "desktop overlay rollback failed (%s)",
                    type(rollback_error).__name__,
                )
        return OverlayWorkResult(
            ActionResult(
                False,
                "상태창 설정 실패",
                (
                    "상태창 상태를 다시 확인해 주세요."
                    if rollback_failed
                    else "설정을 저장하거나 상태창에 적용하지 못했습니다."
                ),
                state_uncertain=rollback_failed,
                overlay_settings=(
                    _observed_overlay_settings(overlay, previous)
                    if rollback_failed
                    else None
                ),
            ),
            base,
        )

    return OverlayWorkResult(
        ActionResult(
            True,
            "상태창 표시" if settings.enabled else "상태창 숨김",
            (
                f"크기 {settings.scale_percent}% · 카드 불투명도 "
                f"{round(settings.opacity * 100)}%"
                if settings.enabled
                else "트레이 또는 설정창에서 언제든 다시 표시할 수 있습니다."
            ),
        ),
        draft,
    )


def _observed_overlay_settings(
    overlay: OverlayWindow,
    fallback: object,
) -> OverlaySettings:
    """Read the actual runtime state without letting diagnostics fail recovery."""

    try:
        state = overlay.state
        return OverlaySettings(
            state.visible,
            state.opacity,
            state.scale_percent,
        )
    except Exception:
        return OverlaySettings(
            bool(getattr(overlay, "visible", getattr(fallback, "enabled", False))),
            float(getattr(overlay, "opacity", getattr(fallback, "opacity", 0.85))),
            int(
                getattr(
                    overlay,
                    "scale_percent",
                    getattr(fallback, "scale_percent", 100),
                )
            ),
        )


def _recover_overlay_hidden(
    overlay: OverlayWindow,
    base: AppConfig,
    config_path: Path | None,
) -> tuple[AppConfig, OverlaySettings]:
    """Hide a failed optional overlay and best-effort persist the actual state."""

    try:
        overlay.hide(notify=False)
    except Exception as error:
        LOGGER.warning(
            "could not hide failed desktop overlay (%s)",
            type(error).__name__,
        )
    actual = _observed_overlay_settings(overlay, base.overlay)
    try:
        return _apply_overlay_settings_config(base, actual, config_path), actual
    except Exception as error:
        LOGGER.warning(
            "could not persist disabled desktop overlay (%s)",
            type(error).__name__,
        )
        fallback = copy.deepcopy(base)
        fallback.overlay.enabled = actual.enabled
        fallback.overlay.opacity = actual.opacity
        fallback.overlay.scale_percent = actual.scale_percent
        return fallback, actual


def _apply_overlay_settings_config(
    base: AppConfig,
    settings: OverlaySettings,
    config_path: Path | None,
) -> AppConfig:
    """Persist display-only overlay controls without touching COM state."""

    with _CONFIG_WRITE_LOCK:
        draft = _overlay_config_from_settings(
            _latest_persisted_config(base, config_path),
            settings,
        )
        save_config(draft, config_path)
        return draft


def _overlay_config_from_settings(
    base: AppConfig,
    settings: OverlaySettings,
) -> AppConfig:
    """Apply observed overlay controls to a copy without doing any I/O."""

    draft = copy.deepcopy(base)
    draft.overlay.enabled = settings.enabled
    draft.overlay.opacity = settings.opacity
    draft.overlay.scale_percent = settings.scale_percent
    draft.validate()
    return draft


def _persist_overlay_state_config(
    base: AppConfig,
    state: OverlayState,
    config_path: Path | None,
) -> AppConfig:
    """Persist the final drag/show/hide state emitted on the Tk thread."""

    with _CONFIG_WRITE_LOCK:
        draft = _overlay_config_from_state(
            _latest_persisted_config(base, config_path),
            state,
        )
        save_config(draft, config_path)
        return draft


def _overlay_config_from_state(
    base: AppConfig,
    state: OverlayState,
) -> AppConfig:
    """Reflect an already-applied overlay state without doing any I/O."""

    draft = copy.deepcopy(base)
    draft.overlay.enabled = state.visible
    draft.overlay.opacity = state.opacity
    draft.overlay.scale_percent = state.scale_percent
    draft.overlay.x = state.x
    draft.overlay.y = state.y
    draft.validate()
    return draft


def _merge_current_overlay_config(
    worker_config: AppConfig,
    current_config: AppConfig,
    config_path: Path | None,
) -> AppConfig:
    """Prevent an older start/brightness snapshot from reverting the overlay."""

    with _CONFIG_WRITE_LOCK:
        merged = copy.deepcopy(worker_config)
        merged.overlay = copy.deepcopy(current_config.overlay)
        merged.validate()
        save_config(merged, config_path)
        return merged


def _config_target(config_path: Path | None) -> Path:
    return config_path if config_path is not None else default_config_path()


def _latest_persisted_config(
    fallback: AppConfig,
    config_path: Path | None,
) -> AppConfig:
    """Read the latest complete snapshot while the process write lock is held."""

    target = _config_target(config_path)
    if target.exists():
        return load_config(target)
    return copy.deepcopy(fallback)


def _save_worker_config_preserving_overlay(
    worker_config: AppConfig,
    config_path: Path | None,
) -> AppConfig:
    """Atomically save worker settings without reverting a newer overlay."""

    with _CONFIG_WRITE_LOCK:
        committed = copy.deepcopy(worker_config)
        target = _config_target(config_path)
        if target.exists():
            committed.overlay = copy.deepcopy(load_config(target).overlay)
        committed.validate()
        save_config(committed, config_path)
        return committed


def _persist_latest_overlay_on_shutdown(
    current_config: AppConfig,
    config_path: Path | None,
) -> AppConfig:
    """Merge the last Tk overlay state after all background writes finish."""

    with _CONFIG_WRITE_LOCK:
        merged = _latest_persisted_config(current_config, config_path)
        merged.overlay = copy.deepcopy(current_config.overlay)
        merged.validate()
        save_config(merged, config_path)
        return merged


def _read_autostart_state(
    config_path: Path | None,
) -> tuple[str, bool, bool, bool]:
    """Read Run state, migrating only a verified opt-in command from this app."""

    command = autostart.build_app_command(config_path)
    try:
        state = autostart.read_state(command)
        if state.configured and not state.enabled:
            try:
                if autostart.migrate_known_legacy(command, config_path=config_path):
                    state = autostart.read_state(command)
            except (OSError, ValueError):
                LOGGER.warning("known legacy autostart could not be migrated")
    except OSError:
        return command, False, False, True
    return command, state.enabled, state.configured and not state.enabled, False


def _change_autostart(enabled: bool, expected_command: str) -> ActionResult:
    """Toggle one HKCU Run value and verify the resulting state immediately."""

    try:
        if enabled:
            autostart.enable(command=expected_command)
        else:
            autostart.disable()
    except (OSError, ValueError):
        return ActionResult(
            False,
            "자동 실행 변경 실패",
            "Windows 사용자 시작 항목에 접근하지 못했습니다.",
        )

    try:
        state = autostart.read_state(expected_command)
    except (OSError, ValueError):
        return ActionResult(
            False,
            "자동 실행 상태 확인 필요",
            "변경 요청 후 Windows 시작 항목을 다시 확인하지 못했습니다.",
            state_uncertain=True,
        )

    if enabled and state.enabled:
        return ActionResult(
            True,
            "자동 실행 켜짐",
            "다음 Windows 로그인부터 설정창 없이 알림 영역에서 시작합니다.",
        )
    if not enabled and not state.configured:
        return ActionResult(
            True,
            "자동 실행 꺼짐",
            "Windows 로그인 시작 항목에서 제거했습니다.",
        )
    return ActionResult(
        False,
        "자동 실행 확인 실패",
        "Windows 시작 항목을 다시 읽었지만 요청한 상태와 일치하지 않습니다.",
    )


def _change_brightness(
    session: DesktopSession,
    base: AppConfig,
    brightness: int,
    config_path: Path | None,
) -> BrightnessWorkResult:
    """Persist one desired value, then route it through the active writer."""

    value = validate_brightness(brightness)
    if getattr(session, "shutting_down", False):
        return BrightnessWorkResult(
            ActionResult(
                False,
                "프로그램 종료 중",
                "밝기 설정을 저장하지 않습니다.",
            ),
            value,
        )

    draft = copy.deepcopy(base)
    draft.device.brightness = value
    draft.validate()
    draft = _save_worker_config_preserving_overlay(draft, config_path)

    routed = False
    if session.running:
        try:
            routed = session.request_brightness(value)
        except Exception:
            routed = False
    applied_live = routed and bool(session.enable_serial)
    if session.running and not routed:
        # A stop/shutdown race can remove the active controller after the
        # atomic save. The desired value is still canonical and will be sent
        # by the same writer on the next Start; never open another COM handle.
        action = ActionResult(
            True,
            f"화면 밝기 {value}% 저장됨",
            "현재 연결이 종료되어 다음 시작 시 적용합니다.",
        )
    else:
        action = ActionResult(
            True,
            f"화면 밝기 {value}%",
            "실행 중인 단일 writer에 적용했습니다."
            if applied_live
            else "다음 모니터 시작 시 적용합니다.",
        )
    return BrightnessWorkResult(action, value, draft)


def _draft_config(base: AppConfig, selection: SetupSelection) -> AppConfig:
    draft = copy.deepcopy(base)
    draft.device.rotation = selection.rotation
    draft.device.brightness = selection.brightness
    draft.ai.provider = selection.provider
    draft.ai.codex_local_consent = selection.codex_local_consent
    draft.ai.usage_refresh_seconds = selection.usage_refresh_seconds
    draft.ai.cost_refresh_seconds = selection.cost_refresh_seconds
    draft.ai.daily_budget_usd = selection.daily_budget_usd
    draft.ai.monthly_budget_usd = selection.monthly_budget_usd
    return draft


def _selection_from_config(config: AppConfig) -> SetupSelection:
    """Build a non-secret selection without resetting saved advanced values."""

    return SetupSelection(
        provider=config.ai.provider,
        codex_local_consent=config.ai.codex_local_consent,
        usage_refresh_seconds=config.ai.usage_refresh_seconds,
        cost_refresh_seconds=config.ai.cost_refresh_seconds,
        daily_budget_usd=config.ai.daily_budget_usd,
        monthly_budget_usd=config.ai.monthly_budget_usd,
        rotation=config.device.rotation,
        brightness=config.device.brightness,
    )


def _renderer_for_rotation(rotation: str) -> DashboardRenderer:
    orientation = orientation_spec(rotation)
    return DashboardRenderer(
        layout=layout_for_dimensions(*orientation.dimensions)
    )


def _initial_setup_provider(config: AppConfig, *, minimized: bool) -> str:
    """Recommend official account limits for interactive first-run setup."""

    if minimized or config.ai.provider != AIProviderKind.NOT_CONFIGURED.value:
        return config.ai.provider
    return AIProviderKind.CODEX_ACCOUNT.value


def _start_session(
    session: DesktopSession,
    draft: AppConfig,
    selection: SetupSelection,
    secret_store: DPAPISecretStore,
    config_path: Path | None,
) -> StartWorkResult:
    if getattr(session, "shutting_down", False):
        return StartWorkResult(ActionResult(False, "프로그램 종료 중", "새 설정이나 COM 연결을 시작하지 않습니다."))
    if draft.ai.provider == AIProviderKind.CODEX_LOCAL.value and not draft.ai.codex_local_consent:
        return StartWorkResult(
            ActionResult(False, "Codex 로컬 읽기 동의를 확인하세요", "또는 AI 사용량에서 ‘사용 안 함’을 선택하세요.")
        )
    try:
        draft.validate()
    except ValueError:
        return StartWorkResult(ActionResult(False, "설정값을 확인하세요", "유효하지 않은 설정입니다."))

    selected_port: str | None = None
    if session.enable_serial:
        try:
            selected_port = DeviceDetector().select(draft.device.manual_port).device
        except Exception as error:
            return StartWorkResult(ActionResult(False, "미니 모니터를 찾지 못했습니다", type(error).__name__))

    if (
        draft.ai.provider == AIProviderKind.OPENAI_API.value
        and not selection.openai_admin_key
        and not secret_store.configured()
    ):
        return StartWorkResult(ActionResult(False, "OpenAI Admin Key가 없습니다", "키를 입력하거나 다른 사용량 방식을 선택하세요."))

    committed_config: AppConfig | None = None

    def commit_persistent_settings() -> None:
        nonlocal committed_config
        if (
            draft.ai.provider == AIProviderKind.OPENAI_API.value
            and selection.openai_admin_key
        ):
            secret_store.set(selection.openai_admin_key)
        committed_config = _save_worker_config_preserving_overlay(
            draft,
            config_path,
        )

    started = session.start(draft, prepare=commit_persistent_settings)
    if not started.ok:
        return StartWorkResult(
            _start_failure_action(started.reason, selected_port),
            committed_config,
        )
    title = "미리보기 모드 시작됨" if not session.enable_serial else f"모니터 시작됨 · {selected_port}"
    detail = "COM을 열지 않습니다." if not session.enable_serial else "첫 전체 화면을 전송하고 실시간 갱신을 시작했습니다."
    return StartWorkResult(
        ActionResult(True, title, detail),
        committed_config if committed_config is not None else draft,
    )


def _start_failure_action(reason: str | None, port: str | None) -> ActionResult:
    """Map internal startup outcomes to bounded, actionable, non-raw UI text."""

    port_label = port or "선택된 포트"
    if reason == "port_in_use":
        return ActionResult(
            False,
            f"PORT IN USE · {port_label}",
            f"{port_label} 액세스가 거부되었습니다. UsbMonitor 또는 다른 직렬 앱을 "
            "트레이까지 완전히 종료한 뒤 ‘모니터 시작’을 다시 누르세요. "
            "앱이 다른 프로세스를 자동으로 종료하지는 않습니다.",
        )
    if reason == "device_absent":
        return ActionResult(
            False,
            "미니 모니터 연결이 끊겼습니다",
            "USB 케이블을 다시 연결하고 ‘장치 찾기’ 후 ‘모니터 시작’을 누르세요.",
        )
    if reason == "timeout":
        return ActionResult(
            False,
            f"첫 화면 전송 시간 초과 · {port_label}",
            "USB 연결을 확인한 뒤 ‘모니터 시작’을 다시 누르세요. "
            "실제 LCD 표시 확인이 아니라 직렬 전송 완료를 기다리다 중단되었습니다.",
        )
    if reason == "link_error":
        return ActionResult(
            False,
            f"첫 화면 전송 실패 · {port_label}",
            "USB 케이블을 다시 연결하고 ‘모니터 시작’을 다시 누르세요.",
        )
    if reason == "shutting_down":
        return ActionResult(False, "프로그램 종료 중", "새 COM 연결을 시작하지 않습니다.")
    if reason == "start_cleanup_incomplete":
        return ActionResult(
            False,
            "연결 정리가 완료되지 않았습니다",
            "프로그램을 종료한 뒤 다시 실행하세요.",
        )
    return ActionResult(
        False,
        "모니터를 시작하지 못했습니다",
        "USB 연결과 다른 직렬 프로그램 사용 여부를 확인한 뒤 다시 시도하세요.",
    )


def _initial_ai(config: AppConfig, account: CodexAccountSnapshot | None = None) -> AIData:
    if config.ai.provider == AIProviderKind.CODEX_ACCOUNT.value:
        return account.ai if account is not None else AIData(
            provider=AIProviderKind.CODEX_ACCOUNT,
            title="CODEX",
            status=SyncStatus.DELAYED,
            primary_value="--",
            primary_label="NO LIMIT",
        )
    if config.ai.provider == AIProviderKind.CODEX_LOCAL.value:
        if not config.ai.codex_local_consent:
            return codex_to_ai_data(CodexUsageProvider(consent_granted=False).refresh())
        return AIData(
            provider=AIProviderKind.CODEX_LOCAL,
            title="CODEX LIMITS",
            status=SyncStatus.DELAYED,
            primary_value="READY",
            primary_label="PRESS START",
            fields=(("LOCAL READ", "ENABLED"),),
        )
    return not_configured_ai()


def _provider_title(provider: str) -> str:
    return {
        AIProviderKind.CODEX_ACCOUNT.value: "CODEX",
        AIProviderKind.CODEX_LOCAL.value: "CODEX LIMITS",
        AIProviderKind.OPENAI_API.value: "OPENAI API",
        AIProviderKind.CHATGPT_ACTIVITY.value: "APP ACTIVITY",
        AIProviderKind.NOT_CONFIGURED.value: "NOT CONFIGURED",
    }.get(provider, "NOT CONFIGURED")
