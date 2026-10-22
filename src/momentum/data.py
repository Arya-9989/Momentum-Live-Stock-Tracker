"""Market data layer.

Three layers, from the outside in:

1. ``MarketDataProvider`` implementations talk to a source (Yahoo Finance via
   yfinance, or the synthetic ``DemoProvider``) and translate every upstream
   failure into a ``DataError`` subclass.
2. Pure parsing functions (``normalize_symbol``, ``clean_history``,
   ``parse_quote``, ``parse_profile``) turn raw payloads into typed objects.
   They never touch the network and are unit-tested directly.
3. ``load_*`` functions combine the two, and the ``cached_*`` wrappers add
   ``st.cache_data`` TTLs. Streamlit does not cache raised exceptions, so a
   transient failure is retried on the next rerun rather than pinned for the TTL.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple, Protocol

import pandas as pd
import streamlit as st

from momentum import config

logger = logging.getLogger(__name__)

OHLCV_COLUMNS: Final = ("Open", "High", "Low", "Close", "Volume")
_SYMBOL_RE: Final = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=&]{0,19}$")
_MAX_WORKERS: Final = 8


# --------------------------------------------------------------------------- errors


class DataError(Exception):
    """Base class for every data failure; ``str(err)`` is safe to show to users."""


class InvalidSymbolError(DataError):
    """The input is not a syntactically valid ticker symbol."""


class NoDataError(DataError):
    """The source answered but has no data (unknown, delisted, or empty window)."""


class EmptyRangeError(NoDataError):
    """The symbol answered, but with no bars in the requested window."""


class DataSourceError(DataError):
    """The source could not be reached or refused the request (network, rate limit).

    These failures are transient: they are never cached, so the next rerun retries.
    """


class RateLimitError(DataSourceError):
    """Yahoo Finance is throttling requests from this server."""


# --------------------------------------------------------------------------- models


@dataclass(frozen=True, slots=True)
class Quote:
    """A point-in-time snapshot of one instrument. Any field may be missing."""

    symbol: str
    name: str
    currency: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    market_state: str | None = None
    price: float | None = None
    previous_close: float | None = None
    open: float | None = None
    day_low: float | None = None
    day_high: float | None = None
    fifty_two_week_low: float | None = None
    fifty_two_week_high: float | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    eps: float | None = None
    beta: float | None = None
    volume: float | None = None
    average_volume: float | None = None
    as_of: datetime | None = None
    timezone: str | None = None

    @property
    def change(self) -> float | None:
        if self.price is None or self.previous_close is None:
            return None
        return self.price - self.previous_close

    @property
    def change_percent(self) -> float | None:
        change = self.change
        if change is None or not self.previous_close:
            return None
        return change / self.previous_close * 100.0

    @property
    def is_index(self) -> bool:
        return self.quote_type == "INDEX"


@dataclass(frozen=True, slots=True)
class Profile:
    """Descriptive company information; absent for indices, FX and most crypto."""

    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    city: str | None = None
    website: str | None = None
    employees: int | None = None
    summary: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.sector, self.industry, self.country, self.website, self.summary))


@dataclass(frozen=True, slots=True)
class QuoteResult:
    """Outcome of loading one symbol in a batch: a quote or a user-facing error."""

    symbol: str
    quote: Quote | None = None
    error: str | None = None
    transient: bool = False  # a network/rate-limit failure worth retrying soon


class Closes(NamedTuple):
    """Close prices for several symbols on one index, plus per-symbol failures."""

    frame: pd.DataFrame
    errors: dict[str, str]
    transient: frozenset[str] = frozenset()  # symbols whose failure is worth retrying


# --------------------------------------------------------------------------- providers


class MarketDataProvider(Protocol):
    """A source of raw market data. Implementations raise ``DataError`` only."""

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame: ...

    def info(self, symbol: str) -> Mapping[str, Any]: ...


class YahooProvider:
    """Yahoo Finance via yfinance (an unofficial, best-effort API)."""

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        import yfinance as yf

        try:
            frame = yf.Ticker(symbol).history(
                period=period, interval=interval, auto_adjust=True, raise_errors=True
            )
        except Exception as exc:
            raise _translate_yahoo_error(symbol, exc) from exc
        if not isinstance(frame, pd.DataFrame):
            msg = f"Unexpected response from Yahoo Finance for {symbol}."
            raise DataSourceError(msg)
        return frame

    def info(self, symbol: str) -> Mapping[str, Any]:
        import yfinance as yf

        try:
            payload = yf.Ticker(symbol).info
        except Exception as exc:
            raise _translate_yahoo_error(symbol, exc) from exc
        if not isinstance(payload, Mapping):
            msg = f"Unexpected response from Yahoo Finance for {symbol}."
            raise DataSourceError(msg)
        return payload


def _translate_yahoo_error(symbol: str, exc: Exception) -> DataError:
    """Map yfinance/HTTP exceptions onto the small ``DataError`` hierarchy."""
    name = type(exc).__name__
    text = str(exc).lower()
    if name == "YFRateLimitError" or "rate limit" in text or "too many requests" in text:
        return RateLimitError("Yahoo Finance is rate-limiting requests. Try again in a minute.")
    if name in {"YFPricesMissingError", "YFTzMissingError", "YFTickerMissingError"} or (
        "delisted" in text or "no data found" in text or "not found" in text or "404" in text
    ):
        return NoDataError(
            f"No data found for '{symbol}'. Check the symbol: Yahoo uses suffixes such as "
            "LT.NS (NSE), VOD.L (London), or a caret for indices (^GSPC)."
        )
    if name == "YFInvalidPeriodError":
        return NoDataError(f"Yahoo Finance does not offer that range/interval for '{symbol}'.")
    logger.warning("Yahoo Finance request for %s failed: %r", symbol, exc)
    return DataSourceError(
        "Could not reach Yahoo Finance. Check your connection and try again, "
        "or turn on Demo data to keep exploring offline."
    )


def get_provider(demo: bool) -> MarketDataProvider:
    if demo:
        from momentum.demo import DemoProvider

        return DemoProvider()
    return YahooProvider()


# --------------------------------------------------------------------------- parsing


def normalize_symbol(raw: str) -> str:
    """Upper-case and validate a user-typed ticker such as ``lt.ns`` or ``^gspc``."""
    symbol = raw.strip().upper().replace(" ", "")
    if not symbol:
        msg = "Enter a ticker symbol, for example AAPL, LT.NS or ^GSPC."
        raise InvalidSymbolError(msg)
    if not _SYMBOL_RE.fullmatch(symbol):
        msg = f"'{raw.strip()}' is not a valid ticker symbol."
        raise InvalidSymbolError(msg)
    return symbol


def parse_symbol_list(raw: Iterable[str], limit: int) -> tuple[list[str], list[str]]:
    """Normalise, de-duplicate (order-preserving) and cap a list of symbols.

    Returns ``(valid, rejected)`` where ``rejected`` holds the original inputs
    that failed validation.
    """
    valid: list[str] = []
    rejected: list[str] = []
    for item in raw:
        try:
            symbol = normalize_symbol(item)
        except InvalidSymbolError:
            if item.strip():
                rejected.append(item.strip())
            continue
        if symbol not in valid:
            valid.append(symbol)
    return valid[:limit], rejected


def clean_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw OHLCV frame.

    * keeps only Open/High/Low/Close/Volume (missing Volume becomes 0),
    * converts the index to naive exchange-local timestamps so charts show
      the exchange's wall-clock time rather than the viewer's,
    * sorts, drops duplicate timestamps and rows without a close.
    """
    if frame.empty or "Close" not in frame.columns:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS), index=pd.DatetimeIndex([], name="Date"))

    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    if "Volume" not in out.columns:
        out["Volume"] = 0.0
    for column in ("Open", "High", "Low"):
        if column not in out.columns:
            out[column] = out["Close"]
    out = out.loc[:, list(OHLCV_COLUMNS)].apply(pd.to_numeric, errors="coerce")

    index = pd.DatetimeIndex(pd.to_datetime(out.index))
    if index.tz is not None:
        index = index.tz_localize(None)
    out.index = index.rename("Date")

    cleaned: pd.DataFrame = out.loc[~out.index.duplicated(keep="last")].sort_index()
    cleaned = cleaned.dropna(subset=["Close"])
    cleaned["Volume"] = cleaned["Volume"].fillna(0.0)
    return cleaned


_PERIOD_OFFSETS: Final = {
    "1mo": pd.DateOffset(months=1),
    "3mo": pd.DateOffset(months=3),
    "6mo": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1),
    "2y": pd.DateOffset(years=2),
    "5y": pd.DateOffset(years=5),
    "10y": pd.DateOffset(years=10),
}


def visible_window(frame: pd.DataFrame, period: str) -> pd.DataFrame:
    """Trim a warmed-up frame to the bars that fall inside ``period``.

    The window is anchored on the last bar, mirroring how Yahoo resolves
    relative periods. Other periods (``1d``, ``5d``, ``max``) are returned as-is
    because they are never fetched with extra warm-up history.
    """
    if frame.empty:
        return frame
    last = pd.Timestamp(frame.index[-1])
    if period == "ytd":
        return frame.loc[frame.index >= pd.Timestamp(year=last.year, month=1, day=1)]
    offset = _PERIOD_OFFSETS.get(period)
    if offset is None:
        return frame
    return frame.loc[frame.index > last - offset]


def _number(info: Mapping[str, Any], *keys: str) -> float | None:
    """First finite numeric value among ``keys`` (Yahoo sometimes sends 'Infinity')."""
    for key in keys:
        value = info.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            return number
    return None


def _text(info: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_quote(symbol: str, info: Mapping[str, Any], history: pd.DataFrame | None = None) -> Quote:
    """Build a ``Quote`` from a Yahoo ``info`` payload.

    ``history`` (cleaned daily or intraday bars) fills in the price when the
    payload is empty, which happens for some indices and when ``info`` fails.
    """
    price = _number(info, "regularMarketPrice", "currentPrice", "navPrice")
    previous_close = _number(info, "regularMarketPreviousClose", "previousClose")
    if price is None and history is not None and not history.empty:
        price = float(history["Close"].iloc[-1])

    as_of: datetime | None = None
    timestamp = _number(info, "regularMarketTime")
    if timestamp is not None:
        as_of = datetime.fromtimestamp(timestamp, tz=UTC)

    quote_type = _text(info, "quoteType")
    return Quote(
        symbol=symbol,
        name=_text(info, "longName", "shortName", "displayName") or symbol,
        currency=_text(info, "currency"),
        exchange=_text(info, "fullExchangeName", "exchange"),
        quote_type=quote_type.upper() if quote_type else None,
        market_state=_text(info, "marketState"),
        price=price,
        previous_close=previous_close,
        open=_number(info, "regularMarketOpen", "open"),
        day_low=_number(info, "regularMarketDayLow", "dayLow"),
        day_high=_number(info, "regularMarketDayHigh", "dayHigh"),
        fifty_two_week_low=_number(info, "fiftyTwoWeekLow"),
        fifty_two_week_high=_number(info, "fiftyTwoWeekHigh"),
        market_cap=_number(info, "marketCap"),
        trailing_pe=_number(info, "trailingPE"),
        forward_pe=_number(info, "forwardPE"),
        eps=_number(info, "trailingEps", "epsTrailingTwelveMonths"),
        beta=_number(info, "beta"),
        volume=_number(info, "regularMarketVolume", "volume"),
        average_volume=_number(info, "averageVolume", "averageDailyVolume3Month"),
        as_of=as_of,
        timezone=_text(info, "exchangeTimezoneShortName"),
    )


def parse_profile(info: Mapping[str, Any]) -> Profile:
    employees = _number(info, "fullTimeEmployees")
    return Profile(
        sector=_text(info, "sector", "sectorDisp"),
        industry=_text(info, "industry", "industryDisp"),
        country=_text(info, "country"),
        city=_text(info, "city"),
        website=_text(info, "website"),
        employees=int(employees) if employees is not None else None,
        summary=_text(info, "longBusinessSummary", "description"),
    )


# --------------------------------------------------------------------------- loaders


def load_history(
    provider: MarketDataProvider, symbol: str, period: str, interval: str
) -> pd.DataFrame:
    """Cleaned OHLCV bars; raises ``NoDataError`` when nothing usable comes back."""
    frame = clean_history(provider.history(symbol, period, interval))
    if frame.empty:
        msg = (
            f"No price data for '{symbol}' in this range. The symbol may be invalid or "
            "delisted, or the market has not traded in this window."
        )
        raise EmptyRangeError(msg)
    return frame


def load_info(provider: MarketDataProvider, symbol: str) -> Mapping[str, Any]:
    info = provider.info(symbol)
    if (
        not _text(info, "longName", "shortName", "quoteType")
        and _number(info, "regularMarketPrice", "currentPrice") is None
    ):
        msg = f"No quote found for '{symbol}'."
        raise NoDataError(msg)
    return info


def load_quotes(
    provider: MarketDataProvider,
    symbols: Iterable[str],
    *,
    max_workers: int = _MAX_WORKERS,
) -> list[QuoteResult]:
    """Fetch quotes concurrently; one failing symbol never fails the batch."""

    def one(symbol: str) -> QuoteResult:
        try:
            return QuoteResult(symbol, quote=parse_quote(symbol, load_info(provider, symbol)))
        except DataError as exc:
            return QuoteResult(symbol, error=str(exc), transient=isinstance(exc, DataSourceError))

    return _map_concurrently(one, list(symbols), max_workers)


def load_closes(
    provider: MarketDataProvider,
    symbols: Iterable[str],
    period: str,
    interval: str,
    *,
    max_workers: int = _MAX_WORKERS,
) -> Closes:
    """Close prices for several symbols aligned on one index, plus per-symbol errors."""

    def one(symbol: str) -> tuple[str, pd.Series | DataError]:
        try:
            return symbol, load_history(provider, symbol, period, interval)["Close"]
        except DataError as exc:
            return symbol, exc

    series: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}
    transient: set[str] = set()
    for symbol, result in _map_concurrently(one, list(symbols), max_workers):
        if isinstance(result, DataError):
            errors[symbol] = str(result)
            if isinstance(result, DataSourceError):
                transient.add(symbol)
        else:
            series[symbol] = result
    closes = pd.DataFrame(series).sort_index() if series else pd.DataFrame()
    return Closes(closes, errors, frozenset(transient))


def _map_concurrently[T, R](func: Callable[[T], R], items: list[T], max_workers: int) -> list[R]:
    if len(items) <= 1:
        return [func(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(func, items))


# --------------------------------------------------------------------------- cached API
# Arguments are plain hashables so Streamlit can key the cache on them; the
# provider is rebuilt inside, which is free.
#
# Raised errors are never memoised by st.cache_data. Batch loaders return
# per-symbol failures as values instead, so their wrappers drop the cache entry
# whenever a failure is transient: the result is shown once and the next rerun
# fetches again, rather than pinning a rate-limit message for the whole TTL.


@st.cache_data(ttl=config.TTL_INTRADAY, show_spinner=False, max_entries=256)
def _history_short_ttl(symbol: str, period: str, interval: str, demo: bool) -> pd.DataFrame:
    return load_history(get_provider(demo), symbol, period, interval)


@st.cache_data(ttl=config.TTL_DAILY, show_spinner=False, max_entries=256)
def _history_long_ttl(symbol: str, period: str, interval: str, demo: bool) -> pd.DataFrame:
    return load_history(get_provider(demo), symbol, period, interval)


def cached_history(symbol: str, period: str, interval: str, demo: bool) -> pd.DataFrame:
    """Intraday bars are cached for a minute, daily and longer bars for 15 minutes."""
    loader = _history_short_ttl if config.is_intraday(interval) else _history_long_ttl
    return loader(symbol, period, interval, demo)


@st.cache_data(ttl=config.TTL_QUOTE, show_spinner=False, max_entries=256)
def cached_info(symbol: str, demo: bool) -> dict[str, Any]:
    return dict(load_info(get_provider(demo), symbol))


@st.cache_data(ttl=config.TTL_PROFILE, show_spinner=False, max_entries=256)
def cached_profile(symbol: str, demo: bool) -> Profile:
    """Company profiles barely change, so they outlive the 60 s quote that carries them."""
    return parse_profile(cached_info(symbol, demo))


@st.cache_data(ttl=config.TTL_QUOTE, show_spinner=False, max_entries=64)
def _quotes(symbols: tuple[str, ...], demo: bool) -> list[QuoteResult]:
    return load_quotes(get_provider(demo), symbols)


def cached_quotes(symbols: tuple[str, ...], demo: bool) -> list[QuoteResult]:
    results = _quotes(symbols, demo)
    if any(r.transient for r in results):
        _quotes.clear(symbols, demo)
    return results


@st.cache_data(ttl=config.TTL_DAILY, show_spinner=False, max_entries=64)
def _closes(symbols: tuple[str, ...], period: str, interval: str, demo: bool) -> Closes:
    return load_closes(get_provider(demo), symbols, period, interval)


def cached_closes(symbols: tuple[str, ...], period: str, interval: str, demo: bool) -> Closes:
    """Daily-or-longer closes for several symbols (15 min TTL; transient failures retried)."""
    result = _closes(symbols, period, interval, demo)
    if result.transient:
        _closes.clear(symbols, period, interval, demo)
    return result


def clear_caches() -> None:
    for func in (
        _history_short_ttl,
        _history_long_ttl,
        cached_info,
        cached_profile,
        _quotes,
        _closes,
    ):
        func.clear()
