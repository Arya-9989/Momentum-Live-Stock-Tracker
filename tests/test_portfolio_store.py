"""Portfolio storage on SQLite and Postgres: exact decimals, ordering and concurrent sales."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D  # noqa: N817 - reads like the hand-worked figures

import pytest

from momentum import portfolio as pf
from momentum.data import InvalidSymbolError
from momentum.store.db import Database
from momentum.store.owners import Owner
from momentum.store.portfolio import SqlPortfolioRepository, TransactionLimitError
from tests.test_store import run_concurrently

ALICE = Owner("dev_AAAAAAAAAAAAAAAAAAAAAA", "device")
MALLORY = Owner("dev_MMMMMMMMMMMMMMMMMMMMMM", "device")


def tx(
    kind: pf.Kind, day: str, quantity: str, price: str = "0", symbol: str = "AAPL"
) -> pf.Transaction:
    return pf.Transaction(
        symbol, kind, date.fromisoformat(day), D(quantity), D(price), D("0"), "USD"
    )


@pytest.fixture
def repo(database: Database) -> SqlPortfolioRepository:
    return SqlPortfolioRepository(database)


def test_round_trip_is_exact(repo: SqlPortfolioRepository) -> None:
    saved = repo.add(
        ALICE,
        pf.Transaction(
            "btc-usd", "buy", date(2025, 1, 2), D("0.12345678"), D("98765.4321"), D("1.99"), "USD"
        ),
    )
    (loaded,) = repo.transactions(ALICE)
    assert loaded.symbol == "BTC-USD"
    assert (loaded.quantity, loaded.price, loaded.fees) == (
        D("0.12345678"),
        D("98765.4321"),
        D("1.99"),
    )
    assert loaded.trade_date == date(2025, 1, 2)
    assert loaded.id == saved.id
    assert loaded.seq == 1


def test_history_is_ordered_by_date_then_entry(repo: SqlPortfolioRepository) -> None:
    repo.add(ALICE, tx("buy", "2025-03-01", "1", "10"))
    repo.add(ALICE, tx("buy", "2025-01-01", "1", "20"))
    repo.add(ALICE, tx("sell", "2025-03-01", "1", "30"))
    assert [(t.trade_date.month, t.seq) for t in repo.transactions(ALICE)] == [
        (1, 2),
        (3, 1),
        (3, 3),
    ]


def test_oversell_is_refused_and_nothing_is_written(repo: SqlPortfolioRepository) -> None:
    repo.add(ALICE, tx("buy", "2025-01-01", "10", "10"))
    with pytest.raises(pf.PortfolioError, match="only 10 are held"):
        repo.add(ALICE, tx("sell", "2025-02-01", "11", "10"))
    assert len(repo.transactions(ALICE)) == 1


def test_concurrent_sales_cannot_overdraw(database: Database) -> None:
    SqlPortfolioRepository(database).add(ALICE, tx("buy", "2025-01-01", "10", "10"))

    def sell(_: int) -> bool:
        try:
            SqlPortfolioRepository(database).add(ALICE, tx("sell", "2025-02-01", "8", "12"))
        except pf.PortfolioError:
            return False
        return True

    assert sorted(run_concurrently(4, sell)) == [False, False, False, True]
    position = pf.build(SqlPortfolioRepository(database).transactions(ALICE))["AAPL"]
    assert position.quantity == D("2")


def test_delete_that_breaks_a_later_sale_is_refused(repo: SqlPortfolioRepository) -> None:
    buy = repo.add(ALICE, tx("buy", "2025-01-01", "10", "10"))
    sale = repo.add(ALICE, tx("sell", "2025-02-01", "8", "12"))
    with pytest.raises(pf.PortfolioError, match="would break a later sale"):
        repo.delete(ALICE, buy.id)
    assert repo.delete(ALICE, sale.id)
    assert repo.delete(ALICE, buy.id)
    assert repo.transactions(ALICE) == []
    assert not repo.delete(ALICE, buy.id)


def test_owners_are_isolated(repo: SqlPortfolioRepository) -> None:
    mine = repo.add(ALICE, tx("buy", "2025-01-01", "1", "10"))
    assert repo.transactions(MALLORY) == []
    assert not repo.delete(MALLORY, mine.id)  # another owner's id deletes nothing
    assert len(repo.transactions(ALICE)) == 1


def test_validation_happens_before_the_database(repo: SqlPortfolioRepository) -> None:
    with pytest.raises(InvalidSymbolError):
        repo.add(ALICE, tx("buy", "2025-01-01", "1", "1", symbol="'; DROP TABLE x;--"))
    with pytest.raises(pf.PortfolioError, match="future"):
        repo.add(ALICE, tx("buy", "2999-01-01", "1", "1"), today=date(2026, 10, 23))
    assert repo.transactions(ALICE) == []


def test_transaction_cap(database: Database) -> None:
    repo = SqlPortfolioRepository(database, limit=2)
    repo.add(ALICE, tx("buy", "2025-01-01", "1", "1"))
    repo.add(ALICE, tx("buy", "2025-01-02", "1", "1"))
    with pytest.raises(TransactionLimitError, match="up to 2 transactions"):
        repo.add(ALICE, tx("buy", "2025-01-03", "1", "1"))


def test_delete_many_is_checked_as_a_whole(repo: SqlPortfolioRepository) -> None:
    buy = repo.add(ALICE, tx("buy", "2025-01-01", "10", "10"))
    sale = repo.add(ALICE, tx("sell", "2025-02-01", "8", "12"))
    other = repo.add(MALLORY, tx("buy", "2025-01-01", "1", "10"))
    with pytest.raises(pf.PortfolioError):
        repo.delete_many(ALICE, [buy.id])
    assert repo.delete_many(ALICE, [buy.id, sale.id, other.id]) == 2
    assert repo.transactions(ALICE) == []
    assert len(repo.transactions(MALLORY)) == 1  # someone else's id is ignored
    assert repo.delete_many(ALICE, ["trx_nothing"]) == 0
