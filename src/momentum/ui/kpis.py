"""Pure builders for KPI cards and the key-statistics table (no Streamlit calls)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from momentum import formatting as fmt
from momentum import indicators as ind
from momentum.data import Quote

DeltaColor = Literal["normal", "inverse", "off"]

_PERIODS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12, "3mo": 4}


@dataclass(frozen=True, slots=True)
class Kpi:
    label: str
    value: str
    delta: str | None = None
    delta_color: DeltaColor = "normal"
    help: str | None = None


def periods_per_year(interval: str, quote_type: str | None) -> int | None:
    if interval == "1d" and quote_type == "CRYPTOCURRENCY":
        return 365  # crypto trades every day
    return _PERIODS_PER_YEAR.get(interval)


def build_kpis(quote: Quote, frame: pd.DataFrame, range_label: str, interval: str) -> list[Kpi]:
    """KPI cards for the overview. Equities get valuation cards; indices, FX and
    crypto (no market cap / P/E) get price-action cards instead."""
    close = frame["Close"]
    currency, qtype = quote.currency, quote.quote_type

    change = float(close.iloc[-1] - close.iloc[0]) if len(close) >= 2 else None
    ret = ind.period_return(close)
    cards = [
        Kpi(
            f"{range_label} return",
            fmt.format_change(change, currency, qtype),
            fmt.format_percent(ret) if ret is not None else None,
            help="Change from the first to the last bar in the selected range.",
        )
    ]

    ppy = periods_per_year(interval, qtype)
    vol = ind.annualized_volatility(close, ppy) if ppy else None
    vol_card = Kpi(
        "Volatility",
        fmt.format_percent(vol, 1, signed=False),
        help="Annualised standard deviation of bar-to-bar returns over the range "
        "(daily or longer bars only).",
    )
    drawdown = ind.max_drawdown(close)

    if quote.market_cap or quote.trailing_pe:
        volume_delta = None
        if quote.volume and quote.average_volume:
            ratio = (quote.volume / quote.average_volume - 1) * 100
            volume_delta = f"{fmt.format_percent(ratio, 0)} vs avg"
        cards += [
            Kpi("Market cap", fmt.format_market_cap(quote.market_cap, currency)),
            Kpi(
                "P/E (TTM)",
                fmt.format_ratio(quote.trailing_pe),
                help="Trailing twelve-month price/earnings. Blank when earnings are negative.",
            ),
            Kpi(
                "Volume",
                fmt.format_compact(quote.volume),
                volume_delta,
                "off",
                help="Latest session volume compared with the 3-month average.",
            ),
            vol_card,
        ]
    else:
        cards += [
            Kpi("Day high", fmt.format_price(quote.day_high, currency, qtype)),
            Kpi("Day low", fmt.format_price(quote.day_low, currency, qtype)),
            Kpi(
                "Drawdown",
                fmt.format_percent(drawdown, 1),
                help="Largest peak-to-trough fall within the selected range.",
            ),
            vol_card,
        ]
    return cards


def currency_label(currency: str | None) -> str:
    """``GBp`` -> ``GBP (quoted in pence)``."""
    if not currency:
        return fmt.PLACEHOLDER
    if currency in fmt.MINOR_UNIT_CURRENCIES:
        unit = "pence" if fmt.MINOR_UNIT_SUFFIXES[currency] == "p" else "cents"
        return f"{fmt.MINOR_UNIT_CURRENCIES[currency][0]} (quoted in {unit})"
    return currency.upper()


def key_stats(quote: Quote) -> list[tuple[str, str]]:
    """Label/value rows for the key statistics table; rows with no data are skipped."""
    c, t = quote.currency, quote.quote_type
    rows = [
        ("Previous close", fmt.format_price(quote.previous_close, c, t)),
        ("Open", fmt.format_price(quote.open, c, t)),
        ("Volume", fmt.format_compact(quote.volume)),
        ("Avg. volume (3M)", fmt.format_compact(quote.average_volume)),
        ("Market cap", fmt.format_market_cap(quote.market_cap, c)),
        ("P/E (TTM)", fmt.format_ratio(quote.trailing_pe)),
        ("Forward P/E", fmt.format_ratio(quote.forward_pe)),
        ("EPS (TTM)", fmt.format_change(quote.eps, c, t).lstrip("+")),
        ("Beta", fmt.format_number(quote.beta)),
        ("Exchange", quote.exchange or fmt.PLACEHOLDER),
        ("Currency", currency_label(quote.currency)),
    ]
    return [(label, value) for label, value in rows if value != fmt.PLACEHOLDER]
