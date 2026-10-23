"""One evaluation cycle: observe each symbol once, step every alert on it, store the result.

``Observer`` is the seam to market data: ``MarketObserver`` reads the configured
provider (without Streamlit's caches, since this runs outside any script run),
and the tests pass deterministic series instead. ``evaluate`` is safe to run
concurrently with itself; see ``SqlAlertRepository.apply_step``.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from momentum import data
from momentum import formatting as fmt
from momentum import indicators as ind
from momentum.alerts import rules
from momentum.data import DataError
from momentum.store.alerts import Alert, Event, SqlAlertRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Observation:
    """Metric values for one symbol at one moment (ms since the epoch)."""

    readings: dict[str, float | None]
    observed_at: int


class Observer(Protocol):
    def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> Observation:
        """Raises ``DataError`` when the symbol cannot be observed right now."""
        ...


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def sma_gap(close: pd.Series, fast: int, slow: int) -> float | None:
    """``(SMA fast / SMA slow - 1) * 100`` at the last bar (None without enough history)."""
    fast_value = ind.sma(close, fast).iloc[-1] if len(close) else math.nan
    slow_value = ind.sma(close, slow).iloc[-1] if len(close) else math.nan
    if not (math.isfinite(fast_value) and math.isfinite(slow_value)) or slow_value == 0:
        return None
    return float((fast_value / slow_value - 1.0) * 100.0)


def metric_values(
    metrics: Iterable[str], quote: data.Quote, daily_close: pd.Series | None
) -> dict[str, float | None]:
    """The requested metrics from a quote and (for indicators) daily closes."""
    out: dict[str, float | None] = {}
    for metric in metrics:
        if metric == "price":
            out[metric] = _finite(quote.price)
        elif metric == "change_pct":
            out[metric] = _finite(quote.change_percent)
        elif metric == "rsi14":
            series = ind.rsi(daily_close, 14) if daily_close is not None else None
            out[metric] = (
                _finite(float(series.iloc[-1])) if series is not None and len(series) else None
            )
        elif metric.startswith("sma_gap:"):
            _, fast, slow = metric.split(":")
            out[metric] = (
                sma_gap(daily_close, int(fast), int(slow)) if daily_close is not None else None
            )
        else:
            out[metric] = None
    return out


class MarketObserver:
    """Observe symbols through the configured market data provider."""

    def __init__(self, provider_for: Callable[[bool], data.MarketDataProvider] = data.get_provider):
        self._provider_for = provider_for

    def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> Observation:
        provider = self._provider_for(demo)
        needs_history = any(m == "rsi14" or m.startswith("sma_gap:") for m in metrics)
        history = data.load_history(provider, symbol, "2y", "1d") if needs_history else None
        try:
            info = data.load_info(provider, symbol)
        except DataError:
            if history is None:
                raise
            info = {}  # indicators still work from the bars
        quote = data.parse_quote(symbol, info, history)
        observed = quote.as_of.timestamp() * 1000 if quote.as_of else time.time() * 1000
        close = history["Close"] if history is not None else None
        return Observation(metric_values(metrics, quote, close), int(observed))


# --------------------------------------------------------------------------- messages


def describe_value(alert: Alert, value: float | None) -> str:
    condition = rules.CONDITIONS_BY_KEY.get(alert.condition)
    if value is None or condition is None:
        return fmt.PLACEHOLDER
    if condition.unit == "price":
        return fmt.format_price(value, alert.currency, alert.quote_type)
    if condition.unit == "percent":
        return fmt.format_percent(value)
    if condition.unit == "points":
        return f"{value:.1f}"
    return f"{value:+.2f}%"  # SMA gap


def describe_level(alert: Alert) -> str:
    condition = rules.CONDITIONS_BY_KEY.get(alert.condition)
    if condition is None:  # pragma: no cover - conditions are validated on create
        return f"{alert.threshold:g}"
    if condition.unit == "price":
        return fmt.format_price(alert.threshold, alert.currency, alert.quote_type)
    if condition.unit == "percent":
        return fmt.format_percent(alert.threshold, 1)
    if condition.unit == "points":
        return f"{alert.threshold:g}"
    return ""


def describe(alert: Alert) -> str:
    """``Price crosses above $200.00`` / ``SMA 50 crosses above SMA 200 (golden cross)``."""
    condition = rules.CONDITIONS_BY_KEY.get(alert.condition)
    label = condition.label if condition else alert.condition
    level = describe_level(alert)
    return f"{label} {level}".strip()


_VERBS = {
    "price_above": "rose above",
    "price_below": "fell below",
    "change_above": "is up more than",
    "change_below": "is down more than",
    "rsi_above": "RSI (14) rose above",
    "rsi_below": "RSI (14) fell below",
}


def trigger_message(alert: Alert, value: float | None) -> str:
    """The notification text, e.g. ``AAPL rose above $200.00 (now $201.30).``"""
    now = describe_value(alert, value)
    if alert.condition.startswith("sma_"):
        condition = rules.CONDITIONS_BY_KEY[alert.condition]
        return f"{alert.symbol}: {condition.label} (gap now {now})."
    verb = _VERBS.get(alert.condition, "crossed")
    level = describe_level(alert)
    if alert.condition == "change_below":
        level = fmt.format_percent(abs(alert.threshold), 1, signed=False)
    return f"{alert.symbol} {verb} {level} (now {now})."


# --------------------------------------------------------------------------- cycle


@dataclass(slots=True)
class CycleStats:
    symbols: int = 0
    alerts: int = 0
    failures: int = 0
    events: list[Event] = field(default_factory=list)


def evaluate(
    repo: SqlAlertRepository, observer: Observer, *, symbols: Iterable[str] | None = None
) -> CycleStats:
    """Observe each (symbol, data source) pair once and step every alert on it.

    ``symbols`` limits the cycle to those symbols (the ``replay`` command).
    """
    stats = CycleStats()
    only = set(symbols) if symbols is not None else None
    groups: dict[tuple[str, bool], list[Alert]] = defaultdict(list)
    for alert in repo.active():
        if only is None or alert.symbol in only:
            groups[(alert.symbol, alert.demo)].append(alert)
    for (symbol, demo), alerts in groups.items():
        stats.symbols += 1
        metrics = frozenset(a.metric for a in alerts)
        try:
            observation = observer.observe(symbol, metrics, demo)
        except DataError as exc:
            stats.failures += 1
            logger.info("Alert check for %s failed: %s", symbol, exc)
            repo.record_check([a.id for a in alerts], int(time.time() * 1000), str(exc))
            continue
        unchanged: list[str] = []
        for alert in alerts:
            stats.alerts += 1
            value = observation.readings.get(alert.metric)
            result = rules.step(alert.rule_state, value, observation.observed_at)
            if result.state == alert.rule_state:
                unchanged.append(alert.id)
                continue
            event = repo.apply_step(alert, result, message=trigger_message(alert, value))
            if event is not None:
                stats.events.append(event)
        repo.record_check(unchanged, int(time.time() * 1000), None)
    return stats
