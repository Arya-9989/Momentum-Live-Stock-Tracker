"""Design tokens shared by the Plotly charts and the custom UI.

Neutral greys carry the interface; market green/red are reserved for price
state (up/down). Each theme is designed for its own surface rather than
inverted, and every text colour clears WCAG AA on the surfaces it is used on:

=========  ===========  ============  ======================================
Token      Light        Dark          Contrast (text tokens)
=========  ===========  ============  ======================================
ink        ``#1d1d1f``  ``#f5f5f7``   16.8:1 on white / 19.3:1 on black
secondary  ``#6e6e73``  ``#a1a1a6``   4.66:1 on #f5f5f7 / 6.61:1 on #1c1c1e
up         ``#1f7a35``  ``#30d158``   4.95:1 on #f5f5f7 / 8.42:1 on #1c1c1e
down       ``#d70015``  ``#ff453a``   4.94:1 on #f5f5f7 / 4.99:1 on #1c1c1e
=========  ===========  ============  ======================================

The categorical series order (blue, orange, violet, magenta, yellow, aqua)
was validated with the dataviz palette checker on both surfaces (adjacent
CVD ΔE ≥ 8.4, normal-vision ΔE ≥ 19.3); green and red are kept out of it so
an indicator line is never mistaken for price direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

ThemeType = Literal["light", "dark"]


@dataclass(frozen=True, slots=True)
class Theme:
    """One colour scheme. ``series`` is ordered; colours follow the entity, never its rank."""

    name: ThemeType
    surface: str
    ink: str
    secondary: str
    tertiary: str
    grid: str
    axis: str
    up: str
    down: str
    flat: str
    hover_bg: str
    hover_border: str
    band: str
    neutral_bar: str
    series: tuple[str, ...]


LIGHT: Final = Theme(
    name="light",
    surface="#ffffff",
    ink="#1d1d1f",
    secondary="#6e6e73",
    tertiary="#8e8e93",
    grid="rgba(60, 60, 67, 0.10)",
    axis="rgba(60, 60, 67, 0.22)",
    up="#1f7a35",
    down="#d70015",
    flat="#6e6e73",
    hover_bg="rgba(255, 255, 255, 0.96)",
    hover_border="rgba(60, 60, 67, 0.18)",
    band="rgba(118, 118, 128, 0.10)",
    neutral_bar="rgba(118, 118, 128, 0.38)",
    series=("#2a78d6", "#eb6834", "#6250d6", "#e87ba4", "#eda100", "#1baf7a"),
)

DARK: Final = Theme(
    name="dark",
    surface="#000000",
    ink="#f5f5f7",
    secondary="#a1a1a6",
    tertiary="#8e8e93",
    grid="rgba(235, 235, 245, 0.10)",
    axis="rgba(235, 235, 245, 0.22)",
    up="#30d158",
    down="#ff453a",
    flat="#a1a1a6",
    hover_bg="rgba(28, 28, 30, 0.96)",
    hover_border="rgba(235, 235, 245, 0.18)",
    band="rgba(118, 118, 128, 0.20)",
    neutral_bar="rgba(152, 152, 157, 0.45)",
    series=("#3987e5", "#d95926", "#9085e9", "#d55181", "#c98500", "#199e70"),
)

# Indicator lines keep a fixed colour whichever subset is switched on.
OVERLAY_SLOTS: Final = {"SMA 20": 0, "SMA 50": 1, "SMA 200": 2, "EMA 12": 3, "EMA 26": 4}


def get_theme(theme_type: str | None) -> Theme:
    """The theme for Streamlit's reported type; anything unknown falls back to light."""
    return DARK if theme_type == "dark" else LIGHT


def direction_color(theme: Theme, change: float | None) -> str:
    """Price-state colour: up, down, or neutral for no change / unknown."""
    if change is None or math.isnan(change) or change == 0:
        return theme.flat
    return theme.up if change > 0 else theme.down


def overlay_color(theme: Theme, column: str) -> str:
    """Stable colour for an SMA/EMA column; any other window takes the last slot."""
    return theme.series[OVERLAY_SLOTS.get(column, len(theme.series) - 1)]


def with_alpha(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` -> ``rgba(r, g, b, alpha)`` for gradients and fills."""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        msg = f"expected #rrggbb, got {hex_color!r}"
        raise ValueError(msg)
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:g})"
