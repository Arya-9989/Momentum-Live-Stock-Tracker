"""Shared fixtures.

Every test runs with its own data directory, so nothing is written to the
working tree. Repository tests take the ``database`` fixture, which runs each
test on SQLite and, when ``MOMENTUM_TEST_POSTGRES_URL`` is set (CI, or the
compose file in ``deploy/``), on Postgres as well.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from momentum import identity, store
from momentum.store.db import Database, SqliteDatabase, open_database
from momentum.store.schema import migrate

POSTGRES_URL = os.environ.get("MOMENTUM_TEST_POSTGRES_URL", "").strip()


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MOMENTUM_DATA_DIR", str(data_dir))
    for name in ("MOMENTUM_DATABASE_URL", "MOMENTUM_SECRET_KEY", "MOMENTUM_SECRET_KEY_PREVIOUS"):
        monkeypatch.delenv(name, raising=False)
    store.close_all()
    identity._deployment_secrets.cache_clear()
    yield data_dir
    store.close_all()
    identity._deployment_secrets.cache_clear()


def _reset_postgres(url: str) -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def empty_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Database]:
    """A database with no tables, on each backend."""
    if request.param == "postgres":
        if not POSTGRES_URL:
            pytest.skip("set MOMENTUM_TEST_POSTGRES_URL to run against Postgres")
        _reset_postgres(POSTGRES_URL)
        db = open_database(POSTGRES_URL)
    else:
        db = SqliteDatabase(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def database(empty_database: Database) -> Database:
    """A migrated database, on each backend."""
    migrate(empty_database)
    return empty_database
