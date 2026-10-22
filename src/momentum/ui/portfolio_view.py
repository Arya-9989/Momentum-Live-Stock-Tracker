"""The Portfolio tab: totals, holdings and transactions (FIFO; see docs/adr/0003).

Numbers lead: the value is the hero, then the day's change and the P&L, all in
tabular figures. Every amount stays in its instrument's currency; totals are per
currency unless the viewer asks for a conversion at today's rate.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Final

import pandas as pd
import streamlit as st

from momentum import data
from momentum import formatting as fmt
from momentum import portfolio as pf
from momentum.data import DataError
from momentum.identity import Identity
from momentum.store import StoreError
from momentum.store.portfolio import SqlPortfolioRepository, TransactionLimitError
from momentum.theme import LIGHT, Theme
from momentum.ui import alerts_view, components, messages, session

logger = logging.getLogger(__name__)

NOTICE_KEY: Final = "_portfolio_notice"
EACH: Final = "Each currency"
TARGETS: Final = ("USD", "EUR", "GBP", "INR", "JPY")
ROW_HEIGHT: Final = 68
HERO_HEIGHT: Final = 200
HERO_WIDTH: Final = 340
_STATS_GRID: Final = (
    "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:16px;row-gap:8px;"
    "margin:12px 0 0;padding:0"
)
KIND_LABELS: Final = {"buy": "Buy", "sell": "Sell", "split": "Split"}

NO_IDENTITY = messages.Copy(
    "Your portfolio needs a saved identity.",
    "This session isn't remembered, so transactions couldn't be kept. Sign in, or open "
    "Momentum with cookies allowed.",
)
UNAVAILABLE = messages.Copy(
    "Your portfolio is unavailable right now.",
    "Saved data can't be reached. Nothing is lost; try again in a moment.",
)
EMPTY = messages.Copy(
    "No holdings yet.",
    "Record a buy below. Cost basis is first in, first out, and fees count toward it.",
)


# --------------------------------------------------------------------------- pure helpers


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def direction(value: Decimal | None) -> str:
    if value is None or round(value, 2) == 0:
        return "flat"
    return "up" if value > 0 else "down"


def summary_html(total: pf.Totals, *, note: str | None = None) -> str:
    """Value as the hero; day change, unrealized and realized P&L beneath (escaped)."""
    c = total.currency
    pct = total.unrealized_pct
    unrealized = fmt.format_money(_f(total.unrealized), c, signed=True)
    pct_html = (
        f'<span class="mm-pct">{escape(fmt.format_percent(float(pct)))}</span>' if pct else ""
    )
    day = fmt.format_money(_f(total.day_change), c, signed=True)
    realized = fmt.format_money(_f(total.realized), c, signed=True)
    stats = (
        ("Day", day, direction(total.day_change)),
        ("Unrealized P&L", unrealized, direction(total.unrealized)),
        ("Realized P&L", realized, direction(total.realized)),
        ("Cost basis", fmt.format_money(_f(total.cost), c), "flat"),
    )
    cells = "".join(
        f'<div class="mm-pstat"><dt style="margin:0">{escape(label)}</dt>'
        f'<dd class="mm-num {state}" style="margin:0">{escape(text)}'
        f"{pct_html if label == 'Unrealized P&L' else ''}</dd></div>"
        for label, text, state in stats
    )
    caveat = "" if total.complete else " · some prices unavailable"
    foot = escape((note or "") + caveat).lstrip(" ·")
    return (
        f'<section class="mm-psummary" aria-label="Portfolio value in {escape(c)}" '
        f'style="height:{HERO_HEIGHT}px;box-sizing:border-box;overflow:hidden">'
        f'<p class="mm-plabel">Value · {escape(c)}</p>'
        f'<p class="mm-pvalue mm-num">{escape(fmt.format_money(_f(total.market_value), c))}</p>'
        f'<dl class="mm-pstats" style="{_STATS_GRID}">{cells}</dl>'
        + (f'<p class="mm-pnote">{foot}</p>' if foot else "")
        + "</section>"
    )


def holdings_frame(holdings: Sequence[pf.Holding]) -> pd.DataFrame:
    """The holdings table as display strings (currencies differ by row)."""
    rows = []
    for h in holdings:
        c = h.currency
        pct = fmt.format_percent(float(h.unrealized_pct)) if h.unrealized_pct is not None else ""
        rows.append(
            {
                "Symbol": h.symbol,
                "Shares": fmt.format_quantity(float(h.quantity)),
                "Avg cost": fmt.format_money(_f(h.average_cost), c),
                "Price": fmt.format_money(_f(h.price), c),
                "Value": fmt.format_money(_f(h.market_value), c),
                "Day": fmt.format_money(_f(h.day_change), c, signed=True),
                "Unrealized": fmt.format_money(_f(h.unrealized), c, signed=True),
                "Unrealized %": pct or fmt.PLACEHOLDER,
                "Realized": fmt.format_money(_f(h.realized), c, signed=True),
                "Currency": c,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "Symbol", "Shares", "Avg cost", "Price", "Value", "Day", "Unrealized",
            "Unrealized %", "Realized", "Currency",
        ],
    )  # fmt: skip


def transactions_frame(transactions: Sequence[pf.Transaction]) -> pd.DataFrame:
    rows = []
    for t in reversed(pf.ordered(transactions)):  # newest first
        major, _ = pf.major_currency(t.currency)
        unit = fmt.MINOR_UNIT_SUFFIXES.get(t.currency)
        price = (
            ""
            if t.kind == "split"
            else (
                f"{float(t.price):,.2f}{unit}" if unit else fmt.format_money(float(t.price), major)
            )
        )
        rows.append(
            {
                "Date": t.trade_date,
                "Type": KIND_LABELS[t.kind],
                "Symbol": t.symbol,
                "Quantity": f"{fmt.format_quantity(float(t.quantity))} for 1"
                if t.kind == "split"
                else fmt.format_quantity(float(t.quantity)),
                "Price": price,
                "Fees": "" if t.kind == "split" else fmt.format_money(float(t.fees), major),
                "id": t.id,
            }
        )
    return pd.DataFrame(rows, columns=["Date", "Type", "Symbol", "Quantity", "Price", "Fees", "id"])


def to_decimal(value: float | int | None) -> Decimal | None:
    """A number input's float as the decimal the viewer typed (shortest round trip)."""
    if value is None:
        return None
    try:
        return Decimal(repr(float(value)))
    except (InvalidOperation, ValueError):
        return None


def parse_date(text: str) -> date | None:
    """``2025-06-02`` (spaces trimmed) or None."""
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def price_label(currency: str | None) -> str:
    if currency in fmt.MINOR_UNIT_SUFFIXES:
        return f"Price ({fmt.MINOR_UNIT_SUFFIXES[currency].strip()})"
    symbol = fmt.currency_symbol(currency).strip()
    return f"Price ({symbol})" if symbol else "Price"


def fees_label(currency: str | None) -> str:
    major, _ = pf.major_currency(currency) if currency else ("", Decimal(1))
    symbol = fmt.currency_symbol(major).strip()
    return f"Fees ({symbol})" if symbol else "Fees"


# --------------------------------------------------------------------------- data


def _repo() -> SqlPortfolioRepository | None:
    store = session.open_store()
    return store.portfolio if store is not None else None


def marks_for(symbols: Sequence[str], demo: bool) -> dict[str, pf.Mark]:
    marks: dict[str, pf.Mark] = {}
    if not symbols:
        return marks
    for result in data.cached_quotes(tuple(symbols), demo):
        quote = result.quote
        if quote is None or quote.price is None:
            continue
        previous = quote.previous_close
        marks[result.symbol] = pf.Mark(
            Decimal(repr(quote.price)),
            Decimal(repr(previous)) if previous is not None else None,
        )
    return marks


def fx_rates(currencies: Sequence[str], target: str, demo: bool) -> dict[str, Decimal]:
    """Units of ``target`` per unit of each currency, from today's quotes (``GBPUSD=X``)."""
    rates: dict[str, Decimal] = {}
    for currency in currencies:
        if currency == target:
            continue
        try:
            quote = data.parse_quote(
                f"{currency}{target}=X", data.cached_info(f"{currency}{target}=X", demo)
            )
        except DataError:
            continue
        if quote.price:
            rates[currency] = Decimal(repr(quote.price))
    return rates


def _currency_of(symbol: str, demo: bool) -> str | None:
    try:
        quote = data.parse_quote(symbol, data.cached_info(symbol, demo))
    except DataError:
        return None
    return quote.currency


# --------------------------------------------------------------------------- callbacks


def read_form(demo: bool) -> pf.Transaction | str:
    """The transaction the form describes, or why it cannot be recorded."""
    kind: pf.Kind = st.session_state.get("pf_kind") or "buy"
    try:
        symbol = data.normalize_symbol(str(st.session_state.get("pf_symbol") or ""))
    except DataError as exc:
        return str(exc)
    currency = _currency_of(symbol, demo)
    if currency is None:
        return f"Couldn't look up {symbol}'s currency. Check the symbol, or try again in a moment."
    trade_date = parse_date(str(st.session_state.get("pf_date") or ""))
    if trade_date is None:
        return "Enter the trade date as YYYY-MM-DD, for example 2025-06-02."
    quantity = to_decimal(st.session_state.get("pf_quantity"))
    price = Decimal(0) if kind == "split" else to_decimal(st.session_state.get("pf_price"))
    fees = Decimal(0) if kind == "split" else to_decimal(st.session_state.get("pf_fees") or 0)
    if quantity is None or price is None or fees is None:
        return "Enter the quantity and price."
    return pf.Transaction(symbol, kind, trade_date, quantity, price, fees, currency)


def _add(who: Identity, demo: bool) -> None:
    repo = _repo()
    if repo is None or who.owner is None:
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title
        return
    entry = read_form(demo)
    if isinstance(entry, str):
        st.session_state[NOTICE_KEY] = entry
        return
    try:
        repo.add(who.owner, entry)
    except (pf.PortfolioError, TransactionLimitError) as exc:
        st.session_state[NOTICE_KEY] = str(exc)
        return
    except StoreError as exc:
        logger.warning("Could not save a transaction: %r", exc)
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title
        return
    st.session_state[NOTICE_KEY] = (
        f"Recorded the {entry.symbol} split."
        if entry.kind == "split"
        else f"Recorded the {KIND_LABELS[entry.kind].lower()} of {entry.symbol}."
    )


def _delete_selected(who: Identity, ids: Sequence[str]) -> None:
    repo = _repo()
    state = st.session_state.get("pf_transactions")
    rows = state.selection.rows if state is not None else []
    if repo is None or who.owner is None or not rows:
        return
    chosen = [ids[row] for row in rows if 0 <= row < len(ids)]
    try:
        deleted = repo.delete_many(who.owner, chosen)
    except pf.PortfolioError as exc:
        st.session_state[NOTICE_KEY] = str(exc)
        return
    except StoreError:
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title
        return
    st.session_state[NOTICE_KEY] = f"Deleted {deleted} transaction{'s' if deleted != 1 else ''}."


# --------------------------------------------------------------------------- view


def portfolio(raw_symbol: str, demo: bool, who: Identity, theme: Theme = LIGHT) -> None:
    components.page_title(
        "Portfolio",
        "Holdings, cost basis (first in, first out) and profit and loss, in each "
        "instrument's own currency.",
    )
    if who.owner is None:
        components.error_panel(NO_IDENTITY, height=160)
        return
    repo = _repo()
    if repo is None:
        components.error_panel(UNAVAILABLE, height=160)
        return
    try:
        transactions = repo.transactions(who.owner)
    except StoreError:
        components.error_panel(UNAVAILABLE, height=160)
        return

    positions = pf.build(transactions)
    symbols = sorted(positions)
    holdings = pf.value(positions, marks_for(symbols, demo))
    totals = pf.totals(holdings)
    _summary(totals, demo)
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        components.notice(notice)
    if holdings:
        _holdings(holdings, theme)
    _form(raw_symbol, demo, who)
    if transactions:
        _transactions(transactions, who)


def _summary(totals: list[pf.Totals], demo: bool) -> None:
    if not totals:
        components.error_panel(EMPTY, height=HERO_HEIGHT)
        return
    currencies = [t.currency for t in totals]
    options = [EACH, *dict.fromkeys([*currencies, *TARGETS])] if len(totals) > 1 else currencies
    choice = currencies[0]
    if len(totals) > 1:
        with st.container(key="pf_target_row", height=ROW_HEIGHT, border=False):
            choice = st.selectbox(
                "Show totals in",
                options,
                key="pf_target",
                width=200,
                help="Converts each currency's totals at today's exchange rate. Gains from "
                "currency moves are not separated from price moves.",
            )
    if choice == EACH or len(totals) == 1:
        _heroes([summary_html(t) for t in totals])
        return
    rates = fx_rates(currencies, choice, demo)
    converted = pf.convert(totals, choice, rates)
    if converted is None:
        components.notice(f"Today's rates into {choice} are unavailable; showing each currency.")
        _heroes([summary_html(t) for t in totals])
        return
    _heroes([summary_html(converted, note=f"All holdings at today's rates into {choice}")])


def _heroes(markup: list[str]) -> None:
    st.html(heroes_html(markup))


def heroes_html(markup: Sequence[str]) -> str:
    """One per currency: two columns on wider screens, one on phones.

    The column count depends on the viewport, never on the main column's width
    (which grows and shrinks as the rail loads), and the grid's own styles are
    inline, so the first paint is already the final layout.
    """
    return (
        "<style>@media (max-width: 719.98px){.mm-pheroes{grid-template-columns:minmax(0,1fr)"
        "!important}}</style>"
        '<div class="mm-pheroes" style="display:grid;grid-template-columns:'
        f'repeat(2,minmax(0,{HERO_WIDTH}px));column-gap:32px;row-gap:16px">'
        + "".join(markup)
        + "</div>"
    )


def _holdings(holdings: list[pf.Holding], theme: Theme) -> None:
    alerts_view.subsection("Holdings", f"{sum(1 for h in holdings if h.quantity)} open")
    frame = holdings_frame(holdings)

    def colour(value: object) -> str:
        if isinstance(value, str) and value[:1] in "+-" and value != fmt.PLACEHOLDER:
            # Real colours: the data grid cannot resolve CSS variables.
            return f"color: {theme.up if value.startswith('+') else theme.down}"
        return ""

    styled = frame.style.map(colour, subset=["Day", "Unrealized", "Unrealized %", "Realized"])
    right = st.column_config.TextColumn
    st.dataframe(
        styled,
        hide_index=True,
        key="pf_holdings",
        row_height=44,
        height=(len(frame) + 1) * 44 + 2,
        column_config={
            "Symbol": right(width=104),
            **{
                name: right(alignment="right")
                for name in (
                    "Shares",
                    "Avg cost",
                    "Price",
                    "Value",
                    "Day",
                    "Unrealized",
                    "Unrealized %",
                    "Realized",
                )
            },
            "Currency": right(width=80),
        },
    )
    components.csv_download(frame, "holdings.csv", "Download holdings (CSV)", key="dl_holdings")


def _form(raw_symbol: str, demo: bool, who: Identity) -> None:
    alerts_view.subsection("Record a transaction")
    if "pf_symbol" not in st.session_state:
        st.session_state["pf_symbol"] = raw_symbol.strip().upper() or "AAPL"
    with st.container(
        key="pf_form_a", horizontal=True, vertical_alignment="top", gap="small",
        height=ROW_HEIGHT, border=False,
    ):  # fmt: skip
        kind = (
            st.segmented_control(
                "Type",
                list(KIND_LABELS),
                format_func=KIND_LABELS.__getitem__,
                default="buy",
                key="pf_kind",
                required=True,
                width=200,
            )
            or "buy"
        )
        symbol_raw = st.text_input("Symbol", key="pf_symbol", max_chars=21, width=120)
    try:
        currency = _currency_of(data.normalize_symbol(symbol_raw or ""), demo)
    except DataError:
        currency = None
    with st.container(
        key="pf_form_b", horizontal=True, vertical_alignment="top", gap="small",
        height=ROW_HEIGHT, border=False,
    ):  # fmt: skip
        # A text field rather than a date picker: the picker's year/month/day
        # segments are 20 px tall, under the 24 px minimum touch target.
        if "pf_date" not in st.session_state:
            st.session_state["pf_date"] = date.today().isoformat()
        st.text_input(
            "Trade date",
            key="pf_date",
            max_chars=10,
            placeholder="YYYY-MM-DD",
            help="Year-month-day, for example 2025-06-02.",
            width=160,
        )
        st.number_input(
            "Ratio (new for 1 old)" if kind == "split" else "Quantity",
            key="pf_quantity",
            min_value=0.0,
            value=None,
            step=1.0,
            format="%.8g",
            width=160,
        )
    if kind != "split":
        with st.container(
            key="pf_form_c", horizontal=True, vertical_alignment="top", gap="small",
            height=ROW_HEIGHT, border=False,
        ):  # fmt: skip
            st.number_input(
                price_label(currency), key="pf_price", min_value=0.0, value=None,
                format="%.4f", width=160,
            )  # fmt: skip
            st.number_input(
                fees_label(currency), key="pf_fees", min_value=0.0, value=0.0,
                format="%.2f", width=160,
            )  # fmt: skip
    with st.container(key="pf_form_d", height=40, border=False):
        st.button(
            "Record " + KIND_LABELS[kind].lower(),
            key="pf_add",
            type="primary",
            on_click=_add,
            args=(who, demo),
            width=140,
        )


def _transactions(transactions: list[pf.Transaction], who: Identity) -> None:
    alerts_view.subsection("Transactions", f"{len(transactions)}")
    frame = transactions_frame(transactions)
    ids = frame["id"].tolist()
    shown = frame.drop(columns=["id"])
    st.dataframe(
        shown,
        hide_index=True,
        key="pf_transactions",
        on_select="rerun",
        selection_mode="multi-row",
        row_height=44,
        height=min(len(shown), 10) * 44 + 46,
        column_config={
            "Date": st.column_config.DateColumn(format="YYYY-MM-DD", width=120),
            "Quantity": st.column_config.TextColumn(alignment="right"),
            "Price": st.column_config.TextColumn(alignment="right"),
            "Fees": st.column_config.TextColumn(alignment="right"),
        },
    )
    with st.container(horizontal=True, gap="small"):
        st.button(
            "Delete selected",
            key="pf_delete",
            type="tertiary",
            on_click=_delete_selected,
            args=(who, ids),
        )
        components.csv_download(
            shown, "transactions.csv", "Download transactions (CSV)", key="dl_transactions"
        )
