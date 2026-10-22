"""Technical indicators as pure pandas functions.

Conventions follow the original definitions as documented by StockCharts:

* SMA is a plain rolling mean that is undefined until ``window`` observations exist.
* EMA uses the recursive form ``ema_t = alpha * x_t + (1 - alpha) * ema_{t-1}`` with
  ``alpha = 2 / (span + 1)``, seeded with the first observation (pandas ``adjust=False``).
* RSI uses Wilder's smoothing, seeded with the simple average of the first ``period``
  gains/losses, which is what charting platforms display.
* Bollinger Bands use the population standard deviation (``ddof=0``), as Bollinger specifies.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def _require_positive(name: str, value: int) -> None:
    if value < 1:
        msg = f"{name} must be a positive integer, got {value}"
        raise ValueError(msg)


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    _require_positive("window", window)
    return series.rolling(window=window, min_periods=window).mean().rename(f"SMA {window}")


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average seeded with the first value."""
    _require_positive("span", span)
    return series.ewm(span=span, adjust=False).mean().rename(f"EMA {span}")


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100) with Wilder's smoothing.

    The first ``period`` values are NaN. When there are no losses in the window
    RSI is 100; when price is completely flat it is 50 (neutral).
    """
    _require_positive("period", period)
    values = close.to_numpy(dtype=float)
    out = np.full(values.shape, np.nan)
    if values.size <= period:
        return pd.Series(out, index=close.index, name=f"RSI {period}")

    delta = np.diff(values)
    gains = np.clip(delta, 0, None)
    losses = np.clip(-delta, 0, None)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period, delta.size):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_value(avg_gain, avg_loss)
    return pd.Series(out, index=close.index, name=f"RSI {period}")


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line and histogram."""
    if fast >= slow:
        msg = f"fast span ({fast}) must be shorter than slow span ({slow})"
        raise ValueError(msg)
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"MACD": line, "Signal": signal_line, "Histogram": line - signal_line},
        index=close.index,
    )


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Middle (SMA), upper and lower Bollinger Bands."""
    _require_positive("window", window)
    rolling = close.rolling(window=window, min_periods=window)
    middle = rolling.mean()
    std = rolling.std(ddof=0)
    return pd.DataFrame(
        {
            "BB Middle": middle,
            "BB Upper": middle + num_std * std,
            "BB Lower": middle - num_std * std,
        },
        index=close.index,
    )


def normalized_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """Cumulative % return of each column relative to its own first valid value.

    Columns that start later (e.g. a recent IPO, or a market on a holiday) are
    rebased at their first observation so every line starts at 0%.
    """

    def rebase(column: pd.Series) -> pd.Series:
        valid = column.dropna()
        first = float(valid.iloc[0]) if not valid.empty else 0.0
        if first == 0:
            return pd.Series(np.nan, index=column.index, name=column.name)
        rebased: pd.Series = (column / first - 1.0) * 100.0
        return rebased

    return closes.apply(rebase)


def period_return(close: pd.Series) -> float | None:
    """Total % return from the first to the last valid observation."""
    valid = close.dropna()
    if len(valid) < 2 or valid.iloc[0] == 0:
        return None
    return float((valid.iloc[-1] / valid.iloc[0] - 1.0) * 100.0)


def max_drawdown(close: pd.Series) -> float | None:
    """Largest peak-to-trough decline, in percent (``-25.0`` means a 25% fall)."""
    valid = close.dropna()
    if len(valid) < 2:
        return None
    drawdowns = valid / valid.cummax() - 1.0
    return float(drawdowns.min() * 100.0)


def annualized_volatility(close: pd.Series, periods_per_year: int = 252) -> float | None:
    """Annualised standard deviation of simple returns, in percent."""
    returns = close.dropna().pct_change().dropna()
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year) * 100.0)


@dataclass(frozen=True, slots=True)
class IndicatorSettings:
    """Which indicators to compute and their parameters."""

    sma_windows: tuple[int, ...] = (20, 50)
    ema_spans: tuple[int, ...] = ()
    bollinger: bool = False
    bollinger_window: int = 20
    bollinger_std: float = 2.0
    rsi: bool = True
    rsi_period: int = 14
    macd: bool = False
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


def add_indicators(ohlcv: pd.DataFrame, settings: IndicatorSettings) -> pd.DataFrame:
    """Return a copy of ``ohlcv`` with the requested indicator columns appended."""
    out = ohlcv.copy()
    close = out["Close"]
    for window in settings.sma_windows:
        out[f"SMA {window}"] = sma(close, window)
    for span in settings.ema_spans:
        out[f"EMA {span}"] = ema(close, span)
    if settings.bollinger:
        out = out.join(bollinger_bands(close, settings.bollinger_window, settings.bollinger_std))
    if settings.rsi:
        out[f"RSI {settings.rsi_period}"] = rsi(close, settings.rsi_period)
    if settings.macd:
        out = out.join(macd(close, settings.macd_fast, settings.macd_slow, settings.macd_signal))
    return out
