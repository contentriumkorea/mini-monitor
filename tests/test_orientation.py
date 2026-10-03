from __future__ import annotations

import pytest

from ai_mini_monitor.orientation import (
    LANDSCAPE,
    LANDSCAPE_INVERTED,
    PORTRAIT,
    PORTRAIT_INVERTED,
    compose_rotation,
    orientation_spec,
    rotation_for_protocol,
)
from ai_mini_monitor.transport.protocol_rev_a import Orientation


@pytest.mark.parametrize(
    ("key", "protocol", "dimensions", "view", "inverted"),
    [
        (LANDSCAPE, Orientation.LANDSCAPE, (480, 320), LANDSCAPE, False),
        (
            LANDSCAPE_INVERTED,
            Orientation.REVERSE_LANDSCAPE,
            (480, 320),
            LANDSCAPE,
            True,
        ),
        (PORTRAIT, Orientation.PORTRAIT, (320, 480), PORTRAIT, False),
        (
            PORTRAIT_INVERTED,
            Orientation.REVERSE_PORTRAIT,
            (320, 480),
            PORTRAIT,
            True,
        ),
    ],
)
def test_orientation_mapping_is_exact_and_round_trips(
    key: str,
    protocol: Orientation,
    dimensions: tuple[int, int],
    view: str,
    inverted: bool,
) -> None:
    spec = orientation_spec(key)
    assert spec.protocol is protocol
    assert spec.dimensions == dimensions
    assert spec.view == view
    assert spec.inverted is inverted
    assert compose_rotation(view, inverted) == key
    assert rotation_for_protocol(protocol) == key


@pytest.mark.parametrize("value", [None, True, 2, "reverse_landscape", "sideways"])
def test_orientation_mapping_rejects_noncanonical_values(value: object) -> None:
    with pytest.raises(ValueError):
        orientation_spec(value)
