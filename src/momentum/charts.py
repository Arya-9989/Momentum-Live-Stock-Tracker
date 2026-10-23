"""Chart payloads for the Lightweight Charts component. Pure: DataFrames in, JSON out.

The browser side (``frontend/chart``, built into ``ui/chart/momentum-chart.js``)
draws exactly what these payloads describe:

* the price chart stacks its panels (price, volume, RSI, MACD) as panes on one
  time scale, so a single crosshair runs through all of them and one readout
  lists every series at the hovered bar, value first;
* the comparison chart draws cumulative % returns with a 0% baseline, each line
  labelled with its symbol and latest return on the price scale.

Times are exchange-local wall-clock times encoded as UTC seconds, so the axis
shows the exchange's hours for every viewer. Weekends, holidays and overnight
gaps take no space: the time scale is indexed by bar, not by calendar time.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal

import pandas as pd

from momentum.config import is_intraday
from momentum.theme import LIGHT, Theme, direction_color, overlay_color, with_alpha

ChartStyle = Literal["Line", "Candles"]
CHART_STYLES: Final[tuple[ChartStyle, ...]] = ("Line", "Candles")

AREA_TOP_ALPHA: Final = 0.24
PANEL_WEIGHTS: Final = {"price": 3.4, "volume": 0.8, "rsi": 1.0, "macd": 1.0}
PICK_LABEL: Final = "Set an alert at"
_OVERLAY_PREFIXES: Final = ("SMA", "EMA")

Json = dict[str, Any]


def trend_change(close: pd.Series, reference: float | None = None) -> float | None:
    """Change that decides the line colour: last close vs ``reference`` (the previous
    close on 1D, as Apple Stocks does) or vs the first close in the window."""
    values = close.dropna()
    if values.empty:
        return None
    base = reference if reference is not None else float(values.iloc[0])
    return float(values.iloc[-1]) - base


def panel_layout(frame: pd.DataFrame) -> list[str]:
    """Which panels to draw, top to bottom: price always; volume/RSI/MACD when data exists."""
    panels = ["price"]
    if "Volume" in frame and bool((frame["Volume"] > 0).any()):
        panels.append("volume")
    if any(c.startswith("RSI") for c in frame.columns):
        panels.append("rsi")
    if {"MACD", "Signal", "Histogram"}.issubset(frame.columns):
        panels.append("macd")
    return panels


def seconds(index: pd.Index) -> list[int]:
    """Naive exchange-local timestamps as seconds (read back as UTC by the chart)."""
    stamps = pd.DatetimeIndex(index)
    if stamps.tz is not None:
        stamps = stamps.tz_localize(None)
    return [int(ts.value // 1_000_000_000) for ts in stamps]


def _clean(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def points(times: list[int], values: Iterable[Any]) -> list[list[float | int | None]]:
    return [[t, _clean(v)] for t, v in zip(times, values, strict=True)]


def theme_tokens(theme: Theme) -> Json:
    return {
        "surface": theme.surface,
        "ink": theme.ink,
        "secondary": theme.secondary,
        "tertiary": theme.tertiary,
        "grid": theme.grid,
        "axis": theme.axis,
        "up": theme.up,
        "down": theme.down,
        "band": theme.band,
        "neutralBar": theme.neutral_bar,
        "hoverBg": theme.hover_bg,
        "hoverBorder": theme.hover_border,
    }


def price_format(prefix: str, suffix: str, decimals: int) -> Json:
    return {"kind": "price", "prefix": prefix, "suffix": suffix, "decimals": decimals}


def _line(  # noqa: PLR0917 - one series' fields, in a fixed order
    column: str, label: str, pane: int, color: str, values: list[list[Any]], fmt: Json, **extra: Any
) -> Json:
    return {
        "id": column,
        "label": label,
        "kind": "line",
        "pane": pane,
        "color": color,
        "points": values,
        "format": fmt,
        **extra,
    }


def price_chart(
    frame: pd.DataFrame,
    *,
    interval: str,
    style: ChartStyle = "Line",
    price_prefix: str = "",
    price_suffix: str = "",
    price_decimals: int = 2,
    reference: float | None = None,
    reference_label: str = "Previous close",
    height: int = 560,
    theme: Theme = LIGHT,
    key: str = "",
    alt: str = "",
    pickable: bool = False,
) -> Json:
    """The price chart's payload: price (area line or candles) with overlays, then
    volume, RSI and MACD panes when the frame has them.

    ``frame`` is the output of ``indicators.add_indicators``. ``key`` names what the
    chart shows (symbol, range, interval, style); when it changes, the series sweep
    in again. ``pickable`` offers "Set an alert at ..." after a click on the prices.
    """
    times = seconds(frame.index)
    fmt_price = price_format(price_prefix, price_suffix, price_decimals)
    panels = panel_layout(frame)
    pane = {name: i for i, name in enumerate(panels)}
    rsi_column = next((c for c in frame.columns if c.startswith("RSI")), None)
    rsi_label = rsi_column.replace(" ", " (") + ")" if rsi_column else ""
    labels = {
        "price": "",
        "volume": "Volume",
        "rsi": f"{rsi_label}  ·  30 / 70 band",
        "macd": "MACD (12, 26, 9)  ·  solid MACD, dotted signal",
    }
    payload: Json = {
        "key": key,
        "alt": alt,
        "height": height,
        "intraday": is_intraday(interval),
        "theme": theme_tokens(theme),
        "panes": [
            {"label": labels[name], "weight": PANEL_WEIGHTS[name]}
            | ({"fixedRange": [0, 100]} if name == "rsi" else {})
            for name in panels
        ],
        "series": [],
        "bands": [],
    }
    series: list[Json] = payload["series"]
    if style == "Candles":
        bars = frame[["Open", "High", "Low", "Close"]].dropna()
        payload["candles"] = {
            "label": "Close",
            "format": fmt_price,
            "bars": [
                [t, float(o), float(h), float(lo), float(c)]
                for t, (o, h, lo, c) in zip(seconds(bars.index), bars.to_numpy(), strict=True)
            ],
        }
    else:
        color = direction_color(theme, trend_change(frame["Close"], reference))
        series.append(
            {
                "id": "close",
                "label": "Close",
                "kind": "area",
                "pane": 0,
                "color": color,
                "areaTop": with_alpha(color, AREA_TOP_ALPHA),
                "points": points(times, frame["Close"]),
                "format": fmt_price,
                "lastValue": True,
            }
        )
    if {"BB Upper", "BB Lower"}.issubset(frame.columns):
        for column, label in (("BB Upper", "Bollinger upper"), ("BB Lower", "Bollinger lower")):
            values = points(times, frame[column])
            series.append(_line(column, label, 0, theme.tertiary, values, fmt_price, width=1))
        payload["bands"].append(
            {"pane": 0, "color": theme.band, "upper": "BB Upper", "lower": "BB Lower"}
        )
    for column in (c for c in frame.columns if c.startswith(_OVERLAY_PREFIXES)):
        values = points(times, frame[column])
        color = overlay_color(theme, column)
        series.append(_line(column, column, 0, color, values, fmt_price, width=1.5))
    if "volume" in pane:
        series.append(
            {
                "id": "volume",
                "label": "Volume",
                "kind": "histogram",
                "pane": pane["volume"],
                "color": theme.neutral_bar,
                "points": points(times, frame["Volume"]),
                "format": {"kind": "compact", "decimals": 2},
            }
        )
    if "rsi" in pane and rsi_column:
        values = points(times, frame[rsi_column])
        rsi_format = {"kind": "plain", "decimals": 1}
        series.append(
            _line("rsi", rsi_label, pane["rsi"], theme.secondary, values, rsi_format, width=1.5)
        )
        payload["bands"].append({"pane": pane["rsi"], "color": theme.band, "from": 70, "to": 30})
    if "macd" in pane:
        macd_format = {"kind": "plain", "decimals": max(price_decimals, 2)}
        series.append(
            {
                "id": "macd_hist",
                "label": "Histogram",
                "kind": "histogram",
                "pane": pane["macd"],
                "color": theme.neutral_bar,
                "points": points(times, frame["Histogram"]),
                "format": macd_format,
            }
        )
        macd, signal = points(times, frame["MACD"]), points(times, frame["Signal"])
        series.append(_line("macd", "MACD", pane["macd"], theme.ink, macd, macd_format, width=1.5))
        series.append(
            _line(
                "signal",
                "Signal",
                pane["macd"],
                theme.secondary,
                signal,
                macd_format,
                width=1.5,
                dashed=True,
            )
        )
    if reference is not None and math.isfinite(reference):
        payload["reference"] = {"price": reference, "label": reference_label}
    if pickable:
        payload["pick"] = {"label": PICK_LABEL}
    return payload


def assign_series_slots(
    symbols: Iterable[str], previous: Mapping[str, int] | None = None, slots: int = 6
) -> dict[str, int]:
    """Give each symbol a stable colour slot.

    Symbols keep the slot they already had; new ones take the lowest free slot.
    Removing a symbol therefore never repaints the others.
    """
    previous = previous or {}
    ordered = list(dict.fromkeys(symbols))
    result = {s: previous[s] for s in ordered if s in previous and previous[s] < slots}
    used = set(result.values())
    for symbol in ordered:
        if symbol in result:
            continue
        free = next((i for i in range(slots) if i not in used), len(used) % slots)
        result[symbol] = free
        used.add(free)
    return {s: result[s] for s in ordered}


def comparison_chart(
    normalized: pd.DataFrame,
    *,
    height: int = 460,
    theme: Theme = LIGHT,
    slots: Mapping[str, int] | None = None,
    key: str = "",
    alt: str = "",
) -> Json:
    """Cumulative % return lines, one per symbol, with a 0% baseline.

    Each line shows its symbol and latest return on the price scale (the labels
    are spaced so they never overlap); the readout lists every symbol.
    """
    slots = slots or assign_series_slots(map(str, normalized.columns))
    times = seconds(normalized.index)
    percent = {"kind": "percent", "decimals": 2, "signed": True}
    series = [
        _line(
            str(column),
            str(column),
            0,
            theme.series[slots.get(str(column), 0) % len(theme.series)],
            points(times, normalized[column]),
            percent,
            width=2,
            title=str(column),
            lastValue=True,
        )
        for column in normalized.columns
    ]
    return {
        "key": key,
        "alt": alt,
        "height": height,
        "intraday": False,
        "theme": theme_tokens(theme),
        "panes": [{"label": "", "weight": 1}],
        "series": series,
        "bands": [],
        "zeroLine": True,
    }
