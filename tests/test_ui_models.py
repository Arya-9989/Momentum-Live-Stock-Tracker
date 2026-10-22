"""Pure UI models: KPI cards, key statistics and the watchlist table."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from momentum import indicators as ind
from momentum.data import Quote, QuoteResult
from momentum.ui import views
from momentum.ui.kpis import build_kpis, currency_label, key_stats, periods_per_year
from momentum.ui.views import WATCHLIST_COLUMNS, watchlist_frame

EQUITY = Quote(
    symbol="AAPL",
    name="Apple Inc.",
    currency="USD",
    quote_type="EQUITY",
    price=190.0,
    previous_close=188.0,
    day_low=187.0,
    day_high=191.0,
    fifty_two_week_low=150.0,
    fifty_two_week_high=200.0,
    market_cap=2.95e12,
    trailing_pe=29.6,
    volume=40e6,
    average_volume=50e6,
)
INDEX = Quote(
    symbol="^GSPC",
    name="S&P 500",
    currency="USD",
    quote_type="INDEX",
    price=5000.0,
    day_low=4950.0,
    day_high=5010.0,
)


def history(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": closes, "Open": closes}, index=pd.bdate_range("2024-01-01", periods=len(closes))
    )


def test_equity_kpis() -> None:
    kpis = build_kpis(EQUITY, history([100.0, 110.0, 120.0]), "6M", "1d")
    by_label = {k.label: k for k in kpis}
    assert list(by_label) == ["6M return", "Market cap", "P/E (TTM)", "Volume", "Volatility"]
    assert by_label["6M return"].value == "+$20.00"
    assert by_label["6M return"].delta == "+20.00%"
    assert by_label["Market cap"].value == "$2.95T"
    assert by_label["Volume"].delta == "-20% vs avg"
    assert by_label["Volume"].delta_color == "off"


def test_index_kpis_use_price_action_instead_of_valuation() -> None:
    kpis = build_kpis(INDEX, history([5000.0, 4500.0, 4800.0]), "1Y", "1d")
    by_label = {k.label: k for k in kpis}
    assert "Market cap" not in by_label
    assert by_label["Day high"].value == "5,010.00"  # points, no currency symbol
    assert by_label["Drawdown"].value == "-10.0%"
    assert by_label["1Y return"].value == "-200.00"


def test_volatility_is_blank_for_intraday_bars() -> None:
    kpis = build_kpis(EQUITY, history([1.0, 2.0, 3.0]), "1D", "5m")
    assert {k.label: k.value for k in kpis}["Volatility"] == "—"


def test_periods_per_year() -> None:
    assert periods_per_year("1d", "EQUITY") == 252
    assert periods_per_year("1d", "CRYPTOCURRENCY") == 365
    assert periods_per_year("1wk", None) == 52
    assert periods_per_year("5m", None) is None


def test_key_stats_skip_missing_rows() -> None:
    labels = [label for label, _ in key_stats(INDEX)]
    assert "Market cap" not in labels
    assert "P/E (TTM)" not in labels
    assert "Currency" in labels


def test_currency_label() -> None:
    assert currency_label("GBp") == "GBP (quoted in pence)"
    assert currency_label("usd") == "USD"
    assert currency_label(None) == "—"


def test_watchlist_frame_mixes_quotes_and_errors() -> None:
    closes = pd.DataFrame({"AAPL": [1.0, np.nan, 3.0]})
    results = [QuoteResult("AAPL", quote=EQUITY), QuoteResult("NOPE", error="No data found")]
    frame = watchlist_frame(results, closes)
    assert tuple(frame.columns) == WATCHLIST_COLUMNS
    apple, nope = frame.iloc[0], frame.iloc[1]
    assert apple["Price"] == "$190.00"
    assert apple["Change"] == pytest.approx(2 / 188 * 100)
    assert apple["52W position"] == pytest.approx(80.0)
    assert apple["1M trend"] == [1.0, 3.0]
    assert nope["Name"] == "No data found"
    assert math.isnan(nope["P/E"])
    assert frame["P/E"].dtype == float  # numeric so the grid never shows "None"


# --------------------------------------------------------------------------- view helpers


def test_chart_and_table_heights() -> None:
    plain = views.chart_height(ind.IndicatorSettings(rsi=False))
    assert views.chart_height(ind.IndicatorSettings(rsi=True)) == plain + 120
    assert views.chart_height(ind.IndicatorSettings(rsi=True, macd=True)) == plain + 240
    assert views.table_height(7) == 8 * 44 + 2
    assert views.table_height(0) == 2 * 44 + 2


def test_price_units() -> None:
    assert views.price_units(EQUITY) == ("$", "")
    assert views.price_units(INDEX) == ("", "")
    assert views.price_units(Quote(symbol="VOD.L", name="Vodafone", currency="GBp")) == ("", "p")


def test_chart_alt_text_states_the_range_move() -> None:
    controls = views.ChartControls("6M", "1d", "Line", ind.IndicatorSettings())
    text = views.chart_alt(EQUITY, history([100.0, 110.0, 120.0]), controls)
    assert text == (
        "Apple Inc. (AAPL), 6M daily chart: $100.00 on Jan 01, 2024 to $120.00 on "
        "Jan 03, 2024 (+20.00%)."
    )


def test_stats_skip_values_already_on_a_kpi_tile() -> None:
    kpis = build_kpis(EQUITY, history([1.0, 2.0]), "6M", "1d")
    rows = views._kpi_labels_without_duplicates(key_stats(EQUITY), kpis)
    labels = [label for label, _ in rows]
    assert "Market cap" not in labels
    assert "Previous close" in labels


def test_trigger_value_accepts_mapping_or_attribute() -> None:
    assert views.trigger_value({"select": "MSFT"}, "select") == "MSFT"
    assert views.trigger_value(SimpleNamespace(select="NVDA"), "select") == "NVDA"
    assert views.trigger_value({"select": None}, "select") is None
    assert views.trigger_value(None, "select") is None


def test_indicator_settings_from_controls() -> None:
    settings = views.indicator_settings(["SMA 20", "EMA 26", "Bollinger Bands"], True, False)
    assert settings == ind.IndicatorSettings(
        sma_windows=(20,), ema_spans=(26,), bollinger=True, rsi=True, macd=False
    )
