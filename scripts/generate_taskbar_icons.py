"""Generate hardware icons; Codex uses its separate approved alpha asset."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


OUT = Path(__file__).resolve().parents[1] / "assets" / "icons"
WHITE = (255, 255, 255, 255)
RENDER_SIDE = 2048
PNG_SIDE = 512


class ScaledDraw:
    """Render the existing editable icon geometry before exporting antialiased PNGs."""
    def __init__(self, image: Image.Image, units: int):
        self.draw = ImageDraw.Draw(image)
        self.scale = RENDER_SIDE / units

    def _call(self, name, coordinates, **kwargs):
        for key in ("width", "radius"):
            if key in kwargs:
                kwargs[key] = max(1, round(kwargs[key] * self.scale))
        return getattr(self.draw, name)(tuple(round(v * self.scale) for v in coordinates), **kwargs)

    def rounded_rectangle(self, coordinates, **kwargs):
        return self._call("rounded_rectangle", coordinates, **kwargs)

    def rectangle(self, coordinates, **kwargs):
        return self._call("rectangle", coordinates, **kwargs)

    def ellipse(self, coordinates, **kwargs):
        return self._call("ellipse", coordinates, **kwargs)

    def line(self, coordinates, **kwargs):
        return self._call("line", coordinates, **kwargs)


def canvas(units: int = 64) -> tuple[Image.Image, ScaledDraw]:
    image = Image.new("RGBA", (RENDER_SIDE, RENDER_SIDE), (0, 0, 0, 0))
    return image, ScaledDraw(image, units)


def cpu() -> Image.Image:
    image, draw = canvas(24)
    draw.rounded_rectangle((6, 6, 18, 18), radius=1, outline=WHITE, width=1.6)
    draw.rectangle((9, 9, 15, 15), outline=WHITE, width=1.6)
    for at in (8, 12, 16):
        draw.line((at, 3, at, 6), fill=WHITE, width=1.6)
        draw.line((at, 18, at, 21), fill=WHITE, width=1.6)
        draw.line((3, at, 6, at), fill=WHITE, width=1.6)
        draw.line((18, at, 21, at), fill=WHITE, width=1.6)
    return image


def gpu() -> Image.Image:
    image, draw = canvas()
    draw.rounded_rectangle((6, 15, 57, 47), radius=3, outline=WHITE, width=5)
    for center_x in (24, 43):
        draw.ellipse((center_x - 7, 25, center_x + 7, 39), outline=WHITE, width=4)
        draw.ellipse((center_x - 2, 30, center_x + 2, 34), fill=WHITE)
    draw.line((12, 47, 12, 54), fill=WHITE, width=5)
    draw.line((52, 47, 52, 54), fill=WHITE, width=5)
    draw.line((57, 24, 62, 24), fill=WHITE, width=4)
    draw.line((57, 38, 62, 38), fill=WHITE, width=4)
    return image


def ram() -> Image.Image:
    image, draw = canvas(24)
    draw.rounded_rectangle((2, 6, 22, 17), radius=1, outline=WHITE, width=1.6)
    for left in (6, 10, 14, 18):
        draw.line((left, 9, left, 13), fill=WHITE, width=1.6)
    for left in (4, 8, 12, 16, 20):
        draw.line((left, 17, left, 20), fill=WHITE, width=1.6)
    return image


def vram() -> Image.Image:
    image, draw = canvas()
    draw.rounded_rectangle((8, 15, 56, 49), radius=4, outline=WHITE, width=5)
    draw.line((21, 23, 32, 41, 43, 23), fill=WHITE, width=5, joint="curve")
    for left in (15, 27, 39, 51):
        draw.line((left, 8, left, 15), fill=WHITE, width=4)
        draw.line((left, 49, left, 56), fill=WHITE, width=4)
    return image


def api() -> Image.Image:
    image, draw = canvas(24)
    for points in ((7, 6, 2, 12, 7, 18), (17, 6, 22, 12, 17, 18), (14, 4, 10, 20)):
        draw.line(points, fill=WHITE, width=1.6, joint="curve")
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, maker in (("cpu", cpu), ("ram", ram), ("gpu", gpu), ("vram", vram), ("api", api)):
        maker().resize((PNG_SIDE, PNG_SIDE), Image.Resampling.LANCZOS).save(OUT / f"{name}.png", optimize=True)


if __name__ == "__main__":
    main()
