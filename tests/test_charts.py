"""Chart payloads (momentum.charts): what the Lightweight Charts component is told to draw."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from momentum import charts
from momentum import indicators as ind
from momentum.theme import DARK, LIGHT, with_alpha


def ohlcv(
    closes: list[float], *, volume: float = 1_000.0, start: str = "2024-01-01"
) -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(closes), name="Date")
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": close - 1, "High": close + 1, "Low": close - 2, "Close": close, "Volume": volume},
        index=index,
    )


def series(payload: dict[str, Any], sid: str) -> dict[str, Any]:
    return next(s for s in payload["series"] if s["id"] == sid)


RISING = ohlcv([float(v) for v in np.linspace(100, 160, 260)])


class TestPriceChart:
    def test_panes_follow_the_selected_indicators(self) -> None:
        settings = ind.IndicatorSettings(sma_windows=(20,), rsi=True, macd=True)
        payload = charts.price_chart(ind.add_indicators(RISING, settings), interval="1d")
        assert [p["label"] for p in payload["panes"]] == [
            "",
            "Volume",
            "RSI (14)  ·  30 / 70 band",
            "MACD (12, 26, 9)  ·  solid MACD, dotted signal",
        ]
        assert [p["weight"] for p in payload["panes"]] == [3.4, 0.8, 1.0, 1.0]
        assert payload["panes"][2]["fixedRange"] == [0, 100]
        panes = {s["id"]: s["pane"] for s in payload["series"]}
        assert panes == {
            "close": 0, "SMA 20": 0, "volume": 1, "rsi": 2, "macd_hist": 3, "macd": 3, "signal": 3,
        }  # fmt: skip
        assert {"pane": 2, "color": LIGHT.band, "from": 70, "to": 30} in payload["bands"]

    def test_indices_have_no_volume_pane(self) -> None:
        payload = charts.price_chart(ohlcv([1.0, 2.0, 3.0], volume=0.0), interval="1d")
        assert len(payload["panes"]) == 1
        assert all(s["id"] != "volume" for s in payload["series"])

    def test_line_is_an_area_coloured_by_direction(self) -> None:
        up = series(charts.price_chart(RISING, interval="1d"), "close")
        assert up["kind"] == "area"
        assert up["color"] == LIGHT.up
        assert up["areaTop"] == with_alpha(LIGHT.up, charts.AREA_TOP_ALPHA)
        falling = charts.price_chart(ohlcv([10.0, 9.0, 8.0]), interval="1d", theme=DARK)
        assert series(falling, "close")["color"] == DARK.down
        flat = series(charts.price_chart(ohlcv([5.0, 5.0]), interval="1d"), "close")
        assert flat["color"] == LIGHT.flat

    def test_previous_close_decides_the_colour_on_1d_and_draws_a_line(self) -> None:
        frame = ohlcv([10.0, 11.0, 10.5])  # up over the window, down on the day
        payload = charts.price_chart(
            frame, interval="5m", reference=12.0, reference_label="Previous close $12.00"
        )
        assert series(payload, "close")["color"] == LIGHT.down
        assert payload["reference"] == {"price": 12.0, "label": "Previous close $12.00"}
        assert payload["intraday"] is True
        assert "reference" not in charts.price_chart(frame, interval="1d", reference=math.nan)

    def test_candles(self) -> None:
        payload = charts.price_chart(
            RISING.head(3), interval="1d", style="Candles", price_prefix="$"
        )
        candles = payload["candles"]
        assert candles["label"] == "Close"
        assert candles["bars"][0][1:] == [99.0, 101.0, 98.0, 100.0]
        assert all(s["kind"] != "area" for s in payload["series"])
        volume = series(payload, "volume")
        assert volume["color"] == LIGHT.neutral_bar  # price state colours stay on the candles
        assert volume["format"] == {"kind": "compact", "decimals": 2}

    def test_price_format_carries_units(self) -> None:
        payload = charts.price_chart(RISING, interval="1d", price_suffix="p", price_decimals=2)
        assert series(payload, "close")["format"] == {
            "kind": "price", "prefix": "", "suffix": "p", "decimals": 2,
        }  # fmt: skip

    def test_overlays_keep_their_colours_and_bands_fill_between(self) -> None:
        settings = ind.IndicatorSettings(sma_windows=(50,), ema_spans=(12,), bollinger=True)
        payload = charts.price_chart(ind.add_indicators(RISING, settings), interval="1d")
        assert series(payload, "SMA 50")["color"] == LIGHT.series[1]
        assert series(payload, "EMA 12")["color"] == LIGHT.series[3]
        assert series(payload, "BB Upper")["color"] == LIGHT.tertiary
        band = {"pane": 0, "color": LIGHT.band, "upper": "BB Upper", "lower": "BB Lower"}
        assert band in payload["bands"]
        assert all(s["id"] != "BB Middle" for s in payload["series"])

    def test_missing_values_are_null_and_times_are_exchange_local(self) -> None:
        frame = ind.add_indicators(RISING.head(30), ind.IndicatorSettings(sma_windows=(20,)))
        sma = series(charts.price_chart(frame, interval="1d"), "SMA 20")["points"]
        assert sma[0][1] is None  # undefined before 20 bars, not 0
        assert sma[19][1] is not None
        assert sma[0][0] == int(pd.Timestamp("2024-01-01").timestamp())
        aware = RISING.head(2).tz_localize("Asia/Kolkata")
        assert charts.seconds(aware.index)[0] == int(pd.Timestamp("2024-01-01").timestamp())

    def test_payload_is_plain_json(self) -> None:
        settings = ind.IndicatorSettings(bollinger=True, rsi=True, macd=True)
        payload = charts.price_chart(
            ind.add_indicators(RISING, settings),
            interval="1d",
            key="AAPL|6M|1d|Line",
            alt="Apple",
            pickable=True,
        )
        text = json.dumps(payload, allow_nan=False)  # NaN would break the browser's JSON.parse
        assert json.loads(text)["pick"] == {"label": "Set an alert at"}
        assert payload["key"] == "AAPL|6M|1d|Line"
        assert payload["theme"]["surface"] == LIGHT.surface


def test_trend_change() -> None:
    close = pd.Series([1.0, np.nan, 3.0])
    assert charts.trend_change(close) == 2.0
    assert charts.trend_change(close, reference=4.0) == -1.0
    assert charts.trend_change(pd.Series([], dtype=float)) is None


def test_series_slots_are_stable_when_symbols_change() -> None:
    first = charts.assign_series_slots(["AAPL", "MSFT", "^GSPC"])
    assert first == {"AAPL": 0, "MSFT": 1, "^GSPC": 2}
    second = charts.assign_series_slots(["MSFT", "^GSPC", "NVDA"], first)
    assert second == {"MSFT": 1, "^GSPC": 2, "NVDA": 0}
    assert charts.assign_series_slots(["A", "B"], {"A": 9}) == {"A": 0, "B": 1}


class TestComparisonChart:
    def normalized(self) -> pd.DataFrame:
        index = pd.bdate_range("2024-01-01", periods=3)
        return pd.DataFrame({"AAPL": [0.0, 5.0, 12.4], "MSFT": [0.0, -1.0, np.nan]}, index=index)

    def test_one_labelled_line_per_symbol_with_a_zero_baseline(self) -> None:
        payload = charts.comparison_chart(self.normalized(), theme=DARK, key="AAPL|MSFT|1Y")
        assert payload["zeroLine"] is True
        aapl, msft = payload["series"]
        assert (aapl["title"], aapl["lastValue"], aapl["color"]) == ("AAPL", True, DARK.series[0])
        assert aapl["format"] == {"kind": "percent", "decimals": 2, "signed": True}
        assert msft["points"][2][1] is None
        assert payload["key"] == "AAPL|MSFT|1Y"

    def test_colours_follow_the_slots(self) -> None:
        payload = charts.comparison_chart(self.normalized(), slots={"AAPL": 4, "MSFT": 2})
        assert [s["color"] for s in payload["series"]] == [LIGHT.series[4], LIGHT.series[2]]


@pytest.mark.parametrize("value", [float("inf"), float("nan"), None, "x"])
def test_points_drop_non_finite_values(value: object) -> None:
    assert charts.points([1], [value]) == [[1, None]]
