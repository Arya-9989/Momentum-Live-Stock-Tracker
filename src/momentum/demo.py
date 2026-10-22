"""Deterministic synthetic market data for offline use, demos and tests.

Prices follow a seeded geometric random walk, so the same symbol always
produces the same series on a given day. Nothing here resembles real prices
beyond a plausible starting level, and the UI labels demo mode clearly.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

from momentum.config import is_intraday
from momentum.data import NoDataError


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


def _instrument(symbol: str) -> DemoInstrument:
    if symbol in INSTRUMENTS:
        return INSTRUMENTS[symbol]
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


class DemoProvider:
    """``MarketDataProvider`` that serves synthetic data and never touches the network."""

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        return synthetic_history(symbol, period, interval)

    def info(self, symbol: str) -> Mapping[str, Any]:
        return synthetic_info(symbol)
