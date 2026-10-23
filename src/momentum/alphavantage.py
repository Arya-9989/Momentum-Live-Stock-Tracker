"""Alpha Vantage as a ``MarketDataProvider`` (and ``FundamentalsProvider``).

See docs/adr/0004-second-data-provider.md. Every method returns exactly what
the Yahoo provider returns, so the parsing layer and the UI cannot tell them
apart: yfinance-shaped OHLCV frames, ``info`` mappings with Yahoo's keys, and
the ``Fundamentals`` model.

Rate limits are handled on both sides of the call: a client-side limiter spaces
requests and keeps a daily budget, and throttle replies (HTTP 200 with a
``Note``/``Information``) become ``RateLimitError``. The API key travels only as
a query parameter and never appears in an error message or a log line.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import httpx
import pandas as pd

from momentum import config, data
from momentum import fundamentals as fu
from momentum.data import DataSourceError, NoDataError, RateLimitError

logger = logging.getLogger(__name__)

BASE_URL: Final = "https://www.alphavantage.co/query"
TIMEOUT_S: Final = 10.0
RETRY_DELAYS_S: Final = (0.5, 1.0)
DEFAULT_DAILY_BUDGET: Final = 25
DEFAULT_MIN_INTERVAL_S: Final = 1.1
OVERVIEW_TTL_S: Final = 24 * 60 * 60

# Yahoo suffix -> Alpha Vantage suffix, for the exchanges its documentation lists.
SUFFIXES: Final = {
    ".L": ".LON",
    ".TO": ".TRT",
    ".V": ".TRV",
    ".DE": ".DEX",
    ".BO": ".BSE",
    ".SS": ".SHH",
    ".SZ": ".SHZ",
}
_INTRADAY: Final = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
    "1h": "60min",
}
_SERIES_KEYS: Final = ("Time Series", "Weekly Time Series", "Monthly Time Series")
_THROTTLE: Final = re.compile(r"rate limit|requests per (day|second|minute)|call frequency", re.I)
_PREMIUM: Final = re.compile(r"premium endpoint|premium plans? to (instantly )?unlock", re.I)


class UnsupportedSymbolError(NoDataError):
    """This provider does not cover the symbol (or the interval); try another provider."""


class QuotaExhaustedError(RateLimitError):
    """The daily request budget is spent; ``retry_after_s`` is the time to the next UTC day."""

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def seconds_to_utc_midnight(now: datetime | None = None) -> float:
    now = now or datetime.now(tz=UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


def to_av_symbol(symbol: str) -> str:
    """``VOD.L`` -> ``VOD.LON``; indices, FX, crypto and undocumented exchanges are refused."""
    if symbol.startswith("^") or symbol.endswith("=X") or "=" in symbol:
        msg = f"Alpha Vantage does not provide {symbol} (indices and FX are not covered)."
        raise UnsupportedSymbolError(msg)
    if re.search(r"-(USD|EUR|GBP|INR|USDT)$", symbol):
        msg = f"Alpha Vantage's free series do not cover {symbol}."
        raise UnsupportedSymbolError(msg)
    if "." in symbol:
        base, _, suffix = symbol.rpartition(".")
        mapped = SUFFIXES.get(f".{suffix}")
        if mapped is None:
            msg = f"Alpha Vantage does not document listings like {symbol}."
            raise UnsupportedSymbolError(msg)
        return base + mapped
    return symbol


# --------------------------------------------------------------------------- limiter


class RateLimiter:
    """Spaces calls by ``min_interval_s`` and refuses them once ``daily_budget`` is spent.

    Thread-safe; the budget resets at 00:00 UTC. ``exhaust`` records a quota reply
    from the server, so the provider stops calling for the rest of the day.
    """

    def __init__(
        self,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        daily_budget: int = DEFAULT_DAILY_BUDGET,
        *,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(tz=UTC).date(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.daily_budget = daily_budget
        self._clock, self._today, self._sleep = clock, today, sleep
        self._lock = threading.Lock()
        self._day = today()
        self._used = 0
        self._next_at = 0.0

    @property
    def used(self) -> int:
        return self._used

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._used = day, 0

    def acquire(self) -> None:
        with self._lock:
            self._roll()
            if self._used >= self.daily_budget:
                msg = "Alpha Vantage's daily request allowance is used up."
                raise QuotaExhaustedError(msg, seconds_to_utc_midnight())
            wait = self._next_at - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._next_at = self._clock() + self.min_interval_s
            self._used += 1

    def exhaust(self) -> None:
        with self._lock:
            self._roll()
            self._used = self.daily_budget


# --------------------------------------------------------------------------- parsing


def _num(value: Any) -> float | None:
    if value is None or value in {"None", "-", ""}:
        return None
    try:
        number = float(str(value).rstrip("%"))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def check_reply(payload: Any, what: str) -> Mapping[str, Any]:
    """Raise the right ``DataError`` for a throttle, premium or error reply."""
    if not isinstance(payload, Mapping):
        msg = f"Unexpected response from Alpha Vantage for {what}."
        raise DataSourceError(msg)
    for key in ("Note", "Information"):
        text = payload.get(key)
        if isinstance(text, str) and len(payload) <= 2:
            if "per day" in text and _THROTTLE.search(text):
                msg = "Alpha Vantage's daily request allowance is used up."
                raise QuotaExhaustedError(msg, seconds_to_utc_midnight())
            if _THROTTLE.search(text):
                msg = "Alpha Vantage is rate-limiting requests. Try again in a minute."
                raise RateLimitError(msg)
            if _PREMIUM.search(text):
                msg = f"Alpha Vantage only offers {what} on a paid plan."
                raise UnsupportedSymbolError(msg)
            msg = "Alpha Vantage refused the request (check ALPHAVANTAGE_API_KEY)."
            raise DataSourceError(msg)
    if "Error Message" in payload:
        msg = f"Alpha Vantage has no data for {what}."
        raise NoDataError(msg)
    return payload


def series_frame(payload: Mapping[str, Any]) -> pd.DataFrame:
    """An Alpha Vantage time series as a yfinance-shaped OHLCV frame (oldest first)."""
    key = next((k for k in payload if any(k.startswith(p) for p in _SERIES_KEYS)), None)
    rows = payload.get(key) if key else None
    if not isinstance(rows, Mapping) or not rows:
        return pd.DataFrame(columns=list(data.OHLCV_COLUMNS))
    records = []
    for stamp, bar in rows.items():
        if not isinstance(bar, Mapping):
            continue
        records.append(
            {
                "Date": pd.Timestamp(stamp),
                "Open": _num(bar.get("1. open")),
                "High": _num(bar.get("2. high")),
                "Low": _num(bar.get("3. low")),
                "Close": _num(bar.get("4. close")),
                "Volume": _num(bar.get("5. volume")) or 0.0,
            }
        )
    frame = pd.DataFrame.from_records(records).set_index("Date").sort_index()
    frame.index = pd.DatetimeIndex(frame.index, name="Date")
    return frame.astype(float)


_QUOTE_TYPES: Final = {"Common Stock": "EQUITY", "ETF": "ETF", "Mutual Fund": "MUTUALFUND"}


def info_mapping(
    symbol: str, quote: Mapping[str, Any], overview: Mapping[str, Any]
) -> dict[str, Any]:
    """``GLOBAL_QUOTE`` + ``OVERVIEW`` in Yahoo's ``info`` vocabulary (absent keys omitted)."""
    raw = quote.get("Global Quote")
    q: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    name = overview.get("Name") if isinstance(overview.get("Name"), str) else None
    fields: dict[str, Any] = {
        "symbol": symbol,
        "longName": name,
        "shortName": name,
        "currency": overview.get("Currency"),
        "fullExchangeName": overview.get("Exchange"),
        "quoteType": _QUOTE_TYPES.get(str(overview.get("AssetType")), "EQUITY" if name else None),
        "regularMarketPrice": _num(q.get("05. price")),
        "regularMarketPreviousClose": _num(q.get("08. previous close")),
        "regularMarketOpen": _num(q.get("02. open")),
        "regularMarketDayHigh": _num(q.get("03. high")),
        "regularMarketDayLow": _num(q.get("04. low")),
        "regularMarketVolume": _num(q.get("06. volume")),
        "fiftyTwoWeekHigh": _num(overview.get("52WeekHigh")),
        "fiftyTwoWeekLow": _num(overview.get("52WeekLow")),
        "marketCap": _num(overview.get("MarketCapitalization")),
        "trailingPE": _num(overview.get("PERatio")),
        "forwardPE": _num(overview.get("ForwardPE")),
        "trailingEps": _num(overview.get("EPS")),
        "beta": _num(overview.get("Beta")),
        "sector": overview.get("Sector"),
        "industry": overview.get("Industry"),
        "country": overview.get("Country"),
        "website": overview.get("OfficialSite"),
        "longBusinessSummary": overview.get("Description"),
    }
    return {k: v for k, v in fields.items() if v not in (None, "", "None")}


def _statement(reports: Any, lines: fu._Lines, limit: int) -> fu.Statement:
    if not isinstance(reports, list) or not reports:
        return fu.Statement()
    rows = [r for r in reports if isinstance(r, Mapping) and r.get("fiscalDateEnding")]
    rows.sort(key=lambda r: str(r["fiscalDateEnding"]), reverse=True)
    rows = rows[:limit]
    periods = tuple(date.fromisoformat(str(r["fiscalDateEnding"])) for r in rows)
    picked = []
    for name, candidates in lines:
        key = next((c for c in candidates if c in rows[0]), None) if rows else None
        picked.append((name, tuple(_num(r.get(key)) if key else None for r in rows)))
    return fu.Statement(periods, tuple(picked))


def fundamentals_from(
    symbol: str,
    income: Mapping[str, Any],
    balance: Mapping[str, Any],
    dividends: Mapping[str, Any],
    frequency: fu.Frequency,
) -> fu.Fundamentals:
    key = "quarterlyReports" if frequency == "quarterly" else "annualReports"
    limit = fu.MAX_PERIODS[frequency]
    income_reports = income.get(key)
    currency = None
    if (
        isinstance(income_reports, list)
        and income_reports
        and isinstance(income_reports[0], Mapping)
    ):
        currency = income_reports[0].get("reportedCurrency")
    payments = []
    for row in dividends.get("data", []) if isinstance(dividends.get("data"), list) else []:
        amount = _num(row.get("amount")) if isinstance(row, Mapping) else None
        stamp = row.get("ex_dividend_date") if isinstance(row, Mapping) else None
        if amount and amount > 0 and isinstance(stamp, str) and stamp not in {"None", ""}:
            payments.append(fu.Dividend(date.fromisoformat(stamp), amount))
    payments.sort(key=lambda d: d.ex_date, reverse=True)
    return fu.Fundamentals(
        symbol=symbol,
        currency=str(currency) if currency else None,
        income=_statement(income_reports, fu.INCOME_LINES, limit),
        balance=_statement(balance.get(key), fu.BALANCE_LINES, limit),
        dividends=tuple(payments[: fu.MAX_DIVIDENDS]),
    )


def trim(frame: pd.DataFrame, period: str, interval: str) -> pd.DataFrame:
    """The bars that fall in ``period``, as Yahoo would return them."""
    if frame.empty or period == "max":
        return frame
    if config.is_intraday(interval):
        days = {"1d": 1, "5d": 5}.get(period, 30)
        day = pd.DatetimeIndex(frame.index).normalize()
        sessions = sorted(set(day))[-days:]
        return frame.loc[day.isin(sessions)]
    return data.visible_window(frame, period)


# --------------------------------------------------------------------------- provider


class AlphaVantageProvider:
    """Alpha Vantage over HTTPS. Pass ``transport`` to replay recorded responses."""

    name = "Alpha Vantage"

    def __init__(
        self,
        api_key: str,
        *,
        limiter: RateLimiter | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            msg = "An Alpha Vantage API key is required."
            raise ValueError(msg)
        self._key = api_key
        self._limiter = limiter or RateLimiter()
        self._client = httpx.Client(transport=transport, timeout=TIMEOUT_S, follow_redirects=False)
        self._sleep = sleep
        self._clock = clock
        self._overviews: dict[str, tuple[float, Mapping[str, Any]]] = {}
        self._lock = threading.Lock()

    # ---- HTTP

    def _get(self, function: str, what: str, **params: str) -> Mapping[str, Any]:
        self._limiter.acquire()
        query = {"function": function, **params, "apikey": self._key}
        last: Exception | None = None
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            try:
                response = self._client.get(BASE_URL, params=query)
            except httpx.TransportError as exc:  # timeouts, refused connections, DNS
                last = exc
            else:
                if response.status_code >= 500:
                    last = DataSourceError(f"HTTP {response.status_code}")
                elif response.status_code != 200:
                    msg = f"Alpha Vantage refused the request (HTTP {response.status_code})."
                    raise DataSourceError(msg)
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        msg = f"Unexpected response from Alpha Vantage for {what}."
                        raise DataSourceError(msg) from exc
                    try:
                        return check_reply(payload, what)
                    except QuotaExhaustedError:
                        self._limiter.exhaust()
                        raise
            if attempt < len(RETRY_DELAYS_S):
                self._sleep(RETRY_DELAYS_S[attempt])
        # Never include the URL: it carries the key.
        logger.warning("Alpha Vantage %s for %s failed: %s", function, what, type(last).__name__)
        msg = "Could not reach Alpha Vantage. Check your connection and try again."
        raise DataSourceError(msg)

    # ---- MarketDataProvider

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        av = to_av_symbol(symbol)
        what = f"{symbol} ({interval} bars)"
        if config.is_intraday(interval):
            av_interval = _INTRADAY.get(interval)
            if av_interval is None:
                msg = f"Alpha Vantage has no {interval} bars."
                raise UnsupportedSymbolError(msg)
            payload = self._get("TIME_SERIES_INTRADAY", what, symbol=av, interval=av_interval)
        elif interval == "1d":
            size = "compact" if period in {"1mo", "3mo"} else "full"
            payload = self._get("TIME_SERIES_DAILY", what, symbol=av, outputsize=size)
        elif interval == "1wk":
            payload = self._get("TIME_SERIES_WEEKLY", what, symbol=av)
        elif interval in {"1mo", "3mo"}:
            payload = self._get("TIME_SERIES_MONTHLY", what, symbol=av)
        else:
            msg = f"Alpha Vantage has no {interval} bars."
            raise UnsupportedSymbolError(msg)
        frame = series_frame(payload)
        if interval == "3mo" and not frame.empty:
            frame = (
                frame.resample("QE")
                .agg(
                    {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
                )
                .dropna()
            )
        return trim(frame, period, interval)

    def _overview(self, symbol: str, av: str) -> Mapping[str, Any]:
        now = self._clock()
        with self._lock:
            cached = self._overviews.get(av)
            if cached and cached[0] > now:
                return cached[1]
        overview = self._get("OVERVIEW", symbol, symbol=av)
        with self._lock:
            self._overviews[av] = (now + OVERVIEW_TTL_S, overview)
        return overview

    def info(self, symbol: str) -> Mapping[str, Any]:
        av = to_av_symbol(symbol)
        quote = self._get("GLOBAL_QUOTE", symbol, symbol=av)
        if not quote.get("Global Quote"):
            msg = f"No data found for '{symbol}' at Alpha Vantage."
            raise NoDataError(msg)
        try:
            overview = self._overview(symbol, av)
        except NoDataError:
            overview = {}
        return info_mapping(symbol, quote, overview)

    # ---- FundamentalsProvider

    def fundamentals(self, symbol: str, frequency: fu.Frequency) -> fu.Fundamentals:
        av = to_av_symbol(symbol)
        income = self._get("INCOME_STATEMENT", symbol, symbol=av)
        balance = self._get("BALANCE_SHEET", symbol, symbol=av)
        dividends = self._get("DIVIDENDS", symbol, symbol=av)
        return fundamentals_from(symbol, income, balance, dividends, frequency)

    def close(self) -> None:
        self._client.close()
