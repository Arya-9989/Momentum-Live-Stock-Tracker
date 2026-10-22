"""Persistent storage: the database, its migrations and the repositories.

``get_store()`` returns one migrated ``Store`` per database URL for the whole
process (Streamlit sessions run as threads of one process). Tests and the CLI
call ``open_store`` directly.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from momentum import settings as settings_mod
from momentum.store.db import Database, StoreError, StoreUnavailableError, open_database
from momentum.store.schema import MigrationError, migrate

if TYPE_CHECKING:
    # Imported when a store is opened, not with the package: the repositories
    # use momentum.data, whose Streamlit caches must not be created before the
    # Streamlit runtime exists (serve.py imports momentum.identity first).
    from momentum.store.alerts import SqlAlertRepository
    from momentum.store.portfolio import SqlPortfolioRepository
    from momentum.store.watchlists import SqlWatchlistRepository

logger = logging.getLogger(__name__)

__all__ = [
    "MigrationError",
    "Store",
    "StoreError",
    "StoreUnavailableError",
    "get_store",
    "open_store",
]


@dataclass(frozen=True, slots=True)
class Store:
    db: Database
    watchlists: SqlWatchlistRepository
    alerts: SqlAlertRepository
    portfolio: SqlPortfolioRepository

    def close(self) -> None:
        self.db.close()


def open_store(url: str) -> Store:
    """Open the database at ``url`` and bring its schema up to date."""
    from momentum.store.alerts import SqlAlertRepository
    from momentum.store.portfolio import SqlPortfolioRepository
    from momentum.store.watchlists import SqlWatchlistRepository

    db = open_database(url)
    try:
        applied = migrate(db)
    except BaseException:
        db.close()
        raise
    if applied:
        logger.info("Applied migrations: %s", ", ".join(applied))
    return Store(
        db=db,
        watchlists=SqlWatchlistRepository(db),
        alerts=SqlAlertRepository(db),
        portfolio=SqlPortfolioRepository(db),
    )


_stores: dict[str, Store] = {}
_lock = threading.Lock()


def get_store(settings: settings_mod.Settings | None = None) -> Store:
    """The process-wide store for the configured database (opened on first use)."""
    url = (settings or settings_mod.load()).database_url
    with _lock:
        store = _stores.get(url)
        if store is None:
            store = open_store(url)
            _stores[url] = store
        return store


def close_all() -> None:
    """Close every store opened by ``get_store`` (shutdown, tests)."""
    with _lock:
        for store in _stores.values():
            store.close()
        _stores.clear()
