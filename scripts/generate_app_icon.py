# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the shared monochrome app icon from scalable simple geometry."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def create_icon() -> Image.Image:
    scale = 4
    image = Image.new("RGBA", (256 * scale, 256 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
        return x0 * scale, y0 * scale, x1 * scale, y1 * scale

    draw.rounded_rectangle(box(19, 31, 237, 189), radius=24 * scale, fill="#161616")
    draw.rounded_rectangle(box(29, 41, 227, 179), radius=15 * scale, fill="#F3F3F3")
    draw.rounded_rectangle(box(38, 50, 218, 170), radius=9 * scale, fill="#242424")

    # Three bar heights stay distinct down to the 16-pixel notification icon.
    draw.rounded_rectangle(box(72, 110, 98, 150), radius=4 * scale, fill="#ACACAC")
    draw.rounded_rectangle(box(114, 84, 140, 150), radius=4 * scale, fill="#FFFFFF")
    draw.rounded_rectangle(box(156, 99, 182, 150), radius=4 * scale, fill="#D3D3D3")
    draw.rounded_rectangle(box(116, 188, 140, 224), radius=4 * scale, fill="#EDEDED")
    draw.rounded_rectangle(box(75, 218, 181, 236), radius=8 * scale, fill="#EDEDED")
    return image.resize((256, 256), Image.Resampling.LANCZOS)


def main() -> None:
    image = create_icon()
    directory = ROOT / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    image.save(directory / "app-icon.png", format="PNG")
    image.save(directory / "app-icon.ico", format="ICO", sizes=[(size, size) for size in ICON_SIZES])


if __name__ == "__main__":
    main()
