"""Indicator maths checked against published reference values and hand calculations."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from momentum import indicators as ind

# StockCharts ChartSchool RSI worksheet (cs-rsi.xls): Wilder smoothing, 14 periods.
# The article table rounds closes to 2 dp; the worksheet itself uses these 4 dp values.
RSI_CLOSES = [
    44.3389,
    44.0902,
    44.1497,
    43.6124,
    44.3278,
    44.8264,
    45.0955,
    45.4245,
    45.8433,
    46.0826,
    45.8931,
    46.0328,
    45.6140,
    46.2820,
    46.2820,
    46.0028,
    46.0328,
    46.4116,
    46.2222,
    45.6439,
    46.2122,
    46.2521,
    45.7137,
    46.4515,
    45.7835,
    45.3548,
    44.0288,
    44.1783,
    44.2181,
    44.5672,
    43.4205,
    42.6628,
    43.1314,
]
RSI_EXPECTED = [
    70.53,
    66.32,
    66.55,
    69.41,
    66.36,
    57.97,
    62.93,
    63.26,
    56.06,
    62.38,
    54.71,
    50.42,
    39.99,
    41.46,
    41.87,
    45.46,
    37.30,
    33.08,
    37.77,
]


def series(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"))


class TestSMA:
    def test_matches_hand_calculation(self) -> None:
        result = ind.sma(series([1, 2, 3, 4, 5]), 3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2:].tolist() == [2.0, 3.0, 4.0]
        assert result.name == "SMA 3"

    def test_window_longer_than_series_is_all_nan(self) -> None:
        assert ind.sma(series([1, 2]), 5).isna().all()

    @pytest.mark.parametrize("window", [0, -3])
    def test_rejects_non_positive_window(self, window: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            ind.sma(series([1, 2, 3]), window)


class TestEMA:
    def test_recursive_definition(self) -> None:
        # span=3 -> alpha=0.5: 10, 0.5*11+0.5*10=10.5, 0.5*12+0.5*10.5=11.25, ...
        result = ind.ema(series([10, 11, 12, 13]), 3)
        assert result.tolist() == pytest.approx([10.0, 10.5, 11.25, 12.125])

    def test_constant_series_is_constant(self) -> None:
        assert ind.ema(series([7.0] * 30), 12).tolist() == pytest.approx([7.0] * 30)


class TestRSI:
    def test_matches_stockcharts_reference(self) -> None:
        result = ind.rsi(series(RSI_CLOSES), 14)
        assert result.iloc[:14].isna().all()
        assert result.iloc[14:].round(2).tolist() == pytest.approx(RSI_EXPECTED, abs=0.01)

    def test_only_gains_is_100(self) -> None:
        assert ind.rsi(series(list(range(1, 30))), 14).dropna().eq(100.0).all()

    def test_only_losses_is_0(self) -> None:
        assert ind.rsi(series(list(range(30, 1, -1))), 14).dropna().eq(0.0).all()

    def test_flat_price_is_neutral(self) -> None:
        assert ind.rsi(series([5.0] * 20), 14).dropna().eq(50.0).all()

    def test_short_series_is_all_nan(self) -> None:
        result = ind.rsi(series([1, 2, 3]), 14)
        assert len(result) == 3
        assert result.isna().all()

    def test_bounded(self) -> None:
        rng = np.random.default_rng(0)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 500)))
        values = ind.rsi(series(prices.tolist())).dropna()
        assert values.between(0, 100).all()


class TestMACD:
    def test_equals_difference_of_emas(self) -> None:
        closes = series([float(x) for x in np.linspace(100, 130, 60)] + [120.0] * 20)
        result = ind.macd(closes, 12, 26, 9)
        expected_line = ind.ema(closes, 12) - ind.ema(closes, 26)
        pd.testing.assert_series_equal(result["MACD"], expected_line, check_names=False)
        expected_signal = expected_line.ewm(span=9, adjust=False).mean()
        pd.testing.assert_series_equal(result["Signal"], expected_signal, check_names=False)
        pd.testing.assert_series_equal(
            result["Histogram"], expected_line - expected_signal, check_names=False
        )

    def test_hand_computed_first_values(self) -> None:
        # fast=2 (alpha 2/3), slow=3 (alpha 1/2), signal=2 (alpha 2/3) on [1, 2, 4].
        result = ind.macd(series([1.0, 2.0, 4.0]), fast=2, slow=3, signal=2)
        fast = [1.0, 1 + 2 / 3, (2 / 3) * 4 + (1 / 3) * (5 / 3)]
        slow = [1.0, 1.5, 0.5 * 4 + 0.5 * 1.5]
        line = [f - s for f, s in zip(fast, slow, strict=True)]
        assert result["MACD"].tolist() == pytest.approx(line)
        signal = [line[0]]
        for value in line[1:]:
            signal.append((2 / 3) * value + (1 / 3) * signal[-1])
        assert result["Signal"].tolist() == pytest.approx(signal)

    def test_constant_series_has_zero_macd(self) -> None:
        result = ind.macd(series([50.0] * 40))
        assert np.allclose(result.to_numpy(), 0.0)

    def test_rejects_fast_not_shorter_than_slow(self) -> None:
        with pytest.raises(ValueError, match="shorter"):
            ind.macd(series([1.0] * 40), fast=26, slow=12)


class TestBollinger:
    def test_population_standard_deviation(self) -> None:
        bands = ind.bollinger_bands(series([1.0, 2.0, 3.0]), window=3, num_std=2)
        std = math.sqrt(2 / 3)
        last = bands.iloc[-1]
        assert last["BB Middle"] == pytest.approx(2.0)
        assert last["BB Upper"] == pytest.approx(2 + 2 * std)
        assert last["BB Lower"] == pytest.approx(2 - 2 * std)
        assert bands.iloc[:2].isna().all().all()

    def test_bands_are_symmetric_and_ordered(self) -> None:
        rng = np.random.default_rng(1)
        closes = series((100 + rng.normal(0, 1, 100).cumsum()).tolist())
        bands = ind.bollinger_bands(closes).dropna()
        assert (bands["BB Upper"] >= bands["BB Middle"]).all()
        assert (bands["BB Middle"] >= bands["BB Lower"]).all()
        np.testing.assert_allclose(
            bands["BB Upper"] - bands["BB Middle"], bands["BB Middle"] - bands["BB Lower"]
        )


class TestReturns:
    def test_normalized_returns_rebase_each_column(self) -> None:
        closes = pd.DataFrame(
            {"A": [100.0, 110.0, 99.0], "B": [np.nan, 50.0, 75.0]},
            index=pd.date_range("2024-01-01", periods=3),
        )
        result = ind.normalized_returns(closes)
        assert result["A"].tolist() == pytest.approx([0.0, 10.0, -1.0])
        assert math.isnan(result["B"].iloc[0])
        assert result["B"].iloc[1:].tolist() == pytest.approx([0.0, 50.0])

    def test_normalized_returns_empty_column(self) -> None:
        closes = pd.DataFrame({"A": [np.nan, np.nan]})
        assert ind.normalized_returns(closes)["A"].isna().all()

    def test_period_return(self) -> None:
        assert ind.period_return(series([50.0, 60.0, 75.0])) == pytest.approx(50.0)
        assert ind.period_return(series([50.0])) is None

    def test_max_drawdown(self) -> None:
        # Peak 120 -> trough 90 is a 25% drawdown; the later recovery does not undo it.
        assert ind.max_drawdown(series([100.0, 120.0, 90.0, 130.0])) == pytest.approx(-25.0)
        assert ind.max_drawdown(series([1.0, 2.0, 3.0])) == pytest.approx(0.0)
        assert ind.max_drawdown(series([1.0])) is None

    def test_annualized_volatility(self) -> None:
        # Alternating +1%/-1% returns: sample std of [0.01, -0.0099..] scaled by sqrt(252).
        closes = series([100.0, 101.0, 100.0, 101.0, 100.0])
        returns = closes.pct_change().dropna()
        expected = returns.std(ddof=1) * math.sqrt(252) * 100
        assert ind.annualized_volatility(closes) == pytest.approx(expected)
        assert ind.annualized_volatility(series([1.0, 2.0])) is None


def test_add_indicators_appends_requested_columns() -> None:
    closes = np.linspace(100, 150, 80)
    frame = pd.DataFrame(
        {"Open": closes, "High": closes + 1, "Low": closes - 1, "Close": closes, "Volume": 1.0},
        index=pd.date_range("2024-01-01", periods=80),
    )
    settings = ind.IndicatorSettings(
        sma_windows=(20,), ema_spans=(12,), bollinger=True, rsi=True, macd=True
    )
    result = ind.add_indicators(frame, settings)
    expected = {
        "SMA 20",
        "EMA 12",
        "BB Upper",
        "BB Middle",
        "BB Lower",
        "RSI 14",
        "MACD",
        "Signal",
        "Histogram",
    }
    assert expected.issubset(result.columns)
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]  # input untouched


def test_add_indicators_with_nothing_selected_is_a_copy() -> None:
    frame = pd.DataFrame({"Close": [1.0, 2.0]})
    result = ind.add_indicators(frame, ind.IndicatorSettings(sma_windows=(), rsi=False))
    pd.testing.assert_frame_equal(result, frame)
    assert result is not frame
