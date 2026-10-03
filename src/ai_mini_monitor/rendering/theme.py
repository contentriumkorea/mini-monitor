from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Theme:
    background: str = "#05070D"
    card: str = "#0B111C"
    raised_card: str = "#101928"
    border: str = "#202C3D"
    primary_text: str = "#F4F7FB"
    secondary_text: str = "#8D9AAF"
    violet: str = "#8B7CFF"
    cyan: str = "#42E8E0"
    success: str = "#3DDC97"
    warning: str = "#FFB454"
    error: str = "#FF6B7A"
    grid: str = "#0B1420"
    dim: str = "#566378"
    card_outline: str = "#466486"
    cpu_accent: str = "#42E8E0"
    gpu_accent: str = "#8B7CFF"
    memory_accent: str = "#3DDC97"
    ai_accent: str = "#5EA7FF"


DEFAULT_THEME = Theme()
