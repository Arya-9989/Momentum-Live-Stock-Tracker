"""FIFO cost basis, P&L and currencies, checked against figures worked out by hand.

Every expected value below is derived in the comment beside it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D  # noqa: N817 - reads like the hand-worked figures

import pytest

from momentum import portfolio as pf
from momentum.portfolio import Mark, PortfolioError, Transaction

TODAY = date(2026, 10, 23)


def tx(  # noqa: PLR0917 - one positional field per column of a statement
    symbol: str,
    kind: pf.Kind,
    day: str,
    quantity: str,
    price: str = "0",
    fees: str = "0",
    currency: str = "USD",
    seq: int = 0,
) -> Transaction:
    return Transaction(
        symbol, kind, date.fromisoformat(day), D(quantity), D(price), D(fees), currency, seq
    )


AAPL = [
    tx("AAPL", "buy", "2024-01-10", "10", "100", "5", seq=1),  # lot 1: 10 x 100 + 5 = 1005
    tx("AAPL", "buy", "2024-02-10", "5", "120", "2", seq=2),  # lot 2: 5 x 120 + 2 = 602
    tx("AAPL", "sell", "2024-03-10", "12", "130", "3", seq=3),  # proceeds 12 x 130 - 3 = 1557
]


class TestFifo:
    def test_partial_sale_across_two_lots(self) -> None:
        position = pf.build(AAPL)["AAPL"]
        # Sold: all of lot 1 (1005) + 2 of lot 2's 5 (602 x 2/5 = 240.8) = 1245.8
        assert position.realized == D("311.2")  # 1557 - 1245.8
        assert position.quantity == D("3")
        assert position.cost == D("361.2")  # 602 - 240.8
        assert position.average_cost == D("120.4")  # 361.2 / 3
        assert position.fees == D("10")

    def test_fifo_differs_from_average_cost(self) -> None:
        # Average cost would price the 12 sold at 1607 / 15 each: 1285.6, realizing 271.4.
        average_realized = D("1557") - D("1607") / 15 * 12
        assert round(average_realized, 1) == D("271.4")
        assert pf.build(AAPL)["AAPL"].realized != round(average_realized, 1)

    def test_valuation(self) -> None:
        (holding,) = pf.value(pf.build(AAPL), {"AAPL": Mark(D("150"), D("148"))})
        assert holding.market_value == D("450")  # 3 x 150
        assert holding.unrealized == D("88.8")  # 450 - 361.2
        assert round(holding.unrealized_pct or 0, 4) == D("24.5847")  # 88.8 / 361.2
        assert holding.day_change == D("6")  # 3 x (150 - 148)
        assert holding.realized == D("311.2")

    def test_backdated_buy_rematches_later_sales(self) -> None:
        history = [
            tx("MSFT", "buy", "2024-03-01", "10", "100", seq=1),
            tx("MSFT", "sell", "2024-04-01", "5", "110", seq=2),
        ]
        assert pf.build(history)["MSFT"].realized == D("50")  # 5 x (110 - 100)
        backdated = tx("MSFT", "buy", "2024-01-01", "10", "50", seq=3)  # entered last
        position = pf.build([*history, backdated])["MSFT"]
        assert position.realized == D("300")  # the January lot is first out: 5 x (110 - 50)
        assert position.quantity == D("15")
        assert position.cost == D("1250")  # 5 x 50 + 10 x 100

    def test_same_day_follows_entry_order(self) -> None:
        history = [
            tx("X", "buy", "2024-01-01", "1", "10", seq=1),
            tx("X", "buy", "2024-01-01", "1", "20", seq=2),
            tx("X", "sell", "2024-01-01", "1", "30", seq=3),
        ]
        assert pf.build(history)["X"].realized == D("20")  # sells the 10 lot

    def test_split_keeps_cost_and_multiplies_shares(self) -> None:
        history = [
            tx("AAPL", "buy", "2020-01-02", "1", "400", seq=1),
            tx("AAPL", "split", "2020-08-31", "4", seq=2),  # 4 for 1: 4 shares, cost 400
            tx("AAPL", "sell", "2021-01-04", "2", "130", seq=3),  # 260 - 200
        ]
        position = pf.build(history)["AAPL"]
        assert position.realized == D("60")
        assert position.quantity == D("2")
        assert position.average_cost == D("100")

    def test_pence_quoted_listing(self) -> None:
        # 1000 VOD.L at 75.50p with £10 fees: 1000 x 0.755 + 10 = £765.
        history = [tx("VOD.L", "buy", "2025-06-02", "1000", "75.50", "10", "GBp", 1)]
        position = pf.build(history)["VOD.L"]
        assert (position.currency, position.cost) == ("GBP", D("765"))
        (holding,) = pf.value({"VOD.L": position}, {"VOD.L": Mark(D("84"), D("83"))})
        assert holding.price == D("0.84")
        assert holding.market_value == D("840")
        assert holding.unrealized == D("75")
        assert holding.day_change == D("10")  # 1000 x 1p

    def test_fractional_quantities_are_exact(self) -> None:
        history = [
            tx("BTC-USD", "buy", "2025-01-01", "0.1", "60000", seq=1),
            tx("BTC-USD", "buy", "2025-02-01", "0.2", "90000", seq=2),
            tx("BTC-USD", "sell", "2025-03-01", "0.3", "100000", seq=3),
        ]
        position = pf.build(history)["BTC-USD"]
        assert position.quantity == 0  # no 5.5e-17 left behind
        assert position.realized == D("6000")  # 30000 - (6000 + 18000)


class TestRules:
    def test_cannot_sell_more_than_held(self) -> None:
        with pytest.raises(PortfolioError, match="only 10 are held then"):
            pf.build([*AAPL[:2], tx("AAPL", "sell", "2024-01-20", "11", "1", seq=9)])

    def test_cannot_sell_before_buying(self) -> None:
        with pytest.raises(PortfolioError, match="only 0 are held"):
            pf.build([tx("AAPL", "sell", "2023-12-31", "1", "1", seq=1), AAPL[0]])

    def test_one_currency_per_symbol(self) -> None:
        with pytest.raises(PortfolioError, match="recorded in GBp, not GBP"):
            pf.build(
                [
                    tx("VOD.L", "buy", "2025-01-01", "1", "80", currency="GBp", seq=1),
                    tx("VOD.L", "buy", "2025-01-02", "1", "0.8", currency="GBP", seq=2),
                ]
            )

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            (tx("A", "buy", "2026-10-24", "1", "1"), "future"),
            (tx("A", "buy", "1899-12-31", "1", "1"), "too far in the past"),
            (tx("A", "buy", "2025-01-01", "0", "1"), "Quantity must be more than zero"),
            (tx("A", "buy", "2025-01-01", "-1", "1"), "Quantity must be more than zero"),
            (tx("A", "buy", "2025-01-01", "1", "-1"), "Price cannot be negative"),
            (tx("A", "buy", "2025-01-01", "1", "1", "-0.01"), "Fees cannot be negative"),
            (tx("A", "buy", "2025-01-01", "1e13", "1"), "Quantity is too large"),
            (tx("A", "buy", "2025-01-01", "0.00000000001", "1"), "at most 10 decimal"),
            (tx("A", "split", "2025-01-01", "4", "1"), "no price or fees"),
            (tx("A", "split", "2025-01-01", "5000"), "ratio is too large"),
            (tx("A", "buy", "2025-01-01", "NaN", "1"), "must be a number"),
            (tx("A", "buy", "2025-01-01", "1", "1", currency="U$D"), "Unknown currency"),
        ],
    )
    def test_invalid_fields(self, bad: Transaction, message: str) -> None:
        with pytest.raises(PortfolioError, match=message):
            pf.validate(bad, today=TODAY)

    def test_major_currency(self) -> None:
        assert pf.major_currency("GBp") == ("GBP", D(100))
        assert pf.major_currency("ZAc") == ("ZAR", D(100))
        assert pf.major_currency("usd") == ("USD", D(1))


class TestTotals:
    def holdings(self) -> list[pf.Holding]:
        history = [
            *AAPL,
            tx("MSFT", "buy", "2025-01-02", "2", "400", seq=4),  # cost 800
            tx("LT.NS", "buy", "2025-01-02", "10", "3500", "20", "INR", 5),  # cost 35,020
            tx("TSLA", "buy", "2025-01-02", "1", "300", seq=6),  # no price: incomplete
        ]
        marks = {
            "AAPL": Mark(D("150"), D("148")),
            "MSFT": Mark(D("500"), D("505")),
            "LT.NS": Mark(D("3600"), D("3610")),
        }
        return pf.value(pf.build(history), marks)

    def test_per_currency_never_mixed(self) -> None:
        inr, usd = pf.totals(self.holdings())
        assert usd.currency == "USD"
        assert usd.market_value == D("1450")  # 450 + 1000 (TSLA unpriced)
        assert usd.cost == D("1161.2")  # 361.2 + 800
        assert usd.unrealized == D("288.8")  # 88.8 + 200
        assert usd.realized == D("311.2")
        assert usd.day_change == D("-4")  # 6 + 2 x (500 - 505)
        assert not usd.complete
        assert inr.currency == "INR"
        assert inr.market_value == D("36000")
        assert inr.unrealized == D("980")  # 36000 - 35020
        assert inr.complete

    def test_conversion_uses_today_s_rate_and_needs_every_rate(self) -> None:
        inr, usd = pf.totals(self.holdings())
        converted = pf.convert([usd, inr], "USD", {"INR": D("0.012")})
        assert converted is not None
        assert converted.market_value == D("1882")  # 1450 + 36000 x 0.012
        assert converted.unrealized == D("300.56")  # 288.8 + 980 x 0.012
        assert pf.convert([usd, inr], "EUR", {"INR": D("0.011")}) is None  # no USD rate
        assert pf.convert([], "USD", {}) is None

    def test_closed_positions_keep_their_realized_p_and_l(self) -> None:
        history = [
            tx("NVDA", "buy", "2025-01-02", "1", "100", seq=1),
            tx("NVDA", "sell", "2025-02-03", "1", "90", seq=2),
        ]
        (holding,) = pf.value(pf.build(history), {})
        assert holding.quantity == 0
        assert holding.realized == D("-10")
        (total,) = pf.totals([holding])
        assert total.realized == D("-10")
        assert total.complete  # nothing open needs a price
        assert total.unrealized_pct is None

    def test_sorted_by_currency_then_value(self) -> None:
        assert [h.symbol for h in self.holdings()] == ["LT.NS", "MSFT", "AAPL", "TSLA"]
