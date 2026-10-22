"""Company fundamentals: income statement, balance sheet and dividends.

Each provider returns the same normalised ``Fundamentals``: a few standard
lines per statement (latest period first), the reporting currency, and the
dividend history. Anything a provider does not report is ``None``, never 0.
Indices, currencies and most crypto have no statements; that is an empty
``Fundamentals``, not an error.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal, Protocol, runtime_checkable

import pandas as pd
import streamlit as st

from momentum import config, data
from momentum.data import DataError, DataSourceError, NoDataError

logger = logging.getLogger(__name__)

Frequency = Literal["annual", "quarterly"]
FREQUENCIES: Final[tuple[Frequency, ...]] = ("annual", "quarterly")
MAX_PERIODS: Final = {"annual": 4, "quarterly": 5}
MAX_DIVIDENDS: Final = 24

# Standard line -> the labels providers use for it, in order of preference.
_Lines = tuple[tuple[str, tuple[str, ...]], ...]
INCOME_LINES: Final[_Lines] = (
    ("Revenue", ("Total Revenue", "Operating Revenue", "totalRevenue")),
    ("Gross profit", ("Gross Profit", "grossProfit")),
    (
        "Operating income",
        ("Operating Income", "Total Operating Income As Reported", "operatingIncome"),
    ),
    ("Net income", ("Net Income", "Net Income Common Stockholders", "netIncome")),
    ("Diluted EPS", ("Diluted EPS", "dilutedEPS")),
)
BALANCE_LINES: Final[_Lines] = (
    ("Total assets", ("Total Assets", "totalAssets")),
    (
        "Total liabilities",
        ("Total Liabilities Net Minority Interest", "Total Liabilities", "totalLiabilities"),
    ),
    (
        "Shareholders' equity",
        ("Stockholders Equity", "Common Stock Equity", "totalShareholderEquity"),
    ),
    (
        "Cash and equivalents",
        ("Cash And Cash Equivalents", "cashAndCashEquivalentsAtCarryingValue"),
    ),
    ("Total debt", ("Total Debt", "shortLongTermDebtTotal")),
)
PER_SHARE: Final = frozenset({"Diluted EPS"})


@dataclass(frozen=True, slots=True)
class Statement:
    """Standard lines (label, one value per period) for periods, latest first."""

    periods: tuple[date, ...] = ()
    lines: tuple[tuple[str, tuple[float | None, ...]], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.periods or all(v is None for _, values in self.lines for v in values)

    def line(self, label: str) -> tuple[float | None, ...]:
        return next((values for name, values in self.lines if name == label), ())


@dataclass(frozen=True, slots=True)
class Dividend:
    ex_date: date
    amount: float


@dataclass(frozen=True, slots=True)
class Fundamentals:
    symbol: str
    currency: str | None = None  # the currency the statements are reported in
    income: Statement = field(default_factory=Statement)
    balance: Statement = field(default_factory=Statement)
    dividends: tuple[Dividend, ...] = ()  # newest first, in the trading currency

    @property
    def is_empty(self) -> bool:
        return self.income.is_empty and self.balance.is_empty and not self.dividends


@runtime_checkable
class FundamentalsProvider(Protocol):
    """A ``MarketDataProvider`` that can also report fundamentals."""

    def fundamentals(self, symbol: str, frequency: Frequency) -> Fundamentals: ...


# --------------------------------------------------------------------------- parsing


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def statement_from_frame(
    frame: pd.DataFrame | None,
    lines: Sequence[tuple[str, Sequence[str]]],
    limit: int,
) -> Statement:
    """A yfinance-shaped statement (rows = labels, columns = period ends) as a ``Statement``.

    Columns with no value on any standard line are dropped (yfinance sometimes
    adds an empty period), then the latest ``limit`` periods are kept.
    """
    if frame is None or frame.empty:
        return Statement()
    rows = {str(label): frame.loc[label] for label in frame.index}
    picked: list[tuple[str, list[float | None]]] = []
    for name, candidates in lines:
        source = next((rows[c] for c in candidates if c in rows), None)
        values = [_finite(source[col]) if source is not None else None for col in frame.columns]
        picked.append((name, values))
    columns = list(frame.columns)
    keep = [i for i in range(len(columns)) if any(values[i] is not None for _, values in picked)]
    keep.sort(key=lambda i: pd.Timestamp(columns[i]), reverse=True)
    keep = keep[:limit]
    return Statement(
        periods=tuple(pd.Timestamp(columns[i]).date() for i in keep),
        lines=tuple((name, tuple(values[i] for i in keep)) for name, values in picked),
    )


def dividends_from_series(series: pd.Series | None) -> tuple[Dividend, ...]:
    """Payments newest first (at most ``MAX_DIVIDENDS``), zero and missing ones dropped."""
    if series is None or series.empty:
        return ()
    out = []
    for stamp, amount in series.items():
        value = _finite(amount)
        if value is None or value <= 0:
            continue
        out.append(Dividend(pd.Timestamp(str(stamp)).date(), value))
    out.sort(key=lambda d: d.ex_date, reverse=True)
    return tuple(out[:MAX_DIVIDENDS])


def trailing_dividends(dividends: Iterable[Dividend], today: date) -> float | None:
    """Sum of the payments with an ex-date in the last 365 days (None if there were none)."""
    since = today - timedelta(days=365)
    paid = [d.amount for d in dividends if since < d.ex_date <= today]
    return sum(paid) if paid else None


def growth(values: Sequence[float | None]) -> float | None:
    """Change of the latest period vs the one before, in percent (latest first)."""
    if len(values) < 2 or values[0] is None or values[1] is None or values[1] == 0:
        return None
    return (values[0] / abs(values[1]) - 1.0) * 100.0 if values[1] > 0 else None


def margin(numerator: float | None, revenue: float | None) -> float | None:
    if numerator is None or revenue is None or revenue <= 0:
        return None
    return numerator / revenue * 100.0


# --------------------------------------------------------------------------- yahoo


def yahoo_fundamentals(symbol: str, frequency: Frequency) -> Fundamentals:
    """Statements and dividends from yfinance; upstream failures become ``DataError``."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(symbol)
        prefix = "quarterly_" if frequency == "quarterly" else ""
        income = getattr(ticker, f"{prefix}income_stmt")
        balance = getattr(ticker, f"{prefix}balance_sheet")
        dividends = ticker.dividends
        info = ticker.info
    except Exception as exc:
        raise data.translate_yahoo_error(symbol, exc) from exc
    return Fundamentals(
        symbol=symbol,
        currency=_reporting_currency(info),
        income=statement_from_frame(income, INCOME_LINES, MAX_PERIODS[frequency]),
        balance=statement_from_frame(balance, BALANCE_LINES, MAX_PERIODS[frequency]),
        dividends=dividends_from_series(dividends),
    )


def _reporting_currency(info: Mapping[str, Any] | None) -> str | None:
    if not isinstance(info, Mapping):
        return None
    for key in ("financialCurrency", "currency"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# --------------------------------------------------------------------------- loading


def load_fundamentals(
    provider: data.MarketDataProvider, symbol: str, frequency: Frequency
) -> Fundamentals:
    if frequency not in FREQUENCIES:
        msg = f"Unknown frequency {frequency!r}"
        raise ValueError(msg)
    if not isinstance(provider, FundamentalsProvider):
        msg = "This data source does not report fundamentals."
        raise NoDataError(msg)
    return provider.fundamentals(symbol, frequency)


@st.cache_data(ttl=config.TTL_PROFILE, show_spinner=False, max_entries=128)
def cached_fundamentals(symbol: str, frequency: Frequency, demo: bool) -> Fundamentals:
    """Statements change quarterly: cached for a day, like company profiles.

    Transient failures raise, and Streamlit never caches a raised exception.
    """
    return load_fundamentals(data.get_provider(demo), symbol, frequency)


def is_transient(exc: DataError) -> bool:
    return isinstance(exc, DataSourceError)


def today_utc() -> date:
    return datetime.now(tz=UTC).date()
