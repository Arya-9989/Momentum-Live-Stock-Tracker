from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import charts
from momentum import indicators as ind
from momentum.theme import DARK, LIGHT, Theme


def ohlcv(index: pd.DatetimeIndex, volume: float = 1_000.0) -> pd.DataFrame:
    close = np.linspace(100, 120, len(index))
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


class TestRangebreaks:
    def test_daily_equities_hide_weekends_and_holidays(self) -> None:
        days = pd.bdate_range("2024-12-20", "2025-01-03")
        days = days[~days.isin([pd.Timestamp("2024-12-25"), pd.Timestamp("2025-01-01")])]
        breaks = charts.compute_rangebreaks(days, "1d")
        assert {"bounds": ["sat", "mon"]} in breaks
        assert {"values": ["2024-12-25", "2025-01-01"]} in breaks

    def test_crypto_trading_on_weekends_keeps_weekends(self) -> None:
        days = pd.date_range("2024-01-01", periods=14, freq="D")
        assert charts.compute_rangebreaks(days, "1d") == []

    def test_intraday_hides_overnight_session_gap(self) -> None:
        sessions = [
            pd.date_range(f"2024-03-0{d} 09:30", f"2024-03-0{d} 15:45", freq="15min")
            for d in (4, 5)
        ]
        index = pd.DatetimeIndex(sessions[0].append(sessions[1]))
        breaks = charts.compute_rangebreaks(index, "15m")
        assert {"pattern": "hour", "bounds": [16.0, 9.5]} in breaks

    def test_weekly_bars_need_no_breaks(self) -> None:
        index = pd.date_range("2024-01-05", periods=10, freq="W-FRI")
        assert charts.compute_rangebreaks(index, "1wk") == []

    def test_too_few_points(self) -> None:
        assert (
            charts.compute_rangebreaks(pd.DatetimeIndex([pd.Timestamp("2024-01-01")]), "1d") == []
        )


def subplot_count(fig: object) -> int:
    layout = fig.layout  # type: ignore[attr-defined]
    return sum(1 for key in layout.to_plotly_json() if key.startswith("yaxis"))


def test_price_chart_panels_follow_selected_indicators() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=60))
    settings = ind.IndicatorSettings(sma_windows=(20,), bollinger=True, rsi=True, macd=True)
    fig = charts.price_chart(ind.add_indicators(frame, settings), interval="1d")
    names = [trace.name for trace in fig.data]
    assert {"Price", "SMA 20", "Bollinger (20, 2)", "Volume", "RSI 14", "MACD", "Signal"} <= set(
        names
    )
    assert subplot_count(fig) == 4


def test_price_chart_drops_volume_panel_for_indices() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=30), volume=0.0)
    fig = charts.price_chart(frame, interval="1d")
    assert "Volume" not in [trace.name for trace in fig.data]
    assert subplot_count(fig) == 1


@pytest.mark.parametrize(("style", "trace_type"), [("Candles", "candlestick"), ("Line", "scatter")])
def test_price_chart_style(style: charts.ChartStyle, trace_type: str) -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=10))
    fig = charts.price_chart(frame, interval="1d", style=style, price_prefix="$")
    assert fig.data[0].type == trace_type
    assert fig.layout.yaxis.tickprefix == "$"


def test_price_chart_labels_minor_units() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=10))
    fig = charts.price_chart(frame, interval="1d", price_suffix="p")
    assert fig.layout.yaxis.ticksuffix == "p"


def test_comparison_chart_has_one_line_per_symbol() -> None:
    normalized = pd.DataFrame(
        {"AAPL": [0.0, 1.0], "MSFT": [0.0, -2.0]}, index=pd.date_range("2024-01-01", periods=2)
    )
    fig = charts.comparison_chart(normalized)
    assert [trace.name for trace in fig.data] == ["AAPL", "MSFT"]
    assert fig.layout.yaxis.ticksuffix == "%"


# --------------------------------------------------------------------------- redesign


@pytest.mark.parametrize("scheme", [LIGHT, DARK])
def test_template_is_designed_per_theme(scheme: Theme) -> None:
    layout = charts.plotly_template(scheme).layout
    assert layout.font.color == scheme.ink
    assert layout.hoverlabel.bgcolor == scheme.hover_bg
    assert layout.hovermode == "x unified"  # crosshair readout lists every series
    assert layout.xaxis.spikemode == "across"
    assert layout.xaxis.spikedash == "solid"
    assert layout.yaxis.gridcolor == scheme.grid
    assert layout.yaxis.side == "right"
    assert list(layout.colorway) == list(scheme.series)


def test_line_is_the_default_style_with_an_area_gradient() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=30))  # rising closes
    fig = charts.price_chart(frame, interval="1d")
    price = fig.data[0]
    assert price.type == "scatter"
    assert price.fill == "tozeroy"
    assert price.line.color == LIGHT.up
    low, high = fig.layout.yaxis.range
    assert price.fillgradient.start == low
    assert price.fillgradient.stop == high
    assert 100 > low > 0  # the axis is not stretched down to zero by the fill


def test_line_colour_tracks_direction_and_theme() -> None:
    falling = ohlcv(pd.bdate_range("2024-01-01", periods=10)).iloc[::-1]
    falling.index = pd.bdate_range("2024-01-01", periods=10)
    assert charts.price_chart(falling, interval="1d", theme=DARK).data[0].line.color == DARK.down


def test_previous_close_reference_sets_colour_range_and_line() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=10))  # closes 100 -> 120
    fig = charts.price_chart(frame, interval="1d", reference=130.0, reference_label="Prev 130")
    assert fig.data[0].line.color == LIGHT.down  # 120 is below the previous close
    assert fig.layout.yaxis.range[1] > 130.0
    assert [s.y0 for s in fig.layout.shapes] == [130.0]
    assert "Prev 130" in [a.text for a in fig.layout.annotations]


def test_candles_use_price_state_colours_and_volume_is_neutral() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=10))
    fig = charts.price_chart(frame, interval="1d", style="Candles", theme=DARK)
    candles = fig.data[0]
    assert candles.increasing.line.color == DARK.up
    assert candles.decreasing.line.color == DARK.down
    volume = next(t for t in fig.data if t.name == "Volume")
    assert volume.marker.color == DARK.neutral_bar


def test_legend_only_lists_price_overlays() -> None:
    frame = ohlcv(pd.bdate_range("2024-01-01", periods=60))
    plain = charts.price_chart(
        ind.add_indicators(frame, ind.IndicatorSettings(sma_windows=(), rsi=True, macd=True)),
        interval="1d",
    )
    assert plain.layout.showlegend is False
    oscillators = [t for t in plain.data if t.name in {"RSI 14", "MACD", "Signal"}]
    assert len(oscillators) == 3
    assert all(t.showlegend is False for t in oscillators)
    with_sma = charts.price_chart(
        ind.add_indicators(frame, ind.IndicatorSettings(sma_windows=(50,), rsi=False)),
        interval="1d",
    )
    assert with_sma.layout.showlegend is True
    sma = next(t for t in with_sma.data if t.name == "SMA 50")
    assert sma.line.color == LIGHT.series[1]  # same colour as when SMA 20 is also on


def test_backgrounds_are_explicitly_transparent() -> None:
    fig = charts.price_chart(ohlcv(pd.bdate_range("2024-01-01", periods=5)), interval="1d")
    assert fig.layout.plot_bgcolor == "rgba(0,0,0,0)"
    assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([100.0, 110.0], (99.4, 110.6)),
        ([5.0, 5.0], (4.994, 5.006)),
        ([0.0, 0.0], (-0.06, 0.06)),
    ],
)
def test_padded_range(values: list[float], expected: tuple[float, float]) -> None:
    bounds = charts.padded_range(values)
    assert bounds is not None
    assert bounds[0] == pytest.approx(expected[0])
    assert bounds[1] == pytest.approx(expected[1])


def test_padded_range_ignores_missing_values() -> None:
    assert charts.padded_range([float("nan")]) is None
    assert charts.padded_range([1.0, float("nan"), 3.0]) == pytest.approx((0.88, 3.12))


def test_trend_change() -> None:
    close = pd.Series([10.0, 12.0, 11.0])
    assert charts.trend_change(close) == pytest.approx(1.0)
    assert charts.trend_change(close, reference=12.5) == pytest.approx(-1.5)
    assert charts.trend_change(pd.Series([], dtype=float)) is None


def test_series_slots_are_stable_when_symbols_change() -> None:
    first = charts.assign_series_slots(["AAPL", "MSFT", "^GSPC"])
    assert first == {"AAPL": 0, "MSFT": 1, "^GSPC": 2}
    # Removing MSFT does not repaint ^GSPC; a newcomer takes the free slot.
    second = charts.assign_series_slots(["AAPL", "^GSPC", "NVDA"], first)
    assert second == {"AAPL": 0, "^GSPC": 2, "NVDA": 1}


def test_comparison_chart_marks_only_the_last_point() -> None:
    normalized = pd.DataFrame(
        {"AAPL": [0.0, 1.0, float("nan")]}, index=pd.date_range("2024-01-01", periods=3)
    )
    fig = charts.comparison_chart(normalized, theme=DARK)
    trace = fig.data[0]
    assert list(trace.marker.size) == [0, 9, 0]  # last *valid* point
    assert trace.line.color == DARK.series[0]
    assert fig.layout.yaxis.tickformat == ".0f"  # "0%", not "+0%"
