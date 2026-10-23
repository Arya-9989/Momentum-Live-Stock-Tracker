"""The persisted watchlist through the real script (AppTest), plus its pure helpers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yfinance
from streamlit.testing.v1 import AppTest

from momentum import data, identity, store
from momentum.identity import Identity
from momentum.store import StoreUnavailableError
from momentum.store.owners import Owner
from momentum.ui import saved, session

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
ALICE = Identity("device", Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device"))
BOB = Identity("device", Owner("dev_BBBBBBBBBBBBBBBBBBBBBB", "device"))


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


def watchlist_tab() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["demo"] = "true"
    at.query_params["view"] = "Watchlist"
    return at.run()


def html_text(at: AppTest) -> str:
    return " ".join(str(el.proto) for el in at.get("html"))


def test_edits_persist_across_sessions_per_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = watchlist_tab()
    assert not at.exception
    assert "Saved in this browser." in html_text(at)
    widget = at.multiselect(key=saved.WIDGET_KEY)
    widget.set_value(["AAPL", "TSLA"]).run()
    assert not at.exception
    assert at.dataframe[0].value["Symbol"].tolist() == ["AAPL", "TSLA"]

    again = watchlist_tab()  # a new session for the same browser
    assert again.multiselect(key=saved.WIDGET_KEY).value == ["AAPL", "TSLA"]

    as_viewer(monkeypatch, BOB)
    other = watchlist_tab()
    assert "TSLA" not in other.multiselect(key=saved.WIDGET_KEY).value  # isolated


def test_unknown_symbol_is_refused_with_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = watchlist_tab()
    at.multiselect(key=saved.WIDGET_KEY).set_value(["AAPL", "ZZZFAKE"]).run()
    assert not at.exception
    assert at.multiselect(key=saved.WIDGET_KEY).value == ["AAPL"]
    assert "No data for ZZZFAKE, so not added." in html_text(at)
    assert store.get_store().watchlists.get(ALICE.owner).symbols == ("AAPL",)  # type: ignore[arg-type,union-attr]


def test_another_tab_s_edit_is_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = watchlist_tab()
    store.get_store().watchlists.apply(ALICE.owner, add=["NVDA"], base=["AAPL"])  # type: ignore[arg-type]
    at.run()
    assert at.multiselect(key=saved.WIDGET_KEY).value == ["AAPL", "NVDA"]


def test_session_only_viewer_keeps_edits_in_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    at = watchlist_tab()  # no cookie under AppTest: a session identity
    assert "Kept for this session only." in html_text(at)
    at.multiselect(key=saved.WIDGET_KEY).set_value(["MSFT"]).run()
    assert at.multiselect(key=saved.WIDGET_KEY).value == ["MSFT"]
    with store.get_store().db.transaction(write=False) as tx:
        assert tx.one("SELECT COUNT(*) FROM owners") == (0,)


def test_store_outage_degrades_to_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)

    def broken() -> None:
        raise StoreUnavailableError("Saved data is unavailable right now.")

    monkeypatch.setattr(store, "get_store", broken)
    at = watchlist_tab()
    assert not at.exception
    assert "Saving is unavailable right now" in html_text(at)
    at.multiselect(key=saved.WIDGET_KEY).set_value(["MSFT", "AAPL"]).run()
    assert not at.exception
    assert at.multiselect(key=saved.WIDGET_KEY).value == ["AAPL", "MSFT"]  # deltas keep order


def test_rail_shows_the_saved_list(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    store.get_store().watchlists.apply(ALICE.owner, base=["MSFT", "^GSPC"])  # type: ignore[arg-type]
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["demo"] = "true"
    at.run()
    rail = next(el for el in at.get("bidi_component") if "rail" in el.proto.component_name)
    assert '"symbol": "MSFT"' in str(rail.proto) or "MSFT" in str(rail.proto)
    assert "RELIANCE" not in str(rail.proto)


class TestCopy:
    def test_status_lines(self) -> None:
        account = Identity("account", Owner("acct_" + "a" * 40, "account"), email="a@x.io")
        assert saved.status_copy("account", account, can_sign_in=True).text == "Saved to a@x.io."
        device = saved.status_copy("device", ALICE, can_sign_in=True)
        assert device.sign_in
        assert "other devices" in device.text
        assert not saved.status_copy("device", ALICE, can_sign_in=False).sign_in
        assert saved.status_copy("session", identity.SESSION, can_sign_in=False).text == (
            "Kept for this session only."
        )
        assert "unavailable" in saved.status_copy("unavailable", ALICE, can_sign_in=True).text

    def test_edit_notice(self) -> None:
        assert saved.edit_notice([], [], []) is None
        assert saved.edit_notice(["FOO"], ["$$"], ["X"]) == (
            "No data for FOO, so not added; not ticker symbols: $$; "
            "the watchlist holds 12 symbols (X left out)."
        )
        assert saved.edit_notice([], ["!"], []) == "Not ticker symbols: !."


def test_symbol_exists_treats_outages_as_unknown_not_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert saved.symbol_exists("AAPL", demo=True)
    assert not saved.symbol_exists("ZZZNOPE", demo=True)
    assert saved.symbol_exists("AAPL", demo=False)  # Yahoo unreachable: keep it
