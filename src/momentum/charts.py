"""Plotly figure builders. Pure functions of DataFrames: no Streamlit, no I/O.

Every figure is drawn with a per-theme template (``plotly_template``) so the
light and dark charts are designed separately rather than inverted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from momentum.config import is_intraday
from momentum.theme import LIGHT, Theme, direction_color, overlay_color, with_alpha

ChartStyle = Literal["Line", "Candles"]
CHART_STYLES: Final[tuple[ChartStyle, ...]] = ("Line", "Candles")

FONT_FAMILY: Final = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
AREA_TOP_ALPHA: Final = 0.24
_Y_PADDING: Final = 0.06  # share of the price range left free above and below the line


def plotly_template(theme: Theme) -> go.layout.Template:
    """Typography, hairline grid, crosshair and hover-label styling for one theme."""
    axis = {
        "showline": False,
        "zeroline": False,
        "ticks": "",
        "tickfont": {"color": theme.secondary, "size": 12},
        "title": {"font": {"color": theme.secondary, "size": 12}},
        "gridcolor": theme.grid,
        "gridwidth": 1,
        "griddash": "solid",
        "automargin": True,
    }
    return go.layout.Template(
        layout={
            "font": {"family": FONT_FAMILY, "size": 12, "color": theme.ink},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "colorway": list(theme.series),
            "hovermode": "x unified",
            "hoverlabel": {
                "bgcolor": theme.hover_bg,
                "bordercolor": theme.hover_border,
                "font": {"family": FONT_FAMILY, "size": 13, "color": theme.ink},
                "align": "left",
                "namelength": -1,
            },
            "legend": {
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.0,
                "xanchor": "left",
                "x": 0,
                "bgcolor": "rgba(0,0,0,0)",
                "font": {"color": theme.secondary, "size": 12},
                "itemclick": "toggle",
            },
            "xaxis": {
                **axis,
                "showgrid": False,
                "showspikes": True,
                "spikemode": "across",
                "spikesnap": "data",
                "spikethickness": 1,
                "spikedash": "solid",
                "spikecolor": theme.axis,
            },
            "yaxis": {**axis, "showgrid": True, "side": "right"},
            "bargap": 0.2,
        }
    )


def compute_rangebreaks(index: pd.DatetimeIndex, interval: str) -> list[dict[str, Any]]:
    """Hide non-trading time so bars sit next to each other.

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


def padded_range(
    values: Iterable[float], padding: float = _Y_PADDING
) -> tuple[float, float] | None:
    """``(low, high)`` around the finite values with ``padding`` of the span on each side.

    Needed because the area fill runs to zero: without an explicit range Plotly
    would stretch the price axis down to 0 and flatten the line.
    """
    finite = [float(v) for v in values if pd.notna(v)]
    if not finite:
        return None
    low, high = min(finite), max(finite)
    span = high - low
    if span == 0:
        span = abs(high) * 0.02 or 1.0
    return low - span * padding, high + span * padding


def trend_change(close: pd.Series, reference: float | None = None) -> float | None:
    """Change that decides the line colour: last close vs ``reference`` (the previous
    close on 1D, as Apple Stocks does) or vs the first close in the window."""
    values = close.dropna()
    if values.empty:
        return None
    base = reference if reference is not None else float(values.iloc[0])
    return float(values.iloc[-1]) - base


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


_PANEL_WEIGHTS = {"price": 3.4, "volume": 0.8, "rsi": 1.0, "macd": 1.0}


def _price_hover(prefix: str, suffix: str, decimals: int) -> str:
    return f"{prefix}%{{y:,.{decimals}f}}{suffix}"


def _add_price(
    fig: go.Figure,
    frame: pd.DataFrame,
    style: ChartStyle,
    theme: Theme,
    *,
    hover: str,
    reference: float | None,
    reference_label: str,
) -> None:
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
                increasing={"line": {"color": theme.up, "width": 1}, "fillcolor": theme.up},
                decreasing={"line": {"color": theme.down, "width": 1}, "fillcolor": theme.down},
                whiskerwidth=0,
                showlegend=False,
            ),
            row=1,
            col=1,
        )
    else:
        color = direction_color(theme, trend_change(frame["Close"], reference))
        bounds = padded_range(
            [*frame["Close"].tolist(), *([reference] if reference is not None else [])]
        )
        low, high = bounds if bounds else (0.0, 1.0)
        fig.add_trace(
            go.Scatter(
                x=x,
                y=frame["Close"],
                name="Price",
                mode="lines",
                line={"color": color, "width": 2, "shape": "linear"},
                fill="tozeroy",
                fillgradient={
                    "type": "vertical",
                    "start": low,
                    "stop": high,
                    "colorscale": [
                        [0.0, with_alpha(color, 0.0)],
                        [1.0, with_alpha(color, AREA_TOP_ALPHA)],
                    ],
                },
                hovertemplate=hover + "<extra></extra>",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.update_yaxes(range=[low, high], row=1, col=1)

    if reference is not None:
        fig.add_hline(
            y=reference,
            line={"color": theme.tertiary, "width": 1, "dash": "dot"},
            annotation={
                "text": reference_label,
                "font": {"color": theme.secondary, "size": 11},
                "xanchor": "left",
                "yanchor": "bottom",
            },
            annotation_position="top left",
            row=1,
            col=1,
        )

    if {"BB Upper", "BB Lower"}.issubset(frame.columns):
        band_line = {"color": theme.tertiary, "width": 1}
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
                fillcolor=theme.band,
                legendgroup="bb",
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )

    for column in (c for c in frame.columns if c.startswith(("SMA", "EMA"))):
        fig.add_trace(
            go.Scatter(
                x=x,
                y=frame[column],
                name=column,
                mode="lines",
                line={"color": overlay_color(theme, column), "width": 1.5},
                hovertemplate=hover,
            ),
            row=1,
            col=1,
        )


def _panel_label(fig: go.Figure, row: int, text: str, theme: Theme) -> None:
    """Name an indicator panel in its top-left corner (oscillators stay out of the legend)."""
    axis = "" if row == 1 else str(row)
    fig.add_annotation(
        text=text,
        xref=f"x{axis} domain",
        yref=f"y{axis} domain",
        x=0,
        y=1,
        xanchor="left",
        yanchor="bottom",
        showarrow=False,
        font={"color": theme.secondary, "size": 11},
    )


def _add_volume(fig: go.Figure, frame: pd.DataFrame, row: int, theme: Theme) -> None:
    fig.add_trace(
        go.Bar(
            x=frame.index,
            y=frame["Volume"],
            name="Volume",
            marker_color=theme.neutral_bar,
            marker_line_width=0,
            showlegend=False,
            hovertemplate="%{y:.3s}",
        ),
        row=row,
        col=1,
    )
    fig.update_yaxes(tickformat="~s", nticks=3, row=row, col=1)
    _panel_label(fig, row, "Volume", theme)


def _add_rsi(fig: go.Figure, frame: pd.DataFrame, row: int, theme: Theme) -> None:
    column = next(c for c in frame.columns if c.startswith("RSI"))
    fig.add_hrect(y0=30, y1=70, fillcolor=theme.band, line_width=0, row=row, col=1)
    fig.add_trace(
        go.Scatter(
            x=frame.index,
            y=frame[column],
            name=column,
            mode="lines",
            line={"color": theme.secondary, "width": 1.5},
            hovertemplate="%{y:.1f}",
            showlegend=False,
        ),
        row=row,
        col=1,
    )
    fig.update_yaxes(range=[0, 100], tickvals=[30, 70], row=row, col=1)
    _panel_label(fig, row, f"{column.replace(' ', ' (')})  ·  30 / 70 band", theme)


def _add_macd(fig: go.Figure, frame: pd.DataFrame, row: int, theme: Theme) -> None:
    fig.add_trace(
        go.Bar(
            x=frame.index,
            y=frame["Histogram"],
            name="MACD hist",
            marker_color=theme.neutral_bar,
            marker_line_width=0,
            showlegend=False,
            hovertemplate="%{y:.3~s}",
        ),
        row=row,
        col=1,
    )
    for column, line in (
        ("MACD", {"color": theme.ink, "width": 1.5}),
        ("Signal", {"color": theme.secondary, "width": 1.5, "dash": "dot"}),
    ):
        fig.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame[column],
                name=column,
                mode="lines",
                line=line,
                hovertemplate="%{y:.3~s}",
                showlegend=False,
            ),
            row=row,
            col=1,
        )
    fig.update_yaxes(nticks=4, tickformat=".3~s", row=row, col=1)
    _panel_label(fig, row, "MACD (12, 26, 9)  ·  solid MACD, dotted signal", theme)


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
) -> go.Figure:
    """Price panel with overlays, plus volume / RSI / MACD panels when present.

    ``frame`` is the output of ``indicators.add_indicators``: OHLCV columns and
    any of ``SMA n``, ``EMA n``, ``BB *``, ``RSI n``, ``MACD``/``Signal``/``Histogram``.
    ``style="Line"`` draws an area-gradient line coloured by price direction;
    ``reference`` (the previous close, on 1D) adds a dotted baseline.
    """
    rows = _panel_layout(frame)
    weights = [_PANEL_WEIGHTS[name] for name in rows]
    fig = make_subplots(
        rows=len(rows),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[w / sum(weights) for w in weights],
    )
    fig.update_layout(
        template=plotly_template(theme),
        # Explicit, because Streamlit's frontend fills an unset plot background.
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 0, "r": 4, "t": 28, "b": 4},
        dragmode="zoom",
        # Only price overlays are in the legend; a lone price line needs none.
        showlegend=any(c.startswith(("SMA", "EMA", "BB")) for c in frame.columns),
    )
    # make_subplots creates every axis explicitly, so re-apply the template's axis look.
    fig.update_xaxes(plotly_template(theme).layout.xaxis.to_plotly_json())
    fig.update_yaxes(plotly_template(theme).layout.yaxis.to_plotly_json())
    hover = _price_hover(price_prefix, price_suffix, price_decimals)
    _add_price(
        fig, frame, style, theme, hover=hover, reference=reference, reference_label=reference_label
    )
    if "volume" in rows:
        _add_volume(fig, frame, rows["volume"], theme)
    if "rsi" in rows:
        _add_rsi(fig, frame, rows["rsi"], theme)
    if "macd" in rows:
        _add_macd(fig, frame, rows["macd"], theme)

    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(
        tickprefix=price_prefix,
        ticksuffix=price_suffix,
        tickformat=",",
        hoverformat=f",.{price_decimals}f",
        row=1,
        col=1,
    )
    breaks = compute_rangebreaks(pd.DatetimeIndex(frame.index), interval)
    if breaks:
        fig.update_xaxes(rangebreaks=breaks)
    fig.update_xaxes(hoverformat="%a %b %d, %H:%M" if is_intraday(interval) else "%a %b %d, %Y")
    return fig


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
) -> go.Figure:
    """Cumulative % return lines, one per symbol, with a 0% baseline and end dots."""
    slots = slots or assign_series_slots(map(str, normalized.columns))
    fig = go.Figure()
    for column in normalized.columns:
        values = normalized[column]
        last = values.last_valid_index()
        is_last = [bool(last is not None and ts == last) for ts in values.index]
        color = theme.series[slots.get(str(column), 0) % len(theme.series)]
        fig.add_trace(
            go.Scatter(
                x=normalized.index,
                y=values,
                name=str(column),
                mode="lines+markers",
                connectgaps=True,
                line={"color": color, "width": 2},
                # Only the latest point gets a dot, ringed in the surface colour.
                marker={
                    "color": color,
                    "size": [9 if flag else 0 for flag in is_last],
                    "line": {
                        "color": theme.surface,
                        "width": [2 if flag else 0 for flag in is_last],
                    },
                },
                hovertemplate="%{y:+.2f}%",
            )
        )
    fig.add_hline(y=0, line={"color": theme.axis, "width": 1})
    fig.update_layout(
        template=plotly_template(theme),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 0, "r": 4, "t": 28, "b": 4},
        showlegend=True,
    )
    fig.update_yaxes(ticksuffix="%", tickformat=".0f")
    fig.update_xaxes(hoverformat="%a %b %d, %Y")
    return fig
