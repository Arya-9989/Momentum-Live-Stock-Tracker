"""The Alerts tab and notification toasts through the real script (AppTest)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
import yfinance
from streamlit.testing.v1 import AppTest

from momentum import data, store
from momentum.alerts import delivery, engine
from momentum.alerts.engine import Observation
from momentum.identity import Identity
from momentum.store.owners import Owner
from momentum.ui import alerts_view, session

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
ALICE = Identity("device", Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device"))
ACCOUNT = Identity(
    "account", Owner("acct_" + "e" * 40, "account"), email="me@example.com", name="Me"
)


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


def alerts_tab(**params: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["demo"] = "true"
    at.query_params["view"] = "Alerts"
    for key, value in params.items():
        at.query_params[key] = value
    return at.run()


def html_text(at: AppTest) -> str:
    return " ".join(str(el.proto) for el in at.get("html"))


def test_session_viewers_are_told_why_there_is_no_form() -> None:
    at = alerts_tab()
    assert not at.exception
    assert "Alerts need a saved identity." in html_text(at)
    assert not [b for b in at.button if str(b.key).startswith(("alert", "webhook"))]


def test_create_list_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = alerts_tab(ticker="MSFT")
    assert not at.exception
    assert at.text_input(key="alert_symbol").value == "MSFT"
    assert "MSFT is $512.00 now." in html_text(at)
    assert at.number_input(key="alert_level").value == pytest.approx(537.6)  # 5% above
    assert at.checkbox(key="alert_email").disabled  # devices cannot get email
    at.number_input(key="alert_level").set_value(600.0)
    at.button(key="alert_create").click().run()
    assert not at.exception
    markup = html_text(at)
    assert "Alert created: MSFT Price crosses above $600.00." in markup
    assert "Waiting for the first check" in markup
    (saved,) = store.get_store().alerts.for_owner(ALICE.owner)  # type: ignore[arg-type]
    assert (saved.symbol, saved.threshold, saved.demo) == ("MSFT", 600.0, True)
    at.button(key=f"alert_delete_{saved.id}").click().run()
    assert store.get_store().alerts.for_owner(ALICE.owner) == []  # type: ignore[arg-type]
    assert "No alerts yet." in html_text(at)


def test_invalid_input_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = alerts_tab()
    at.selectbox(key="alert_condition").set_value("rsi_above").run()
    assert at.number_input(key="alert_level").value == 70.0
    assert "RSI (14) for AAPL is" in html_text(at)
    at.number_input(key="alert_level").set_value(150.0)
    at.button(key="alert_create").click().run()
    assert "RSI runs from 0 to 100" in html_text(at)
    at.text_input(key="alert_symbol").set_value("ZZZNOPE").run()
    at.button(key="alert_create").click().run()
    assert "No data for ZZZNOPE, so no alert was created." in html_text(at)
    at.text_input(key="alert_symbol").set_value("bad symbol!").run()
    assert "is not a valid ticker symbol" in html_text(at)
    assert store.get_store().alerts.for_owner(ALICE.owner) == []  # type: ignore[arg-type]


def test_crossovers_need_no_level(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = alerts_tab()
    at.selectbox(key="alert_condition").set_value("sma_50_200_up").run()
    assert not at.number_input
    at.button(key="alert_create").click().run()
    (saved,) = store.get_store().alerts.for_owner(ALICE.owner)  # type: ignore[arg-type]
    assert saved.condition == "sma_50_200_up"


def test_accounts_can_choose_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOMENTUM_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("MOMENTUM_SMTP_FROM", "Momentum <alerts@momentum.test>")
    as_viewer(monkeypatch, ACCOUNT)
    at = alerts_tab()
    email = at.checkbox(key="alert_email")
    assert not email.disabled
    assert email.label == "Email"
    assert email.help == "Sent to me@example.com, the address your sign-in verified."
    email.check()
    at.button(key="alert_create").click().run()
    (saved,) = store.get_store().alerts.for_owner(ACCOUNT.owner)  # type: ignore[arg-type]
    assert saved.channels == ("email",)
    assert store.get_store().alerts.channels(ACCOUNT.owner).email == "me@example.com"  # type: ignore[arg-type]


def test_notifications_list_and_mark_read(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    repo = store.get_store().alerts
    from momentum.store.alerts import NewAlert

    repo.create(ALICE.owner, NewAlert("AAPL", "price_above", 200.0, demo=True, currency="USD"))  # type: ignore[arg-type]

    class Series:
        n = 0

        def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> Observation:
            self.n += 1
            return Observation({"price": 190.0 if self.n == 1 else 205.0}, self.n * 60_000)

    series = Series()
    engine.evaluate(repo, series)
    engine.evaluate(repo, series)
    at = alerts_tab()
    markup = html_text(at)
    assert "AAPL rose above $200.00 (now $205.00)." in markup
    assert "1 new" in markup
    # Markdown-escaped, so "$200.00 ... $205.00" is not rendered as LaTeX.
    assert [t.value for t in at.toast] == [r"AAPL rose above \$200\.00 \(now \$205\.00\)\."]
    at.button(key="alert_mark_read").click().run()
    assert "All caught up" in html_text(at)
    assert repo.unread_count(ALICE.owner) == 0  # type: ignore[arg-type]


def test_webhook_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = alerts_tab()
    monkeypatch.setattr(delivery, "check_url", lambda url, allow_private=False: None)
    at.session_state["webhook_panel"] = True
    at.run()
    at.text_input(key="webhook_url").set_value("https://hooks.example.com/m").run()
    at.button(key="webhook_save").click().run()
    assert not at.exception
    assert "Webhook saved." in html_text(at)
    assert any(code.value.startswith("whsec_") for code in at.code)
    channels = store.get_store().alerts.channels(ALICE.owner)  # type: ignore[arg-type]
    assert channels.webhook_url == "https://hooks.example.com/m"
    at.run()
    assert not any(code.value.startswith("whsec_") for code in at.code)  # shown once
    at.button(key="webhook_test").click().run()
    assert "Test webhook queued" in html_text(at)
    at.button(key="webhook_remove").click().run()
    assert store.get_store().alerts.channels(ALICE.owner).webhook_url is None  # type: ignore[arg-type]


def test_unsafe_webhook_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    as_viewer(monkeypatch, ALICE)
    at = alerts_tab()
    at.session_state["webhook_panel"] = True
    at.run()
    at.text_input(key="webhook_url").set_value("http://169.254.169.254/latest").run()
    at.button(key="webhook_save").click().run()
    assert "Webhook URLs must start with https://." in html_text(at)
    assert store.get_store().alerts.channels(ALICE.owner).webhook_url is None  # type: ignore[arg-type]


class TestPure:
    NOW = 1_700_000_000_000

    def test_format_ago(self) -> None:
        assert alerts_view.format_ago(None, self.NOW) == "not checked yet"
        assert alerts_view.format_ago(self.NOW - 5_000, self.NOW) == "just now"
        assert alerts_view.format_ago(self.NOW - 5 * 60_000, self.NOW) == "5 min ago"
        assert alerts_view.format_ago(self.NOW - 3 * 3_600_000, self.NOW) == "3 h ago"
        assert alerts_view.format_ago(self.NOW - 3 * 86_400_000, self.NOW) == "Nov 11"

    def test_level_labels_and_defaults(self) -> None:
        from momentum.alerts import rules

        price = rules.CONDITIONS_BY_KEY["price_above"]
        assert alerts_view.level_label(price, "USD", "EQUITY") == "Level ($)"
        assert alerts_view.level_label(price, "GBp", "EQUITY") == "Level (£)"
        assert alerts_view.level_label(price, "INR", "INDEX") == "Level"
        assert alerts_view.default_level(rules.CONDITIONS_BY_KEY["price_below"], 100.0) == 95.0
        assert alerts_view.default_level(rules.CONDITIONS_BY_KEY["change_below"], None) == -3.0
        assert alerts_view.default_level(price, None) == 0.0

    def test_row_markup_is_escaped(self) -> None:
        from momentum.store.alerts import Alert

        alert = Alert(
            "alt_x", ALICE.owner.id, "<b>X</b>", "price_above", "price", "above", 1.0,  # type: ignore[union-attr]
            0.0, 0, True, ("webhook",), "USD", "EQUITY", "fired", 1, 1.2, self.NOW,
            self.NOW - 120_000, self.NOW - 60_000, None, 0,
        )  # fmt: skip
        markup = alerts_view.alert_row_html(alert, self.NOW)
        assert "&lt;b&gt;X&lt;/b&gt;" in markup
        assert "Triggered 2 min ago" in markup
        assert "In app · Webhook" in markup
        assert "Demo data" in markup
        failed = alerts_view.status_text(replace(alert, last_error="offline"), self.NOW)
        assert failed == "Couldn't check: offline · 1 min ago"
        pending = alerts_view.status_text(replace(alert, state="pending"), self.NOW)
        assert pending == "Waiting for the first check"
        armed = alerts_view.status_text(replace(alert, state="armed"), self.NOW)
        assert armed == "Waiting · checked 1 min ago"


def test_plain_markdown_keeps_text_verbatim() -> None:
    assert alerts_view.plain_markdown("A $1 *b* [c](d)") == r"A \$1 \*b\* \[c\]\(d\)"
    assert alerts_view.plain_markdown("plain words") == "plain words"


def test_toast_text_merges_several_notifications() -> None:
    from momentum.store.alerts import Event

    def event(message: str) -> Event:
        return Event("e", "o", None, "triggered", None, None, None, None, message, None, 0, None)

    assert alerts_view.toast_text([event("A.")]) == "A."
    assert alerts_view.toast_text([event("A."), event("B.")]) == (
        "B. And 1 more alert in the Alerts tab."
    )
    assert alerts_view.toast_text([event("A."), event("B."), event("C.")]).endswith(
        "And 2 more alerts in the Alerts tab."
    )
