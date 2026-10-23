"""The Fundamentals tab: key ratios, income statement, balance sheet and dividends.

Statements are shown as the company reports them, in its reporting currency
(which can differ from the trading currency: an ADR quoted in dollars may report
in another currency, and the page says which). Missing lines read "—"; a
symbol with no statements (an index, FX, most crypto) gets one calm sentence.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from html import escape
from typing import Final

import streamlit as st

from momentum import data
from momentum import formatting as fmt
from momentum import fundamentals as fu
from momentum.data import DataError, Quote
from momentum.ui import alerts_view, components, messages

ROW_HEIGHT: Final = 44
STATS_HEIGHT: Final = 148  # two rows of label, figure and note
LABEL_WIDTH: Final = 190
PERIOD_WIDTH: Final = 136
FREQUENCY_LABELS: Final = {"annual": "Annual", "quarterly": "Quarterly"}


# --------------------------------------------------------------------------- pure helpers


def period_label(period: date, frequency: fu.Frequency) -> str:
    """``FY 2025`` for annual statements (by fiscal year end), ``Sep 2026`` for quarters."""
    return f"FY {period.year}" if frequency == "annual" else period.strftime("%b %Y")


def value_text(label: str, value: float | None, currency: str | None) -> str:
    if label in fu.PER_SHARE:
        return (
            fmt.format_price(value, currency, decimals=2) if value is not None else fmt.PLACEHOLDER
        )
    return fmt.format_compact_money(value, currency)


def statement_table_html(
    title: str,
    statement: fu.Statement,
    frequency: fu.Frequency,
    currency: str | None,
    *,
    extra: Sequence[tuple[str, Sequence[float | None]]] = (),
) -> str:
    """An accessible table: periods across, lines down; ``extra`` rows are percentages."""
    head = "".join(
        f'<th scope="col">{escape(period_label(p, frequency))}</th>' for p in statement.periods
    )
    body = ""
    for label, values in statement.lines:
        cells = "".join(f"<td>{escape(value_text(label, v, currency))}</td>" for v in values)
        body += f'<tr><th scope="row">{escape(label)}</th>{cells}</tr>'
    for label, ratios in extra:
        cells = "".join(
            f"<td>{escape(fmt.format_percent(v, 1, signed=False))}</td>" for v in ratios
        )
        body += f'<tr class="mm-ratio"><th scope="row">{escape(label)}</th>{cells}</tr>'
    cols = f'<col style="width:{LABEL_WIDTH}px">' + "".join(
        f'<col style="width:{PERIOD_WIDTH}px">' for _ in statement.periods
    )
    # Fixed column widths: the table does not reflow as the main column's width
    # settles. On a phone it scrolls sideways, so the region is focusable and named.
    return (
        f'<div class="mm-ftable" role="region" aria-label="{escape(title, quote=True)}" '
        f'tabindex="0" style="height:{table_height(statement, extra)}px;overflow-x:auto">'
        f'<table style="table-layout:fixed;width:{table_width(statement)}px">'
        f'<caption class="mm-sr">{escape(title)}</caption><colgroup>{cols}</colgroup>'
        f'<thead><tr><th scope="col"><span class="mm-sr">Line</span></th>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def table_width(statement: fu.Statement) -> int:
    return LABEL_WIDTH + PERIOD_WIDTH * len(statement.periods)


def table_height(statement: fu.Statement, extra: Sequence[object] = ()) -> int:
    """Rows of 44 px, plus room for a sideways scrollbar on narrow screens."""
    return (len(statement.lines) + len(extra) + 1) * ROW_HEIGHT + 12


def margins(statement: fu.Statement) -> list[tuple[str, list[float | None]]]:
    revenue = statement.line("Revenue")
    out = []
    for label, line in (("Gross margin", "Gross profit"), ("Net margin", "Net income")):
        values = statement.line(line)
        out.append((label, [fu.margin(v, r) for v, r in zip(values, revenue, strict=True)]))
    return out


def key_stats(
    fund: fu.Fundamentals, quote: Quote | None, today: date
) -> list[tuple[str, str, str]]:
    """``(label, value, note)`` for the strip above the statements."""
    income = fund.income
    revenue = income.line("Revenue")
    latest = revenue[0] if revenue else None
    yoy = fu.growth(revenue)
    net = income.line("Net income")
    ttm = fu.trailing_dividends(fund.dividends, today)
    price = quote.price if quote else None
    yield_pct = ttm / price * 100 if ttm is not None and price else None
    quote_currency = quote.currency if quote else None
    return [
        (
            "Revenue",
            fmt.format_compact_money(latest, fund.currency),
            f"{fmt.format_percent(yoy, 1)} vs prior period" if yoy is not None else "",
        ),
        (
            "Net margin",
            fmt.format_percent(fu.margin(net[0] if net else None, latest), 1, signed=False),
            "latest period",
        ),
        (
            "Dividend (TTM)",
            fmt.format_price(ttm, quote_currency, decimals=2) if ttm is not None else "None",
            "per share, last 12 months",
        ),
        (
            "Dividend yield",
            fmt.format_percent(yield_pct, 2, signed=False)
            if yield_pct is not None
            else fmt.PLACEHOLDER,
            "at today's price",
        ),
    ]


def stats_html(stats: Sequence[tuple[str, str, str]]) -> str:
    cells = "".join(
        f'<div class="mm-pstat"><dt style="margin:0">{escape(label)}</dt>'
        f'<dd class="mm-num" style="margin:0">{escape(value)}</dd>'
        f'<dd class="mm-fnote" style="margin:0">{escape(note)}</dd></div>'
        for label, value, note in stats
    )
    # Two by two at every width, in a slot of fixed height: the first paint is final
    # whatever the viewport, and nothing below moves as the page loads.
    return (
        f'<dl class="mm-fstats" style="height:{STATS_HEIGHT}px;box-sizing:border-box;margin:0;'
        "padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));max-width:640px;"
        f'column-gap:24px;row-gap:12px;overflow:hidden">{cells}</dl>'
    )


def dividend_text(amount: float, currency: str | None) -> str:
    """Cents where that is exact (``$0.26``), up to four places otherwise (``₹8.6250``)."""
    places = 2 if round(amount, 2) == round(amount, 6) else 4
    return fmt.format_price(amount, currency, decimals=places)


def dividends_html(dividends: Sequence[fu.Dividend], quote_currency: str | None) -> str:
    rows = "".join(
        f'<tr><th scope="row">{escape(d.ex_date.strftime("%b %d, %Y"))}</th>'
        f"<td>{escape(dividend_text(d.amount, quote_currency))}</td></tr>"
        for d in dividends[:8]
    )
    count = min(len(dividends), 8) + 1
    return (
        f'<div class="mm-ftable mm-fdivs" style="height:{count * ROW_HEIGHT}px">'
        '<table><caption class="mm-sr">Recent dividends</caption>'
        '<thead><tr><th scope="col">Ex-dividend date</th><th scope="col">Per share</th></tr>'
        f"</thead><tbody>{rows}</tbody></table></div>"
    )


NO_STATEMENTS = messages.Copy(
    "No financial statements.",
    "Indices, currencies and most crypto don't publish them. Try a company, such as AAPL or LT.NS.",
)


def empty_copy(symbol: str, quote: Quote | None) -> messages.Copy:
    if quote is not None and quote.quote_type not in {None, "EQUITY"}:
        kind = (quote.quote_type or "").replace("CRYPTOCURRENCY", "crypto").lower()
        return messages.Copy(
            f"{symbol} has no financial statements.",
            f"It is {'an' if kind[:1] in 'aeiou' else 'a'} {kind}, not a company. Try a share, "
            "such as AAPL or LT.NS.",
        )
    return messages.Copy(
        f"No financial statements for {symbol}.",
        "The data source doesn't report them for this symbol.",
    )


# --------------------------------------------------------------------------- view


def fundamentals(raw_symbol: str, demo: bool) -> None:
    components.page_title(
        "Fundamentals",
        "Income statement, balance sheet and dividends, as the company reports them.",
    )
    with st.container(
        key="fund_toolbar", horizontal=True, vertical_alignment="center",
        height=components.TOOLBAR_HEIGHT, border=False,
    ):  # fmt: skip
        choice = (
            st.segmented_control(
                "Period",
                list(FREQUENCY_LABELS),
                format_func=FREQUENCY_LABELS.__getitem__,
                default="annual",
                key="fund_frequency",
                required=True,
                label_visibility="collapsed",
                persist_state="page",
            )
            or "annual"
        )
    frequency: fu.Frequency = "quarterly" if choice == "quarterly" else "annual"
    try:
        symbol = data.normalize_symbol(raw_symbol)
    except DataError as exc:
        alerts_view.panel(messages.describe(exc, raw_symbol))
        return
    try:
        quote: Quote | None = data.parse_quote(symbol, data.cached_info(symbol, demo))
    except DataError:
        quote = None
    try:
        fund = fu.cached_fundamentals(symbol, frequency, demo)
    except DataError as exc:
        copy = messages.describe(exc, symbol) if fu.is_transient(exc) else empty_copy(symbol, quote)
        alerts_view.panel(copy)
        return
    if fund.is_empty:
        alerts_view.panel(empty_copy(symbol, quote))
        return

    name = quote.name if quote else symbol
    reported = f"Figures in {fund.currency}" if fund.currency else "Currency not reported"
    if quote and fund.currency and quote.currency and _major(quote.currency) != fund.currency:
        reported += f"; the shares trade in {quote.currency}"
    _fixed("fund_source", 24, lambda: components.fineprint(f"{name} · {symbol} · {reported}."))
    stats = stats_html(key_stats(fund, quote, fu.today_utc()))
    _fixed("fund_stats", STATS_HEIGHT, lambda: st.html(stats))

    if not fund.income.is_empty:
        extra = margins(fund.income)
        _section(
            "fund_income",
            "Income statement",
            statement_table_html(
                "Income statement", fund.income, frequency, fund.currency, extra=extra
            ),
            table_height(fund.income, extra),
        )
    if not fund.balance.is_empty:
        _section(
            "fund_balance",
            "Balance sheet",
            statement_table_html("Balance sheet", fund.balance, frequency, fund.currency),
            table_height(fund.balance),
        )
    if fund.dividends:
        rows = min(len(fund.dividends), 8) + 1
        _section(
            "fund_dividends",
            "Dividends",
            dividends_html(fund.dividends, quote.currency if quote else None),
            rows * ROW_HEIGHT + 12,
        )
    else:
        alerts_view.subsection("Dividends")
        components.fineprint(f"{symbol} has paid no dividends in the period the data covers.")
    components.fineprint(
        "Fundamentals come from the market data source and may be restated or incomplete. "
        "Not financial advice."
    )


def _fixed(key: str, height: int, render: Callable[[], object]) -> None:
    """Render into a slot of known height: nothing below moves as elements load."""
    with st.container(key=key, height=height, border=False):
        render()


def _section(key: str, title: str, table: str, height: int) -> None:
    alerts_view.subsection(title)  # already in a slot of its final height
    _fixed(key, height, lambda: st.html(table))


def _major(currency: str) -> str:
    return (
        fmt.MINOR_UNIT_CURRENCIES[currency][0]
        if currency in fmt.MINOR_UNIT_CURRENCIES
        else currency
    )
