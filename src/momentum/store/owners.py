"""Owners: the accounts and anonymous devices that saved data belongs to.

No owner row exists until an owner first saves something, so visitors who
only look write nothing. Device owners that have not been seen for the
retention period are pruned with everything they own (``ON DELETE CASCADE``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from momentum.store.db import Database, StoreError, Tx
from momentum.store.schema import now_ms

OwnerKind = Literal["account", "device"]
OWNER_ID_RE: Final = re.compile(r"^(acct|dev)_[A-Za-z0-9_-]{16,59}$")
TOUCH_EVERY_MS: Final = 24 * 60 * 60 * 1000
DEFAULT_MAX_DEVICES: Final = 100_000
_DAY_MS: Final = 24 * 60 * 60 * 1000


class CapacityError(StoreError):
    """The store refuses new anonymous owners (the configured ceiling is reached)."""


@dataclass(frozen=True, slots=True)
class Owner:
    """Who saved data belongs to. ``id`` is ``acct_...`` or ``dev_...``."""

    id: str
    kind: OwnerKind

    def __post_init__(self) -> None:
        prefix = "acct_" if self.kind == "account" else "dev_"
        if not OWNER_ID_RE.fullmatch(self.id) or not self.id.startswith(prefix):
            msg = f"invalid {self.kind} owner id"
            raise ValueError(msg)


def ensure_owner(tx: Tx, owner: Owner, *, max_devices: int = DEFAULT_MAX_DEVICES) -> bool:
    """Create the owner row if missing; returns True if it was created.

    New anonymous owners are refused once ``max_devices`` exist, so a script
    that discards its cookie on every request cannot grow the table without
    bound (signed-in accounts are never refused).
    """
    if tx.one("SELECT 1 FROM owners WHERE id = ?", (owner.id,)) is not None:
        return False
    if owner.kind == "device":
        row = tx.one("SELECT COUNT(*) FROM owners WHERE kind = 'device'")
        if row is not None and int(row[0]) >= max_devices:
            msg = "Saving is paused: this server has reached its limit for anonymous visitors."
            raise CapacityError(msg)
    now = now_ms()
    created = tx.execute(
        "INSERT INTO owners (id, kind, created_at, last_seen_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (id) DO NOTHING",
        (owner.id, owner.kind, now, now),
    )
    return created == 1


def touch(db: Database, owner: Owner, last_seen_at: int | None) -> None:
    """Record a visit, at most once a day (so a page view is not a write)."""
    now = now_ms()
    if last_seen_at is not None and now - last_seen_at < TOUCH_EVERY_MS:
        return
    with db.transaction() as tx:
        tx.execute(
            "UPDATE owners SET last_seen_at = ? WHERE id = ? AND last_seen_at < ?",
            (now, owner.id, now - TOUCH_EVERY_MS),
        )


def last_seen(tx: Tx, owner: Owner) -> int | None:
    row = tx.one("SELECT last_seen_at FROM owners WHERE id = ?", (owner.id,))
    return int(row[0]) if row is not None else None


def prune_devices(db: Database, retention_days: int) -> int:
    """Delete anonymous owners not seen for ``retention_days``; returns how many."""
    cutoff = now_ms() - retention_days * _DAY_MS
    with db.transaction() as tx:
        return tx.execute(
            "DELETE FROM owners WHERE kind = 'device' AND last_seen_at < ?", (cutoff,)
        )


def delete_owner(db: Database, owner: Owner) -> bool:
    """Delete an owner and everything it owns; returns False if there was nothing."""
    with db.transaction() as tx:
        return tx.execute("DELETE FROM owners WHERE id = ?", (owner.id,)) == 1
