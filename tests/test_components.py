"""Pure markup builders and the static assets they depend on."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from momentum.data import Quote
from momentum.ui import components

UI = Path(components.__file__).parent

QUOTE = Quote(
    symbol="AAPL",
    name="Apple <Inc.>",
    currency="USD",
    exchange="NasdaqGS",
    quote_type="EQUITY",
    market_state="REGULAR",
    price=228.0,
    previous_close=227.4923,
    day_low=223.12,
    day_high=232.0,
    as_of=datetime(2026, 10, 22, 16, 0, tzinfo=UTC),
)


def test_quote_header_payload() -> None:
    payload = components.quote_header_payload(QUOTE)
    assert json.loads(json.dumps(payload)) == payload  # plain JSON for the component
    # Names stay plain text: quote.js sets them with textContent, never as markup.
    assert payload["name"] == "Apple <Inc.>"
    assert payload["price"] == "$228.00"
    assert payload["price_value"] == 228.0
    assert payload["state"] == {"code": "REGULAR", "label": "Market open"}
    assert payload["as_of"] == {
        "iso": "2026-10-22T16:00:00+00:00",
        "label": "As of Oct 22, 16:00 UTC",
    }


def test_day_change_uses_the_price_precision() -> None:
    change = components.quote_header_payload(QUOTE)["change"]
    assert change["text"] == "+$0.51 (+0.22%)"  # not +$0.5077
    assert change["direction"] == "up"
    assert change["value"] > 0


def test_header_without_change_keeps_its_line() -> None:
    assert components.quote_header_payload(Quote(symbol="X", name="X", price=1.0))["change"] is None
    js = (UI / "quote.js").read_text(encoding="utf-8")
    assert r'"\u00a0"' in js  # a non-breaking space holds the line's height


def test_payload_numbers_are_json_safe() -> None:
    payload = components.quote_header_payload(
        Quote(symbol="X", name="X", price=math.nan, previous_close=1.0)
    )
    assert payload["price_value"] is None
    json.dumps(payload, allow_nan=False)


def test_range_bar() -> None:
    markup = components.range_bar_html("Day range", 225.0, 220.0, 230.0, QUOTE)
    assert "50% of range" in markup
    assert "left:50.0%" in markup
    assert "$220.00" in markup
    assert components.range_bar_html("Day range", None, 1.0, 2.0, QUOTE) == ""
    assert components.range_bar_html("Day range", 1.0, 2.0, 2.0, QUOTE) == ""


def test_stylesheet_survives_st_html_sanitising() -> None:
    # DOMPurify drops a <style> whose text contains "<" followed by a letter or "/".
    css = (UI / "styles.css").read_text(encoding="utf-8")
    assert not re.search(r"<[A-Za-z/!]", css)
    assert "light-dark(" in css
    assert "prefers-reduced-motion: reduce" in css


def test_rail_assets_ship_with_the_package() -> None:
    js = (UI / "rail.js").read_text(encoding="utf-8")
    assert "textContent" in js
    assert "innerHTML" not in js  # Yahoo-provided names never become markup
    assert "prefers-reduced-motion" in (UI / "rail.css").read_text(encoding="utf-8")


def test_a_change_that_prints_as_zero_is_flat() -> None:
    flat = Quote(symbol="X", name="X", currency="USD", price=100.0, previous_close=100.004)
    change = components.quote_header_payload(flat)["change"]
    assert change["direction"] == "flat"
    assert "-$0.00" not in change["text"]


def test_csv_keeps_full_precision_without_exponents() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {"Close": [81_499.46, 0.000123456], "Volume": [123_456_789.0, 3.45e12]},
        index=pd.Index(["a", "b"], name="Date"),
    )
    text = components.csv_bytes(frame).decode()
    assert "81499.46" in text
    assert "0.000123" in text
    assert "123456789" in text
    assert "3450000000000" in text
    assert "e+" not in text


def test_every_row_above_the_fold_has_a_fixed_height() -> None:
    """The top bar was the one unbounded row: a failed search-field chunk grew it
    40 -> 185 px and shifted the page (CLS 0.137). It now has a fixed slot."""
    import inspect

    from momentum import app

    source = inspect.getsource(app.main)
    assert "height=components.TOPBAR_HEIGHT" in source
    assert components.TOPBAR_HEIGHT >= 48  # room for the search field's 2 px focus ring


def test_narrow_phones_keep_one_top_bar_row_and_the_state_badge() -> None:
    payload = components.quote_header_payload(QUOTE)
    # Type and currency (and their dots) are optional, the exchange and state are not.
    assert payload["meta"] == [
        {"text": "NasdaqGS", "optional": False},
        {"text": "Equity", "optional": True},
        {"text": "USD", "optional": True},
    ]
    assert payload["state"]["label"] == "Market open"
    css = (UI / "quote.css").read_text(encoding="utf-8")
    assert re.search(r"max-width: 380px\)\s*\{[^}]*\.mm-opt\s*\{\s*display:\s*none", css)
