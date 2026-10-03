# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
from pathlib import Path

from ai_mini_monitor import preview
from ai_mini_monitor.rendering.layout import LANDSCAPE_LAYOUT


def test_render_all_previews_includes_unknown_and_vram_boundaries(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[tuple[str, tuple[int, int]]] = []

    def fake_render_preview(
        state: str,
        output: Path,
        *,
        dimensions: tuple[int, int] = LANDSCAPE_LAYOUT.size,
    ) -> Path:
        calls.append((state, dimensions))
        return output

    monkeypatch.setattr(preview, "render_preview", fake_render_preview)

    paths = preview.render_all_previews(tmp_path)

    assert [state for state, _dimensions in calls] == list(preview.PREVIEW_STATES)
    assert len(paths) == 12
    assert {"unknown", "ai_error", "vram_max"}.issubset(preview.PREVIEW_STATES)
    assert all(dimensions == (480, 320) for _state, dimensions in calls)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["native_size"] == [480, 320]
    assert manifest["files"] == [f"{state}.png" for state in preview.PREVIEW_STATES]
