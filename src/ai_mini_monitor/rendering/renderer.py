from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from math import isfinite
import re
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from ..models import (
    AIData,
    AIProviderKind,
    ConnectionStatus,
    DisplaySnapshot,
    Metric,
    SyncStatus,
)
from .fonts import inter, metric_mono, mono
from .layout import (
    CONNECTION,
    CPU,
    GPU,
    LANDSCAPE_LAYOUT,
    MEMORY,
    DashboardLayout,
    Rect,
)
from .theme import DEFAULT_THEME, Theme


@dataclass(frozen=True, slots=True)
class TextPlacement:
    key: str
    anchor_xy: tuple[int, int]
    bbox: tuple[int, int, int, int]
    clip: Rect
    text: str
    anchor: str
    font_size: int
    font_name: tuple[str, str]
    drawn: bool = True

    @property
    def clipped(self) -> bool:
        left, top, right, bottom = self.bbox
        return left < self.clip.x or top < self.clip.y or right > self.clip.right or bottom > self.clip.bottom

    @property
    def baseline_y(self) -> int | None:
        """Return the text baseline when the placement uses a baseline anchor."""

        return self.anchor_xy[1] if self.anchor.endswith("s") else None


@dataclass(frozen=True, slots=True)
class GaugePlacement:
    key: str
    track: Rect
    fill: Rect | None
    ratio: float | None
    state: str


OVERLAY_LAYERS_INFO_KEY = "ai_mini_monitor_overlay_layers"


@dataclass(frozen=True, slots=True, init=False)
class OverlayLayers:
    """Semantic dashboard layers for a card-only translucent overlay.

    The stored Pillow images are deliberately private.  Public accessors
    return copies so an overlay consumer cannot mutate renderer-owned state or
    the layer metadata attached to a rendered framebuffer.
    """

    _size: tuple[int, int]
    _background_bytes: bytes = field(repr=False)
    _foreground_bytes: bytes = field(repr=False)
    _surface_mask_bytes: bytes = field(repr=False)

    def __init__(
        self,
        background: Image.Image,
        foreground: Image.Image,
        surface_mask: Image.Image,
    ) -> None:
        if background.mode != "RGB":
            raise ValueError("Overlay background must be RGB")
        if foreground.mode != "RGBA":
            raise ValueError("Overlay foreground must be RGBA")
        if background.size != foreground.size:
            raise ValueError("Overlay layers must have matching sizes")
        if surface_mask.mode != "L" or surface_mask.size != background.size:
            raise ValueError("Overlay surface mask must be L mode and match the layers")
        object.__setattr__(self, "_size", background.size)
        object.__setattr__(self, "_background_bytes", background.tobytes())
        object.__setattr__(self, "_foreground_bytes", foreground.tobytes())
        object.__setattr__(self, "_surface_mask_bytes", surface_mask.tobytes())

    @classmethod
    def _from_cached_background(
        cls,
        size: tuple[int, int],
        background_bytes: bytes,
        foreground: Image.Image,
        surface_mask_bytes: bytes,
    ) -> OverlayLayers:
        """Build frame metadata while reusing the renderer's static payload."""

        if foreground.mode != "RGBA" or foreground.size != size:
            raise ValueError("Overlay foreground must be RGBA and match the background size")
        if len(background_bytes) != size[0] * size[1] * 3:
            raise ValueError("Cached overlay background payload has an invalid length")
        if len(surface_mask_bytes) != size[0] * size[1]:
            raise ValueError("Cached overlay surface mask payload has an invalid length")
        layers = object.__new__(cls)
        object.__setattr__(layers, "_size", size)
        object.__setattr__(layers, "_background_bytes", background_bytes)
        object.__setattr__(layers, "_foreground_bytes", foreground.tobytes())
        object.__setattr__(layers, "_surface_mask_bytes", surface_mask_bytes)
        return layers

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def background(self) -> Image.Image:
        """Return the physical RGB canvas/card source used with the mask."""

        return Image.frombytes("RGB", self._size, self._background_bytes)

    @property
    def foreground(self) -> Image.Image:
        """Return an independent RGBA copy of the fully opaque content layer."""

        return Image.frombytes("RGBA", self._size, self._foreground_bytes)

    @property
    def surface_mask(self) -> Image.Image:
        """Return the card/connection alpha mask; the outer grid stays clear."""

        return Image.frombytes("L", self._size, self._surface_mask_bytes)


def get_overlay_layers(image: Image.Image) -> OverlayLayers:
    """Return immutable semantic layer metadata attached by ``render``.

    Access to each image goes through copy-returning properties on
    :class:`OverlayLayers`; callers may therefore resize or alter their copies
    without affecting the physical RGB framebuffer or future reads.
    """

    layers = image.info.get(OVERLAY_LAYERS_INFO_KEY)
    if not isinstance(layers, OverlayLayers):
        raise ValueError("Image does not contain AI Mini Monitor overlay layers")
    if layers.size != image.size:
        raise ValueError("Overlay layer metadata does not match image size")
    return layers


def _metric_gauge(rect: Rect) -> Rect:
    portrait = rect.width > 228
    return Rect(rect.x + 11, rect.bottom - (22 if portrait else 27), rect.width - 22, 8 if portrait else 10)


def _memory_gauge(rect: Rect) -> Rect:
    return _metric_gauge(rect)


# Backwards-compatible landscape aliases used by benchmarks and existing tests.
CPU_GAUGE = _metric_gauge(CPU)
GPU_GAUGE = _metric_gauge(GPU)
MEMORY_GAUGE = _memory_gauge(MEMORY)


def _metric_percent(metric: Metric) -> str:
    return "--" if metric.value is None else f"{round(metric.value):d}%"


_PERCENT_VALUE = re.compile(r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))%")


def _metric_temperature(metric: Metric) -> str:
    return "-- °C" if metric.value is None else f"{round(metric.value):d} °C"


def _short_number(value: float | int) -> str:
    number = float(value)
    absolute = abs(number)
    if absolute >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:.0f}"


class DashboardRenderer:
    def __init__(
        self,
        theme: Theme = DEFAULT_THEME,
        layout: DashboardLayout = LANDSCAPE_LAYOUT,
    ) -> None:
        self.theme = theme
        self.layout = layout
        self.cpu_gauge = _metric_gauge(layout.cpu)
        self.gpu_gauge = _metric_gauge(layout.gpu)
        self.memory_gauge = _memory_gauge(layout.memory)
        (
            self._static_background,
            self._static_foreground,
            self._overlay_surface_mask,
        ) = self._render_static_layers()
        self._overlay_background_bytes = self._static_background.tobytes()
        self._overlay_surface_mask_bytes = self._overlay_surface_mask.tobytes()
        self._static = Image.alpha_composite(
            self._static_background.convert("RGBA"),
            self._static_foreground,
        ).convert("RGB")
        self.last_placements: dict[str, TextPlacement] = {}
        self.last_gauges: dict[str, GaugePlacement] = {}
        self.last_sparklines: dict[str, Rect] = {}
        self.last_budget_bar: Rect | None = None

    def _render_static_layers(self) -> tuple[Image.Image, Image.Image, Image.Image]:
        background = Image.new("RGB", self.layout.size, self.theme.background)
        draw = ImageDraw.Draw(background)
        surface_mask = Image.new("L", self.layout.size, 0)
        surface_draw = ImageDraw.Draw(surface_mask)
        for name, rect in self.layout.card_rects.items():
            fill = self.theme.raised_card if name == "ai" else self.theme.card
            draw.rounded_rectangle(
                (rect.x, rect.y, rect.right - 1, rect.bottom - 1),
                radius=8,
                fill=fill,
                outline=self.theme.card_outline,
                width=1,
            )
            surface_draw.rounded_rectangle(
                (rect.x, rect.y, rect.right - 1, rect.bottom - 1),
                radius=8,
                fill=255,
            )
        foreground = Image.new("RGBA", self.layout.size, (0, 0, 0, 0))
        foreground_draw = ImageDraw.Draw(foreground)
        for track in (self.cpu_gauge, self.gpu_gauge, self.memory_gauge):
            foreground_draw.rounded_rectangle(
                (track.x, track.y, track.right - 1, track.bottom - 1),
                radius=3,
                fill=self.theme.grid,
                outline=self.theme.border,
                width=1,
            )
        return background, foreground, surface_mask

    def _render_static(self) -> Image.Image:
        """Return the legacy fully composited static RGB dashboard."""

        return self._static.copy()

    def render(self, snapshot: DisplaySnapshot) -> Image.Image:
        foreground = self._static_foreground.copy()
        draw = ImageDraw.Draw(foreground)
        self.last_placements = {}
        self.last_gauges = {}
        self.last_sparklines = {}
        self.last_budget_bar = None
        self._draw_connection(draw, snapshot)
        self._draw_metric_card(
            draw,
            self.layout.cpu,
            "CPU",
            snapshot.cpu_percent,
            snapshot.cpu_temperature,
            snapshot.cpu_history,
            "cpu",
            snapshot.cpu_model,
        )
        self._draw_metric_card(
            draw,
            self.layout.gpu,
            "GPU",
            snapshot.gpu_percent,
            snapshot.gpu_temperature,
            snapshot.gpu_history,
            "gpu",
            snapshot.gpu_model,
            snapshot=snapshot,
        )
        self._draw_memory(draw, snapshot)
        self._draw_ai(draw, snapshot.ai)
        image = Image.alpha_composite(
            self._static_background.convert("RGBA"),
            foreground,
        ).convert("RGB")
        image.info[OVERLAY_LAYERS_INFO_KEY] = OverlayLayers._from_cached_background(
            self.layout.size,
            self._overlay_background_bytes,
            foreground,
            self._overlay_surface_mask_bytes,
        )
        return image

    @property
    def clipping_issues(self) -> tuple[TextPlacement, ...]:
        return tuple(item for item in self.last_placements.values() if item.clipped)

    def _text(
        self,
        draw: ImageDraw.ImageDraw,
        key: str,
        xy: tuple[int, int],
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: str,
        clip: Rect,
        *,
        anchor: str = "la",
    ) -> TextPlacement:
        bbox = draw.textbbox(xy, text, font=font, anchor=anchor)
        placement = TextPlacement(
            key=key,
            anchor_xy=xy,
            bbox=bbox,
            clip=clip,
            text=text,
            anchor=anchor,
            font_size=font.size,
            font_name=font.getname(),
        )
        self.last_placements[key] = placement
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)
        return placement

    def _draw_large_percent(
        self,
        draw: ImageDraw.ImageDraw,
        key: str,
        xy: tuple[int, int],
        text: str,
        digit_size: int,
        fill: str,
        clip: Rect,
        *,
        anchor: str,
    ) -> None:
        """Draw a numeric percentage with a compact, bottom-aligned unit suffix.

        The original key is retained as a virtual group placement so callers
        can keep treating the value as one right- or left-anchored label. The
        actual glyph runs are exposed as ``*_digits`` and ``*_suffix``.
        """

        match = _PERCENT_VALUE.fullmatch(text)
        if match is None:
            self._text(draw, key, xy, text, metric_mono(digit_size), fill, clip, anchor=anchor)
            return

        digits = match.group(1)
        digit_font = metric_mono(digit_size)
        suffix_font = metric_mono(max(1, round(digit_size * 0.5)))
        gap = max(1, round(digit_size * 0.04))
        horizontal_anchor = anchor[0]
        baseline_y = xy[1]

        # Keep the compact unit on the digit baseline. Using Pillow's actual
        # glyph bounds for the final adjustment also keeps their visible
        # bottoms aligned if the bundled font metrics change later.
        digit_probe = draw.textbbox(
            (xy[0], baseline_y),
            digits,
            font=digit_font,
            anchor="rs" if horizontal_anchor == "r" else "ls",
        )
        suffix_probe = draw.textbbox(
            (xy[0], baseline_y),
            "%",
            font=suffix_font,
            anchor="rs" if horizontal_anchor == "r" else "ls",
        )
        suffix_y = baseline_y + digit_probe[3] - suffix_probe[3]

        if horizontal_anchor == "r":
            suffix_probe = draw.textbbox(
                (xy[0], suffix_y),
                "%",
                font=suffix_font,
                anchor="rs",
            )
            suffix_x = xy[0] - max(0, suffix_probe[2] - xy[0])
            suffix = self._text(
                draw,
                f"{key}_suffix",
                (suffix_x, suffix_y),
                "%",
                suffix_font,
                fill,
                clip,
                anchor="rs",
            )
            number = self._text(
                draw,
                f"{key}_digits",
                (suffix.bbox[0] - gap, baseline_y),
                digits,
                digit_font,
                fill,
                clip,
                anchor="rs",
            )
        elif horizontal_anchor == "l":
            number = self._text(
                draw,
                f"{key}_digits",
                (xy[0], baseline_y),
                digits,
                digit_font,
                fill,
                clip,
                anchor="ls",
            )
            suffix = self._text(
                draw,
                f"{key}_suffix",
                (number.bbox[2] + gap, suffix_y),
                "%",
                suffix_font,
                fill,
                clip,
                anchor="ls",
            )
        else:
            raise ValueError(f"Unsupported percentage anchor: {anchor}")

        group_bbox = (
            min(number.bbox[0], suffix.bbox[0]),
            min(number.bbox[1], suffix.bbox[1]),
            max(number.bbox[2], suffix.bbox[2]),
            max(number.bbox[3], suffix.bbox[3]),
        )
        self.last_placements[key] = TextPlacement(
            key=key,
            anchor_xy=xy,
            bbox=group_bbox,
            clip=clip,
            text=text,
            anchor=anchor,
            font_size=digit_font.size,
            font_name=digit_font.getname(),
            drawn=False,
        )

    def _draw_connection(self, draw: ImageDraw.ImageDraw, snapshot: DisplaySnapshot) -> None:
        rect = self.layout.connection
        portrait = self.layout.height > self.layout.width
        connection = snapshot.connection
        color = {
            ConnectionStatus.ONLINE: self.theme.success,
            ConnectionStatus.RECONNECTING: self.theme.warning,
            ConnectionStatus.DISCONNECTED: self.theme.error,
        }[connection.status]
        center_y = rect.y + rect.height // 2
        draw.ellipse((rect.x + 9, center_y - 3, rect.x + 15, center_y + 3), fill=color)
        self._text(draw, "connection_label", (rect.x + 22, center_y), "MINI MONITOR", inter(11, "Bold"), self.theme.primary_text, rect, anchor="lm")
        self._text(draw, "connection_status", (rect.x + (129 if portrait else 148), center_y), connection.status.value, mono(10), color, rect, anchor="lm")
        detail = "DEMO" if snapshot.ai.demo else (connection.port or connection.detail or "NO DEVICE")
        detail_limit = 9 if portrait else 20
        self._text(
            draw,
            "connection_detail",
            (rect.right - 10, center_y),
            detail.upper()[:detail_limit],
            mono(10),
            self.theme.secondary_text,
            rect,
            anchor="rm",
        )

    def _draw_metric_card(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        label: str,
        percent: Metric,
        temperature: Metric,
        history: tuple[float | None, ...],
        key_prefix: str,
        model: str | None,
        snapshot: DisplaySnapshot | None = None,
    ) -> None:
        value_color = self.theme.primary_text
        if temperature.value is not None and temperature.value >= 90:
            value_color = self.theme.error
        accent = self.theme.cpu_accent if key_prefix == "cpu" else self.theme.gpu_accent
        self._draw_card_heading(draw, f"{key_prefix}_label", rect, label, accent)
        gauge = self.cpu_gauge if key_prefix == "cpu" else self.gpu_gauge
        self._draw_percent_gauge(
            draw,
            key_prefix,
            rect,
            gauge,
            percent,
            accent,
            (rect.right - 11, gauge.y - 3),
            "rs",
        )
        portrait = self.layout.height > self.layout.width
        temperature_font = mono(12)
        temperature_text = _metric_temperature(temperature)
        info_x = rect.x + (130 if portrait else 123)
        if model:
            model_font, model_text = self._fit_model_name(
                model,
                rect.width - 145 if portrait else rect.width - (62 if key_prefix == "gpu" and snapshot is not None and snapshot.gpu_power_w.value is not None else 30),
            )
            self._text(
                draw,
                f"{key_prefix}_model",
                (info_x if portrait else rect.x + 13, rect.y + (29 if portrait else 32)),
                model_text,
                model_font,
                self.theme.secondary_text,
                rect,
                anchor="la",
            )
        self._draw_large_percent(
            draw,
            f"{key_prefix}_value",
            (rect.x + 112, rect.y + (73 if portrait else 80)),
            _metric_percent(percent),
            40 if portrait else 42,
            value_color,
            rect,
            anchor="rs",
        )
        temp_color = self.theme.error if temperature.value is not None and temperature.value >= 90 else self.theme.secondary_text
        self._text(
            draw,
            f"{key_prefix}_temperature",
            (info_x, rect.y + (52 if portrait else 65)),
            temperature_text,
            temperature_font,
            temp_color,
            rect,
            anchor="la",
        )
        if key_prefix == "gpu" and snapshot is not None:
            used = snapshot.gpu_vram_used_gib.value
            total = snapshot.gpu_vram_total_gib.value
            valid = (
                used is not None and total is not None
                and isfinite(float(used)) and isfinite(float(total))
                and 0 <= used <= total and total > 0
            )
            vram = f"VRAM {used:.1f} / {total:.1f} GiB" if valid else "VRAM -- / -- GiB"
            self._text(draw, "gpu_vram", (info_x if portrait else rect.x + 12, rect.y + (64 if portrait else 84)), vram, mono(10), self.theme.secondary_text, rect)
            power = snapshot.gpu_power_w.value
            if power is not None and isfinite(float(power)) and power >= 0:
                self._text(draw, "gpu_power", (rect.right - 11, rect.y + (51 if portrait else 31)), f"{power:.0f} W", mono(10), self.theme.secondary_text, rect, anchor="ra")
        chart_width = rect.width - 22
        chart = Rect(
            rect.x + 11,
            rect.bottom - (10 if portrait else 13),
            chart_width,
            7 if portrait else 9,
        )
        self._draw_sparkline(draw, chart, history, accent, key=key_prefix)

    def _draw_memory(self, draw: ImageDraw.ImageDraw, snapshot: DisplaySnapshot) -> None:
        rect = self.layout.memory
        portrait = self.layout.height > self.layout.width
        accent = self.theme.memory_accent
        self._draw_card_heading(draw, "memory_label", rect, "RAM", accent)
        self._draw_large_percent(
            draw,
            "memory_value",
            (rect.x + 112, rect.y + (73 if portrait else 80)),
            _metric_percent(snapshot.memory_percent),
            40 if portrait else 42,
            self.theme.primary_text,
            rect,
            anchor="rs",
        )
        if snapshot.memory_used_gib.value is None or snapshot.memory_total_gib.value is None:
            used_total = "-- / -- GiB"
        else:
            used_total = f"{snapshot.memory_used_gib.value:.1f} / {snapshot.memory_total_gib.value:.1f} GiB"
        self._text(draw, "memory_used", (rect.x + (130 if portrait else 121), rect.y + (40 if portrait else 48)), used_total, mono(11 if portrait else 10), self.theme.secondary_text, rect)
        available = "-- GiB FREE" if snapshot.memory_available_gib.value is None else f"{snapshot.memory_available_gib.value:.1f} GiB FREE"
        self._text(draw, "memory_available", (rect.x + (130 if portrait else 121), rect.y + (62 if portrait else 69)), available, mono(11), self.theme.secondary_text, rect)
        chart = Rect(rect.x + 11, rect.bottom - (10 if portrait else 13), rect.width - 22, 7 if portrait else 9)
        self._draw_sparkline(draw, chart, snapshot.memory_history, accent, key="memory")
        self._draw_percent_gauge(
            draw,
            "memory",
            rect,
            self.memory_gauge,
            snapshot.memory_percent,
            accent,
            (self.memory_gauge.right, self.memory_gauge.y - 3),
            "rs",
        )

    def _draw_percent_gauge(
        self,
        draw: ImageDraw.ImageDraw,
        key: str,
        card: Rect,
        track: Rect,
        metric: Metric,
        accent: str,
        status_xy: tuple[int, int],
        status_anchor: str,
    ) -> None:
        value: float | None
        if metric.value is None or not isfinite(float(metric.value)):
            value = None
            ratio = None
            state = "NO DATA"
            state_color = self.theme.dim
        else:
            value = min(100.0, max(0.0, float(metric.value)))
            ratio = value / 100.0
            if value >= 95.0:
                state = "WARNING"
                state_color = self.theme.error
            elif value >= 80.0:
                state = "CAUTION"
                state_color = self.theme.warning
            else:
                state = "NORMAL"
                state_color = self.theme.success

        inner = Rect(track.x + 1, track.y + 1, track.width - 2, track.height - 2)
        fill: Rect | None = None
        if ratio is None:
            for x in range(inner.x, inner.right, 4):
                draw.line(
                    (x, inner.y, min(inner.right - 1, x + 2), inner.bottom - 1),
                    fill=self.theme.dim,
                    width=1,
                )
        elif ratio > 0.0:
            fill_width = min(inner.width, max(1, round(inner.width * ratio)))
            fill = Rect(inner.x, inner.y, fill_width, inner.height)
            fill_color = accent if state == "NORMAL" else state_color
            draw.rounded_rectangle(
                (fill.x, fill.y, fill.right - 1, fill.bottom - 1),
                radius=min(2, fill.width // 2),
                fill=fill_color,
            )

        self.last_gauges[key] = GaugePlacement(key, track, fill, ratio, state)
        if state != "NORMAL":
            self._text(draw, f"{key}_gauge_status", status_xy, state, mono(8), state_color, card, anchor=status_anchor)

    def _draw_ai(self, draw: ImageDraw.ImageDraw, ai: AIData) -> None:
        rect = self.layout.ai
        portrait = self.layout.height > self.layout.width
        title_y = rect.y + 10
        status_color = {
            SyncStatus.OK: self.theme.success,
            SyncStatus.DELAYED: self.theme.warning,
            SyncStatus.AUTH_ERROR: self.theme.error,
            SyncStatus.RATE_LIMITED: self.theme.warning,
            SyncStatus.NETWORK_ERROR: self.theme.error,
            SyncStatus.SETUP_REQUIRED: self.theme.warning,
        }[ai.status]
        badge = ("DEMO" if ai.status is SyncStatus.OK else f"DEMO {ai.status.value}") if ai.demo else ai.status.value
        status_font = mono(9)
        status_xy = (rect.right - 11, title_y)
        status_bbox = draw.textbbox(status_xy, badge, font=status_font, anchor="ra")
        self._draw_card_heading(
            draw,
            "ai_title",
            rect,
            ai.title,
            self.theme.ai_accent,
            max_right=status_bbox[0] - 8,
            underline=not portrait,
        )
        self._text(draw, "ai_status", status_xy, badge, status_font, status_color, rect, anchor="ra")
        primary_is_percent = _PERCENT_VALUE.fullmatch(ai.primary_value) is not None
        codex_quota_value = (
            ai.provider in (AIProviderKind.CODEX_LOCAL, AIProviderKind.CODEX_ACCOUNT)
            and (primary_is_percent or ai.primary_value == "--")
        )
        if portrait:
            primary_size = (
                40
                if codex_quota_value
                else (34 if primary_is_percent else (28 if len(ai.primary_value) <= 7 else 24))
            )
            primary_xy = (
                (rect.x + 112, rect.y + 73)
                if codex_quota_value
                else (rect.x + 11, rect.y + 52)
            )
            primary_label_xy = (
                (rect.right - 11, rect.y + 65)
                if codex_quota_value
                else (rect.right - 11, rect.y + 49)
            )
            primary_label_font = inter(10)
        else:
            primary_size = (
                42
                if codex_quota_value
                else (40 if primary_is_percent else (34 if len(ai.primary_value) <= 7 else 28))
            )
            primary_xy = (
                (rect.x + 112, rect.y + 80)
                if codex_quota_value
                else (rect.x + 11, rect.y + 72)
            )
            primary_label_xy = (
                (rect.right - 11, rect.y + 79)
                if codex_quota_value
                else (rect.right - 11, rect.y + 68)
            )
            primary_label_font = inter(11)
        primary_anchor = "rs" if codex_quota_value else "ls"
        primary_label_bbox = draw.textbbox(
            primary_label_xy,
            ai.primary_label,
            font=primary_label_font,
            anchor="rs",
        )
        primary_available_width = (
            primary_xy[0] - (rect.x + 11)
            if codex_quota_value
            else primary_label_bbox[0] - 8 - primary_xy[0]
        )
        primary_size = self._fit_large_value_size(
            draw,
            ai.primary_value,
            primary_size,
            max(24, primary_size - 10) if primary_is_percent else 18,
            max(1, primary_available_width),
        )
        self._draw_large_percent(
            draw,
            "ai_primary",
            primary_xy,
            ai.primary_value,
            primary_size,
            self.theme.primary_text,
            rect,
            anchor=primary_anchor,
        )
        self._text(
            draw,
            "ai_primary_label",
            primary_label_xy,
            ai.primary_label,
            primary_label_font,
            self.theme.secondary_text,
            rect,
            anchor="rs",
        )
        fields = tuple((label, value) for label, value in ai.fields if "RESET" not in label.upper()) if ai.provider is AIProviderKind.CODEX_ACCOUNT else ai.fields
        fields = fields[:1] if portrait or ai.budget_ratio is not None else fields[:2]
        for index, (label, value) in enumerate(fields):
            top = rect.y + (68 if portrait else 84) + index * 16
            left = rect.x + (130 if portrait else 121)
            self._text(draw, f"ai_field_{index}_label", (left, top), label.upper()[:10], inter(10), self.theme.secondary_text, rect)
            self._text(draw, f"ai_field_{index}_value", (rect.right - 11, top), value[:8], mono(10), self.theme.ai_accent, rect, anchor="ra")
        if ai.budget_ratio is not None:
            ratio = min(1.0, max(0.0, ai.budget_ratio))
            bar = _metric_gauge(rect)
            self.last_budget_bar = bar
            draw.rounded_rectangle((bar.x, bar.y, bar.right - 1, bar.bottom - 1), radius=3, fill=self.theme.border)
            if ratio > 0:
                color = self.theme.error if ratio >= 1.0 else self.theme.ai_accent
                draw.rounded_rectangle((bar.x, bar.y, bar.x + max(3, round(bar.width * ratio)) - 1, bar.bottom - 1), radius=3, fill=color)
        if ai.last_sync is None:
            sync_text = "LAST SYNC --"
        else:
            local_time = ai.last_sync.astimezone().strftime("%H:%M:%S")
            sync_text = f"LAST SYNC {local_time}"
        sync_y = rect.bottom - 5
        self._text(draw, "ai_last_sync", (rect.x + 11, sync_y), sync_text, mono(8), self.theme.secondary_text, rect, anchor="ls")

    def _draw_card_heading(
        self,
        draw: ImageDraw.ImageDraw,
        key: str,
        rect: Rect,
        text: str,
        accent: str,
        *,
        max_right: int | None = None,
        underline: bool = True,
    ) -> TextPlacement:
        """Draw a compact, high-contrast identity lane for a metric card.

        The text remains the primary identifier; the rail and underline make
        the lane easy to locate on a 3.5-inch panel without relying on hue
        alone.  Geometry stays clear of the existing status and data lanes.
        """

        font = inter(18, "ExtraBold")
        text_left = rect.x + 12
        if max_right is not None:
            text, font = self._fit_heading(text, max(0, max_right - text_left))
        placement = self._text(
            draw,
            key,
            (text_left, rect.y + 7),
            text,
            font,
            self.theme.primary_text,
            rect,
            anchor="la",
        )
        return placement

    @staticmethod
    def _fit_heading(text: str, max_width: int) -> tuple[str, ImageFont.FreeTypeFont]:
        """Keep an AI heading whole when possible, then ellipsize at 11px."""

        for size in range(18, 12, -1):
            font = inter(size, "ExtraBold")
            if font.getlength(text) <= max_width:
                return text, font
        font = inter(12, "ExtraBold")
        ellipsis = "..."
        if font.getlength(ellipsis) > max_width:
            return "", font
        fitted = ""
        for character in text:
            candidate = f"{fitted}{character}"
            if font.getlength(f"{candidate.rstrip()}{ellipsis}") > max_width:
                break
            fitted = candidate
        return f"{fitted.rstrip()}{ellipsis}", font

    @staticmethod
    def _fit_model_name(text: str, max_width: int) -> tuple[ImageFont.FreeTypeFont, str]:
        """Fit a stable hardware identity without letting it move metric lanes."""

        original = re.sub(r"\s+", " ", text).strip()
        compact = re.sub(r"\((?:R|TM)\)", "", original, flags=re.IGNORECASE)
        compact = re.sub(r"\b(?:Processor|CPU)\b", "", compact, flags=re.IGNORECASE)
        compact = re.sub(r"\s+", " ", compact).strip()
        if not compact:
            compact = original
        for size in (10, 9):
            font = inter(size, "Bold")
            if font.getlength(compact) <= max_width:
                return font, compact
        font = inter(9, "Bold")
        ellipsis = "..."
        if font.getlength(ellipsis) > max_width:
            return font, ""
        fitted = ""
        for character in compact:
            candidate = f"{fitted}{character}"
            if font.getlength(f"{candidate.rstrip()}{ellipsis}") > max_width:
                break
            fitted = candidate
        return font, f"{fitted.rstrip()}{ellipsis}"

    @staticmethod
    def _fit_large_value_size(
        draw: ImageDraw.ImageDraw,
        text: str,
        maximum: int,
        minimum: int,
        max_width: int,
    ) -> int:
        """Fit the large AI value beside its label without moving its anchor."""

        match = _PERCENT_VALUE.fullmatch(text)
        for size in range(maximum, minimum - 1, -1):
            if match is None:
                bbox = draw.textbbox((0, 0), text, font=metric_mono(size), anchor="ls")
                width = bbox[2] - bbox[0]
            else:
                digits_font = metric_mono(size)
                suffix_font = metric_mono(max(1, round(size * 0.5)))
                digits_bbox = draw.textbbox((0, 0), match.group(1), font=digits_font, anchor="ls")
                suffix_bbox = draw.textbbox((0, 0), "%", font=suffix_font, anchor="ls")
                width = (
                    digits_bbox[2]
                    - digits_bbox[0]
                    + suffix_bbox[2]
                    - suffix_bbox[0]
                    + max(1, round(size * 0.04))
                )
            if width <= max_width:
                return size
        return minimum

    def _draw_sparkline(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        values: Iterable[float | None],
        color: str,
        *,
        key: str | None = None,
    ) -> None:
        if key is not None:
            self.last_sparklines[key] = rect
        draw.line((rect.x, rect.bottom - 1, rect.right - 1, rect.bottom - 1), fill=self.theme.border, width=1)
        draw.line((rect.x, rect.y + rect.height // 2, rect.right - 1, rect.y + rect.height // 2), fill=self.theme.grid, width=1)
        samples = list(values)
        if len(samples) < 2:
            return
        count = len(samples)
        segment: list[tuple[int, int]] = []
        for index, raw in enumerate(samples):
            if raw is None or not isfinite(float(raw)):
                if len(segment) >= 2:
                    draw.line(segment, fill=color, width=2, joint="curve")
                segment = []
                continue
            value = min(100.0, max(0.0, float(raw)))
            x = rect.x + round(index * (rect.width - 1) / (count - 1))
            y = rect.bottom - 1 - round(value * (rect.height - 2) / 100.0)
            segment.append((x, y))
        if len(segment) >= 2:
            draw.line(segment, fill=color, width=2, joint="curve")
