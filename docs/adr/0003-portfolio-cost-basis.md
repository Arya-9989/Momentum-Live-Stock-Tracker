# 0003: Portfolio cost basis, P&L and currencies

- Status: accepted
- Date: 2026-10-23

## Context

The portfolio view records buys and sells and shows holdings, cost basis, and realized and
unrealized P&L. Each of those numbers depends on a method decision. A wrong one looks plausible,
so the method must be explicit, documented and tested against figures worked out by hand.

The instruments are global: US shares in dollars, NSE listings in rupees, London listings
*quoted in pence* (`GBp`), indices, crypto. A portfolio can hold several currencies at once.

## Options considered

**Cost basis**

1. **FIFO (first in, first out).** A sale consumes the oldest lots first. It is the default for
   US brokers' 1099-B reporting, the method Indian tax rules apply to listed shares, and how most
   brokers' statements read. Each lot keeps its own cost, so a holding's cost basis is exactly
   the cost of the shares still held.
2. **Average cost.** Every share costs the running average; a sale removes shares at that
   average. It is required for UK share pooling (Section 104) and Canadian ACB, and is simpler
   to explain. But it forgets which purchase was sold, and the realized P&L of a sale differs
   from FIFO's whenever purchase prices differ.
3. **Specific identification.** The most flexible, but it needs the user to pick lots on every
   sale. That is too much for this app's scope.

**Currency**

1. Convert everything to one base currency at trade-date FX rates. This is correct for tax in that
   base currency, but it needs historical FX for every trade and a base currency per user.
2. Keep every figure in the instrument's own currency, total per currency, and convert totals at
   today's rate only on request, labelled as such.

## Decision

- **FIFO.** Fees are part of cost: a buy's fees are added to its lot's cost. A sell's fees reduce
  its proceeds. Realized P&L = (quantity × price − fees) − FIFO cost of the shares sold.
  Unrealized P&L = quantity held × current price − cost of the remaining lots. The engine is a
  pure function of the transaction list (`momentum.portfolio`), so another method could be added
  beside it later.
- **Order.** Transactions are applied by trade date, then in the order they were entered.
  Back-dating a buy re-matches later sales, as FIFO requires.
- **Validation.** A sale may never exceed the shares held at that point in the order. That is
  checked when a transaction is added *or deleted* (deleting an old buy can invalidate a later
  sale), inside the write transaction, so two concurrent sales cannot overdraw a holding.
- **Splits** are a transaction kind with a ratio (4 for a 4-for-1 split). They multiply the
  remaining lots' quantities and keep their cost. Market prices from the data source are
  split-adjusted, so a holding bought before a split needs the split recorded to stay correct.
- **Currencies.** Every amount is computed in the instrument's *major* currency. A price entered
  in pence (`GBp`, as London quotes) is divided by 100; fees are entered in the major unit (pounds),
  as brokers charge them. Holdings show their own currency. Totals are shown **per currency** and
  are never summed across currencies. An optional "show totals in" converts each currency's
  totals at today's rate from the data source (`GBPUSD=X`, ...). It is labelled as today's rate,
  because it does not separate currency moves from price moves.
- **Arithmetic** uses `Decimal`, stored exactly (`NUMERIC(28, 10)` on Postgres, text on
  SQLite). Floats appear only at the display edge.

## Consequences

- Figures match a FIFO broker statement in the instrument's currency. Average-cost users (UK, Canada)
  will see different realized P&L, and the README says so.
- Per-currency totals avoid a silently wrong grand total, at the price of a less compact summary
  when several currencies are held.
- Bounded growth: at most 1,000 transactions per owner.
- The tests check hand-computed figures: partial sales across lots, fees on both sides, a
  back-dated buy, a split, pence-quoted shares, a sale that would overdraw, and a delete that
  would invalidate a later sale.
