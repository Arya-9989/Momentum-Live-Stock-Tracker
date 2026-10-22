"""Storage: migrations, owners and the watchlist repository, on SQLite and Postgres."""

from __future__ import annotations

import importlib.resources
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path

import pytest

from momentum import config, settings, store
from momentum.data import InvalidSymbolError
from momentum.store import owners, schema
from momentum.store.db import Database, SqliteDatabase, StoreError, StoreUnavailableError
from momentum.store.owners import CapacityError, Owner
from momentum.store.schema import MigrationError, current_version, migrate, split_statements
from momentum.store.watchlists import SqlWatchlistRepository

DEVICE = Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device")
OTHER = Owner("dev_BBBBBBBBBBBBBBBBBBBBBB", "device")
ACCOUNT = Owner("acct_" + "a" * 40, "account")


def run_concurrently(count: int, work: Callable[[int], object]) -> list[object]:
    """Run ``work(i)`` on ``count`` threads that all start at the same moment."""
    barrier = threading.Barrier(count)

    def go(i: int) -> object:
        barrier.wait()
        return work(i)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(go, range(count)))


# --------------------------------------------------------------------------- migrations


class TestMigrations:
    def test_applies_once_and_is_idempotent(self, empty_database: Database) -> None:
        assert current_version(empty_database) == 0
        applied = migrate(empty_database)
        assert applied[0] == "0001_owners_and_watchlists"
        assert current_version(empty_database) == len(applied)
        assert migrate(empty_database) == []

    def test_refuses_an_edited_migration(self, database: Database) -> None:
        with database.transaction() as tx:
            tx.execute("UPDATE schema_migrations SET checksum = 'x' WHERE version = 1")
        with pytest.raises(MigrationError, match="edited after it was applied"):
            migrate(database)

    def test_refuses_a_database_from_a_newer_release(self, database: Database) -> None:
        with database.transaction() as tx:
            tx.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                "VALUES (999, 'future', 'x', 0)"
            )
        with pytest.raises(MigrationError, match="newer release"):
            migrate(database)

    def test_concurrent_starts_migrate_exactly_once(self, empty_database: Database) -> None:
        results = run_concurrently(6, lambda _: migrate(empty_database))
        assert sum(1 for r in results if r) == 1
        assert current_version(empty_database) == len(schema.load_migrations("sqlite"))

    def test_a_failed_migration_leaves_nothing_behind(
        self, empty_database: Database, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = schema.Migration(1, "broken", "CREATE TABLE ok_table (id INTEGER);\nNOPE;")
        monkeypatch.setattr(schema, "load_migrations", lambda _dialect: [broken])
        with pytest.raises(Exception):  # noqa: B017, PT011 - the driver's syntax error
            migrate(empty_database)
        monkeypatch.undo()
        assert current_version(empty_database) == 0  # the whole run rolled back

    def test_split_statements_drops_comments(self) -> None:
        sql = (
            "-- header; with a semicolon\nCREATE TABLE a (x INTEGER);\n\n"
            "  -- note\nCREATE INDEX i ON a (x);\n"
        )
        assert split_statements(sql) == ["CREATE TABLE a (x INTEGER)", "CREATE INDEX i ON a (x)"]

    def test_both_dialects_define_the_same_tables_and_versions(self) -> None:
        def shape(dialect: str) -> list[tuple[str, list[str]]]:
            folder = resources.files("momentum.store") / "migrations" / dialect
            out = []
            for entry in sorted(folder.iterdir(), key=lambda e: e.name):
                tables = re.findall(r"CREATE TABLE (\w+)", entry.read_text(encoding="utf-8"))
                out.append((entry.name, tables))
            return out

        assert shape("sqlite") == shape("postgres")

    def test_versions_must_not_have_gaps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "migrations" / "sqlite").mkdir(parents=True)
        (tmp_path / "migrations" / "sqlite" / "0002_late.sql").write_text("SELECT 1;")
        monkeypatch.setattr(importlib.resources, "files", lambda _pkg: tmp_path)
        with pytest.raises(MigrationError, match="without gaps"):
            schema.load_migrations("sqlite")


# --------------------------------------------------------------------------- owners


class TestOwners:
    @pytest.mark.parametrize(
        ("owner_id", "kind"),
        [
            ("dev_short", "device"),
            ("acct_" + "a" * 40, "device"),  # prefix must match the kind
            ("dev_AAAAAAAAAAAAAAAAAAAAAA'; DROP TABLE owners;--", "device"),
            ("dev_" + "A" * 70, "device"),
        ],
    )
    def test_rejects_malformed_ids(self, owner_id: str, kind: owners.OwnerKind) -> None:
        with pytest.raises(ValueError, match="owner id"):
            Owner(owner_id, kind)

    def test_device_ceiling_refuses_new_devices_only(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database, max_devices=1)
        repo.apply(DEVICE, add=["AAPL"])
        repo.apply(DEVICE, add=["MSFT"])  # an existing device keeps working
        with pytest.raises(CapacityError, match="limit for anonymous visitors"):
            repo.apply(OTHER, add=["AAPL"])
        repo.apply(ACCOUNT, add=["AAPL"])  # accounts are never refused

    def test_prune_removes_stale_devices_and_their_data(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        repo.apply(DEVICE, add=["AAPL"])
        repo.apply(OTHER, add=["MSFT"])
        repo.apply(ACCOUNT, add=["NVDA"])
        with database.transaction() as tx:
            tx.execute(
                "UPDATE owners SET last_seen_at = 0 WHERE id IN (?, ?)", (DEVICE.id, ACCOUNT.id)
            )
        assert owners.prune_devices(database, retention_days=30) == 1
        assert repo.get(DEVICE) is None
        assert repo.get(OTHER) is not None
        assert repo.get(ACCOUNT) is not None  # accounts are not pruned
        with database.transaction(write=False) as tx:
            assert tx.one(
                "SELECT COUNT(*) FROM watchlist_items WHERE owner_id = ?", (DEVICE.id,)
            ) == (0,)

    def test_touch_writes_at_most_once_a_day(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        repo.apply(DEVICE, add=["AAPL"])
        saved = repo.get(DEVICE)
        assert saved is not None
        assert saved.last_seen_at is not None
        repo.touch(DEVICE, saved.last_seen_at)  # recent: no write
        assert repo.get(DEVICE).last_seen_at == saved.last_seen_at  # type: ignore[union-attr]
        with database.transaction() as tx:
            tx.execute("UPDATE owners SET last_seen_at = 5 WHERE id = ?", (DEVICE.id,))
        repo.touch(DEVICE, 5)
        touched = repo.get(DEVICE)
        assert touched is not None
        assert (touched.last_seen_at or 0) > 5

    def test_delete_owner_cascades(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        repo.apply(DEVICE, add=["AAPL"])
        assert owners.delete_owner(database, DEVICE)
        assert repo.get(DEVICE) is None
        assert not owners.delete_owner(database, DEVICE)


# --------------------------------------------------------------------------- watchlists


class TestWatchlists:
    def test_nothing_is_saved_until_the_first_edit(self, database: Database) -> None:
        assert SqlWatchlistRepository(database).get(DEVICE) is None
        with database.transaction(write=False) as tx:
            assert tx.one("SELECT COUNT(*) FROM owners") == (0,)

    def test_first_edit_seeds_what_the_owner_saw(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        change = repo.apply(DEVICE, add=["tsla"], remove=["MSFT"], base=["AAPL", "MSFT", "^gspc"])
        assert change.watchlist.symbols == ("AAPL", "^GSPC", "TSLA")
        assert change.watchlist.version == 1
        assert change.over_limit == ()

    def test_edits_are_deltas_and_keep_order(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        repo.apply(DEVICE, add=["AAPL", "MSFT"])
        repo.apply(DEVICE, add=["NVDA", "AAPL"])  # AAPL is already there
        repo.apply(DEVICE, remove=["MSFT", "NOTTHERE"])
        saved = repo.get(DEVICE)
        assert saved is not None
        assert saved.symbols == ("AAPL", "NVDA")
        assert saved.version == 3

    def test_owners_are_isolated(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        repo.apply(DEVICE, add=["AAPL"])
        repo.apply(OTHER, add=["MSFT"])
        assert repo.get(DEVICE).symbols == ("AAPL",)  # type: ignore[union-attr]
        assert repo.get(OTHER).symbols == ("MSFT",)  # type: ignore[union-attr]

    def test_cap_turns_symbols_away(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database, limit=3)
        change = repo.apply(DEVICE, add=["A", "B", "C", "D", "E"])
        assert change.watchlist.symbols == ("A", "B", "C")
        assert change.over_limit == ("D", "E")
        # Removing and adding in one edit frees the slot first.
        change = repo.apply(DEVICE, add=["D"], remove=["A"])
        assert change.watchlist.symbols == ("B", "C", "D")

    def test_seed_is_capped_too(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database, limit=2)
        change = repo.apply(DEVICE, base=["A", "B", "C"])
        assert change.watchlist.symbols == ("A", "B")

    @pytest.mark.parametrize(
        "bad",
        ["", "AAPL MSFT!", "'; DROP TABLE watchlist_items; --", "<script>", "A" * 30, "∆"],
    )
    def test_invalid_symbols_never_reach_the_database(self, database: Database, bad: str) -> None:
        repo = SqlWatchlistRepository(database)
        with pytest.raises(InvalidSymbolError):
            repo.apply(DEVICE, add=[bad])
        with pytest.raises(InvalidSymbolError):
            repo.apply(DEVICE, base=[bad])
        assert repo.get(DEVICE) is None  # the transaction never started

    def test_schema_rejects_oversized_symbols_even_without_the_app(
        self, database: Database
    ) -> None:
        repo = SqlWatchlistRepository(database)
        repo.apply(DEVICE, add=["AAPL"])
        with pytest.raises(Exception), database.transaction() as tx:  # noqa: B017, PT011
            tx.execute(
                "INSERT INTO watchlist_items (owner_id, symbol, position, added_at) "
                "VALUES (?, ?, 9, 0)",
                (DEVICE.id, "X" * 22),
            )

    def test_concurrent_adds_never_exceed_the_cap_or_lose_updates(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        symbols = [f"SYM{i}" for i in range(20)]
        results = run_concurrently(20, lambda i: repo.apply(DEVICE, add=[symbols[i]]))
        saved = repo.get(DEVICE)
        assert saved is not None
        assert len(saved.symbols) == config.MAX_WATCHLIST
        assert len(set(saved.symbols)) == len(saved.symbols)
        refused = [s for r in results for s in r.over_limit]  # type: ignore[attr-defined]
        assert sorted(refused + list(saved.symbols)) == sorted(symbols)
        assert saved.version == 20  # every write was applied, one at a time

    def test_two_tabs_do_not_undo_each_other(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        base = ["AAPL", "MSFT"]
        repo.apply(DEVICE, base=base)
        # Both tabs loaded [AAPL, MSFT]. Tab 1 removes MSFT, tab 2 adds NVDA.
        run_concurrently(
            2,
            lambda i: (
                repo.apply(DEVICE, remove=["MSFT"], base=base)
                if i == 0
                else repo.apply(DEVICE, add=["NVDA"], base=base)
            ),
        )
        assert repo.get(DEVICE).symbols == ("AAPL", "NVDA")  # type: ignore[union-attr]

    def test_concurrent_first_saves_seed_once(self, database: Database) -> None:
        repo = SqlWatchlistRepository(database)
        run_concurrently(8, lambda i: repo.apply(DEVICE, add=[f"NEW{i}"], base=["AAPL", "MSFT"]))
        saved = repo.get(DEVICE)
        assert saved is not None
        assert saved.symbols[:2] == ("AAPL", "MSFT")
        assert len(saved.symbols) == 10


# --------------------------------------------------------------------------- sqlite specifics


def test_sqlite_lock_timeout_is_a_store_error(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "busy.db", busy_timeout_s=0.05)
    migrate(db)
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with db.transaction():
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    held.wait(5)
    try:
        with pytest.raises(StoreUnavailableError, match="busy"):
            SqlWatchlistRepository(db).apply(DEVICE, add=["AAPL"])
    finally:
        release.set()
        thread.join()
    SqlWatchlistRepository(db).apply(DEVICE, add=["AAPL"])  # fine once released


def test_unreachable_data_dir_is_a_store_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(StoreUnavailableError):
        SqliteDatabase(blocker / "sub" / "x.db")


def test_get_store_is_one_migrated_store_per_url(isolated_data_dir: Path) -> None:
    first = store.get_store()
    assert first is store.get_store()
    assert (isolated_data_dir / "momentum.db").exists()
    first.watchlists.apply(DEVICE, add=["AAPL"])
    store.close_all()
    assert store.get_store().watchlists.get(DEVICE).symbols == ("AAPL",)  # type: ignore[union-attr]


def test_unsupported_url_is_rejected() -> None:
    with pytest.raises(StoreError, match="Unsupported"):
        store.open_store("mysql://nope")
    with pytest.raises(settings.SettingsError):
        settings.load({"MOMENTUM_DATABASE_URL": "mysql://nope"})


@pytest.mark.postgres
def test_postgres_unreachable_is_a_store_error() -> None:
    pytest.importorskip("psycopg")
    from momentum.store.db import PostgresDatabase

    db = PostgresDatabase("postgresql://nobody:nothing@127.0.0.1:1/none")
    try:
        with pytest.raises(StoreUnavailableError), db.transaction():
            pass
    finally:
        db.close()
