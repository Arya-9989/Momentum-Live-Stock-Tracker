"""Persisted watchlists.

``WatchlistRepository`` is the interface the UI depends on; ``SqlWatchlistRepository``
implements it on SQLite or Postgres. Edits arrive as deltas (symbols added and
removed), applied in one transaction under the owner's row lock, so two tabs
editing the same list never undo each other and the size cap cannot be raced
past. Every write bumps ``version``; a session that sees a newer version than
the one it loaded reloads the list.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from momentum import config, data
from momentum.store.db import Database, StoreError, Tx
from momentum.store.owners import DEFAULT_MAX_DEVICES, Owner, ensure_owner, last_seen, touch
from momentum.store.schema import now_ms


@dataclass(frozen=True, slots=True)
class Watchlist:
    symbols: tuple[str, ...]
    version: int
    last_seen_at: int | None = None


@dataclass(frozen=True, slots=True)
class WatchlistChange:
    """The list after an edit, plus the symbols the size cap turned away."""

    watchlist: Watchlist
    over_limit: tuple[str, ...] = field(default=())


class WatchlistRepository(Protocol):
    def get(self, owner: Owner) -> Watchlist | None:
        """The saved list, or None if this owner has never saved one."""
        ...

    def apply(
        self,
        owner: Owner,
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
        base: Sequence[str] = (),
    ) -> WatchlistChange:
        """Remove then add symbols. ``base`` seeds a first save (what the owner saw)."""
        ...

    def touch(self, owner: Owner, last_seen_at: int | None) -> None: ...


def clean_symbols(symbols: Iterable[str]) -> list[str]:
    """Normalised, de-duplicated symbols; anything invalid raises ``InvalidSymbolError``."""
    out: list[str] = []
    for raw in symbols:
        symbol = data.normalize_symbol(raw)
        if symbol not in out:
            out.append(symbol)
    return out


class SqlWatchlistRepository:
    def __init__(
        self,
        db: Database,
        *,
        limit: int = config.MAX_WATCHLIST,
        max_devices: int = DEFAULT_MAX_DEVICES,
    ) -> None:
        self._db = db
        self.limit = limit
        self._max_devices = max_devices

    def get(self, owner: Owner) -> Watchlist | None:
        with self._db.transaction(write=False) as tx:
            return self._read(tx, owner)

    def _read(self, tx: Tx, owner: Owner) -> Watchlist | None:
        row = tx.one("SELECT version FROM watchlists WHERE owner_id = ?", (owner.id,))
        if row is None:
            return None
        symbols = tx.all(
            "SELECT symbol FROM watchlist_items WHERE owner_id = ? ORDER BY position, symbol",
            (owner.id,),
        )
        return Watchlist(
            symbols=tuple(str(s) for (s,) in symbols),
            version=int(row[0]),
            last_seen_at=last_seen(tx, owner),
        )

    def apply(
        self,
        owner: Owner,
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
        base: Sequence[str] = (),
    ) -> WatchlistChange:
        additions, removals = clean_symbols(add), clean_symbols(remove)
        seed = clean_symbols(base)[: self.limit]
        now = now_ms()
        over: list[str] = []
        with self._db.transaction() as tx:
            ensure_owner(tx, owner, max_devices=self._max_devices)
            created = tx.execute(
                "INSERT INTO watchlists (owner_id, version, updated_at) VALUES (?, 0, ?) "
                "ON CONFLICT (owner_id) DO NOTHING",
                (owner.id, now),
            )
            # Row lock (Postgres); SQLite's BEGIN IMMEDIATE already serialises writers.
            tx.one(f"SELECT version FROM watchlists WHERE owner_id = ?{tx.for_update}", (owner.id,))
            if created == 1:
                for position, symbol in enumerate(seed):
                    self._insert(tx, owner, symbol, position, now)
            for symbol in removals:
                tx.execute(
                    "DELETE FROM watchlist_items WHERE owner_id = ? AND symbol = ?",
                    (owner.id, symbol),
                )
            for symbol in additions:
                if tx.one(
                    "SELECT 1 FROM watchlist_items WHERE owner_id = ? AND symbol = ?",
                    (owner.id, symbol),
                ):
                    continue
                count, top = tx.one(
                    "SELECT COUNT(*), COALESCE(MAX(position), -1) FROM watchlist_items "
                    "WHERE owner_id = ?",
                    (owner.id,),
                ) or (0, -1)
                if int(count) >= self.limit:
                    over.append(symbol)
                    continue
                self._insert(tx, owner, symbol, int(top) + 1, now)
            tx.execute(
                "UPDATE watchlists SET version = version + 1, updated_at = ? WHERE owner_id = ?",
                (now, owner.id),
            )
            saved = self._read(tx, owner)
        if saved is None:  # pragma: no cover - written in this very transaction
            msg = "The watchlist vanished while it was being saved."
            raise StoreError(msg)
        return WatchlistChange(saved, tuple(over))

    @staticmethod
    def _insert(tx: Tx, owner: Owner, symbol: str, position: int, now: int) -> None:
        tx.execute(
            "INSERT INTO watchlist_items (owner_id, symbol, position, added_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (owner_id, symbol) DO NOTHING",
            (owner.id, symbol, position, now),
        )

    def touch(self, owner: Owner, last_seen_at: int | None) -> None:
        touch(self._db, owner, last_seen_at)
