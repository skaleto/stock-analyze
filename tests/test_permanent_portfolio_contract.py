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


if __name__ == "__main__":
    unittest.main()
