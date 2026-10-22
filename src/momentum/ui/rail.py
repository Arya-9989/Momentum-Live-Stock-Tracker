"""Pure model for the watchlist rail (no Streamlit calls).

The rail is a calm list in the style of Apple Stocks: symbol and name, a
neutral 1-month sparkline, the last price and a day-change badge. Only the
badge carries market green/red, because it is the only price-state element.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Final, Literal

import pandas as pd

from momentum import formatting as fmt
from momentum.data import QuoteResult

Direction = Literal["up", "down", "flat"]

ROW_HEIGHT: Final = 60  # px; >= 44 px touch target, matches rail.css
SPARK_WIDTH: Final = 56
SPARK_HEIGHT: Final = 24


@dataclass(frozen=True, slots=True)
class RailRow:
    symbol: str
    name: str
    price: str
    change: str
    direction: Direction
    spark: str
    label: str
    selected: bool


def direction_of(change: float | None) -> Direction:
    if change is None or math.isnan(change) or change == 0:
        return "flat"
    return "up" if change > 0 else "down"


def sparkline_path(
    values: Sequence[float], width: int = SPARK_WIDTH, height: int = SPARK_HEIGHT, pad: float = 2.0
) -> str:
    """SVG path ``d`` for a polyline scaled into ``width`` x ``height`` (y grows down).

    Returns ``""`` when there are fewer than two finite points. A flat series is
    drawn through the vertical middle.
    """
    points = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(points) < 2:
        return ""
    low, high = min(points), max(points)
    span = high - low
    inner_h = height - 2 * pad
    step = (width - 2 * pad) / (len(points) - 1)
    coords = []
    for i, value in enumerate(points):
        x = pad + i * step
        y = height / 2 if span == 0 else pad + (high - value) / span * inner_h
        coords.append(f"{x:.1f},{y:.1f}")
    return "M" + " L".join(coords)


def _spoken_change(percent: float | None) -> str:
    if percent is None or math.isnan(percent):
        return "change unavailable"
    if percent == 0:
        return "unchanged today"
    word = "up" if percent > 0 else "down"
    return f"{word} {abs(percent):.2f}% today"


def rail_rows(
    results: Sequence[QuoteResult], closes: pd.DataFrame, selected: str | None
) -> list[RailRow]:
    """One row per watchlist symbol, in watchlist order; failed quotes keep their slot."""
    rows: list[RailRow] = []
    for result in results:
        quote = result.quote
        trend = closes[result.symbol].dropna().tolist() if result.symbol in closes else []
        if quote is None:
            rows.append(
                RailRow(
                    symbol=result.symbol,
                    name=result.error or "Unavailable",
                    price=fmt.PLACEHOLDER,
                    change=fmt.PLACEHOLDER,
                    direction="flat",
                    spark=sparkline_path(trend),
                    label=f"{result.symbol}, unavailable",
                    selected=result.symbol == selected,
                )
            )
            continue
        percent = quote.change_percent
        price = fmt.format_price(quote.price, quote.currency, quote.quote_type)
        rows.append(
            RailRow(
                symbol=quote.symbol,
                name=quote.name,
                price=price,
                change=fmt.format_percent(percent),
                direction=direction_of(percent),
                spark=sparkline_path(trend),
                label=f"{quote.symbol}, {quote.name}, {price}, {_spoken_change(percent)}",
                selected=quote.symbol == selected,
            )
        )
    return rows


def rail_payload(rows: Sequence[RailRow]) -> dict[str, list[dict[str, object]]]:
    """JSON-serialisable data for the rail component."""
    return {"rows": [asdict(row) for row in rows]}


def rail_height(count: int) -> int:
    """Exact component height, reserved up front so the rail never shifts the layout."""
    return max(count, 1) * ROW_HEIGHT
