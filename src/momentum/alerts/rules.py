"""Alert conditions and the state machine that decides when one fires (pure).

See docs/adr/0002-alerts.md. Every condition is "metric crosses threshold in a
direction". ``step`` takes the alert's stored state and one observation and
returns the next state, and whether to notify. It has no I/O, so the tests drive
it with deterministic price series.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Final, Literal

Direction = Literal["above", "below"]
State = Literal["pending", "armed", "fired"]


@dataclass(frozen=True, slots=True)
class Condition:
    """One kind of alert the UI offers."""

    key: str
    label: str  # "Price crosses above"
    metric: str  # "price", "change_pct", "rsi14", "sma_gap:50:200"
    direction: Direction
    unit: Literal["price", "percent", "points", "none"]
    default_hysteresis: float
    relative_hysteresis: bool  # hysteresis is a fraction of the threshold (prices)
    fixed_threshold: float | None = None  # crossovers compare with 0


CONDITIONS: Final[tuple[Condition, ...]] = (
    Condition("price_above", "Price crosses above", "price", "above", "price", 0.005, True),
    Condition("price_below", "Price crosses below", "price", "below", "price", 0.005, True),
    Condition(
        "change_above", "Day change rises above", "change_pct", "above", "percent", 0.5, False
    ),
    Condition(
        "change_below", "Day change falls below", "change_pct", "below", "percent", 0.5, False
    ),
    Condition("rsi_above", "RSI (14) crosses above", "rsi14", "above", "points", 5.0, False),
    Condition("rsi_below", "RSI (14) crosses below", "rsi14", "below", "points", 5.0, False),
    Condition(
        "sma_20_50_up", "SMA 20 crosses above SMA 50", "sma_gap:20:50", "above", "none", 0.25,
        False, fixed_threshold=0.0,
    ),
    Condition(
        "sma_20_50_down", "SMA 20 crosses below SMA 50", "sma_gap:20:50", "below", "none", 0.25,
        False, fixed_threshold=0.0,
    ),
    Condition(
        "sma_50_200_up", "SMA 50 crosses above SMA 200 (golden cross)", "sma_gap:50:200",
        "above", "none", 0.25, False, fixed_threshold=0.0,
    ),
    Condition(
        "sma_50_200_down", "SMA 50 crosses below SMA 200 (death cross)", "sma_gap:50:200",
        "below", "none", 0.25, False, fixed_threshold=0.0,
    ),
)  # fmt: skip
CONDITIONS_BY_KEY: Final = {c.key: c for c in CONDITIONS}
METRICS: Final = frozenset(c.metric for c in CONDITIONS)

DEFAULT_COOLDOWN_S: Final = 60 * 60
MAX_ALERTS_PER_OWNER: Final = 20


class RuleError(ValueError):
    """An alert definition that cannot be evaluated; the message is safe to show."""


def hysteresis_for(condition: Condition, threshold: float) -> float:
    """Distance the metric must retreat past the threshold before the alert re-arms."""
    if condition.relative_hysteresis:
        return abs(threshold) * condition.default_hysteresis
    return condition.default_hysteresis


def validate_threshold(condition: Condition, threshold: float) -> float:
    """The threshold to store for ``condition`` (crossovers ignore the input)."""
    if condition.fixed_threshold is not None:
        return condition.fixed_threshold
    if not math.isfinite(threshold):
        msg = "Enter a number."
        raise RuleError(msg)
    if condition.unit == "price" and threshold <= 0:
        msg = "A price level must be above zero."
        raise RuleError(msg)
    if condition.metric == "rsi14" and not 0 < threshold < 100:
        msg = "RSI runs from 0 to 100; pick a level between them, such as 70 or 30."
        raise RuleError(msg)
    if condition.metric == "change_pct":
        if condition.direction == "above" and threshold <= 0:
            msg = "Pick a rise above 0%, such as 3%."
            raise RuleError(msg)
        if condition.direction == "below" and threshold >= 0:
            msg = "Pick a fall below 0%, such as -3%."
            raise RuleError(msg)
        if abs(threshold) > 100:
            msg = "A day change beyond 100% is not a useful alert."
            raise RuleError(msg)
    return float(threshold)


@dataclass(frozen=True, slots=True)
class AlertState:
    """The parts of an alert that ``step`` reads and writes."""

    direction: Direction
    threshold: float
    hysteresis: float
    cooldown_s: int
    state: State = "pending"
    last_value: float | None = None
    last_observed_at: int | None = None  # ms
    last_fired_at: int | None = None  # ms


@dataclass(frozen=True, slots=True)
class Step:
    state: AlertState
    fired: bool  # the threshold was crossed in this step
    notify: bool  # ...and the cooldown allows a notification


def _past(direction: Direction, value: float, level: float) -> bool:
    return value >= level if direction == "above" else value <= level


def rearm_level(alert: AlertState) -> float:
    """The level the metric must get back past for the alert to re-arm."""
    if alert.direction == "above":
        return alert.threshold - alert.hysteresis
    return alert.threshold + alert.hysteresis


def step(alert: AlertState, value: float | None, observed_at: int) -> Step:
    """Advance ``alert`` by one observation of its metric at ``observed_at`` (ms)."""
    if alert.last_observed_at is not None and observed_at <= alert.last_observed_at:
        return Step(alert, fired=False, notify=False)  # already evaluated: idempotent
    if value is None or not math.isfinite(value):
        return Step(alert, fired=False, notify=False)  # nothing to compare with
    seen = replace(alert, last_value=value, last_observed_at=observed_at)
    beyond = _past(alert.direction, value, alert.threshold)
    if alert.state == "pending":
        # The first look cannot see a cross: past the level already means "fired", quietly.
        return Step(replace(seen, state="fired" if beyond else "armed"), fired=False, notify=False)
    if alert.state == "armed":
        if not beyond:
            return Step(seen, fired=False, notify=False)
        cooled = (
            alert.last_fired_at is None
            or observed_at - alert.last_fired_at >= alert.cooldown_s * 1000
        )
        last_fired = observed_at if cooled else alert.last_fired_at
        return Step(
            replace(seen, state="fired", last_fired_at=last_fired), fired=True, notify=cooled
        )
    # fired: re-arm only once the metric is back past the hysteresis band.
    level = rearm_level(alert)
    retreated = value < level if alert.direction == "above" else value > level
    return Step(replace(seen, state="armed" if retreated else "fired"), fired=False, notify=False)
