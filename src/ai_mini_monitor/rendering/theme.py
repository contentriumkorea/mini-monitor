from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Theme:
    background: str = "#111317"
    card: str = "#1B1E23"
    raised_card: str = "#1B1E23"
    border: str = "#30343B"
    primary_text: str = "#F4F7FB"
    secondary_text: str = "#B1B8C2"
    violet: str = "#8B7CFF"
    cyan: str = "#42E8E0"
    success: str = "#3DDC97"
    warning: str = "#FFB454"
    error: str = "#FF6B7A"
    grid: str = "#30343B"
    dim: str = "#87909B"
    card_outline: str = "#343941"
    cpu_accent: str = "#42E8E0"
    gpu_accent: str = "#8B7CFF"
    memory_accent: str = "#3DDC97"
    ai_accent: str = "#5EA7FF"


DEFAULT_THEME = Theme()
