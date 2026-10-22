"""Plotly figure builders. Pure functions of DataFrames: no Streamlit, no I/O.

Every figure is drawn with a per-theme template (``plotly_template``) so the
light and dark charts are designed separately rather than inverted.

The price chart stacks its panels (price, volume, RSI, MACD) as y-axis domains
on **one** shared x-axis, so a single crosshair runs through every panel and a
single readout (``hoversubplots="axis"``) lists every series at the hovered bar.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Literal

import pandas as pd
import plotly.graph_objects as go

from momentum import formatting as fmt
from momentum.config import is_intraday
from momentum.theme import LIGHT, Theme, direction_color, overlay_color, with_alpha

ChartStyle = Literal["Line", "Candles"]
CHART_STYLES: Final[tuple[ChartStyle, ...]] = ("Line", "Candles")

FONT_FAMILY: Final = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
AREA_TOP_ALPHA: Final = 0.24
_Y_PADDING: Final = 0.06  # share of the price range left free above and below the line
_PANEL_GAP: Final = 0.06  # share of the plot height between stacked panels
DIRECT_LABEL_LIMIT: Final = 4  # dataviz rule: up to four series are labelled at the line end
_LABEL_PX: Final = 16  # minimum vertical distance between two end labels
_OVERLAY_PREFIXES: Final = ("SMA", "EMA", "BB")


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
                "traceorder": "normal",
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
    finite = [float(v) for v in values if pd.notna(v) and math.isfinite(float(v))]
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


def compact_ticks(maximum: float) -> tuple[list[float], list[str]]:
    """Two round ticks under ``maximum`` labelled like the rest of the app (100M, 1.5B).

    Plotly's SI format would print billions as "G"; finance reads "B".
    """
    if not math.isfinite(maximum) or maximum <= 0:
        return [], []
    raw = maximum / 2
    magnitude = 10 ** math.floor(math.log10(raw))
    step = max(m * magnitude for m in (1, 2, 2.5, 5) if m * magnitude <= raw)
    values = [step, 2 * step]
    return values, [_compact_label(v) for v in values]


def _compact_label(value: float) -> str:
    text = fmt.format_compact(value, 1)
    return text.replace(".0", "") if ".0" in text else text


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


def panel_domains(rows: Mapping[str, int]) -> dict[str, tuple[float, float]]:
    """Vertical domain of each panel, top to bottom, with ``_PANEL_GAP`` between them."""
    names = sorted(rows, key=rows.__getitem__)
    usable = 1.0 - _PANEL_GAP * (len(names) - 1)
    total = sum(_PANEL_WEIGHTS[n] for n in names)
    domains: dict[str, tuple[float, float]] = {}
    top = 1.0
    for name in names:
        height = usable * _PANEL_WEIGHTS[name] / total
        domains[name] = (max(0.0, top - height), top)
        top -= height + _PANEL_GAP
    return domains


def _axis_ref(row: int) -> str:
    """Trace reference of a panel's y-axis (``y``, ``y2``...)."""
    return "y" if row == 1 else f"y{row}"


def _readout(value: str, name: str, theme: Theme) -> str:
    """One readout row: the value leads, the series name follows in secondary ink."""
    return f"<b>{value}</b>  <span style='color:{theme.secondary}'>{name}</span><extra></extra>"


def _price_value(prefix: str, suffix: str, decimals: int) -> str:
    return f"{prefix}%{{y:,.{decimals}f}}{suffix}"


def price_range(
    frame: pd.DataFrame, style: ChartStyle, reference: float | None
) -> tuple[float, float]:
    """Price-panel range that keeps every drawn series in view: the line (or the
    candles' highs and lows), the moving averages, the bands and the reference."""
    columns = ["High", "Low"] if style == "Candles" else ["Close"]
    columns += [c for c in frame.columns if c.startswith(_OVERLAY_PREFIXES) and c != "BB Middle"]
    values = [v for c in columns if c in frame for v in frame[c].tolist()]
    if reference is not None:
        values.append(reference)
    return padded_range(values) or (0.0, 1.0)


def _add_price(
    fig: go.Figure,
    frame: pd.DataFrame,
    style: ChartStyle,
    theme: Theme,
    *,
    value: str,
    bounds: tuple[float, float],
    reference: float | None,
    reference_label: str,
) -> None:
    x = frame.index
    low, high = bounds
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
                hovertemplate=(
                    f"<b>{value.replace('%{y', '%{close')}</b>  "
                    f"<span style='color:{theme.secondary}'>O {value.replace('%{y', '%{open')}"
                    f"  H {value.replace('%{y', '%{high')}  L {value.replace('%{y', '%{low')}"
                    "</span><extra></extra>"
                ),
                yaxis="y",
            )
        )
    else:
        color = direction_color(theme, trend_change(frame["Close"], reference))
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
                hovertemplate=_readout(value, "Close", theme),
                showlegend=False,
                yaxis="y",
            )
        )

    if reference is not None:
        fig.add_shape(
            type="line",
            xref="x domain",
            x0=0,
            x1=1,
            yref="y",
            y0=reference,
            y1=reference,
            line={"color": theme.tertiary, "width": 1, "dash": "dot"},
            layer="below",
        )
        fig.add_annotation(
            text=reference_label,
            xref="x domain",
            x=0,
            yref="y",
            y=reference,
            xanchor="left",
            yanchor="bottom",
            showarrow=False,
            font={"color": theme.secondary, "size": 11},
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
                hovertemplate=_readout(value, "Bollinger upper", theme),
                yaxis="y",
            )
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
                hovertemplate=_readout(value, "Bollinger lower", theme),
                yaxis="y",
            )
        )

    for column in (c for c in frame.columns if c.startswith(("SMA", "EMA"))):
        fig.add_trace(
            go.Scatter(
                x=x,
                y=frame[column],
                name=column,
                mode="lines",
                line={"color": overlay_color(theme, column), "width": 1.5},
                hovertemplate=_readout(value, column, theme),
                yaxis="y",
            )
        )


def _panel_label(fig: go.Figure, row: int, text: str, theme: Theme) -> None:
    """Name an indicator panel in its top-left corner (oscillators stay out of the legend)."""
    fig.add_annotation(
        text=text,
        xref="x domain",
        yref=f"{_axis_ref(row)} domain",
        x=0,
        y=1,
        xanchor="left",
        yanchor="bottom",
        showarrow=False,
        font={"color": theme.secondary, "size": 11},
    )


def _add_volume(fig: go.Figure, frame: pd.DataFrame, row: int, theme: Theme) -> dict[str, Any]:
    volume = frame["Volume"]
    fig.add_trace(
        go.Bar(
            x=frame.index,
            y=volume,
            name="Volume",
            marker_color=theme.neutral_bar,
            marker_line_width=0,
            showlegend=False,
            customdata=[fmt.format_compact(v) for v in volume],
            hovertemplate=_readout("%{customdata}", "Volume", theme),
            yaxis=_axis_ref(row),
        )
    )
    _panel_label(fig, row, "Volume", theme)
    tickvals, ticktext = compact_ticks(float(volume.max()))
    return {"tickvals": tickvals, "ticktext": ticktext}


def _add_rsi(fig: go.Figure, frame: pd.DataFrame, row: int, theme: Theme) -> dict[str, Any]:
    column = next(c for c in frame.columns if c.startswith("RSI"))
    fig.add_shape(
        type="rect",
        xref="x domain",
        x0=0,
        x1=1,
        yref=_axis_ref(row),
        y0=30,
        y1=70,
        fillcolor=theme.band,
        line_width=0,
        layer="below",
    )
    label = column.replace(" ", " (") + ")"
    fig.add_trace(
        go.Scatter(
            x=frame.index,
            y=frame[column],
            name=column,
            mode="lines",
            line={"color": theme.secondary, "width": 1.5},
            hovertemplate=_readout("%{y:.1f}", label, theme),
            showlegend=False,
            yaxis=_axis_ref(row),
        )
    )
    _panel_label(fig, row, f"{label}  ·  30 / 70 band", theme)
    return {"range": [0, 100], "tickvals": [30, 70]}


def _add_macd(
    fig: go.Figure, frame: pd.DataFrame, row: int, theme: Theme, decimals: int
) -> dict[str, Any]:
    value = f"%{{y:,.{decimals}f}}"
    fig.add_trace(
        go.Bar(
            x=frame.index,
            y=frame["Histogram"],
            name="MACD hist",
            marker_color=theme.neutral_bar,
            marker_line_width=0,
            showlegend=False,
            hovertemplate=_readout(value, "Histogram", theme),
            yaxis=_axis_ref(row),
        )
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
                hovertemplate=_readout(value, column, theme),
                showlegend=False,
                yaxis=_axis_ref(row),
            )
        )
    _panel_label(fig, row, "MACD (12, 26, 9)  ·  solid MACD, dotted signal", theme)
    # Significant digits, not SI prefixes: an FX MACD of 0.0012 must not read "1.2m".
    return {"nticks": 4, "tickformat": ".3~r"}


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
    domains = panel_domains(rows)
    template = plotly_template(theme)
    fig = go.Figure()
    fig.update_layout(
        template=template,
        # Explicit, because Streamlit's frontend fills an unset plot background.
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 0, "r": 4, "t": 28, "b": 4},
        dragmode="zoom",
        # One readout and one crosshair through every panel on the shared x-axis.
        hovermode="x unified",
        hoversubplots="axis",
        # Only price overlays are in the legend; a lone price line needs none.
        showlegend=any(c.startswith(_OVERLAY_PREFIXES) for c in frame.columns),
    )
    bounds = price_range(frame, style, reference)
    value = _price_value(price_prefix, price_suffix, price_decimals)
    _add_price(
        fig,
        frame,
        style,
        theme,
        value=value,
        bounds=bounds,
        reference=reference,
        reference_label=reference_label,
    )
    axes: dict[str, dict[str, Any]] = {
        "price": {
            "range": list(bounds),
            "tickprefix": price_prefix,
            "ticksuffix": price_suffix,
            "tickformat": ",",
            "hoverformat": f",.{price_decimals}f",
        }
    }
    if "volume" in rows:
        axes["volume"] = _add_volume(fig, frame, rows["volume"], theme)
    if "rsi" in rows:
        axes["rsi"] = _add_rsi(fig, frame, rows["rsi"], theme)
    if "macd" in rows:
        axes["macd"] = _add_macd(fig, frame, rows["macd"], theme, price_decimals)

    y_template = template.layout.yaxis.to_plotly_json()
    bottom = max(rows.values())
    for name, row in rows.items():
        key = "yaxis" if row == 1 else f"yaxis{row}"
        fig.update_layout(
            {key: {**y_template, **axes[name], "domain": list(domains[name]), "anchor": "x"}}
        )
    breaks = compute_rangebreaks(pd.DatetimeIndex(frame.index), interval)
    fig.update_xaxes(
        template.layout.xaxis.to_plotly_json(),
        anchor=_axis_ref(bottom),
        rangeslider_visible=False,
        rangebreaks=breaks or None,
        hoverformat="%a %b %d, %H:%M" if is_intraday(interval) else "%a %b %d, %Y",
    )
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


def dodge(values: Sequence[float], min_gap: float) -> list[float]:
    """Positions for labels at ``values`` that are at least ``min_gap`` apart.

    Overlapping labels are grouped and each group is centred on the mean of its
    targets, so every label moves as little as possible and the order is kept.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    groups: list[list[int]] = []
    for index in order:
        groups.append([index])
        while len(groups) > 1:
            lower, upper = groups[-2], groups[-1]
            if _group_top(lower, values, min_gap) + min_gap <= _group_bottom(
                upper, values, min_gap
            ):
                break
            groups[-2:] = [lower + upper]
    positions = [0.0] * len(values)
    for group in groups:
        bottom = _group_bottom(group, values, min_gap)
        for offset, index in enumerate(group):
            positions[index] = bottom + offset * min_gap
    return positions


def _group_bottom(group: list[int], values: Sequence[float], gap: float) -> float:
    centre = sum(values[i] for i in group) / len(group)
    return centre - gap * (len(group) - 1) / 2


def _group_top(group: list[int], values: Sequence[float], gap: float) -> float:
    return _group_bottom(group, values, gap) + gap * (len(group) - 1)


def comparison_chart(
    normalized: pd.DataFrame,
    *,
    height: int = 460,
    theme: Theme = LIGHT,
    slots: Mapping[str, int] | None = None,
) -> go.Figure:
    """Cumulative % return lines, one per symbol, with a 0% baseline and end dots.

    Up to ``DIRECT_LABEL_LIMIT`` lines are labelled at their end ("AAPL +12.4%",
    in text ink beside the line's coloured end dot); the legend stays as well.
    """
    slots = slots or assign_series_slots(map(str, normalized.columns))
    fig = go.Figure()
    ends: list[tuple[str, float]] = []
    for column in normalized.columns:
        values = normalized[column]
        last = values.last_valid_index()
        is_last = [bool(last is not None and ts == last) for ts in values.index]
        color = theme.series[slots.get(str(column), 0) % len(theme.series)]
        if last is not None:
            ends.append((str(column), float(values.loc[last])))
        fig.add_trace(
            go.Scatter(
                x=normalized.index,
                y=values,
                name=str(column),
                mode="lines+markers",
                connectgaps=True,
                cliponaxis=False,  # the end dot sits on the plot edge
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
                hovertemplate=_readout("%{y:+.2f}%", str(column), theme),
            )
        )
    fig.add_hline(y=0, line={"color": theme.axis, "width": 1})
    bounds = padded_range([0.0, *normalized.to_numpy(dtype=float).ravel().tolist()]) or (-1.0, 1.0)
    label_margin = 4
    if 0 < len(ends) <= DIRECT_LABEL_LIMIT:
        plot_px = max(height - 28 - 32, 1)  # minus the top margin and the date axis
        gap = (bounds[1] - bounds[0]) * _LABEL_PX / plot_px
        positions = dodge([v for _, v in ends], gap)
        texts = [f"{name} {fmt.format_percent(v, 1)}" for name, v in ends]
        for text, y in zip(texts, positions, strict=True):
            fig.add_annotation(
                text=text,
                xref="x domain",
                x=1,
                xshift=10,
                yref="y",
                y=y,
                xanchor="left",
                yanchor="middle",
                showarrow=False,
                font={"color": theme.ink, "size": 12},
            )
        label_margin = 18 + 7 * max(len(t) for t in texts)
    fig.update_layout(
        template=plotly_template(theme),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 0, "r": label_margin, "t": 28, "b": 4},
        showlegend=True,
    )
    # The end labels own the right edge, so the % scale moves to the left.
    fig.update_yaxes(ticksuffix="%", tickformat=".0f", range=list(bounds), side="left")
    if len(normalized.index):
        # An explicit range: Plotly pads the x autorange for markers, which left a gap.
        fig.update_xaxes(range=[normalized.index[0], normalized.index[-1]])
    fig.update_xaxes(hoverformat="%a %b %d, %Y")
    return fig
