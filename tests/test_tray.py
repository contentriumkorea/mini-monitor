# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from types import SimpleNamespace

from ai_mini_monitor.ui import tray as tray_module
from ai_mini_monitor.ui.tray import TrayCommand, TrayController


class FakeMenuItem:
    def __init__(self, text, action, default=False) -> None:
        self.text = text
        self.action = action
        self.default = default


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:
        self.items = items


class FakeIcon:
    def __init__(self, name, image, title, menu) -> None:
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.stopped = False

    def run(self) -> None:
        return

    def stop(self) -> None:
        self.stopped = True

    def update_menu(self) -> None:
        return


def test_tray_overlay_menu_only_queues_a_toggle_intent(monkeypatch) -> None:
    fake = SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
    monkeypatch.setattr(tray_module, "_load_pystray", lambda: fake)
    controller = TrayController()

    controller.start()
    assert controller._thread is not None
    controller._thread.join(timeout=2)
    assert controller._icon is not None
    items = [item for item in controller._icon.menu.items if isinstance(item, FakeMenuItem)]
    overlay_item = next(item for item in items if item.text == "PC 상태창 켜기/끄기")
    assert all("미리보기" not in str(item.text) for item in items)

    overlay_item.action(None, None)

    assert controller.commands.get_nowait() is TrayCommand.OVERLAY
    controller.stop()


def test_tray_rejects_non_enum_commands() -> None:
    controller = TrayController()

    try:
        controller.emit("overlay")  # type: ignore[arg-type]
    except TypeError as error:
        assert "TrayCommand" in str(error)
    else:
        raise AssertionError("raw strings must not enter the tray queue")
