from __future__ import annotations

import pandas as pd
import pytest

from momentum import data
from momentum.demo import DemoProvider, synthetic_history, synthetic_info
from momentum.formatting import format_price

TODAY = pd.Timestamp("2025-06-13")  # a Friday


def test_history_is_deterministic_and_well_formed() -> None:
    first = synthetic_history("AAPL", "6mo", "1d", TODAY)
    second = synthetic_history("AAPL", "6mo", "1d", TODAY)
    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 126
    assert (first["High"] >= first[["Open", "Close"]].max(axis=1)).all()
    assert (first["Low"] <= first[["Open", "Close"]].min(axis=1)).all()
    assert first.index[-1].date() == TODAY.date()


def test_intraday_bars_stay_inside_the_session() -> None:
    index = pd.DatetimeIndex(
        data.clean_history(synthetic_history("MSFT", "5d", "15m", TODAY)).index
    )
    assert index.hour.min() == 9
    assert index.hour.max() == 15
    assert len(set(index.date)) == 5


def test_weekly_resampling() -> None:
    weekly = synthetic_history("AAPL", "1y", "1wk", TODAY)
    assert 50 <= len(weekly) <= 54


def test_indices_have_no_volume_and_crypto_trades_weekends() -> None:
    assert (synthetic_history("^GSPC", "1mo", "1d", TODAY)["Volume"] == 0).all()
    crypto = synthetic_history("BTC-USD", "1mo", "1d", TODAY)
    assert (pd.DatetimeIndex(crypto.index).dayofweek >= 5).any()


def test_info_round_trips_through_parse_quote() -> None:
    quote = data.parse_quote("LT.NS", synthetic_info("LT.NS", TODAY))
    assert quote.currency == "INR"
    assert quote.exchange == "NSE"
    assert quote.price is not None
    assert quote.fifty_two_week_low is not None
    assert quote.fifty_two_week_low <= quote.price
    assert quote.market_cap is not None


def test_unknown_symbols_get_synthetic_data_but_invalid_ones_fail() -> None:
    provider = DemoProvider()
    assert not provider.history("ACME", "1mo", "1d").empty
    with pytest.raises(data.NoDataError):
        provider.history("INVALID", "1mo", "1d")


def test_demo_fx_quotes_as_a_rate() -> None:
    quote = data.parse_quote("EURUSD=X", synthetic_info("EURUSD=X"))
    assert quote.quote_type == "CURRENCY"
    text = format_price(quote.price, quote.currency, quote.quote_type)
    assert "$" not in text
    assert len(text.split(".")[1]) == 4
    assert (synthetic_history("EURUSD=X", "1mo", "1d")["Volume"] == 0).all()
