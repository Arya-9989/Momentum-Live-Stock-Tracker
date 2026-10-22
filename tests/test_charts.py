from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import charts
from momentum import indicators as ind


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
