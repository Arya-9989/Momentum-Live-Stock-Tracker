"""One transaction API over SQLite (standard library) and Postgres (psycopg 3).

Repositories write SQL once, with ``?`` placeholders, and run it on either
database; only DDL differs (see ``store/migrations``). Every unit of work is a
``transaction``: write transactions on SQLite start with ``BEGIN IMMEDIATE``,
so they take the database's write lock up front and never fail half-way with
"database is locked"; on Postgres a write that reads before it writes locks
the rows it reads with ``tx.for_update``.

Failures to reach or lock the database become ``StoreUnavailableError``, whose
message is safe to show.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Literal, Protocol

logger = logging.getLogger(__name__)

Dialect = Literal["sqlite", "postgres"]
Row = tuple[Any, ...]
Params = Sequence[object]

SQLITE_BUSY_TIMEOUT_S: Final = 5.0
PG_CONNECT_TIMEOUT_S: Final = 5
PG_STATEMENT_TIMEOUT_MS: Final = 5_000
PG_POOL_MAX: Final = 8


class StoreError(Exception):
    """Base class for storage failures; ``str(err)`` is safe to show to users."""


class StoreUnavailableError(StoreError):
    """The database could not be reached, or stayed locked past the timeout."""


class Tx(Protocol):
    """An open transaction."""

    dialect: Dialect

    def execute(self, sql: str, params: Params = ()) -> int:
        """Run a statement; returns the number of rows it changed."""
        ...

    def one(self, sql: str, params: Params = ()) -> Row | None: ...

    def all(self, sql: str, params: Params = ()) -> list[Row]: ...

    @property
    def for_update(self) -> str:
        """`` FOR UPDATE`` on Postgres; empty on SQLite, whose write lock covers it."""
        ...


class Database(Protocol):
    dialect: Dialect

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[Tx]: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- sqlite


class _SqliteTx:
    dialect: Dialect = "sqlite"

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Params = ()) -> int:
        return self._conn.execute(sql, tuple(params)).rowcount

    def one(self, sql: str, params: Params = ()) -> Row | None:
        row = self._conn.execute(sql, tuple(params)).fetchone()
        return tuple(row) if row is not None else None

    def all(self, sql: str, params: Params = ()) -> list[Row]:
        return [tuple(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    @property
    def for_update(self) -> str:
        return ""


class SqliteDatabase:
    """A file-backed SQLite database in WAL mode, one connection per transaction.

    Connections are cheap to open and are never shared between threads, which
    sidesteps sqlite3's thread rules (Streamlit runs each session in its own
    thread, and the alert worker in another).
    """

    dialect: Dialect = "sqlite"

    def __init__(self, path: Path, *, busy_timeout_s: float = SQLITE_BUSY_TIMEOUT_S) -> None:
        self.path = path
        self._busy_timeout_s = busy_timeout_s
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("Cannot create the data directory %s: %r", path.parent, exc)
            raise StoreUnavailableError("Saved data is unavailable right now.") from exc
        conn = self._connect()
        try:
            # WAL lets readers run while a write is in progress; it is persistent.
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(
                self.path,
                timeout=self._busy_timeout_s,
                isolation_level=None,  # explicit BEGIN/COMMIT below
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            logger.error("Cannot open SQLite database %s: %r", self.path, exc)
            raise StoreUnavailableError("Saved data is unavailable right now.") from exc
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_s * 1000)}")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[Tx]:
        conn = self._connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            except sqlite3.OperationalError as exc:
                raise StoreUnavailableError("Saved data is busy. Try again in a moment.") from exc
            try:
                yield _SqliteTx(conn)
            except BaseException:
                conn.rollback()
                raise
            try:
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                raise StoreUnavailableError("Saved data is busy. Try again in a moment.") from exc
        except sqlite3.OperationalError as exc:
            logger.warning("SQLite operation failed: %r", exc)
            raise StoreUnavailableError("Saved data is unavailable right now.") from exc
        finally:
            conn.close()

    def close(self) -> None:
        """Nothing to release: connections close after each transaction."""


# --------------------------------------------------------------------------- postgres


def to_pyformat(sql: str) -> str:
    """``?`` placeholders to psycopg's ``%s`` (literal ``%`` is escaped first)."""
    return sql.replace("%", "%%").replace("?", "%s")


class _PostgresTx:
    dialect: Dialect = "postgres"

    def __init__(self, cursor: Any) -> None:
        self._cur = cursor

    def execute(self, sql: str, params: Params = ()) -> int:
        self._cur.execute(to_pyformat(sql), tuple(params))
        return int(self._cur.rowcount)

    def one(self, sql: str, params: Params = ()) -> Row | None:
        self._cur.execute(to_pyformat(sql), tuple(params))
        row = self._cur.fetchone()
        return tuple(row) if row is not None else None

    def all(self, sql: str, params: Params = ()) -> list[Row]:
        self._cur.execute(to_pyformat(sql), tuple(params))
        return [tuple(r) for r in self._cur.fetchall()]

    @property
    def for_update(self) -> str:
        return " FOR UPDATE"


class PostgresDatabase:
    """Postgres through a small psycopg connection pool (the ``postgres`` extra)."""

    dialect: Dialect = "postgres"

    def __init__(self, url: str, *, max_size: int = PG_POOL_MAX) -> None:
        try:
            import psycopg
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on the install
            msg = "Postgres support needs the 'postgres' extra: uv sync --extra postgres"
            raise StoreError(msg) from exc
        self._errors: tuple[type[Exception], ...] = (psycopg.OperationalError,)
        self._pool = ConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            timeout=PG_CONNECT_TIMEOUT_S * 2,
            kwargs={
                "connect_timeout": PG_CONNECT_TIMEOUT_S,
                "options": f"-c statement_timeout={PG_STATEMENT_TIMEOUT_MS}",
            },
            open=False,
        )
        self._lock = threading.Lock()
        self._opened = False

    def _open(self) -> None:
        with self._lock:
            if not self._opened:
                self._pool.open(wait=False)
                self._opened = True

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[Tx]:
        from psycopg_pool import PoolTimeout

        self._open()
        unavailable: tuple[type[Exception], ...] = (PoolTimeout, *self._errors)
        try:
            # The pool's context manager commits on success and rolls back on error.
            with self._pool.connection() as conn, conn.cursor() as cur:
                if not write:
                    cur.execute("SET TRANSACTION READ ONLY")
                yield _PostgresTx(cur)
        except unavailable as exc:
            logger.warning("Postgres operation failed: %r", exc)
            raise StoreUnavailableError("Saved data is unavailable right now.") from exc

    def close(self) -> None:
        with self._lock:
            if self._opened:
                self._pool.close()
                self._opened = False


def open_database(url: str) -> Database:
    """``sqlite:///path/to/file.db`` or ``postgresql://user:pass@host/db``."""
    if url.startswith("sqlite:///"):
        return SqliteDatabase(Path(url.removeprefix("sqlite:///")))
    if url.startswith(("postgresql://", "postgres://")):
        return PostgresDatabase(url)
    msg = "Unsupported database URL; use sqlite:/// or postgresql://"
    raise StoreError(msg)
