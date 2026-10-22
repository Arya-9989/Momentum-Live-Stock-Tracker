"""Portfolio arithmetic: FIFO lots, realized and unrealized P&L, per-currency totals (pure).

See docs/adr/0003-portfolio-cost-basis.md. Everything is ``Decimal`` and in the
instrument's major currency (pence-quoted prices are divided by 100). Nothing
here does I/O, so every figure is tested against values worked out by hand.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from momentum.formatting import MINOR_UNIT_CURRENCIES

Kind = Literal["buy", "sell", "split"]
KINDS: Final[tuple[Kind, ...]] = ("buy", "sell", "split")
ZERO: Final = Decimal(0)
MAX_QUANTITY: Final = Decimal("1e12")
MAX_PRICE: Final = Decimal("1e12")
MAX_DECIMALS: Final = 10
MAX_TRANSACTIONS: Final = 1_000


class PortfolioError(ValueError):
    """A transaction that cannot be recorded; the message is safe to show."""


@dataclass(frozen=True, slots=True)
class Transaction:
    """One entry. ``price`` is per unit in the quote currency (pence for ``GBp``);
    ``fees`` are in the major currency. For a split, ``quantity`` is the ratio."""

    symbol: str
    kind: Kind
    trade_date: date
    quantity: Decimal
    price: Decimal
    fees: Decimal
    currency: str
    seq: int = 0
    id: str = ""


def major_currency(currency: str) -> tuple[str, Decimal]:
    """``GBp`` -> (``GBP``, 100): the currency totals are kept in, and the divisor."""
    if currency in MINOR_UNIT_CURRENCIES:
        major, factor = MINOR_UNIT_CURRENCIES[currency]
        return major, Decimal(factor)
    return currency.upper(), Decimal(1)


def _check_amount(name: str, value: Decimal, *, positive: bool, limit: Decimal) -> None:
    if not value.is_finite():
        msg = f"{name} must be a number."
        raise PortfolioError(msg)
    if positive and value <= 0:
        msg = f"{name} must be more than zero."
        raise PortfolioError(msg)
    if value < 0:
        msg = f"{name} cannot be negative."
        raise PortfolioError(msg)
    if value >= limit:
        msg = f"{name} is too large."
        raise PortfolioError(msg)
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_DECIMALS:
        msg = f"{name} can have at most {MAX_DECIMALS} decimal places."
        raise PortfolioError(msg)


def validate(tx: Transaction, *, today: date) -> None:
    """Field-level checks for one transaction (ordering checks are in ``build``)."""
    if tx.kind not in KINDS:
        msg = "Pick buy, sell or split."
        raise PortfolioError(msg)
    if tx.trade_date > today:
        msg = "The trade date cannot be in the future."
        raise PortfolioError(msg)
    if tx.trade_date.year < 1900:
        msg = "The trade date is too far in the past."
        raise PortfolioError(msg)
    if not (3 <= len(tx.currency) <= 8) or not tx.currency.isalpha():
        msg = "Unknown currency."
        raise PortfolioError(msg)
    if tx.kind == "split":
        _check_amount("The split ratio", tx.quantity, positive=True, limit=Decimal(1000))
        if tx.price != 0 or tx.fees != 0:
            msg = "A split has no price or fees."
            raise PortfolioError(msg)
        return
    _check_amount("Quantity", tx.quantity, positive=True, limit=MAX_QUANTITY)
    _check_amount("Price", tx.price, positive=False, limit=MAX_PRICE)
    _check_amount("Fees", tx.fees, positive=False, limit=MAX_PRICE)


@dataclass(slots=True)
class Lot:
    quantity: Decimal
    cost: Decimal  # total cost of the remaining quantity, fees included
    acquired: date


@dataclass(slots=True)
class Position:
    """One symbol after applying its transactions in order."""

    symbol: str
    currency: str  # major
    quote_currency: str  # as quoted, e.g. GBp
    lots: list[Lot] = field(default_factory=list)
    realized: Decimal = ZERO
    proceeds: Decimal = ZERO
    fees: Decimal = ZERO

    @property
    def quantity(self) -> Decimal:
        return sum((lot.quantity for lot in self.lots), ZERO)

    @property
    def cost(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), ZERO)

    @property
    def average_cost(self) -> Decimal | None:
        """Cost per unit of what is still held (the FIFO cost of the remaining lots)."""
        quantity = self.quantity
        return self.cost / quantity if quantity else None


def ordered(transactions: Iterable[Transaction]) -> list[Transaction]:
    """By trade date, then in the order they were entered."""
    return sorted(transactions, key=lambda t: (t.trade_date, t.seq))


def build(transactions: Iterable[Transaction]) -> dict[str, Position]:
    """Apply every transaction in order; raises ``PortfolioError`` on an overdrawn sale."""
    positions: dict[str, Position] = {}
    for tx in ordered(transactions):
        major, divisor = major_currency(tx.currency)
        position = positions.get(tx.symbol)
        if position is None:
            position = positions[tx.symbol] = Position(tx.symbol, major, tx.currency)
        elif position.quote_currency != tx.currency:
            msg = f"{tx.symbol} was recorded in {position.quote_currency}, not {tx.currency}."
            raise PortfolioError(msg)
        unit_price = tx.price / divisor
        if tx.kind == "buy":
            position.lots.append(
                Lot(tx.quantity, tx.quantity * unit_price + tx.fees, tx.trade_date)
            )
            position.fees += tx.fees
        elif tx.kind == "sell":
            _sell(position, tx, unit_price)
        else:  # split: same cost, more (or fewer) units
            for lot in position.lots:
                lot.quantity *= tx.quantity
    return positions


def _sell(position: Position, tx: Transaction, unit_price: Decimal) -> None:
    held = position.quantity
    if tx.quantity > held:
        msg = (
            f"That sells {_plain(tx.quantity)} {tx.symbol} on {tx.trade_date:%b %d, %Y}, "
            f"but only {_plain(held)} are held then."
        )
        raise PortfolioError(msg)
    remaining = tx.quantity
    cost = ZERO
    while remaining > 0:
        lot = position.lots[0]
        take = min(lot.quantity, remaining)
        share = lot.cost * take / lot.quantity
        cost += share
        lot.cost -= share
        lot.quantity -= take
        remaining -= take
        if lot.quantity == 0:
            position.lots.pop(0)
    proceeds = tx.quantity * unit_price - tx.fees
    position.proceeds += proceeds
    position.realized += proceeds - cost
    position.fees += tx.fees


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


# --------------------------------------------------------------------------- valuation


@dataclass(frozen=True, slots=True)
class Mark:
    """The current price of one symbol (in its quote currency) and the previous close."""

    price: Decimal | None
    previous_close: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    currency: str  # major
    quote_currency: str
    quantity: Decimal
    cost: Decimal
    average_cost: Decimal | None  # per unit, major currency
    price: Decimal | None  # per unit, major currency
    market_value: Decimal | None
    unrealized: Decimal | None
    unrealized_pct: Decimal | None
    day_change: Decimal | None
    realized: Decimal


def value(positions: Mapping[str, Position], marks: Mapping[str, Mark]) -> list[Holding]:
    """Holdings valued at ``marks``: open ones first, by currency, then by value."""
    holdings: list[Holding] = []
    for symbol, position in positions.items():
        quantity = position.quantity
        if quantity == 0 and position.realized == 0 and position.proceeds == 0:
            continue
        _, divisor = major_currency(position.quote_currency)
        mark = marks.get(symbol, Mark(None))
        price = mark.price / divisor if mark.price is not None else None
        market_value = quantity * price if price is not None else None
        cost = position.cost
        unrealized = market_value - cost if market_value is not None else None
        unrealized_pct = unrealized / cost * 100 if unrealized is not None and cost else None
        day = None
        if mark.price is not None and mark.previous_close is not None:
            day = quantity * (mark.price - mark.previous_close) / divisor
        holdings.append(
            Holding(
                symbol=symbol,
                currency=position.currency,
                quote_currency=position.quote_currency,
                quantity=quantity,
                cost=cost,
                average_cost=position.average_cost,
                price=price,
                market_value=market_value,
                unrealized=unrealized,
                unrealized_pct=unrealized_pct,
                day_change=day,
                realized=position.realized,
            )
        )
    # Values in different currencies are not comparable: group by currency first.
    holdings.sort(key=lambda h: (h.quantity == 0, h.currency, -(h.market_value or ZERO), h.symbol))
    return holdings


@dataclass(frozen=True, slots=True)
class Totals:
    """Sums for one currency. ``complete`` is False when a held symbol had no price."""

    currency: str
    market_value: Decimal
    cost: Decimal
    unrealized: Decimal
    realized: Decimal
    day_change: Decimal
    complete: bool

    @property
    def unrealized_pct(self) -> Decimal | None:
        return self.unrealized / self.cost * 100 if self.cost else None


def totals(holdings: Sequence[Holding]) -> list[Totals]:
    """One ``Totals`` per currency (never summed across currencies), by currency code."""
    grouped: dict[str, list[Holding]] = defaultdict(list)
    for holding in holdings:
        grouped[holding.currency].append(holding)
    out = []
    for currency, items in grouped.items():
        open_items = [h for h in items if h.quantity]
        priced = [h for h in open_items if h.market_value is not None]
        out.append(
            Totals(
                currency=currency,
                market_value=sum((h.market_value or ZERO for h in priced), ZERO),
                cost=sum((h.cost for h in priced), ZERO),
                unrealized=sum((h.unrealized or ZERO for h in priced), ZERO),
                realized=sum((h.realized for h in items), ZERO),
                day_change=sum((h.day_change or ZERO for h in priced), ZERO),
                complete=len(priced) == len(open_items),
            )
        )
    out.sort(key=lambda t: t.currency)  # amounts in different currencies don't compare
    return out


def convert(items: Sequence[Totals], target: str, rates: Mapping[str, Decimal]) -> Totals | None:
    """Every currency's totals in ``target`` at ``rates`` (units of target per unit);
    None if a rate is missing. Today's rates only: see the ADR."""
    converted = []
    for t in items:
        rate = Decimal(1) if t.currency == target else rates.get(t.currency)
        if rate is None:
            return None
        converted.append(t)
    if not converted:
        return None

    def total(attr: str) -> Decimal:
        return sum(
            (
                getattr(t, attr) * (Decimal(1) if t.currency == target else rates[t.currency])
                for t in converted
            ),
            ZERO,
        )

    return Totals(
        currency=target,
        market_value=total("market_value"),
        cost=total("cost"),
        unrealized=total("unrealized"),
        realized=total("realized"),
        day_change=total("day_change"),
        complete=all(t.complete for t in converted),
    )
