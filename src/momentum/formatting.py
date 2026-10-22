"""Pure display helpers for prices, percentages and large numbers.

All functions accept ``None``/NaN and return a placeholder instead of raising,
because Yahoo routinely omits fields (an index has no P/E, a fund no market cap).
"""

from __future__ import annotations

import math
from typing import Final

PLACEHOLDER: Final = "—"

CURRENCY_SYMBOLS: Final = {
    "USD": "$",
    "CAD": "C$",
    "AUD": "A$",
    "NZD": "NZ$",
    "HKD": "HK$",
    "SGD": "S$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "JPY": "¥",
    "CNY": "CN¥",  # plain ¥ is the yen
    "KRW": "₩",
    "CHF": "CHF ",
    "BRL": "R$",
    "ZAR": "R",
    "ILS": "₪",
    "TWD": "NT$",
}

# Yahoo quotes London, Johannesburg and Tel Aviv listings in minor units (pence,
# cents, agorot). Prices are shown in those units, as the exchanges do ("120.50p");
# market cap is reported by Yahoo in the major currency.
MINOR_UNIT_CURRENCIES: Final = {
    "GBp": ("GBP", 100),
    "GBX": ("GBP", 100),
    "ZAc": ("ZAR", 100),
    "ILA": ("ILS", 100),
}
MINOR_UNIT_SUFFIXES: Final = {"GBp": "p", "GBX": "p", "ZAc": "c", "ILA": " ag"}

# Index levels are points and exchange rates are ratios, not money, even though
# Yahoo attaches a currency to both.
INDEX_QUOTE_TYPES: Final = frozenset({"INDEX"})
RATE_QUOTE_TYPES: Final = frozenset({"CURRENCY"})
UNITLESS_QUOTE_TYPES: Final = INDEX_QUOTE_TYPES | RATE_QUOTE_TYPES

_COMPACT_SUFFIXES: Final = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def is_missing(value: float | int | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def currency_symbol(currency: str | None) -> str:
    """Prefix for a currency code; unknown codes render as ``"XYZ "``."""
    if not currency:
        return ""
    if currency in MINOR_UNIT_CURRENCIES:
        currency = MINOR_UNIT_CURRENCIES[currency][0]
    return CURRENCY_SYMBOLS.get(currency.upper(), f"{currency.upper()} ")


def _decimals_for(value: float) -> int:
    magnitude = abs(value)
    if magnitude >= 10_000:
        return 0  # $108,250 reads better than $108,250.37 and fits in a KPI card
    if magnitude >= 1 or magnitude == 0:
        return 2
    if magnitude >= 0.01:
        return 4
    return 6


def _rate_decimals(value: float) -> int:
    """FX convention: four places for rates like EUR/USD 1.1222, two for USD/JPY 157.07."""
    return 4 if abs(value) < 10 else 2


def is_unitless(quote_type: str | None) -> bool:
    """Index levels and FX rates carry no currency symbol."""
    return quote_type in UNITLESS_QUOTE_TYPES


def price_decimals(value: float | None, quote_type: str | None = None) -> int:
    """Decimals used for a price of this size, matching ``format_price``."""
    if value is None or math.isnan(value) or quote_type in INDEX_QUOTE_TYPES:
        return 2
    if quote_type in RATE_QUOTE_TYPES:
        return _rate_decimals(value)
    return _decimals_for(value)


def rounds_to_zero(value: float, decimals: int) -> bool:
    """True when ``value`` would print as zero at ``decimals`` places (no "-0.00")."""
    return round(abs(value), decimals) == 0


def format_number(value: float | None, decimals: int = 2) -> str:
    if value is None or math.isnan(value):
        return PLACEHOLDER
    return f"{value:,.{decimals}f}"


def format_price(
    value: float | None,
    currency: str | None = None,
    quote_type: str | None = None,
    *,
    decimals: int | None = None,
) -> str:
    """Format a price with its currency, e.g. ``$189.32``, ``₹3,412.10``, ``120.50p``.

    Index levels and FX rates are shown without a currency symbol. Sub-unit prices
    (crypto, penny stocks) keep enough decimals to stay meaningful. ``decimals``
    overrides the size-based choice (used to give a change the precision of
    its price).
    """
    if value is None or math.isnan(value):
        return PLACEHOLDER
    places = price_decimals(value, quote_type) if decimals is None else decimals
    if rounds_to_zero(value, places):
        value = 0.0
    if quote_type in UNITLESS_QUOTE_TYPES:
        return format_number(value, places)
    sign = "-" if value < 0 else ""
    amount = f"{abs(value):,.{places}f}"
    if currency in MINOR_UNIT_SUFFIXES:
        return f"{sign}{amount}{MINOR_UNIT_SUFFIXES[currency]}"
    return f"{sign}{currency_symbol(currency)}{amount}"


def format_change(
    value: float | None,
    currency: str | None = None,
    quote_type: str | None = None,
    *,
    decimals: int | None = None,
) -> str:
    """Signed price change, e.g. ``+$1.20`` / ``-₹4.50``.

    Pass the price's ``decimals`` so a small move on a large price reads
    ``+$0.51`` rather than ``+$0.5077``.
    """
    if value is None or math.isnan(value):
        return PLACEHOLDER
    places = price_decimals(abs(value), quote_type) if decimals is None else decimals
    if rounds_to_zero(value, places):
        return format_price(0.0, currency, quote_type, decimals=places)
    sign = "+" if value > 0 else "-"
    return f"{sign}{format_price(abs(value), currency, quote_type, decimals=places)}"


def format_percent(value: float | None, decimals: int = 2, *, signed: bool = True) -> str:
    """Format a value already expressed in percent, e.g. ``1.234 -> +1.23%``."""
    if value is None or math.isnan(value):
        return PLACEHOLDER
    if rounds_to_zero(value, decimals):
        value = 0.0
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:,.{decimals}f}%"


def format_compact(value: float | None, decimals: int = 2) -> str:
    """Human-readable large numbers: ``3_450_000_000_000 -> 3.45T``."""
    if value is None or math.isnan(value):
        return PLACEHOLDER
    magnitude = abs(value)
    for threshold, suffix in _COMPACT_SUFFIXES:
        if magnitude >= threshold:
            return f"{value / threshold:,.{decimals}f}{suffix}"
    return f"{value:,.0f}"


def format_market_cap(value: float | None, currency: str | None = None) -> str:
    """Compact market cap with currency, e.g. ``$3.45T``.

    Yahoo reports market cap in the major currency even when the price is in
    pence, so only the symbol is normalised here.
    """
    if value is None or math.isnan(value) or value <= 0:
        return PLACEHOLDER
    major = MINOR_UNIT_CURRENCIES[currency][0] if currency in MINOR_UNIT_CURRENCIES else currency
    return f"{currency_symbol(major)}{format_compact(value)}"


def format_ratio(value: float | None, decimals: int = 2) -> str:
    """Valuation ratios such as P/E; negative or missing values are not meaningful."""
    if value is None or math.isnan(value) or value <= 0:
        return PLACEHOLDER
    return f"{value:,.{decimals}f}"


def format_range(
    low: float | None,
    high: float | None,
    currency: str | None = None,
    quote_type: str | None = None,
) -> str:
    if is_missing(low) or is_missing(high):
        return PLACEHOLDER
    return f"{format_price(low, currency, quote_type)} – {format_price(high, currency, quote_type)}"


def range_position(value: float | None, low: float | None, high: float | None) -> float | None:
    """Where ``value`` sits inside ``[low, high]`` as a 0-1 fraction (clamped)."""
    if value is None or low is None or high is None:
        return None
    if any(math.isnan(v) for v in (value, low, high)) or high <= low:
        return None
    return min(1.0, max(0.0, (value - low) / (high - low)))


def format_money(value: float | None, currency: str | None, *, signed: bool = False) -> str:
    """Money in a major currency, always to the cent: ``$12,345.60``, ``-₹1,200.00``.

    Unlike ``format_price`` (whose decimals follow the size of a quote), account
    values keep two places, so a column of totals lines up.
    """
    if value is None or math.isnan(value):
        return PLACEHOLDER
    if rounds_to_zero(value, 2):
        value = 0.0
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}{currency_symbol(currency)}{abs(value):,.2f}"


def format_quantity(value: float | None) -> str:
    """Share counts: whole numbers plainly, fractions to at most 8 places (``0.0125``)."""
    if value is None or math.isnan(value):
        return PLACEHOLDER
    if float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.8f}".rstrip("0").rstrip(".")
