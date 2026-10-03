from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_path(relative: str | Path) -> Path:
    base = Path(getattr(sys, "_MEIPASS", project_root()))
    return base / Path(relative)


def user_data_dir() -> Path:
    import os

    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "AI-Mini-Monitor"

