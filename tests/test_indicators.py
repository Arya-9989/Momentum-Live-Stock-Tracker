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


# StockCharts ChartSchool "Moving Averages" worksheet (cs-movavg.xls): 10-day EMA,
# seeded with the 10-day SMA. The sheet rounds to the cent from unrounded inputs, so
# one value sits on a rounding boundary (23.5335 vs 23.54); 0.01 is the tolerance.
EMA_CLOSES = [
    22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29,
    22.15, 22.39, 22.38, 22.61, 23.36, 24.05, 23.75, 23.83, 23.95, 23.63,
    23.82, 23.87, 23.65, 23.19, 23.10, 23.33, 22.68, 23.10, 22.40, 22.17,
]  # fmt: skip
EMA_EXPECTED = [
    22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28, 23.34,
    23.43, 23.51, 23.54, 23.47, 23.40, 23.39, 23.26, 23.23, 23.08, 22.92,
]  # fmt: skip


class TestEMA:
    def test_matches_stockcharts_reference(self) -> None:
        result = ind.ema(series(EMA_CLOSES), 10)
        assert result.iloc[:9].isna().all()
        assert result.iloc[9:].tolist() == pytest.approx(EMA_EXPECTED, abs=0.0101)

    def test_recursive_definition_seeded_with_sma(self) -> None:
        # span=3 -> alpha=0.5, seed = mean(10, 11, 12) = 11, then 0.5*13+0.5*11 = 12, ...
        result = ind.ema(series([10, 11, 12, 13, 14]), 3)
        assert result.iloc[:2].isna().all()
        assert result.iloc[2:].tolist() == pytest.approx([11.0, 12.0, 13.0])
        assert result.name == "EMA 3"

    def test_short_series_is_all_nan(self) -> None:
        assert ind.ema(series([1.0, 2.0]), 12).isna().all()

    def test_skips_missing_values(self) -> None:
        result = ind.ema(series([10, 11, np.nan, 12, 13, 14]), 3)
        assert math.isnan(result.iloc[2])
        assert result.dropna().tolist() == pytest.approx([11.0, 12.0, 13.0])

    def test_constant_series_is_constant(self) -> None:
        assert ind.ema(series([7.0] * 30), 12).dropna().tolist() == pytest.approx([7.0] * 19)


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

    def test_a_missing_value_does_not_poison_later_values(self) -> None:
        closes = [*RSI_CLOSES[:20], math.nan, *RSI_CLOSES[20:]]
        result = ind.rsi(series(closes), 14)
        assert math.isnan(result.iloc[20])
        # Skipping the gap reproduces the reference exactly.
        assert result.dropna().round(2).tolist() == pytest.approx(RSI_EXPECTED, abs=0.01)

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
        expected_signal = ind.ema(expected_line, 9)
        pd.testing.assert_series_equal(result["Signal"], expected_signal, check_names=False)
        pd.testing.assert_series_equal(
            result["Histogram"], expected_line - expected_signal, check_names=False
        )

    def test_hand_computed_first_values(self) -> None:
        # fast=2 (alpha 2/3), slow=3 (alpha 1/2), signal=2 on [1, 2, 4, 8], SMA-seeded.
        result = ind.macd(series([1.0, 2.0, 4.0, 8.0]), fast=2, slow=3, signal=2)
        fast_2 = (2 / 3) * 4 + (1 / 3) * 1.5  # seed mean(1, 2) = 1.5 at bar 1
        fast_3 = (2 / 3) * 8 + (1 / 3) * fast_2
        slow_2 = 7 / 3  # seed mean(1, 2, 4) at bar 2
        slow_3 = 0.5 * 8 + 0.5 * slow_2
        line = [fast_2 - slow_2, fast_3 - slow_3]
        assert result["MACD"].iloc[:2].isna().all()
        assert result["MACD"].iloc[2:].tolist() == pytest.approx(line)
        # The signal line is seeded with the mean of the first two MACD values.
        assert result["Signal"].iloc[:3].isna().all()
        assert result["Signal"].iloc[3] == pytest.approx(sum(line) / 2)

    def test_needs_slow_plus_signal_bars(self) -> None:
        result = ind.macd(series(np.linspace(1, 50, 40).tolist()))
        assert result["MACD"].first_valid_index() == result.index[25]
        assert result["Signal"].first_valid_index() == result.index[33]

    def test_constant_series_has_zero_macd(self) -> None:
        result = ind.macd(series([50.0] * 40)).dropna()
        assert not result.empty
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
