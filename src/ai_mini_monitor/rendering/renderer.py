from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
import re

from PIL import Image, ImageDraw, ImageFont

from ..formatting import format_credit_balance

from ..models import (
    AIData,
    AIProviderKind,
    ConnectionStatus,
    DisplaySnapshot,
    Metric,
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
    return Rect(rect.right - 22, rect.bottom - 50, 10, 40)


def _memory_gauge(rect: Rect) -> Rect:
    return _metric_gauge(rect)


# Backwards-compatible landscape aliases used by benchmarks and existing tests.
CPU_GAUGE = _metric_gauge(CPU)
GPU_GAUGE = _metric_gauge(GPU)
MEMORY_GAUGE = _memory_gauge(MEMORY)


def _metric_percent(metric: Metric) -> str:
    return "--" if metric.value is None else f"{round(metric.value):d}%"


_PERCENT_VALUE = re.compile(r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))%")
_CREDIT_VALUE = re.compile(r"(\d{1,3}(?:,\d{3})*|--|UNLTD) Credit")


def _metric_temperature(metric: Metric) -> str:
    return "-- °C" if metric.value is None else f"{round(metric.value):d} °C"


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
        self.vram_gauge = _metric_gauge(layout.vram)
        self.ai_gauge = _metric_gauge(layout.ai)
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
        for track in (self.cpu_gauge, self.memory_gauge, self.gpu_gauge, self.vram_gauge, self.ai_gauge):
            foreground_draw.rounded_rectangle(
                (track.x, track.y, track.right - 1, track.bottom - 1),
                radius=4,
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
            "cpu",
            snapshot.cpu_model,
        )
        self._draw_metric_card(
            draw,
            self.layout.gpu,
            "GPU",
            snapshot.gpu_percent,
            snapshot.gpu_temperature,
            "gpu",
            snapshot.gpu_model,
            snapshot=snapshot,
        )
        self._draw_memory(draw, snapshot)
        self._draw_vram(draw, snapshot)
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
        """Draw a percent or credit value with a compact, bottom-aligned unit.

        The original key is retained as a virtual group placement so callers
        can keep treating the value as one right- or left-anchored label. The
        actual glyph runs are exposed as ``*_digits`` and ``*_suffix``.
        """

        match = _PERCENT_VALUE.fullmatch(text) or _CREDIT_VALUE.fullmatch(text)
        unit = "Credit" if text.endswith(" Credit") else "%"
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
            unit,
            font=suffix_font,
            anchor="rs" if horizontal_anchor == "r" else "ls",
        )
        suffix_y = baseline_y + digit_probe[3] - suffix_probe[3]

        if horizontal_anchor == "r":
            suffix_probe = draw.textbbox(
                (xy[0], suffix_y),
                unit,
                font=suffix_font,
                anchor="rs",
            )
            suffix_x = xy[0] - max(0, suffix_probe[2] - xy[0])
            suffix = self._text(
                draw,
                f"{key}_suffix",
                (suffix_x, suffix_y),
                unit,
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
                unit,
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

    def _draw_card_value(
        self, draw: ImageDraw.ImageDraw, key: str, rect: Rect, track: Rect,
        value: str, color: str,
    ) -> TextPlacement:
        credit = _CREDIT_VALUE.fullmatch(value) is not None
        max_width = track.x - rect.x - 110 if credit else min(112, track.x - rect.x - 30)
        size = self._fit_large_value_size(draw, value, 50, 6 if credit else 24, max_width)
        self._draw_large_percent(
            draw, key, (track.x - 6, rect.bottom - 10), value,
            size, color, rect, anchor="rs",
        )
        return self.last_placements[key]

    @staticmethod
    def _wrap_words(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if font.getlength(word) <= max_width:
                current = word
                continue
            chunk = ""
            for char in word:
                if chunk and font.getlength(chunk + char) > max_width:
                    lines.append(chunk)
                    chunk = ""
                chunk += char
            current = chunk
        if current:
            lines.append(current)
        return lines

    def _draw_wrapped_detail(
        self, draw: ImageDraw.ImageDraw, key: str, rect: Rect, text: str,
        x: int, y: int, max_width: int, font: ImageFont.FreeTypeFont,
        *, line_step: int = 11, max_bottom: int | None = None,
    ) -> int:
        """Draw complete words in the detail lane and expose one group placement."""

        limit = rect.bottom - 3 if max_bottom is None else max_bottom
        placements: list[TextPlacement] = []
        lines = self._wrap_words(text, font, max_width)
        for index, line in enumerate(lines):
            line_y = y + index * line_step
            bbox = draw.textbbox((x, line_y), line, font=font, anchor="la")
            if bbox[3] > limit:
                break
            placements.append(self._text(
                draw, f"{key}_line_{index}", (x, line_y), line,
                font, self.theme.secondary_text, rect,
            ))
        if placements:
            bbox = (
                min(item.bbox[0] for item in placements),
                min(item.bbox[1] for item in placements),
                max(item.bbox[2] for item in placements),
                max(item.bbox[3] for item in placements),
            )
        else:
            bbox = (x, y, x, y)
        if len(placements) < len(lines):
            bbox = (bbox[0], bbox[1], bbox[2], rect.bottom + 1)
        self.last_placements[key] = TextPlacement(
            key, (x, y), bbox, rect, text, "la", font.size,
            font.getname(), drawn=False,
        )
        return y + len(placements) * line_step

    def _draw_metric_card(
        self,
        draw: ImageDraw.ImageDraw,
        rect: Rect,
        label: str,
        percent: Metric,
        temperature: Metric,
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
            gauge,
            percent,
            accent,
        )
        value_placement = self._draw_card_value(
            draw, f"{key_prefix}_value", rect, gauge,
            _metric_percent(percent), value_color,
        )
        info_x = rect.x + 12
        info_width = max(1, value_placement.bbox[0] - info_x - 6)
        next_y = rect.y + 30
        if model:
            next_y = self._draw_wrapped_detail(
                draw, f"{key_prefix}_model", rect, model,
                info_x, next_y, info_width, inter(10, "Bold"),
            )
        temp_color = self.theme.error if temperature.value is not None and temperature.value >= 90 else self.theme.secondary_text
        temp_text = _metric_temperature(temperature)
        temp_font = mono(10)
        if (next_y + 10 <= rect.bottom - 2 and temp_font.getlength(temp_text) <= info_width):
            self._text(
                draw, f"{key_prefix}_temperature", (info_x, next_y),
                temp_text, temp_font, temp_color, rect,
            )
            next_y += 11
        if key_prefix == "gpu" and snapshot is not None:
            power = snapshot.gpu_power_w.value
            if power is not None and isfinite(float(power)) and power >= 0:
                power_text = f"{power:.0f} W"
                if next_y + 9 <= rect.bottom - 2 and mono(9).getlength(power_text) <= info_width:
                    self._text(draw, "gpu_power", (info_x, next_y), power_text, mono(9), self.theme.secondary_text, rect)

    def _draw_memory(self, draw: ImageDraw.ImageDraw, snapshot: DisplaySnapshot) -> None:
        rect = self.layout.memory
        accent = self.theme.memory_accent
        self._draw_card_heading(draw, "memory_label", rect, "RAM", accent)
        value = self._draw_card_value(
            draw, "memory_value", rect, self.memory_gauge,
            _metric_percent(snapshot.memory_percent), self.theme.primary_text,
        )
        if snapshot.memory_used_gib.value is None or snapshot.memory_total_gib.value is None:
            used_total = "-- / -- GiB"
        else:
            used_total = f"{snapshot.memory_used_gib.value:.1f} / {snapshot.memory_total_gib.value:.1f} GiB"
        info_x = rect.x + 12
        info_width = max(1, value.bbox[0] - info_x - 6)
        next_y = self._draw_wrapped_detail(
            draw, "memory_used", rect, used_total,
            info_x, rect.y + 31, info_width, mono(9),
        )
        available = "-- GiB FREE" if snapshot.memory_available_gib.value is None else f"{snapshot.memory_available_gib.value:.1f} GiB FREE"
        self._draw_wrapped_detail(
            draw, "memory_available", rect, available,
            info_x, next_y + 2, info_width, mono(9),
        )
        self._draw_percent_gauge(
            draw,
            "memory",
            self.memory_gauge,
            snapshot.memory_percent,
            accent,
        )

    def _draw_vram(self, draw: ImageDraw.ImageDraw, snapshot: DisplaySnapshot) -> None:
        rect = self.layout.vram
        used = snapshot.gpu_vram_used_gib.value
        total = snapshot.gpu_vram_total_gib.value
        valid = (used is not None and total is not None and isfinite(float(used))
                 and isfinite(float(total)) and 0 <= used <= total and total > 0)
        percent = Metric(used / total * 100 if valid else None, "%")
        self._draw_card_heading(draw, "vram_label", rect, "VRAM", self.theme.gpu_accent)
        value = self._draw_card_value(
            draw, "vram_value", rect, self.vram_gauge,
            _metric_percent(percent), self.theme.primary_text,
        )
        capacity = f"{used:.1f} / {total:.1f} GiB" if valid else "-- / -- GiB"
        info_x = rect.x + 12
        self._draw_wrapped_detail(
            draw, "vram_used", rect, capacity,
            info_x, rect.y + 36, max(1, value.bbox[0] - info_x - 6), mono(9),
        )
        self._draw_percent_gauge(draw, "vram", self.vram_gauge, percent, self.theme.gpu_accent)

    def _draw_percent_gauge(
        self,
        draw: ImageDraw.ImageDraw,
        key: str,
        track: Rect,
        metric: Metric,
        accent: str,
        *,
        remaining: bool = False,
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
            severity_value = 100.0 - value if remaining else value
            if severity_value >= 95.0:
                state = "WARNING"
                state_color = self.theme.error
            elif severity_value >= 80.0:
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
            fill_height = min(inner.height, max(1, round(inner.height * ratio)))
            fill = Rect(inner.x, inner.bottom - fill_height, inner.width, fill_height)
            fill_color = accent if state == "NORMAL" else state_color
            draw.rounded_rectangle(
                (fill.x, fill.y, fill.right - 1, fill.bottom - 1),
                radius=min(2, fill.height // 2),
                fill=fill_color,
            )

        self.last_gauges[key] = GaugePlacement(key, track, fill, ratio, state)

    def _draw_ai(self, draw: ImageDraw.ImageDraw, ai: AIData) -> None:
        rect = self.layout.ai
        fields = tuple((label, value) for label, value in ai.fields if "RESET" not in label.upper()) if ai.provider is AIProviderKind.CODEX_ACCOUNT else ai.fields
        fields = fields[:2]
        self._draw_card_heading(
            draw,
            "ai_title",
            rect,
            ai.title,
            self.theme.ai_accent,
            max_right=rect.right - 11,
            underline=False,
        )
        value = self._draw_card_value(
            draw, "ai_primary", rect, self.ai_gauge,
            ai.primary_value, self.theme.primary_text,
        )
        info_x = rect.x + 12
        info_right = value.bbox[0] - 6
        primary_label_font = inter(10)
        self._text(
            draw,
            "ai_primary_label",
            (info_x, rect.y + 30),
            ai.primary_label,
            primary_label_font,
            self.theme.secondary_text,
            rect,
        )
        for index, (label, value) in enumerate(fields):
            top = rect.y + 46 + index * 17
            label_placement = self._text(draw, f"ai_field_{index}_label", (info_x, top), label.upper()[:10], inter(10), self.theme.secondary_text, rect)
            display_value = format_credit_balance(value) if label.upper() == "CREDITS" else value[:8]
            value_right = min(info_right, info_x + 130)
            available_width = value_right - label_placement.bbox[2] - 6
            value_font = mono(10)
            for size in range(10, 5, -1):
                value_font = mono(size)
                if value_font.getlength(display_value) <= available_width:
                    break
            self._text(draw, f"ai_field_{index}_value", (value_right, top), display_value, value_font, self.theme.ai_accent, rect, anchor="ra")
        if ai.provider in (AIProviderKind.CODEX_LOCAL, AIProviderKind.CODEX_ACCOUNT):
            match = _PERCENT_VALUE.fullmatch(ai.primary_value)
            gauge_percent = float(match.group(1)) if match else None
        else:
            gauge_percent = ai.budget_ratio * 100 if ai.budget_ratio is not None else None
        self._draw_percent_gauge(draw, "ai", self.ai_gauge,
                                 Metric(gauge_percent, "%"), self.theme.ai_accent,
                                 remaining=ai.provider in (AIProviderKind.CODEX_LOCAL, AIProviderKind.CODEX_ACCOUNT))

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

        match = _PERCENT_VALUE.fullmatch(text) or _CREDIT_VALUE.fullmatch(text)
        unit = "Credit" if text.endswith(" Credit") else "%"
        for size in range(maximum, minimum - 1, -1):
            if match is None:
                bbox = draw.textbbox((0, 0), text, font=metric_mono(size), anchor="ls")
                width = bbox[2] - bbox[0]
            else:
                digits_font = metric_mono(size)
                suffix_font = metric_mono(max(1, round(size * 0.5)))
                digits_bbox = draw.textbbox((0, 0), match.group(1), font=digits_font, anchor="ls")
                suffix_bbox = draw.textbbox((0, 0), unit, font=suffix_font, anchor="ls")
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
