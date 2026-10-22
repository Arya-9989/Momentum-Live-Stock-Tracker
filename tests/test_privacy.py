"""Your data (export and erase), the audit log and the readiness endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import MutableMapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import streamlit
from streamlit.testing.v1 import AppTest

from momentum import audit, privacy, server, store
from momentum import portfolio as pf
from momentum.identity import Identity
from momentum.store.alerts import NewAlert
from momentum.store.owners import Owner
from momentum.ui import data_view, saved, session

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
ALICE = Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device")
BOB = Owner("dev_BBBBBBBBBBBBBBBBBBBBBB", "device")
OWNED_TABLES = (
    "watchlists",
    "watchlist_items",
    "alerts",
    "alert_events",
    "alert_deliveries",
    "alert_channels",
    "portfolio_transactions",
)


def fill(s: store.Store, owner: Owner) -> None:
    s.watchlists.apply(owner, add=["TSLA"], base=["AAPL"])
    s.alerts.create(owner, NewAlert("AAPL", "price_above", 250.0, demo=True))
    s.alerts.set_webhook(owner, "https://hooks.example.com/x")
    s.alerts.send_test(owner, "webhook")
    s.portfolio.add(
        owner,
        pf.Transaction(
            "AAPL", "buy", date(2025, 1, 2), Decimal("2.5"), Decimal(200), Decimal(1), "USD"
        ),
    )


class TestPrivacy:
    def test_export_holds_everything_but_secrets(self) -> None:
        s = store.get_store()
        fill(s, ALICE)
        fill(s, BOB)
        doc = json.loads(privacy.export_json(s, ALICE))
        assert doc["format"] == "momentum-export"
        assert doc["identity"]["kind"] == "device"
        assert doc["identity"]["first_saved_at"].endswith("+00:00")
        assert doc["watchlist"] == ["AAPL", "TSLA"]
        assert [a["symbol"] for a in doc["alerts"]] == ["AAPL"]
        assert doc["delivery"] == {
            "email": None,
            "webhook_url": "https://hooks.example.com/x",
            "webhook_secret_set": True,
        }
        assert "whsec_" not in json.dumps(doc)  # the signing secret never leaves
        assert doc["notifications"][0]["message"].startswith("Test webhook")
        assert doc["portfolio"] == [
            {"date": "2025-01-02", "type": "buy", "symbol": "AAPL", "quantity": "2.5",
             "price": "200", "fees": "1", "currency": "USD"},
        ]  # fmt: skip
        assert ALICE.id not in json.dumps(doc)  # no bearer identifiers in the file

    def test_export_of_a_viewer_who_saved_nothing(self) -> None:
        doc = privacy.export(store.get_store(), ALICE)
        assert doc["identity"]["first_saved_at"] is None
        assert (doc["watchlist"], doc["alerts"], doc["portfolio"]) == (None, [], [])

    def test_erase_removes_one_owner_entirely(self) -> None:
        s = store.get_store()
        fill(s, ALICE)
        fill(s, BOB)
        assert privacy.erase(s, ALICE)
        assert not privacy.erase(s, ALICE)
        with s.db.transaction(write=False) as tx:
            for table in OWNED_TABLES:
                row = tx.one(f"SELECT COUNT(*) FROM {table} WHERE owner_id = ?", (ALICE.id,))
                assert row == (0,), table
        assert s.watchlists.get(BOB) is not None  # someone else's data is untouched
        assert len(s.portfolio.transactions(BOB)) == 1


class TestDataView:
    def test_export_bytes_are_audited(self, caplog: pytest.LogCaptureFixture) -> None:
        fill(store.get_store(), ALICE)
        records: list[str] = []

        class Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = Collect()
        audit.logger.addHandler(handler)
        try:
            body = data_view.export_bytes(Identity("device", ALICE))
        finally:
            audit.logger.removeHandler(handler)
        assert json.loads(body)["watchlist"] == ["AAPL", "TSLA"]
        (line,) = records
        event = json.loads(line)
        assert event["audit"] == "data.exported"
        assert event["owner"].startswith("device:")
        assert ALICE.id not in line

    def test_erase_clears_the_session_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fill(store.get_store(), ALICE)
        state: dict[str, Any] = {saved.WATCHLIST_KEY: ["AAPL", "TSLA"], saved.WIDGET_KEY: ["AAPL"]}
        monkeypatch.setattr(streamlit, "session_state", state)
        data_view.erase(Identity("device", ALICE))
        assert saved.WATCHLIST_KEY not in state
        assert state[data_view.NOTICE_KEY].startswith("Deleted your watchlist")
        assert store.get_store().watchlists.get(ALICE) is None
        data_view.erase(Identity("device", ALICE))
        assert state[data_view.NOTICE_KEY] == "There was nothing saved to delete."

    def test_erase_without_a_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state: dict[str, Any] = {}
        monkeypatch.setattr(streamlit, "session_state", state)
        monkeypatch.setattr(session, "open_store", lambda: None)
        data_view.erase(Identity("device", ALICE))
        assert "nothing was deleted" in state[data_view.NOTICE_KEY]
        assert b"unavailable" in data_view.export_bytes(Identity("device", ALICE))

    def test_popover_needs_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake() -> Identity:
            streamlit.session_state["_mm_identity"] = Identity("device", ALICE)
            return Identity("device", ALICE)

        monkeypatch.setattr(session, "current_identity", fake)
        fill(store.get_store(), ALICE)
        at = AppTest.from_file(APP, default_timeout=60)
        at.query_params["demo"] = "true"
        at.session_state["your_data"] = True  # the popover is open
        at.run()
        assert not at.exception
        delete = at.button(key="data_delete")
        assert delete.disabled
        at.checkbox(key=data_view.CONFIRM_KEY).check().run()
        at.button(key="data_delete").click().run()
        assert store.get_store().watchlists.get(ALICE) is None
        assert not at.checkbox(key=data_view.CONFIRM_KEY).value  # reset for next time

    def test_session_viewers_see_no_data_controls(self) -> None:
        at = AppTest.from_file(APP, default_timeout=60)
        at.query_params["demo"] = "true"
        at.run()
        assert not [b for b in at.button if str(b.key).startswith("data_")]


class TestAudit:
    def test_unknown_events_are_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown audit event"):
            audit.record("everything.fine", None)

    def test_owner_reference_is_stable_and_pseudonymous(self) -> None:
        ref = audit.owner_ref(ALICE)
        assert ref == audit.owner_ref(ALICE)
        assert ref is not None
        assert ref.startswith("device:")
        assert ALICE.id.removeprefix("dev_") not in ref
        assert audit.owner_ref(BOB) != ref
        assert audit.owner_ref(None) is None


class TestReadiness:
    def call(self) -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []

        async def receive() -> MutableMapping[str, Any]:
            return {"type": "http.request"}

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(dict(message))

        route: Any = next(r for r in server.ROUTES if getattr(r, "path", "") == "/readyz")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/readyz",
            "headers": [],
            "query_string": b"",
        }
        asyncio.run(route.app(scope, receive, send))
        return int(sent[0]["status"]), json.loads(sent[1]["body"])

    def test_ready_when_migrated(self) -> None:
        status, body = self.call()
        assert status == 200
        assert body["status"] == "ready"
        assert body["database"] == "sqlite"
        assert body["schema"] == body["schema_expected"] >= 3
        assert body["alert_worker"] == "off"  # the tests' setting

    def test_unavailable_store_is_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def broken(_settings: object = None) -> None:
            raise store.StoreUnavailableError("down")

        monkeypatch.setattr(store, "get_store", broken)
        status, body = self.call()
        assert (status, body["status"]) == (503, "unavailable")
        assert "down" not in json.dumps(body)  # no error text leaks

    def test_a_stopped_worker_is_not_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MOMENTUM_ALERT_WORKER", "auto")
        status, body = self.call()
        assert (status, body["status"]) == (503, "alert worker not running")
