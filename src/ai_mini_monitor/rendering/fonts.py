from __future__ import annotations

from functools import lru_cache

from PIL import ImageFont

from ..resources import resource_path


@lru_cache(maxsize=64)
def inter(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(resource_path("assets/fonts/Inter-Variable.ttf")), size=size)
    _set_weight(font, weight)
    return font


@lru_cache(maxsize=64)
def mono(size: int, weight: str = "SemiBold") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(resource_path("assets/fonts/JetBrainsMono-Variable.ttf")), size=size)
    _set_weight(font, weight)
    return font


def metric_mono(size: int) -> ImageFont.FreeTypeFont:
    """Extra-bold tabular font for the dashboard's large numeric readouts."""

    return mono(size, "ExtraBold")


def _set_weight(font: ImageFont.FreeTypeFont, weight: str) -> None:
    try:
        names = [name.decode("ascii", errors="ignore") if isinstance(name, bytes) else str(name) for name in font.get_variation_names()]
        target = next((name for name in names if name.casefold() == weight.casefold()), None)
        if target:
            font.set_variation_by_name(target)
    except (AttributeError, OSError):
        # Some FreeType builds expose the variable file without named instances.
        # The file remains usable; layout tests protect against a metric change.
        return


def digits_are_tabular(font: ImageFont.FreeTypeFont) -> bool:
    widths = {round(font.getlength(character), 4) for character in "0123456789"}
    return len(widths) == 1
