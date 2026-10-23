"""Alpha Vantage provider and the fallback chain, against recorded HTTP responses.

``tests/fixtures/alphavantage/IBM`` holds real responses recorded on 2026-10-23
(see the README there); ``tests/fixtures/yahoo/IBM_daily.json`` is Yahoo's data
for the same symbol and day, so both providers' normalised output is compared.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytest
import yfinance

from momentum import alphavantage as av
from momentum import data, providers, settings
from momentum import fundamentals as fu
from momentum.alphavantage import (
    AlphaVantageProvider,
    QuotaExhaustedError,
    RateLimiter,
    UnsupportedSymbolError,
)
from momentum.data import DataSourceError, NoDataError, RateLimitError
from momentum.providers import FallbackProvider

FIXTURES = Path(__file__).parent / "fixtures"
IBM = FIXTURES / "alphavantage" / "IBM"
REPLIES = FIXTURES / "alphavantage" / "replies"
KEY = "test-key-SECRET"


def recorded(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    assert params.pop("apikey") == KEY
    function = params.pop("function")
    params.pop("symbol", None)
    if params.get("outputsize") == "compact":
        params.pop("outputsize")
    name = "_".join([function, *(f"{k}-{v}" for k, v in sorted(params.items()))]) + ".json"
    return httpx.Response(200, content=(IBM / name).read_bytes())


class Recorder:
    """A transport that answers from a handler and remembers each request."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] = recorded) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def functions(self) -> list[str]:
        return [r.url.params["function"] for r in self.requests]


def provider(
    handler: Callable[[httpx.Request], httpx.Response] = recorded,
    *,
    budget: int = 1000,
) -> tuple[AlphaVantageProvider, Recorder]:
    recorder = Recorder(handler)
    limiter = RateLimiter(0, budget, sleep=lambda _s: None)
    return (
        AlphaVantageProvider(
            KEY, limiter=limiter, transport=httpx.MockTransport(recorder), sleep=lambda _s: None
        ),
        recorder,
    )


def reply(name: str, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(status, content=(REPLIES / name).read_bytes())


# --------------------------------------------------------------------------- history


class TestHistory:
    def test_daily_compact_is_yfinance_shaped(self) -> None:
        p, rec = provider()
        frame = p.history("IBM", "3mo", "1d")
        assert rec.requests[0].url.params["outputsize"] == "compact"
        assert list(frame.columns) == list(data.OHLCV_COLUMNS)
        assert isinstance(frame.index, pd.DatetimeIndex)
        assert frame.index.tz is None
        assert frame.index.is_monotonic_increasing
        assert all(frame[c].dtype == np.float64 for c in frame.columns)
        assert frame.index[-1] == pd.Timestamp("2026-10-22")
        assert frame["Close"].iloc[-1] == pytest.approx(252.11)
        cleaned = data.load_history(p, "IBM", "3mo", "1d")
        assert len(cleaned) == len(frame)

    def test_long_ranges_use_the_full_series_trimmed_to_the_period(self) -> None:
        p, rec = provider()
        frame = p.history("IBM", "1y", "1d")
        assert rec.requests[0].url.params["outputsize"] == "full"
        assert frame.index[0] > pd.Timestamp("2025-10-22")
        assert 240 <= len(frame) <= 256

    @pytest.mark.parametrize(
        ("interval", "function"), [("1wk", "TIME_SERIES_WEEKLY"), ("1mo", "TIME_SERIES_MONTHLY")]
    )
    def test_weekly_and_monthly(self, interval: str, function: str) -> None:
        p, rec = provider()
        frame = p.history("IBM", "5y", interval)
        assert rec.functions == [function]
        assert frame.index[0] > pd.Timestamp("2021-10-22")
        assert not frame.empty

    def test_quarterly_bars_are_resampled_from_monthly(self) -> None:
        p, _ = provider()
        frame = p.history("IBM", "max", "3mo")
        assert all(ts.month in {3, 6, 9, 12} for ts in frame.index[:-1])

    def test_intraday(self) -> None:
        p, rec = provider()
        frame = p.history("IBM", "1d", "5m")
        assert rec.requests[0].url.params["interval"] == "5min"
        days = pd.DatetimeIndex(frame.index).normalize()
        assert (days == days[0]).all()
        with pytest.raises(UnsupportedSymbolError, match="2m bars"):
            p.history("IBM", "1d", "2m")


# --------------------------------------------------------------------------- quote


class TestInfo:
    def test_quote_and_overview_in_yahoo_keys(self) -> None:
        p, rec = provider()
        quote = data.parse_quote("IBM", p.info("IBM"))
        assert quote.name == "International Business Machines"
        assert quote.price == pytest.approx(252.11)
        assert quote.previous_close == pytest.approx(251.46)
        assert quote.currency == "USD"
        assert quote.quote_type == "EQUITY"
        assert quote.market_cap is not None
        assert quote.market_cap > 1e11
        assert rec.functions == ["GLOBAL_QUOTE", "OVERVIEW"]
        p.info("IBM")
        assert rec.functions == ["GLOBAL_QUOTE", "OVERVIEW", "GLOBAL_QUOTE"]  # overview cached

    def test_profile_comes_from_the_overview(self) -> None:
        p, _ = provider()
        profile = data.parse_profile(p.info("IBM"))
        assert profile.sector
        assert profile.summary
        assert "IBM" in profile.summary

    def test_unknown_symbol(self) -> None:
        p, _ = provider(lambda _r: httpx.Response(200, json={"Global Quote": {}}))
        with pytest.raises(NoDataError, match="No data found for 'ZZZZ'"):
            p.info("ZZZZ")

    def test_empty_overview_keeps_the_quote(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["function"] == "OVERVIEW":
                return reply("empty_overview.json")(request)
            return recorded(request)

        p, _ = provider(handler)
        quote = data.parse_quote("IBM", p.info("IBM"))
        assert quote.name == "IBM"
        assert quote.price == pytest.approx(252.11)


class TestFundamentals:
    def test_statements_and_dividends(self) -> None:
        p, rec = provider()
        fund = p.fundamentals("IBM", "annual")
        assert rec.functions == ["INCOME_STATEMENT", "BALANCE_SHEET", "DIVIDENDS"]
        assert fund.currency == "USD"
        assert fund.income.periods[0] == date(2025, 12, 31)
        assert fund.income.line("Revenue")[0] == 67_536_000_000
        assert fund.income.line("Diluted EPS") == (None,) * len(fund.income.periods)  # not in AV
        assert all(v is not None for v in fund.balance.line("Total assets"))
        assert fund.dividends[0] == fu.Dividend(date(2026, 8, 10), 1.69)

    def test_quarterly(self) -> None:
        p, _ = provider()
        fund = p.fundamentals("IBM", "quarterly")
        assert len(fund.income.periods) == fu.MAX_PERIODS["quarterly"]
        assert list(fund.income.periods) == sorted(fund.income.periods, reverse=True)


# --------------------------------------------------------------------------- same output


def yahoo_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = json.loads((FIXTURES / "yahoo" / "IBM_daily.json").read_text("utf-8"))

    class Ticker:
        def __init__(self, symbol: str) -> None:
            assert symbol == "IBM"

        def history(self, **_: Any) -> pd.DataFrame:
            h = rec["history"]
            index = pd.to_datetime(h["index"], utc=True).tz_convert("America/New_York")
            return pd.DataFrame(
                h["data"], index=pd.DatetimeIndex(index, name="Date"), columns=h["columns"]
            )

        @property
        def info(self) -> dict[str, Any]:
            return dict(rec["info"])

    monkeypatch.setattr(yfinance, "Ticker", Ticker)


class TestSameNormalisedOutput:
    """Both providers feed the same parsing layer and must look the same to it."""

    def test_history_has_the_same_shape_and_prices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        yahoo_recorded(monkeypatch)
        p, _ = provider()
        from_yahoo = data.load_history(data.YahooProvider(), "IBM", "3mo", "1d")
        from_av = data.load_history(p, "IBM", "3mo", "1d")
        assert list(from_yahoo.columns) == list(from_av.columns)
        assert list(from_yahoo.dtypes) == list(from_av.dtypes)
        assert type(from_yahoo.index) is type(from_av.index)
        assert pd.DatetimeIndex(from_yahoo.index).tz is None
        assert pd.DatetimeIndex(from_av.index).tz is None
        assert from_yahoo.index.name == from_av.index.name == "Date"
        # Yahoo's prices are dividend-adjusted, Alpha Vantage's free series is not: bars
        # after the last ex-dividend date (2026-08-10) must agree to the cent.
        after = pd.Timestamp("2026-08-11")
        both = from_yahoo.loc[from_yahoo.index >= after].index.intersection(from_av.index)
        assert len(both) >= 40
        assert np.allclose(from_yahoo.loc[both, "Close"], from_av.loc[both, "Close"], atol=0.01)
        assert np.allclose(from_yahoo.loc[both, "Volume"], from_av.loc[both, "Volume"], rtol=0.05)

    def test_quote_fields_agree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        yahoo_recorded(monkeypatch)
        p, _ = provider()
        yahoo = data.parse_quote("IBM", data.YahooProvider().info("IBM"))
        alpha = data.parse_quote("IBM", p.info("IBM"))
        assert (yahoo.currency, yahoo.quote_type) == (alpha.currency, alpha.quote_type)
        assert yahoo.previous_close == pytest.approx(alpha.previous_close, abs=0.01)
        assert type(yahoo) is type(alpha)


# --------------------------------------------------------------------------- failures


class TestReplies:
    def test_recorded_throttle_is_a_rate_limit(self) -> None:
        p, _ = provider(reply("information_rate_limit.json"))
        with pytest.raises(RateLimitError, match="rate-limiting"):
            p.info("IBM")

    def test_daily_quota_stops_further_calls_locally(self) -> None:
        p, rec = provider(reply("information_daily_quota.json"))
        with pytest.raises(QuotaExhaustedError) as info:
            p.info("IBM")
        assert 0 < info.value.retry_after_s <= 86_400
        with pytest.raises(QuotaExhaustedError):
            p.history("IBM", "3mo", "1d")
        assert len(rec.requests) == 1  # the second call never left the process

    def test_older_per_minute_note_is_a_rate_limit(self) -> None:
        p, _ = provider(reply("note_per_minute.json"))
        with pytest.raises(RateLimitError):
            p.history("IBM", "3mo", "1d")

    def test_premium_endpoint_is_unsupported(self) -> None:
        p, _ = provider(reply("information_premium.json"))
        with pytest.raises(UnsupportedSymbolError, match="paid plan"):
            p.history("IBM", "1y", "1d")

    def test_bad_key_reply_is_an_outage_not_a_missing_symbol(self) -> None:
        p, _ = provider(reply("information_demo_key.json"))
        with pytest.raises(DataSourceError, match="ALPHAVANTAGE_API_KEY"):
            p.info("IBM")

    def test_error_message_is_no_data(self) -> None:
        p, _ = provider(reply("error_message.json"))
        with pytest.raises(NoDataError):
            p.history("NOPE", "3mo", "1d")

    def test_server_errors_are_retried(self) -> None:
        calls = {"n": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503) if calls["n"] < 3 else recorded(request)

        p, rec = provider(flaky)
        assert p.history("IBM", "3mo", "1d").shape[0] > 50
        assert len(rec.requests) == 3

    def test_timeouts_give_up_without_leaking_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        p, rec = provider(timeout)
        with caplog.at_level(logging.WARNING), pytest.raises(DataSourceError) as info:
            p.info("IBM")
        assert len(rec.requests) == 3  # one try and two retries
        assert KEY not in str(info.value)
        assert KEY not in caplog.text

    def test_client_errors_are_not_retried(self) -> None:
        p, rec = provider(lambda _r: httpx.Response(403))
        with pytest.raises(DataSourceError, match="HTTP 403"):
            p.info("IBM")
        assert len(rec.requests) == 1

    def test_garbage_is_an_outage(self) -> None:
        p, _ = provider(lambda _r: httpx.Response(200, content=b"<html>"))
        with pytest.raises(DataSourceError, match="Unexpected response"):
            p.info("IBM")
        with pytest.raises(DataSourceError):
            av.check_reply(["not", "a", "mapping"], "IBM")

    def test_a_key_is_required(self) -> None:
        with pytest.raises(ValueError, match="key"):
            AlphaVantageProvider("")


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("IBM", "IBM"),
        ("BRK-B", "BRK-B"),
        ("VOD.L", "VOD.LON"),
        ("SHOP.TO", "SHOP.TRT"),
        ("SAP.DE", "SAP.DEX"),
        ("RELIANCE.BO", "RELIANCE.BSE"),
        ("600104.SS", "600104.SHH"),
    ],
)
def test_symbol_mapping(symbol: str, expected: str) -> None:
    assert av.to_av_symbol(symbol) == expected


@pytest.mark.parametrize("symbol", ["^GSPC", "EURUSD=X", "BTC-USD", "LT.NS", "X.XYZ"])
def test_symbols_it_does_not_cover(symbol: str) -> None:
    with pytest.raises(UnsupportedSymbolError):
        av.to_av_symbol(symbol)


class TestRateLimiter:
    def test_spacing_budget_and_daily_reset(self) -> None:
        now = {"t": 100.0}
        slept: list[float] = []
        day = {"d": date(2026, 10, 23)}

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            now["t"] += seconds

        limiter = RateLimiter(1.0, 3, clock=lambda: now["t"], today=lambda: day["d"], sleep=sleep)
        for _ in range(3):
            limiter.acquire()
        assert slept == [pytest.approx(1.0), pytest.approx(1.0)]
        with pytest.raises(QuotaExhaustedError):
            limiter.acquire()
        day["d"] = date(2026, 10, 24)
        limiter.acquire()
        assert limiter.used == 1
        limiter.exhaust()
        with pytest.raises(QuotaExhaustedError):
            limiter.acquire()


# --------------------------------------------------------------------------- chain


class Fake:
    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.calls = 0

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        return self._answer(pd.DataFrame({"Close": [1.0]}))

    def info(self, symbol: str) -> dict[str, Any]:
        return self._answer({"longName": self.name})

    def _answer[T](self, value: T) -> T:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return value


class TestFallback:
    def test_transient_failures_move_on(self) -> None:
        yahoo, alpha = Fake("yahoo", DataSourceError("down")), Fake("alpha")
        chain = FallbackProvider([("yahoo", yahoo), ("alphavantage", alpha)])
        assert chain.info("IBM") == {"longName": "alpha"}
        assert chain.last_source == "alphavantage"

    def test_unsupported_moves_on(self) -> None:
        alpha, yahoo = Fake("alpha", UnsupportedSymbolError("index")), Fake("yahoo")
        chain = FallbackProvider([("alphavantage", alpha), ("yahoo", yahoo)])
        assert chain.info("^GSPC") == {"longName": "yahoo"}

    def test_a_definite_no_stops_the_chain(self) -> None:
        yahoo, alpha = Fake("yahoo", NoDataError("no such symbol")), Fake("alpha")
        chain = FallbackProvider([("yahoo", yahoo), ("alphavantage", alpha)])
        with pytest.raises(NoDataError, match="no such symbol"):
            chain.info("TYPO")
        assert alpha.calls == 0  # its quota is not spent on a typo

    def test_rate_limited_provider_cools_down(self) -> None:
        now = {"t": 0.0}
        yahoo, alpha = Fake("yahoo", RateLimitError("busy")), Fake("alpha")
        chain = FallbackProvider(
            [("yahoo", yahoo), ("alphavantage", alpha)], clock=lambda: now["t"]
        )
        chain.info("IBM")
        chain.info("IBM")
        assert yahoo.calls == 1  # skipped while cooling down
        now["t"] = 61.0
        yahoo.error = None
        assert chain.info("IBM") == {"longName": "yahoo"}

    def test_quota_cooldown_lasts_until_the_next_day(self) -> None:
        now = {"t": 0.0}
        alpha = Fake("alpha", QuotaExhaustedError("spent", retry_after_s=3_600))
        yahoo = Fake("yahoo")
        chain = FallbackProvider(
            [("alphavantage", alpha), ("yahoo", yahoo)], clock=lambda: now["t"]
        )
        chain.info("IBM")
        now["t"] = 1_000.0
        chain.info("IBM")
        assert alpha.calls == 1

    def test_the_most_informative_error_wins(self) -> None:
        chain = FallbackProvider(
            [
                ("yahoo", Fake("yahoo", RateLimitError("Yahoo is busy"))),
                ("alphavantage", Fake("alpha", UnsupportedSymbolError("index"))),
            ]
        )
        with pytest.raises(RateLimitError, match="Yahoo is busy"):
            chain.info("^GSPC")

    def test_fundamentals_skip_providers_without_them(self) -> None:
        p, _ = provider()
        chain = FallbackProvider([("bare", Fake("bare")), ("alphavantage", p)])
        assert chain.fundamentals("IBM", "annual").currency == "USD"
        with pytest.raises(NoDataError, match="fundamentals"):
            FallbackProvider([("bare", Fake("bare"))]).fundamentals("IBM", "annual")
        assert chain.history("IBM", "3mo", "1d").shape[0] == 1

    def test_needs_a_provider(self) -> None:
        with pytest.raises(ValueError, match="provider"):
            FallbackProvider([])


class TestConfiguration:
    def test_default_is_yahoo_alone(self) -> None:
        assert isinstance(providers.build(settings.load({})), data.YahooProvider)

    def test_alphavantage_needs_a_key(self, caplog: pytest.LogCaptureFixture) -> None:
        config = settings.load({"MOMENTUM_PROVIDERS": "alphavantage"})
        with caplog.at_level(logging.WARNING):
            assert isinstance(providers.build(config), data.YahooProvider)
        assert "ALPHAVANTAGE_API_KEY is not set" in caplog.text

    def test_chain_in_order(self) -> None:
        config = settings.load(
            {"MOMENTUM_PROVIDERS": "yahoo, alphavantage", "ALPHAVANTAGE_API_KEY": "k"}
        )
        chain = providers.build(config)
        assert isinstance(chain, FallbackProvider)
        assert chain.names == ["yahoo", "alphavantage"]
        only = providers.build(
            settings.load({"MOMENTUM_PROVIDERS": "alphavantage", "ALPHAVANTAGE_API_KEY": "k"})
        )
        assert isinstance(only, AlphaVantageProvider)

    def test_unknown_names_are_rejected(self) -> None:
        with pytest.raises(settings.SettingsError, match="MOMENTUM_PROVIDERS"):
            settings.load({"MOMENTUM_PROVIDERS": "yahoo,bloomberg"})

    def test_one_live_provider_per_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MOMENTUM_PROVIDERS", raising=False)
        assert data.get_provider(False) is data.get_provider(False)
        assert type(data.get_provider(True)).__name__ == "DemoProvider"
