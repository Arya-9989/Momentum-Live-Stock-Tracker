"""Fundamentals: parsing real (recorded) yfinance statements, derived figures, and the tab."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yfinance
from streamlit.testing.v1 import AppTest

from momentum import data, demo
from momentum import fundamentals as fu
from momentum.data import DataSourceError, NoDataError, RateLimitError
from momentum.ui import fundamentals_view as fv

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
RECORDED = json.loads(
    (Path(__file__).parent / "fixtures" / "yahoo" / "fundamentals.json").read_text("utf-8")
)


def frame(recorded: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        recorded["data"],
        index=recorded["index"],
        columns=pd.to_datetime(recorded["columns"]),
    )


class RecordedTicker:
    """``yfinance.Ticker`` replaying what Yahoo returned on 2026-10-23 (fixtures/yahoo)."""

    fail: Exception | None = None

    def __init__(self, symbol: str) -> None:
        if RecordedTicker.fail is not None:
            raise RecordedTicker.fail
        self.rec = RECORDED[symbol]

    def __getattr__(self, name: str) -> Any:
        if name in {
            "income_stmt",
            "balance_sheet",
            "quarterly_income_stmt",
            "quarterly_balance_sheet",
        }:
            return frame(self.rec[name])
        raise AttributeError(name)

    @property
    def dividends(self) -> pd.Series:
        pairs = self.rec["dividends"]
        stamps = pd.to_datetime([t for t, _ in pairs], utc=True).tz_convert("America/New_York")
        index = pd.DatetimeIndex(stamps, name="Date")  # as yfinance returns it
        return pd.Series([v for _, v in pairs], index=index, name="Dividends", dtype=float)

    @property
    def info(self) -> dict[str, Any]:
        return dict(self.rec["info"])


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    RecordedTicker.fail = None
    monkeypatch.setattr(yfinance, "Ticker", RecordedTicker)
    data.clear_caches()
    yield
    data.clear_caches()


class TestYahooRecorded:
    def test_annual_statements(self, recorded: None) -> None:
        fund = data.YahooProvider().fundamentals("AAPL", "annual")
        assert fund.currency == "USD"
        assert fund.income.periods == (
            date(2025, 9, 30),
            date(2024, 9, 30),
            date(2023, 9, 30),
            date(2022, 9, 30),
        )
        revenue = fund.income.line("Revenue")
        source = frame(RECORDED["AAPL"]["income_stmt"]).loc["Total Revenue"]
        assert revenue[0] == float(source.loc[pd.Timestamp("2025-09-30")])  # type: ignore[arg-type]
        assert [name for name, _ in fund.income.lines] == [n for n, _ in fu.INCOME_LINES]
        assert [name for name, _ in fund.balance.lines] == [n for n, _ in fu.BALANCE_LINES]
        assert all(v is not None for v in fund.balance.line("Total assets"))

    def test_quarterly_keeps_the_latest_five(self, recorded: None) -> None:
        fund = data.YahooProvider().fundamentals("AAPL", "quarterly")
        assert len(fund.income.periods) <= 5
        assert list(fund.income.periods) == sorted(fund.income.periods, reverse=True)

    def test_dividends_newest_first(self, recorded: None) -> None:
        fund = data.YahooProvider().fundamentals("AAPL", "annual")
        dates = [d.ex_date for d in fund.dividends]
        assert dates == sorted(dates, reverse=True)
        assert all(d.amount > 0 for d in fund.dividends)

    def test_index_is_empty_not_an_error(self, recorded: None) -> None:
        fund = data.YahooProvider().fundamentals("^GSPC", "annual")
        assert fund.is_empty

    def test_failures_are_translated(self, recorded: None) -> None:
        RecordedTicker.fail = yfinance.exceptions.YFRateLimitError()
        with pytest.raises(RateLimitError):
            fu.yahoo_fundamentals("AAPL", "annual")
        RecordedTicker.fail = ConnectionError("down")
        with pytest.raises(DataSourceError):
            fu.yahoo_fundamentals("AAPL", "annual")


class TestParsing:
    def test_missing_lines_are_none_and_empty_periods_are_dropped(self) -> None:
        raw = pd.DataFrame(
            {
                pd.Timestamp("2025-12-31"): [100.0, float("nan")],
                pd.Timestamp("2024-12-31"): [90.0, 9.0],
                pd.Timestamp("2023-12-31"): [float("nan"), float("nan")],  # all empty
            },
            index=["Total Revenue", "Net Income"],
        )
        st = fu.statement_from_frame(raw, fu.INCOME_LINES, 4)
        assert st.periods == (date(2025, 12, 31), date(2024, 12, 31))
        assert st.line("Revenue") == (100.0, 90.0)
        assert st.line("Net income") == (None, 9.0)
        assert st.line("Gross profit") == (None, None)
        assert fu.statement_from_frame(None, fu.INCOME_LINES, 4).is_empty
        assert fu.statement_from_frame(pd.DataFrame(), fu.INCOME_LINES, 4).is_empty

    def test_dividends_ignore_zero_and_missing(self) -> None:
        series = pd.Series(
            [0.25, 0.0, float("nan"), 0.26],
            index=pd.to_datetime(["2025-02-10", "2025-03-01", "2025-04-01", "2025-05-12"]),
        )
        assert [d.amount for d in fu.dividends_from_series(series)] == [0.26, 0.25]
        assert fu.dividends_from_series(None) == ()

    def test_trailing_dividends_and_growth(self) -> None:
        paid = [fu.Dividend(date(2026, 8, 10), 0.27), fu.Dividend(date(2025, 10, 1), 0.26)]
        assert fu.trailing_dividends(paid, date(2026, 10, 23)) == 0.27  # the 2025 one is >365 days
        assert fu.trailing_dividends(paid, date(2030, 1, 1)) is None
        assert fu.growth([110.0, 100.0]) == pytest.approx(10.0)
        assert fu.growth([110.0, -100.0]) is None  # growth from a loss is not meaningful
        assert fu.growth([110.0]) is None
        assert fu.margin(25.0, 100.0) == 25.0
        assert fu.margin(25.0, 0.0) is None

    def test_providers_without_fundamentals(self) -> None:
        class Bare:
            def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
                return pd.DataFrame()

            def info(self, symbol: str) -> dict[str, Any]:
                return {}

        with pytest.raises(NoDataError, match="does not report fundamentals"):
            fu.load_fundamentals(Bare(), "AAPL", "annual")
        with pytest.raises(ValueError, match="frequency"):
            fu.load_fundamentals(demo.DemoProvider(), "AAPL", "monthly")  # type: ignore[arg-type]


class TestDemo:
    def test_equities_have_statements_others_do_not(self) -> None:
        aapl = demo.synthetic_fundamentals("AAPL", "annual", pd.Timestamp("2026-10-23"))
        assert aapl.income.periods[0] == date(2025, 12, 31)
        assert len(aapl.income.periods) == 4
        assert aapl.dividends
        assert demo.synthetic_fundamentals("^GSPC", "annual").is_empty
        assert demo.synthetic_fundamentals("BTC-USD", "annual").is_empty
        lt = demo.synthetic_fundamentals("LT.NS", "quarterly", pd.Timestamp("2026-10-23"))
        assert lt.currency == "INR"
        assert lt.income.periods[0] == date(2026, 9, 30)
        assert len(lt.income.periods) == 5


class TestView:
    def test_table_markup_is_accessible_and_escaped(self) -> None:
        st = fu.Statement(
            (date(2025, 9, 30), date(2024, 9, 30)),
            (("Revenue", (416e9, None)), ("Diluted EPS", (7.46, -0.5))),
        )
        html = fv.statement_table_html("Income <statement>", st, "annual", "USD")
        assert '<caption class="mm-sr">Income &lt;statement&gt;</caption>' in html
        assert '<th scope="col">FY 2025</th>' in html
        assert "<td>$416.00B</td><td>—</td>" in html
        assert "<td>$7.46</td><td>-$0.50</td>" in html
        assert fv.period_label(date(2026, 6, 30), "quarterly") == "Jun 2026"

    def test_key_stats(self) -> None:
        fund = fu.Fundamentals(
            "AAPL",
            "USD",
            fu.Statement(
                (date(2025, 9, 30), date(2024, 9, 30)),
                (("Revenue", (110.0e9, 100.0e9)), ("Net income", (22.0e9, 20.0e9))),
            ),
            dividends=(fu.Dividend(date(2026, 8, 1), 0.5), fu.Dividend(date(2026, 5, 1), 0.5)),
        )
        quote = data.Quote("AAPL", "Apple", currency="USD", price=200.0)
        stats = fv.key_stats(fund, quote, date(2026, 10, 23))
        assert stats[0] == ("Revenue", "$110.00B", "+10.0% vs prior period")
        assert stats[1][1] == "20.0%"
        assert stats[2][1] == "$1.00"
        assert stats[3][1] == "0.50%"
        assert fv.dividend_text(0.2625, "USD") == "$0.2625"
        assert fv.dividend_text(4.5, "GBp") == "4.50p"

    def test_empty_copy(self) -> None:
        index = data.Quote("^GSPC", "S&P 500", quote_type="INDEX")
        assert fv.empty_copy("^GSPC", index).detail.startswith("It is an index")
        crypto = data.Quote("BTC-USD", "Bitcoin", quote_type="CRYPTOCURRENCY")
        assert "a crypto" in fv.empty_copy("BTC-USD", crypto).detail
        assert fv.empty_copy("ZZZ", None).title == "No financial statements for ZZZ."


class OfflineTicker:
    def __init__(self, symbol: str) -> None:
        raise ConnectionError(f"network disabled in tests ({symbol})")


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(yfinance, "Ticker", OfflineTicker)
    data.clear_caches()
    yield
    data.clear_caches()


def tab(*, demo_mode: bool = True, **params: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    if demo_mode:
        at.query_params["demo"] = "true"
    at.query_params["view"] = "Fundamentals"
    for key, value in params.items():
        at.query_params[key] = value
    return at.run()


def html_text(at: AppTest) -> str:
    return " ".join(str(el.proto) for el in at.get("html"))


def test_tab_shows_statements(offline: None) -> None:
    at = tab(ticker="MSFT")
    assert not at.exception
    markup = html_text(at)
    assert "Income statement" in markup
    assert "Balance sheet" in markup
    assert "FY 2025" in markup
    at.segmented_control(key="fund_frequency").set_value("quarterly").run()
    assert "FY 2025" not in html_text(at)


def test_tab_explains_an_index_and_an_outage(offline: None) -> None:
    index = tab(ticker="^NSEI")
    assert "has no financial statements" in html_text(index)
    down = tab(demo_mode=False, ticker="AAPL")
    assert not down.exception
    assert "reach Yahoo Finance." in html_text(down)
