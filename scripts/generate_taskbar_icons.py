"""Generate hardware icons; Codex uses its separate approved alpha asset."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


OUT = Path(__file__).resolve().parents[1] / "assets" / "icons"
WHITE = (255, 255, 255, 255)


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def cpu() -> Image.Image:
    image, draw = canvas()
    draw.rounded_rectangle((16, 16, 48, 48), radius=3, outline=WHITE, width=5)
    draw.rectangle((25, 25, 39, 39), outline=WHITE, width=4)
    for at in (22, 32, 42):
        draw.line((at, 7, at, 16), fill=WHITE, width=5)
        draw.line((at, 48, at, 57), fill=WHITE, width=5)
        draw.line((7, at, 16, at), fill=WHITE, width=5)
        draw.line((48, at, 57, at), fill=WHITE, width=5)
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
    image, draw = canvas()
    draw.rounded_rectangle((5, 20, 59, 44), radius=3, outline=WHITE, width=5)
    for left in (14, 26, 38):
        draw.rectangle((left, 26, left + 7, 38), outline=WHITE, width=3)
    for left in (13, 23, 33, 43, 53):
        draw.line((left, 44, left, 52), fill=WHITE, width=4)
    return image


def vram() -> Image.Image:
    image, draw = canvas()
    draw.rounded_rectangle((8, 15, 56, 49), radius=4, outline=WHITE, width=5)
    draw.line((21, 23, 32, 41, 43, 23), fill=WHITE, width=5, joint="curve")
    for left in (15, 27, 39, 51):
        draw.line((left, 8, left, 15), fill=WHITE, width=4)
        draw.line((left, 49, left, 56), fill=WHITE, width=4)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, maker in (("cpu", cpu), ("ram", ram), ("gpu", gpu), ("vram", vram)):
        maker().resize((32, 32), Image.Resampling.LANCZOS).save(OUT / f"{name}.png")


if __name__ == "__main__":
    main()
