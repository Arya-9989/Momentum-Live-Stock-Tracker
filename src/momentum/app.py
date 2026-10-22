"""Page composition: sidebar controls, header and the three views."""

from __future__ import annotations

from typing import cast

import streamlit as st

from momentum import config, data
from momentum import indicators as ind
from momentum.charts import ChartStyle
from momentum.data import DataError
from momentum.ui import components, views

OVERLAYS = ("SMA 20", "SMA 50", "SMA 200", "EMA 12", "EMA 26", "Bollinger Bands")
TABS = ("Overview", "Compare", "Watchlist")


def _apply_quick_pick() -> None:
    pick = st.session_state.get("quick_pick")
    if pick:
        st.session_state["ticker"] = pick
        st.session_state["quick_pick"] = None


def _indicator_settings(
    overlays: list[str], show_rsi: bool, show_macd: bool
) -> ind.IndicatorSettings:
    return ind.IndicatorSettings(
        sma_windows=tuple(int(o.split()[1]) for o in overlays if o.startswith("SMA")),
        ema_spans=tuple(int(o.split()[1]) for o in overlays if o.startswith("EMA")),
        bollinger="Bollinger Bands" in overlays,
        rsi=show_rsi,
        macd=show_macd,
    )


def sidebar() -> tuple[str, views.ChartControls, bool]:
    """Render the sidebar and return (raw symbol, chart controls, demo flag)."""
    with st.sidebar:
        st.html(
            '<div style="font-size:1.35rem;font-weight:700;letter-spacing:-0.01em">'
            "Momentum</div>"
            '<div style="font-size:0.8rem;opacity:0.65;margin-top:-0.1rem">'
            "Global stocks &amp; indices dashboard</div>"
        )
        raw_symbol = st.text_input(
            "Symbol",
            value=config.DEFAULT_TICKER,
            key="ticker",
            bind="query-params",
            placeholder="AAPL, LT.NS, ^GSPC",
            icon=":material/search:",
            help="Any Yahoo Finance symbol: AAPL, LT.NS (NSE), VOD.L (London), ^GSPC (index), "
            "BTC-USD (crypto), EURUSD=X (FX).",
        )
        st.pills(
            "Quick picks",
            config.QUICK_PICKS,
            key="quick_pick",
            on_change=_apply_quick_pick,
            label_visibility="collapsed",
        )

        st.subheader("Chart", anchor=False)
        range_label = (
            st.segmented_control(
                "Range",
                [r.label for r in config.RANGES],
                default=config.DEFAULT_RANGE,
                key="range",
                bind="query-params",
                required=True,
            )
            or config.DEFAULT_RANGE
        )
        option = config.get_range(range_label)
        if st.session_state.get("interval") not in option.intervals:
            st.session_state["interval"] = option.default_interval
        interval = st.selectbox(
            "Interval",
            option.intervals,
            key="interval",
            format_func=lambda i: config.INTERVAL_LABELS.get(i, i),
            help="Yahoo limits intraday history: 1-minute bars cover about a week, "
            "sub-hourly bars about 60 days.",
        )
        style = cast(
            ChartStyle,
            st.segmented_control(
                "Style", ["Candles", "Line"], default="Candles", key="style", required=True
            ),
        )

        st.subheader("Indicators", anchor=False)
        overlays = st.multiselect(
            "Overlays", OVERLAYS, default=["SMA 20", "SMA 50"], key="overlays"
        )
        col_rsi, col_macd = st.columns(2)
        show_rsi = col_rsi.toggle("RSI (14)", value=True, key="rsi")
        show_macd = col_macd.toggle("MACD", value=False, key="macd")

        st.subheader("Data", anchor=False)
        demo = st.toggle(
            "Demo data",
            value=config.demo_mode_enabled(),
            key="demo",
            bind="query-params",
            help="Use deterministic synthetic data instead of Yahoo Finance (works offline).",
        )
        if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
            data.clear_caches()
        st.caption(
            "Data from Yahoo Finance via the unofficial yfinance library; it may be delayed "
            "or incomplete. Not financial advice."
        )

    controls = views.ChartControls(
        range_label=range_label,
        interval=config.resolve_interval(range_label, interval),
        style=style,
        indicators=_indicator_settings(overlays, show_rsi, show_macd),
    )
    return raw_symbol or "", controls, demo


def main() -> None:
    st.set_page_config(
        page_title="Momentum · Stock Dashboard",
        page_icon=":material/monitoring:",
        layout="wide",
        initial_sidebar_state="auto",
    )
    components.inject_styles()
    raw_symbol, controls, demo = sidebar()
    if demo:
        components.demo_banner()

    symbol: str | None = None
    history = None
    error: str | None = None
    try:
        symbol = data.normalize_symbol(raw_symbol)
        option = config.get_range(controls.range_label)
        with st.spinner(f"Loading {symbol}..."):
            history = data.cached_history(
                symbol,
                config.fetch_period(option.period, controls.interval),
                controls.interval,
                demo,
            )
    except DataError as exc:
        error = str(exc)

    quote = None
    if symbol and history is not None:
        quote, quote_error = views.load_quote(symbol, history, demo)
        components.quote_header(quote, demo=demo)
        if quote_error:
            st.caption(f"Some quote details are unavailable: {quote_error}")

    overview_tab, compare_tab, watch_tab = st.tabs(
        list(TABS), key="view", on_change="rerun", bind="query-params"
    )
    if overview_tab.open is not False:
        with overview_tab:
            if error or symbol is None or history is None or quote is None:
                st.error(error or "Enter a ticker symbol in the sidebar.", icon=":material/error:")
                st.caption(
                    "Try one of the quick picks in the sidebar, or switch on demo data "
                    "if Yahoo Finance is unreachable."
                )
            else:
                views.overview(symbol, quote, history, controls, demo)
    if compare_tab.open:
        with compare_tab:
            views.compare(symbol or config.DEFAULT_TICKER, demo)
    if watch_tab.open:
        with watch_tab:
            views.watchlist(demo)
