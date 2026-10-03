from __future__ import annotations

from collections.abc import Iterable

from PIL import Image, ImageChops

from .layout import DashboardLayout, LANDSCAPE_LAYOUT, Rect


def union_rect(first: Rect, second: Rect) -> Rect:
    left = min(first.x, second.x)
    top = min(first.y, second.y)
    right = max(first.right, second.right)
    bottom = max(first.bottom, second.bottom)
    return Rect(left, top, right - left, bottom - top)


def rectangle_distance(first: Rect, second: Rect) -> tuple[int, int]:
    horizontal = max(0, max(first.x, second.x) - min(first.right, second.right))
    vertical = max(0, max(first.y, second.y) - min(first.bottom, second.bottom))
    return horizontal, vertical


def should_merge(first: Rect, second: Rect, proximity: int = 3, max_overdraw: float = 1.35) -> bool:
    dx, dy = rectangle_distance(first, second)
    if dx > proximity or dy > proximity:
        return False
    merged = union_rect(first, second)
    overlap = max(0, min(first.right, second.right) - max(first.x, second.x)) * max(
        0, min(first.bottom, second.bottom) - max(first.y, second.y)
    )
    useful_area = first.area + second.area - overlap
    return useful_area > 0 and merged.area <= useful_area * max_overdraw


def merge_nearby(
    rectangles: Iterable[Rect],
    proximity: int = 3,
    max_overdraw: float = 1.35,
    *,
    width: int = LANDSCAPE_LAYOUT.width,
    height: int = LANDSCAPE_LAYOUT.height,
) -> list[Rect]:
    pending = [
        rect.clamp(width, height)
        for rect in rectangles
        if rect.width > 0 and rect.height > 0
    ]
    changed = True
    while changed:
        changed = False
        output: list[Rect] = []
        while pending:
            current = pending.pop(0)
            for index, candidate in enumerate(pending):
                if should_merge(current, candidate, proximity, max_overdraw):
                    pending[index] = union_rect(current, candidate)
                    changed = True
                    break
            else:
                output.append(current)
        pending = output
    return sorted(pending, key=lambda rect: (rect.y, rect.x))


def _diff_components(
    previous: Image.Image,
    current: Image.Image,
    region: Rect,
    padding: int,
) -> list[Rect]:
    before = previous.crop(region.as_box()).convert("RGB")
    after = current.crop(region.as_box()).convert("RGB")
    channels = ImageChops.difference(before, after).split()
    mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    changed = mask.tobytes()
    width, height = mask.size
    visited = bytearray(width * height)
    components: list[Rect] = []

    for start, value in enumerate(changed):
        if value == 0 or visited[start]:
            continue
        visited[start] = 1
        stack = [start]
        start_x = start % width
        start_y = start // width
        left = right = start_x
        top = bottom = start_y

        while stack:
            index = stack.pop()
            x = index % width
            y = index // width
            left = min(left, x)
            right = max(right, x)
            top = min(top, y)
            bottom = max(bottom, y)
            for next_y in range(max(0, y - 1), min(height, y + 2)):
                row = next_y * width
                for next_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = row + next_x
                    if changed[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        stack.append(neighbor)

        # Padding is clipped to the originating card, not merely the screen.
        # This preserves SerialWriter's invariant that every partial region is
        # wholly contained by exactly one dashboard card.
        global_left = max(region.x, region.x + left - padding)
        global_top = max(region.y, region.y + top - padding)
        global_right = min(region.right, region.x + right + 1 + padding)
        global_bottom = min(region.bottom, region.y + bottom + 1 + padding)
        components.append(
            Rect(
                global_left,
                global_top,
                global_right - global_left,
                global_bottom - global_top,
            )
        )
    return components


def calculate_dirty_rectangles(
    previous: Image.Image | None,
    current: Image.Image,
    *,
    padding: int = 1,
    proximity: int = 3,
    layout: DashboardLayout = LANDSCAPE_LAYOUT,
) -> list[Rect]:
    if current.size != layout.size:
        raise ValueError(
            f"frame must be {layout.width}x{layout.height}, got {current.size}"
        )
    if previous is None:
        return [Rect(0, 0, layout.width, layout.height)]
    if previous.size != current.size:
        return [Rect(0, 0, layout.width, layout.height)]
    dirty = [
        box
        for region in layout.card_rects.values()
        for box in _diff_components(previous, current, region, padding)
    ]
    return merge_nearby(
        dirty,
        proximity=proximity,
        width=layout.width,
        height=layout.height,
    )
