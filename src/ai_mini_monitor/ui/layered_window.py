# SPDX-License-Identifier: GPL-3.0-or-later

"""Win32 per-pixel-alpha presenter for a single Tk top-level window.

The presenter intentionally owns no persistent GDI resources.  Every call
uploads one top-down 32-bit DIB with premultiplied BGRA pixels and releases all
temporary handles before returning.  This keeps its lifecycle independent of
Tk window destruction and makes failures recoverable on the next frame.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Protocol

from PIL import Image


GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

BI_RGB = 0
DIB_RGB_COLORS = 0
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
ULW_ALPHA = 0x00000002
HGDI_ERROR = ctypes.c_void_p(-1).value


class _BlendFunction(ctypes.Structure):
    _fields_ = (
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    )


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _RgbQuad(ctypes.Structure):
    _fields_ = (
        ("rgbBlue", wintypes.BYTE),
        ("rgbGreen", wintypes.BYTE),
        ("rgbRed", wintypes.BYTE),
        ("rgbReserved", wintypes.BYTE),
    )


class _BitmapInfo(ctypes.Structure):
    _fields_ = (
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", _RgbQuad * 1),
    )


class _TkWindow(Protocol):
    def winfo_id(self) -> int: ...


class _LayeredApi(Protocol):
    """Python-friendly seam around the low-level Win32 calls."""

    def get_ancestor(self, hwnd: int, flags: int) -> int: ...

    def get_window_long(self, hwnd: int, index: int) -> int: ...

    def set_window_long(self, hwnd: int, index: int, value: int) -> None: ...

    def get_dc(self, hwnd: int) -> int: ...

    def release_dc(self, hwnd: int, dc: int) -> None: ...

    def create_compatible_dc(self, dc: int) -> int: ...

    def delete_dc(self, dc: int) -> None: ...

    def create_dib_section(
        self,
        dc: int,
        width: int,
        height: int,
        *,
        bits_per_pixel: int,
        top_down: bool,
    ) -> tuple[int, int]: ...

    def select_object(self, dc: int, obj: int) -> int: ...

    def delete_object(self, obj: int) -> None: ...

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
    ) -> None: ...


def premultiplied_bgra_bytes(image: Image.Image) -> bytes:
    """Return top-down, premultiplied BGRA bytes for ``image``.

    Pillow's ``RGBa`` mode performs the alpha multiplication in native code
    with its defined 8-bit rounding.  The raw ``BGRa`` packer then swaps the
    color channels without multiplying them a second time.
    """

    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    return rgba.convert("RGBa").tobytes("raw", "BGRa")


class LayeredWindowPresenter:
    """Upload RGBA frames to one Tk top-level HWND with per-pixel alpha.

    ``window`` must expose Tk's ``winfo_id()``.  The real top-level HWND is
    resolved with ``GetAncestor(..., GA_ROOT)`` on the first frame.  Call
    :meth:`present` from the Tk/UI thread after the top-level has been created.

    The private ``api`` injection point exists to make handle and failure
    behavior testable without opening a native window.
    """

    def __init__(self, window: _TkWindow, *, api: _LayeredApi | None = None) -> None:
        self._window = window
        self._api = api if api is not None else _NativeWin32Api()
        self._hwnd: int | None = None
        self._style_applied = False

    @property
    def hwnd(self) -> int | None:
        """Resolved root HWND, or ``None`` before the first presentation."""

        return self._hwnd

    def present(self, image: Image.Image, *, x: int, y: int) -> None:
        """Present one non-empty image with its top-left corner at ``x, y``."""

        if not isinstance(image, Image.Image):
            raise TypeError("image must be a PIL Image")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("layered window image must have positive dimensions")
        if isinstance(x, bool) or not isinstance(x, int):
            raise TypeError("x must be an integer")
        if isinstance(y, bool) or not isinstance(y, int):
            raise TypeError("y must be an integer")

        hwnd = self._resolve_hwnd()
        self._ensure_extended_style(hwnd)
        pixels = premultiplied_bgra_bytes(image)

        api = self._api
        screen_dc: int | None = None
        memory_dc: int | None = None
        bitmap: int | None = None
        previous_object: int | None = None
        primary_error: BaseException | None = None
        cleanup_errors: list[BaseException] = []

        try:
            screen_dc = api.get_dc(0)
            memory_dc = api.create_compatible_dc(screen_dc)
            bitmap, bits = api.create_dib_section(
                screen_dc,
                width,
                height,
                bits_per_pixel=32,
                top_down=True,
            )
            ctypes.memmove(bits, pixels, len(pixels))
            previous_object = api.select_object(memory_dc, bitmap)
            api.update_layered_window(
                hwnd,
                screen_dc,
                memory_dc,
                x=x,
                y=y,
                width=width,
                height=height,
                source_constant_alpha=255,
                alpha_format=AC_SRC_ALPHA,
                flags=ULW_ALPHA,
            )
        except BaseException as exc:  # cleanup must also run on interrupts
            primary_error = exc
        finally:
            if previous_object is not None and memory_dc is not None:
                try:
                    api.select_object(memory_dc, previous_object)
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if bitmap is not None:
                try:
                    api.delete_object(bitmap)
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if memory_dc is not None:
                try:
                    api.delete_dc(memory_dc)
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if screen_dc is not None:
                try:
                    api.release_dc(0, screen_dc)
                except BaseException as exc:
                    cleanup_errors.append(exc)

        if primary_error is not None:
            raise primary_error.with_traceback(primary_error.__traceback__)
        if cleanup_errors:
            raise cleanup_errors[0]

    def _resolve_hwnd(self) -> int:
        if self._hwnd is not None:
            return self._hwnd
        child_hwnd = int(self._window.winfo_id())
        if not child_hwnd:
            raise OSError("Tk window does not have a native handle")
        hwnd = int(self._api.get_ancestor(child_hwnd, GA_ROOT))
        if not hwnd:
            raise OSError("GetAncestor(GA_ROOT) did not return a top-level HWND")
        self._hwnd = hwnd
        return hwnd

    def _ensure_extended_style(self, hwnd: int) -> None:
        if self._style_applied:
            return
        existing = self._api.get_window_long(hwnd, GWL_EXSTYLE)
        required = WS_EX_LAYERED | WS_EX_TOOLWINDOW
        updated = existing | required
        if updated != existing:
            self._api.set_window_long(hwnd, GWL_EXSTYLE, updated)
        self._style_applied = True


class _NativeWin32Api:
    """ctypes implementation of the narrow API used by the presenter."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("per-pixel layered windows are supported only on Windows")

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        user32 = self._user32
        gdi32 = self._gdi32

        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND

        get_long_name = (
            "GetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "GetWindowLongW"
        )
        set_long_name = (
            "SetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "SetWindowLongW"
        )
        self._get_window_long = getattr(user32, get_long_name)
        self._set_window_long = getattr(user32, set_long_name)
        self._get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
        self._get_window_long.restype = ctypes.c_ssize_t
        self._set_window_long.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_ssize_t,
        )
        self._set_window_long.restype = ctypes.c_ssize_t

        user32.GetDC.argtypes = (wintypes.HWND,)
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
        user32.ReleaseDC.restype = ctypes.c_int

        gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = (wintypes.HDC,)
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.CreateDIBSection.argtypes = (
            wintypes.HDC,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        )
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        gdi32.DeleteObject.restype = wintypes.BOOL

        user32.UpdateLayeredWindow.argtypes = (
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE),
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            wintypes.COLORREF,
            ctypes.POINTER(_BlendFunction),
            wintypes.DWORD,
        )
        user32.UpdateLayeredWindow.restype = wintypes.BOOL

    def get_ancestor(self, hwnd: int, flags: int) -> int:
        result = self._user32.GetAncestor(hwnd, flags)
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(result)

    def get_window_long(self, hwnd: int, index: int) -> int:
        ctypes.set_last_error(0)
        result = int(self._get_window_long(hwnd, index))
        error = ctypes.get_last_error()
        if result == 0 and error:
            raise ctypes.WinError(error)
        return result

    def set_window_long(self, hwnd: int, index: int, value: int) -> None:
        ctypes.set_last_error(0)
        result = int(self._set_window_long(hwnd, index, value))
        error = ctypes.get_last_error()
        if result == 0 and error:
            raise ctypes.WinError(error)

    def get_dc(self, hwnd: int) -> int:
        result = self._user32.GetDC(hwnd)
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(result)

    def release_dc(self, hwnd: int, dc: int) -> None:
        if not self._user32.ReleaseDC(hwnd, dc):
            raise ctypes.WinError(ctypes.get_last_error())

    def create_compatible_dc(self, dc: int) -> int:
        result = self._gdi32.CreateCompatibleDC(dc)
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(result)

    def delete_dc(self, dc: int) -> None:
        if not self._gdi32.DeleteDC(dc):
            raise ctypes.WinError(ctypes.get_last_error())

    def create_dib_section(
        self,
        dc: int,
        width: int,
        height: int,
        *,
        bits_per_pixel: int,
        top_down: bool,
    ) -> tuple[int, int]:
        bitmap_info = _BitmapInfo()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height if top_down else height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = bits_per_pixel
        bitmap_info.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        bitmap = self._gdi32.CreateDIBSection(
            dc,
            ctypes.byref(bitmap_info),
            DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits.value:
            error = ctypes.get_last_error()
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            raise ctypes.WinError(error)
        return int(bitmap), int(bits.value)

    def select_object(self, dc: int, obj: int) -> int:
        result = self._gdi32.SelectObject(dc, obj)
        result_value = int(result) if result else 0
        if not result_value or result_value == HGDI_ERROR:
            raise ctypes.WinError(ctypes.get_last_error())
        return result_value

    def delete_object(self, obj: int) -> None:
        if not self._gdi32.DeleteObject(obj):
            raise ctypes.WinError(ctypes.get_last_error())

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
        destination = wintypes.POINT(x, y)
        size = wintypes.SIZE(width, height)
        source = wintypes.POINT(0, 0)
        blend = _BlendFunction(
            AC_SRC_OVER,
            0,
            source_constant_alpha,
            alpha_format,
        )
        if not self._user32.UpdateLayeredWindow(
            hwnd,
            screen_dc,
            ctypes.byref(destination),
            ctypes.byref(size),
            memory_dc,
            ctypes.byref(source),
            0,
            ctypes.byref(blend),
            flags,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
