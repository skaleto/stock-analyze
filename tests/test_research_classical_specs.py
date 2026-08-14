from __future__ import annotations

import unittest

from stock_analyze.research.classical_specs import (
    a_share_h3_specs,
    a_share_h20_specs,
    qdii_h5_specs,
    qdii_h10_specs,
)


class ResearchClassicalSpecsTest(unittest.TestCase):
    def test_a_share_candidates_are_predeclared_and_hash_isolated(self) -> None:
        specs = a_share_h3_specs("hs300")

        self.assertEqual(len(specs), 4)
        self.assertEqual(len({item.spec_hash for item in specs}), 4)
        self.assertEqual({item.account_scope for item in specs}, {"hs300"})
        self.assertEqual({item.horizon for item in specs}, {3})
        self.assertEqual(
            {item.estimator for item in specs},
            {"ridge", "elastic_net", "hgbr", "fixed_blend"},
        )

    def test_qdii_candidates_use_h10_and_scope_local_identity(self) -> None:
        hk = qdii_h10_specs("hk")
        us = qdii_h10_specs("us")

        self.assertEqual({item.horizon for item in hk}, {10})
        self.assertEqual({item.account_scope for item in hk}, {"hk"})
        self.assertTrue({item.spec_hash for item in hk}.isdisjoint(
            {item.spec_hash for item in us}
        ))

    def test_qdii_h5_specs_match_the_live_iteration_horizon(self) -> None:
        specs = qdii_h5_specs("us_exposure")

        self.assertEqual(len(specs), 4)
        self.assertEqual({item.horizon for item in specs}, {5})
        self.assertEqual({item.account_scope for item in specs}, {"us_exposure"})
        self.assertEqual(
            {item.estimator for item in specs},
            {"ridge", "elastic_net", "hgbr", "fixed_blend"},
        )

    def test_a_share_h20_specs_define_one_cross_sectional_candidate(self) -> None:
        specs = a_share_h20_specs("hs300")

        self.assertEqual(len(specs), 1)
        self.assertEqual({item.horizon for item in specs}, {20})
        self.assertEqual({item.hypothesis_id for item in specs}, {"quality_lowvol"})
        self.assertEqual({item.rebalance_frequency for item in specs}, {"monthly"})
        self.assertTrue(all(item.feature_allowlist for item in specs))
        self.assertTrue(all("momentum_20" not in item.feature_allowlist for item in specs))
        self.assertEqual({item.estimator for item in specs}, {"ridge"})
        self.assertEqual(
            {item.ranking_target for item in specs},
            {"daily_cross_sectional_percentile_v1"},
        )
        self.assertEqual(
            {item.feature_selection_mode for item in specs},
            {"fixed_profile_v1"},
        )
        self.assertEqual(
            specs[0].spec_id,
            "h20_cross_sectional_quality_lowvol_ridge_v1",
        )

    def test_specs_are_serializable_for_the_trial_ledger(self) -> None:
        rows = [item.as_ledger_spec() for item in a_share_h3_specs("zz500")]

        self.assertTrue(all(row["spec_hash"] for row in rows))
        self.assertTrue(all(row["objective"] == "exact_net_active_return" for row in rows))
        self.assertTrue(all(row["ranking_target"] for row in rows))
        self.assertTrue(all(row["feature_selection_mode"] for row in rows))


if __name__ == "__main__":
    unittest.main()
