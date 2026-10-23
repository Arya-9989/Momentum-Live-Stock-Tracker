"""Persisted portfolio transactions.

Adding or deleting a transaction re-runs the FIFO engine over the owner's whole
history inside the write transaction (under the owner's row lock), so a sale can
never overdraw a holding, even when two tabs submit at once. See
docs/adr/0003-portfolio-cost-basis.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from momentum import data
from momentum import portfolio as pf
from momentum.store.alerts import new_id
from momentum.store.db import Database, StoreError, Tx
from momentum.store.owners import DEFAULT_MAX_DEVICES, Owner, ensure_owner
from momentum.store.schema import now_ms

_COLUMNS = "id, seq, symbol, kind, trade_date, quantity, price, fees, currency"


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - the schema stores numbers only
        msg = "A stored amount is not a number."
        raise StoreError(msg) from exc


def _transaction(row: Sequence[Any]) -> pf.Transaction:
    id_, seq, symbol, kind, trade_date, quantity, price, fees, currency = row
    return pf.Transaction(
        symbol=str(symbol),
        kind=kind,
        trade_date=trade_date if isinstance(trade_date, date) else date.fromisoformat(trade_date),
        quantity=_decimal(quantity),
        price=_decimal(price),
        fees=_decimal(fees),
        currency=str(currency),
        seq=int(seq),
        id=str(id_),
    )


class TransactionLimitError(StoreError):
    """The owner already has the maximum number of transactions."""


class SqlPortfolioRepository:
    def __init__(
        self,
        db: Database,
        *,
        limit: int = pf.MAX_TRANSACTIONS,
        max_devices: int = DEFAULT_MAX_DEVICES,
    ) -> None:
        self._db = db
        self.limit = limit
        self._max_devices = max_devices

    @staticmethod
    def _all(tx: Tx, owner: Owner) -> list[pf.Transaction]:
        rows = tx.all(
            f"SELECT {_COLUMNS} FROM portfolio_transactions WHERE owner_id = ? "
            "ORDER BY trade_date, seq",
            (owner.id,),
        )
        return [_transaction(r) for r in rows]

    def transactions(self, owner: Owner) -> list[pf.Transaction]:
        with self._db.transaction(write=False) as tx:
            return self._all(tx, owner)

    def add(
        self, owner: Owner, new: pf.Transaction, *, today: date | None = None
    ) -> pf.Transaction:
        """Validate and record ``new``; raises ``PortfolioError`` if the history would break."""
        symbol = data.normalize_symbol(new.symbol)
        pf.validate(new, today=today or date.today())
        with self._db.transaction() as tx:
            ensure_owner(tx, owner, max_devices=self._max_devices)
            tx.one(f"SELECT id FROM owners WHERE id = ?{tx.for_update}", (owner.id,))
            existing = self._all(tx, owner)
            if len(existing) >= self.limit:
                msg = f"A portfolio can hold up to {self.limit} transactions."
                raise TransactionLimitError(msg)
            row = tx.one(
                "SELECT COALESCE(MAX(seq), 0) FROM portfolio_transactions WHERE owner_id = ?",
                (owner.id,),
            )
            seq = int(row[0]) + 1 if row else 1
            record = pf.Transaction(
                symbol=symbol,
                kind=new.kind,
                trade_date=new.trade_date,
                quantity=new.quantity,
                price=new.price,
                fees=new.fees,
                currency=new.currency,
                seq=seq,
                id=new_id("trx"),
            )
            pf.build([*existing, record])  # raises before anything is written
            tx.execute(
                f"INSERT INTO portfolio_transactions ({_COLUMNS}, owner_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    seq,
                    symbol,
                    record.kind,
                    record.trade_date.isoformat(),
                    str(record.quantity),
                    str(record.price),
                    str(record.fees),
                    record.currency,
                    owner.id,
                    now_ms(),
                ),
            )
        return record

    def delete(self, owner: Owner, transaction_id: str) -> bool:
        """Delete one of ``owner``'s transactions, unless a later sale depends on it."""
        return self.delete_many(owner, [transaction_id]) == 1

    def delete_many(self, owner: Owner, transaction_ids: Sequence[str]) -> int:
        """Delete several of ``owner``'s transactions at once, all or none.

        The history that remains is checked as a whole, so deleting a sale together
        with the buy it drew on is allowed, while deleting the buy alone is not.
        Ids that belong to someone else are ignored.
        """
        wanted = set(transaction_ids)
        with self._db.transaction() as tx:
            tx.one(f"SELECT id FROM owners WHERE id = ?{tx.for_update}", (owner.id,))
            existing = self._all(tx, owner)
            doomed = [t.id for t in existing if t.id in wanted]
            if not doomed:
                return 0
            try:
                pf.build([t for t in existing if t.id not in wanted])
            except pf.PortfolioError as exc:
                msg = f"That would break a later sale: {exc}"
                raise pf.PortfolioError(msg) from exc
            for transaction_id in doomed:
                tx.execute(
                    "DELETE FROM portfolio_transactions WHERE id = ? AND owner_id = ?",
                    (transaction_id, owner.id),
                )
        return len(doomed)
