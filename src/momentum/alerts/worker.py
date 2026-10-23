"""The alert loop: evaluate, deliver, and once a day tidy up.

It runs as a thread in the app process (started by ``serve.py``'s lifespan, or
lazily by the script under plain ``streamlit run``) or as its own process
(``python -m momentum.alerts run``). Correctness never depends on there being
exactly one loop; see docs/adr/0002-alerts.md.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from momentum import settings as settings_mod
from momentum.alerts import delivery, engine
from momentum.store import Store, StoreError, get_store
from momentum.store.alerts import SqlAlertRepository
from momentum.store.owners import prune_devices

logger = logging.getLogger(__name__)

MAINTENANCE_EVERY_S: Final = 24 * 60 * 60
MAX_BACKOFF_S: Final = 15 * 60


def senders_for(config: settings_mod.Settings) -> dict[str, delivery.Sender]:
    senders: dict[str, delivery.Sender] = {
        "webhook": delivery.WebhookSender(allow_private=config.webhook_allow_private)
    }
    if config.smtp is not None:
        senders["email"] = delivery.EmailSender(config.smtp)
    return senders


@dataclass
class Worker:
    """One loop. ``run_once`` is a full cycle; ``start``/``stop`` manage the thread."""

    store_factory: Callable[[], Store] = get_store
    observer: engine.Observer = field(default_factory=engine.MarketObserver)
    config: settings_mod.Settings = field(default_factory=settings_mod.load)
    senders: dict[str, delivery.Sender] | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _last_maintenance: float | None = None  # monotonic time of the last run
    failures: int = 0

    def run_once(self) -> tuple[engine.CycleStats, delivery.DispatchStats]:
        store = self.store_factory()
        repo: SqlAlertRepository = store.alerts
        cycle = engine.evaluate(repo, self.observer)
        sent = delivery.dispatch(repo, self.senders or senders_for(self.config))
        last = self._last_maintenance
        if last is None or time.monotonic() - last >= MAINTENANCE_EVERY_S:
            self.maintain(store)
        return cycle, sent

    def maintain(self, store: Store) -> None:
        events, deliveries = store.alerts.prune()
        devices = prune_devices(store.db, self.config.device_retention_days)
        self._last_maintenance = time.monotonic()
        logger.info(
            "Maintenance: pruned %d events, %d deliveries, %d stale devices",
            events,
            deliveries,
            devices,
        )

    def _delay(self) -> float:
        """The interval, with jitter so replicas spread out, and backoff after failures."""
        base = float(self.config.alert_interval_s)
        if self.failures:
            base = min(base * 2**self.failures, MAX_BACKOFF_S)
        return base * random.uniform(0.9, 1.1)  # jitter, not security

    def loop(self) -> None:
        logger.info("Alert worker started (every %ds)", self.config.alert_interval_s)
        while not self._stop.is_set():
            try:
                cycle, sent = self.run_once()
                self.failures = 0
                if cycle.events or sent.sent or sent.failed:
                    logger.info(
                        "Alerts: %d checked, %d fired; deliveries %d sent, %d retried, %d failed",
                        cycle.alerts,
                        len(cycle.events),
                        sent.sent,
                        sent.retried,
                        sent.failed,
                    )
            except (StoreError, OSError) as exc:
                self.failures += 1
                logger.warning("Alert cycle failed (%d in a row): %r", self.failures, exc)
            except Exception:  # a bug must not kill the loop for good
                self.failures += 1
                logger.exception("Alert cycle crashed (%d in a row)", self.failures)
            self._stop.wait(self._delay())
        logger.info("Alert worker stopped")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.loop, name="momentum-alerts", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Ask the loop to stop after its current step (safe from a signal handler)."""
        self._stop.set()

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the loop to finish its current step and wait for it."""
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


_running: dict[str, Worker] = {}  # at most one entry: the in-process loop
_lock = threading.Lock()


def ensure_started(config: settings_mod.Settings | None = None) -> Worker | None:
    """Start the in-process loop once per process (unless it runs elsewhere)."""
    config = config or settings_mod.load()
    if config.alert_worker != "auto":
        return None
    with _lock:
        current = _running.get("worker")
        if current is None or not current.running:
            current = Worker(config=config)
            current.start()
            _running["worker"] = current
        return current


def stop_running(timeout: float = 10.0) -> None:
    with _lock:
        current = _running.pop("worker", None)
    if current is not None:
        current.stop(timeout)
