"""Page composition: top bar, watchlist rail and the three views.

Layout (no ``st.sidebar``: Streamlit mounts the sidebar after the first
paint, which shifts the whole page; an in-page rail does not)::

    topbar   [logo  Momentum]                         [search]
    shell    [main: tabs -> overview/compare/watchlist]  [rail: watchlist, data]

The chart comes first; on narrow screens the rail wraps below the main column.
"""

from __future__ import annotations

import time

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from momentum import config, data
from momentum.data import DataError
from momentum.theme import get_theme
from momentum.ui import components, views
from momentum.ui.rail import rail_height

TABS = ("Overview", "Compare", "Watchlist")
RAIL_WIDTH = 320
SEARCH_WIDTH = 200  # wordmark + field fit one row at 390 px
FIRST_PAINT_PAUSE_S = 0.12
ABOUT = (
    "**Momentum** is a Streamlit dashboard for global stocks, indices, FX and crypto.\n\n"
    "Market data comes from Yahoo Finance via the unofficial yfinance library and may be "
    "delayed or incomplete. Not financial advice."
)


def _let_layout_settle() -> None:
    """Give the browser the page config before any element.

    Streamlit paints its initial (centred) layout first and switches to the
    wide layout when the page config arrives. If elements arrive in the same
    frame, the switch moves them all (a layout shift of 0.02-0.06). A short
    pause on a session's first run lets the switch happen on an empty page.
    """
    time.sleep(FIRST_PAINT_PAUSE_S)


def demo_enabled() -> bool:
    """Demo flag before its (URL-bound) toggle has rendered in this run."""
    if "demo" in st.session_state:
        return bool(st.session_state["demo"])
    raw = st.query_params.get("demo")
    return config.is_truthy(raw) if raw is not None else config.demo_mode_enabled()


def _current_symbol(raw: str) -> str | None:
    try:
        return data.normalize_symbol(raw)
    except DataError:
        return None


def _rail(rail: DeltaGenerator, raw_symbol: str, demo: bool, *, first_run: bool) -> None:
    """Watchlist rail: list, demo toggle, refresh and the data-source note."""
    count = len(views.watchlist_symbols())
    with rail:
        views.rail_header()
        slot = st.container(key="rail_slot", height=rail_height(count), border=False).empty()
        if first_run:
            with slot:
                components.rail_skeleton(count)
        _data_controls()
    with slot:
        views.watchlist_rail(_current_symbol(raw_symbol), demo)


def _data_controls() -> None:
    """Demo toggle, refresh and the data-source note (rail footer, or under the
    watchlist table when the rail steps aside)."""
    with st.container(key="data_controls"):
        components.hairline()
        st.toggle(
            "Demo data",
            value=config.demo_mode_enabled(),
            key="demo",
            bind="query-params",
        )
        st.button(
            "Refresh data",
            icon=":material/refresh:",
            type="tertiary",
            key="refresh",
            on_click=data.clear_caches,
        )
        components.fineprint(
            "Demo data is synthetic and works offline. Live data comes from Yahoo Finance "
            "via the unofficial yfinance library and may be delayed or incomplete. "
            "Not financial advice."
        )


def main() -> None:
    st.set_page_config(
        page_title="Momentum · Markets",
        page_icon=":material/monitoring:",
        layout="wide",
        initial_sidebar_state="collapsed",
        menu_items={"About": ABOUT},
    )
    first_run = not st.session_state.get("_mm_rendered", False)
    if first_run:
        _let_layout_settle()
    components.inject_styles()
    theme = get_theme(st.context.theme.type)

    # Fixed height, like every other row above the fold: if the search field's
    # code fails to load, its error card must not push the page down.
    with st.container(
        key="topbar",
        horizontal=True,
        horizontal_alignment="distribute",
        vertical_alignment="center",
        gap="small",
        height=components.TOPBAR_HEIGHT,
        border=False,
    ):
        components.wordmark()
        raw_symbol = st.text_input(
            "Search symbol",
            value=config.DEFAULT_TICKER,
            key="ticker",
            bind="query-params",
            placeholder="Search symbol",
            icon=":material/search:",
            label_visibility="collapsed",
            help="Any Yahoo Finance symbol: AAPL, LT.NS (NSE), VOD.L (London), ^GSPC (index), "
            "BTC-USD (crypto), EURUSD=X (FX).",
            width=SEARCH_WIDTH,
        )

    # DOM order is visual order at every width (main, then rail), so no CSS
    # reordering is needed and nothing jumps when the stylesheet arrives.
    with st.container(key="shell", horizontal=True, gap="large"):
        main_column = st.container(key="main")
        rail = st.container(key="rail", width=RAIL_WIDTH)

    # The demo flag is needed before its toggle renders (the rail is filled last).
    demo = demo_enabled()

    with main_column:
        if demo:
            components.demo_notice()
        overview_tab, compare_tab, watch_tab = st.tabs(
            list(TABS), key="view", on_change="rerun", bind="query-params"
        )
        if overview_tab.open is not False:
            with overview_tab:
                views.overview(raw_symbol or "", demo, theme, first_run=first_run)
        if compare_tab.open:
            with compare_tab:
                views.compare(
                    _current_symbol(raw_symbol or "") or config.DEFAULT_TICKER,
                    demo,
                    theme,
                    first_run=first_run,
                )
        if watch_tab.open:
            with watch_tab:
                views.watchlist(demo, theme, first_run=first_run)

    # The rail renders after the main column: on phones it sits below it, so
    # rendering it first would let the arriving chart push it down the page.
    # The Watchlist tab is the rail's expanded form, so the rail steps aside
    # there (an empty container takes no space) and the table gets the width.
    if watch_tab.open:
        with main_column:
            _data_controls()
    else:
        _rail(rail, raw_symbol or "", demo, first_run=first_run)

    st.space("large")
    components.theme_watcher()
    st.session_state["_mm_rendered"] = True
