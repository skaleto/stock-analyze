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
    distribution_cash_per_share: float = 0.0,
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "role": role,
        "code": code,
        "open": open_price,
        "close": close,
        "adjusted_close": close,
        "adj_factor": 1.0,
        "distribution_cash_per_share": distribution_cash_per_share,
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

    def test_factor_change_does_not_create_phantom_wealth_for_same_day_buyer(self) -> None:
        first = _row("20180102")
        second = _row("20180103", open_price=2.0, close=2.0)
        second["adj_factor"] = 2.0
        second["adjusted_close"] = 4.0

        result = replay_strategy(
            pd.DataFrame([first, second]),
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"equity": 1.0}},
            lot_size=1,
            commission_rate=0.0,
            minimum_commission=0.0,
            slippage_rate=0.0,
        )

        self.assertEqual(result.trades.iloc[0]["price"], 2.0)
        self.assertEqual(result.nav.iloc[-1]["total_value"], 200000.0)

    def test_distribution_is_credited_to_pre_open_holders_once(self) -> None:
        first = _row("20180102", open_price=10.0, close=10.0)
        second = _row(
            "20180103",
            open_price=9.0,
            close=9.0,
            distribution_cash_per_share=1.0,
        )
        second["adj_factor"] = 10.0 / 9.0
        second["adjusted_close"] = 10.0

        result = replay_strategy(
            pd.DataFrame([first, second]),
            strategy="fixed",
            initial_cash=100.0,
            initial_positions={"equity": 100},
            target_schedule={},
            commission_rate=0.0,
            minimum_commission=0.0,
            slippage_rate=0.0,
        )

        self.assertEqual(result.nav.iloc[-1]["cash"], 200.0)
        self.assertEqual(result.nav.iloc[-1]["cash_distribution"], 100.0)
        self.assertEqual(result.nav.iloc[-1]["market_value"], 900.0)
        self.assertEqual(result.nav.iloc[-1]["total_value"], 1100.0)

    def test_distribution_survives_later_sale_without_double_counting(
        self,
    ) -> None:
        market = pd.DataFrame(
            [
                _row("20180102", open_price=10.0, close=10.0),
                _row(
                    "20180103",
                    open_price=9.0,
                    close=9.0,
                    distribution_cash_per_share=1.0,
                ),
                _row("20180104", open_price=9.0, close=9.0),
            ]
        )

        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=1.0,
            initial_positions={"equity": 100},
            target_schedule={"20180102": {"equity": 0.0}},
            lot_size=1,
            commission_rate=0.0,
            minimum_commission=0.0,
            slippage_rate=0.0,
            stamp_tax_rate=0.0,
        )

        self.assertEqual(result.trades.iloc[0]["side"], "SELL")
        self.assertEqual(result.trades.iloc[0]["trade_date"], "20180103")
        self.assertEqual(
            result.nav["cash_distribution"].tolist(),
            [0.0, 100.0, 0.0],
        )
        self.assertEqual(result.nav.iloc[-1]["cash"], 1001.0)
        self.assertEqual(result.nav.iloc[-1]["total_value"], 1001.0)

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
