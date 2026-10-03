# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

import pytest
from PIL import Image

from ai_mini_monitor.ui.layered_window import (
    AC_SRC_ALPHA,
    GA_ROOT,
    GWL_EXSTYLE,
    ULW_ALPHA,
    WS_EX_LAYERED,
    WS_EX_TOOLWINDOW,
    LayeredWindowPresenter,
    premultiplied_bgra_bytes,
)


class FakeWindow:
    def __init__(self, hwnd: int = 123) -> None:
        self.hwnd = hwnd
        self.calls = 0

    def winfo_id(self) -> int:
        self.calls += 1
        return self.hwnd


class FakeWin32Api:
    def __init__(self, *, extended_style: int = 0x20) -> None:
        self.extended_style = extended_style
        self.events: list[tuple[object, ...]] = []
        self.buffers: list[ctypes.Array[ctypes.c_char]] = []
        self.uploaded = b""
        self.select_calls = 0
        self.fail_update: BaseException | None = None
        self.fail_initial_select: BaseException | None = None
        self.fail_restore: BaseException | None = None
        self.fail_delete_object: BaseException | None = None
        self.fail_delete_dc: BaseException | None = None
        self.fail_release_dc: BaseException | None = None
        self.fail_get_dc: BaseException | None = None
        self.fail_create_compatible_dc: BaseException | None = None
        self.fail_create_dib: BaseException | None = None

    def get_ancestor(self, hwnd: int, flags: int) -> int:
        self.events.append(("get_ancestor", hwnd, flags))
        return 999

    def get_window_long(self, hwnd: int, index: int) -> int:
        self.events.append(("get_window_long", hwnd, index))
        return self.extended_style

    def set_window_long(self, hwnd: int, index: int, value: int) -> None:
        self.events.append(("set_window_long", hwnd, index, value))
        self.extended_style = value

    def get_dc(self, hwnd: int) -> int:
        self.events.append(("get_dc", hwnd))
        if self.fail_get_dc is not None:
            raise self.fail_get_dc
        return 101

    def release_dc(self, hwnd: int, dc: int) -> None:
        self.events.append(("release_dc", hwnd, dc))
        if self.fail_release_dc is not None:
            raise self.fail_release_dc

    def create_compatible_dc(self, dc: int) -> int:
        self.events.append(("create_compatible_dc", dc))
        if self.fail_create_compatible_dc is not None:
            raise self.fail_create_compatible_dc
        return 202

    def delete_dc(self, dc: int) -> None:
        self.events.append(("delete_dc", dc))
        if self.fail_delete_dc is not None:
            raise self.fail_delete_dc

    def create_dib_section(
        self,
        dc: int,
        width: int,
        height: int,
        *,
        bits_per_pixel: int,
        top_down: bool,
    ) -> tuple[int, int]:
        self.events.append(
            (
                "create_dib_section",
                dc,
                width,
                height,
                bits_per_pixel,
                top_down,
            )
        )
        if self.fail_create_dib is not None:
            raise self.fail_create_dib
        buffer = ctypes.create_string_buffer(width * height * 4)
        self.buffers.append(buffer)
        return 303, ctypes.addressof(buffer)

    def select_object(self, dc: int, obj: int) -> int:
        self.select_calls += 1
        self.events.append(("select_object", dc, obj))
        if self.select_calls == 1:
            if self.fail_initial_select is not None:
                raise self.fail_initial_select
            return 404
        if self.fail_restore is not None:
            raise self.fail_restore
        return 303

    def delete_object(self, obj: int) -> None:
        self.events.append(("delete_object", obj))
        if self.fail_delete_object is not None:
            raise self.fail_delete_object

    def update_layered_window(
        self,
        hwnd: int,
        screen_dc: int,
        memory_dc: int,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        source_constant_alpha: int,
        alpha_format: int,
        flags: int,
    ) -> None:
        self.uploaded = ctypes.string_at(
            ctypes.addressof(self.buffers[-1]), width * height * 4
        )
        self.events.append(
            (
                "update_layered_window",
                hwnd,
                screen_dc,
                memory_dc,
                x,
                y,
                width,
                height,
                source_constant_alpha,
                alpha_format,
                flags,
            )
        )
        if self.fail_update is not None:
            raise self.fail_update


def _event_names(api: FakeWin32Api) -> list[object]:
    return [event[0] for event in api.events]


def test_premultiplied_bgra_bytes_has_exact_channel_order_and_rounding() -> None:
    image = Image.new("RGBA", (4, 1))
    image.putdata(
        [
            (255, 0, 0, 128),
            (1, 2, 3, 0),
            (10, 20, 30, 255),
            (128, 129, 130, 127),
        ]
    )

    assert premultiplied_bgra_bytes(image) == bytes(
        [
            0,
            0,
            128,
            128,
            0,
            0,
            0,
            0,
            30,
            20,
            10,
            255,
            65,
            64,
            64,
            127,
        ]
    )


def test_premultiplied_bgra_bytes_converts_non_rgba_input() -> None:
    assert premultiplied_bgra_bytes(Image.new("RGB", (1, 1), (4, 5, 6))) == bytes(
        (6, 5, 4, 255)
    )


def test_present_uploads_top_down_32bpp_frame_with_required_flags() -> None:
    api = FakeWin32Api()
    window = FakeWindow()
    presenter = LayeredWindowPresenter(window, api=api)
    image = Image.new("RGBA", (2, 1))
    image.putdata(((255, 0, 0, 128), (10, 20, 30, 255)))

    presenter.present(image, x=-120, y=75)

    assert presenter.hwnd == 999
    assert ("get_ancestor", 123, GA_ROOT) in api.events
    assert (
        "set_window_long",
        999,
        GWL_EXSTYLE,
        0x20 | WS_EX_LAYERED | WS_EX_TOOLWINDOW,
    ) in api.events
    assert ("create_dib_section", 101, 2, 1, 32, True) in api.events
    assert api.uploaded == bytes((0, 0, 128, 128, 30, 20, 10, 255))
    assert (
        "update_layered_window",
        999,
        101,
        202,
        -120,
        75,
        2,
        1,
        255,
        AC_SRC_ALPHA,
        ULW_ALPHA,
    ) in api.events
    assert _event_names(api)[-4:] == [
        "select_object",
        "delete_object",
        "delete_dc",
        "release_dc",
    ]


def test_hwnd_and_extended_style_are_resolved_only_once() -> None:
    required = WS_EX_LAYERED | WS_EX_TOOLWINDOW
    api = FakeWin32Api(extended_style=required | 0x40)
    window = FakeWindow()
    presenter = LayeredWindowPresenter(window, api=api)

    presenter.present(Image.new("RGBA", (1, 1)), x=0, y=0)
    api.select_calls = 0
    presenter.present(Image.new("RGBA", (1, 1)), x=1, y=2)

    assert window.calls == 1
    assert _event_names(api).count("get_ancestor") == 1
    assert _event_names(api).count("get_window_long") == 1
    assert "set_window_long" not in _event_names(api)


def test_update_failure_restores_and_releases_every_resource() -> None:
    api = FakeWin32Api()
    api.fail_update = RuntimeError("upload failed")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="upload failed"):
        presenter.present(Image.new("RGBA", (2, 2)), x=0, y=0)

    assert _event_names(api)[-4:] == [
        "select_object",
        "delete_object",
        "delete_dc",
        "release_dc",
    ]


def test_initial_select_failure_deletes_unselected_bitmap_and_dcs() -> None:
    api = FakeWin32Api()
    api.fail_initial_select = RuntimeError("select failed")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="select failed"):
        presenter.present(Image.new("RGBA", (2, 2)), x=0, y=0)

    assert _event_names(api)[-3:] == [
        "delete_object",
        "delete_dc",
        "release_dc",
    ]
    assert _event_names(api).count("select_object") == 1


def test_dib_creation_failure_still_releases_both_dcs() -> None:
    api = FakeWin32Api()
    api.fail_create_dib = RuntimeError("dib failed")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="dib failed"):
        presenter.present(Image.new("RGBA", (2, 2)), x=0, y=0)

    assert _event_names(api)[-2:] == ["delete_dc", "release_dc"]
    assert "delete_object" not in _event_names(api)


def test_compatible_dc_failure_releases_acquired_screen_dc() -> None:
    api = FakeWin32Api()
    api.fail_create_compatible_dc = RuntimeError("compatible dc failed")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="compatible dc failed"):
        presenter.present(Image.new("RGBA", (2, 2)), x=0, y=0)

    assert _event_names(api)[-1:] == ["release_dc"]
    assert "delete_dc" not in _event_names(api)


def test_screen_dc_failure_has_no_resource_to_release() -> None:
    api = FakeWin32Api()
    api.fail_get_dc = RuntimeError("screen dc failed")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="screen dc failed"):
        presenter.present(Image.new("RGBA", (2, 2)), x=0, y=0)

    assert _event_names(api)[-1:] == ["get_dc"]


def test_cleanup_failures_do_not_prevent_later_cleanup_attempts() -> None:
    api = FakeWin32Api()
    api.fail_restore = RuntimeError("restore failed")
    api.fail_delete_object = RuntimeError("delete object failed")
    api.fail_delete_dc = RuntimeError("delete dc failed")
    api.fail_release_dc = RuntimeError("release dc failed")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="restore failed"):
        presenter.present(Image.new("RGBA", (2, 2)), x=0, y=0)

    assert _event_names(api)[-4:] == [
        "select_object",
        "delete_object",
        "delete_dc",
        "release_dc",
    ]


def test_primary_failure_is_not_replaced_by_cleanup_failure() -> None:
    api = FakeWin32Api()
    api.fail_update = RuntimeError("primary upload error")
    api.fail_restore = RuntimeError("secondary restore error")
    presenter = LayeredWindowPresenter(FakeWindow(), api=api)

    with pytest.raises(RuntimeError, match="primary upload error"):
        presenter.present(Image.new("RGBA", (1, 1)), x=0, y=0)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Win32 layered windows")
def test_real_win32_tk_presenter_smoke_without_device_io() -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk desktop is unavailable: {exc}")

    top: tk.Toplevel | None = None
    try:
        root.withdraw()
        top = tk.Toplevel(root)
        top.withdraw()
        top.overrideredirect(True)
        top.update_idletasks()
        presenter = LayeredWindowPresenter(top)
        presenter.present(
            Image.new("RGBA", (2, 2), (20, 40, 60, 128)),
            x=-32_000,
            y=-32_000,
        )
        assert presenter.hwnd
    finally:
        if top is not None:
            top.destroy()
        root.destroy()
