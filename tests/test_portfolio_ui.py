"""The Portfolio tab through the real script (AppTest), and its pure display helpers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal as D  # noqa: N817 - reads like the hand-worked figures
from pathlib import Path

import pytest
import streamlit
import yfinance
from streamlit.testing.v1 import AppTest

from momentum import data, store
from momentum import portfolio as pf
from momentum.identity import Identity
from momentum.store.owners import Owner
from momentum.ui import portfolio_view, session

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
ALICE = Identity("device", Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device"))


class OfflineTicker:
    def __init__(self, symbol: str) -> None:
        raise ConnectionError(f"network disabled in tests ({symbol})")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(yfinance, "Ticker", OfflineTicker)
    data.clear_caches()
    yield
    data.clear_caches()


def as_viewer(monkeypatch: pytest.MonkeyPatch, who: Identity) -> None:
    def fake() -> Identity:
        import streamlit as st

        st.session_state["_mm_identity"] = who
        return who

    monkeypatch.setattr(session, "current_identity", fake)


def portfolio_tab() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["demo"] = "true"
    at.query_params["view"] = "Portfolio"
    return at.run()


def html_text(at: AppTest) -> str:
    return " ".join(str(el.proto) for el in at.get("html"))


def record(at: AppTest, kind: str, symbol: str, quantity: float, price: float = 0.0) -> None:
    at.segmented_control(key="pf_kind").set_value(kind).run()
    at.text_input(key="pf_symbol").set_value(symbol).run()
    at.text_input(key="pf_date").set_value("2025-06-02")
    at.number_input(key="pf_quantity").set_value(quantity)
    if kind != "split":
        at.number_input(key="pf_price").set_value(price)
    at.button(key="pf_add").click().run()


def test_session_viewers_are_told_why() -> None:
    at = portfolio_tab()
    assert not at.exception
    assert "Your portfolio needs a saved identity." in html_text(at)


def test_record_value_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = portfolio_tab()
    assert "No holdings yet." in html_text(at)
    record(at, "buy", "MSFT", 2, 400.0)
    assert not at.exception
    markup = html_text(at)
    assert "Recorded the buy of MSFT." in markup
    # Demo MSFT is $512.00: value 1,024.00, cost 800.00, unrealized +224.00 (+28.00%).
    assert "$1,024.00" in markup
    assert "+$224.00" in markup
    assert '<span class="mm-pct">+28.00%</span>' in markup or "+28.00%" in markup
    holdings = at.dataframe[0].value
    assert holdings.loc[0, "Shares"] == "2"
    assert holdings.loc[0, "Avg cost"] == "$400.00"

    record(at, "sell", "MSFT", 5, 500.0)
    assert "only 2 are held then" in html_text(at)

    transactions = at.dataframe[1]
    assert transactions.value["Type"].tolist() == ["Buy"]


def test_delete_selected_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """AppTest cannot click grid rows, so the callback is driven with a selection."""
    owner = ALICE.owner
    assert owner is not None
    repo = store.get_store().portfolio
    buy = repo.add(owner, pf.Transaction("AAPL", "buy", date(2025, 1, 2), D(5), D(1), D(0), "USD"))
    sale = repo.add(
        owner, pf.Transaction("AAPL", "sell", date(2025, 2, 3), D(5), D(2), D(0), "USD")
    )

    class Selection:
        def __init__(self, rows: list[int]) -> None:
            self.selection = type("S", (), {"rows": rows})()

    state: dict[str, object] = {"pf_transactions": Selection([1])}  # the buy (oldest last)
    monkeypatch.setattr(streamlit, "session_state", state)
    portfolio_view._delete_selected(ALICE, [sale.id, buy.id])
    assert "would break a later sale" in str(state[portfolio_view.NOTICE_KEY])
    state["pf_transactions"] = Selection([0, 1])  # both: the sale goes first
    portfolio_view._delete_selected(ALICE, [sale.id, buy.id])
    assert state[portfolio_view.NOTICE_KEY] == "Deleted 2 transactions."
    assert repo.transactions(owner) == []


def test_multi_currency_totals_and_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    repo = store.get_store().portfolio
    owner = ALICE.owner
    assert owner is not None
    repo.add(owner, pf.Transaction("AAPL", "buy", date(2025, 1, 2), D(1), D(200), D(0), "USD"))
    repo.add(owner, pf.Transaction("LT.NS", "buy", date(2025, 1, 2), D(1), D(3000), D(0), "INR"))
    at = portfolio_tab()
    markup = html_text(at)
    assert "Value · INR" in markup
    assert "Value · USD" in markup  # two heroes; never one mixed sum
    at.selectbox(key="pf_target").set_value("USD").run()
    markup = html_text(at)
    # Demo prices: AAPL $228.00, LT.NS ₹3,610.00 at 0.01134 USD/INR = $40.9374
    assert "$268.94" in markup
    assert "rates into USD" in markup


class TestPure:
    def test_summary_markup(self) -> None:
        total = pf.Totals("USD", D("1450"), D("1161.2"), D("288.8"), D("-12.5"), D("0"), False)
        markup = portfolio_view.summary_html(total)
        assert "$1,450.00" in markup
        assert '+$288.80<span class="mm-pct">+24.87%</span>' in markup
        assert '<dd class="mm-num down" style="margin:0">-$12.50</dd>' in markup
        assert '<dd class="mm-num flat" style="margin:0">$0.00</dd>' in markup  # no "+$0.00"
        assert "some prices unavailable" in markup
        grid = portfolio_view.heroes_html([markup, markup])
        assert grid.count("mm-psummary") == 2
        assert "repeat(2,minmax(0,340px))" in grid

    def test_labels(self) -> None:
        assert portfolio_view.price_label("GBp") == "Price (p)"
        assert portfolio_view.price_label("INR") == "Price (₹)"
        assert portfolio_view.fees_label("GBp") == "Fees (£)"
        assert portfolio_view.to_decimal(0.1) == D("0.1")
        assert portfolio_view.to_decimal(None) is None
        assert portfolio_view.parse_date(" 2025-06-02 ") == date(2025, 6, 2)
        assert portfolio_view.parse_date("02/06/2025") is None

    def test_transactions_frame(self) -> None:
        rows = portfolio_view.transactions_frame(
            [
                pf.Transaction("VOD.L", "buy", date(2025, 1, 2), D(10), D("75.5"), D(2), "GBp", 1),
                pf.Transaction("VOD.L", "split", date(2025, 2, 2), D(2), D(0), D(0), "GBp", 2),
            ]
        )
        assert rows["Type"].tolist() == ["Split", "Buy"]
        assert rows["Quantity"].tolist() == ["2 for 1", "10"]
        assert rows["Price"].tolist() == ["", "75.50p"]
        assert rows["Fees"].tolist() == ["", "£2.00"]


def test_bad_date_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = portfolio_tab()
    at.text_input(key="pf_date").set_value("yesterday")
    at.number_input(key="pf_quantity").set_value(1.0)
    at.number_input(key="pf_price").set_value(10.0)
    at.button(key="pf_add").click().run()
    assert "Enter the trade date as YYYY-MM-DD" in html_text(at)
