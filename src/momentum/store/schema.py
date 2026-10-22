"""Versioned schema migrations, one SQL file per step and dialect.

Files live in ``store/migrations/<dialect>/NNNN_name.sql`` and are applied in
order. Each applied file is recorded with its SHA-256 in ``schema_migrations``;
if an applied file is later edited, ``migrate`` refuses to run rather than
leave two databases with different schemas under the same version number.

The whole run is one transaction, serialised across processes (``BEGIN
IMMEDIATE`` on SQLite, a transaction-scoped advisory lock on Postgres), so
several replicas can start at once and the schema is migrated exactly once.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from importlib import resources
from typing import Final

from momentum.store.db import Database, Dialect, StoreError, Tx

_FILE_RE: Final = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
# Any constant works; it only has to be the same for every process of this app.
_PG_LOCK_ID: Final = 0x4D4F4D454E54  # "MOMENT"


class MigrationError(StoreError):
    """The migration history on disk and in the database disagree."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        # Line endings are normalised so a Windows checkout hashes like Linux.
        return hashlib.sha256(self.sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def load_migrations(dialect: Dialect) -> list[Migration]:
    """Every migration for ``dialect``, ordered by version; gaps are an error."""
    folder = resources.files("momentum.store") / "migrations" / dialect
    found: list[Migration] = []
    for entry in folder.iterdir():
        match = _FILE_RE.match(entry.name)
        if match is None:
            continue
        found.append(Migration(int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    expected = list(range(1, len(found) + 1))
    if [m.version for m in found] != expected:
        msg = f"Migration versions for {dialect} must run 1..n without gaps"
        raise MigrationError(msg)
    return found


def split_statements(sql: str) -> list[str]:
    """Statements of a migration file: ``--`` comments dropped, split on ``;``.

    Migration files are plain DDL with no semicolons inside literals, which
    keeps this simple and lets each statement run inside the shared transaction
    (``sqlite3.executescript`` would commit first).
    """
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


_CREATE_TABLE: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at BIGINT NOT NULL
)
"""


def _lock(tx: Tx) -> None:
    if tx.dialect == "postgres":
        tx.one("SELECT pg_advisory_xact_lock(?)", (_PG_LOCK_ID,))
    # SQLite: the BEGIN IMMEDIATE that opened this transaction is the lock.


def migrate(db: Database) -> list[str]:
    """Apply pending migrations; returns the names applied (empty when up to date)."""
    migrations = load_migrations(db.dialect)
    applied_now: list[str] = []
    with db.transaction(write=True) as tx:
        _lock(tx)
        tx.execute(_CREATE_TABLE)
        recorded = {
            int(version): (str(name), str(checksum))
            for version, name, checksum in tx.all(
                "SELECT version, name, checksum FROM schema_migrations"
            )
        }
        known = {m.version for m in migrations}
        unknown = sorted(set(recorded) - known)
        if unknown:
            msg = (
                f"The database has migrations this version of Momentum does not know "
                f"({unknown}); it was migrated by a newer release."
            )
            raise MigrationError(msg)
        for migration in migrations:
            if migration.version in recorded:
                name, checksum = recorded[migration.version]
                if checksum != migration.checksum:
                    msg = (
                        f"Migration {migration.version:04d}_{name} was edited after it was "
                        "applied. Add a new migration instead of changing an applied one."
                    )
                    raise MigrationError(msg)
                continue
            for statement in split_statements(migration.sql):
                tx.execute(statement)
            tx.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, now_ms()),
            )
            applied_now.append(f"{migration.version:04d}_{migration.name}")
    return applied_now


def current_version(db: Database) -> int:
    """Highest applied migration (0 for an empty database)."""
    with db.transaction(write=False) as tx:
        exists = (
            tx.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
            if tx.dialect == "sqlite"
            else tx.one("SELECT to_regclass('schema_migrations')::text")
        )
        if exists is None or exists[0] is None:
            return 0
        row = tx.one("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
    return int(row[0]) if row else 0


def now_ms() -> int:
    return time.time_ns() // 1_000_000
