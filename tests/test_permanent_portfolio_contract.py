from __future__ import annotations

import unittest

from stock_analyze.research.permanent_portfolio.contract import (
    STUDY_ID,
    canonical_hash,
    load_contract,
    transition_state,
)


class PermanentPortfolioContractTests(unittest.TestCase):
    def test_repository_contract_is_frozen(self) -> None:
        contract = load_contract("configs/research/permanent_portfolio_v1.yaml")

        self.assertEqual(contract.study_id, STUDY_ID)
        self.assertEqual(contract.initial_cash, 200000.0)
        self.assertEqual(contract.development_start, "20180101")
        self.assertEqual(contract.development_end, "20241231")
        self.assertEqual(contract.holdout_start, "20250101")
        self.assertEqual(
            [asset.code for asset in contract.assets],
            ["510300.SH", "511260.SH", "511880.SH", "518880.SH"],
        )
        self.assertEqual(
            contract.dynamic_rank_weights,
            (0.40, 0.30, 0.20, 0.10),
        )

    def test_holdout_cannot_open_twice(self) -> None:
        opened = transition_state(
            {"status": "development_complete"},
            "holdout_opened",
            expected_from="development_complete",
        )

        with self.assertRaisesRegex(ValueError, "permanent_portfolio_state"):
            transition_state(
                opened,
                "holdout_opened",
                expected_from="development_complete",
            )

    def test_hash_is_order_independent(self) -> None:
        self.assertEqual(
            canonical_hash({"a": 1, "b": 2}),
            canonical_hash({"b": 2, "a": 1}),
        )

    def test_development_window_rejects_holdout_date(self) -> None:
        contract = load_contract("configs/research/permanent_portfolio_v1.yaml")

        with self.assertRaisesRegex(ValueError, "development_window"):
            contract.assert_development_date("20250101")

    def test_v2_contract_changes_only_accounting_and_development_start(self) -> None:
        v1 = load_contract("configs/research/permanent_portfolio_v1.yaml")
        v2 = load_contract("configs/research/permanent_portfolio_v2.yaml")

        self.assertEqual(v2.study_id, "permanent_portfolio_v2")
        self.assertEqual(v2.accounting_version, "cash_distributions_v2")
        self.assertEqual(v2.development_start, "20180903")
        self.assertEqual(v2.development_end, v1.development_end)
        self.assertEqual(v2.holdout_start, v1.holdout_start)
        self.assertEqual(v2.initial_cash, v1.initial_cash)
        self.assertEqual(v2.assets, v1.assets)
        self.assertEqual(v2.lower_band, v1.lower_band)
        self.assertEqual(v2.upper_band, v1.upper_band)
        self.assertEqual(v2.fixed_target_weight, v1.fixed_target_weight)
        self.assertEqual(v2.dynamic_rank_weights, v1.dynamic_rank_weights)
        self.assertEqual(v2.tie_break_order, v1.tie_break_order)
        self.assertEqual(v2.lot_size, v1.lot_size)
        self.assertEqual(v2.commission_rate, v1.commission_rate)
        self.assertEqual(v2.minimum_commission, v1.minimum_commission)
        self.assertEqual(v2.slippage_rate, v1.slippage_rate)
        self.assertEqual(v2.stamp_tax_rate, v1.stamp_tax_rate)


if __name__ == "__main__":
    unittest.main()
