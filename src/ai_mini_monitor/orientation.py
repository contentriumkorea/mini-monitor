from __future__ import annotations

"""Canonical display-orientation settings shared by UI and transport.

The host renderer always produces content in the selected logical dimensions.
The reverse variants are handled by the display's audited orientation command;
the host never rotates an already-rendered raster and therefore never turns
text sideways in portrait mode.
"""

from dataclasses import dataclass
from typing import Final

from .transport.protocol_rev_a import Orientation, oriented_dimensions


LANDSCAPE: Final[str] = "landscape"
LANDSCAPE_INVERTED: Final[str] = "landscape_inverted"
PORTRAIT: Final[str] = "portrait"
PORTRAIT_INVERTED: Final[str] = "portrait_inverted"

CANONICAL_ROTATIONS: Final[frozenset[str]] = frozenset(
    {LANDSCAPE, LANDSCAPE_INVERTED, PORTRAIT, PORTRAIT_INVERTED}
)

# These names appeared in upstream configuration examples and early local
# experiments. Loading them is a one-way migration to the clearer UI names;
# validation and saving only expose the four canonical values above.
_LEGACY_ROTATIONS: Final[dict[str, str]] = {
    "reverse_landscape": LANDSCAPE_INVERTED,
    "reverse_portrait": PORTRAIT_INVERTED,
}


@dataclass(frozen=True, slots=True)
class OrientationSpec:
    key: str
    view: str
    inverted: bool
    protocol: Orientation
    width: int
    height: int

    @property
    def dimensions(self) -> tuple[int, int]:
        return self.width, self.height


_PROTOCOL_BY_ROTATION: Final[dict[str, Orientation]] = {
    LANDSCAPE: Orientation.LANDSCAPE,
    LANDSCAPE_INVERTED: Orientation.REVERSE_LANDSCAPE,
    PORTRAIT: Orientation.PORTRAIT,
    PORTRAIT_INVERTED: Orientation.REVERSE_PORTRAIT,
}


def migrate_rotation(value: object) -> object:
    """Return a canonical key for a known legacy string.

    Unknown values are left untouched so normal validation can reject them
    with one bounded error instead of silently changing user intent.
    """

    if isinstance(value, str):
        return _LEGACY_ROTATIONS.get(value, value)
    return value


def orientation_spec(value: object) -> OrientationSpec:
    if not isinstance(value, str) or value not in CANONICAL_ROTATIONS:
        raise ValueError(
            "device.rotation must be one of "
            f"{sorted(CANONICAL_ROTATIONS)}"
        )
    canonical = value
    protocol = _PROTOCOL_BY_ROTATION[canonical]
    width, height = oriented_dimensions(protocol)
    return OrientationSpec(
        key=canonical,
        view=PORTRAIT if canonical.startswith("portrait") else LANDSCAPE,
        inverted=canonical.endswith("_inverted"),
        protocol=protocol,
        width=width,
        height=height,
    )


def compose_rotation(view: object, inverted: object) -> str:
    if view not in (LANDSCAPE, PORTRAIT):
        raise ValueError("display view must be landscape or portrait")
    if not isinstance(inverted, bool):
        raise ValueError("display inversion must be true or false")
    return f"{view}_inverted" if inverted else str(view)


def rotation_for_protocol(value: object) -> str:
    """Return the canonical setting key for an audited protocol enum."""

    if not isinstance(value, Orientation):
        raise ValueError("orientation must be an Orientation value")
    for key, protocol in _PROTOCOL_BY_ROTATION.items():
        if protocol is value:
            return key
    raise ValueError(f"unsupported orientation: {value!r}")
