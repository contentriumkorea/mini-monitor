# SPDX-License-Identifier: GPL-3.0-or-later

"""Main-thread-only setup and control center for the desktop application."""

from __future__ import annotations

import ctypes
import itertools
import sys
from ctypes import wintypes
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk

from ..config import (
    DEFAULT_BRIGHTNESS,
    DEFAULT_OVERLAY_OPACITY,
    DEFAULT_OVERLAY_SCALE_PERCENT,
    DEFAULT_TASKBAR_ITEMS,
    MAX_OVERLAY_OPACITY,
    MAX_OVERLAY_SCALE_PERCENT,
    MIN_OVERLAY_OPACITY,
    MIN_OVERLAY_SCALE_PERCENT,
    validate_brightness,
    validate_taskbar_options,
)
from ..models import AIData, AIProviderKind, ConnectionData, ConnectionStatus, SyncStatus
from ..orientation import (
    LANDSCAPE,
    PORTRAIT,
    compose_rotation,
    orientation_spec,
)
from .preview import require_main_thread
from .app_icon import set_window_icon

if TYPE_CHECKING:
    from ..ai.codex_account import CodexAccountSnapshot
    from ..updater import UpdateSnapshot


BG = "#0B0B0B"
CARD = "#171717"
CARD_RAISED = "#242424"
BORDER = "#3B3B3B"
TEXT = "#F4F4F4"
SECONDARY = "#AEAEAE"
DIM = "#797979"
CYAN = "#D6D6D6"
GREEN = "#D6D6D6"
AMBER = "#FFB454"
RED = "#FF6B7A"

_VIEW_LABEL_TO_KEY = {"가로": LANDSCAPE, "세로": PORTRAIT}
_VIEW_KEY_TO_LABEL = {value: label for label, value in _VIEW_LABEL_TO_KEY.items()}
_CHECKMARK_STYLE_IDS = itertools.count(1)


@dataclass(frozen=True, slots=True)
class WindowBounds:
    """Usable monitor work area and non-client frame extents in pixels."""

    left: int
    top: int
    right: int
    bottom: int
    frame_width: int = 0
    frame_height: int = 0

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


def _window_geometry(width: int, height: int, x: int, y: int) -> str:
    """Return a Tk geometry string that preserves virtual-screen negatives."""

    x_part = f"+{x}" if x >= 0 else str(x)
    y_part = f"+{y}" if y >= 0 else str(y)
    return f"{width}x{height}{x_part}{y_part}"


def _set_window_geometry(
    window: tk.Misc,
    width: int,
    height: int,
    x: int,
    y: int,
) -> None:
    """Size a Tk window and place it at absolute virtual-desktop coordinates."""

    window.geometry(f"{width}x{height}")  # type: ignore[attr-defined]
    if sys.platform == "win32":
        try:
            window.update_idletasks()
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_ancestor = user32.GetAncestor
            get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
            get_ancestor.restype = wintypes.HWND
            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = (
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            )
            set_window_pos.restype = wintypes.BOOL
            hwnd = get_ancestor(wintypes.HWND(int(window.winfo_id())), 2)
            if not hwnd:
                hwnd = wintypes.HWND(int(window.winfo_id()))
            if set_window_pos(
                hwnd,
                None,
                int(x),
                int(y),
                0,
                0,
                0x0015,  # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            ):
                return
        except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
            pass
    window.geometry(  # type: ignore[attr-defined]
        _window_geometry(width, height, x, y)
    )


def _window_bounds(window: tk.Misc) -> WindowBounds:
    """Return the active Windows monitor work area, excluding the taskbar."""

    screen = WindowBounds(
        0,
        0,
        int(window.winfo_screenwidth()),
        int(window.winfo_screenheight()),
    )
    if sys.platform != "win32":
        return screen

    class MonitorInfo(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        )

    # The final emergency fallback remains deliberately conservative.  It is
    # used only if user32 itself is unavailable; normal Windows fallback uses
    # SPI_GETWORKAREA below so the taskbar is still excluded.
    fallback = WindowBounds(
        screen.left,
        screen.top,
        screen.right,
        max(screen.top + 1, screen.bottom - 80),
        16,
        48,
    )
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_system_metrics = user32.GetSystemMetrics
        get_system_metrics.argtypes = (ctypes.c_int,)
        get_system_metrics.restype = ctypes.c_int
        frame_x = max(
            0,
            int(get_system_metrics(32)) + int(get_system_metrics(92)),
        )
        frame_y = max(
            0,
            int(get_system_metrics(33)) + int(get_system_metrics(92)),
        )
        caption = max(0, int(get_system_metrics(4)))
        fallback = WindowBounds(
            fallback.left,
            fallback.top,
            fallback.right,
            fallback.bottom,
            max(fallback.frame_width, frame_x * 2),
            max(fallback.frame_height, frame_y * 2 + caption),
        )

        system_parameters_info = user32.SystemParametersInfoW
        system_parameters_info.argtypes = (
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        )
        system_parameters_info.restype = wintypes.BOOL
        primary_work = wintypes.RECT()
        if system_parameters_info(
            0x0030,  # SPI_GETWORKAREA
            0,
            ctypes.byref(primary_work),
            0,
        ):
            fallback = WindowBounds(
                primary_work.left,
                primary_work.top,
                primary_work.right,
                primary_work.bottom,
                fallback.frame_width,
                fallback.frame_height,
            )

        get_ancestor = user32.GetAncestor
        get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
        get_ancestor.restype = wintypes.HWND
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.argtypes = (wintypes.HWND, wintypes.DWORD)
        monitor_from_window.restype = wintypes.HANDLE
        get_monitor_info = user32.GetMonitorInfoW
        get_monitor_info.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(MonitorInfo),
        )
        get_monitor_info.restype = wintypes.BOOL
        get_window_rect = user32.GetWindowRect
        get_window_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_window_rect.restype = wintypes.BOOL
        get_client_rect = user32.GetClientRect
        get_client_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_client_rect.restype = wintypes.BOOL

        hwnd = get_ancestor(wintypes.HWND(int(window.winfo_id())), 2)
        if not hwnd:
            hwnd = wintypes.HWND(int(window.winfo_id()))
        monitor = monitor_from_window(hwnd, 2)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        outer = wintypes.RECT()
        client = wintypes.RECT()
        if not (monitor and get_monitor_info(monitor, ctypes.byref(info))):
            return fallback
        frame_width = fallback.frame_width
        frame_height = fallback.frame_height
        if get_window_rect(hwnd, ctypes.byref(outer)) and get_client_rect(
            hwnd,
            ctypes.byref(client),
        ):
            frame_width = max(
                0,
                (outer.right - outer.left) - (client.right - client.left),
            )
            frame_height = max(
                0,
                (outer.bottom - outer.top) - (client.bottom - client.top),
            )
        return WindowBounds(
            info.rcWork.left,
            info.rcWork.top,
            info.rcWork.right,
            info.rcWork.bottom,
            frame_width,
            frame_height,
        )
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        return fallback


def _checkbox_icon(size: int, *, checked: bool, disabled: bool) -> Image.Image:
    """Return a DPI-sized checkbox with an unambiguous tick, never an X."""

    size = max(12, int(size))
    gap = max(5, round(size * 0.38))
    supersample = 4
    box = size * supersample
    image = Image.new(
        "RGBA",
        ((size + gap) * supersample, box),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)
    border = DIM if disabled else (CYAN if checked else SECONDARY)
    fill = CARD_RAISED if checked else CARD
    inset = supersample
    draw.rounded_rectangle(
        (inset, inset, box - inset - 1, box - inset - 1),
        radius=max(2, round(size * 0.18)) * supersample,
        fill=fill,
        outline=border,
        width=max(1, round(size / 16)) * supersample,
    )
    if checked:
        mark = DIM if disabled else CYAN
        draw.line(
            (
                round(size * 0.23) * supersample,
                round(size * 0.52) * supersample,
                round(size * 0.43) * supersample,
                round(size * 0.72) * supersample,
                round(size * 0.79) * supersample,
                round(size * 0.29) * supersample,
            ),
            fill=mark,
            width=max(2, round(size * 0.13)) * supersample,
            joint="curve",
        )
    return image.resize((size + gap, size), Image.Resampling.LANCZOS)


@dataclass(frozen=True, slots=True)
class SetupSelection:
    """Non-secret settings plus an optional one-shot Admin Key entry."""

    provider: str
    codex_local_consent: bool
    openai_admin_key: str = ""
    usage_refresh_seconds: int = 60
    cost_refresh_seconds: int = 600
    daily_budget_usd: float | None = None
    monthly_budget_usd: float | None = None
    rotation: str = LANDSCAPE
    brightness: int = DEFAULT_BRIGHTNESS


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    title: str
    detail: str = ""
    pending: bool = False
    state_uncertain: bool = False
    overlay_settings: OverlaySettings | None = None


@dataclass(frozen=True, slots=True)
class OverlaySettings:
    """User-facing desktop overlay settings (position is drag-managed)."""

    enabled: bool
    opacity: float
    scale_percent: int


Action = Callable[[], ActionResult]
SelectionAction = Callable[[SetupSelection], ActionResult]
ToggleAction = Callable[[bool], ActionResult]
BrightnessAction = Callable[[int], ActionResult]
OverlayAction = Callable[[OverlaySettings], ActionResult]
TaskbarOptionsAction = Callable[[tuple[str, ...], str], ActionResult]
TaskbarPositionGetter = Callable[[], tuple[float, int] | None]
TaskbarPositionAction = Callable[[str, float], ActionResult]

_TASKBAR_STYLE_LABELS = {"icon": "아이콘", "text": "텍스트", "both": "아이콘+텍스트"}
_TASKBAR_LABEL_STYLES = {label: style for style, label in _TASKBAR_STYLE_LABELS.items()}


class SetupWindow:
    """Separate setup window with an embedded native-size framebuffer preview."""

    def __init__(
        self,
        *,
        provider: str,
        codex_local_consent: bool,
        on_detect_device: Action,
        on_check_usage: SelectionAction,
        on_start: SelectionAction,
        on_stop: Action,
        on_reconnect: Action,
        on_exit: Callable[[], None],
        enable_serial: bool = True,
        openai_key_configured: bool = False,
        usage_refresh_seconds: int = 60,
        cost_refresh_seconds: int = 600,
        daily_budget_usd: float | None = None,
        monthly_budget_usd: float | None = None,
        rotation: str = LANDSCAPE,
        on_orientation_change: Callable[[str], None] | None = None,
        brightness: int = DEFAULT_BRIGHTNESS,
        on_brightness_change: BrightnessAction | None = None,
        autostart_enabled: bool = False,
        autostart_needs_repair: bool = False,
        autostart_state_unknown: bool = False,
        on_autostart_change: ToggleAction | None = None,
        overlay_enabled: bool = False,
        overlay_opacity: float = DEFAULT_OVERLAY_OPACITY,
        overlay_scale_percent: int = DEFAULT_OVERLAY_SCALE_PERCENT,
        on_overlay_change: OverlayAction | None = None,
        on_overlay_reset_position: Action | None = None,
        taskbar_enabled: bool = False,
        on_taskbar_change: ToggleAction | None = None,
        taskbar_items: tuple[str, ...] | list[str] = DEFAULT_TASKBAR_ITEMS,
        taskbar_style: str = "icon",
        on_taskbar_options: TaskbarOptionsAction | None = None,
        get_taskbar_position: TaskbarPositionGetter | None = None,
        on_taskbar_position: TaskbarPositionAction | None = None,
        on_codex_login: Action | None = None,
        on_codex_cancel: Action | None = None,
        on_codex_logout: Action | None = None,
        on_codex_disconnect: Action | None = None,
        on_codex_cli_selected: Callable[[Path], ActionResult] | None = None,
        on_codex_install_guide: Action | None = None,
        on_update_check: Action | None = None,
        on_update_apply: Action | None = None,
        on_update_dismiss: Action | None = None,
        on_update_open_release: Action | None = None,
    ) -> None:
        require_main_thread()
        initial_brightness = validate_brightness(brightness)
        display_orientation = orientation_spec(rotation)
        selected_taskbar_items = validate_taskbar_options(taskbar_items, taskbar_style)
        if not isinstance(overlay_enabled, bool):
            raise ValueError("overlay_enabled must be true or false")
        if (
            isinstance(overlay_scale_percent, bool)
            or not isinstance(overlay_scale_percent, int)
            or not MIN_OVERLAY_SCALE_PERCENT
            <= overlay_scale_percent
            <= MAX_OVERLAY_SCALE_PERCENT
        ):
            raise ValueError("invalid overlay scale")
        if (
            isinstance(overlay_opacity, bool)
            or not isinstance(overlay_opacity, (int, float))
            or not MIN_OVERLAY_OPACITY
            <= float(overlay_opacity)
            <= MAX_OVERLAY_OPACITY
        ):
            raise ValueError("invalid overlay opacity")
        self._window = tk.Tk(className="AIMiniMonitorSetup")
        # Hide immediately so --minimized and startup registration never flash
        # an unconfigured root window before the caller chooses to show it.
        self._window.withdraw()
        set_window_icon(self._window)
        self._closed = False
        self._photo: ImageTk.PhotoImage | None = None
        self._preview_source: Image.Image | None = None
        self._preview_available_size: tuple[int, int] | None = None
        self._on_detect_device = on_detect_device
        self._on_check_usage = on_check_usage
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_reconnect = on_reconnect
        self._on_orientation_change = on_orientation_change
        self._on_brightness_change = on_brightness_change
        self._on_autostart_change = on_autostart_change
        self._on_overlay_change = on_overlay_change
        self._on_overlay_reset_position = on_overlay_reset_position
        self._taskbar_enabled = bool(taskbar_enabled)
        self._on_taskbar_change = on_taskbar_change
        self._taskbar_committed_items = selected_taskbar_items
        self._taskbar_committed_style = taskbar_style
        self._on_taskbar_options = on_taskbar_options
        self._get_taskbar_position = get_taskbar_position
        self._on_taskbar_position = on_taskbar_position
        self._taskbar_position_syncing = False
        self._on_codex_login = on_codex_login
        self._on_codex_cancel = on_codex_cancel
        self._on_codex_logout = on_codex_logout
        self._on_codex_disconnect = on_codex_disconnect
        self._on_codex_cli_selected = on_codex_cli_selected
        self._on_codex_install_guide = on_codex_install_guide
        self._on_update_check = on_update_check
        self._on_update_apply = on_update_apply
        self._on_update_dismiss = on_update_dismiss
        self._on_update_open_release = on_update_open_release
        self._on_exit = on_exit
        self._enable_serial = enable_serial
        self._openai_key_configured = bool(openai_key_configured)
        self._openai_dialog: tk.Toplevel | None = None
        self._overlay_dialog: tk.Toplevel | None = None
        self._overlay_dialog_size = (480, 390)
        self._taskbar_options_dialog: tk.Toplevel | None = None
        checkmark_prefix = f"AIMiniMonitorCheck{next(_CHECKMARK_STYLE_IDS)}"
        self._checkbutton_style = f"{checkmark_prefix}.TCheckbutton"
        self._checkmark_element = f"{checkmark_prefix}.indicator"
        self._checkmark_images: dict[str, ImageTk.PhotoImage] = {}
        self._checkmark_bitmaps: dict[str, Image.Image] = {}

        # Legacy providers remain in persisted config for migration, but are
        # not selectable from this account-only setup window.
        selected = AIProviderKind.CODEX_ACCOUNT.value
        self._provider = tk.StringVar(self._window, selected)
        self._admin_key = tk.StringVar(self._window, "")
        self._orientation_view = tk.StringVar(
            self._window, _VIEW_KEY_TO_LABEL[display_orientation.view]
        )
        self._orientation_inverted = tk.BooleanVar(
            self._window, display_orientation.inverted
        )
        self._brightness = tk.IntVar(self._window, initial_brightness)
        self._brightness_committed = initial_brightness
        self._brightness_after_id: str | None = None
        self._brightness_adjusting = False
        self._autostart_enabled = tk.BooleanVar(
            self._window, bool(autostart_enabled)
        )
        self._autostart_committed = bool(autostart_enabled)
        self._autostart_needs_repair = bool(autostart_needs_repair)
        self._autostart_state_unknown = bool(autostart_state_unknown)
        self._overlay_enabled = tk.BooleanVar(self._window, overlay_enabled)
        self._overlay_opacity_percent = tk.IntVar(
            self._window, round(float(overlay_opacity) * 100)
        )
        self._overlay_scale_percent = tk.IntVar(
            self._window, overlay_scale_percent
        )
        self._overlay_committed = OverlaySettings(
            overlay_enabled,
            float(overlay_opacity),
            overlay_scale_percent,
        )
        self._overlay_after_id: str | None = None
        self._overlay_action_busy = False
        self._last_valid_usage_refresh = int(usage_refresh_seconds)
        self._last_valid_cost_refresh = int(cost_refresh_seconds)
        self._last_valid_daily_budget = daily_budget_usd
        self._last_valid_monthly_budget = monthly_budget_usd
        self._running = False
        self._usage_busy = False
        self._lifecycle_busy = False
        self._brightness_busy = False
        self._blocked = False

        try:
            self._configure_window()
            self._configure_styles()
            self._build()
            self._provider.trace_add("write", self._provider_changed)
            self._orientation_view.trace_add("write", self._orientation_changed)
            self._orientation_inverted.trace_add("write", self._orientation_changed)
            self._provider_changed()
            self.set_running(False)
        except Exception:
            # A Tk construction error must not leave an invisible root behind
            # holding the process open or the single-instance mutex occupied.
            self._closed = True
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            raise

    @property
    def window(self) -> tk.Tk:
        return self._window

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def visible(self) -> bool:
        require_main_thread()
        return not self._closed and bool(self._window.winfo_viewable())

    @property
    def selection(self) -> SetupSelection:
        require_main_thread()
        provider = self._provider.get()
        return SetupSelection(
            provider=provider,
            codex_local_consent=False,
            openai_admin_key="",
            usage_refresh_seconds=self._last_valid_usage_refresh,
            cost_refresh_seconds=self._last_valid_cost_refresh,
            daily_budget_usd=self._last_valid_daily_budget,
            monthly_budget_usd=self._last_valid_monthly_budget,
            rotation=self._selected_rotation(),
            brightness=validate_brightness(self._brightness.get()),
        )

    @property
    def selected_brightness(self) -> int:
        require_main_thread()
        return validate_brightness(self._brightness.get())

    @property
    def selected_provider(self) -> str:
        """Return only the radio value without parsing in-progress form input."""

        require_main_thread()
        return self._provider.get()

    def set_provider(self, provider: str) -> None:
        """Select a known data source without reaching into Tk internals."""

        require_main_thread()
        self._ensure_open()
        if provider != AIProviderKind.CODEX_ACCOUNT.value:
            raise ValueError("invalid provider")
        self._provider.set(provider)

    @property
    def selected_rotation(self) -> str:
        require_main_thread()
        return self._selected_rotation()

    @property
    def overlay_settings(self) -> OverlaySettings:
        require_main_thread()
        return OverlaySettings(
            bool(self._overlay_enabled.get()),
            self._overlay_opacity_percent.get() / 100.0,
            int(self._overlay_scale_percent.get()),
        )

    def set_overlay_settings(self, settings: OverlaySettings) -> None:
        """Reflect a committed app/tray overlay state without re-submitting it."""

        require_main_thread()
        self._overlay_committed = settings
        self._overlay_enabled.set(settings.enabled)
        self._overlay_opacity_percent.set(round(settings.opacity * 100))
        self._overlay_scale_percent.set(settings.scale_percent)
        if hasattr(self, "_overlay_quick_button"):
            self._sync_overlay_quick_button()
            self._overlay_mode_status.configure(
                text="켜짐" if settings.enabled else "꺼짐"
            )
        if hasattr(self, "_overlay_dialog_visibility"):
            self._overlay_dialog_visibility.configure(
                text="표시 중" if settings.enabled else "숨김"
            )
        if hasattr(self, "_overlay_opacity_value"):
            self._overlay_opacity_value.configure(
                text=f"{round(settings.opacity * 100)}%"
            )
            self._overlay_scale_value.configure(
                text=f"{settings.scale_percent}%"
            )

    def set_taskbar_enabled(self, enabled: bool) -> None:
        """Reflect an externally committed compact-bar visibility state."""

        require_main_thread()
        self._ensure_open()
        self._taskbar_enabled = bool(enabled)
        self._taskbar_button.configure(
            text="작업 표시줄 바 끄기" if self._taskbar_enabled else "작업 표시줄 바 켜기"
        )
        self._taskbar_mode_status.configure(
            text="켜짐" if self._taskbar_enabled else "꺼짐"
        )

    @property
    def taskbar_options(self) -> tuple[tuple[str, ...], str]:
        """The last committed subset and display style."""

        return self._taskbar_committed_items, self._taskbar_committed_style

    def set_taskbar_options(self, items: tuple[str, ...] | list[str], style: str) -> None:
        """Reflect options saved by the app or restored on startup."""

        require_main_thread()
        self._ensure_open()
        self._taskbar_committed_items = validate_taskbar_options(items, style)
        self._taskbar_committed_style = style
        if hasattr(self, "_taskbar_hint"):
            self._sync_taskbar_hint()
        if self._taskbar_options_dialog is not None:
            for name, variable in self._taskbar_item_vars.items():
                variable.set(name in self._taskbar_committed_items)
            self._taskbar_style_var.set(_TASKBAR_STYLE_LABELS[style])
            self._update_taskbar_selection_count()

    def show_overlay_recovery(self) -> None:
        """Explain an automatic fail-closed hide in the reserved feedback lane."""

        require_main_thread()
        self._ensure_open()
        self._overlay_quick_status.configure(
            text="상태창 그래픽 오류\nPC 상태창 버튼으로 다시 시도",
            foreground=RED,
        )

    def clear_overlay_recovery(self) -> None:
        """Clear stale automatic-error feedback after any successful action."""

        require_main_thread()
        self._ensure_open()
        self._overlay_quick_status.configure(text=" ")

    def show(self) -> None:
        require_main_thread()
        self._ensure_open()
        self._fit_main_window()
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()

    def hide(self) -> None:
        require_main_thread()
        if not self._closed:
            self._window.withdraw()

    def close(self) -> None:
        require_main_thread()
        if self._closed:
            return
        self._cancel_brightness_schedule()
        self._cancel_overlay_schedule()
        self._closed = True
        self._photo = None
        self._preview_source = None
        self._admin_key.set("")
        if self._overlay_dialog is not None:
            try:
                if self._overlay_dialog.grab_current() is self._overlay_dialog:
                    self._overlay_dialog.grab_release()
            except tk.TclError:
                pass
            self._overlay_dialog.destroy()
            self._overlay_dialog = None
        if self._taskbar_options_dialog is not None:
            try:
                if self._taskbar_options_dialog.grab_current() is self._taskbar_options_dialog:
                    self._taskbar_options_dialog.grab_release()
            except tk.TclError:
                pass
            self._taskbar_options_dialog.destroy()
            self._taskbar_options_dialog = None
        if hasattr(self, "_update_dialog"):
            self._update_dialog.destroy()
        self._window.destroy()

    def update_image(self, image: Image.Image) -> None:
        require_main_thread()
        self._ensure_open()
        if not isinstance(image, Image.Image):
            raise TypeError("setup preview must be a Pillow Image")
        rgb = image.convert("RGB")
        if rgb.size not in {(480, 320), (320, 480)}:
            raise ValueError("setup preview must be exactly 480x320 or 320x480")
        self._preview_source = rgb
        self._preview_available_size = None
        self._render_preview()
        self._preview_detail.configure(
            text=(
                f"선택한 {rgb.width}×{rgb.height} 화면입니다. "
                "설정창에서는 비율을 유지해 축소하며, PC 상태창도 같은 내용을 표시합니다."
            )
        )

    def _render_preview(self) -> None:
        if self._preview_source is None:
            return
        # The physical framebuffer stays 480x320 or 320x480; only this
        # settings-window copy shrinks to leave the detail controls visible.
        pane_width = self._preview_detail.master.winfo_width()
        available_width = min(480, max(1, pane_width - 30)) if pane_width > 1 else 480
        viewport_height = self._right_canvas.winfo_height()
        if viewport_height > 1:
            preview_border = self._preview_label.master
            fixed_height = max(
                0,
                self._right_scroll_content.winfo_reqheight() - preview_border.winfo_reqheight(),
            )
            available_height = min(320, max(80, viewport_height - fixed_height - 4))
        else:
            available_height = 320
        available_size = (available_width, available_height)
        if available_size == self._preview_available_size and self._photo is not None:
            return
        self._preview_available_size = available_size
        fitted = ImageOps.contain(
            self._preview_source,
            available_size,
            Image.Resampling.LANCZOS,
        )
        self._photo = ImageTk.PhotoImage(fitted, master=self._window)
        self._preview_label.configure(image=self._photo)

    def _resize_preview_pane(self, _event: tk.Event) -> None:
        pane_width = self._preview_detail.master.winfo_width()
        if pane_width > 1:
            self._preview_detail.configure(wraplength=max(1, pane_width - 28))
        self._render_preview()

    def set_device_result(self, result: ActionResult) -> None:
        require_main_thread()
        color = GREEN if result.ok else RED
        self._device_title.configure(text=result.title, foreground=color)
        self._device_detail.configure(text=result.detail or " ")
        self._set_dot(self._device_dot, color)

    def complete_device(self, result: ActionResult) -> None:
        require_main_thread()
        self.set_device_result(result)
        self._device_button.state(["!disabled"])

    def set_usage_result(self, result: ActionResult) -> None:
        require_main_thread()
        color = GREEN if result.ok else AMBER
        self._usage_status.configure(text=result.title, foreground=color)
        self._usage_detail.configure(text=result.detail or " ")
        self._set_codex_feedback(
            result.title + (f" · {result.detail}" if result.detail else ""), color,
        )

    def complete_usage(self, result: ActionResult) -> None:
        require_main_thread()
        self._usage_busy = False
        self.set_usage_result(result)
        self._usage_button.state(
            ["disabled"] if self._brightness_busy else ["!disabled"]
        )
        if not self._running and not self._lifecycle_busy and not self._brightness_busy:
            self._start_button.state(["!disabled"])
        self._set_settings_enabled(
            not self._running
            and not self._lifecycle_busy
            and not self._brightness_busy
        )
        self._update_brightness_enabled()

    def complete_start(self, result: ActionResult, *, running: bool) -> None:
        require_main_thread()
        self._lifecycle_busy = False
        # Admin Keys are one-shot UI input. The worker may already have copied
        # the immutable selection, but the Tk variable must never retain it.
        self.clear_secret_entry()
        self.set_running(running)
        if result.ok:
            self._brightness_committed = self.selected_brightness
        self._usage_button.state(
            ["disabled"] if self._brightness_busy else ["!disabled"]
        )
        self.set_device_result(result)
        if not result.ok:
            label = "포트 사용 중" if result.title.startswith("PORT IN USE") else "시작 실패"
            self._run_status.configure(text=label, foreground=RED)
            self._set_dot(self._run_dot, RED)

    def clear_secret_entry(self) -> None:
        require_main_thread()
        self._admin_key.set("")

    def set_openai_key_configured(self, configured: bool) -> None:
        """Accept legacy app notifications without exposing an API key form."""
        require_main_thread()
        self._openai_key_configured = bool(configured)

    def complete_stop(
        self,
        result: ActionResult,
        *,
        running: bool,
        blocked: bool = False,
    ) -> None:
        require_main_thread()
        self._lifecycle_busy = False
        if blocked:
            self.set_blocked()
        else:
            self.set_running(running)
        self._device_title.configure(text=result.title, foreground=GREEN if result.ok else RED)
        self._device_detail.configure(text=result.detail or " ")
        if not blocked:
            self._usage_button.state(
                ["disabled"] if self._brightness_busy else ["!disabled"]
            )

    def complete_brightness(
        self,
        result: ActionResult,
        *,
        brightness: int,
    ) -> None:
        """Complete the latest persisted/live brightness request on Tk."""

        require_main_thread()
        value = validate_brightness(brightness)
        current = self.selected_brightness
        newer_target_pending = (
            self._brightness_after_id is not None or current != value
        )
        if newer_target_pending:
            if result.ok:
                self._brightness_committed = value
            self._brightness_busy = True
            self._brightness_value.configure(text=f"{current}%", foreground=CYAN)
            self._start_button.state(["disabled"])
            self._usage_button.state(["disabled"])
            self._set_settings_enabled(False)
            self._update_brightness_enabled()
            return

        self._brightness_busy = False
        if result.ok:
            self._brightness_committed = value
            color = GREEN
        else:
            color = RED
            if current == value:
                self._set_brightness_value(self._brightness_committed)
                current = self._brightness_committed
        self._brightness_value.configure(text=f"{current}%", foreground=color)
        self._usage_button.state(
            ["disabled"] if self._usage_busy or self._lifecycle_busy else ["!disabled"]
        )
        if not self._running:
            self._start_button.state(
                ["disabled"]
                if self._usage_busy or self._lifecycle_busy
                else ["!disabled"]
            )
        self._set_settings_enabled(
            not self._running and not self._usage_busy and not self._lifecycle_busy
        )
        self._update_brightness_enabled()

    def set_blocked(self) -> None:
        """Prevent a second controller after an incomplete one-shot cleanup."""

        require_main_thread()
        self._blocked = True
        self._running = False
        self._run_status.configure(text="종료 후 다시 실행 필요", foreground=RED)
        self._set_dot(self._run_dot, RED)
        self._start_button.state(["disabled"])
        self._stop_button.state(["disabled"])
        self._reconnect_button.state(["disabled"])
        self._set_settings_enabled(False)
        self._set_brightness_enabled(False)

    def set_running(self, running: bool) -> None:
        require_main_thread()
        self._blocked = False
        self._running = bool(running)
        self._monitor_mode_status.configure(text="실행 중" if running else "중지됨")
        if running:
            self._run_status.configure(text="실행 중", foreground=GREEN)
            self._set_dot(self._run_dot, GREEN)
            self._start_button.state(["disabled"])
            self._stop_button.state(["!disabled"])
            self._reconnect_button.state(["!disabled"] if self._enable_serial else ["disabled"])
            self._set_settings_enabled(False)
        else:
            self._run_status.configure(text="설정 대기", foreground=SECONDARY)
            self._set_dot(self._run_dot, DIM)
            self._start_button.state(
                ["disabled"]
                if self._lifecycle_busy or self._usage_busy or self._brightness_busy
                else ["!disabled"]
            )
            self._stop_button.state(["disabled"])
            self._reconnect_button.state(["disabled"])
            self._set_settings_enabled(
                not self._usage_busy
                and not self._lifecycle_busy
                and not self._brightness_busy
            )
        self._update_brightness_enabled()

    def update_runtime(
        self,
        connection: ConnectionData,
        ai: AIData,
        *,
        running: bool,
    ) -> None:
        require_main_thread()
        self.set_running(running)
        if not running:
            return
        connection_color = {
            ConnectionStatus.ONLINE: GREEN,
            ConnectionStatus.RECONNECTING: AMBER,
            ConnectionStatus.DISCONNECTED: RED,
        }[connection.status]
        connection_text = {
            ConnectionStatus.ONLINE: "연결됨",
            ConnectionStatus.RECONNECTING: "다시 연결 중",
            ConnectionStatus.DISCONNECTED: "연결 끊김",
        }[connection.status]
        if connection.port:
            connection_text = f"{connection_text} · {connection.port}"
        connection_detail = connection.detail or "정확한 USB 장치 식별 후 전송 중"
        if connection.detail == "PORT IN USE":
            connection_text = "PORT IN USE" + (
                f" · {connection.port}" if connection.port else ""
            )
            connection_detail = (
                "UsbMonitor 또는 다른 직렬 앱을 트레이까지 완전히 종료한 뒤 "
                "‘장치 다시 연결’을 누르세요."
            )
            self._run_status.configure(text="연결 복구 필요", foreground=RED)
            self._set_dot(self._run_dot, RED)
        self._device_title.configure(text=connection_text, foreground=connection_color)
        self._device_detail.configure(text=connection_detail)
        self._set_dot(self._device_dot, connection_color)

        ai_color = {
            SyncStatus.OK: GREEN,
            SyncStatus.DELAYED: AMBER,
            SyncStatus.AUTH_ERROR: RED,
            SyncStatus.RATE_LIMITED: AMBER,
            SyncStatus.NETWORK_ERROR: RED,
            SyncStatus.SETUP_REQUIRED: AMBER,
        }[ai.status]
        self._usage_status.configure(
            text=f"{ai.title} · {ai.primary_value} {ai.primary_label}".strip(),
            foreground=ai_color,
        )
        detail = " · ".join(f"{label} {value}" for label, value in ai.fields[:2])
        status_copy = {
            SyncStatus.OK: "정상",
            SyncStatus.DELAYED: "갱신 지연",
            SyncStatus.AUTH_ERROR: "로그인 필요",
            SyncStatus.RATE_LIMITED: "요청 제한",
            SyncStatus.NETWORK_ERROR: "네트워크 오류",
            SyncStatus.SETUP_REQUIRED: "계정 연결 필요",
        }[ai.status]
        self._usage_detail.configure(text=detail or status_copy)

    def _configure_window(self) -> None:
        self._window.title("Mini Monitor 설정")
        self._window.configure(background=BG)
        self._window.geometry("960x700")
        self._window.minsize(920, 600)
        self._window.protocol("WM_DELETE_WINDOW", self.hide)
        self._window.option_add("*Font", "{Malgun Gothic} 10")

    def _fit_main_window(self) -> None:
        """Fit inside the active monitor work area without clipping controls."""

        require_main_thread()
        if self._closed:
            return
        self._window.update_idletasks()
        bounds = _window_bounds(self._window)
        max_width = max(1, bounds.width - bounds.frame_width)
        max_height = max(1, bounds.height - bounds.frame_height)
        width = max(960, self._window.winfo_reqwidth())
        height = 720
        width = min(width, max_width)
        height = min(height, max_height)
        self._window.minsize(min(920, max_width), min(600, max_height))
        outer_width = width + bounds.frame_width
        outer_height = height + bounds.frame_height
        x = bounds.left + max(0, (bounds.width - outer_width) // 2)
        y = bounds.top + max(0, (bounds.height - outer_height) // 2)
        _set_window_geometry(self._window, width, height, x, y)

    def _configure_styles(self) -> None:
        style = ttk.Style(self._window)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Raised.TFrame", background=CARD_RAISED)
        style.configure("App.TLabel", background=BG, foreground=TEXT)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Malgun Gothic", 19, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=SECONDARY, font=("Malgun Gothic", 10))
        style.configure("CardTitle.TLabel", background=CARD, foreground=TEXT, font=("Malgun Gothic", 12, "bold"))
        style.configure("CardText.TLabel", background=CARD, foreground=SECONDARY, font=("Malgun Gothic", 9))
        style.configure("Status.TLabel", background=CARD, foreground=SECONDARY, font=("Malgun Gothic", 10, "bold"))
        style.configure("Primary.TButton", background=CYAN, foreground=BG, bordercolor=CYAN, padding=(12, 6), font=("Malgun Gothic", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#F0F0F0"), ("disabled", BORDER)], foreground=[("disabled", DIM)])
        style.configure("Secondary.TButton", background=CARD_RAISED, foreground=TEXT, bordercolor=BORDER, padding=(9, 5), font=("Malgun Gothic", 9, "bold"))
        style.map("Secondary.TButton", background=[("active", "#333333"), ("disabled", CARD)], foreground=[("disabled", DIM)])
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=CARD_RAISED,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=SECONDARY,
            lightcolor=CARD_RAISED,
            darkcolor=CARD_RAISED,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", BORDER), ("pressed", CYAN)],
            arrowcolor=[("active", TEXT), ("disabled", DIM)],
        )
        style.configure(
            "Brightness.Horizontal.TScale",
            background=CARD,
            troughcolor=BORDER,
            bordercolor=CARD,
            lightcolor=CYAN,
            darkcolor=CYAN,
        )
        style.map(
            "Brightness.Horizontal.TScale",
            background=[("disabled", CARD), ("active", CYAN)],
            troughcolor=[("disabled", CARD_RAISED)],
        )
        self._configure_checkmark_style(style)

    def _configure_checkmark_style(self, style: ttk.Style) -> None:
        scale = float(self._window.tk.call("tk", "scaling")) / (96.0 / 72.0)
        icon_size = max(16, min(28, round(16 * scale)))
        self._checkmark_bitmaps = {
            "unchecked": _checkbox_icon(icon_size, checked=False, disabled=False),
            "checked": _checkbox_icon(icon_size, checked=True, disabled=False),
            "disabled_unchecked": _checkbox_icon(
                icon_size,
                checked=False,
                disabled=True,
            ),
            "disabled_checked": _checkbox_icon(
                icon_size,
                checked=True,
                disabled=True,
            ),
        }
        self._checkmark_images = {
            name: ImageTk.PhotoImage(bitmap, master=self._window)
            for name, bitmap in self._checkmark_bitmaps.items()
        }
        style.element_create(
            self._checkmark_element,
            "image",
            self._checkmark_images["unchecked"],
            (
                "disabled",
                "selected",
                self._checkmark_images["disabled_checked"],
            ),
            ("disabled", self._checkmark_images["disabled_unchecked"]),
            ("selected", self._checkmark_images["checked"]),
        )
        style.layout(
            self._checkbutton_style,
            [
                (
                    "Checkbutton.padding",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                self._checkmark_element,
                                {"side": "left", "sticky": ""},
                            ),
                            (
                                "Checkbutton.focus",
                                {
                                    "side": "left",
                                    "sticky": "w",
                                    "children": [
                                        (
                                            "Checkbutton.label",
                                            {"sticky": "nswe"},
                                        )
                                    ],
                                },
                            ),
                        ],
                    },
                )
            ],
        )
        style.configure(
            self._checkbutton_style,
            background=CARD,
            foreground=SECONDARY,
            padding=(3, 4),
            font=("Malgun Gothic", 9),
        )
        style.map(
            self._checkbutton_style,
            background=[("active", CARD)],
            foreground=[("disabled", DIM)],
        )

    def _build(self) -> None:
        root = ttk.Frame(self._window, style="App.TFrame", padding=(20, 14, 20, 14))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0, minsize=360)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="App.TFrame")
        self._header = header
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Mini Monitor", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="USB 디스플레이 설정 및 실시간 상태", style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        run_box = ttk.Frame(header, style="App.TFrame")
        run_box.grid(row=0, column=1, rowspan=2, sticky="e")
        self._run_dot = tk.Canvas(run_box, width=12, height=12, background=BG, highlightthickness=0)
        self._run_dot.pack(side="left", padx=(0, 8))
        self._run_status = ttk.Label(run_box, text="설정 대기", style="App.TLabel", font=("Malgun Gothic", 10, "bold"))
        self._run_status.pack(side="left")

        self._build_display_controls(root)

        left = ttk.Frame(root, style="App.TFrame")
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 18))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        scroll_shell = ttk.Frame(left, style="App.TFrame")
        scroll_shell.grid(row=0, column=0, sticky="nsew")
        scroll_shell.columnconfigure(0, weight=1)
        scroll_shell.rowconfigure(0, weight=1)
        self._left_canvas = tk.Canvas(
            scroll_shell,
            background=BG,
            borderwidth=0,
            highlightthickness=0,
            height=1,
        )
        self._left_canvas.grid(row=0, column=0, sticky="nsew")
        self._left_scrollbar = ttk.Scrollbar(
            scroll_shell,
            orient="vertical",
            command=self._left_canvas.yview,
            takefocus=True,
            style="Dark.Vertical.TScrollbar",
        )
        self._left_canvas.configure(yscrollcommand=self._left_scrollbar.set)
        self._left_scrollbar_visible = False
        self._left_scroll_content = ttk.Frame(
            self._left_canvas,
            style="App.TFrame",
        )
        self._left_scroll_content.columnconfigure(0, weight=1)
        self._left_scroll_window = self._left_canvas.create_window(
            (0, 0),
            anchor="nw",
            window=self._left_scroll_content,
        )
        self._left_scroll_content.bind(
            "<Configure>",
            self._sync_left_scroll_region,
            add="+",
        )
        self._left_canvas.bind(
            "<Configure>",
            self._resize_left_viewport,
            add="+",
        )
        self._build_device_card(self._left_scroll_content)
        self._build_codex_account_card(self._left_scroll_content)
        self._build_usage_card(self._codex_account_card)
        self._build_update_banner()
        self._bind_left_viewport_interactions(self._left_scroll_content)
        self._left_canvas.bind(
            "<MouseWheel>",
            self._scroll_left_mousewheel,
            add="+",
        )
        self._build_controls(left)

        right_shell = ttk.Frame(root, style="Card.TFrame")
        right_shell.grid(row=2, column=1, sticky="nsew")
        right_shell.columnconfigure(0, weight=1)
        right_shell.rowconfigure(0, weight=1)
        self._right_canvas = tk.Canvas(
            right_shell,
            background=CARD,
            borderwidth=0,
            highlightthickness=0,
            height=1,
        )
        self._right_canvas.grid(row=0, column=0, sticky="nsew")
        self._right_scrollbar = ttk.Scrollbar(
            right_shell,
            orient="vertical",
            command=self._right_canvas.yview,
            takefocus=True,
            style="Dark.Vertical.TScrollbar",
        )
        self._right_canvas.configure(yscrollcommand=self._right_scrollbar.set)
        self._right_scrollbar_visible = False
        right = ttk.Frame(self._right_canvas, style="Card.TFrame", padding=14)
        self._right_scroll_content = right
        self._right_scroll_window = self._right_canvas.create_window(
            (0, 0), anchor="nw", window=right,
        )
        right.bind("<Configure>", self._sync_right_scroll_region, add="+")
        self._right_canvas.bind("<Configure>", self._resize_right_viewport, add="+")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="미니 모니터 화면", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self._preview_detail = ttk.Label(
            right,
            text="선택한 가로·세로 방향과 같은 비율의 화면입니다. CPU·GPU·메모리 게이지와 Codex 한도를 함께 표시합니다.",
            style="CardText.TLabel",
            wraplength=500,
            justify="left",
        )
        self._preview_detail.grid(row=1, column=0, sticky="w", pady=(3, 12))
        preview_border = tk.Frame(right, background=BORDER, padx=1, pady=1)
        preview_border.grid(row=2, column=0, sticky="n", pady=(0, 12))
        self._preview_label = tk.Label(preview_border, background=BG, borderwidth=0, highlightthickness=0)
        self._preview_label.pack()
        right.bind("<Configure>", self._resize_preview_pane, add="+")
        preview_actions = ttk.Frame(right, style="Card.TFrame")
        preview_actions.grid(row=3, column=0, sticky="ew")
        preview_actions.columnconfigure(0, weight=1)
        self._overlay_options_button = ttk.Button(
            preview_actions,
            text="PC 상태창 설정",
            style="Secondary.TButton",
            command=self._show_overlay_options,
        )
        self._overlay_options_button.grid(
            row=0,
            column=0,
            sticky="ew",
        )
        self._taskbar_options_button = ttk.Button(
            preview_actions,
            text="작업표시줄 바 설정",
            style="Secondary.TButton",
            command=self._show_taskbar_options,
        )
        self._taskbar_options_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        if self._on_taskbar_options is None:
            self._taskbar_options_button.state(["disabled"])
        self._taskbar_hint = ttk.Label(
            preview_actions,
            text="",
            style="CardText.TLabel",
            wraplength=260,
            justify="left",
        )
        self._taskbar_hint.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._sync_taskbar_hint()
        self._reconnect_button = ttk.Button(preview_actions, text="장치 다시 연결", style="Secondary.TButton", command=self._reconnect)
        self._reconnect_button.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._bind_right_viewport_interactions(right)
        self._right_canvas.bind("<MouseWheel>", self._scroll_right_mousewheel, add="+")
        self._create_overlay_dialog()
        self._sync_overlay_quick_button()

    def _build_display_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="App.TFrame")
        self._display_controls = controls
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for column in range(3):
            controls.columnconfigure(column, weight=1, uniform="display_modes")

        groups: list[ttk.Frame] = []
        for column, title in enumerate(("미니 모니터", "PC 오버레이", "작업표시줄 바")):
            group = ttk.Frame(controls, style="Card.TFrame", padding=(8, 6))
            group.grid(row=0, column=column, sticky="nsew", padx=5)
            group.columnconfigure(0, weight=1)
            ttk.Label(group, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            groups.append(group)
        self._monitor_control_group, self._overlay_control_group, self._taskbar_control_group = groups
        groups[0].columnconfigure(1, weight=1)

        self._monitor_mode_status = ttk.Label(groups[0], text="중지됨", style="Status.TLabel")
        self._monitor_mode_status.grid(row=1, column=0, sticky="w", pady=(2, 3))
        self._start_button = ttk.Button(groups[0], text="모니터 시작", style="Primary.TButton", command=self._start)
        self._start_button.grid(row=2, column=0, sticky="ew", padx=(0, 3))
        self._stop_button = ttk.Button(groups[0], text="모니터 중지", style="Secondary.TButton", command=self._stop)
        self._stop_button.grid(row=2, column=1, sticky="ew", padx=(3, 0))

        self._overlay_mode_status = ttk.Label(
            groups[1],
            text="켜짐" if self._overlay_committed.enabled else "꺼짐",
            style="Status.TLabel",
        )
        self._overlay_mode_status.grid(row=1, column=0, sticky="w", pady=(2, 3))
        self._overlay_quick_button = ttk.Button(
            groups[1], style="Primary.TButton", command=self._toggle_overlay_from_main, takefocus=True,
        )
        self._overlay_quick_button.grid(row=2, column=0, sticky="ew")

        self._taskbar_mode_status = ttk.Label(groups[2], text="꺼짐", style="Status.TLabel")
        self._taskbar_mode_status.grid(row=1, column=0, sticky="w", pady=(2, 3))
        self._taskbar_button = ttk.Button(
            groups[2], style="Secondary.TButton", command=self._toggle_taskbar_from_main, takefocus=True,
        )
        self._taskbar_button.grid(row=2, column=0, sticky="ew")
        self.set_taskbar_enabled(self._taskbar_enabled)
        if self._on_taskbar_change is None:
            self._taskbar_button.state(["disabled"])

        self._overlay_quick_status = ttk.Label(
            controls, text=" ", style="App.TLabel", foreground=RED,
            wraplength=800, justify="left",
        )
        self._overlay_quick_status.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        probe = ttk.Label(controls, text="status line one\nstatus line two", style="App.TLabel")
        self._window.update_idletasks()
        controls.rowconfigure(1, minsize=probe.winfo_reqheight() + 4)
        probe.destroy()
        controls.bind("<Configure>", self._resize_overlay_quick_feedback, add="+")

    def _build_device_card(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="1. 미니 모니터 연결", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self._device_dot = tk.Canvas(card, width=12, height=12, background=CARD, highlightthickness=0)
        self._device_dot.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._device_title = ttk.Label(card, text="장치 확인 전", style="Status.TLabel")
        self._device_title.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self._device_detail = ttk.Label(
            card,
            text="VID 1A86 · PID 5722 · USB35INCHIPSV2",
            style="CardText.TLabel",
            wraplength=205,
            justify="left",
        )
        self._device_detail.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(2, 0))
        self._device_button = ttk.Button(card, text="장치 찾기", style="Secondary.TButton", command=self._detect_device)
        self._device_button.grid(row=1, column=2, rowspan=2, sticky="e", padx=(10, 0), pady=(5, 0))

        orientation = ttk.Frame(card, style="Card.TFrame")
        orientation.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(6, 0),
        )
        orientation.columnconfigure(1, weight=1)
        ttk.Label(
            orientation,
            text="화면 보기",
            style="CardText.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._orientation_combo = ttk.Combobox(
            orientation,
            textvariable=self._orientation_view,
            values=tuple(_VIEW_LABEL_TO_KEY),
            state="readonly",
            width=8,
        )
        self._orientation_combo.grid(row=0, column=1, sticky="w")
        self._orientation_flip = ttk.Checkbutton(
            orientation,
            text="상하 반전 (180°)",
            variable=self._orientation_inverted,
            style=self._checkbutton_style,
        )
        self._orientation_flip.grid(row=0, column=2, sticky="e", padx=(12, 0))

        ttk.Label(
            orientation,
            text="화면 밝기",
            style="CardText.TLabel",
        ).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        self._brightness_value = ttk.Label(
            orientation,
            text=f"{self._brightness.get()}%",
            style="Status.TLabel",
            width=5,
            anchor="e",
        )
        self._brightness_scale = ttk.Scale(
            orientation,
            from_=1,
            to=50,
            orient="horizontal",
            variable=self._brightness,
            command=self._brightness_changed,
            takefocus=True,
            style="Brightness.Horizontal.TScale",
        )
        self._brightness_scale.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(6, 0),
        )
        self._brightness_value.grid(
            row=1,
            column=2,
            sticky="e",
            padx=(12, 0),
            pady=(6, 0),
        )

    def _build_codex_account_card(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        self._codex_account_card = card
        card.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Codex 계정", style="CardTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self._codex_identity = ttk.Label(card, text="연결된 계정 없음", style="Status.TLabel", wraplength=310)
        self._codex_identity.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._codex_limits = ttk.Label(card, text="한도 정보 없음", style="CardText.TLabel", wraplength=310)
        self._codex_limits.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._codex_credits = ttk.Label(card, text="크레딧 확인 불가", style="CardText.TLabel", wraplength=310)
        self._codex_credits.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._codex_feedback = ttk.Label(card, text=" ", style="CardText.TLabel", wraplength=310)
        self._codex_feedback.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._codex_feedback.grid_remove()
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        for column in range(2):
            actions.columnconfigure(column, weight=1)
        self._codex_login_button = ttk.Button(actions, text="ChatGPT로 로그인", style="Primary.TButton", command=lambda: self._account_action(self._on_codex_login, "로그인을 시작하지 못했습니다"))
        self._codex_login_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._codex_cancel_button = ttk.Button(actions, text="로그인 취소", style="Secondary.TButton", command=lambda: self._account_action(self._on_codex_cancel, "로그인을 취소하지 못했습니다"))
        self._codex_cancel_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._codex_refresh_button = ttk.Button(actions, text="새로고침", style="Secondary.TButton", command=self._refresh_codex_account)
        self._codex_refresh_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._codex_logout_button = ttk.Button(actions, text="로그아웃", style="Secondary.TButton", command=self._confirm_codex_logout)
        self._codex_logout_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._codex_cancel_button.grid_remove()
        self._codex_logout_button.grid_remove()
        self._codex_help_button = ttk.Button(
            card, text="연결 문제 해결 ▾", style="Secondary.TButton", command=self._toggle_codex_help,
        )
        self._codex_help_button.grid(row=8, column=0, sticky="ew", pady=(4, 0))
        self._update_open_button = ttk.Button(
            card, text="업데이트", style="Secondary.TButton", command=self._show_update_dialog,
        )
        self._update_open_button.grid(row=8, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))
        help_frame = ttk.Frame(card, style="Card.TFrame")
        self._codex_help_frame = help_frame
        help_frame.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        help_frame.columnconfigure(0, weight=1)
        help_frame.columnconfigure(1, weight=1)
        self._codex_cli_button = ttk.Button(help_frame, text="설치된 CLI 찾기", style="Secondary.TButton", command=self._select_codex_cli)
        self._codex_cli_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._codex_guide_button = ttk.Button(help_frame, text="Codex CLI 설치 안내", style="Secondary.TButton", command=lambda: self._account_action(self._on_codex_install_guide, "설치 안내를 열지 못했습니다"))
        self._codex_guide_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        help_frame.grid_remove()
        self._codex_cancel_button.state(["disabled"])
        self._codex_logout_button.state(["disabled"])
        if self._on_codex_login is None:
            self._codex_login_button.state(["disabled"])
        if self._on_codex_cli_selected is None:
            self._codex_cli_button.state(["disabled"])
        if self._on_codex_install_guide is None:
            self._codex_guide_button.state(["disabled"])

    def _toggle_codex_help(self) -> None:
        if self._codex_help_frame.winfo_manager():
            self._codex_help_frame.grid_remove()
            self._codex_help_button.configure(text="연결 문제 해결 ▾")
        else:
            self._codex_help_frame.grid()
            self._codex_help_button.configure(text="연결 문제 해결 ▴")

    def _show_update_dialog(self) -> None:
        dialog = self._update_dialog
        dialog.deiconify()
        dialog.update_idletasks()
        bounds = _window_bounds(self._window)
        width = min(max(420, dialog.winfo_reqwidth()), max(1, bounds.width - bounds.frame_width))
        height = min(max(240, dialog.winfo_reqheight()), max(1, bounds.height - bounds.frame_height))
        x = max(bounds.left, min(self._window.winfo_rootx() + (self._window.winfo_width() - width) // 2, bounds.right - width - bounds.frame_width))
        y = max(bounds.top, min(self._window.winfo_rooty() + (self._window.winfo_height() - height) // 2, bounds.bottom - height - bounds.frame_height))
        dialog.lift()
        dialog.focus_set()
        _set_window_geometry(dialog, width, height, x, y)

    def _build_update_banner(self) -> None:
        dialog = tk.Toplevel(self._window, class_="AIMiniMonitorUpdates")
        dialog.withdraw()
        dialog.title("업데이트")
        dialog.configure(background=BG)
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        dialog.bind("<Escape>", lambda _event: dialog.withdraw())
        self._update_dialog = dialog
        banner = ttk.Frame(dialog, style="Card.TFrame", padding=(14, 8))
        self._update_banner = banner
        banner.pack(fill="both", expand=True, padx=12, pady=12)
        banner.grid_propagate(False)
        banner.columnconfigure(0, weight=1)
        banner.columnconfigure(1, weight=1)
        title = ttk.Label(banner, text="업데이트", style="CardTitle.TLabel")
        title.grid(row=0, column=0, columnspan=2, sticky="w")
        self._update_message = ttk.Label(
            banner, text="최신 버전 확인 전", style="CardText.TLabel",
            wraplength=400, justify="left",
        )
        self._update_message.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        self._update_check_button = ttk.Button(banner, text="업데이트 확인", style="Secondary.TButton", command=lambda: self._update_action(self._on_update_check, "업데이트를 확인하지 못했습니다"))
        self._update_check_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._update_apply_button = ttk.Button(banner, text="업데이트 후 다시 시작", style="Secondary.TButton", command=lambda: self._update_action(self._on_update_apply, "업데이트를 준비하지 못했습니다"))
        self._update_apply_button.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        self._update_dismiss_button = ttk.Button(banner, text="나중에", style="Secondary.TButton", command=lambda: self._update_action(self._on_update_dismiss, "알림을 닫지 못했습니다"))
        self._update_dismiss_button.grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        self._update_dismiss_button.state(["disabled"])
        self._update_release_button = ttk.Button(banner, text="릴리스 보기", style="Secondary.TButton", command=lambda: self._update_action(self._on_update_open_release, "릴리스를 열지 못했습니다"))
        self._update_release_button.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self._update_release_button.state(["disabled"])
        self._update_apply_button.state(["disabled"])
        if self._on_update_check is None:
            self._update_check_button.state(["disabled"])
        # Reserve room for the longest displayed message before any status
        # arrives, so long release text never shifts the fixed footer.
        message_probe = ttk.Label(
            banner, text="가" * 81, style="CardText.TLabel", wraplength=400,
        )
        banner.update_idletasks()
        reserved_height = (
            title.winfo_reqheight() + message_probe.winfo_reqheight()
            + self._update_check_button.winfo_reqheight()
            + self._update_release_button.winfo_reqheight()
            + 8 + 3 + 8 + 6 + 8
        )
        message_probe.destroy()
        banner.configure(height=max(184, reserved_height))

    def _build_usage_card(self, parent: ttk.Frame) -> None:
        usage_bottom = ttk.Frame(parent, style="Card.TFrame")
        usage_bottom.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        usage_bottom.columnconfigure(0, weight=1)
        self._usage_status = ttk.Label(usage_bottom, text="확인 전", style="Status.TLabel")
        self._usage_status.grid(row=0, column=0, sticky="nw")
        self._usage_button = ttk.Button(usage_bottom, text="사용량 확인", style="Secondary.TButton", command=self._check_usage)
        self._usage_button.grid(row=0, column=1, sticky="ne", padx=(8, 0))
        self._usage_detail = ttk.Label(
            usage_bottom,
            text=" ",
            style="CardText.TLabel",
            wraplength=316,
            justify="left",
        )
        self._usage_detail.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
        )
        # The account card already shows limits and credits. Keep these labels
        # for the existing runtime/status API, but avoid a second visible
        # usage summary in the compact main view.
        self._usage_status.grid_remove()
        self._usage_detail.grid_remove()
        self._window.update_idletasks()
        usage_bottom.configure(height=self._usage_button.winfo_reqheight())
        usage_bottom.grid_propagate(False)
        usage_bottom.bind("<Configure>", self._resize_usage_feedback, add="+")

    def _build_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent, style="App.TFrame")
        self._controls = controls
        controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)

        autostart_line = ttk.Frame(
            controls,
            style="Card.TFrame",
            padding=(8, 2),
        )
        autostart_line.grid(
            row=0,
            column=0,
            columnspan=3,
            sticky="ew",
        )
        autostart_line.columnconfigure(0, weight=1)
        self._autostart_toggle = ttk.Checkbutton(
            autostart_line,
            text="Windows 시작 시 자동 실행",
            variable=self._autostart_enabled,
            style=self._checkbutton_style,
            command=self._toggle_autostart,
        )
        self._autostart_toggle.grid(row=0, column=0, sticky="w")
        self._autostart_status = ttk.Label(
            autostart_line,
            text="",
            style="CardText.TLabel",
            width=11,
            anchor="e",
        )
        self._autostart_status.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._set_autostart_status()
        if self._on_autostart_change is None:
            self._autostart_toggle.state(["disabled"])

        self._exit_button = ttk.Button(
            controls,
            text="프로그램 종료",
            style="Secondary.TButton",
            command=self._on_exit,
        )
        self._exit_button.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(8, 0),
        )

    def _sync_left_scroll_region(self, _event: tk.Event | None = None) -> None:
        if self._closed:
            return
        bounds = self._left_canvas.bbox("all")
        if bounds is not None:
            self._left_canvas.configure(scrollregion=bounds)
        self._window.after_idle(self._update_left_scrollbar)

    def _resize_left_viewport(self, event: tk.Event) -> None:
        if self._closed:
            return
        self._left_canvas.itemconfigure(
            self._left_scroll_window,
            width=max(1, int(event.width)),
        )
        self._window.after_idle(self._update_left_scrollbar)

    def _update_left_scrollbar(self) -> None:
        if self._closed:
            return
        overflow = (
            self._left_scroll_content.winfo_reqheight()
            > self._left_canvas.winfo_height() + 1
        )
        if overflow and not self._left_scrollbar_visible:
            self._left_scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
            self._left_scrollbar_visible = True
        elif not overflow and self._left_scrollbar_visible:
            self._left_scrollbar.grid_remove()
            self._left_canvas.yview_moveto(0.0)
            self._left_scrollbar_visible = False

    def _bind_left_viewport_interactions(self, widget: tk.Misc) -> None:
        widget.bind("<FocusIn>", self._reveal_left_focus, add="+")
        widget.bind("<MouseWheel>", self._scroll_left_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_left_viewport_interactions(child)

    def _reveal_left_focus(self, event: tk.Event) -> None:
        if self._closed:
            return
        widget = event.widget
        self._scroll_left_widget_into_view(widget)
        self._window.after_idle(
            lambda: self._scroll_left_widget_into_view(widget)
        )

    def _scroll_left_widget_into_view(self, widget: tk.Misc) -> None:
        if self._closed or not self._left_scrollbar_visible:
            return
        try:
            self._window.update_idletasks()
            viewport_height = self._left_canvas.winfo_height()
            content_height = self._left_scroll_content.winfo_height()
            if viewport_height <= 1 or content_height <= viewport_height:
                return
            view_top = float(self._left_canvas.canvasy(0))
            view_bottom = view_top + viewport_height
            widget_top = (
                widget.winfo_rooty()
                - self._left_canvas.winfo_rooty()
                + view_top
            )
            widget_bottom = widget_top + widget.winfo_height()
            margin = 4
            if widget_top - margin < view_top:
                target = max(0.0, widget_top - margin)
            elif widget_bottom + margin > view_bottom:
                target = min(
                    float(content_height - viewport_height),
                    widget_bottom + margin - viewport_height,
                )
            else:
                return
            self._left_canvas.yview_moveto(target / float(content_height))
        except tk.TclError:
            return

    def _scroll_left_mousewheel(self, event: tk.Event) -> str | None:
        if self._closed or not self._left_scrollbar_visible:
            return None
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return None
        units = -3 if delta > 0 else 3
        self._left_canvas.yview_scroll(units, "units")
        return "break"

    def _sync_right_scroll_region(self, _event: tk.Event | None = None) -> None:
        if self._closed:
            return
        bounds = self._right_canvas.bbox("all")
        if bounds is not None:
            self._right_canvas.configure(scrollregion=bounds)
        self._window.after_idle(self._update_right_scrollbar)

    def _resize_right_viewport(self, event: tk.Event) -> None:
        if self._closed:
            return
        self._right_canvas.itemconfigure(
            self._right_scroll_window,
            width=max(1, int(event.width)),
        )
        self._render_preview()
        self._window.after_idle(self._update_right_scrollbar)

    def _update_right_scrollbar(self) -> None:
        if self._closed:
            return
        overflow = (
            self._right_scroll_content.winfo_reqheight()
            > self._right_canvas.winfo_height() + 1
        )
        if overflow and not self._right_scrollbar_visible:
            self._right_scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
            self._right_scrollbar_visible = True
        elif not overflow and self._right_scrollbar_visible:
            self._right_scrollbar.grid_remove()
            self._right_canvas.yview_moveto(0.0)
            self._right_scrollbar_visible = False

    def _bind_right_viewport_interactions(self, widget: tk.Misc) -> None:
        widget.bind("<FocusIn>", self._reveal_right_focus, add="+")
        widget.bind("<MouseWheel>", self._scroll_right_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_right_viewport_interactions(child)

    def _reveal_right_focus(self, event: tk.Event) -> None:
        if self._closed:
            return
        self._scroll_right_widget_into_view(event.widget)
        self._window.after_idle(
            lambda: self._scroll_right_widget_into_view(event.widget)
        )

    def _scroll_right_widget_into_view(self, widget: tk.Misc) -> None:
        if self._closed or not self._right_scrollbar_visible:
            return
        try:
            self._window.update_idletasks()
            viewport_height = self._right_canvas.winfo_height()
            content_height = self._right_scroll_content.winfo_height()
            if viewport_height <= 1 or content_height <= viewport_height:
                return
            view_top = float(self._right_canvas.canvasy(0))
            view_bottom = view_top + viewport_height
            widget_top = widget.winfo_rooty() - self._right_canvas.winfo_rooty() + view_top
            widget_bottom = widget_top + widget.winfo_height()
            margin = 4
            if widget_top - margin < view_top:
                target = max(0.0, widget_top - margin)
            elif widget_bottom + margin > view_bottom:
                target = min(
                    float(content_height - viewport_height),
                    widget_bottom + margin - viewport_height,
                )
            else:
                return
            self._right_canvas.yview_moveto(target / float(content_height))
        except tk.TclError:
            return

    def _scroll_right_mousewheel(self, event: tk.Event) -> str | None:
        if self._closed or not self._right_scrollbar_visible:
            return None
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return None
        self._right_canvas.yview_scroll(-3 if delta > 0 else 3, "units")
        return "break"

    def _brightness_changed(self, raw_value: str) -> None:
        """Quantize a native scale movement and debounce its side effect."""

        if self._closed or self._brightness_adjusting:
            return
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            self._set_brightness_value(self._brightness_committed)
            return
        if numeric != numeric or numeric in {float("inf"), float("-inf")}:
            self._set_brightness_value(self._brightness_committed)
            return
        value = validate_brightness(max(1, min(50, int(numeric + 0.5))))
        self._set_brightness_value(value)
        self._brightness_value.configure(text=f"{value}%", foreground=CYAN)

        # Freeze every setting that would create a stale config draft, while
        # leaving this one live control available for latest-value coalescing.
        self._brightness_busy = True
        self._start_button.state(["disabled"])
        self._usage_button.state(["disabled"])
        self._set_settings_enabled(False)
        self._update_brightness_enabled()
        self._cancel_brightness_schedule()
        self._brightness_after_id = self._window.after(
            120,
            self._commit_brightness,
        )

    def _commit_brightness(self) -> None:
        self._brightness_after_id = None
        if self._closed:
            return
        value = self.selected_brightness
        callback = self._on_brightness_change
        if callback is None:
            self.complete_brightness(
                ActionResult(True, f"화면 밝기 {value}%"),
                brightness=value,
            )
            return
        result = self._invoke(
            lambda: callback(value),
            "화면 밝기를 적용하지 못했습니다",
        )
        if result.pending:
            self._brightness_value.configure(text=f"{value}%", foreground=CYAN)
        else:
            self.complete_brightness(result, brightness=value)

    def _cancel_brightness_schedule(self) -> None:
        after_id = self._brightness_after_id
        self._brightness_after_id = None
        if after_id is None:
            return
        try:
            self._window.after_cancel(after_id)
        except tk.TclError:
            pass

    def _set_brightness_value(self, value: int) -> None:
        canonical = validate_brightness(value)
        self._brightness_adjusting = True
        try:
            self._brightness.set(canonical)
        finally:
            self._brightness_adjusting = False
        self._brightness_value.configure(text=f"{canonical}%")

    def _set_brightness_enabled(self, enabled: bool) -> None:
        state = "!disabled" if enabled else "disabled"
        self._brightness_scale.state([state])

    def _update_brightness_enabled(self) -> None:
        enabled = (
            not self._blocked
            and not self._usage_busy
            and not self._lifecycle_busy
            and (not self._running or self._on_brightness_change is not None)
        )
        self._set_brightness_enabled(enabled)
        self._update_overlay_controls_enabled()

    def _toggle_autostart(self) -> None:
        desired = bool(self._autostart_enabled.get())
        callback = self._on_autostart_change
        if callback is None:
            self._autostart_enabled.set(self._autostart_committed)
            return
        self._autostart_toggle.state(["disabled"])
        try:
            try:
                result = callback(desired)
            except Exception:
                result = ActionResult(
                    False,
                    "자동 실행 변경 실패",
                    "Windows 사용자 시작 항목을 변경하지 못했습니다.",
                )
            if not isinstance(result, ActionResult):
                result = ActionResult(
                    False,
                    "자동 실행 변경 실패",
                    "잘못된 내부 응답",
                )
            if result.ok:
                self._autostart_committed = desired
                self._autostart_needs_repair = False
                self._autostart_state_unknown = False
            elif result.state_uncertain:
                # The registry mutation may have succeeded even though its
                # verification read failed. Keep the requested visual value,
                # but explicitly avoid claiming that it is confirmed.
                self._autostart_committed = desired
                self._autostart_state_unknown = True
            else:
                self._autostart_enabled.set(self._autostart_committed)
            self._set_autostart_status(failed=not result.ok)
        finally:
            self._autostart_toggle.state(["!disabled"])

    def _set_autostart_status(self, *, failed: bool = False) -> None:
        if self._autostart_state_unknown:
            text, color = "확인 필요", AMBER
        elif failed:
            text, color = "변경 실패", RED
        elif self._autostart_needs_repair:
            text, color = "재설정 필요", AMBER
        elif self._autostart_committed:
            text, color = "켜짐", GREEN
        else:
            text, color = "꺼짐", DIM
        self._autostart_status.configure(text=text, foreground=color)

    def _resize_usage_feedback(self, event: tk.Event) -> None:
        """Wrap async feedback inside its reserved area without layout shifts."""

        if self._closed:
            return
        full_width = max(80, int(event.width))
        status_width = max(
            80,
            full_width - self._usage_button.winfo_reqwidth() - 8,
        )
        self._usage_status.configure(wraplength=status_width, justify="left")
        self._usage_detail.configure(wraplength=full_width, justify="left")

    def _resize_overlay_quick_feedback(self, event: tk.Event) -> None:
        """Keep the fixed retry lane inside the right-column action width."""

        if self._closed:
            return
        self._overlay_quick_status.configure(
            wraplength=max(80, int(event.width)),
            justify="left",
        )

    def _sync_taskbar_hint(self) -> None:
        names = " · ".join(name.upper() for name in self._taskbar_committed_items)
        style = _TASKBAR_STYLE_LABELS[self._taskbar_committed_style]
        self._taskbar_hint.configure(
            text=f"{names} / 투명 배경 · {style} + % / 드래그로 이동"
        )

    def _create_taskbar_options_dialog(self) -> None:
        if self._taskbar_options_dialog is not None:
            return
        dialog = tk.Toplevel(self._window, class_="AIMiniMonitorTaskbarOptions")
        dialog.withdraw()
        dialog.title("작업표시줄 바 설정")
        dialog.configure(background=BG)
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.protocol("WM_DELETE_WINDOW", self._hide_taskbar_options)
        dialog.bind("<Escape>", lambda _event: self._hide_taskbar_options())
        self._taskbar_options_dialog = dialog

        root = ttk.Frame(dialog, style="App.TFrame", padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        ttk.Label(root, text="작업표시줄 바 설정", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            root, text="표시할 항목을 개별 선택하세요.", style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 8))

        card = ttk.Frame(root, style="Card.TFrame", padding=(12, 8))
        card.grid(row=2, column=0, sticky="ew")
        for column in range(3):
            card.columnconfigure(column, weight=1)
        self._taskbar_item_vars: dict[str, tk.BooleanVar] = {}
        self._taskbar_item_checks: dict[str, ttk.Checkbutton] = {}
        for index, name in enumerate(DEFAULT_TASKBAR_ITEMS):
            variable = tk.BooleanVar(dialog, name in self._taskbar_committed_items)
            check = ttk.Checkbutton(
                card, text=name.upper(), variable=variable,
                style=self._checkbutton_style, command=self._update_taskbar_selection_count,
                takefocus=True,
            )
            check.grid(row=index // 3, column=index % 3, sticky="w", pady=2)
            self._taskbar_item_vars[name] = variable
            self._taskbar_item_checks[name] = check

        ttk.Label(card, text="표시 방식", style="CardText.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 3),
        )
        self._taskbar_style_var = tk.StringVar(dialog, _TASKBAR_STYLE_LABELS[self._taskbar_committed_style])
        self._taskbar_style_combo = ttk.Combobox(
            card, textvariable=self._taskbar_style_var,
            values=tuple(_TASKBAR_LABEL_STYLES), state="readonly", takefocus=True,
        )
        self._taskbar_style_combo.grid(row=3, column=0, columnspan=3, sticky="ew")

        ttk.Separator(card, orient="horizontal").grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(10, 6),
        )
        ttk.Label(card, text="위치 (0~100%)", style="CardText.TLabel").grid(
            row=5, column=0, sticky="w",
        )
        self._taskbar_position_reset = ttk.Button(
            card, text="기본 가로 위치", style="Secondary.TButton",
            command=lambda: self._request_taskbar_position("reset", 0.0),
        )
        self._taskbar_position_reset.grid(row=5, column=1, sticky="ew", padx=4)
        self._taskbar_position_x = ttk.Label(card, text="X --px", style="Status.TLabel", anchor="e")
        self._taskbar_position_x.grid(row=5, column=2, sticky="e")
        self._taskbar_nudge_left = ttk.Button(
            card, text="◀ 1px", style="Secondary.TButton",
            command=lambda: self._request_taskbar_position("nudge", -1.0),
        )
        self._taskbar_nudge_left.grid(row=6, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self._taskbar_position_percent = tk.DoubleVar(dialog, 0.0)
        self._taskbar_position_scale = ttk.Scale(
            card, from_=0, to=100, variable=self._taskbar_position_percent,
            command=self._taskbar_position_changed, style="Brightness.Horizontal.TScale",
            takefocus=True,
        )
        self._taskbar_position_scale.grid(row=6, column=1, sticky="ew", pady=(6, 0))
        self._taskbar_nudge_right = ttk.Button(
            card, text="1px ▶", style="Secondary.TButton",
            command=lambda: self._request_taskbar_position("nudge", 1.0),
        )
        self._taskbar_nudge_right.grid(row=6, column=2, sticky="ew", pady=(6, 0), padx=(4, 0))
        self._taskbar_position_status = ttk.Label(
            card, text="위치는 즉시 저장됩니다", style="CardText.TLabel",
            foreground=SECONDARY,
        )
        self._taskbar_position_status.grid(row=7, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self._taskbar_selection_count = ttk.Label(root, style="App.TLabel")
        self._taskbar_selection_count.grid(row=3, column=0, sticky="w", pady=(5, 0))
        self._update_taskbar_selection_count()
        self._taskbar_options_status = ttk.Label(
            root, text=" ", style="App.TLabel", foreground=RED,
            wraplength=380, justify="left",
        )
        self._taskbar_options_status.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        actions = ttk.Frame(root, style="App.TFrame")
        actions.grid(row=5, column=0, sticky="ew", pady=(7, 0))
        actions.columnconfigure(0, weight=1, uniform="taskbar_actions")
        actions.columnconfigure(1, weight=1, uniform="taskbar_actions")
        ttk.Button(
            actions, text="취소", style="Secondary.TButton",
            command=self._hide_taskbar_options,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._taskbar_apply_button = ttk.Button(
            actions, text="적용", style="Primary.TButton",
            command=self._apply_taskbar_options,
        )
        self._taskbar_apply_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _update_taskbar_selection_count(self) -> None:
        count = sum(variable.get() for variable in self._taskbar_item_vars.values())
        self._taskbar_selection_count.configure(text=f"선택한 {count}개")

    def _refresh_taskbar_position(self) -> bool:
        getter = self._get_taskbar_position
        try:
            position = getter() if getter is not None else None
            if position is None:
                raise ValueError("position unavailable")
            percent, x = position
            percent = float(percent)
            if not 0.0 <= percent <= 100.0 or isinstance(x, bool) or not isinstance(x, int):
                raise ValueError("invalid position")
        except Exception:
            self._taskbar_position_x.configure(text="X --px")
            self._taskbar_position_status.configure(text="위치를 읽지 못했습니다", foreground=RED)
            enabled = False
        else:
            self._taskbar_position_syncing = True
            try:
                self._taskbar_position_percent.set(percent)
            finally:
                self._taskbar_position_syncing = False
            self._taskbar_position_x.configure(text=f"X {x}px")
            enabled = self._on_taskbar_position is not None
        for widget in (
            self._taskbar_position_scale, self._taskbar_nudge_left,
            self._taskbar_nudge_right, self._taskbar_position_reset,
        ):
            widget.state(["!disabled" if enabled else "disabled"])
        return enabled

    def _taskbar_position_changed(self, value: str) -> None:
        if self._taskbar_position_syncing:
            return
        try:
            percent = max(0.0, min(100.0, float(value)))
        except ValueError:
            return
        self._request_taskbar_position("percent", percent)

    def _request_taskbar_position(self, action: str, value: float) -> None:
        callback = self._on_taskbar_position
        if callback is None or self._taskbar_position_syncing:
            return
        result = self._invoke(lambda: callback(action, value), "위치 저장 실패")
        refreshed = self._refresh_taskbar_position()
        if result.ok and refreshed:
            self._taskbar_position_status.configure(text="위치는 즉시 저장됩니다", foreground=SECONDARY)
        elif not result.ok:
            self._taskbar_position_status.configure(text=result.detail or result.title, foreground=RED)

    def _show_taskbar_options(self) -> None:
        require_main_thread()
        self._ensure_open()
        self._create_taskbar_options_dialog()
        assert self._taskbar_options_dialog is not None
        dialog = self._taskbar_options_dialog
        for name, variable in self._taskbar_item_vars.items():
            variable.set(name in self._taskbar_committed_items)
        self._taskbar_style_var.set(_TASKBAR_STYLE_LABELS[self._taskbar_committed_style])
        self._update_taskbar_selection_count()
        self._taskbar_options_status.configure(text=" ")
        self._taskbar_position_status.configure(text="위치는 즉시 저장됩니다", foreground=SECONDARY)
        self._refresh_taskbar_position()
        dialog.update_idletasks()
        bounds = _window_bounds(self._window)
        width = min(max(420, dialog.winfo_reqwidth()), max(1, bounds.width - bounds.frame_width))
        height = min(max(360, dialog.winfo_reqheight()), max(1, bounds.height - bounds.frame_height))
        dialog.deiconify()
        dialog.update_idletasks()
        self._window.update_idletasks()
        x = self._window.winfo_rootx() + (self._window.winfo_width() - width) // 2
        y = self._window.winfo_rooty() + (self._window.winfo_height() - height) // 2
        x = max(bounds.left, min(x, bounds.right - width - bounds.frame_width))
        y = max(bounds.top, min(y, bounds.bottom - height - bounds.frame_height))
        _set_window_geometry(dialog, width, height, x, y)
        dialog.lift()
        dialog.grab_set()
        self._taskbar_item_checks[DEFAULT_TASKBAR_ITEMS[0]].focus_set()

    def _hide_taskbar_options(self) -> None:
        dialog = self._taskbar_options_dialog
        if dialog is None:
            return
        try:
            if dialog.grab_current() is dialog:
                dialog.grab_release()
        except tk.TclError:
            pass
        dialog.withdraw()

    def _apply_taskbar_options(self) -> None:
        require_main_thread()
        self._ensure_open()
        items = tuple(name for name in DEFAULT_TASKBAR_ITEMS if self._taskbar_item_vars[name].get())
        style = _TASKBAR_LABEL_STYLES.get(self._taskbar_style_var.get())
        try:
            items = validate_taskbar_options(items, style)
        except ValueError:
            self._taskbar_options_status.configure(text="표시할 항목을 하나 이상 선택하고 표시 방식을 확인하세요.")
            return
        callback = self._on_taskbar_options
        if callback is None:
            self._taskbar_options_status.configure(text="설정 변경을 사용할 수 없습니다.")
            return
        result = self._invoke(lambda: callback(items, style), "작업표시줄 바 설정 실패")
        if result.ok:
            self.set_taskbar_options(items, style)
            self._hide_taskbar_options()
        else:
            self._taskbar_options_status.configure(text=result.detail or result.title)

    def _create_overlay_dialog(self) -> None:
        if self._overlay_dialog is not None:
            return
        dialog = tk.Toplevel(self._window, class_="AIMiniMonitorOverlayOptions")
        dialog.withdraw()
        dialog.title("PC 항상 위 상태창 설정")
        dialog.configure(background=BG)
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.protocol("WM_DELETE_WINDOW", self._hide_overlay_options)
        dialog.bind("<Escape>", lambda _event: self._hide_overlay_options())
        self._overlay_dialog = dialog

        root = ttk.Frame(dialog, style="App.TFrame", padding=20)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        ttk.Label(root, text="PC 항상 위 상태창", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            root,
            text=(
                "미니 모니터와 같은 화면을 PC 바탕화면 위에 표시합니다. "
                "상태창 전체를 마우스로 끌어 옮길 수 있으며, 모니터 시작 후 같은 실시간 값으로 갱신됩니다."
            ),
            style="Subtitle.TLabel",
            wraplength=440,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(3, 14))

        card = ttk.Frame(root, style="Card.TFrame", padding=14)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        self._overlay_dialog_visibility = ttk.Label(
            card,
            text="표시 중" if self._overlay_committed.enabled else "숨김",
            style="Status.TLabel",
        )
        self._overlay_dialog_visibility.grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(card, text="상태창 크기", style="CardText.TLabel").grid(
            row=1, column=0, sticky="w", pady=(16, 4)
        )
        self._overlay_scale_value = ttk.Label(
            card,
            text=f"{self._overlay_scale_percent.get()}%",
            style="Status.TLabel",
            width=6,
            anchor="e",
        )
        self._overlay_scale_value.grid(row=1, column=1, sticky="e", pady=(16, 4))
        self._overlay_scale = ttk.Scale(
            card,
            from_=MIN_OVERLAY_SCALE_PERCENT,
            to=MAX_OVERLAY_SCALE_PERCENT,
            variable=self._overlay_scale_percent,
            command=self._overlay_scale_changed,
            takefocus=True,
            style="Brightness.Horizontal.TScale",
        )
        self._overlay_scale.grid(row=2, column=0, columnspan=2, sticky="ew")

        ttk.Label(
            card,
            text=(
                "카드 불투명도 (0~100%) · 전체 격자 배경은 표시하지 않으며 "
                "글자·숫자·게이지·그래프는 "
                "선명하게 유지됩니다"
            ),
            style="CardText.TLabel",
            wraplength=350,
            justify="left",
        ).grid(row=3, column=0, sticky="w", pady=(16, 4))
        self._overlay_opacity_value = ttk.Label(
            card,
            text=f"{self._overlay_opacity_percent.get()}%",
            style="Status.TLabel",
            width=6,
            anchor="e",
        )
        self._overlay_opacity_value.grid(row=3, column=1, sticky="e", pady=(16, 4))
        self._overlay_opacity_scale = ttk.Scale(
            card,
            from_=round(MIN_OVERLAY_OPACITY * 100),
            to=round(MAX_OVERLAY_OPACITY * 100),
            variable=self._overlay_opacity_percent,
            command=self._overlay_opacity_changed,
            takefocus=True,
            style="Brightness.Horizontal.TScale",
        )
        self._overlay_opacity_scale.grid(
            row=4, column=0, columnspan=2, sticky="ew"
        )
        self._overlay_status = ttk.Label(
            card,
            text="설정 변경은 즉시 저장됩니다.",
            style="CardText.TLabel",
            wraplength=410,
            justify="left",
        )
        self._overlay_status.grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

        actions = ttk.Frame(root, style="App.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        actions.columnconfigure(1, weight=1)
        self._overlay_reset_button = ttk.Button(
            actions,
            text="위치 초기화",
            style="Secondary.TButton",
            command=self._reset_overlay_position,
        )
        self._overlay_reset_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Button(
            actions,
            text="설정창으로 돌아가기",
            style="Primary.TButton",
            command=self._hide_overlay_options,
        ).grid(row=0, column=1, sticky="ew")
        if self._on_overlay_change is None:
            self._overlay_scale.state(["disabled"])
            self._overlay_opacity_scale.state(["disabled"])
        if self._on_overlay_reset_position is None:
            self._overlay_reset_button.state(["disabled"])
        self._resize_overlay_dialog()

    def _resize_overlay_dialog(self) -> None:
        dialog = self._overlay_dialog
        if dialog is None:
            return
        dialog.update_idletasks()
        width = max(480, dialog.winfo_reqwidth())
        height = max(390, dialog.winfo_reqheight())
        self._overlay_dialog_size = (width, height)
        dialog.geometry(f"{width}x{height}")

    def _show_overlay_options(self) -> None:
        require_main_thread()
        self._create_overlay_dialog()
        assert self._overlay_dialog is not None
        dialog = self._overlay_dialog
        self._resize_overlay_dialog()
        width, height = self._overlay_dialog_size
        dialog.deiconify()
        dialog.update_idletasks()
        self._window.update_idletasks()
        x = self._window.winfo_rootx() + (self._window.winfo_width() - width) // 2
        y = self._window.winfo_rooty() + (self._window.winfo_height() - height) // 2
        bounds = _window_bounds(self._window)
        outer_width = width + bounds.frame_width
        outer_height = height + bounds.frame_height
        max_x = max(bounds.left, bounds.right - outer_width)
        max_y = max(bounds.top, bounds.bottom - outer_height)
        x = max(bounds.left, min(x, max_x))
        y = max(bounds.top, min(y, max_y))
        _set_window_geometry(dialog, width, height, x, y)
        try:
            dialog.attributes("-topmost", True)
        except tk.TclError:
            pass
        dialog.lift()
        dialog.grab_set()
        self._overlay_scale.focus_set()

    def _hide_overlay_options(self) -> None:
        dialog = self._overlay_dialog
        if dialog is None:
            return
        try:
            if dialog.grab_current() is dialog:
                dialog.grab_release()
        except tk.TclError:
            pass
        try:
            dialog.attributes("-topmost", False)
        except tk.TclError:
            pass
        dialog.withdraw()

    def _sync_overlay_quick_button(self) -> None:
        """Keep the main action label tied to the last committed visibility."""

        if not hasattr(self, "_overlay_quick_button"):
            return
        self._overlay_quick_button.configure(
            text=(
                "PC 상태창 끄기"
                if self._overlay_committed.enabled
                else "PC 상태창 켜기"
            )
        )
        enabled = (
            self._on_overlay_change is not None
            and not self._overlay_action_busy
            and not self._lifecycle_busy
            and not self._brightness_busy
            and not self._closed
        )
        self._overlay_quick_button.state(
            ["!disabled" if enabled else "disabled"]
        )

    def _update_overlay_controls_enabled(self) -> None:
        """Prevent config races while start/stop or brightness is committing."""

        enabled = (
            self._on_overlay_change is not None
            and not self._overlay_action_busy
            and not self._lifecycle_busy
            and not self._brightness_busy
            and not self._closed
        )
        state = "!disabled" if enabled else "disabled"
        for name in (
            "_overlay_scale",
            "_overlay_opacity_scale",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.state([state])
        reset_enabled = (
            enabled and self._on_overlay_reset_position is not None
        )
        reset_button = getattr(self, "_overlay_reset_button", None)
        if reset_button is not None:
            reset_button.state(
                ["!disabled" if reset_enabled else "disabled"]
            )
        self._sync_overlay_quick_button()

    def _toggle_overlay_from_main(self) -> None:
        """Show or hide the real always-on-top status window immediately."""

        require_main_thread()
        if (
            self._overlay_action_busy
            or self._lifecycle_busy
            or self._brightness_busy
        ):
            return
        self._cancel_overlay_schedule()
        current = self._overlay_committed
        requested = OverlaySettings(
            not current.enabled,
            current.opacity,
            current.scale_percent,
        )
        self._overlay_action_busy = True
        self._update_overlay_controls_enabled()
        self._overlay_quick_button.configure(
            text=(
                "PC 상태창 켜는 중…"
                if requested.enabled
                else "PC 상태창 끄는 중…"
            )
        )
        self._window.update_idletasks()
        result = self._submit_overlay_settings(requested)
        self._overlay_action_busy = False
        self._update_overlay_controls_enabled()
        if result.ok:
            self._overlay_quick_status.configure(text=" ")
        else:
            self._overlay_quick_status.configure(
                text="상태창 변경 실패\n설정 확인 후 다시 시도",
                foreground=RED,
            )

    def _toggle_taskbar_from_main(self) -> None:
        require_main_thread()
        if self._on_taskbar_change is None:
            return
        target = not self._taskbar_enabled
        result = self._invoke(
            lambda: self._on_taskbar_change(target),
            "작업 표시줄 바 변경 실패",
        )
        if result.ok:
            self.set_taskbar_enabled(target)
            self._overlay_quick_status.configure(text=" ")
        else:
            self._overlay_quick_status.configure(
                text="작업 표시줄 바 변경 실패\n설정 확인 후 다시 시도",
                foreground=RED,
            )

    def _overlay_scale_changed(self, value: str) -> None:
        rounded = max(
            MIN_OVERLAY_SCALE_PERCENT,
            min(MAX_OVERLAY_SCALE_PERCENT, round(float(value))),
        )
        self._overlay_scale_percent.set(rounded)
        self._overlay_scale_value.configure(text=f"{rounded}%")
        self._schedule_overlay_change()

    def _overlay_opacity_changed(self, value: str) -> None:
        rounded = max(
            round(MIN_OVERLAY_OPACITY * 100),
            min(round(MAX_OVERLAY_OPACITY * 100), round(float(value))),
        )
        self._overlay_opacity_percent.set(rounded)
        self._overlay_opacity_value.configure(text=f"{rounded}%")
        self._schedule_overlay_change()

    def _schedule_overlay_change(self) -> None:
        self._cancel_overlay_schedule()
        self._overlay_after_id = self._window.after(
            140, self._commit_overlay_settings
        )

    def _cancel_overlay_schedule(self) -> None:
        if self._overlay_after_id is None:
            return
        try:
            self._window.after_cancel(self._overlay_after_id)
        except tk.TclError:
            pass
        self._overlay_after_id = None

    def _commit_overlay_settings(self) -> None:
        self._overlay_after_id = None
        requested = self.overlay_settings
        self._submit_overlay_settings(requested)

    def _submit_overlay_settings(
        self,
        requested: OverlaySettings,
    ) -> ActionResult:
        """Apply one overlay state and restore the committed UI on failure."""

        callback = self._on_overlay_change
        if callback is None:
            result = ActionResult(
                False,
                "상태창 설정 실패",
                "상태창 기능을 사용할 수 없습니다.",
            )
        else:
            try:
                result = callback(requested)
                if not isinstance(result, ActionResult):
                    raise TypeError("overlay callback must return ActionResult")
            except Exception:
                result = ActionResult(
                    False,
                    "상태창 설정 실패",
                    "설정을 저장하지 못했습니다.",
                )
        if result.ok:
            self.set_overlay_settings(requested)
            self._overlay_status.configure(
                text=result.detail or result.title,
                foreground=GREEN,
            )
            if hasattr(self, "_overlay_quick_status"):
                self._overlay_quick_status.configure(text=" ")
        else:
            if result.state_uncertain and result.overlay_settings is not None:
                # A native presenter or disk failure may leave the actual
                # topmost window hidden even though the old setting was ON.
                # Reflect the observed runtime state instead of lying with an
                # out-of-date action label.
                self.set_overlay_settings(result.overlay_settings)
            else:
                self.set_overlay_settings(self._overlay_committed)
            self._overlay_status.configure(
                text=result.detail or result.title,
                foreground=RED,
            )
        return result

    def _reset_overlay_position(self) -> None:
        callback = self._on_overlay_reset_position
        if callback is None:
            return
        try:
            result = callback()
            if not isinstance(result, ActionResult):
                raise TypeError("overlay reset callback must return ActionResult")
        except Exception:
            result = ActionResult(False, "위치 초기화 실패", "다시 시도해 주세요.")
        self._overlay_status.configure(
            text=result.detail or result.title,
            foreground=GREEN if result.ok else RED,
        )

    def _set_settings_enabled(self, enabled: bool) -> None:
        state = "!disabled" if enabled else "disabled"
        widgets: list[ttk.Widget] = [
            self._orientation_combo,
            self._orientation_flip,
        ]
        for widget in widgets:
            try:
                widget.state([state])
            except tk.TclError:
                continue

    def _provider_changed(self, *_args: object) -> None:
        self._admin_key.set("")

    def _selected_rotation(self) -> str:
        view = _VIEW_LABEL_TO_KEY.get(self._orientation_view.get())
        return compose_rotation(view, bool(self._orientation_inverted.get()))

    def _orientation_changed(self, *_args: object) -> None:
        try:
            rotation = self._selected_rotation()
        except ValueError:
            return
        if self._on_orientation_change is not None:
            self._on_orientation_change(rotation)

    def _detect_device(self) -> None:
        self._device_button.state(["disabled"])
        result = self._invoke(self._on_detect_device, "장치를 확인하지 못했습니다")
        if result.pending:
            self._device_title.configure(text=result.title, foreground=CYAN)
            self._device_detail.configure(text=result.detail or " ")
        else:
            self.complete_device(result)

    def update_codex_account(self, snapshot: CodexAccountSnapshot) -> None:
        """Apply a non-secret account snapshot on Tk's main thread."""

        require_main_thread()
        self._ensure_open()
        state = str(getattr(snapshot, "state", "unavailable"))
        pending = bool(getattr(snapshot, "login_pending", False))
        email = getattr(snapshot, "email", None)
        plan = getattr(snapshot, "plan_type", None)
        if state in {"ready", "delayed", "no_data"} and email:
            self._codex_identity.configure(text=f"{email} · {plan or '플랜 미상'}")
        else:
            self._codex_identity.configure(text={
                "login_pending": "ChatGPT 로그인 대기 중",
                "signed_out": "연결된 계정 없음",
                "auth_error": "로그인이 필요합니다",
                "unavailable": "Codex CLI 확인 필요",
            }.get(state, "한도 정보 없음"))
        windows = getattr(snapshot, "windows", ())
        limit_lines = []
        for window in tuple(windows)[:2]:
            duration = getattr(window, "duration_mins", 0)
            label = f"{duration // 10080}주" if duration >= 10080 and duration % 10080 == 0 else f"{duration // 1440}일" if duration >= 1440 and duration % 1440 == 0 else f"{duration // 60}시간" if duration >= 60 and duration % 60 == 0 else f"{duration}분"
            remaining = getattr(window, "remaining_percent", None)
            reset = getattr(window, "resets_at", None)
            reset_text = reset.astimezone().strftime("%m-%d %H:%M") if reset is not None else "초기화 미상"
            limit_lines.append(f"{label} 남음 {remaining:.0f}% · {reset_text}" if remaining is not None else f"{label} 한도 미상 · {reset_text}")
        self._codex_limits.configure(text=" / ".join(limit_lines) if limit_lines else "한도 정보 없음")
        balance = getattr(snapshot, "credit_balance", None)
        unlimited = bool(getattr(snapshot, "credits_unlimited", False))
        if unlimited:
            credits_text = "크레딧 무제한"
        else:
            try:
                credit_value = Decimal(str(balance)) if balance is not None and str(balance).strip() else None
                if credit_value is None or not credit_value.is_finite() or credit_value < 0:
                    raise InvalidOperation
                credit_display = f"{credit_value:,.2f}".rstrip("0").rstrip(".")
                credits_text = f"크레딧 {credit_display}"
            except (InvalidOperation, ValueError):
                credits_text = "크레딧 확인 불가"
        self._codex_credits.configure(text=credits_text)
        refreshed = getattr(snapshot, "updated_at", None)
        detail = "지연 · 마지막 확인 " + refreshed.astimezone().strftime("%H:%M") if state == "delayed" and refreshed is not None else getattr(snapshot, "error_detail", None) or " "
        if detail.strip():
            self._set_codex_feedback(
                detail, RED if state in {"auth_error", "unavailable", "error"} else SECONDARY,
            )
        self._codex_login_button.state(["disabled"] if pending or self._on_codex_login is None else ["!disabled"])
        self._codex_cancel_button.state(["!disabled"] if pending and self._on_codex_cancel is not None else ["disabled"])
        connected = state in {"ready", "delayed", "no_data"}
        self._codex_logout_button.state(["!disabled"] if connected and self._on_codex_logout is not None else ["disabled"])
        for button in (self._codex_login_button, self._codex_cancel_button, self._codex_logout_button):
            button.grid_remove()
        if pending and self._on_codex_cancel is not None:
            self._codex_cancel_button.grid()
        elif connected and self._on_codex_logout is not None:
            self._codex_logout_button.grid()
        else:
            self._codex_login_button.grid()

    def update_update_status(self, snapshot: UpdateSnapshot) -> None:
        """Refresh fixed-size update feedback without moving setup controls."""

        require_main_thread()
        self._ensure_open()
        state = str(getattr(snapshot, "state", "idle"))
        message = str(getattr(snapshot, "message", ""))
        version = getattr(snapshot, "version", None)
        self._update_open_button.configure(
            text="업데이트 · 새 버전" if state in {"available", "ready", "manual_required"} else "업데이트"
        )
        self._update_message.configure(text=(f"{version} · {message}" if version else message)[:70] or "최신 버전 확인 전")
        self._update_apply_button.state(["!disabled"] if state in {"available", "ready"} and self._on_update_apply is not None else ["disabled"])
        self._update_dismiss_button.state(["!disabled"] if state in {"available", "manual_required"} and self._on_update_dismiss is not None else ["disabled"])
        self._update_release_button.state(["!disabled"] if state == "manual_required" and self._on_update_open_release is not None else ["disabled"])

    def _set_codex_feedback(self, text: str, color: str = SECONDARY) -> None:
        message = text.strip()[:80]
        self._codex_feedback.configure(text=message or " ", foreground=color)
        if message:
            self._codex_feedback.grid()
        else:
            self._codex_feedback.grid_remove()

    def _account_action(self, action: Action | None, fallback: str) -> None:
        if action is None:
            return
        result = self._invoke(action, fallback)
        self._set_codex_feedback(
            result.title + (f" · {result.detail}" if result.detail else ""),
            SECONDARY if result.ok else RED,
        )

    def _confirm_codex_logout(self) -> None:
        if self._on_codex_logout is not None and messagebox.askyesno(
            "Codex 로그아웃",
            "Mini Monitor 전용 Codex 계정에서 로그아웃할까요? 기본 Codex 앱 계정은 변경되지 않습니다.",
            parent=self._window,
        ):
            self._account_action(self._on_codex_logout, "로그아웃하지 못했습니다")

    def _refresh_codex_account(self) -> None:
        selection = SetupSelection(AIProviderKind.CODEX_ACCOUNT.value, False)
        result = self._invoke(lambda: self._on_check_usage(selection), "한도를 확인하지 못했습니다")
        self._set_codex_feedback(
            result.title + (f" · {result.detail}" if result.detail else ""),
            SECONDARY if result.ok else RED,
        )

    def _select_codex_cli(self) -> None:
        if self._on_codex_cli_selected is None:
            return
        path = filedialog.askopenfilename(parent=self._window, title="Codex CLI 선택", filetypes=(("실행 파일", "*.exe"),))
        if path:
            result = self._invoke(lambda: self._on_codex_cli_selected(Path(path)), "CLI를 확인하지 못했습니다")
            self._set_codex_feedback(result.title, SECONDARY if result.ok else RED)

    def _update_action(self, action: Action | None, fallback: str) -> None:
        if action is None:
            return
        result = self._invoke(action, fallback)
        self._update_message.configure(text=result.title[:70])

    def _check_usage(self) -> None:
        self._usage_busy = True
        self._usage_button.state(["disabled"])
        self._start_button.state(["disabled"])
        self._set_settings_enabled(False)
        self._update_brightness_enabled()
        self._usage_status.configure(text="확인 중…", foreground=CYAN)
        self._set_codex_feedback("사용량 확인 중…", CYAN)
        self._window.update_idletasks()
        result = self._invoke(lambda: self._on_check_usage(self.selection), "사용량을 확인하지 못했습니다")
        if result.pending:
            self._usage_status.configure(text=result.title, foreground=CYAN)
            self._usage_detail.configure(text=result.detail or " ")
            self._set_codex_feedback(
                result.title + (f" · {result.detail}" if result.detail else ""), CYAN,
            )
        else:
            self.complete_usage(result)

    def _start(self) -> None:
        self._lifecycle_busy = True
        self._start_button.state(["disabled"])
        self._usage_button.state(["disabled"])
        self._set_settings_enabled(False)
        self._update_brightness_enabled()
        self._run_status.configure(text="시작 중…", foreground=CYAN)
        self._window.update_idletasks()
        try:
            selection = self.selection
        except ValueError:
            self.complete_start(
                ActionResult(
                    False,
                    "OpenAI 설정값을 확인하세요",
                    "Usage 60초·Costs 600초 이상, 예산은 양수만 입력할 수 있습니다.",
                ),
                running=False,
            )
            return
        # Clear before returning to Tk's event loop. The selected value is
        # handed off exactly once and is never kept in a widget after submit.
        self.clear_secret_entry()
        result = self._invoke(lambda: self._on_start(selection), "모니터를 시작하지 못했습니다")
        if result.pending:
            self._run_status.configure(text=result.title, foreground=CYAN)
        elif result.ok:
            self.complete_start(result, running=True)
        else:
            self.complete_start(result, running=False)

    def _stop(self) -> None:
        self._lifecycle_busy = True
        self._stop_button.state(["disabled"])
        self._usage_button.state(["disabled"])
        self._update_brightness_enabled()
        self._run_status.configure(text="중지 중…", foreground=CYAN)
        result = self._invoke(self._on_stop, "모니터를 중지하지 못했습니다")
        if result.pending:
            self._run_status.configure(text=result.title, foreground=CYAN)
        else:
            self.complete_stop(result, running=not result.ok)

    def _reconnect(self) -> None:
        result = self._invoke(self._on_reconnect, "다시 연결을 요청하지 못했습니다")
        self._device_title.configure(text=result.title, foreground=CYAN if result.ok else RED)
        self._device_detail.configure(text=result.detail or " ")

    @staticmethod
    def _set_dot(canvas: tk.Canvas, color: str) -> None:
        canvas.delete("all")
        canvas.create_oval(2, 2, 10, 10, fill=color, outline=color)

    @staticmethod
    def _invoke(action: Action, fallback: str) -> ActionResult:
        try:
            result = action()
        except Exception as error:
            # Never surface arbitrary exception text here: it may contain a
            # local path, a serial backend detail, or secret-adjacent input.
            return ActionResult(False, fallback, type(error).__name__)
        if not isinstance(result, ActionResult):
            return ActionResult(False, fallback, "잘못된 내부 응답")
        return result

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("setup window is closed")
