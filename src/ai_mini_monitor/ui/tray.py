# SPDX-License-Identifier: GPL-3.0-or-later

"""Win32 notification-area controller that emits queue-only commands."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from enum import Enum
from typing import Any, Protocol

from PIL import Image

from .app_icon import load_icon_image


if sys.platform == "win32":
    # pystray reads this before backend import; the app is intentionally
    # Windows-only and must not probe GTK/AppIndicator backends.
    os.environ["PYSTRAY_BACKEND"] = "win32"


LOGGER = logging.getLogger(__name__)


class TrayCommand(str, Enum):
    STATUS = "status"
    PROVIDER = "provider"
    SETUP = "setup"
    OVERLAY = "overlay"
    RECONNECT = "reconnect"
    EXIT = "exit"


class CommandQueue(Protocol):
    def put_nowait(self, item: TrayCommand) -> None: ...


def create_command_queue() -> queue.SimpleQueue[TrayCommand]:
    return queue.SimpleQueue()


def _load_pystray() -> Any:
    if sys.platform != "win32":
        raise OSError("the AI Mini Monitor tray requires Windows")
    os.environ["PYSTRAY_BACKEND"] = "win32"
    import pystray

    return pystray


def _default_icon() -> Image.Image:
    return load_icon_image()


class TrayController:
    """Own pystray on a worker thread and publish menu intent to a queue."""

    def __init__(
        self,
        command_queue: CommandQueue | None = None,
        *,
        title: str = "Mini Monitor",
        icon_image: Image.Image | None = None,
    ) -> None:
        self.commands = command_queue or create_command_queue()
        self.title = title
        self._icon_image = icon_image.copy() if icon_image is not None else _default_icon()
        self._lock = threading.RLock()
        self._status = "STARTING"
        self._provider = "NOT CONFIGURED"
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        pystray = _load_pystray()
        menu = pystray.Menu(
            pystray.MenuItem(self._status_text, self._action(TrayCommand.STATUS)),
            pystray.MenuItem(self._provider_text, self._action(TrayCommand.PROVIDER)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("설정 열기", self._action(TrayCommand.SETUP), default=True),
            pystray.MenuItem("PC 상태창 켜기/끄기", self._action(TrayCommand.OVERLAY)),
            pystray.MenuItem("미니 모니터 다시 연결", self._action(TrayCommand.RECONNECT)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self._action(TrayCommand.EXIT)),
        )
        self._icon = pystray.Icon(
            "ai-mini-monitor",
            self._icon_image,
            self.title,
            menu,
        )
        self._thread = threading.Thread(
            target=self._run_icon,
            name="ai-mini-monitor-tray",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        icon = self._icon
        if icon is not None:
            icon.stop()
        thread = self._thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout)
        self._icon = None
        self._thread = None

    def set_status(self, status: object) -> None:
        with self._lock:
            self._status = str(status).strip() or "UNKNOWN"
        self._refresh_menu()

    def set_provider(self, provider: object) -> None:
        with self._lock:
            self._provider = str(provider).strip() or "NOT CONFIGURED"
        self._refresh_menu()

    def notify_update(self, version: str) -> bool:
        """Show a bounded native notification; the app owns once-per-version gating."""

        icon = self._icon
        if icon is None or not version:
            return False
        try:
            icon.notify(f"Mini Monitor {version} 업데이트가 있습니다.", "Mini Monitor")
            return True
        except Exception:
            LOGGER.warning("could not show update notification")
            return False

    def emit(self, command: TrayCommand) -> None:
        """Publish a command without invoking preview or serial code."""

        if not isinstance(command, TrayCommand):
            raise TypeError("tray command must be a TrayCommand")
        try:
            self.commands.put_nowait(command)
        except queue.Full:
            LOGGER.warning("tray command queue is full; dropped %s", command.value)

    def _action(self, command: TrayCommand) -> Any:
        def callback(_icon: Any, _item: Any) -> None:
            self.emit(command)

        return callback

    def _status_text(self, _item: Any) -> str:
        with self._lock:
            return f"Status: {self._status}"

    def _provider_text(self, _item: Any) -> str:
        with self._lock:
            return f"Provider: {self._provider}"

    def _refresh_menu(self) -> None:
        icon = self._icon
        if icon is not None:
            try:
                icon.update_menu()
            except Exception:
                LOGGER.exception("failed to refresh tray menu")

    def _run_icon(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.run()
        except Exception:
            LOGGER.exception("tray icon loop failed")
