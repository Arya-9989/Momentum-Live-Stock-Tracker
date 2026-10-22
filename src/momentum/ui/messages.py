"""User-facing copy for empty and error states (pure, so it is unit-tested).

Each failure gets a short declarative title and one line of next steps, in the
same voice as the rest of the interface. Raw exception text stays available as
``str(err)`` for logs and tooltips but is not the headline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from momentum.data import (
    DataError,
    DataSourceError,
    EmptyRangeError,
    InvalidSymbolError,
    QuoteResult,
    RateLimitError,
)

EXAMPLES = "AAPL, LT.NS (NSE), VOD.L (London), ^GSPC (index) or BTC-USD"


@dataclass(frozen=True, slots=True)
class Copy:
    title: str
    detail: str


OFFLINE = Copy(
    "Can't reach Yahoo Finance.",
    "Check your connection and try again, or turn on Demo data to keep exploring offline.",
)
RATE_LIMITED = Copy(
    "Yahoo Finance is busy.",
    "It is limiting requests from this server. Try again in a minute, or turn on Demo data.",
)


def describe(exc: DataError, symbol: str) -> Copy:
    """Title and next step for a failure loading ``symbol`` (as the viewer typed it)."""
    shown = symbol.strip().upper()
    if isinstance(exc, RateLimitError):
        return RATE_LIMITED
    if isinstance(exc, DataSourceError):
        return OFFLINE
    if isinstance(exc, InvalidSymbolError):
        if not shown:
            return Copy("Search for a symbol.", f"For example {EXAMPLES}.")
        return Copy(
            f"“{symbol.strip()}” isn't a ticker symbol.",
            f"Symbols use letters, digits and . - = ^, for example {EXAMPLES}.",
        )
    if isinstance(exc, EmptyRangeError):
        return Copy(
            f"No prices for {shown} in this range.",
            "It may be delisted, or its market has not traded in this window yet. "
            "Try a longer range.",
        )
    return Copy(
        f"No data for {shown}.",
        "Yahoo Finance doesn't recognise it. Listings outside the US need a suffix, such as "
        "LT.NS (NSE) or VOD.L (London); indices start with a caret (^GSPC).",
    )


def chart_note(exc: DataError) -> Copy:
    """Quiet copy for the empty chart panel under an error header (no repetition)."""
    if isinstance(exc, DataSourceError):
        return Copy("No chart yet.", "It will appear as soon as Yahoo Finance answers.")
    return Copy("No chart to show.", "Pick a symbol from your watchlist, or search for another.")


def batch_failure(errors: Mapping[str, str], transient: frozenset[str]) -> Copy:
    """Copy for a batch (Compare, Watchlist, rail) in which every symbol failed."""
    if transient:
        message = next(errors[s] for s in errors if s in transient)
        return RATE_LIMITED if "rate-limit" in message else OFFLINE
    return Copy(
        "None of these symbols has data.",
        "Check the symbols: listings outside the US need a suffix, such as LT.NS or VOD.L.",
    )


def batch_from_results(results: Sequence[QuoteResult]) -> Copy | None:
    """``batch_failure`` for quote results, or None when at least one quote loaded."""
    if not results or any(r.quote is not None for r in results):
        return None
    errors = {r.symbol: r.error or "" for r in results}
    return batch_failure(errors, frozenset(r.symbol for r in results if r.transient))


def not_shown(errors: Mapping[str, str], transient: frozenset[str]) -> str:
    """One line naming the symbols left out of a partly failed batch."""
    parts = [
        f"{symbol} ({'Yahoo Finance unreachable' if symbol in transient else 'no data'})"
        for symbol in errors
    ]
    return "Not shown: " + ", ".join(parts) + "."
