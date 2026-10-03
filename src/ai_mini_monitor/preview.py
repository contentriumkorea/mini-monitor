from __future__ import annotations

import json
from pathlib import Path

from .demo import demo_snapshot
from .rendering.layout import LANDSCAPE_LAYOUT, layout_for_dimensions
from .rendering.renderer import DashboardRenderer


PREVIEW_STATES = (
    "normal",
    "zero",
    "hundred",
    "temperature_warning",
    "memory_99",
    "ai_not_configured",
    "ai_delayed",
    "reconnecting",
    "disconnected",
)


def render_preview(
    state: str,
    output: Path,
    *,
    dimensions: tuple[int, int] = LANDSCAPE_LAYOUT.size,
) -> Path:
    if state not in PREVIEW_STATES:
        raise ValueError(f"unknown preview state: {state}")
    renderer = DashboardRenderer(layout=layout_for_dimensions(*dimensions))
    image = renderer.render(demo_snapshot(state))
    if renderer.clipping_issues:
        names = ", ".join(issue.key for issue in renderer.clipping_issues)
        raise RuntimeError(f"text clipping detected: {names}")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return output


def render_all_previews(output_dir: Path) -> list[Path]:
    # These nine files are canonical landscape packaging/audit assets. The
    # interactive --preview path follows the saved display orientation instead.
    output = [
        render_preview(
            state,
            output_dir / f"{state}.png",
            dimensions=LANDSCAPE_LAYOUT.size,
        )
        for state in PREVIEW_STATES
    ]
    manifest = {
        "native_size": list(LANDSCAPE_LAYOUT.size),
        "demo_data": True,
        "files": [path.name for path in output],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
