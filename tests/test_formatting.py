from __future__ import annotations

import math

import pytest

from momentum import formatting as fmt

NAN = math.nan


@pytest.mark.parametrize(
    ("value", "currency", "quote_type", "expected"),
    [
        (189.3249, "USD", "EQUITY", "$189.32"),
        (3412.1, "INR", "EQUITY", "₹3,412.10"),
        (98.5, "EUR", None, "€98.50"),
        (1234.5, "GBp", "EQUITY", "1,234.50p"),  # London quotes stay in pence
        (-2.5, "ZAc", "EQUITY", "-2.50c"),
        (1500.0, "JPY", None, "¥1,500.00"),
        (12.0, "SEK", None, "SEK 12.00"),  # unknown code falls back to the ISO code
        (12.0, None, None, "12.00"),
        (0.4567, "USD", "CRYPTOCURRENCY", "$0.4567"),
        (0.00001234, "USD", "CRYPTOCURRENCY", "$0.000012"),
        (6650.123, "USD", "INDEX", "6,650.12"),  # index levels are points, not dollars
        (-3.5, "USD", None, "-$3.50"),
        (108_250.37, "USD", "CRYPTOCURRENCY", "$108,250"),
        (25_300.45, "INR", "INDEX", "25,300.45"),  # index levels keep 2 dp
        (None, "USD", None, "—"),
        (NAN, "USD", None, "—"),
    ],
)
def test_format_price(
    value: float | None, currency: str | None, quote_type: str | None, expected: str
) -> None:
    assert fmt.format_price(value, currency, quote_type) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.2, "+$1.20"), (-4.5, "-$4.50"), (0.0, "$0.00"), (None, "—")],
)
def test_format_change(value: float | None, expected: str) -> None:
    assert fmt.format_change(value, "USD") == expected


def test_format_change_in_pence() -> None:
    assert fmt.format_change(1.0, "GBp") == "+1.00p"


def test_format_change_for_index_has_no_currency() -> None:
    assert fmt.format_change(-12.5, "USD", "INDEX") == "-12.50"


@pytest.mark.parametrize(
    ("value", "kwargs", "expected"),
    [
        (1.234, {}, "+1.23%"),
        (-0.5, {}, "-0.50%"),
        (0.0, {}, "0.00%"),
        (12.0, {"signed": False}, "12.00%"),
        (3.14159, {"decimals": 1}, "+3.1%"),
        (None, {}, "—"),
    ],
)
def test_format_percent(value: float | None, kwargs: dict[str, object], expected: str) -> None:
    assert fmt.format_percent(value, **kwargs) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3_450_000_000_000, "3.45T"),
        (987_600_000_000, "987.60B"),
        (12_340_000, "12.34M"),
        (5_600, "5.60K"),
        (950, "950"),
        (-2_000_000_000, "-2.00B"),
        (None, "—"),
        (NAN, "—"),
    ],
)
def test_format_compact(value: float | None, expected: str) -> None:
    assert fmt.format_compact(value) == expected


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        (3.45e12, "USD", "$3.45T"),
        (4.95e12, "INR", "₹4.95T"),
        (2.05e10, "GBp", "£20.50B"),  # market cap is already in pounds
        (0, "USD", "—"),
        (None, "USD", "—"),
    ],
)
def test_format_market_cap(value: float | None, currency: str, expected: str) -> None:
    assert fmt.format_market_cap(value, currency) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(28.456, "28.46"), (-5.0, "—"), (0.0, "—"), (None, "—")]
)
def test_format_ratio(value: float | None, expected: str) -> None:
    assert fmt.format_ratio(value) == expected


def test_format_range() -> None:
    assert fmt.format_range(100, 120.5, "USD") == "$100.00 – $120.50"
    assert fmt.format_range(None, 120.5, "USD") == "—"


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (150, 100, 200, 0.5),
        (90, 100, 200, 0.0),  # clamped below
        (250, 100, 200, 1.0),  # clamped above
        (150, 200, 200, None),  # degenerate range
        (None, 100, 200, None),
        (NAN, 100, 200, None),
    ],
)
def test_range_position(
    value: float | None, low: float, high: float, expected: float | None
) -> None:
    assert fmt.range_position(value, low, high) == expected


def test_currency_symbol() -> None:
    assert fmt.currency_symbol("usd") == "$"
    assert fmt.currency_symbol("GBp") == "£"
    assert fmt.currency_symbol(None) == ""
