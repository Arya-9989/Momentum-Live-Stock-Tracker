"""Design tokens: theme lookup, price-state colours and the contrast claims in theme.py."""

from __future__ import annotations

import math

import pytest

from momentum import theme
from momentum.theme import DARK, LIGHT


def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_get_theme() -> None:
    assert theme.get_theme("dark") is DARK
    assert theme.get_theme("light") is LIGHT
    assert theme.get_theme(None) is LIGHT  # unknown or not yet reported
    assert theme.get_theme("sepia") is LIGHT


@pytest.mark.parametrize(
    ("change", "attr"),
    [(1.5, "up"), (-0.01, "down"), (0.0, "flat"), (None, "flat"), (math.nan, "flat")],
)
@pytest.mark.parametrize("scheme", [LIGHT, DARK])
def test_direction_color(scheme: theme.Theme, change: float | None, attr: str) -> None:
    assert theme.direction_color(scheme, change) == getattr(scheme, attr)


def test_overlay_colour_follows_the_indicator_not_its_position() -> None:
    # SMA 50 keeps its colour whether or not SMA 20 is switched on.
    assert theme.overlay_color(LIGHT, "SMA 50") == LIGHT.series[1]
    assert theme.overlay_color(LIGHT, "EMA 26") == LIGHT.series[4]
    assert theme.overlay_color(DARK, "SMA 20") == DARK.series[0]
    assert theme.overlay_color(LIGHT, "SMA 100") == LIGHT.series[-1]


def test_series_never_reuse_price_state_colours() -> None:
    for scheme in (LIGHT, DARK):
        assert scheme.up not in scheme.series
        assert scheme.down not in scheme.series
        assert len(set(scheme.series)) == len(scheme.series) == 6


def test_with_alpha() -> None:
    assert theme.with_alpha("#1f7a35", 0.24) == "rgba(31, 122, 53, 0.24)"
    assert theme.with_alpha("#000000", 0) == "rgba(0, 0, 0, 0)"
    with pytest.raises(ValueError, match="rrggbb"):
        theme.with_alpha("#fff", 0.5)


# The contrast table in theme.py's docstring, checked rather than asserted in prose.
@pytest.mark.parametrize(
    ("foreground", "background", "minimum"),
    [
        (LIGHT.ink, "#ffffff", 16.0),
        (DARK.ink, "#000000", 19.0),
        (LIGHT.secondary, "#f5f5f7", 4.5),
        (DARK.secondary, "#1c1c1e", 4.5),
        (LIGHT.up, "#f5f5f7", 4.5),
        (DARK.up, "#1c1c1e", 4.5),
        (LIGHT.down, "#f5f5f7", 4.5),
        (DARK.down, "#1c1c1e", 4.5),
        ("#ffffff", "#1f7a35", 4.5),  # rail badge text on the up fill
        ("#ffffff", "#d70015", 4.5),  # rail badge text on the down fill
        ("#ffffff", "#6e6e73", 4.5),  # rail badge text on the flat fill
        ("#ffffff", "#0066cc", 4.5),  # white on the action blue (tags, toggles)
    ],
)
def test_text_tokens_meet_wcag_aa(foreground: str, background: str, minimum: float) -> None:
    assert contrast(foreground, background) >= minimum
