# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import pytest
from PIL import Image

from ai_mini_monitor import cli
from ai_mini_monitor.config import AppConfig


def test_no_serial_is_forwarded_to_the_normal_desktop_path(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_desktop(config, *, minimized: bool, enable_serial: bool, config_path=None, auto_exit_seconds=None) -> int:
        captured.update(
            config=config,
            minimized=minimized,
            enable_serial=enable_serial,
            config_path=config_path,
            auto_exit_seconds=auto_exit_seconds,
        )
        return 0

    monkeypatch.setattr(cli, "load_config", lambda _path: AppConfig())
    monkeypatch.setattr(cli, "setup_logging", lambda _level: object())
    monkeypatch.setattr(cli, "shutdown_logging", lambda _listener: None)
    monkeypatch.setattr(cli, "run_desktop", fake_desktop)

    assert cli.main(["--no-serial", "--minimized"]) == 0
    assert captured["enable_serial"] is False
    assert captured["minimized"] is True
    assert captured["config_path"] is None


def test_custom_config_path_is_forwarded_to_setup_saves(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    path = tmp_path / "custom.json"

    def fake_desktop(config, *, minimized: bool, enable_serial: bool, config_path=None, auto_exit_seconds=None) -> int:
        captured["config_path"] = config_path
        captured["auto_exit_seconds"] = auto_exit_seconds
        return 0

    monkeypatch.setattr(cli, "load_config", lambda _path: AppConfig())
    monkeypatch.setattr(cli, "setup_logging", lambda _level: object())
    monkeypatch.setattr(cli, "shutdown_logging", lambda _listener: None)
    monkeypatch.setattr(cli, "run_desktop", fake_desktop)

    assert cli.main(["--config", str(path), "--no-serial"]) == 0
    assert captured["config_path"] == path


def test_hidden_desktop_smoke_timeout_is_forwarded_without_enabling_serial(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_desktop(
        _config,
        *,
        minimized: bool,
        enable_serial: bool,
        config_path=None,
        auto_exit_seconds=None,
    ) -> int:
        captured.update(
            minimized=minimized,
            enable_serial=enable_serial,
            auto_exit_seconds=auto_exit_seconds,
        )
        return 0

    monkeypatch.setattr(cli, "load_config", lambda _path: AppConfig())
    monkeypatch.setattr(cli, "setup_logging", lambda _level: object())
    monkeypatch.setattr(cli, "shutdown_logging", lambda _listener: None)
    monkeypatch.setattr(cli, "run_desktop", fake_desktop)

    assert cli.main(["--desktop-smoke", "1.25", "--minimized", "--no-serial"]) == 0
    assert captured == {
        "minimized": True,
        "enable_serial": False,
        "auto_exit_seconds": 1.25,
    }


@pytest.mark.parametrize(
    ("rotation", "expected_size"),
    (
        ("landscape", (480, 320)),
        ("landscape_inverted", (480, 320)),
        ("portrait", (320, 480)),
        ("portrait_inverted", (320, 480)),
    ),
)
def test_preview_uses_saved_orientation_native_size_and_shows_it(
    monkeypatch,
    tmp_path,
    capsys,
    rotation: str,
    expected_size: tuple[int, int],
) -> None:
    config = AppConfig()
    config.device.rotation = rotation
    output = tmp_path / f"{rotation}.png"
    windows: list[object] = []

    class FakePreviewWindow:
        def __init__(self, *, title: str) -> None:
            self.title = title
            self.image_size: tuple[int, int] | None = None
            self.ran = False
            windows.append(self)

        def update_image(self, image: Image.Image) -> None:
            self.image_size = image.size

        def run(self) -> None:
            self.ran = True

    monkeypatch.setattr(cli, "load_config", lambda _path: config)
    monkeypatch.setattr(cli, "setup_logging", lambda _level: object())
    monkeypatch.setattr(cli, "shutdown_logging", lambda _listener: None)
    monkeypatch.setattr(cli, "PreviewWindow", FakePreviewWindow)

    assert cli.main(["--preview", "normal", "--preview-output", str(output)]) == 0
    with Image.open(output) as rendered:
        assert rendered.size == expected_size
    assert len(windows) == 1
    assert windows[0].image_size == expected_size
    assert windows[0].ran is True
    assert f"Native {expected_size[0]}x{expected_size[1]} DEMO preview:" in capsys.readouterr().out
