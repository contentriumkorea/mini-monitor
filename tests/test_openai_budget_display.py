from decimal import Decimal
import pytest
from ai_mini_monitor.ai.openai_budget import BudgetSnapshot
from ai_mini_monitor.config import OpenAIBudgetConfig, DEFAULT_TASKBAR_ITEMS, validate_taskbar_options
from ai_mini_monitor.models import DisplaySnapshot, AIData, AIProviderKind
from ai_mini_monitor.rendering.layout import layout_for_dimensions, validate_layout
from ai_mini_monitor.rendering.renderer import DashboardRenderer, get_overlay_layers
from ai_mini_monitor.state import RuntimeStateStore, DisplayComposer
from ai_mini_monitor.ui.taskbar import render_taskbar_frame


def api(value="23.50"):
    return BudgetSnapshot(OpenAIBudgetConfig(True, "100", "2026-10-01"), Decimal(value), "ok").ai


@pytest.mark.parametrize("size", [(480, 320), (320, 480)])
@pytest.mark.parametrize("spent", ["0", "23.50", "123.45"])
def test_api_and_codex_render_together_with_independent_values(size, spent):
    assert "api" in DisplaySnapshot.__dataclass_fields__, "API snapshot must not replace Codex"
    snapshot = DisplaySnapshot(ai=AIData(provider=AIProviderKind.CODEX_ACCOUNT, title="CODEX", primary_value="87%"), api=api(spent))
    renderer = DashboardRenderer(layout=layout_for_dimensions(*size))
    image = renderer.render(snapshot)
    assert image.size == size
    assert renderer.last_placements["ai_primary"].text == "87%"
    assert renderer.last_placements["api_primary"].text == api(spent).primary_value
    assert renderer.last_placements["api_field_0_value"].text == api(spent).fields[0][1]
    assert renderer.last_placements["api_field_1_value"].text == "$100.00"
    assert not renderer.clipping_issues
    assert len(renderer.last_gauges) == 6
    assert get_overlay_layers(image).foreground.size == size
    renderer.render(DisplaySnapshot())
    assert "api_primary" not in renderer.last_placements
    assert len(renderer.last_gauges) == 5


def test_state_composer_preserves_independent_api_without_sensor():
    store = RuntimeStateStore()
    assert hasattr(store, "update_api"), "missing API state path"
    store.update_api(api())
    frame = DisplayComposer().compose(store.read(), monotonic_now=1)
    assert frame.api.primary_value == "23.5%"
    assert frame.ai.provider != AIProviderKind.OPENAI_API


@pytest.mark.parametrize("style", ["icon", "text", "both"])
@pytest.mark.parametrize("dpi", [100, 200, 250])
def test_taskbar_api_is_selectable_without_changing_existing_default(style, dpi):
    assert "api" not in DEFAULT_TASKBAR_ITEMS
    assert validate_taskbar_options(["api"], style) == ("api",)
    image = render_taskbar_frame(None, items=("api",), style=style, dpi_percent=dpi, api=api("123.45"))
    alpha = get_overlay_layers(image).foreground.getchannel("A")
    assert alpha.getbbox()[2] < image.width
    assert alpha.getbbox()[3] < image.height


def test_small_configured_total_large_spend_does_not_overlap_api_values():
    ai = BudgetSnapshot(OpenAIBudgetConfig(True, "0.01", "2026-10-01"), Decimal("1000000000000"), "ok").ai
    renderer = DashboardRenderer(layout=layout_for_dimensions(320, 480))
    renderer.render(DisplaySnapshot(api=ai))
    primary = renderer.last_placements["api_primary"].bbox
    for key in ("api_field_0_value", "api_field_1_value"):
        box = renderer.last_placements[key].bbox
        assert box[2] < primary[0], (key, box, primary)
