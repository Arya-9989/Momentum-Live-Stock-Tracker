"""The chart component: registration, mounting and the one event it sends back.

The component is built from ``frontend/chart`` (TypeScript on TradingView
Lightweight Charts) into ``ui/chart/momentum-chart.js``. It is declared in
``src/momentum/pyproject.toml`` with ``asset_dir = "ui/chart"``, so Streamlit
serves the bundle as a cacheable file from ``/_stcore/bidi-components/``. Where
the package is not installed (``requirements.txt`` hosts), there is no manifest,
and the same files are registered inline instead.

Python -> JS: the payloads from ``momentum.charts``. JS -> Python: ``alert``,
the price level the viewer chose with "Set an alert at ...".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

import streamlit as st
from streamlit.errors import StreamlitAPIException

logger = logging.getLogger(__name__)

ASSET_DIR: Final = Path(__file__).parent / "chart"
JS: Final = "momentum-chart.js"
CSS: Final = "momentum-chart.css"
PACKAGED_NAME: Final = "momentum.price_chart"
INLINE_NAME: Final = "momentum_price_chart"
# Lightweight Charts' licence asks for its notice and a link to tradingview.com on
# the page. The chart's logo carries the link; this line carries the notice.
ATTRIBUTION: Final = (
    "Charts: [TradingView Lightweight Charts™](https://www.tradingview.com/lightweight-charts/), "
    "© 2026 [TradingView, Inc.](https://www.tradingview.com/)"
)


def _register() -> tuple[Callable[..., Any], bool]:
    """The component's mount function, and whether it is served as files."""
    try:
        return st.components.v2.component(PACKAGED_NAME, js=JS, css=CSS), True
    except StreamlitAPIException:
        logger.info("No component manifest (package not installed); the chart is inlined")
        js = (ASSET_DIR / JS).read_text(encoding="utf-8")
        css = (ASSET_DIR / CSS).read_text(encoding="utf-8")
        return st.components.v2.component(INLINE_NAME, js=js, css=css), False


_registered: dict[str, Any] = {}


def component() -> Callable[..., Any]:
    """Registered on first use, inside a script run: the runtime has discovered the
    package manifests by then (an import outside a run, as in a unit test, would
    fall back to the inline copy)."""
    if "mount" not in _registered:
        _registered["mount"], _registered["files"] = _register()
    mount: Callable[..., Any] = _registered["mount"]
    return mount


def served_as_files() -> bool:
    component()
    return bool(_registered["files"])


def alert_price(state: object) -> float | None:
    """The price in an ``alert`` trigger, whether exposed as a key or an attribute."""
    value = state.get("alert") if isinstance(state, Mapping) else getattr(state, "alert", None)
    price = value.get("price") if isinstance(value, Mapping) else None
    if isinstance(price, int | float) and not isinstance(price, bool) and price > 0:
        return float(price)
    return None


def chart(
    payload: Mapping[str, Any],
    *,
    key: str,
    on_alert: Callable[[], None] | None = None,
) -> None:
    """Mount (or update) the chart in place; the slot's height is the payload's."""
    component()(
        key=key,
        data=dict(payload),
        height=int(payload["height"]),
        on_alert_change=on_alert or (lambda: None),
    )
