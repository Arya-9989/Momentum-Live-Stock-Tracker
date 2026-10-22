"""Static configuration: chart ranges, interval rules, defaults and cache lifetimes.

Yahoo Finance only serves intraday bars for a limited look-back window
(1m for ~7 days, <=90m for ~60 days, 1h for ~730 days), so every range
declares which intervals are valid for it. The UI only ever offers those.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

INTRADAY_INTERVALS: Final = frozenset({"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"})


@dataclass(frozen=True, slots=True)
class RangeOption:
    """A selectable chart range and the bar intervals Yahoo supports for it."""

    label: str
    period: str
    intervals: tuple[str, ...]
    default_interval: str

    def __post_init__(self) -> None:
        if self.default_interval not in self.intervals:
            msg = f"default interval {self.default_interval!r} not in {self.intervals!r}"
            raise ValueError(msg)


RANGES: Final[tuple[RangeOption, ...]] = (
    RangeOption("1D", "1d", ("1m", "5m", "15m", "30m"), "5m"),
    RangeOption("5D", "5d", ("5m", "15m", "30m", "1h"), "15m"),
    RangeOption("1M", "1mo", ("30m", "1h", "1d"), "1h"),
    RangeOption("3M", "3mo", ("1h", "1d", "1wk"), "1d"),
    RangeOption("6M", "6mo", ("1d", "1wk"), "1d"),
    RangeOption("YTD", "ytd", ("1d", "1wk"), "1d"),
    RangeOption("1Y", "1y", ("1d", "1wk"), "1d"),
    RangeOption("2Y", "2y", ("1d", "1wk", "1mo"), "1d"),
    RangeOption("5Y", "5y", ("1d", "1wk", "1mo"), "1wk"),
    RangeOption("Max", "max", ("1wk", "1mo", "3mo"), "1mo"),
)
RANGES_BY_LABEL: Final = {r.label: r for r in RANGES}
DEFAULT_RANGE: Final = "6M"

INTERVAL_LABELS: Final = {
    "1m": "1 min",
    "2m": "2 min",
    "5m": "5 min",
    "15m": "15 min",
    "30m": "30 min",
    "60m": "1 hour",
    "90m": "90 min",
    "1h": "1 hour",
    "1d": "Daily",
    "1wk": "Weekly",
    "1mo": "Monthly",
    "3mo": "Quarterly",
}


def get_range(label: str) -> RangeOption:
    """Return the range for ``label``, falling back to the default range."""
    return RANGES_BY_LABEL.get(label, RANGES_BY_LABEL[DEFAULT_RANGE])


def resolve_interval(range_label: str, interval: str | None) -> str:
    """Return ``interval`` if Yahoo supports it for the range, else the range default."""
    option = get_range(range_label)
    return interval if interval in option.intervals else option.default_interval


def is_intraday(interval: str) -> bool:
    return interval in INTRADAY_INTERVALS


# Moving averages need history *before* the visible window (SMA 200 needs 200 bars,
# and the EMA/RSI recursions need a run-in before they match charting platforms).
# For daily-and-longer bars Yahoo has no look-back limit, so a longer period is
# fetched, indicators are computed on it, and the result is trimmed for display.
WARMUP_BARS: Final = 250
_BARS_PER_YEAR: Final = {"1d": 252, "1wk": 52, "1mo": 12, "3mo": 4}
_VISIBLE_YEARS: Final = {
    "1mo": 1 / 12,
    "3mo": 0.25,
    "6mo": 0.5,
    "ytd": 1.0,  # at most a year
    "1y": 1.0,
    "2y": 2.0,
    "5y": 5.0,
    "10y": 10.0,
}
_FETCHABLE_YEARS: Final = (("1y", 1.0), ("2y", 2.0), ("5y", 5.0), ("10y", 10.0))


def fetch_period(period: str, interval: str) -> str:
    """The shortest Yahoo period that covers the visible range plus ``WARMUP_BARS`` bars.

    Intraday look-back is capped by Yahoo, so intraday requests are left as they are;
    ``max`` has no earlier history to add. Both start with undefined indicators.
    """
    if is_intraday(interval) or period not in _VISIBLE_YEARS:
        return period
    needed = _VISIBLE_YEARS[period] + WARMUP_BARS / _BARS_PER_YEAR.get(interval, 252)
    return next((name for name, years in _FETCHABLE_YEARS if years >= needed), "max")


DEFAULT_TICKER: Final = "AAPL"
DEFAULT_WATCHLIST: Final = ("AAPL", "MSFT", "NVDA", "LT.NS", "RELIANCE.NS", "^GSPC", "^NSEI")
DEFAULT_COMPARISON: Final = ("AAPL", "MSFT", "^GSPC")
QUICK_PICKS: Final = ("AAPL", "MSFT", "NVDA", "TSLA", "LT.NS", "^GSPC", "^NSEI", "BTC-USD")
MAX_COMPARE: Final = 6
MAX_WATCHLIST: Final = 12

# Cache lifetimes (seconds). Intraday bars and quotes go stale quickly;
# company profiles barely change.
TTL_QUOTE: Final = 60
TTL_INTRADAY: Final = 60
TTL_DAILY: Final = 15 * 60
TTL_PROFILE: Final = 24 * 60 * 60


def is_truthy(value: str) -> bool:
    """``"1"``, ``"true"``, ``"yes"`` and ``"on"`` (any case, padded) are true."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def demo_mode_enabled(env: dict[str, str] | None = None) -> bool:
    """Demo mode serves deterministic synthetic data instead of calling Yahoo."""
    return is_truthy((env if env is not None else dict(os.environ)).get("MOMENTUM_DEMO", ""))
