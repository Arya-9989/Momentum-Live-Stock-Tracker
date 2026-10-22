"""Alert evaluation and the alerts repository, on SQLite and Postgres.

A ``SeriesObserver`` replays deterministic price series one observation per
cycle, so every expectation below can be checked by hand.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from momentum import indicators as ind
from momentum.alerts import engine, rules
from momentum.alerts.engine import Observation
from momentum.data import DataSourceError, InvalidSymbolError, Quote
from momentum.store.alerts import (
    MAX_ATTEMPTS,
    AlertLimitError,
    NewAlert,
    SqlAlertRepository,
)
from momentum.store.db import Database, StoreError
from momentum.store.owners import Owner
from momentum.store.schema import now_ms
from tests.test_store import run_concurrently

ALICE = Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device")
MALLORY = Owner("dev_MMMMMMMMMMMMMMMMMMMMMM", "device")
ACCOUNT = Owner("acct_" + "b" * 40, "account")


class SeriesObserver:
    """Replays ``prices[symbol]``: the n-th call to ``advance`` moves every symbol on."""

    def __init__(self, prices: dict[str, list[float]], start: int = 1_700_000_000_000) -> None:
        self.prices = prices
        self.start = start
        self.tick = 0
        self.fail: set[str] = set()
        self.calls = 0

    def advance(self) -> None:
        self.tick += 1

    def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> Observation:
        self.calls += 1
        if symbol in self.fail:
            raise DataSourceError("Could not reach Yahoo Finance.")
        price = self.prices[symbol][self.tick]
        readings: dict[str, float | None] = dict.fromkeys(metrics)
        readings["price"] = price
        return Observation(readings, self.start + self.tick * 60_000)


@pytest.fixture
def repo(database: Database) -> SqlAlertRepository:
    return SqlAlertRepository(database)


def make(repo: SqlAlertRepository, owner: Owner = ALICE, **kwargs: object) -> str:
    fields: dict[str, object] = {
        "symbol": "AAPL",
        "condition": "price_above",
        "threshold": 200.0,
        "demo": True,
        "currency": "USD",
        "quote_type": "EQUITY",
        "cooldown_s": 0,
    }
    fields.update(kwargs)
    return repo.create(owner, NewAlert(**fields)).id  # type: ignore[arg-type]


def cycles(repo: SqlAlertRepository, observer: SeriesObserver, count: int) -> list[str]:
    """Run ``count`` cycles; returns the messages notified, in order."""
    messages: list[str] = []
    for _ in range(count):
        messages += [e.message for e in engine.evaluate(repo, observer).events]
        observer.advance()
    return messages


# --------------------------------------------------------------------------- evaluation


class TestEvaluation:
    def test_price_series_fires_once_per_cross(self, repo: SqlAlertRepository) -> None:
        make(repo)
        series = [190.0, 199.0, 201.0, 199.5, 202.0, 198.0, 203.5]
        observer = SeriesObserver({"AAPL": series})
        messages = cycles(repo, observer, len(series))
        # 201 crosses; 199.5 stays inside the 1.00 band (200 * 0.5%), so 202 is quiet;
        # 198 retreats past 199.00 and re-arms; 203.5 crosses again.
        assert messages == [
            "AAPL rose above $200.00 (now $201.00).",
            "AAPL rose above $200.00 (now $203.50).",
        ]
        events = repo.events(ALICE)
        assert [e.value for e in events] == [203.5, 201.0]
        assert all(e.read_at is None for e in events)

    def test_one_observation_per_symbol_per_cycle(self, repo: SqlAlertRepository) -> None:
        make(repo, threshold=200.0)
        make(repo, condition="price_below", threshold=150.0)
        make(repo, MALLORY, threshold=250.0)
        observer = SeriesObserver({"AAPL": [190.0]})
        engine.evaluate(repo, observer)
        assert observer.calls == 1

    def test_restart_mid_series_continues_without_duplicates(self, database: Database) -> None:
        make(SqlAlertRepository(database))
        series = [190.0, 201.0, 202.0, 203.0, 198.0, 201.0]
        observer = SeriesObserver({"AAPL": series})
        first = cycles(SqlAlertRepository(database), observer, 3)
        # "Restart": a fresh repository (and worker) that also re-evaluates the last
        # observation it saw before the crash.
        observer.tick -= 1
        second = cycles(SqlAlertRepository(database), observer, 4)
        assert first == ["AAPL rose above $200.00 (now $201.00)."]
        assert second == ["AAPL rose above $200.00 (now $201.00)."]  # the 6th value, re-armed
        assert len(SqlAlertRepository(database).events(ALICE)) == 2

    def test_concurrent_evaluators_notify_once(self, database: Database) -> None:
        make(SqlAlertRepository(database))
        observer = SeriesObserver({"AAPL": [190.0, 201.0]})
        engine.evaluate(SqlAlertRepository(database), observer)
        observer.advance()
        results = run_concurrently(
            6, lambda _: engine.evaluate(SqlAlertRepository(database), observer)
        )
        fired = sum(len(r.events) for r in results)
        assert fired == 1
        assert len(SqlAlertRepository(database).events(ALICE)) == 1

    def test_stale_state_cannot_fire_twice(self, repo: SqlAlertRepository) -> None:
        alert_id = make(repo)
        observer = SeriesObserver({"AAPL": [190.0, 201.0]})
        engine.evaluate(repo, observer)
        stale = repo.get(alert_id)
        assert stale is not None
        observer.advance()
        engine.evaluate(repo, observer)
        # A second worker still holding the pre-cross row tries to apply the same step.
        result = rules.step(stale.rule_state, 201.0, observer.start + 60_000)
        assert result.notify
        assert repo.apply_step(stale, result, message="duplicate") is None
        assert [e.message for e in repo.events(ALICE)] == ["AAPL rose above $200.00 (now $201.00)."]

    def test_failures_are_recorded_and_change_nothing(self, repo: SqlAlertRepository) -> None:
        alert_id = make(repo)
        observer = SeriesObserver({"AAPL": [190.0, 201.0]})
        observer.fail.add("AAPL")
        stats = engine.evaluate(repo, observer)
        assert stats.failures == 1
        alert = repo.get(alert_id)
        assert alert is not None
        assert alert.state == "pending"
        assert alert.last_error == "Could not reach Yahoo Finance."
        observer.fail.clear()
        engine.evaluate(repo, observer)
        alert = repo.get(alert_id)
        assert alert is not None
        assert alert.last_error is None
        assert alert.last_checked_at is not None

    def test_deleted_alert_keeps_its_notifications(self, repo: SqlAlertRepository) -> None:
        alert_id = make(repo)
        cycles(repo, SeriesObserver({"AAPL": [190.0, 201.0]}), 2)
        assert repo.delete(ALICE, alert_id)
        events = repo.events(ALICE)
        assert len(events) == 1
        assert events[0].alert_id is None


# --------------------------------------------------------------------------- repository


class TestRepository:
    def test_create_validates(self, repo: SqlAlertRepository) -> None:
        with pytest.raises(rules.RuleError, match="Unknown"):
            make(repo, condition="moon")
        with pytest.raises(rules.RuleError, match="above zero"):
            make(repo, threshold=-1.0)
        with pytest.raises(InvalidSymbolError):
            make(repo, symbol="'; DROP TABLE alerts; --")
        with pytest.raises(rules.RuleError, match="negative"):
            make(repo, cooldown_s=-1)
        assert repo.for_owner(ALICE) == []

    def test_crossover_and_hysteresis_are_stored(self, repo: SqlAlertRepository) -> None:
        make(repo, condition="sma_50_200_up", threshold=999.0)
        make(repo, condition="price_below", threshold=80.0)
        cross, price = repo.for_owner(ALICE)
        assert (cross.metric, cross.threshold, cross.hysteresis) == ("sma_gap:50:200", 0.0, 0.25)
        assert price.hysteresis == pytest.approx(0.4)

    def test_cap_per_owner_holds_under_concurrency(self, database: Database) -> None:
        repo = SqlAlertRepository(database, limit=5)
        results = run_concurrently(
            12,
            lambda i: _try_create(repo, i),
        )
        assert sum(1 for r in results if r) == 5
        assert len(repo.for_owner(ALICE)) == 5
        with pytest.raises(AlertLimitError, match="up to 5 alerts"):
            make(repo)

    def test_owners_cannot_touch_each_other_s_alerts(self, repo: SqlAlertRepository) -> None:
        alert_id = make(repo)
        assert not repo.delete(MALLORY, alert_id)  # guessing an id is not enough
        assert repo.for_owner(MALLORY) == []
        assert [a.id for a in repo.for_owner(ALICE)] == [alert_id]

    def test_read_state(self, repo: SqlAlertRepository) -> None:
        make(repo)
        cycles(repo, SeriesObserver({"AAPL": [190.0, 201.0]}), 2)
        assert repo.unread_count(ALICE) == 1
        assert len(repo.unread_since(ALICE, 0)) == 1
        assert repo.unread_since(ALICE, now_ms() + 1000) == []
        assert repo.mark_read(ALICE) == 1
        assert repo.unread_count(ALICE) == 0
        assert repo.unread_count(MALLORY) == 0

    def test_channels(self, repo: SqlAlertRepository) -> None:
        with pytest.raises(StoreError, match="signed-in accounts"):
            repo.set_email(ALICE, "victim@example.com")  # devices can never mail anyone
        repo.set_email(ACCOUNT, "me@example.com")
        repo.set_email(ACCOUNT, "me@example.com")  # unchanged: no write
        secret = repo.set_webhook(ACCOUNT, "https://hooks.example.com/x")
        assert secret.startswith("whsec_")
        assert len(secret) > 40
        channels = repo.channels(ACCOUNT)
        assert (channels.email, channels.webhook_url, channels.webhook_secret) == (
            "me@example.com",
            "https://hooks.example.com/x",
            secret,
        )
        rotated = repo.rotate_webhook_secret(ACCOUNT)
        assert rotated is not None
        assert rotated != secret
        repo.remove_webhook(ACCOUNT)
        assert repo.channels(ACCOUNT).webhook_url is None
        assert repo.channels(ACCOUNT).email == "me@example.com"
        assert repo.rotate_webhook_secret(ACCOUNT) is None


def _try_create(repo: SqlAlertRepository, i: int) -> bool:
    try:
        make(repo, threshold=100.0 + i)
    except AlertLimitError:
        return False
    return True


# --------------------------------------------------------------------------- outbox


class TestOutbox:
    def fire(self, repo: SqlAlertRepository, channels: tuple[str, ...]) -> None:
        make(repo, ACCOUNT, channels=channels)
        cycles(repo, SeriesObserver({"AAPL": [190.0, 201.0]}), 2)

    def test_only_configured_channels_are_queued(self, repo: SqlAlertRepository) -> None:
        repo.set_email(ACCOUNT, "me@example.com")
        self.fire(repo, ("email", "webhook"))  # no webhook configured
        jobs = repo.claim_due()
        assert [j.channel for j in jobs] == ["email"]
        assert jobs[0].target.email == "me@example.com"
        assert jobs[0].event.message.startswith("AAPL rose above")

    def test_each_delivery_is_claimed_once(self, database: Database) -> None:
        repo = SqlAlertRepository(database)
        repo.set_email(ACCOUNT, "me@example.com")
        repo.set_webhook(ACCOUNT, "https://hooks.example.com/x")
        self.fire(repo, ("email", "webhook"))
        claimed = run_concurrently(5, lambda _: SqlAlertRepository(database).claim_due())
        ids = [job.id for batch in claimed for job in batch]
        assert len(ids) == 2
        assert len(set(ids)) == 2

    def test_backoff_then_give_up(self, repo: SqlAlertRepository) -> None:
        repo.set_email(ACCOUNT, "me@example.com")
        self.fire(repo, ("email",))
        now = now_ms()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            (job,) = repo.claim_due(now=now)
            assert job.attempts == attempt
            assert repo.claim_due(now=now) == []  # leased
            retried = repo.mark_failed(job, "SMTP down")
            assert retried == (attempt < MAX_ATTEMPTS)
            now += 3 * 60 * 60 * 1000  # past the longest backoff
        assert repo.claim_due(now=now) == []
        (status,) = repo.delivery_status(job.event.id)
        assert status == ("email", "failed", MAX_ATTEMPTS, "SMTP down")

    def test_retry_waits_for_its_backoff(self, repo: SqlAlertRepository) -> None:
        repo.set_email(ACCOUNT, "me@example.com")
        self.fire(repo, ("email",))
        (job,) = repo.claim_due()
        repo.mark_failed(job, "timeout")
        assert repo.claim_due(now=now_ms() + 10_000) == []
        assert len(repo.claim_due(now=now_ms() + 31_000)) == 1

    def test_a_crashed_sender_s_lease_expires(self, repo: SqlAlertRepository) -> None:
        repo.set_email(ACCOUNT, "me@example.com")
        self.fire(repo, ("email",))
        (job,) = repo.claim_due()
        # The worker died mid-send: no mark_sent, no mark_failed.
        assert repo.claim_due(now=now_ms() + 30_000) == []
        (again,) = repo.claim_due(now=now_ms() + 61_000)
        assert again.id == job.id
        assert again.attempts == 2
        repo.mark_sent(again.id)
        assert repo.delivery_status(job.event.id)[0][1] == "sent"

    def test_permanent_failures_are_not_retried(self, repo: SqlAlertRepository) -> None:
        repo.set_email(ACCOUNT, "me@example.com")
        self.fire(repo, ("email",))
        (job,) = repo.claim_due()
        assert not repo.mark_failed(job, "refused", permanent=True)
        assert repo.delivery_status(job.event.id)[0][1] == "failed"

    def test_test_events_go_to_one_channel(self, repo: SqlAlertRepository) -> None:
        repo.set_webhook(ACCOUNT, "https://hooks.example.com/x")
        event = repo.send_test(ACCOUNT, "webhook")
        (job,) = repo.claim_due()
        assert job.event.id == event.id
        assert job.event.kind == "test"
        assert job.target.webhook_secret is not None

    def test_prune(self, database: Database) -> None:
        repo = SqlAlertRepository(database)
        repo.set_email(ACCOUNT, "me@example.com")
        self.fire(repo, ("email",))
        (job,) = repo.claim_due()
        repo.mark_sent(job.id)
        later = now_ms() + 100 * 24 * 60 * 60 * 1000
        events, deliveries = repo.prune(now=later)
        assert (events, deliveries) == (1, 1)
        assert repo.events(ACCOUNT) == []

    def test_prune_caps_events_per_owner(
        self, database: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from momentum.store import alerts as alerts_store

        monkeypatch.setattr(alerts_store, "MAX_EVENTS_PER_OWNER", 3)
        repo = SqlAlertRepository(database)
        for _ in range(5):
            repo.send_test(ALICE, "webhook")  # no webhook: only the in-app event
        events, _ = repo.prune()
        assert events == 2
        assert len(repo.events(ALICE)) == 3


# --------------------------------------------------------------------------- metrics


def closes(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2025-01-01", periods=len(values)))


class TestMetrics:
    QUOTE = Quote("AAPL", "Apple", currency="USD", price=210.0, previous_close=200.0)

    def test_values_from_quote_and_closes(self) -> None:
        series = closes([float(v) for v in np.linspace(100, 300, 260)])
        values = engine.metric_values(
            ["price", "change_pct", "rsi14", "sma_gap:50:200", "nope"], self.QUOTE, series
        )
        assert values["price"] == 210.0
        assert values["change_pct"] == pytest.approx(5.0)
        assert values["rsi14"] == 100.0  # only gains
        sma50 = float(ind.sma(series, 50).iloc[-1])
        sma200 = float(ind.sma(series, 200).iloc[-1])
        assert values["sma_gap:50:200"] == pytest.approx((sma50 / sma200 - 1) * 100)
        assert values["nope"] is None

    def test_not_enough_history_is_none(self) -> None:
        values = engine.metric_values(["rsi14", "sma_gap:50:200"], self.QUOTE, closes([1.0] * 10))
        assert values == {"rsi14": None, "sma_gap:50:200": None}
        assert engine.metric_values(["rsi14"], self.QUOTE, None) == {"rsi14": None}
        assert engine.sma_gap(closes([]), 20, 50) is None

    def test_golden_cross_series(self, repo: SqlAlertRepository) -> None:
        """A downtrend that turns up: SMA 20 crosses SMA 50 exactly once."""
        prices = [float(v) for v in np.r_[np.linspace(200, 120, 120), np.linspace(120, 220, 120)]]
        make(repo, condition="sma_20_50_up", threshold=0.0)

        class Daily:
            day = 60

            def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> Observation:
                window = closes(prices[: self.day])
                return Observation(
                    engine.metric_values(metrics, self.QUOTE_FOR(window), window),
                    1_700_000_000_000 + self.day * 86_400_000,
                )

            @staticmethod
            def QUOTE_FOR(window: pd.Series) -> Quote:  # noqa: N802
                return Quote("AAPL", "Apple", price=float(window.iloc[-1]))

        daily = Daily()
        messages: list[str] = []
        for day in range(60, 240):
            daily.day = day
            messages += [e.message for e in engine.evaluate(repo, daily).events]
        assert len(messages) == 1
        assert messages[0].startswith("AAPL: SMA 20 crosses above SMA 50 (gap now +")

    def test_market_observer_on_demo_data(self) -> None:
        observation = engine.MarketObserver().observe(
            "AAPL", frozenset({"price", "change_pct", "rsi14", "sma_gap:50:200"}), True
        )
        assert all(v is not None and math.isfinite(v) for v in observation.readings.values())
        assert observation.observed_at > 0

    def test_market_observer_falls_back_to_bars_for_indicators(self) -> None:
        class NoInfo:
            def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
                return pd.DataFrame(
                    {"Close": np.linspace(10, 20, 300)},
                    index=pd.bdate_range("2024-01-01", periods=300),
                )

            def info(self, symbol: str) -> dict[str, object]:
                raise DataSourceError("info down")

        observer = engine.MarketObserver(lambda demo: NoInfo())
        reading = observer.observe("X", frozenset({"rsi14"}), False)
        assert reading.readings["rsi14"] == 100.0
        with pytest.raises(DataSourceError):
            observer.observe("X", frozenset({"price"}), False)


MESSAGE_CASES = [
    (("price_above", 200.0, "USD", "EQUITY"), 201.3, "AAPL rose above $200.00 (now $201.30)."),
    (("price_below", 120.0, "GBp", "EQUITY"), 119.5, "AAPL fell below 120.00p (now 119.50p)."),
    (
        ("price_above", 25_000.0, "INR", "INDEX"),
        25_100.0,
        "AAPL rose above 25,000.00 (now 25,100.00).",
    ),
    (("change_above", 3.0, "USD", "EQUITY"), 3.42, "AAPL is up more than +3.0% (now +3.42%)."),
    (("change_below", -3.0, "USD", "EQUITY"), -4.1, "AAPL is down more than 3.0% (now -4.10%)."),
    (("rsi_above", 70.0, "USD", "EQUITY"), 71.26, "AAPL RSI (14) rose above 70 (now 71.3)."),
]


class TestMessages:
    @pytest.mark.parametrize(("alert", "value", "expected"), MESSAGE_CASES)
    def test_trigger_message(
        self,
        repo: SqlAlertRepository,
        alert: tuple[str, float, str, str],
        value: float,
        expected: str,
    ) -> None:
        condition, threshold, currency, quote_type = alert
        saved = repo.get(
            make(
                repo,
                condition=condition,
                threshold=threshold,
                currency=currency,
                quote_type=quote_type,
            )
        )
        assert saved is not None
        assert engine.trigger_message(saved, value) == expected

    def test_describe(self, repo: SqlAlertRepository) -> None:
        alert = repo.get(make(repo, condition="sma_50_200_down"))
        assert alert is not None
        assert engine.describe(alert) == "SMA 50 crosses below SMA 200 (death cross)"
        assert engine.describe_value(alert, None) == "—"
        price = repo.get(make(repo, threshold=99.5))
        assert price is not None
        assert engine.describe(price) == "Price crosses above $99.50"


@pytest.fixture(autouse=True)
def _no_threads_left() -> Iterator[None]:
    before = threading.active_count()
    yield
    assert threading.active_count() <= before + 1
