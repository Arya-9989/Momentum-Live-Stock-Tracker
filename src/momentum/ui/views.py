"""The dashboard views: overview, comparison, watchlist and the watchlist rail."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from momentum import charts, config, data
from momentum import formatting as fmt
from momentum import indicators as ind
from momentum.charts import CHART_STYLES, ChartStyle
from momentum.data import DataError, Quote
from momentum.identity import Identity
from momentum.theme import Theme
from momentum.ui import components, messages, rail, saved, session
from momentum.ui.kpis import Kpi, build_kpis, key_stats

OVERLAYS = ("SMA 20", "SMA 50", "SMA 200", "EMA 12", "EMA 26", "Bollinger Bands")
DEFAULT_OVERLAYS = ("SMA 20", "SMA 50")
COMPARE_HEIGHT = 460
SHOWN_QUOTE = "_mm_shown_quote"  # the quote the header shows, for its number transition
STYLE_LABELS = {
    "Line": ":material/show_chart: Line",
    "Candles": ":material/candlestick_chart: Candles",
}


@dataclass(frozen=True, slots=True)
class ChartControls:
    range_label: str
    interval: str
    style: ChartStyle
    indicators: ind.IndicatorSettings


def indicator_settings(
    overlays: Sequence[str], show_rsi: bool, show_macd: bool
) -> ind.IndicatorSettings:
    return ind.IndicatorSettings(
        sma_windows=tuple(int(o.split()[1]) for o in overlays if o.startswith("SMA")),
        ema_spans=tuple(int(o.split()[1]) for o in overlays if o.startswith("EMA")),
        bollinger="Bollinger Bands" in overlays,
        rsi=show_rsi,
        macd=show_macd,
    )


# --------------------------------------------------------------------------- overview


def load_quote(symbol: str, history: pd.DataFrame, demo: bool) -> tuple[Quote, str | None]:
    """Quote for the header; degrades to history-derived values if ``info`` fails."""
    try:
        return data.parse_quote(symbol, data.cached_info(symbol, demo), history), None
    except DataError as exc:
        return data.parse_quote(symbol, {}, history), str(exc)


def range_control() -> str:
    """The one filter above the chart (Apple Stocks-style segmented range)."""
    with st.container(
        key="chart_toolbar",
        horizontal=True,
        vertical_alignment="center",
        height=components.TOOLBAR_HEIGHT,
        border=False,
    ):
        return (
            st.segmented_control(
                "Range",
                [r.label for r in config.RANGES],
                default=config.DEFAULT_RANGE,
                key="range",
                bind="query-params",
                required=True,
                label_visibility="collapsed",
            )
            or config.DEFAULT_RANGE
        )


def chart_options(range_label: str) -> ChartControls:
    """View options under the chart: line/candles and an indicators popover."""
    option = config.get_range(range_label)
    if st.session_state.get("interval") not in option.intervals:
        st.session_state["interval"] = option.default_interval
    with st.container(
        key="chart_options",
        horizontal=True,
        vertical_alignment="center",
        gap="small",
        height=components.TOOLBAR_HEIGHT,
        border=False,
    ):
        style: ChartStyle = st.segmented_control(
            "Chart style",
            CHART_STYLES,
            default="Line",
            format_func=STYLE_LABELS.__getitem__,
            key="style",
            required=True,
            label_visibility="collapsed",
            persist_state="page",
        )
        with st.popover("Indicators", icon=":material/tune:", key="indicators_popover"):
            interval = st.selectbox(
                "Bar interval",
                option.intervals,
                key="interval",
                format_func=lambda i: config.INTERVAL_LABELS.get(i, i),
                help="Yahoo limits intraday history: 1-minute bars cover about a week, "
                "sub-hourly bars about 60 days.",
                persist_state="page",
            )
            overlays = st.pills(
                "Overlays",
                OVERLAYS,
                selection_mode="multi",
                default=list(DEFAULT_OVERLAYS),
                key="overlays",
                persist_state="page",
            )
            show_rsi = st.toggle("RSI (14)", value=True, key="rsi", persist_state="page")
            show_macd = st.toggle("MACD (12, 26, 9)", value=False, key="macd", persist_state="page")
    return ChartControls(
        range_label=range_label,
        interval=config.resolve_interval(range_label, interval),
        style=style,
        indicators=indicator_settings(overlays or [], show_rsi, show_macd),
    )


def expected_chart_height() -> int:
    """Chart height from the current oscillator toggles (read before they render,
    because the chart slot sits above the options row)."""
    return chart_height(
        indicator_settings(
            [], bool(st.session_state.get("rsi", True)), bool(st.session_state.get("macd", False))
        )
    )


def chart_height(settings: ind.IndicatorSettings) -> int:
    """Figure height in px: price and volume, plus a 120 px band per oscillator."""
    return 420 + (120 if settings.rsi else 0) + (120 if settings.macd else 0)


def price_units(quote: Quote) -> tuple[str, str]:
    """Axis prefix/suffix: currency symbol, pence/cents suffix, or nothing for
    index levels and FX rates."""
    if fmt.is_unitless(quote.quote_type):
        return "", ""
    if quote.currency in fmt.MINOR_UNIT_CURRENCIES:
        return "", fmt.MINOR_UNIT_SUFFIXES[quote.currency]
    return fmt.currency_symbol(quote.currency), ""


def chart_alt(quote: Quote, history: pd.DataFrame, controls: ChartControls) -> str:
    """Text alternative for the price chart (first and last close in the window)."""
    close = history["Close"].dropna()
    if close.empty:
        return f"{quote.symbol} price chart, no data in range."
    first, last = float(close.iloc[0]), float(close.iloc[-1])
    c, t = quote.currency, quote.quote_type
    change = ind.period_return(close)
    interval = config.INTERVAL_LABELS.get(controls.interval, controls.interval).lower()
    return (
        f"{quote.name} ({quote.symbol}), {controls.range_label} {interval} chart: "
        f"{fmt.format_price(first, c, t)} on {close.index[0]:%b %d, %Y} to "
        f"{fmt.format_price(last, c, t)} on {close.index[-1]:%b %d, %Y} "
        f"({fmt.format_percent(change)})."
    )


def _kpi_labels_without_duplicates(
    stats: list[tuple[str, str]], kpis: list[Kpi]
) -> list[tuple[str, str]]:
    shown = {k.label for k in kpis}
    return [(label, value) for label, value in stats if label not in shown]


def overview(raw_symbol: str, demo: bool, theme: Theme, *, first_run: bool) -> None:
    """Quote header, range control, chart, chart options, KPIs, statistics and profile.

    On a session's first run, skeletons of the final size hold the header and
    chart slots while data loads; later runs keep the previous render (dimmed by
    Streamlit) until the new one replaces it.
    """
    header_slot = components.slot(
        "quote_slot",
        components.HEADER_HEIGHT,
        first_run=first_run,
        skeleton=components.header_skeleton,
    )
    range_label = range_control()
    height = expected_chart_height()
    chart_slot = components.slot(
        "chart_slot",
        height,
        first_run=first_run,
        skeleton=lambda: components.chart_skeleton(height),
    )
    controls = chart_options(range_label)
    footnote = st.empty()

    try:
        symbol = data.normalize_symbol(raw_symbol)
        option = config.get_range(controls.range_label)
        full_history = data.cached_history(
            symbol, config.fetch_period(option.period, controls.interval), controls.interval, demo
        )
    except DataError as exc:
        # One designed state: the headline says what happened and what to do next;
        # the chart panel stays quiet instead of repeating it.
        # Invalid input is quoted as typed in the headline, so no symbol line above it.
        shown = "" if isinstance(exc, data.InvalidSymbolError) else raw_symbol
        with header_slot:
            components.error_header(shown, messages.describe(exc, raw_symbol))
        st.session_state.pop(SHOWN_QUOTE, None)  # the next quote appears without a roll
        with chart_slot:
            components.error_panel(messages.chart_note(exc), height=height, compact=True)
        return

    quote, quote_error = load_quote(symbol, full_history, demo)
    with header_slot:
        # The figures that differ from what the header showed last roll in.
        components.quote_header(quote, st.session_state.get(SHOWN_QUOTE))
    st.session_state[SHOWN_QUOTE] = quote

    period = config.get_range(controls.range_label).period
    history = data.visible_window(full_history, period)
    frame = data.visible_window(ind.add_indicators(full_history, controls.indicators), period)
    prefix, suffix = price_units(quote)
    reference = quote.previous_close if controls.range_label == "1D" else None
    figure = charts.price_chart(
        frame,
        interval=controls.interval,
        style=controls.style,
        price_prefix=prefix,
        price_suffix=suffix,
        price_decimals=fmt.price_decimals(quote.price, quote.quote_type),
        reference=reference,
        reference_label=(
            f"Previous close {fmt.format_price(reference, quote.currency, quote.quote_type)}"
            if reference is not None
            else "Previous close"
        ),
        height=chart_height(controls.indicators),
        theme=theme,
    )
    with chart_slot:
        st.plotly_chart(
            figure,
            theme=None,
            height=chart_height(controls.indicators),
            config=components.plotly_config(),
            key="price_chart",
            alt=chart_alt(quote, history, controls),
        )
    bars = f"{len(history):,} {config.INTERVAL_LABELS[controls.interval].lower()} bars"
    note = (
        f"{bars} · {history.index[0]:%b %d, %Y} to {history.index[-1]:%b %d, %Y} · "
        "exchange local time · adjusted for splits and dividends"
    )
    if quote_error:
        note += f". Some quote details are unavailable: {quote_error}"
    footnote.caption(note)

    _details(symbol, quote=quote, history=history, frame=frame, controls=controls, demo=demo)


def _details(
    symbol: str,
    *,
    quote: Quote,
    history: pd.DataFrame,
    frame: pd.DataFrame,
    controls: ChartControls,
    demo: bool,
) -> None:
    """KPI tiles, statistics, profile and the price table below the chart."""
    kpis = build_kpis(quote, history, controls.range_label, controls.interval)
    components.kpi_row(kpis)

    left, right = st.columns([3, 2], gap="large")
    with left, st.container(key="stats_section"):
        components.section_title("Statistics")
        components.range_bars(quote)
        components.stats_grid(_kpi_labels_without_duplicates(key_stats(quote), kpis))
    with right, st.container(key="about_section"):
        components.section_title("About")
        try:
            profile = data.cached_profile(symbol, demo)
        except DataError:
            profile = data.Profile()
        components.profile_card(profile, quote)

    # Rendered only when opened, so the table's code never loads with the page.
    price_data = st.expander("Price data", key="price_data", on_change="rerun")
    if price_data.open:
        with price_data:
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


# --------------------------------------------------------------------------- compare


def compare(symbol: str, demo: bool, theme: Theme, *, first_run: bool = False) -> None:
    components.page_title(
        "Compare", "Cumulative return of each symbol, rebased to 0% at the start of the range."
    )
    default = list(dict.fromkeys([symbol, *config.DEFAULT_COMPARISON]))[: config.MAX_COMPARE]
    raw = st.multiselect(
        "Symbols to compare",
        options=list(dict.fromkeys([*default, *config.QUICK_PICKS])),
        default=default,
        accept_new_options=True,
        max_selections=config.MAX_COMPARE,
        key="compare_symbols",
        placeholder="Add a ticker, e.g. GOOGL",
        label_visibility="collapsed",
        persist_state="page",
    )
    with st.container(
        key="compare_toolbar",
        horizontal=True,
        vertical_alignment="center",
        height=components.TOOLBAR_HEIGHT,
        border=False,
    ):
        range_label = (
            st.segmented_control(
                "Range",
                [r.label for r in config.RANGES if r.label not in {"1D", "5D"}],
                default="1Y",
                key="compare_range",
                required=True,
                label_visibility="collapsed",
                persist_state="page",
            )
            or "1Y"
        )
    chart_slot = components.slot(
        "compare_slot",
        COMPARE_HEIGHT,
        first_run=first_run,
        skeleton=lambda: components.chart_skeleton(COMPARE_HEIGHT),
    )

    symbols, rejected = data.parse_symbol_list(raw, config.MAX_COMPARE)
    if not symbols:
        with chart_slot:
            components.error_panel(NOTHING_TO_COMPARE, height=COMPARE_HEIGHT)
        return

    option = config.get_range(range_label)
    interval = "1d" if "1d" in option.intervals else option.default_interval
    closes, errors, transient = data.cached_closes(tuple(symbols), option.period, interval, demo)
    if closes.empty:
        # Every symbol failed: say so where the chart would be, not below the fold.
        with chart_slot:
            components.error_panel(messages.batch_failure(errors, transient), height=COMPARE_HEIGHT)
        return

    slots = charts.assign_series_slots(closes.columns, st.session_state.get("_compare_slots"))
    st.session_state["_compare_slots"] = slots
    normalized = ind.normalized_returns(closes)
    summary_alt = ", ".join(
        f"{c} {fmt.format_percent(ind.period_return(closes[c]))}" for c in closes.columns
    )
    with chart_slot:
        st.plotly_chart(
            charts.comparison_chart(normalized, theme=theme, slots=slots, height=COMPARE_HEIGHT),
            theme=None,
            height=COMPARE_HEIGHT,
            config=components.plotly_config(),
            key="compare_chart",
            alt=f"Cumulative return over {range_label}: {summary_alt}.",
        )
    if errors:
        components.fineprint(messages.not_shown(errors, transient))
    if rejected:
        components.fineprint(f"Ignored, not ticker symbols: {', '.join(rejected)}.")

    rows = [
        {
            "Symbol": column,
            "Return": ind.period_return(closes[column]),
            "Volatility (ann.)": ind.annualized_volatility(
                closes[column], bars_per_year(closes[column], interval)
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


NOTHING_TO_COMPARE = messages.Copy(
    "Nothing to compare yet.", "Add a symbol above, for example AAPL, LT.NS or ^GSPC."
)


def bars_per_year(close: pd.Series, interval: str) -> int:
    """Annualisation factor for one series: daily bars of anything that trades at
    weekends (crypto) count 365 a year, other daily bars 252."""
    if interval == "1d":
        index = pd.DatetimeIndex(close.dropna().index)
        return 365 if bool((index.dayofweek >= 5).any()) else 252
    return {"1wk": 52, "1mo": 12, "3mo": 4}.get(interval, 252)


# --------------------------------------------------------------------------- watchlist


def watchlist_symbols() -> list[str]:
    """The viewer's watchlist (the Watchlist tab edits it; the rail reads it)."""
    return saved.watchlist()


def open_symbol(symbol: str) -> None:
    st.session_state["ticker"] = symbol
    st.session_state["view"] = "Overview"


def _open_from_watchlist() -> None:
    state = st.session_state.get("watchlist_table")
    symbols = st.session_state.get("_watchlist_symbols", [])
    rows = state.selection.rows if state is not None else []
    if rows and rows[0] < len(symbols):
        open_symbol(symbols[rows[0]])


def trigger_value(state: object, event: str) -> str | None:
    """Read a v2 component trigger value whether it is exposed as a key or an attribute."""
    value = state.get(event) if isinstance(state, Mapping) else getattr(state, event, None)
    return value if isinstance(value, str) and value else None


def _open_from_rail() -> None:
    symbol = trigger_value(st.session_state.get("rail_list"), "select")
    if symbol:
        open_symbol(symbol)


def _edit_watchlist() -> None:
    st.session_state["view"] = "Watchlist"


def watchlist_rail(selected: str | None, demo: bool) -> None:
    symbols = watchlist_symbols()
    if not symbols:
        components.notice("Your watchlist is empty. Add symbols in the Watchlist tab.")
        return
    results = data.cached_quotes(tuple(symbols), demo)
    failure = messages.batch_from_results(results)
    if failure is not None:
        # One calm message instead of the same error on every row.
        components.error_panel(failure, height=rail.rail_height(len(symbols)), compact=True)
        return
    closes = data.cached_closes(tuple(symbols), "1mo", "1d", demo).frame
    components.watchlist_rail(rail.rail_rows(results, closes, selected), _open_from_rail)


def rail_header() -> None:
    with st.container(
        key="rail_header",
        horizontal=True,
        vertical_alignment="center",
        height=components.RAIL_HEADER_HEIGHT,
        border=False,
    ):
        components.eyebrow("Watchlist")
        st.space("stretch")
        st.button("Edit", key="rail_edit", type="tertiary", on_click=_edit_watchlist)


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
                    "Name": rail.UNAVAILABLE,
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


def watchlist_grid(frame: pd.DataFrame) -> pd.DataFrame:
    """The grid's copy of the watchlist: P/E as text, because the grid prints a
    missing number as a faded "None" (indices have no P/E)."""
    grid = frame.copy()
    grid["P/E"] = [fmt.format_ratio(v, 1) for v in frame["P/E"]]
    return grid


def table_height(rows: int, row_height: int = 44) -> int:
    """Pixel height that shows every row of a dataframe without inner scrolling."""
    return (max(rows, 1) + 1) * row_height + 2


EMPTY_WATCHLIST = messages.Copy(
    "Your watchlist is empty.", "Add a ticker below, for example AAPL, LT.NS or ^GSPC."
)


def save_status(who: Identity) -> None:
    """Where the watchlist is saved, with Sign in / Sign out when OIDC is configured."""
    can_sign_in = session.auth_configured()
    copy = saved.status_copy(saved.status(), who, can_sign_in=can_sign_in)
    with st.container(key="save_status", horizontal=True, vertical_alignment="center", gap="small"):
        components.fineprint(copy.text, width="content")
        if copy.sign_in:
            st.button("Sign in", key="sign_in", type="tertiary", on_click=st.login)
        elif who.kind == "account" and can_sign_in:
            st.button("Sign out", key="sign_out", type="tertiary", on_click=st.logout)


def watchlist(
    demo: bool, theme: Theme, *, first_run: bool = False, who: Identity = session.SESSION_IDENTITY
) -> None:
    components.page_title(
        "Watchlist", "Quotes refresh every minute. Select a row to open it in the overview."
    )
    symbols = watchlist_symbols()
    height = table_height(len(symbols))
    table_slot = components.slot(
        "watchlist_slot",
        height,
        first_run=first_run,
        skeleton=lambda: components.chart_skeleton(height),
    )
    # The widget mirrors the session's (saved) list; edits go through the store.
    if st.session_state.get(saved.WIDGET_KEY) != symbols:
        st.session_state[saved.WIDGET_KEY] = symbols
    st.multiselect(
        "Symbols in your watchlist",
        options=list(dict.fromkeys([*symbols, *config.DEFAULT_WATCHLIST, *config.QUICK_PICKS])),
        accept_new_options=True,
        max_selections=config.MAX_WATCHLIST,
        key=saved.WIDGET_KEY,
        placeholder="Add a ticker",
        on_change=saved.on_watchlist_change,
    )
    notice = st.session_state.pop(saved.NOTICE_KEY, None)
    if notice:
        components.fineprint(notice)
    save_status(who)
    if not symbols:
        with table_slot:
            components.error_panel(EMPTY_WATCHLIST, height=height)
        return

    results = data.cached_quotes(tuple(symbols), demo)
    failure = messages.batch_from_results(results)
    if failure is not None:
        with table_slot:
            components.error_panel(failure, height=height)
        return
    loaded = [r for r in results if r.quote is not None]
    closes = data.cached_closes(tuple(symbols), "1mo", "1d", demo).frame
    table = watchlist_frame(loaded, closes)
    st.session_state["_watchlist_symbols"] = table["Symbol"].tolist()

    def colour(value: object) -> str:
        if isinstance(value, float) and not math.isnan(value) and not fmt.rounds_to_zero(value, 2):
            return f"color: {theme.up if value > 0 else theme.down}"
        return ""

    styled = watchlist_grid(table).style.map(colour, subset=["Change"])
    table_slot.dataframe(
        styled,
        hide_index=True,
        height=table_height(len(table)),
        key="watchlist_table",
        on_select=_open_from_watchlist,
        selection_mode="single-row",
        row_height=44,
        column_config={
            "Symbol": st.column_config.TextColumn(width=104),
            "Name": st.column_config.TextColumn(width=200),
            "Price": st.column_config.TextColumn(width=96),
            "52W range": st.column_config.TextColumn(width=176),
            "Change": st.column_config.NumberColumn("Day", format="%+.2f%%", width=72),
            "1M trend": st.column_config.AreaChartColumn("1M trend", width=96, color="gray"),
            "52W position": st.column_config.ProgressColumn(
                "52W position",
                min_value=0.0,
                max_value=100.0,
                format="%.0f%%",
                help="Where the price sits between the 52-week low (0%) and high (100%).",
                color="gray",
                width=110,
            ),
            "P/E": st.column_config.TextColumn("P/E (TTM)", alignment="right"),
        },
    )
    failed = {r.symbol: r.error or "" for r in results if r.quote is None}
    if failed:
        components.fineprint(
            messages.not_shown(failed, frozenset(r.symbol for r in results if r.transient))
        )
    components.csv_download(table.drop(columns=["1M trend"]), "watchlist.csv", key="dl_watch")
