"""Persisted alerts, their events, the delivery outbox and delivery channels.

Every state change is a compare-and-set on the row the caller read, so several
evaluators (the in-app worker, a separate worker process, another replica) can
run at once without notifying twice. See docs/adr/0002-alerts.md.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from momentum.alerts import rules
from momentum.store.db import Database, StoreError, Tx
from momentum.store.owners import DEFAULT_MAX_DEVICES, Owner, ensure_owner
from momentum.store.schema import now_ms

Channel = Literal["email", "webhook"]
CHANNELS: Final[tuple[Channel, ...]] = ("email", "webhook")

LEASE_MS: Final = 60_000
MAX_ATTEMPTS: Final = 6
BACKOFF_MS: Final = (30_000, 120_000, 480_000, 1_920_000, 7_200_000)
EVENT_RETENTION_MS: Final = 90 * 24 * 60 * 60 * 1000
DELIVERY_RETENTION_MS: Final = 30 * 24 * 60 * 60 * 1000
MAX_EVENTS_PER_OWNER: Final = 200

_ALERT_COLUMNS: Final = (
    "id, owner_id, symbol, condition, metric, direction, threshold, hysteresis, cooldown_s, "
    "demo, channels, currency, quote_type, state, fire_count, last_value, last_observed_at, "
    "last_fired_at, last_checked_at, last_error, created_at"
)


class AlertLimitError(StoreError):
    """The owner already has the maximum number of alerts."""


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


@dataclass(frozen=True, slots=True)
class Alert:
    id: str
    owner_id: str
    symbol: str
    condition: str
    metric: str
    direction: rules.Direction
    threshold: float
    hysteresis: float
    cooldown_s: int
    demo: bool
    channels: tuple[Channel, ...]
    currency: str | None
    quote_type: str | None
    state: rules.State
    fire_count: int
    last_value: float | None
    last_observed_at: int | None
    last_fired_at: int | None
    last_checked_at: int | None
    last_error: str | None
    created_at: int

    @property
    def rule_state(self) -> rules.AlertState:
        return rules.AlertState(
            direction=self.direction,
            threshold=self.threshold,
            hysteresis=self.hysteresis,
            cooldown_s=self.cooldown_s,
            state=self.state,
            last_value=self.last_value,
            last_observed_at=self.last_observed_at,
            last_fired_at=self.last_fired_at,
        )


def _alert(row: Sequence[Any]) -> Alert:
    (
        id_, owner_id, symbol, condition, metric, direction, threshold, hysteresis, cooldown,
        demo, channels, currency, quote_type, state, fire_count, last_value, last_observed_at,
        last_fired_at, last_checked_at, last_error, created_at,
    ) = row  # fmt: skip
    return Alert(
        id=str(id_),
        owner_id=str(owner_id),
        symbol=str(symbol),
        condition=str(condition),
        metric=str(metric),
        direction="above" if direction == "above" else "below",
        threshold=float(threshold),
        hysteresis=float(hysteresis),
        cooldown_s=int(cooldown),
        demo=bool(demo),
        channels=tuple(c for c in CHANNELS if c in str(channels).split(",")),
        currency=str(currency) if currency is not None else None,
        quote_type=str(quote_type) if quote_type is not None else None,
        state=state if state in ("pending", "armed", "fired") else "pending",
        fire_count=int(fire_count),
        last_value=float(last_value) if last_value is not None else None,
        last_observed_at=_int(last_observed_at),
        last_fired_at=_int(last_fired_at),
        last_checked_at=_int(last_checked_at),
        last_error=str(last_error) if last_error is not None else None,
        created_at=int(created_at),
    )


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class NewAlert:
    symbol: str
    condition: str
    threshold: float
    demo: bool
    channels: tuple[Channel, ...] = ()
    currency: str | None = None
    quote_type: str | None = None
    cooldown_s: int = rules.DEFAULT_COOLDOWN_S


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    owner_id: str
    alert_id: str | None
    kind: Literal["triggered", "test"]
    symbol: str | None
    condition: str | None
    threshold: float | None
    value: float | None
    message: str
    observed_at: int | None
    created_at: int
    read_at: int | None


_EVENT_COLUMNS: Final = (
    "id, owner_id, alert_id, kind, symbol, condition, threshold, value, message, observed_at, "
    "created_at, read_at"
)


def _event(row: Sequence[Any]) -> Event:
    (id_, owner_id, alert_id, kind, symbol, condition, threshold, value, message, observed_at,
     created_at, read_at) = row  # fmt: skip
    return Event(
        id=str(id_),
        owner_id=str(owner_id),
        alert_id=str(alert_id) if alert_id is not None else None,
        kind="test" if kind == "test" else "triggered",
        symbol=str(symbol) if symbol is not None else None,
        condition=str(condition) if condition is not None else None,
        threshold=float(threshold) if threshold is not None else None,
        value=float(value) if value is not None else None,
        message=str(message),
        observed_at=_int(observed_at),
        created_at=int(created_at),
        read_at=_int(read_at),
    )


@dataclass(frozen=True, slots=True)
class Channels:
    email: str | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    """A claimed delivery, with everything a sender needs."""

    id: str
    channel: Channel
    attempts: int
    event: Event
    target: Channels


class SqlAlertRepository:
    def __init__(
        self,
        db: Database,
        *,
        limit: int = rules.MAX_ALERTS_PER_OWNER,
        max_devices: int = DEFAULT_MAX_DEVICES,
    ) -> None:
        self._db = db
        self.limit = limit
        self._max_devices = max_devices

    # ------------------------------------------------------------------ alerts

    def create(self, owner: Owner, new: NewAlert) -> Alert:
        from momentum import data

        condition = rules.CONDITIONS_BY_KEY.get(new.condition)
        if condition is None:
            msg = "Unknown alert condition."
            raise rules.RuleError(msg)
        symbol = data.normalize_symbol(new.symbol)
        threshold = rules.validate_threshold(condition, new.threshold)
        if new.cooldown_s < 0:
            msg = "The cooldown cannot be negative."
            raise rules.RuleError(msg)
        channels = ",".join(c for c in CHANNELS if c in new.channels)
        now = now_ms()
        alert_id = new_id("alt")
        with self._db.transaction() as tx:
            ensure_owner(tx, owner, max_devices=self._max_devices)
            # Serialise this owner's writers so the cap cannot be raced past.
            tx.one(f"SELECT id FROM owners WHERE id = ?{tx.for_update}", (owner.id,))
            count = tx.one("SELECT COUNT(*) FROM alerts WHERE owner_id = ?", (owner.id,))
            if count is not None and int(count[0]) >= self.limit:
                msg = f"You can keep up to {self.limit} alerts. Delete one to add another."
                raise AlertLimitError(msg)
            tx.execute(
                "INSERT INTO alerts (id, owner_id, symbol, condition, metric, direction, "
                "threshold, hysteresis, cooldown_s, demo, channels, currency, quote_type, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    alert_id,
                    owner.id,
                    symbol,
                    condition.key,
                    condition.metric,
                    condition.direction,
                    threshold,
                    rules.hysteresis_for(condition, threshold),
                    new.cooldown_s,
                    1 if new.demo else 0,
                    channels,
                    (new.currency or "")[:8] or None,
                    (new.quote_type or "")[:32] or None,
                    now,
                ),
            )
            row = tx.one(f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE id = ?", (alert_id,))
        if row is None:  # pragma: no cover - inserted in this transaction
            msg = "The alert vanished while it was being saved."
            raise StoreError(msg)
        return _alert(row)

    def for_owner(self, owner: Owner) -> list[Alert]:
        with self._db.transaction(write=False) as tx:
            rows = tx.all(
                f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE owner_id = ? ORDER BY created_at, id",
                (owner.id,),
            )
        return [_alert(r) for r in rows]

    def delete(self, owner: Owner, alert_id: str) -> bool:
        """Delete one of ``owner``'s alerts (another owner's id deletes nothing)."""
        with self._db.transaction() as tx:
            return (
                tx.execute("DELETE FROM alerts WHERE id = ? AND owner_id = ?", (alert_id, owner.id))
                == 1
            )

    def active(self) -> list[Alert]:
        """Every alert, for the evaluation loop (grouped by the caller)."""
        with self._db.transaction(write=False) as tx:
            rows = tx.all(f"SELECT {_ALERT_COLUMNS} FROM alerts ORDER BY symbol, demo, id")
        return [_alert(r) for r in rows]

    def get(self, alert_id: str) -> Alert | None:
        with self._db.transaction(write=False) as tx:
            row = tx.one(f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE id = ?", (alert_id,))
        return _alert(row) if row else None

    # ------------------------------------------------------------------ evaluation

    def record_check(self, alert_ids: Sequence[str], checked_at: int, error: str | None) -> None:
        """Note when alerts were last checked, and why a check failed (one statement)."""
        if not alert_ids:
            return
        marks = ", ".join("?" for _ in alert_ids)
        with self._db.transaction() as tx:
            tx.execute(
                f"UPDATE alerts SET last_checked_at = ?, last_error = ? WHERE id IN ({marks})",
                (checked_at, error[:300] if error else None, *alert_ids),
            )

    def apply_step(
        self, alert: Alert, result: rules.Step, *, message: str | None = None
    ) -> Event | None:
        """Store ``result`` if ``alert`` is unchanged since it was read (compare-and-set).

        When the step notifies, the event (the in-app notification) and one outbox
        row per configured external channel are written in the same transaction.
        Returns the event, or None if nothing was notified or another evaluator
        got there first.
        """
        new = result.state
        now = now_ms()
        with self._db.transaction() as tx:
            changed = tx.execute(
                "UPDATE alerts SET state = ?, fire_count = fire_count + ?, last_value = ?, "
                "last_observed_at = ?, last_fired_at = ?, last_checked_at = ?, last_error = NULL "
                "WHERE id = ? AND state = ? AND fire_count = ? "
                "AND COALESCE(last_observed_at, -1) = ?",
                (
                    new.state,
                    1 if result.fired else 0,
                    new.last_value,
                    new.last_observed_at,
                    new.last_fired_at,
                    now,
                    alert.id,
                    alert.state,
                    alert.fire_count,
                    alert.last_observed_at if alert.last_observed_at is not None else -1,
                ),
            )
            if changed != 1 or not result.notify:
                return None
            event = Event(
                id=new_id("evt"),
                owner_id=alert.owner_id,
                alert_id=alert.id,
                kind="triggered",
                symbol=alert.symbol,
                condition=alert.condition,
                threshold=alert.threshold,
                value=new.last_value,
                message=(message or f"{alert.symbol} alert triggered.")[:500],
                observed_at=new.last_observed_at,
                created_at=now,
                read_at=None,
            )
            self._insert_event(tx, event, seq=alert.fire_count + 1)
            self._enqueue(tx, event, alert.channels, now)
        return event

    @staticmethod
    def _insert_event(tx: Tx, event: Event, *, seq: int) -> None:
        tx.execute(
            f"INSERT INTO alert_events ({_EVENT_COLUMNS}, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.owner_id,
                event.alert_id,
                event.kind,
                event.symbol,
                event.condition,
                event.threshold,
                event.value,
                event.message,
                event.observed_at,
                event.created_at,
                event.read_at,
                seq,
            ),
        )

    def _enqueue(self, tx: Tx, event: Event, channels: Sequence[Channel], now: int) -> None:
        target = self._channels(tx, event.owner_id)
        for channel in channels:
            configured = target.email if channel == "email" else target.webhook_url
            if not configured:
                continue
            tx.execute(
                "INSERT INTO alert_deliveries (id, event_id, owner_id, channel, next_attempt_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (event_id, channel) DO NOTHING",
                (new_id("dlv"), event.id, event.owner_id, channel, now, now),
            )

    def send_test(self, owner: Owner, channel: Channel) -> Event:
        """Queue a test event on one external channel (and show it in the app)."""
        now = now_ms()
        event = Event(
            id=new_id("evt"),
            owner_id=owner.id,
            alert_id=None,
            kind="test",
            symbol=None,
            condition=None,
            threshold=None,
            value=None,
            message=f"Test {channel} from Momentum. Delivery works.",
            observed_at=None,
            created_at=now,
            read_at=None,
        )
        with self._db.transaction() as tx:
            ensure_owner(tx, owner, max_devices=self._max_devices)
            self._insert_event(tx, event, seq=0)
            self._enqueue(tx, event, (channel,), now)
        return event

    # ------------------------------------------------------------------ events (in-app)

    def events(self, owner: Owner, *, limit: int = 20) -> list[Event]:
        with self._db.transaction(write=False) as tx:
            rows = tx.all(
                f"SELECT {_EVENT_COLUMNS} FROM alert_events WHERE owner_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (owner.id, max(1, min(limit, MAX_EVENTS_PER_OWNER))),
            )
        return [_event(r) for r in rows]

    def unread_since(self, owner: Owner, after: int) -> list[Event]:
        """Unread events created after ``after`` (ms), oldest first."""
        with self._db.transaction(write=False) as tx:
            rows = tx.all(
                f"SELECT {_EVENT_COLUMNS} FROM alert_events WHERE owner_id = ? "
                "AND read_at IS NULL AND created_at > ? ORDER BY created_at, id LIMIT 20",
                (owner.id, after),
            )
        return [_event(r) for r in rows]

    def unread_count(self, owner: Owner) -> int:
        with self._db.transaction(write=False) as tx:
            row = tx.one(
                "SELECT COUNT(*) FROM alert_events WHERE owner_id = ? AND read_at IS NULL",
                (owner.id,),
            )
        return int(row[0]) if row else 0

    def mark_read(self, owner: Owner) -> int:
        with self._db.transaction() as tx:
            return tx.execute(
                "UPDATE alert_events SET read_at = ? WHERE owner_id = ? AND read_at IS NULL",
                (now_ms(), owner.id),
            )

    # ------------------------------------------------------------------ channels

    @staticmethod
    def _channels(tx: Tx, owner_id: str) -> Channels:
        row = tx.one(
            "SELECT email, webhook_url, webhook_secret FROM alert_channels WHERE owner_id = ?",
            (owner_id,),
        )
        if row is None:
            return Channels()
        email, url, secret = row
        return Channels(
            email=str(email) if email else None,
            webhook_url=str(url) if url else None,
            webhook_secret=str(secret) if secret else None,
        )

    def channels(self, owner: Owner) -> Channels:
        with self._db.transaction(write=False) as tx:
            return self._channels(tx, owner.id)

    def _upsert_channels(self, tx: Tx, owner: Owner, channels: Channels) -> None:
        ensure_owner(tx, owner, max_devices=self._max_devices)
        tx.execute(
            "INSERT INTO alert_channels (owner_id, email, webhook_url, webhook_secret, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (owner_id) DO UPDATE SET email = excluded.email, "
            "webhook_url = excluded.webhook_url, webhook_secret = excluded.webhook_secret, "
            "updated_at = excluded.updated_at",
            (owner.id, channels.email, channels.webhook_url, channels.webhook_secret, now_ms()),
        )

    def set_email(self, owner: Owner, email: str | None) -> None:
        """Record the verified email of a signed-in owner (or clear it)."""
        if owner.kind != "account" and email is not None:
            msg = "Only signed-in accounts with a verified email can receive email alerts."
            raise StoreError(msg)
        with self._db.transaction() as tx:
            current = self._channels(tx, owner.id)
            if current.email == email:
                return
            self._upsert_channels(
                tx, owner, Channels(email, current.webhook_url, current.webhook_secret)
            )

    def set_webhook(self, owner: Owner, url: str) -> str:
        """Save the owner's webhook URL (already validated) and return a new signing secret."""
        secret = "whsec_" + secrets.token_urlsafe(32)
        with self._db.transaction() as tx:
            current = self._channels(tx, owner.id)
            self._upsert_channels(tx, owner, Channels(current.email, url, secret))
        return secret

    def rotate_webhook_secret(self, owner: Owner) -> str | None:
        secret = "whsec_" + secrets.token_urlsafe(32)
        with self._db.transaction() as tx:
            current = self._channels(tx, owner.id)
            if not current.webhook_url:
                return None
            self._upsert_channels(tx, owner, Channels(current.email, current.webhook_url, secret))
        return secret

    def remove_webhook(self, owner: Owner) -> None:
        with self._db.transaction() as tx:
            current = self._channels(tx, owner.id)
            if current.webhook_url:
                self._upsert_channels(tx, owner, Channels(current.email, None, None))

    # ------------------------------------------------------------------ outbox

    def claim_due(self, *, limit: int = 20, now: int | None = None) -> list[DeliveryJob]:
        """Lease up to ``limit`` due deliveries (each claim is a compare-and-set)."""
        now = now_ms() if now is None else now
        with self._db.transaction(write=False) as tx:
            candidates = tx.all(
                "SELECT id FROM alert_deliveries WHERE next_attempt_at <= ? AND "
                "(status = 'pending' OR (status = 'sending' AND lease_until < ?)) "
                "ORDER BY next_attempt_at, id LIMIT ?",
                (now, now, limit),
            )
        jobs: list[DeliveryJob] = []
        for (delivery_id,) in candidates:
            with self._db.transaction() as tx:
                claimed = tx.execute(
                    "UPDATE alert_deliveries SET status = 'sending', lease_until = ?, "
                    "attempts = attempts + 1 WHERE id = ? AND next_attempt_at <= ? AND "
                    "(status = 'pending' OR (status = 'sending' AND lease_until < ?))",
                    (now + LEASE_MS, delivery_id, now, now),
                )
                if claimed != 1:
                    continue  # another worker took it
                row = tx.one(
                    "SELECT d.channel, d.attempts, d.owner_id, "
                    + ", ".join(f"e.{c.strip()}" for c in _EVENT_COLUMNS.split(","))
                    + " FROM alert_deliveries d JOIN alert_events e ON e.id = d.event_id "
                    "WHERE d.id = ?",
                    (delivery_id,),
                )
                if row is None:  # pragma: no cover - the event cascades with the delivery
                    continue
                channel, attempts, owner_id = row[0], row[1], row[2]
                jobs.append(
                    DeliveryJob(
                        id=str(delivery_id),
                        channel="email" if channel == "email" else "webhook",
                        attempts=int(attempts),
                        event=_event(row[3:]),
                        target=self._channels(tx, str(owner_id)),
                    )
                )
        return jobs

    def mark_sent(self, delivery_id: str) -> None:
        with self._db.transaction() as tx:
            tx.execute(
                "UPDATE alert_deliveries SET status = 'sent', finished_at = ?, lease_until = NULL, "
                "last_error = NULL WHERE id = ? AND status = 'sending'",
                (now_ms(), delivery_id),
            )

    def mark_failed(self, job: DeliveryJob, error: str, *, permanent: bool = False) -> bool:
        """Schedule a retry with backoff, or give up; returns True if it will be retried."""
        now = now_ms()
        retry = not permanent and job.attempts < MAX_ATTEMPTS
        with self._db.transaction() as tx:
            if retry:
                delay = BACKOFF_MS[min(job.attempts - 1, len(BACKOFF_MS) - 1)]
                tx.execute(
                    "UPDATE alert_deliveries SET status = 'pending', next_attempt_at = ?, "
                    "lease_until = NULL, last_error = ? WHERE id = ? AND status = 'sending'",
                    (now + delay, error[:300], job.id),
                )
            else:
                tx.execute(
                    "UPDATE alert_deliveries SET status = 'failed', finished_at = ?, "
                    "lease_until = NULL, last_error = ? WHERE id = ? AND status = 'sending'",
                    (now, error[:300], job.id),
                )
        return retry

    def delivery_status(self, event_id: str) -> list[tuple[str, str, int, str | None]]:
        """``(channel, status, attempts, last_error)`` for each delivery of an event."""
        with self._db.transaction(write=False) as tx:
            rows = tx.all(
                "SELECT channel, status, attempts, last_error FROM alert_deliveries "
                "WHERE event_id = ? ORDER BY channel",
                (event_id,),
            )
        return [(str(c), str(s), int(a), str(e) if e else None) for c, s, a, e in rows]

    # ------------------------------------------------------------------ maintenance

    def prune(self, now: int | None = None) -> tuple[int, int]:
        """Drop old events (and per-owner overflow) and finished deliveries."""
        now = now_ms() if now is None else now
        with self._db.transaction() as tx:
            deliveries = tx.execute(
                "DELETE FROM alert_deliveries WHERE status IN ('sent', 'failed') AND "
                "finished_at < ?",
                (now - DELIVERY_RETENTION_MS,),
            )
            events = tx.execute(
                "DELETE FROM alert_events WHERE created_at < ? AND id NOT IN "
                "(SELECT event_id FROM alert_deliveries WHERE status IN ('pending', 'sending'))",
                (now - EVENT_RETENTION_MS,),
            )
            events += tx.execute(
                "DELETE FROM alert_events WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() "
                "OVER (PARTITION BY owner_id ORDER BY created_at DESC, id DESC) AS n "
                "FROM alert_events) ranked WHERE n > ?) AND id NOT IN (SELECT event_id FROM "
                "alert_deliveries WHERE status IN ('pending', 'sending'))",
                (MAX_EVENTS_PER_OWNER,),
            )
        return events, deliveries
