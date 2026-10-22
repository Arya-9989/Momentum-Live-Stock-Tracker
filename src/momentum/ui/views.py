"""The three dashboard views: overview, comparison and watchlist."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from momentum import charts, config, data
from momentum import formatting as fmt
from momentum import indicators as ind
from momentum.charts import ChartStyle
from momentum.data import DataError, Quote
from momentum.ui import components
from momentum.ui.kpis import build_kpis, key_stats

# Drag to zoom, double-click to reset; the modebar is hidden because it
# overlaps the legend on phones and adds little over those gestures.
PLOTLY_CONFIG = {"displaylogo": False, "displayModeBar": False, "scrollZoom": False}


@dataclass(frozen=True, slots=True)
class ChartControls:
    range_label: str
    interval: str
    style: ChartStyle
    indicators: ind.IndicatorSettings


# --------------------------------------------------------------------------- overview


def load_quote(symbol: str, history: pd.DataFrame, demo: bool) -> tuple[Quote, str | None]:
    """Quote for the header; degrades to history-derived values if ``info`` fails."""
    try:
        return data.parse_quote(symbol, data.cached_info(symbol, demo), history), None
    except DataError as exc:
        return data.parse_quote(symbol, {}, history), str(exc)


def overview(
    symbol: str, quote: Quote, full_history: pd.DataFrame, controls: ChartControls, demo: bool
) -> None:
    """``full_history`` includes warm-up bars before the selected range (see
    ``config.fetch_period``); indicators are computed on it and then trimmed."""
    period = config.get_range(controls.range_label).period
    history = data.visible_window(full_history, period)
    frame = data.visible_window(ind.add_indicators(full_history, controls.indicators), period)

    components.kpi_row(build_kpis(quote, history, controls.range_label, controls.interval))

    prefix = "" if quote.is_index else fmt.currency_symbol(quote.currency)
    suffix = ""
    if quote.currency in fmt.MINOR_UNIT_CURRENCIES:
        # Pence/cents, matching how the header and stats show these listings.
        prefix, suffix = "", fmt.MINOR_UNIT_SUFFIXES[quote.currency]
    figure = charts.price_chart(
        frame,
        interval=controls.interval,
        style=controls.style,
        price_prefix=prefix,
        price_suffix=suffix,
        height=_chart_height(controls.indicators),
    )
    with st.container(border=True):
        st.plotly_chart(figure, config=PLOTLY_CONFIG, key="price_chart")
        bars = f"{len(history):,} {config.INTERVAL_LABELS[controls.interval].lower()} bars"
        st.caption(
            f"{bars} · {history.index[0]:%b %d, %Y} to {history.index[-1]:%b %d, %Y} · "
            "times in exchange local time · prices adjusted for splits and dividends"
        )

    left, right = st.columns([3, 2], gap="medium")
    with left, st.container(border=True):
        components.section_title("Key statistics")
        components.range_bar("Day range", quote.price, quote.day_low, quote.day_high, quote)
        components.range_bar(
            "52-week range", quote.price, quote.fifty_two_week_low, quote.fifty_two_week_high, quote
        )
        components.stats_grid(key_stats(quote))
    with right, st.container(border=True):
        components.section_title("Profile")
        try:
            profile = data.parse_profile(data.cached_info(symbol, demo))
        except DataError:
            profile = data.Profile()
        components.profile_card(profile, quote)

    with st.expander("Price data and export", icon=":material/table:"):
        table = frame.round(4).sort_index(ascending=False)
        st.dataframe(
            table,
            height=320,
            column_config={
                "Date": st.column_config.DatetimeColumn(
                    format="YYYY-MM-DD HH:mm"
                    if config.is_intraday(controls.interval)
                    else "YYYY-MM-DD"
                ),
                "Volume": st.column_config.NumberColumn(format="compact"),
            },
        )
        name = f"{symbol}_{controls.range_label}_{controls.interval}.csv".replace("^", "")
        components.csv_download(
            frame, name, "Download CSV (prices + indicators)", key="dl_overview"
        )


def _chart_height(settings: ind.IndicatorSettings) -> int:
    return 520 + (140 if settings.rsi else 0) + (140 if settings.macd else 0)


# --------------------------------------------------------------------------- compare


def compare(symbol: str, demo: bool) -> None:
    components.section_title("Relative performance")
    st.caption("Cumulative return of each symbol, rebased to 0% at the start of the range.")
    default = list(dict.fromkeys([symbol, *config.DEFAULT_COMPARISON]))[: config.MAX_COMPARE]
    left, right = st.columns([3, 2], gap="medium", vertical_alignment="bottom")
    with left:
        raw = st.multiselect(
            "Symbols",
            options=list(dict.fromkeys([*default, *config.QUICK_PICKS])),
            default=default,
            accept_new_options=True,
            max_selections=config.MAX_COMPARE,
            key="compare_symbols",
            placeholder="Add a ticker, e.g. GOOGL",
        )
    with right:
        range_label = (
            st.segmented_control(
                "Range",
                [r.label for r in config.RANGES if r.label not in {"1D", "5D"}],
                default="1Y",
                key="compare_range",
                required=True,
            )
            or "1Y"
        )

    symbols, rejected = data.parse_symbol_list(raw, config.MAX_COMPARE)
    if rejected:
        st.warning(f"Ignored invalid symbols: {', '.join(rejected)}")
    if not symbols:
        st.info("Add at least one symbol to compare.")
        return

    option = config.get_range(range_label)
    interval = "1d" if "1d" in option.intervals else option.default_interval
    with st.spinner("Loading prices..."):
        closes, errors = data.cached_closes(tuple(symbols), option.period, interval, demo)
    for failed, message in errors.items():
        st.warning(f"**{failed}**: {message}")
    if closes.empty:
        return

    normalized = ind.normalized_returns(closes)
    with st.container(border=True):
        st.plotly_chart(
            charts.comparison_chart(normalized), config=PLOTLY_CONFIG, key="compare_chart"
        )

    ppy = 365 if interval == "1d" and all(s.endswith("-USD") for s in closes.columns) else None
    rows = [
        {
            "Symbol": column,
            "Return": ind.period_return(closes[column]),
            "Volatility (ann.)": ind.annualized_volatility(
                closes[column], ppy or {"1d": 252, "1wk": 52, "1mo": 12}.get(interval, 252)
            ),
            "Max drawdown": ind.max_drawdown(closes[column]),
            "Start": closes[column].dropna().index[0],
        }
        for column in closes.columns
    ]
    summary = pd.DataFrame(rows).sort_values("Return", ascending=False)
    st.dataframe(
        summary,
        hide_index=True,
        column_config={
            "Return": st.column_config.NumberColumn(format="%+.2f%%"),
            "Volatility (ann.)": st.column_config.NumberColumn(format="%.1f%%"),
            "Max drawdown": st.column_config.NumberColumn(format="%.1f%%"),
            "Start": st.column_config.DateColumn("First bar", format="YYYY-MM-DD"),
        },
    )
    export = closes.join(normalized.add_suffix(" return %"))
    components.csv_download(export, f"comparison_{range_label}.csv", key="dl_compare")


# --------------------------------------------------------------------------- watchlist


def _open_from_watchlist() -> None:
    state = st.session_state.get("watchlist_table")
    symbols = st.session_state.get("_watchlist_symbols", [])
    rows = state.selection.rows if state is not None else []
    if rows and rows[0] < len(symbols):
        st.session_state["ticker"] = symbols[rows[0]]
        st.session_state["view"] = "Overview"


WATCHLIST_COLUMNS = (
    "Symbol",
    "Name",
    "Price",
    "Change",
    "1M trend",
    "52W position",
    "52W range",
    "Market cap",
    "P/E",
)


def watchlist_frame(results: Sequence[data.QuoteResult], closes: pd.DataFrame) -> pd.DataFrame:
    """Tabular watchlist model (pure, so it can be tested without Streamlit)."""
    rows: list[dict[str, object]] = []
    for result in results:
        quote = result.quote
        trend = closes[result.symbol].dropna().tolist() if result.symbol in closes else []
        if quote is None:
            rows.append(
                {
                    "Symbol": result.symbol,
                    "Name": result.error or "Unavailable",
                    "Price": fmt.PLACEHOLDER,
                    "Change": None,
                    "52W position": None,
                    "52W range": fmt.PLACEHOLDER,
                    "Market cap": fmt.PLACEHOLDER,
                    "P/E": None,
                    "1M trend": trend,
                }
            )
            continue
        c, t = quote.currency, quote.quote_type
        rows.append(
            {
                "Symbol": quote.symbol,
                "Name": quote.name,
                "Price": fmt.format_price(quote.price, c, t),
                "Change": quote.change_percent,
                "52W position": _percent(
                    fmt.range_position(
                        quote.price, quote.fifty_two_week_low, quote.fifty_two_week_high
                    )
                ),
                "52W range": fmt.format_range(
                    quote.fifty_two_week_low, quote.fifty_two_week_high, c, t
                ),
                "Market cap": fmt.format_market_cap(quote.market_cap, c),
                "P/E": quote.trailing_pe if quote.trailing_pe and quote.trailing_pe > 0 else None,
                "1M trend": trend,
            }
        )
    frame = pd.DataFrame(rows, columns=WATCHLIST_COLUMNS)
    for column in ("Change", "52W position", "P/E"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    return frame


def _percent(fraction: float | None) -> float | None:
    return None if fraction is None else fraction * 100.0


def watchlist(demo: bool) -> None:
    components.section_title("Watchlist")
    st.caption("Quotes refresh every minute. Select a row to open it in the overview.")
    raw = st.multiselect(
        "Symbols",
        options=list(dict.fromkeys([*config.DEFAULT_WATCHLIST, *config.QUICK_PICKS])),
        default=list(config.DEFAULT_WATCHLIST),
        accept_new_options=True,
        max_selections=config.MAX_WATCHLIST,
        key="watchlist_symbols",
        placeholder="Add a ticker",
        label_visibility="collapsed",
    )
    symbols, rejected = data.parse_symbol_list(raw, config.MAX_WATCHLIST)
    if rejected:
        st.warning(f"Ignored invalid symbols: {', '.join(rejected)}")
    if not symbols:
        st.info("Your watchlist is empty. Add a ticker above.")
        return

    with st.spinner("Loading quotes..."):
        results = data.cached_quotes(tuple(symbols), demo)
        closes, _ = data.cached_closes(tuple(symbols), "1mo", "1d", demo)
    table = watchlist_frame(results, closes)
    st.session_state["_watchlist_symbols"] = table["Symbol"].tolist()

    def colour(value: object) -> str:
        if isinstance(value, float) and not math.isnan(value):
            return f"color: {config.PALETTE.up if value >= 0 else config.PALETTE.down}"
        return ""

    styled = table.style.map(colour, subset=["Change"])
    st.dataframe(
        styled,
        hide_index=True,
        key="watchlist_table",
        on_select=_open_from_watchlist,
        selection_mode="single-row",
        row_height=44,
        column_config={
            "Symbol": st.column_config.TextColumn(width=110),
            "Name": st.column_config.TextColumn(width="medium"),
            "Change": st.column_config.NumberColumn("Day", format="%+.2f%%"),
            "1M trend": st.column_config.AreaChartColumn("1M trend", width=110),
            "52W position": st.column_config.ProgressColumn(
                "52W position",
                min_value=0.0,
                max_value=100.0,
                format="%.0f%%",
                help="Where the price sits between the 52-week low (0%) and high (100%).",
            ),
            "P/E": st.column_config.NumberColumn("P/E (TTM)", format="%.1f"),
        },
    )
    components.csv_download(table.drop(columns=["1M trend"]), "watchlist.csv", key="dl_watch")
