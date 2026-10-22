"""Plotly figure builders. Pure functions of DataFrames: no Streamlit, no I/O."""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from momentum.config import PALETTE, is_intraday

ChartStyle = Literal["Candles", "Line"]

_FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
_GRID = "rgba(148, 163, 184, 0.18)"


def compute_rangebreaks(index: pd.DatetimeIndex, interval: str) -> list[dict[str, Any]]:
    """Hide non-trading time so candles sit next to each other.

    * Weekends are hidden unless the instrument trades on them (crypto).
    * Missing weekdays (exchange holidays) are hidden for daily and intraday bars.
    * For intraday bars, overnight hours outside the observed session are hidden.
    """
    if len(index) < 2 or (interval not in {"1d"} and not is_intraday(interval)):
        return []
    breaks: list[dict[str, Any]] = []
    trades_weekends = bool((index.dayofweek >= 5).any())
    if not trades_weekends:
        breaks.append({"bounds": ["sat", "mon"]})
        observed = set(index.normalize())
        weekdays = pd.bdate_range(index.min().normalize(), index.max().normalize())
        holidays = [day for day in weekdays if day not in observed]
        if holidays:
            breaks.append({"values": [day.strftime("%Y-%m-%d") for day in holidays]})

    if is_intraday(interval):
        hours = index.hour + index.minute / 60.0
        step = pd.Series(index).diff().dropna()
        bar_hours = float(step.min().total_seconds() / 3600) if not step.empty else 0.0
        session_open = float(hours.min())
        session_close = float(hours.max()) + bar_hours
        if 0 < session_open < session_close < 24:
            breaks.append({"pattern": "hour", "bounds": [session_close, session_open]})
    return breaks


def _base_layout(fig: go.Figure, height: int) -> None:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
        font={"family": _FONT, "size": 12},
        hovermode="x unified",
        dragmode="zoom",
        bargap=0.15,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.0,
            "xanchor": "left",
            "x": 0,
            "bgcolor": "rgba(0,0,0,0)",
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(
        showgrid=False,
        rangeslider_visible=False,
        showspikes=True,
        spikemode="across",
        spikethickness=1,
        spikedash="dot",
        spikecolor=PALETTE.muted,
    )
    fig.update_yaxes(gridcolor=_GRID, zeroline=False, side="right", tickformat=",")


def _panel_layout(frame: pd.DataFrame) -> dict[str, int]:
    """Which panels to draw (price always; volume/RSI/MACD when data exists) and their rows."""
    panels = ["price"]
    if "Volume" in frame and bool((frame["Volume"] > 0).any()):
        panels.append("volume")
    if any(c.startswith("RSI") for c in frame.columns):
        panels.append("rsi")
    if {"MACD", "Signal", "Histogram"}.issubset(frame.columns):
        panels.append("macd")
    return {name: i + 1 for i, name in enumerate(panels)}


_PANEL_WEIGHTS = {"price": 3.2, "volume": 0.8, "rsi": 1.0, "macd": 1.0}


def _add_price(fig: go.Figure, frame: pd.DataFrame, style: ChartStyle) -> None:
    x = frame.index
    if style == "Candles":
        fig.add_trace(
            go.Candlestick(
                x=x,
                open=frame["Open"],
                high=frame["High"],
                low=frame["Low"],
                close=frame["Close"],
                name="Price",
                increasing={"line": {"color": PALETTE.up, "width": 1}, "fillcolor": PALETTE.up},
                decreasing={"line": {"color": PALETTE.down, "width": 1}, "fillcolor": PALETTE.down},
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=frame["Close"],
                name="Close",
                mode="lines",
                line={"color": PALETTE.accent, "width": 2},
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    if {"BB Upper", "BB Lower"}.issubset(frame.columns):
        band_line = {"color": PALETTE.muted, "width": 1, "dash": "dot"}
        fig.add_trace(
            go.Scatter(
                x=x,
                y=frame["BB Upper"],
                name="BB upper",
                mode="lines",
                line=band_line,
                legendgroup="bb",
                showlegend=False,
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=x,
                y=frame["BB Lower"],
                name="Bollinger (20, 2)",
                mode="lines",
                line=band_line,
                fill="tonexty",
                fillcolor=PALETTE.band,
                legendgroup="bb",
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )

    overlays = [c for c in frame.columns if c.startswith(("SMA", "EMA"))]
    for i, column in enumerate(overlays):
        color = PALETTE.overlays[i % len(PALETTE.overlays)]
        fig.add_trace(
            go.Scatter(
                x=x, y=frame[column], name=column, mode="lines", line={"color": color, "width": 1.4}
            ),
            row=1,
            col=1,
        )


def _add_volume(fig: go.Figure, frame: pd.DataFrame, row: int) -> None:
    up = frame["Close"] >= frame["Open"]
    fig.add_trace(
        go.Bar(
            x=frame.index,
            y=frame["Volume"],
            name="Volume",
            marker_color=[PALETTE.up if is_up else PALETTE.down for is_up in up],
            marker_line_width=0,
            opacity=0.5,
            showlegend=False,
            hovertemplate="%{y:.3s}<extra>Volume</extra>",
        ),
        row=row,
        col=1,
    )
    fig.update_yaxes(tickformat="~s", nticks=3, row=row, col=1)


def _add_rsi(fig: go.Figure, frame: pd.DataFrame, row: int) -> None:
    column = next(c for c in frame.columns if c.startswith("RSI"))
    fig.add_hrect(y0=30, y1=70, fillcolor=PALETTE.band, line_width=0, row=row, col=1)
    for level in (30, 70):
        fig.add_hline(
            y=level, line={"color": PALETTE.muted, "width": 1, "dash": "dot"}, row=row, col=1
        )
    fig.add_trace(
        go.Scatter(
            x=frame.index,
            y=frame[column],
            name=column,
            mode="lines",
            line={"color": PALETTE.series[3], "width": 1.4},
        ),
        row=row,
        col=1,
    )
    fig.update_yaxes(range=[0, 100], tickvals=[30, 70], row=row, col=1)


def _add_macd(fig: go.Figure, frame: pd.DataFrame, row: int) -> None:
    hist = frame["Histogram"].fillna(0.0)
    fig.add_trace(
        go.Bar(
            x=frame.index,
            y=frame["Histogram"],
            name="MACD hist",
            marker_color=[PALETTE.up if v >= 0 else PALETTE.down for v in hist],
            marker_line_width=0,
            opacity=0.55,
            showlegend=False,
        ),
        row=row,
        col=1,
    )
    for column, color, width in (
        ("MACD", PALETTE.series[1], 1.4),
        ("Signal", PALETTE.series[2], 1.2),
    ):
        fig.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame[column],
                name=column,
                mode="lines",
                line={"color": color, "width": width},
            ),
            row=row,
            col=1,
        )
    fig.update_yaxes(nticks=4, tickformat=".3~s", row=row, col=1)


def price_chart(
    frame: pd.DataFrame,
    *,
    interval: str,
    style: ChartStyle = "Candles",
    price_prefix: str = "",
    price_suffix: str = "",
    height: int = 640,
) -> go.Figure:
    """Price panel with overlays, plus volume / RSI / MACD panels when present.

    ``frame`` is the output of ``indicators.add_indicators``: OHLCV columns and
    any of ``SMA n``, ``EMA n``, ``BB *``, ``RSI n``, ``MACD``/``Signal``/``Histogram``.
    """
    rows = _panel_layout(frame)
    weights = [_PANEL_WEIGHTS[name] for name in rows]
    fig = make_subplots(
        rows=len(rows),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[w / sum(weights) for w in weights],
    )
    _base_layout(fig, height)  # first, so panel-specific axis formats below win
    _add_price(fig, frame, style)
    if "volume" in rows:
        _add_volume(fig, frame, rows["volume"])
    if "rsi" in rows:
        _add_rsi(fig, frame, rows["rsi"])
    if "macd" in rows:
        _add_macd(fig, frame, rows["macd"])

    fig.update_yaxes(tickprefix=price_prefix, ticksuffix=price_suffix, row=1, col=1)
    breaks = compute_rangebreaks(pd.DatetimeIndex(frame.index), interval)
    if breaks:
        fig.update_xaxes(rangebreaks=breaks)
    fig.update_xaxes(hoverformat="%b %d, %H:%M" if is_intraday(interval) else "%b %d, %Y")
    return fig


def comparison_chart(normalized: pd.DataFrame, *, height: int = 460) -> go.Figure:
    """Cumulative % return lines, one per symbol, with a 0% baseline."""
    fig = go.Figure()
    for i, column in enumerate(normalized.columns):
        fig.add_trace(
            go.Scatter(
                x=normalized.index,
                y=normalized[column],
                name=str(column),
                mode="lines",
                connectgaps=True,
                line={"color": PALETTE.series[i % len(PALETTE.series)], "width": 2},
                hovertemplate="%{y:+.2f}%",
            )
        )
    fig.add_hline(y=0, line={"color": PALETTE.muted, "width": 1, "dash": "dot"})
    _base_layout(fig, height)
    fig.update_yaxes(ticksuffix="%", tickformat="+.0f")
    fig.update_xaxes(hoverformat="%b %d, %Y")
    return fig
