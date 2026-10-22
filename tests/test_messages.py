"""Copy for empty and error states."""

from __future__ import annotations

import pytest

from momentum.data import (
    DataError,
    DataSourceError,
    EmptyRangeError,
    InvalidSymbolError,
    NoDataError,
    Quote,
    QuoteResult,
    RateLimitError,
)
from momentum.ui import components, messages


@pytest.mark.parametrize(
    ("exc", "symbol", "title"),
    [
        (RateLimitError("rate-limiting"), "AAPL", "Yahoo Finance is busy."),
        (DataSourceError("offline"), "AAPL", "Can't reach Yahoo Finance."),
        (InvalidSymbolError("bad"), " not a ticker! ", "“not a ticker!” isn't a ticker symbol."),
        (InvalidSymbolError("empty"), "  ", "Search for a symbol."),
        (EmptyRangeError("empty"), "lt.ns", "No prices for LT.NS in this range."),
        (NoDataError("unknown"), "zzzz", "No data for ZZZZ."),
    ],
)
def test_describe(exc: DataError, symbol: str, title: str) -> None:
    copy = messages.describe(exc, symbol)
    assert copy.title == title
    assert copy.detail.endswith(".")
    assert "MOMENTUM_DEMO" not in copy.detail


def test_chart_note_does_not_repeat_the_header() -> None:
    offline = messages.chart_note(DataSourceError("x"))
    unknown = messages.chart_note(NoDataError("x"))
    assert offline.title != messages.OFFLINE.title
    assert "Yahoo Finance answers" in offline.detail
    assert "watchlist" in unknown.detail


def test_batch_failure() -> None:
    rate = "Yahoo Finance is rate-limiting requests. Try again in a minute."
    assert messages.batch_failure({"A": rate}, frozenset({"A"})) == messages.RATE_LIMITED
    assert messages.batch_failure({"A": "offline"}, frozenset({"A"})) == messages.OFFLINE
    assert messages.batch_failure({"A": "no data"}, frozenset()).title.startswith("None of")


def test_batch_from_results_only_when_everything_failed() -> None:
    ok = QuoteResult("AAPL", quote=Quote(symbol="AAPL", name="Apple"))
    down = QuoteResult("MSFT", error="offline", transient=True)
    assert messages.batch_from_results([ok, down]) is None
    assert messages.batch_from_results([]) is None
    assert messages.batch_from_results([down]) == messages.OFFLINE


def test_not_shown_names_each_symbol_and_why() -> None:
    line = messages.not_shown({"ZZZZ": "no data", "MSFT": "offline"}, frozenset({"MSFT"}))
    assert line == "Not shown: ZZZZ (no data), MSFT (Yahoo Finance unreachable)."


def test_error_header_escapes_and_keeps_the_slot_height() -> None:
    markup = components.error_header_html("<b>x", messages.describe(NoDataError("x"), "<b>x"))
    assert "&lt;B&gt;X" in markup
    assert "<b>" not in markup.lower()
    assert f"height:{components.HEADER_HEIGHT}px" in markup
    assert 'role="alert"' in markup
    panel = components.error_panel_html(messages.OFFLINE, height=420)
    assert "height:420px" in panel
    assert "Can&#x27;t reach Yahoo Finance." in panel
