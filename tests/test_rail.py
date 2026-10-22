"""Watchlist rail model: rows, sparkline geometry and the payload sent to the component."""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd
import pytest

from momentum.data import Quote, QuoteResult
from momentum.ui import rail

APPLE = Quote(
    symbol="AAPL",
    name="Apple Inc.",
    currency="USD",
    quote_type="EQUITY",
    price=190.0,
    previous_close=188.0,
)
NIFTY = Quote(
    symbol="^NSEI",
    name="NIFTY 50",
    currency="INR",
    quote_type="INDEX",
    price=24_000.0,
    previous_close=24_120.0,
)


def points(path: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in re.findall(r"([\d.]+),([\d.]+)", path)]


class TestSparkline:
    def test_scales_into_the_box(self) -> None:
        coords = points(rail.sparkline_path([10.0, 20.0, 15.0], width=56, height=24, pad=2))
        assert coords[0] == (2.0, 22.0)  # lowest value sits on the bottom padding
        assert coords[1] == (28.0, 2.0)  # highest on the top padding
        assert coords[-1][0] == 54.0  # last point on the right padding

    def test_flat_series_is_drawn_through_the_middle(self) -> None:
        assert {y for _, y in points(rail.sparkline_path([5.0, 5.0, 5.0]))} == {12.0}

    def test_skips_non_finite_points_and_needs_two(self) -> None:
        assert rail.sparkline_path([]) == ""
        assert rail.sparkline_path([1.0, math.nan]) == ""
        assert len(points(rail.sparkline_path([1.0, math.nan, 3.0, math.inf]))) == 2

    def test_path_syntax(self) -> None:
        assert rail.sparkline_path([1.0, 2.0]).startswith("M")


@pytest.mark.parametrize(
    ("change", "expected"), [(0.5, "up"), (-0.5, "down"), (0.0, "flat"), (None, "flat")]
)
def test_direction_of(change: float | None, expected: str) -> None:
    assert rail.direction_of(change) == expected


def test_rail_rows() -> None:
    closes = pd.DataFrame({"AAPL": [1.0, np.nan, 3.0], "^NSEI": [3.0, 2.0, 1.0]})
    results = [
        QuoteResult("AAPL", quote=APPLE),
        QuoteResult("^NSEI", quote=NIFTY),
        QuoteResult("NOPE", error="No data found"),
    ]
    rows = rail.rail_rows(results, closes, selected="^NSEI")
    apple, nifty, nope = rows
    assert (apple.price, apple.change, apple.direction) == ("$190.00", "+1.06%", "up")
    assert apple.label == "AAPL, Apple Inc., $190.00, up 1.06% today"
    assert not apple.selected
    assert (nifty.price, nifty.direction, nifty.selected) == ("24,000.00", "down", True)
    assert nifty.label.endswith("down 0.50% today")
    assert (nope.name, nope.price, nope.change, nope.spark) == ("Quote unavailable", "—", "", "")
    assert nope.label == "NOPE, quote unavailable"


def test_payload_is_json_and_height_is_reserved() -> None:
    rows = rail.rail_rows([QuoteResult("AAPL", quote=APPLE)], pd.DataFrame(), selected=None)
    payload = rail.rail_payload(rows)
    assert json.loads(json.dumps(payload))["rows"][0]["symbol"] == "AAPL"
    assert rail.rail_height(7) == 7 * rail.ROW_HEIGHT
    assert rail.rail_height(0) == rail.ROW_HEIGHT
    assert rail.ROW_HEIGHT >= 44  # touch target


def test_a_move_that_prints_as_zero_is_flat() -> None:
    assert rail.direction_of(-0.004) == "flat"
    assert rail.direction_of(-0.006) == "down"
