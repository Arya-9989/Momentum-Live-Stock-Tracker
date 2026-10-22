"""Smoke tests that run the whole Streamlit script headlessly with ``AppTest``.

Demo data is used and yfinance is replaced with a stub that fails loudly, so
these tests never touch the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yfinance
from streamlit.testing.v1 import AppTest

from momentum import data

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


class OfflineTicker:
    def __init__(self, symbol: str) -> None:
        raise ConnectionError(f"network disabled in tests ({symbol})")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(yfinance, "Ticker", OfflineTicker)
    data.clear_caches()
    yield
    data.clear_caches()


def run(*, demo: bool = True, **params: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    if demo:
        at.query_params["demo"] = "true"
    for key, value in params.items():
        at.query_params[key] = value
    return at.run()


def html_text(at: AppTest) -> str:
    return " ".join(str(el.proto) for el in at.get("html"))


def component_names(at: AppTest) -> list[str]:
    return [str(el.proto.component_name) for el in at.get("bidi_component")]


def test_overview_renders_kpis_chart_and_stats() -> None:
    at = run()
    assert not at.exception
    assert "Apple Inc." in html_text(at)
    assert [m.label for m in at.metric][:2] == ["6M return", "Market cap"]
    assert len(at.get("plotly_chart")) == 1
    assert "Demo mode" in html_text(at)  # one-line notice, shown whenever demo data is on
    assert component_names(at) == ["momentum_watchlist_rail", "momentum_theme_watch"]


def test_index_symbol_from_url() -> None:
    at = run(ticker="^NSEI", range="1Y")
    assert not at.exception
    assert "NIFTY 50" in html_text(at)
    assert "Drawdown" in [m.label for m in at.metric]


def test_changing_range_resets_unsupported_interval() -> None:
    at = run()
    at.segmented_control(key="range").set_value("1D").run()
    assert not at.exception
    assert at.selectbox(key="interval").value == "5m"


def test_invalid_symbol_shows_friendly_error() -> None:
    at = run()
    at.text_input(key="ticker").set_value("not a ticker!").run()
    assert not at.exception
    markup = html_text(at)
    assert "isn&#x27;t a ticker symbol." in markup or "isn't a ticker symbol." in markup
    assert "mm-quote-error" in markup  # the header slot's alert, not a raw st.error box
    assert not at.error


def test_unknown_symbol_shows_no_data_error() -> None:
    at = run(ticker="INVALID")
    assert not at.exception
    markup = html_text(at)
    assert "No data for INVALID." in markup
    assert "No chart to show." in markup


def test_network_failure_is_reported_not_raised() -> None:
    at = run(demo=False)
    assert not at.exception
    markup = html_text(at)
    assert "Can&#x27;t reach Yahoo Finance." in markup or "Can't reach Yahoo Finance." in markup
    assert "turn on Demo data" in markup
    assert "MOMENTUM_DEMO" not in markup  # end users are not told to set env vars
    # The rail says it once instead of repeating the error on every row.
    assert "momentum_watchlist_rail" not in component_names(at)


class RateLimitedTicker:
    def __init__(self, symbol: str) -> None:
        raise yfinance.exceptions.YFRateLimitError()


def test_rate_limit_has_its_own_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yfinance, "Ticker", RateLimitedTicker)
    data.clear_caches()
    at = run(demo=False)
    assert not at.exception
    assert "Yahoo Finance is busy." in html_text(at)


def test_compare_and_watchlist_explain_a_total_outage() -> None:
    compare = run(demo=False, view="Compare")
    assert not compare.exception
    assert "reach Yahoo Finance." in html_text(compare)
    assert not compare.get("plotly_chart")
    watch = run(demo=False, view="Watchlist")
    assert not watch.exception
    assert "reach Yahoo Finance." in html_text(watch)
    assert not watch.dataframe


def test_compare_and_watchlist_tabs() -> None:
    compare = run(view="Compare")
    assert not compare.exception
    assert len(compare.get("plotly_chart")) == 1
    assert len(compare.dataframe) == 1

    watch = run(view="Watchlist")
    assert not watch.exception
    table = watch.dataframe[0].value
    assert "RELIANCE.NS" in table["Symbol"].tolist()
