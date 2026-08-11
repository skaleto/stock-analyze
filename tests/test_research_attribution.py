import unittest

from stock_analyze.research.attribution import (
    DailyAttributionInput,
    attribute_daily_pnl,
)


class ResearchAttributionTest(unittest.TestCase):
    def test_a_share_components_reconcile_to_net_pnl(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=1_000.0,
                before_weights={"000001.SZ": 0.6, "600000.SH": 0.3},
                after_weights={"000001.SZ": 0.5, "600000.SH": 0.4},
                security_returns={"000001.SZ": 0.02, "600000.SH": -0.01},
                benchmark_returns={"000300.SH": 0.01},
                benchmark_exposures={
                    "000001.SZ": {"000300.SH": 1.0},
                    "600000.SH": {"000300.SH": 1.0},
                },
                factor_exposures={
                    "000001.SZ": {"industry:bank": 1.0, "quality": 0.5},
                    "600000.SH": {"industry:bank": 1.0, "quality": -0.2},
                },
                factor_returns={"industry:bank": 0.002, "quality": 0.006},
                cash_return=0.001,
                estimated_fees=0.8,
                realized_fees=1.2,
                model_selection_effects={"ranker": -2.0},
                sizing_effects={"weighting": -0.74},
                timing_effects={"next_open": -0.5},
                constraint_effects={"industry_cap": -0.3},
                strategy_id="trend",
                account_id="hs300",
                model_policy_status="active",
                model_versions={"5d": "A20-V005"},
            )
        )

        self.assertEqual(result.status, "complete")
        self.assertAlmostEqual(result.market, 9.0)
        self.assertAlmostEqual(result.industry, 1.8)
        self.assertAlmostEqual(result.alpha, 1.44)
        self.assertAlmostEqual(result.cash, 0.1)
        self.assertAlmostEqual(result.cost, -1.2)
        self.assertAlmostEqual(result.model_selection, -2.0)
        self.assertAlmostEqual(result.sizing, -0.74)
        self.assertAlmostEqual(result.timing, -0.5)
        self.assertAlmostEqual(result.constraint, -0.3)
        self.assertAlmostEqual(result.residual, 0.0)
        self.assertAlmostEqual(result.net_pnl, 7.6)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)
        self.assertAlmostEqual(result.explained_ratio, 1.0)
        self.assertAlmostEqual(result.residual_ratio, 0.0)
        self.assertEqual(result.model_versions, {"5d": "A20-V005"})
        self.assertTrue(result.is_reconciled)
        self.assertAlmostEqual(
            sum(item.gross_pnl or 0.0 for item in result.security),
            9.0,
        )

    def test_realized_fees_override_estimate_and_keep_estimation_error(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=100.0,
                before_weights={"000001.SZ": 1.0},
                after_weights={"000001.SZ": 0.8},
                security_returns={"000001.SZ": 0.01},
                estimated_fees={"buy": 0.4},
                realized_fees={"commission": 0.7, "tax": 0.3},
            )
        )

        self.assertEqual(result.cost_basis, "realized")
        self.assertAlmostEqual(result.cost, -1.0)
        self.assertAlmostEqual(result.cost_estimation_error, -0.6)
        self.assertAlmostEqual(result.net_pnl, 0.0)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)

    def test_cash_return_uses_opening_cash_weight(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=2_000.0,
                before_weights={"000001.SZ": 0.75},
                after_weights={"000001.SZ": 0.75},
                security_returns={"000001.SZ": 0.0},
                cash_return=0.002,
            )
        )

        self.assertAlmostEqual(result.cash, 1.0)
        self.assertAlmostEqual(result.net_pnl, 1.0)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)

    def test_qdii_fx_and_premium_are_explicit_alpha_subcomponents(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="cn_qdii_etf",
                as_of="2026-07-24",
                opening_nav=1_000.0,
                before_weights={"513100.SH": 0.8},
                after_weights={"513100.SH": 0.8},
                security_returns={"513100.SH": 0.03},
                benchmark_returns={"NDX": 0.01},
                benchmark_exposures={"513100.SH": {"NDX": 1.0}},
                factor_exposures={
                    "513100.SH": {"fx:USD_CNY": 0.5, "premium": 0.25}
                },
                factor_returns={"fx:USD_CNY": 0.02, "premium": -0.04},
                cash_return=0.0,
                timing_effects={"overseas_close_gap": 16.0},
            )
        )

        self.assertAlmostEqual(result.market, 8.0)
        self.assertAlmostEqual(result.alpha, 0.0)
        self.assertEqual(set(result.qdii_breakdown), {"fx", "premium"})
        self.assertAlmostEqual(result.qdii_breakdown["fx"], 8.0)
        self.assertAlmostEqual(result.qdii_breakdown["premium"], -8.0)
        self.assertAlmostEqual(result.timing, 16.0)
        self.assertAlmostEqual(result.residual, 0.0)
        self.assertAlmostEqual(result.net_pnl, 24.0)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)

    def test_large_residual_is_partial_even_when_additive_identity_reconciles(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=100.0,
                before_weights={"000001.SZ": 1.0},
                after_weights={"000001.SZ": 1.0},
                security_returns={"000001.SZ": 0.02},
                benchmark_returns={"000300.SH": 0.01},
                benchmark_exposures={
                    "000001.SZ": {"000300.SH": 1.0},
                },
                factor_exposures={"000001.SZ": {"quality": 0.002}},
                factor_returns={"quality": 0.5},
            )
        )

        self.assertEqual(result.status, "partial")
        self.assertAlmostEqual(result.residual, 0.9)
        self.assertGreater(result.residual_ratio, 0.05)
        self.assertLess(result.explained_ratio, 0.95)
        self.assertIn("residual_above_limit", result.unavailable_inputs)

    def test_rule_only_decision_cannot_claim_model_contribution(self):
        with self.assertRaisesRegex(
            ValueError,
            "attribution_model_effect_for_rule_only",
        ):
            attribute_daily_pnl(
                DailyAttributionInput(
                    market="a_share",
                    as_of="2026-07-24",
                    opening_nav=100.0,
                    before_weights={"000001.SZ": 1.0},
                    after_weights={"000001.SZ": 1.0},
                    security_returns={"000001.SZ": 0.01},
                    model_selection_effects={"ranker": 1.0},
                    model_policy_status="rule_only",
                    model_versions={"5d": "A20-V005"},
                )
            )

    def test_market_factor_is_included_in_market_component(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=100.0,
                before_weights={"000001.SZ": 1.0},
                after_weights={"000001.SZ": 1.0},
                security_returns={"000001.SZ": 0.01},
                factor_exposures={"000001.SZ": {"market_beta": 1.0}},
                factor_returns={"market_beta": 0.01},
            )
        )

        self.assertAlmostEqual(result.market, 1.0)
        self.assertAlmostEqual(result.residual, 0.0)
        self.assertAlmostEqual(result.net_pnl, 1.0)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)

    def test_missing_security_return_without_observed_pnl_is_unavailable(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=1_000.0,
                before_weights={"000001.SZ": 0.5, "600000.SH": 0.5},
                after_weights={"000001.SZ": 0.5, "600000.SH": 0.5},
                security_returns={"000001.SZ": 0.01},
            )
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.net_pnl)
        self.assertIsNone(result.reconciliation_delta)
        self.assertIn("security_return:600000.SH", result.unavailable_inputs)
        unavailable = {
            item.code: item.status
            for item in result.security
        }
        self.assertEqual(unavailable["600000.SH"], "unavailable")

    def test_missing_factor_return_is_reported_and_goes_to_residual(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=100.0,
                before_weights={"000001.SZ": 1.0},
                after_weights={"000001.SZ": 1.0},
                security_returns={"000001.SZ": 0.02},
                factor_exposures={"000001.SZ": {"quality": 0.5}},
                factor_returns={},
            )
        )

        self.assertEqual(result.status, "partial")
        self.assertIn("factor_return:quality", result.unavailable_inputs)
        self.assertAlmostEqual(result.alpha, 0.0)
        self.assertAlmostEqual(result.residual, 2.0)
        self.assertAlmostEqual(result.net_pnl, 2.0)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)

    def test_observed_pnl_can_reconcile_partial_input_without_fabrication(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="cn_qdii_etf",
                as_of="2026-07-24",
                opening_nav=1_000.0,
                before_weights={"513100.SH": 0.5, "513500.SH": 0.5},
                after_weights={"513100.SH": 0.5, "513500.SH": 0.5},
                security_returns={"513100.SH": 0.01},
                realized_fees=1.0,
                observed_net_pnl=3.0,
            )
        )

        self.assertEqual(result.status, "partial")
        self.assertAlmostEqual(result.net_pnl, 3.0)
        self.assertAlmostEqual(result.residual, 4.0)
        self.assertAlmostEqual(result.reconciliation_delta, 0.0)
        self.assertIn("security_return:513500.SH", result.unavailable_inputs)

    def test_declared_unavailable_dimensions_keep_nav_attribution_partial(self):
        result = attribute_daily_pnl(
            DailyAttributionInput(
                market="a_share",
                as_of="2026-07-24",
                opening_nav=100.0,
                before_weights={"__PORTFOLIO__": 0.8},
                after_weights={"__PORTFOLIO__": 0.8},
                security_returns={"__PORTFOLIO__": 0.01},
                observed_net_pnl=0.8,
                declared_unavailable_inputs=(
                    "industry_attribution",
                    "factor_attribution",
                ),
            )
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.reconciliation_delta, 0.0)
        self.assertIn("industry_attribution", result.unavailable_inputs)

    def test_result_and_lineage_rows_are_deterministic(self):
        attribution_input = DailyAttributionInput(
            market="a_share",
            as_of="2026-07-24",
            opening_nav=100.0,
            before_weights={"600000.SH": 0.4, "000001.SZ": 0.5},
            after_weights={"600000.SH": 0.3, "000001.SZ": 0.6},
            security_returns={"600000.SH": -0.01, "000001.SZ": 0.02},
            constraint_effects={"turnover_cap": -0.1},
        )

        first = attribute_daily_pnl(attribution_input)
        second = attribute_daily_pnl(attribution_input)
        self.assertEqual(first, second)
        self.assertEqual(
            [item.code for item in first.security],
            ["000001.SZ", "600000.SH"],
        )

        first_rows = first.to_lineage_rows(
            "decision-a-share-20260724",
            fill_ids={"000001.SZ": "fill-1"},
        )
        second_rows = second.to_lineage_rows(
            "decision-a-share-20260724",
            fill_ids={"000001.SZ": "fill-1"},
        )
        self.assertEqual(first_rows, second_rows)
        self.assertEqual(first_rows[0]["security_code"], "__PORTFOLIO__")
        self.assertTrue(
            all(row["decision_run_id"] == "decision-a-share-20260724"
                for row in first_rows)
        )
        self.assertEqual(first_rows[1]["fill_id"], "fill-1")
        self.assertIn("reconciliation_delta", first_rows[0])


if __name__ == "__main__":
    unittest.main()
