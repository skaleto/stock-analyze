from __future__ import annotations

import unittest

from stock_analyze.research.classical_specs import (
    a_share_h3_specs,
    a_share_h20_specs,
    incremental_residual_specs,
    mainline_horizon,
    mainline_specs,
    qdii_h5_specs,
    qdii_h10_specs,
    transparent_strategy_specs,
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
        self.assertEqual(
            {item.hypothesis_id for item in specs},
            {"momentum_baseline_residual"},
        )
        self.assertEqual({item.rebalance_frequency for item in specs}, {"monthly"})
        self.assertTrue(all(item.feature_allowlist for item in specs))
        self.assertTrue(all(
            {"momentum_20", "momentum_60"}.issubset(item.feature_allowlist)
            for item in specs
        ))
        self.assertEqual({item.estimator for item in specs}, {"ridge"})
        self.assertEqual(
            {item.ranking_target for item in specs},
            {"momentum_anchor_residual_v1"},
        )
        self.assertEqual(
            {item.feature_selection_mode for item in specs},
            {"fixed_profile_v1"},
        )
        self.assertEqual(
            specs[0].spec_id,
            "h20_momentum_baseline_residual_ridge_v1",
        )
        self.assertEqual(specs[0].parameter_map["residual_tilt_weight"], 0.10)

    def test_qdii_h10_mainline_is_weekly_trend_residual_ridge(self) -> None:
        spec = mainline_specs("cn_qdii_etf", "us_exposure")[0]

        self.assertEqual(spec.spec_id, "h10_trend_baseline_residual_ridge_v1")
        self.assertEqual(spec.estimator, "ridge")
        self.assertEqual(spec.rebalance_frequency, "weekly")
        self.assertEqual(spec.ranking_target, "qdii_trend_anchor_residual_v1")
        self.assertEqual(spec.feature_selection_mode, "fixed_profile_v1")
        self.assertEqual(spec.parameter_map["residual_tilt_weight"], 0.10)

    def test_specs_are_serializable_for_the_trial_ledger(self) -> None:
        rows = [item.as_ledger_spec() for item in a_share_h3_specs("zz500")]

        self.assertTrue(all(row["spec_hash"] for row in rows))
        self.assertTrue(all(row["objective"] == "exact_net_active_return" for row in rows))
        self.assertTrue(all(row["ranking_target"] for row in rows))
        self.assertTrue(all(row["feature_selection_mode"] for row in rows))

    def test_each_market_has_one_declared_mainline(self) -> None:
        self.assertEqual(mainline_horizon("a_share"), 20)
        self.assertEqual(mainline_horizon("cn_qdii_etf"), 10)

        a_share = mainline_specs("a_share", "hs300")
        qdii = mainline_specs("cn_qdii_etf", "us_exposure")

        self.assertEqual(len(a_share), 1)
        self.assertEqual(len(qdii), 1)
        self.assertEqual(a_share[0].horizon, 20)
        self.assertEqual(qdii[0].horizon, 10)
        self.assertEqual(qdii[0].estimator, "ridge")
        self.assertEqual(qdii[0].rebalance_frequency, "weekly")

    def test_mainline_rejects_undeclared_market(self) -> None:
        with self.assertRaisesRegex(ValueError, "classical_mainline_market"):
            mainline_horizon("us_equity")

    def test_strategy_recovery_predeclares_six_transparent_specs_per_scope(self) -> None:
        a_share = transparent_strategy_specs("a_share", "hs300")
        qdii = transparent_strategy_specs("cn_qdii_etf", "hk_exposure")

        self.assertEqual(
            [item.spec_id for item in a_share],
            [
                "A_MOM_01",
                "A_MOM_02",
                "A_QMLV_01",
                "A_QMLV_02",
                "A_REGIME_01",
                "A_REGIME_02",
            ],
        )
        self.assertEqual(
            [item.spec_id for item in qdii],
            [
                "Q_TREND_01",
                "Q_TREND_02",
                "Q_DUAL_01",
                "Q_DUAL_02",
                "Q_TRACK_01",
                "Q_TRACK_02",
            ],
        )
        self.assertEqual({item.estimator for item in (*a_share, *qdii)}, {"rule"})
        self.assertEqual({item.rebalance_frequency for item in a_share}, {"monthly"})
        self.assertEqual({item.rebalance_frequency for item in qdii}, {"weekly"})
        self.assertEqual(len({item.spec_hash for item in (*a_share, *qdii)}), 12)
        self.assertEqual(qdii[1].parameter_map["max_risky_exposure"], 0.85)
        self.assertEqual(qdii[5].parameter_map["max_risky_exposure"], 0.85)

    def test_strategy_recovery_specs_are_scope_local_without_scope_tuning(self) -> None:
        hs300 = transparent_strategy_specs("a_share", "hs300")
        zz500 = transparent_strategy_specs("a_share", "zz500")

        self.assertEqual(
            [item.parameter_map for item in hs300],
            [item.parameter_map for item in zz500],
        )
        self.assertTrue(
            {item.spec_hash for item in hs300}.isdisjoint(
                {item.spec_hash for item in zz500}
            )
        )

    def test_incremental_residual_budget_is_two_fixed_models(self) -> None:
        a_share = incremental_residual_specs(
            "a_share",
            "hs300",
            baseline_spec_id="A_MOM_01",
        )
        qdii = incremental_residual_specs(
            "cn_qdii_etf",
            "hk_exposure",
            baseline_spec_id="Q_TREND_01",
        )

        self.assertEqual([item.estimator for item in a_share], ["ridge", "hgbr"])
        self.assertEqual([item.parameter_map["residual_tilt_weight"] for item in a_share], [0.05, 0.05])
        self.assertEqual(a_share[0].parameter_map["alpha"], 25.0)
        self.assertEqual(qdii[0].parameter_map["alpha"], 35.0)
        self.assertEqual(a_share[1].parameter_map["max_leaf_nodes"], 7)
        self.assertEqual(a_share[1].random_state, 20260814)


if __name__ == "__main__":
    unittest.main()
