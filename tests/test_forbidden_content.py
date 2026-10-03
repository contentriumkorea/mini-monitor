from __future__ import annotations

from ai_mini_monitor.demo import demo_snapshot
from ai_mini_monitor.rendering.renderer import DashboardRenderer


def test_screen_contains_no_forbidden_brand_or_metric_labels() -> None:
    renderer = DashboardRenderer()
    renderer.render(demo_snapshot("normal"))
    text = " ".join(placement.text for placement in renderer.last_placements.values()).upper()
    for forbidden in ("HDD", "SSD", "DISK", "NETWORK", "WEATHER", "VOLUME", "MSI"):
        assert forbidden not in text


def test_regular_configuration_has_no_disk_or_network_polling_sections() -> None:
    config = __import__("json").loads(
        (__import__("pathlib").Path(__file__).parents[1] / "config.example.json").read_text(encoding="utf-8")
    )
    assert "disk" not in config
    assert "storage" not in config
    assert "network" not in config
