from __future__ import annotations

import pytest

from momentum import config


def test_every_range_default_is_offered() -> None:
    for option in config.RANGES:
        assert option.default_interval in option.intervals
        for interval in option.intervals:
            assert interval in config.INTERVAL_LABELS


def test_range_option_rejects_inconsistent_default() -> None:
    with pytest.raises(ValueError, match="default interval"):
        config.RangeOption("X", "1d", ("1m",), "1d")


def test_one_minute_bars_only_for_short_ranges() -> None:
    # Yahoo only serves 1m bars for roughly the last week.
    offering_1m = {r.label for r in config.RANGES if "1m" in r.intervals}
    assert offering_1m == {"1D"}


@pytest.mark.parametrize(
    ("range_label", "interval", "expected"),
    [
        ("6M", "1d", "1d"),
        ("6M", "5m", "1d"),  # not allowed for 6M -> range default
        ("1D", "1d", "5m"),
        ("bogus", None, "1d"),  # unknown range -> default range (6M) default
    ],
)
def test_resolve_interval(range_label: str, interval: str | None, expected: str) -> None:
    assert config.resolve_interval(range_label, interval) == expected


@pytest.mark.parametrize(
    ("period", "interval", "expected"),
    [
        ("6mo", "1d", "2y"),
        ("1y", "1wk", "2y"),
        ("5y", "1wk", "10y"),
        ("max", "1mo", "max"),
        ("1mo", "1h", "1mo"),  # intraday look-back is capped, so no warm-up
        ("1d", "5m", "1d"),
    ],
)
def test_fetch_period_adds_warmup_for_daily_bars(period: str, interval: str, expected: str) -> None:
    assert config.fetch_period(period, interval) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), (" Yes ", True), ("on", True), ("0", False), ("", False)],
)
def test_demo_mode_flag(value: str, expected: bool) -> None:
    assert config.demo_mode_enabled({"MOMENTUM_DEMO": value}) is expected


def test_demo_mode_defaults_off() -> None:
    assert config.demo_mode_enabled({}) is False
