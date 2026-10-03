from __future__ import annotations

from dataclasses import dataclass


SCREEN_WIDTH = 480
SCREEN_HEIGHT = 320
PORTRAIT_WIDTH = 320
PORTRAIT_HEIGHT = 480
OUTER_MARGIN = 8
CARD_GAP = 8


@dataclass(frozen=True, slots=True)
class Rect:
    """Half-open integer rectangle: [x, right) × [y, bottom)."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.right, self.bottom

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )

    def clamp(self, width: int = SCREEN_WIDTH, height: int = SCREEN_HEIGHT) -> "Rect":
        left = min(width, max(0, self.x))
        top = min(height, max(0, self.y))
        right = min(width, max(left, self.right))
        bottom = min(height, max(top, self.bottom))
        return Rect(left, top, right - left, bottom - top)


@dataclass(frozen=True, slots=True)
class DashboardLayout:
    width: int
    height: int
    connection: Rect
    cpu: Rect
    gpu: Rect
    memory: Rect
    ai: Rect

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def card_rects(self) -> dict[str, Rect]:
        return {
            "connection": self.connection,
            "cpu": self.cpu,
            "gpu": self.gpu,
            "memory": self.memory,
            "ai": self.ai,
        }


LANDSCAPE_LAYOUT = DashboardLayout(
    width=SCREEN_WIDTH,
    height=SCREEN_HEIGHT,
    connection=Rect(8, 8, 464, 20),
    cpu=Rect(8, 36, 228, 134),
    gpu=Rect(244, 36, 228, 134),
    memory=Rect(8, 178, 228, 134),
    ai=Rect(244, 178, 228, 134),
)

# A genuine portrait dashboard: every card is rendered in a 320x480 logical
# coordinate space. Nothing here is a rotated landscape raster, so labels and
# values stay horizontal when the monitor is mounted vertically.
PORTRAIT_LAYOUT = DashboardLayout(
    width=PORTRAIT_WIDTH,
    height=PORTRAIT_HEIGHT,
    connection=Rect(8, 8, 304, 20),
    cpu=Rect(8, 36, 304, 103),
    gpu=Rect(8, 147, 304, 103),
    memory=Rect(8, 258, 304, 103),
    ai=Rect(8, 369, 304, 103),
)


def layout_for_dimensions(width: int, height: int) -> DashboardLayout:
    if (width, height) == LANDSCAPE_LAYOUT.size:
        return LANDSCAPE_LAYOUT
    if (width, height) == PORTRAIT_LAYOUT.size:
        return PORTRAIT_LAYOUT
    raise ValueError(f"unsupported dashboard dimensions: {width}x{height}")


# Backwards-compatible landscape aliases used by benchmark and existing tests.
CONNECTION = LANDSCAPE_LAYOUT.connection
CPU = LANDSCAPE_LAYOUT.cpu
GPU = LANDSCAPE_LAYOUT.gpu
MEMORY = LANDSCAPE_LAYOUT.memory
AI = LANDSCAPE_LAYOUT.ai
CARD_RECTS = LANDSCAPE_LAYOUT.card_rects


def validate_layout(layout: DashboardLayout = LANDSCAPE_LAYOUT) -> list[str]:
    errors: list[str] = []
    rects = list(layout.card_rects.items())
    for name, rect in rects:
        if rect.x < 0 or rect.y < 0 or rect.right > layout.width or rect.bottom > layout.height:
            errors.append(f"{name} is out of bounds: {rect}")
    for index, (name, rect) in enumerate(rects):
        for other_name, other in rects[index + 1 :]:
            if rect.intersects(other):
                errors.append(f"{name} overlaps {other_name}")
    if layout.connection.width != layout.width - 16 or layout.connection.height != 20:
        errors.append("connection dimensions changed")
    if layout is LANDSCAPE_LAYOUT:
        expected = {
            "cpu": (228, 134),
            "gpu": (228, 134),
            "memory": (228, 134),
            "ai": (228, 134),
        }
        for name, dimensions in expected.items():
            rect = layout.card_rects[name]
            if (rect.width, rect.height) != dimensions:
                errors.append(f"{name} dimensions changed")
    return errors


def normalized_area_ratios(
    layout: DashboardLayout = LANDSCAPE_LAYOUT,
) -> dict[str, float]:
    base = float(layout.cpu.area)
    return {name: rect.area / base for name, rect in layout.card_rects.items()}
