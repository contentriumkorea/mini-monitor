# SPDX-License-Identifier: GPL-3.0-or-later

"""A small Tk preview window whose operations are main-thread-only."""

from __future__ import annotations

import threading
import tkinter as tk

from PIL import Image, ImageTk


class MainThreadRequired(RuntimeError):
    pass


def require_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise MainThreadRequired("Tk preview operations must run on the main thread")


class PreviewWindow:
    """Display the latest Pillow framebuffer without touching serial I/O."""

    def __init__(
        self,
        root: tk.Misc | None = None,
        *,
        title: str = "AI Mini Monitor Preview",
    ) -> None:
        require_main_thread()
        self._owns_root = root is None
        self._window: tk.Tk | tk.Toplevel
        if root is None:
            self._window = tk.Tk(className="AIMiniMonitorPreview")
        else:
            self._window = tk.Toplevel(root, class_="AIMiniMonitorPreview")
        self._closed = False
        self._photo: ImageTk.PhotoImage | None = None

        self._window.title(title)
        self._window.resizable(False, False)
        self._window.configure(background="#070A0F")
        self._window.protocol("WM_DELETE_WINDOW", self.hide)
        self._image_label = tk.Label(
            self._window,
            background="#070A0F",
            borderwidth=0,
            highlightthickness=0,
        )
        self._image_label.pack(padx=12, pady=12)
        self._window.withdraw()

    @property
    def window(self) -> tk.Tk | tk.Toplevel:
        return self._window

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def visible(self) -> bool:
        require_main_thread()
        return not self._closed and bool(self._window.winfo_viewable())

    def update_image(self, image: Image.Image) -> None:
        """Replace the preview image; call from the Tk/main thread only."""

        require_main_thread()
        self._ensure_open()
        if not isinstance(image, Image.Image):
            raise TypeError("preview image must be a Pillow Image")
        rgb = image.convert("RGB")
        self._photo = ImageTk.PhotoImage(rgb, master=self._window)
        self._image_label.configure(image=self._photo)

    def show(self) -> None:
        require_main_thread()
        self._ensure_open()
        self._window.deiconify()
        self._window.lift()

    def hide(self) -> None:
        require_main_thread()
        if not self._closed:
            self._window.withdraw()

    def toggle(self) -> None:
        require_main_thread()
        if self.visible:
            self.hide()
        else:
            self.show()

    def run(self) -> None:
        """Run the Tk loop when this object owns the root window."""

        require_main_thread()
        self._ensure_open()
        if not self._owns_root:
            raise RuntimeError("the caller-owned Tk root must run its own mainloop")
        self.show()
        self._window.mainloop()

    def close(self) -> None:
        require_main_thread()
        if self._closed:
            return
        self._closed = True
        self._photo = None
        self._window.destroy()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("preview window is closed")
