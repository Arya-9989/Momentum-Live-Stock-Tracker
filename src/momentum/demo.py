"""Deterministic synthetic market data for offline use, demos and tests.

Prices follow a seeded geometric random walk, so the same symbol always
produces the same series on a given day. Nothing here resembles real prices
beyond a plausible starting level, and the UI labels demo mode clearly.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd

from momentum.config import is_intraday
from momentum.data import NoDataError

if TYPE_CHECKING:
    from momentum.fundamentals import Frequency, Fundamentals


@dataclass(frozen=True, slots=True)
class DemoInstrument:
    name: str
    price: float
    currency: str
    exchange: str
    quote_type: str = "EQUITY"
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    pe: float | None = None
    shares: float | None = None
    timezone: str = "EST"


INSTRUMENTS: Final[dict[str, DemoInstrument]] = {
    "AAPL": DemoInstrument(
        "Apple Inc.",
        228.0,
        "USD",
        "NasdaqGS",
        sector="Technology",
        industry="Consumer Electronics",
        country="United States",
        pe=34.6,
        shares=14.8e9,
    ),
    "MSFT": DemoInstrument(
        "Microsoft Corporation",
        512.0,
        "USD",
        "NasdaqGS",
        sector="Technology",
        industry="Software - Infrastructure",
        country="United States",
        pe=37.9,
        shares=7.43e9,
    ),
    "NVDA": DemoInstrument(
        "NVIDIA Corporation",
        181.0,
        "USD",
        "NasdaqGS",
        sector="Technology",
        industry="Semiconductors",
        country="United States",
        pe=51.2,
        shares=24.3e9,
    ),
    "TSLA": DemoInstrument(
        "Tesla, Inc.",
        431.0,
        "USD",
        "NasdaqGS",
        sector="Consumer Cyclical",
        industry="Auto Manufacturers",
        country="United States",
        pe=180.4,
        shares=3.22e9,
    ),
    "LT.NS": DemoInstrument(
        "Larsen & Toubro Limited",
        3610.0,
        "INR",
        "NSE",
        sector="Industrials",
        industry="Engineering & Construction",
        country="India",
        pe=33.1,
        shares=1.37e9,
        timezone="IST",
    ),
    "RELIANCE.NS": DemoInstrument(
        "Reliance Industries Limited",
        1402.0,
        "INR",
        "NSE",
        sector="Energy",
        industry="Oil & Gas Refining & Marketing",
        country="India",
        pe=23.4,
        shares=13.5e9,
        timezone="IST",
    ),
    "VOD.L": DemoInstrument(
        "Vodafone Group Public Limited Company",
        84.0,
        "GBp",
        "LSE",
        sector="Communication Services",
        industry="Telecom Services",
        country="United Kingdom",
        shares=24.5e9,
        timezone="BST",
    ),
    "^GSPC": DemoInstrument("S&P 500", 6650.0, "USD", "SNP", quote_type="INDEX"),
    "^NSEI": DemoInstrument("NIFTY 50", 25300.0, "INR", "NSE", quote_type="INDEX", timezone="IST"),
    "EURUSD=X": DemoInstrument(
        "EUR/USD", 1.12, "USD", "CCY", quote_type="CURRENCY", timezone="BST"
    ),
    "BTC-USD": DemoInstrument(
        "Bitcoin USD", 108000.0, "USD", "CCC", quote_type="CRYPTOCURRENCY", timezone="UTC"
    ),
}

_PERIOD_DAYS: Final = {
    "1d": 1,
    "5d": 5,
    "1mo": 21,
    "3mo": 63,
    "6mo": 126,
    "1y": 252,
    "2y": 504,
    "5y": 1260,
    "10y": 2520,
    "max": 5040,
}
_INTERVAL_MINUTES: Final = {
    "1m": 1,
    "2m": 2,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "90m": 90,
    "1h": 60,
}
_RESAMPLE: Final = {"1wk": "W-FRI", "1mo": "ME", "3mo": "QE"}
_SESSION_MINUTES: Final = 390  # 09:30-16:00


def _seed(symbol: str) -> int:
    return zlib.crc32(symbol.encode())


# US dollars per unit, for synthetic exchange rates such as GBPINR=X (demo mode only).
DEMO_USD_RATES: Final = {
    "USD": 1.0,
    "EUR": 1.12,
    "GBP": 1.33,
    "INR": 0.01134,
    "JPY": 0.0066,
    "CAD": 0.72,
    "AUD": 0.66,
    "CHF": 1.25,
    "CNY": 0.14,
    "HKD": 0.128,
}


def _fx_instrument(symbol: str) -> DemoInstrument | None:
    """``GBPINR=X`` -> a currency pair priced from ``DEMO_USD_RATES``."""
    if len(symbol) != 8 or not symbol.endswith("=X"):
        return None
    base, quote = symbol[:3], symbol[3:6]
    if base not in DEMO_USD_RATES or quote not in DEMO_USD_RATES:
        return None
    rate = DEMO_USD_RATES[base] / DEMO_USD_RATES[quote]
    return DemoInstrument(f"{base}/{quote}", rate, quote, "CCY", quote_type="CURRENCY")


def _instrument(symbol: str) -> DemoInstrument:
    if symbol in INSTRUMENTS:
        return INSTRUMENTS[symbol]
    fx = _fx_instrument(symbol)
    if fx is not None:
        return fx
    if symbol.startswith("INVALID") or symbol.startswith("ZZZ"):
        msg = f"No data found for '{symbol}' (demo mode)."
        raise NoDataError(msg)
    rng = np.random.default_rng(_seed(symbol))
    return DemoInstrument(
        f"{symbol} (synthetic)",
        float(rng.uniform(20, 400)),
        "USD",
        "DEMO",
        pe=float(rng.uniform(8, 45)),
        shares=float(rng.uniform(1e8, 5e9)),
    )


def _session_days(period: str, today: pd.Timestamp, crypto: bool) -> pd.DatetimeIndex:
    if period == "ytd":
        start = pd.Timestamp(year=today.year, month=1, day=1)
        if crypto:
            return pd.date_range(start, today, freq="D")
        return pd.bdate_range(start, today)
    count = _PERIOD_DAYS.get(period, 126)
    if crypto:
        return pd.date_range(end=today, periods=round(count * 365 / 252), freq="D")
    return pd.bdate_range(end=today, periods=count)


def _walk(rng: np.random.Generator, start: float, steps: int, vol: float) -> np.ndarray:
    drift = 0.0003
    shocks = rng.normal(drift, vol, steps)
    # A slow sine component gives the series visible trends for indicators to find.
    trend = 0.004 * np.sin(np.linspace(0, 3 * np.pi, steps))
    return start * np.exp(np.cumsum(shocks + trend * vol * 10))


def synthetic_history(
    symbol: str, period: str, interval: str, today: pd.Timestamp | None = None
) -> pd.DataFrame:
    """OHLCV bars shaped like a yfinance ``history`` frame (tz-aware index)."""
    inst = _instrument(symbol)
    today = (today or pd.Timestamp.now()).normalize()
    if today.dayofweek >= 5 and inst.quote_type != "CRYPTOCURRENCY":
        today -= pd.offsets.BDay(1)
    crypto = inst.quote_type == "CRYPTOCURRENCY"
    days = _session_days(period, today, crypto)

    if is_intraday(interval):
        step = _INTERVAL_MINUTES[interval]
        bars = max(1, _SESSION_MINUTES // step)
        offsets = pd.to_timedelta(np.arange(bars) * step, unit="min") + pd.Timedelta(hours=9.5)
        index = pd.DatetimeIndex([day + off for day in days for off in offsets])
        vol = 0.0016 * np.sqrt(step / 5)
    else:
        index = days
        vol = 0.016

    rng = np.random.default_rng(_seed(f"{symbol}|{interval}|{today.date()}"))
    end_level = inst.price
    close = _walk(rng, 1.0, len(index), vol)
    close = close / close[-1] * end_level
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, vol / 4, len(index)))
    spread = np.abs(rng.normal(0, vol * 0.8, len(index)))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    base_volume = 0 if inst.quote_type in {"INDEX", "CURRENCY"} else (inst.shares or 1e9) * 0.004
    volume = (base_volume * rng.lognormal(0, 0.35, len(index))).round()

    frame = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index.tz_localize("UTC").rename("Date"),
    )
    if interval in _RESAMPLE:
        frame = (
            frame.resample(_RESAMPLE[interval])
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna()
        )
    return frame


def synthetic_info(symbol: str, today: pd.Timestamp | None = None) -> dict[str, Any]:
    """An ``info`` payload with the same keys yfinance returns."""
    inst = _instrument(symbol)
    year = synthetic_history(symbol, "1y", "1d", today)
    last, prev = float(year["Close"].iloc[-1]), float(year["Close"].iloc[-2])
    info: dict[str, Any] = {
        "symbol": symbol,
        "longName": inst.name,
        "shortName": inst.name,
        "currency": inst.currency,
        "fullExchangeName": inst.exchange,
        "quoteType": inst.quote_type,
        "marketState": "CLOSED",
        "regularMarketPrice": last,
        "regularMarketPreviousClose": prev,
        "regularMarketOpen": float(year["Open"].iloc[-1]),
        "regularMarketDayLow": float(year["Low"].iloc[-1]),
        "regularMarketDayHigh": float(year["High"].iloc[-1]),
        "fiftyTwoWeekLow": float(year["Low"].min()),
        "fiftyTwoWeekHigh": float(year["High"].max()),
        "regularMarketVolume": float(year["Volume"].iloc[-1]),
        "averageVolume": float(year["Volume"].tail(63).mean()),
        "exchangeTimezoneShortName": inst.timezone,
        "regularMarketTime": int(year.index[-1].timestamp()) + 16 * 3600,
    }
    if inst.shares:
        price_major = last / 100 if inst.currency == "GBp" else last
        info["marketCap"] = price_major * inst.shares
    if inst.pe:
        info["trailingPE"] = inst.pe
        info["forwardPE"] = round(inst.pe * 0.85, 2)
        info["trailingEps"] = round(last / inst.pe, 2)
        info["beta"] = 1.1
    if inst.sector:
        info.update(
            sector=inst.sector,
            industry=inst.industry,
            country=inst.country,
            website="https://example.com",
            longBusinessSummary=(
                f"Demo mode: this is placeholder text for {inst.name.rstrip('.')}. Prices, "
                "ratios and market data shown in demo mode are synthetic and are not real "
                "quotes."
            ),
        )
    return info


# Annual dividend per share (in the quote unit) and payments a year, for demo listings.
DEMO_DIVIDENDS: Final = {
    "AAPL": (1.04, 4),
    "MSFT": (3.32, 4),
    "NVDA": (0.04, 4),
    "LT.NS": (34.0, 1),
    "RELIANCE.NS": (5.5, 1),
    "VOD.L": (4.5, 2),
}


def synthetic_fundamentals(
    symbol: str, frequency: str, today: pd.Timestamp | None = None
) -> Fundamentals:
    """Plausible statements derived from the demo market cap and P/E (equities only).

    Net income is market cap / P/E; revenue follows from a seeded net margin;
    earlier periods shrink with a seeded growth rate. Indices, FX and crypto have
    none, as in real data.
    """
    from momentum.fundamentals import (
        BALANCE_LINES,
        INCOME_LINES,
        MAX_PERIODS,
        Dividend,
        Fundamentals,
        Statement,
    )

    inst = _instrument(symbol)
    if inst.quote_type != "EQUITY" or not inst.pe or not inst.shares:
        return Fundamentals(symbol=symbol)
    today = (today or pd.Timestamp.now()).normalize()
    rng = np.random.default_rng(_seed(f"{symbol}|fundamentals"))
    price_major = inst.price / 100 if inst.currency == "GBp" else inst.price
    annual_net = price_major * inst.shares / inst.pe
    net_margin = float(rng.uniform(0.12, 0.3))
    yearly_growth = float(rng.uniform(0.03, 0.12))
    quarterly = frequency == "quarterly"
    count = MAX_PERIODS["quarterly" if quarterly else "annual"]
    if quarterly:
        ends = pd.date_range(end=today - pd.offsets.QuarterEnd(1), periods=count, freq="QE")[::-1]
        scale, step = 0.25, (1 + yearly_growth) ** 0.25
    else:
        ends = pd.date_range(end=today - pd.offsets.YearEnd(1), periods=count, freq="YE")[::-1]
        scale, step = 1.0, 1 + yearly_growth
    income: dict[str, list[float | None]] = {name: [] for name, _ in INCOME_LINES}
    balance: dict[str, list[float | None]] = {name: [] for name, _ in BALANCE_LINES}
    for i in range(count):
        net = annual_net * scale / step**i * float(rng.uniform(0.92, 1.08))
        revenue = net / net_margin
        income["Revenue"].append(round(revenue, -3))
        income["Gross profit"].append(round(revenue * 0.44, -3))
        income["Operating income"].append(round(revenue * net_margin * 1.3, -3))
        income["Net income"].append(round(net, -3))
        income["Diluted EPS"].append(round(net / inst.shares, 2))
        assets = annual_net / net_margin * 1.1 / step**i
        liabilities = assets * 0.58
        balance["Total assets"].append(round(assets, -3))
        balance["Total liabilities"].append(round(liabilities, -3))
        balance["Shareholders' equity"].append(round(assets - liabilities, -3))
        balance["Cash and equivalents"].append(round(assets * 0.14, -3))
        balance["Total debt"].append(round(liabilities * 0.36, -3))
    periods = tuple(ts.date() for ts in ends)
    dividends: list[Dividend] = []
    if symbol in DEMO_DIVIDENDS:
        per_year, payments = DEMO_DIVIDENDS[symbol]
        months = 12 // payments
        for k in range(payments * 3):
            ex = (today - pd.DateOffset(months=1 + k * months)).date()
            dividends.append(
                Dividend(ex, round(per_year / payments / (1.05 ** (k // payments)), 4))
            )
    return Fundamentals(
        symbol=symbol,
        currency=inst.currency if inst.currency != "GBp" else "GBP",
        income=Statement(periods, tuple((k, tuple(v)) for k, v in income.items())),
        balance=Statement(periods, tuple((k, tuple(v)) for k, v in balance.items())),
        dividends=tuple(dividends),
    )


class DemoProvider:
    """``MarketDataProvider`` that serves synthetic data and never touches the network."""

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        return synthetic_history(symbol, period, interval)

    def info(self, symbol: str) -> Mapping[str, Any]:
        return synthetic_info(symbol)

    def fundamentals(self, symbol: str, frequency: Frequency) -> Fundamentals:
        return synthetic_fundamentals(symbol, frequency)
