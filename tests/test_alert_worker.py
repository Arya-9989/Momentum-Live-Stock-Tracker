"""The alert loop: one full cycle, the thread's lifecycle, maintenance and the server lifespan."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

import pytest

from momentum import settings, store
from momentum.alerts import delivery, worker
from momentum.alerts.engine import CycleStats, Observation
from momentum.server import lifespan
from momentum.store import Store
from momentum.store.alerts import DeliveryJob, NewAlert
from momentum.store.owners import Owner
from momentum.store.watchlists import SqlWatchlistRepository

ACCOUNT = Owner("acct_" + "d" * 40, "account")
DEVICE = Owner("dev_DDDDDDDDDDDDDDDDDDDDDD", "device")


class Rising:
    """190, then 201, 202 ... one step per observation."""

    def __init__(self) -> None:
        self.n = 0

    def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> Observation:
        price = 190.0 if self.n == 0 else 200.0 + self.n
        self.n += 1
        return Observation({"price": price}, 1_700_000_000_000 + self.n * 60_000)


class Recorder:
    def __init__(self) -> None:
        self.jobs: list[DeliveryJob] = []

    def send(self, job: DeliveryJob) -> None:
        self.jobs.append(job)


@pytest.fixture
def app_store() -> Iterator[Store]:
    yield store.get_store()
    worker.stop_running()


def test_a_cycle_evaluates_and_delivers(app_store: Store) -> None:
    repo = app_store.alerts
    repo.set_email(ACCOUNT, "me@example.com")
    repo.create(ACCOUNT, NewAlert("AAPL", "price_above", 200.0, demo=True, channels=("email",)))
    email = Recorder()
    loop = worker.Worker(observer=Rising(), senders={"email": email})
    first, _ = loop.run_once()
    assert first.events == []
    second, sent = loop.run_once()
    assert len(second.events) == 1
    assert sent.sent == 1
    assert [j.event.id for j in email.jobs] == [second.events[0].id]


def test_maintenance_prunes_stale_devices_daily(
    app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    SqlWatchlistRepository(app_store.db).apply(DEVICE, add=["AAPL"])
    with app_store.db.transaction() as tx:
        tx.execute("UPDATE owners SET last_seen_at = 0")
    loop = worker.Worker(observer=Rising(), senders={})
    loop.run_once()
    assert SqlWatchlistRepository(app_store.db).get(DEVICE) is None
    # Not again until a day has passed.
    SqlWatchlistRepository(app_store.db).apply(DEVICE, add=["AAPL"])
    with app_store.db.transaction() as tx:
        tx.execute("UPDATE owners SET last_seen_at = 0")
    loop.run_once()
    assert SqlWatchlistRepository(app_store.db).get(DEVICE) is not None


def test_thread_starts_once_and_stops_cleanly(app_store: Store) -> None:
    config = settings.load({"MOMENTUM_ALERT_INTERVAL_S": "10"})
    first = worker.ensure_started(config)
    assert first is not None
    assert first.running
    assert worker.ensure_started(config) is first
    worker.stop_running(timeout=5)
    assert not first.running


@pytest.mark.parametrize("mode", ["external", "off"])
def test_other_modes_never_start_a_thread(mode: str) -> None:
    assert worker.ensure_started(settings.load({"MOMENTUM_ALERT_WORKER": mode})) is None


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(settings.SettingsError, match="MOMENTUM_ALERT_WORKER"):
        settings.load({"MOMENTUM_ALERT_WORKER": "sometimes"})


def test_loop_survives_failures_and_backs_off(
    app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []
    loop = worker.Worker(config=settings.load({"MOMENTUM_ALERT_INTERVAL_S": "10"}))

    def failing() -> tuple[CycleStats, delivery.DispatchStats]:
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise store.StoreUnavailableError("down")
        if len(calls) == 2:
            raise RuntimeError("a bug")
        loop._stop.set()
        return CycleStats(), delivery.DispatchStats()

    monkeypatch.setattr(loop, "run_once", failing)
    monkeypatch.setattr(loop, "_delay", lambda: 0.01)
    loop.loop()
    assert len(calls) == 3
    assert loop.failures == 0  # reset by the successful third cycle


def test_backoff_delay_grows_and_is_capped() -> None:
    loop = worker.Worker(config=settings.load({"MOMENTUM_ALERT_INTERVAL_S": "60"}))
    assert 54 <= loop._delay() <= 66
    loop.failures = 3
    assert 432 <= loop._delay() <= 528
    loop.failures = 30
    assert loop._delay() <= worker.MAX_BACKOFF_S * 1.1


def test_senders_follow_the_settings() -> None:
    assert set(worker.senders_for(settings.load({}))) == {"webhook"}
    with_smtp = settings.load(
        {"MOMENTUM_SMTP_HOST": "127.0.0.1", "MOMENTUM_SMTP_FROM": "Momentum <a@b.test>"}
    )
    senders = worker.senders_for(with_smtp)
    assert isinstance(senders["email"], delivery.EmailSender)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MOMENTUM_SMTP_HOST": "h"}, "MOMENTUM_SMTP_FROM"),
        ({"MOMENTUM_SMTP_HOST": "h", "MOMENTUM_SMTP_FROM": "no-at-sign"}, "MOMENTUM_SMTP_FROM"),
        ({"MOMENTUM_ALERT_INTERVAL_S": "1"}, "at least 10"),
    ],
)
def test_alert_settings_are_validated(env: dict[str, str], message: str) -> None:
    with pytest.raises(settings.SettingsError, match=message):
        settings.load(env)


def test_smtp_defaults() -> None:
    smtp = settings.load(
        {"MOMENTUM_SMTP_HOST": "mail", "MOMENTUM_SMTP_FROM": "a@b.test", "MOMENTUM_SMTP_SSL": "1"}
    ).smtp
    assert smtp is not None
    assert (smtp.port, smtp.ssl, smtp.starttls) == (465, True, False)
    plain = settings.load(
        {
            "MOMENTUM_SMTP_HOST": "mail",
            "MOMENTUM_SMTP_FROM": "a@b.test",
            "MOMENTUM_SMTP_STARTTLS": "0",
            "MOMENTUM_SMTP_PORT": "1025",
        }
    ).smtp
    assert plain is not None
    assert (plain.port, plain.starttls) == (1025, False)


def test_server_lifespan_starts_and_stops_the_loop(
    app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOMENTUM_ALERT_WORKER", "auto")
    monkeypatch.setenv("MOMENTUM_ALERT_INTERVAL_S", "600")
    seen: list[bool] = []

    async def run() -> None:
        async with lifespan(object()):
            current = worker._running.get("worker")
            seen.append(current is not None and current.running)

    asyncio.run(run())
    assert seen == [True]
    assert "worker" not in worker._running
