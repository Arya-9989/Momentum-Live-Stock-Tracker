"""The alert state machine, driven by deterministic price series."""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence

import numpy as np
import pytest

from momentum.alerts import rules
from momentum.alerts.rules import AlertState, step

MINUTE = 60_000


def above(threshold: float = 100.0, hysteresis: float = 0.5, cooldown_s: int = 0) -> AlertState:
    return AlertState("above", threshold, hysteresis, cooldown_s)


def below(threshold: float = 100.0, hysteresis: float = 0.5, cooldown_s: int = 0) -> AlertState:
    return AlertState("below", threshold, hysteresis, cooldown_s)


def run(
    alert: AlertState, series: Sequence[float | None], start: int = MINUTE
) -> tuple[list[int], AlertState]:
    """Feed one value per minute; returns the indexes that notified, and the final state."""
    notified = []
    for i, value in enumerate(series):
        result = step(alert, value, start + i * MINUTE)
        alert = result.state
        if result.notify:
            notified.append(i)
    return notified, alert


class TestCrossings:
    def test_first_look_never_notifies(self) -> None:
        notified, state = run(above(), [105.0])
        assert notified == []
        assert state.state == "fired"  # already past: waits for a retreat before arming
        notified, state = run(above(), [95.0])
        assert notified == []
        assert state.state == "armed"

    def test_slow_rise_notifies_once(self) -> None:
        series = [97.0, 98.0, 99.0, 99.9, 100.0, 100.4, 101.0, 102.0]
        notified, state = run(above(), series)
        assert notified == [4]  # touching the level counts
        assert state.state == "fired"

    def test_wobble_inside_the_band_does_not_storm(self) -> None:
        # Back and forth across 100 while never retreating below 99.5.
        series = [98.0, 100.1, 99.8, 100.2, 99.6, 100.3, 99.7, 100.05]
        notified, _ = run(above(hysteresis=0.5), series)
        assert notified == [1]

    def test_retreat_past_the_band_re_arms(self) -> None:
        series = [98.0, 101.0, 99.4, 100.5, 99.0, 102.0]
        notified, _ = run(above(hysteresis=0.5), series)
        assert notified == [1, 3, 5]

    def test_retreat_to_exactly_the_band_edge_does_not_re_arm(self) -> None:
        notified, _ = run(above(hysteresis=0.5), [98.0, 101.0, 99.5, 101.0])
        assert notified == [1]

    def test_gap_through_the_level_notifies_once(self) -> None:
        notified, _ = run(above(), [90.0, 130.0, 131.0, 129.0])
        assert notified == [1]

    def test_cooldown_moves_state_but_stays_quiet(self) -> None:
        # Re-arms and crosses again 3 minutes later; the cooldown is 10 minutes.
        series = [98.0, 101.0, 99.0, 101.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 101.0]
        notified, state = run(above(cooldown_s=600), series)
        assert notified == [1, 12]  # index 3 crossed inside the cooldown
        assert state.last_fired_at == MINUTE + 12 * MINUTE

    def test_below_is_symmetric(self) -> None:
        series = [103.0, 101.0, 100.0, 100.4, 99.0, 100.6, 99.9]
        notified, _ = run(below(hysteresis=0.5), series)
        assert notified == [2, 6]


class TestRobustness:
    def test_the_same_observation_is_evaluated_once(self) -> None:
        alert = step(above(), 95.0, MINUTE).state
        first = step(alert, 101.0, 2 * MINUTE)
        assert first.notify
        # A rerun with the same data after the state was stored. (A second worker
        # holding the old state is stopped by the database's compare-and-set; see
        # test_alert_engine.py.)
        again = step(first.state, 101.0, 2 * MINUTE)
        assert not again.notify
        assert again.state == first.state
        older = step(first.state, 90.0, MINUTE)  # an out-of-order, older observation
        assert older.state == first.state

    @pytest.mark.parametrize("missing", [None, math.nan, math.inf])
    def test_missing_values_change_nothing(self, missing: float | None) -> None:
        alert = step(above(), 95.0, MINUTE).state
        result = step(alert, missing, 2 * MINUTE)
        assert result.state == alert
        assert not result.fired

    def test_random_walks_never_notify_without_a_retreat(self) -> None:
        """Invariant: between two notifications the metric went back past the band."""
        rng = np.random.default_rng(7)
        for _ in range(200):
            series = list(100.0 + np.cumsum(rng.normal(0, 0.4, 300)))
            alert = above(threshold=100.0, hysteresis=0.5)
            notified, _ = run(alert, series)
            for prev, nxt in itertools.pairwise(notified):
                assert min(series[prev:nxt]) < 99.5
            for i in notified:
                assert series[i] >= 100.0
                assert i == 0 or any(v < 100.0 for v in series[:i])


class TestConditions:
    def test_every_condition_has_a_unique_key_and_known_metric(self) -> None:
        assert len(rules.CONDITIONS_BY_KEY) == len(rules.CONDITIONS)
        assert {"price", "change_pct", "rsi14", "sma_gap:20:50", "sma_gap:50:200"} == rules.METRICS

    def test_hysteresis_is_relative_for_prices(self) -> None:
        price = rules.CONDITIONS_BY_KEY["price_above"]
        assert rules.hysteresis_for(price, 200.0) == pytest.approx(1.0)
        assert rules.hysteresis_for(rules.CONDITIONS_BY_KEY["rsi_above"], 70.0) == 5.0

    def test_crossovers_ignore_the_level(self) -> None:
        cross = rules.CONDITIONS_BY_KEY["sma_50_200_up"]
        assert rules.validate_threshold(cross, 12345.0) == 0.0

    @pytest.mark.parametrize(
        ("key", "threshold", "message"),
        [
            ("price_above", 0.0, "above zero"),
            ("price_below", -5.0, "above zero"),
            ("price_above", math.nan, "Enter a number"),
            ("rsi_above", 100.0, "between them"),
            ("rsi_below", 0.0, "between them"),
            ("change_above", -2.0, "rise above 0%"),
            ("change_below", 2.0, "fall below 0%"),
            ("change_above", 150.0, "beyond 100%"),
        ],
    )
    def test_invalid_levels(self, key: str, threshold: float, message: str) -> None:
        with pytest.raises(rules.RuleError, match=message):
            rules.validate_threshold(rules.CONDITIONS_BY_KEY[key], threshold)

    def test_rearm_level(self) -> None:
        assert rules.rearm_level(above(100.0, 2.0)) == 98.0
        assert rules.rearm_level(below(100.0, 2.0)) == 102.0
