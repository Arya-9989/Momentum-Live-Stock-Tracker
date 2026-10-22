"""Data layer tests. yfinance is replaced with fakes; nothing here touches the network."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yfinance

from momentum import data
from momentum.data import (
    DataSourceError,
    InvalidSymbolError,
    NoDataError,
    Quote,
    RateLimitError,
    YahooProvider,
)


def yahoo_frame(closes: list[float], tz: str = "America/New_York") -> pd.DataFrame:
    """A frame shaped like ``yf.Ticker(...).history()`` output."""
    index = pd.date_range("2024-03-04 09:30", periods=len(closes), freq="D", tz=tz, name="Date")
    closes_arr = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes_arr - 1,
            "High": closes_arr + 2,
            "Low": closes_arr - 2,
            "Close": closes_arr,
            "Volume": np.full(len(closes), 1_000.0),
            "Dividends": 0.0,
            "Stock Splits": 0.0,
        },
        index=index,
    )


class FakeTicker:
    """Stands in for ``yfinance.Ticker``; behaviour is configured per symbol."""

    histories: dict[str, pd.DataFrame | Exception] = {}  # noqa: RUF012
    infos: dict[str, Mapping[str, Any] | Exception] = {}  # noqa: RUF012
    calls: list[tuple[str, dict[str, Any]]] = []  # noqa: RUF012

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kwargs: Any) -> pd.DataFrame:
        FakeTicker.calls.append((self.symbol, kwargs))
        result = self.histories.get(self.symbol, pd.DataFrame())
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def info(self) -> Mapping[str, Any]:
        result = self.infos.get(self.symbol, {})
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def fake_yf(monkeypatch: pytest.MonkeyPatch) -> type[FakeTicker]:
    FakeTicker.histories = {}
    FakeTicker.infos = {}
    FakeTicker.calls = []
    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    return FakeTicker


AAPL_INFO: dict[str, Any] = {
    "symbol": "AAPL",
    "longName": "Apple Inc.",
    "shortName": "Apple",
    "currency": "USD",
    "fullExchangeName": "NasdaqGS",
    "exchange": "NMS",
    "quoteType": "EQUITY",
    "marketState": "REGULAR",
    "regularMarketPrice": 190.5,
    "regularMarketPreviousClose": 188.0,
    "regularMarketDayLow": 187.1,
    "regularMarketDayHigh": 191.0,
    "fiftyTwoWeekLow": 164.08,
    "fiftyTwoWeekHigh": 199.62,
    "marketCap": 2.95e12,
    "trailingPE": 29.6,
    "forwardPE": "Infinity",  # Yahoo really sends this
    "trailingEps": 6.43,
    "regularMarketVolume": 51_000_000,
    "averageVolume": 58_000_000,
    "regularMarketTime": 1_710_000_000,
    "exchangeTimezoneShortName": "EDT",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "country": "United States",
    "website": "https://www.apple.com",
    "fullTimeEmployees": 161_000,
    "longBusinessSummary": "Apple Inc. designs smartphones.",
}


# ----------------------------------------------------------------- symbols


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("aapl", "AAPL"),
        ("  lt.ns ", "LT.NS"),
        ("^gspc", "^GSPC"),
        ("btc-usd", "BTC-USD"),
        ("EURUSD=X", "EURUSD=X"),
        ("brk-b", "BRK-B"),
        ("M&M.NS", "M&M.NS"),
    ],
)
def test_normalize_symbol_accepts_yahoo_formats(raw: str, expected: str) -> None:
    assert data.normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "AAPL;DROP", "<script>", "A" * 25, "..AAPL", "^^X"])
def test_normalize_symbol_rejects_garbage(raw: str) -> None:
    with pytest.raises(InvalidSymbolError):
        data.normalize_symbol(raw)


def test_parse_symbol_list_dedupes_caps_and_reports_rejects() -> None:
    valid, rejected = data.parse_symbol_list(["aapl", "AAPL", "msft", "bad sym!", "", "nvda"], 2)
    assert valid == ["AAPL", "MSFT"]
    assert rejected == ["bad sym!"]


# ----------------------------------------------------------------- history cleaning


def test_clean_history_keeps_ohlcv_and_localizes_to_exchange_time() -> None:
    raw = yahoo_frame([10.0, 11.0, 12.0], tz="Asia/Kolkata")
    cleaned = data.clean_history(raw)
    assert list(cleaned.columns) == list(data.OHLCV_COLUMNS)
    assert pd.DatetimeIndex(cleaned.index).tz is None
    assert cleaned.index[0] == pd.Timestamp("2024-03-04 09:30")  # wall-clock time preserved
    assert cleaned.index.name == "Date"


def test_clean_history_sorts_dedupes_and_drops_missing_closes() -> None:
    raw = yahoo_frame([10.0, 11.0, 12.0, 13.0])
    raw.loc[raw.index[1], "Close"] = np.nan
    shuffled = pd.concat([raw.iloc[[3, 0, 2]], raw.iloc[[2]].assign(Close=99.0), raw.iloc[[1]]])
    cleaned = data.clean_history(shuffled)
    assert cleaned.index.is_monotonic_increasing
    assert cleaned["Close"].tolist() == [10.0, 99.0, 13.0]  # last duplicate wins, NaN row gone


def test_clean_history_fills_missing_columns() -> None:
    raw = pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2))
    cleaned = data.clean_history(raw)
    assert cleaned["Volume"].tolist() == [0.0, 0.0]
    assert cleaned["Open"].tolist() == [1.0, 2.0]


def test_clean_history_handles_multiindex_columns() -> None:
    raw = yahoo_frame([1.0, 2.0])
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["AAPL"]])
    assert data.clean_history(raw)["Close"].tolist() == [1.0, 2.0]


def test_clean_history_empty_frame() -> None:
    cleaned = data.clean_history(pd.DataFrame())
    assert cleaned.empty
    assert list(cleaned.columns) == list(data.OHLCV_COLUMNS)


# ----------------------------------------------------------------- quote parsing


def test_parse_quote_full_payload() -> None:
    quote = data.parse_quote("AAPL", AAPL_INFO)
    assert quote.name == "Apple Inc."
    assert quote.exchange == "NasdaqGS"
    assert quote.currency == "USD"
    assert quote.price == 190.5
    assert quote.change == pytest.approx(2.5)
    assert quote.change_percent == pytest.approx(2.5 / 188.0 * 100)
    assert quote.forward_pe is None  # "Infinity" is discarded
    assert quote.trailing_pe == 29.6
    assert quote.as_of == datetime.fromtimestamp(1_710_000_000, tz=UTC)
    assert not quote.is_index


def test_parse_quote_falls_back_to_short_name_and_current_price() -> None:
    info = {
        "shortName": "Larsen",
        "currentPrice": "3500.5",
        "previousClose": 3400,
        "quoteType": "equity",
        "exchange": "NSI",
    }
    quote = data.parse_quote("LT.NS", info)
    assert quote.name == "Larsen"
    assert quote.price == 3500.5
    assert quote.previous_close == 3400.0
    assert quote.quote_type == "EQUITY"
    assert quote.exchange == "NSI"


def test_parse_quote_empty_payload_uses_history() -> None:
    history = data.clean_history(yahoo_frame([100.0, 101.0]))
    quote = data.parse_quote("^NSEI", {}, history)
    assert quote.name == "^NSEI"
    assert quote.price == 101.0
    assert quote.change is None
    assert quote.change_percent is None


def test_quote_change_percent_guards_zero_previous_close() -> None:
    assert Quote("X", "X", price=1.0, previous_close=0.0).change_percent is None


def test_parse_quote_ignores_booleans_and_junk() -> None:
    quote = data.parse_quote("X", {"marketCap": True, "trailingPE": "n/a", "beta": None})
    assert quote.market_cap is None
    assert quote.trailing_pe is None
    assert quote.beta is None


def test_parse_profile() -> None:
    profile = data.parse_profile(AAPL_INFO)
    assert profile.sector == "Technology"
    assert profile.employees == 161_000
    assert not profile.is_empty
    assert data.parse_profile({"quoteType": "INDEX"}).is_empty


# ----------------------------------------------------------------- Yahoo provider


def test_yahoo_history_passes_parameters(fake_yf: type[FakeTicker]) -> None:
    fake_yf.histories["AAPL"] = yahoo_frame([1.0, 2.0])
    frame = data.load_history(YahooProvider(), "AAPL", "6mo", "1d")
    assert frame["Close"].tolist() == [1.0, 2.0]
    symbol, kwargs = fake_yf.calls[0]
    assert symbol == "AAPL"
    assert kwargs == {"period": "6mo", "interval": "1d", "auto_adjust": True, "raise_errors": True}


def test_empty_history_is_no_data(fake_yf: type[FakeTicker]) -> None:
    fake_yf.histories["NOPE"] = pd.DataFrame()
    with pytest.raises(NoDataError, match="NOPE"):
        data.load_history(YahooProvider(), "NOPE", "1mo", "1d")


def test_all_nan_history_is_no_data(fake_yf: type[FakeTicker]) -> None:
    frame = yahoo_frame([1.0, 2.0])
    frame["Close"] = np.nan
    fake_yf.histories["X"] = frame
    with pytest.raises(NoDataError):
        data.load_history(YahooProvider(), "X", "1mo", "1d")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (yfinance.exceptions.YFPricesMissingError("ZZZZ", "possibly delisted"), NoDataError),
        (yfinance.exceptions.YFTzMissingError("ZZZZ"), NoDataError),
        (yfinance.exceptions.YFRateLimitError(), RateLimitError),
        (ConnectionError("Failed to resolve query1.finance.yahoo.com"), DataSourceError),
        (TimeoutError("timed out"), DataSourceError),
        (RuntimeError("HTTP Error 404: Not Found"), NoDataError),
    ],
)
def test_upstream_errors_are_translated(
    fake_yf: type[FakeTicker], exc: Exception, expected: type[Exception]
) -> None:
    fake_yf.histories["ZZZZ"] = exc
    with pytest.raises(expected):
        YahooProvider().history("ZZZZ", "1mo", "1d")


def test_rate_limit_message_is_user_friendly(fake_yf: type[FakeTicker]) -> None:
    fake_yf.infos["AAPL"] = yfinance.exceptions.YFRateLimitError()
    with pytest.raises(RateLimitError, match="rate-limiting"):
        YahooProvider().info("AAPL")


def test_network_message_points_users_at_demo_data_not_env_vars(fake_yf: type[FakeTicker]) -> None:
    fake_yf.infos["AAPL"] = ConnectionError("offline")
    with pytest.raises(DataSourceError) as caught:
        YahooProvider().info("AAPL")
    assert "Demo data" in str(caught.value)
    assert "MOMENTUM_DEMO" not in str(caught.value)


def test_load_info_rejects_empty_payload(fake_yf: type[FakeTicker]) -> None:
    fake_yf.infos["NOPE"] = {"trailingPegRatio": None}
    with pytest.raises(NoDataError):
        data.load_info(YahooProvider(), "NOPE")


def test_load_quotes_isolates_failures(fake_yf: type[FakeTicker]) -> None:
    fake_yf.infos["AAPL"] = AAPL_INFO
    fake_yf.infos["DOWN"] = ConnectionError("network unreachable")
    fake_yf.infos["NOPE"] = {}
    results = data.load_quotes(YahooProvider(), ["AAPL", "DOWN", "NOPE"])
    assert [r.symbol for r in results] == ["AAPL", "DOWN", "NOPE"]  # order preserved
    assert results[0].quote is not None
    assert results[0].quote.price == 190.5
    assert results[1].quote is None
    assert results[1].error is not None
    assert "reach Yahoo" in results[1].error
    assert results[2].error is not None
    assert [r.transient for r in results] == [False, True, False]


def test_load_closes_aligns_series_and_collects_errors(fake_yf: type[FakeTicker]) -> None:
    fake_yf.histories["A"] = yahoo_frame([1.0, 2.0, 3.0])
    fake_yf.histories["B"] = yahoo_frame([10.0, 20.0])
    fake_yf.histories["C"] = pd.DataFrame()
    closes, errors, transient = data.load_closes(YahooProvider(), ["A", "B", "C"], "1mo", "1d")
    assert list(closes.columns) == ["A", "B"]
    assert len(closes) == 3
    assert np.isnan(closes["B"].iloc[-1])
    assert set(errors) == {"C"}
    assert transient == frozenset()  # "no data" is definitive, not worth a retry


def test_load_closes_all_failed_returns_empty_frame(fake_yf: type[FakeTicker]) -> None:
    closes, errors, _ = data.load_closes(YahooProvider(), ["Q"], "1mo", "1d")
    assert closes.empty
    assert "Q" in errors


# ----------------------------------------------------------------- caching


def test_cached_history_uses_demo_provider_without_network(fake_yf: type[FakeTicker]) -> None:
    data.clear_caches()
    first = data.cached_history("AAPL", "1mo", "1d", demo=True)
    second = data.cached_history("AAPL", "1mo", "1d", demo=True)
    pd.testing.assert_frame_equal(first, second)
    assert fake_yf.calls == []


def test_cached_history_caches_successes(fake_yf: type[FakeTicker]) -> None:
    data.clear_caches()
    fake_yf.histories["AAPL"] = yahoo_frame([1.0, 2.0])
    data.cached_history("AAPL", "1mo", "1d", demo=False)
    data.cached_history("AAPL", "1mo", "1d", demo=False)
    assert len(fake_yf.calls) == 1


def test_cached_history_does_not_cache_failures(fake_yf: type[FakeTicker]) -> None:
    data.clear_caches()
    fake_yf.histories["AAPL"] = ConnectionError("offline")
    with pytest.raises(DataSourceError):
        data.cached_history("AAPL", "5d", "15m", demo=False)
    fake_yf.histories["AAPL"] = yahoo_frame([1.0, 2.0])
    assert not data.cached_history("AAPL", "5d", "15m", demo=False).empty
    assert len(fake_yf.calls) == 2


# ----------------------------------------------------------------- warm-up window


def test_visible_window_trims_warmup_bars() -> None:
    frame = pd.DataFrame({"Close": range(400)}, index=pd.bdate_range("2023-06-01", periods=400))
    last = frame.index[-1]
    six_months = data.visible_window(frame, "6mo")
    assert six_months.index[-1] == last
    assert six_months.index[0] > last - pd.DateOffset(months=6)
    assert len(frame.loc[: six_months.index[0]]) > 1  # earlier bars were dropped
    ytd = data.visible_window(frame, "ytd")
    assert ytd.index[0] >= pd.Timestamp(year=last.year, month=1, day=1)


@pytest.mark.parametrize("period", ["1d", "5d", "max"])
def test_visible_window_passes_through_unwarmed_periods(period: str) -> None:
    frame = pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2))
    pd.testing.assert_frame_equal(data.visible_window(frame, period), frame)


def test_visible_window_empty() -> None:
    assert data.visible_window(pd.DataFrame(), "6mo").empty


def test_load_closes_marks_network_failures_transient(fake_yf: type[FakeTicker]) -> None:
    fake_yf.histories["A"] = yahoo_frame([1.0, 2.0])
    fake_yf.histories["DOWN"] = ConnectionError("offline")
    fake_yf.histories["SLOW"] = yfinance.exceptions.YFRateLimitError()
    result = data.load_closes(YahooProvider(), ["A", "DOWN", "SLOW"], "1mo", "1d")
    assert list(result.frame.columns) == ["A"]
    assert result.transient == frozenset({"DOWN", "SLOW"})


def test_cached_quotes_retry_transient_failures(fake_yf: type[FakeTicker]) -> None:
    """A rate limit must not be pinned for the quote TTL: the next rerun asks again."""
    data.clear_caches()
    fake_yf.infos["AAPL"] = yfinance.exceptions.YFRateLimitError()
    first = data.cached_quotes(("AAPL",), demo=False)
    assert first[0].quote is None
    assert first[0].transient
    fake_yf.infos["AAPL"] = AAPL_INFO
    second = data.cached_quotes(("AAPL",), demo=False)
    assert second[0].quote is not None


def test_cached_quotes_keep_definitive_answers(fake_yf: type[FakeTicker]) -> None:
    data.clear_caches()
    fake_yf.infos["AAPL"] = AAPL_INFO
    fake_yf.infos["NOPE"] = {}
    data.cached_quotes(("AAPL", "NOPE"), demo=False)
    fake_yf.infos["AAPL"] = ConnectionError("offline")  # would fail if fetched again
    again = data.cached_quotes(("AAPL", "NOPE"), demo=False)
    assert again[0].quote is not None
    assert again[1].error is not None


def test_cached_closes_retry_transient_failures(fake_yf: type[FakeTicker]) -> None:
    data.clear_caches()
    fake_yf.histories["A"] = ConnectionError("offline")
    assert data.cached_closes(("A",), "1y", "1d", demo=False).frame.empty
    fake_yf.histories["A"] = yahoo_frame([1.0, 2.0])
    assert list(data.cached_closes(("A",), "1y", "1d", demo=False).frame.columns) == ["A"]
    calls = len(fake_yf.calls)
    data.cached_closes(("A",), "1y", "1d", demo=False)  # a success is cached
    assert len(fake_yf.calls) == calls


def test_cached_profile_outlives_a_failing_quote(fake_yf: type[FakeTicker]) -> None:
    data.clear_caches()
    fake_yf.infos["AAPL"] = {**AAPL_INFO, "sector": "Technology"}
    assert data.cached_profile("AAPL", demo=False).sector == "Technology"
    data.cached_info.clear()  # the 60 s quote expires...
    fake_yf.infos["AAPL"] = ConnectionError("offline")  # ...and Yahoo is down
    assert data.cached_profile("AAPL", demo=False).sector == "Technology"
