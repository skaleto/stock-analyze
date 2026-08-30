from __future__ import annotations

import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.engine import replay_strategy


def _row(
    trade_date: str,
    *,
    role: str = "equity",
    code: str = "510300.SH",
    open_price: float | None = 4.0,
    close: float = 4.0,
    is_open: bool = True,
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "role": role,
        "code": code,
        "open": open_price,
        "close": close,
        "adjusted_close": close,
        "adj_factor": 1.0,
        "is_open": is_open,
    }


class PermanentPortfolioEngineTests(unittest.TestCase):
    def test_close_signal_executes_at_next_open_with_costs_and_lots(self) -> None:
        market = pd.DataFrame(
            [
                _row("20180102"),
                _row("20180103", open_price=4.10, close=4.20),
            ]
        )

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"equity": 1.0}},
            lot_size=100,
            commission_rate=0.0003,
            minimum_commission=5.0,
            slippage_rate=0.0005,
            stamp_tax_rate=0.0,
        )

        trade = result.trades.iloc[0]
        self.assertEqual(trade["signal_date"], "20180102")
        self.assertEqual(trade["trade_date"], "20180103")
        self.assertEqual(trade["shares"] % 100, 0)
        self.assertGreater(trade["price"], 4.10)
        self.assertGreaterEqual(trade["commission"], 5.0)
        self.assertGreaterEqual(result.nav.iloc[-1]["cash"], 0.0)

    def test_fill_uses_raw_open_when_adjustment_factor_changes(self) -> None:
        first = _row("20180102")
        second = _row("20180103", open_price=2.05, close=2.10)
        second["adj_factor"] = 2.0
        second["adjusted_close"] = 4.20

        result = replay_strategy(
            pd.DataFrame([first, second]),
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"equity": 1.0}},
            slippage_rate=0.0005,
        )

        self.assertAlmostEqual(
            result.trades.iloc[0]["price"],
            2.05 * 1.0005,
        )

    def test_same_close_signal_never_executes_same_day(self) -> None:
        market = pd.DataFrame([_row("20180102")])

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"equity": 1.0}},
        )

        self.assertTrue(result.trades.empty)
        self.assertEqual(result.pending.iloc[0]["signal_date"], "20180102")

    def test_suspended_asset_keeps_pending_order(self) -> None:
        market = pd.DataFrame(
            [
                _row(
                    "20180102",
                    role="gold",
                    code="518880.SH",
                    open_price=2.70,
                    close=2.70,
                ),
                _row(
                    "20180103",
                    role="gold",
                    code="518880.SH",
                    open_price=None,
                    close=2.70,
                    is_open=False,
                ),
            ]
        )

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"gold": 1.0}},
        )

        self.assertEqual(result.pending.iloc[0]["reason"], "asset_not_open")
        self.assertEqual(result.nav.iloc[-1]["total_value"], 200000.0)

    def test_sell_executes_before_buy(self) -> None:
        market = pd.DataFrame(
            [
                _row("20180102"),
                _row("20180103"),
                _row("20180104"),
                _row("20180102", role="gold", code="518880.SH", open_price=2.0, close=2.0),
                _row("20180103", role="gold", code="518880.SH", open_price=2.0, close=2.0),
                _row("20180104", role="gold", code="518880.SH", open_price=2.0, close=2.0),
            ]
        )

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={
                "20180102": {"equity": 1.0, "gold": 0.0},
                "20180103": {"equity": 0.0, "gold": 1.0},
            },
        )

        final_day = result.trades.loc[result.trades["trade_date"].eq("20180104")]
        self.assertEqual(final_day.iloc[0]["side"], "SELL")
        self.assertEqual(final_day.iloc[-1]["side"], "BUY")

    def test_buys_largest_target_deficit_first(self) -> None:
        market = pd.DataFrame(
            [
                _row("20180102"),
                _row("20180103"),
                _row(
                    "20180102",
                    role="bond",
                    code="511260.SH",
                    open_price=10.0,
                    close=10.0,
                ),
                _row(
                    "20180103",
                    role="bond",
                    code="511260.SH",
                    open_price=10.0,
                    close=10.0,
                ),
            ]
        )

        result = replay_strategy(
            market,
            strategy="dynamic",
            initial_cash=200000.0,
            target_schedule={
                "20180102": {"equity": 0.80, "bond": 0.20}
            },
        )

        buys = result.trades.loc[result.trades["side"].eq("BUY")]
        self.assertEqual(buys.iloc[0]["role"], "equity")

    def test_account_identity_holds_to_one_cent(self) -> None:
        market = pd.DataFrame(
            [
                _row("20180102"),
                _row("20180103", open_price=4.10, close=4.20),
            ]
        )

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"equity": 1.0}},
        )

        difference = (
            result.nav["cash"]
            + result.nav["market_value"]
            - result.nav["total_value"]
        ).abs()
        self.assertLessEqual(difference.max(), 0.01)

    def test_close_policy_generates_next_open_target(self) -> None:
        market = pd.DataFrame(
            [
                _row("20180102"),
                _row("20180103", open_price=4.10, close=4.20),
            ]
        )

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={},
            target_policy=lambda date, weights, history: (
                {"equity": 1.0} if date == "20180102" else None
            ),
        )

        self.assertEqual(result.trades.iloc[0]["signal_date"], "20180102")
        self.assertEqual(result.trades.iloc[0]["trade_date"], "20180103")


if __name__ == "__main__":
    unittest.main()
