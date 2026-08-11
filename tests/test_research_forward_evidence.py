import tempfile
import unittest
import json
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.forward_evidence import load_forward_portfolio_evidence


class ResearchForwardEvidenceTest(unittest.TestCase):
    def test_requires_realized_nav_for_every_expected_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame([
                {"date": "2026-01-05", "account_id": "hs300", "total_value": 500_000, "benchmark_close": 4_000},
                {"date": "2026-01-12", "account_id": "hs300", "total_value": 501_000, "benchmark_close": 4_010},
            ]).to_csv(root / "daily_nav.csv", index=False)

            evidence = load_forward_portfolio_evidence(
                root,
                expected_account_ids=("hs300", "zz500"),
            )

        self.assertEqual(evidence["forward_evidence_status"], "insufficient_evidence")
        self.assertIn("missing_account:zz500", evidence["forward_evidence_gaps"])

    def test_reports_account_level_active_returns_cycles_drawdown_and_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dates = pd.date_range("2026-01-05", periods=13, freq="W-MON")
            rows = []
            for index, day in enumerate(dates):
                rows.extend([
                    {
                        "date": day.strftime("%Y-%m-%d"),
                        "account_id": "hs300",
                        "total_value": 500_000 * (1.01 ** index),
                        "benchmark_close": 4_000 * (1.004 ** index),
                    },
                    {
                        "date": day.strftime("%Y-%m-%d"),
                        "account_id": "zz500",
                        "total_value": 500_000 * (1.008 ** index),
                        "benchmark_close": 6_000 * (1.003 ** index),
                    },
                ])
            pd.DataFrame(rows).to_csv(root / "daily_nav.csv", index=False)
            pd.DataFrame([
                {
                    "trade_date": "2026-01-12", "account_id": "hs300",
                    "gross_amount": 100_000, "commission": 30,
                    "stamp_tax": 0, "slippage": 50,
                },
                {
                    "trade_date": "2026-01-12", "account_id": "zz500",
                    "gross_amount": 100_000, "commission": 30,
                    "stamp_tax": 50, "slippage": 50,
                },
            ]).to_csv(root / "trades.csv", index=False)

            evidence = load_forward_portfolio_evidence(
                root,
                expected_account_ids=("hs300", "zz500"),
            )

        self.assertEqual(evidence["forward_evidence_status"], "available")
        self.assertGreaterEqual(evidence["forward_cycles"], 12)
        self.assertGreater(evidence["forward_net_excess_return"], 0.0)
        self.assertTrue(evidence["forward_all_accounts_positive_active"])
        self.assertGreater(evidence["forward_execution_cost_bps"], 0.0)
        self.assertEqual(set(evidence["forward_account_metrics"]), {"hs300", "zz500"})

    def test_qdii_lookthrough_uses_conservative_account_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dates = pd.date_range("2026-01-05", periods=13, freq="W-MON")
            rows = []
            for index, day in enumerate(dates):
                for account_id, benchmark_close in (("us_exposure", 5_000), ("hk_exposure", 3_000)):
                    rows.append({
                        "date": day.strftime("%Y-%m-%d"),
                        "account_id": account_id,
                        "total_value": 500_000 * (1.005 ** index),
                        "benchmark_close": benchmark_close * (1.002 ** index),
                    })
            pd.DataFrame(rows).to_csv(root / "daily_nav.csv", index=False)
            (root / "shadow_status.json").write_text(
                json.dumps({
                    "accounts": [
                        {
                            "account_id": "us_exposure",
                            "optimizer_diagnostics": {
                                "underlying_profile_coverage": 0.92,
                                "underlying_company_weight_coverage": 0.71,
                            },
                        },
                        {
                            "account_id": "hk_exposure",
                            "optimizer_diagnostics": {
                                "underlying_profile_coverage": 0.83,
                                "underlying_company_weight_coverage": 0.64,
                            },
                        },
                    ]
                }),
                encoding="utf-8",
            )

            evidence = load_forward_portfolio_evidence(
                root,
                expected_account_ids=("us_exposure", "hk_exposure"),
                require_lookthrough=True,
            )

        self.assertEqual(evidence["lookthrough_evidence_status"], "available")
        self.assertEqual(evidence["underlying_profile_coverage"], 0.83)
        self.assertEqual(evidence["underlying_company_weight_coverage"], 0.64)
        self.assertEqual(
            set(evidence["lookthrough_account_metrics"]),
            {"us_exposure", "hk_exposure"},
        )

    def test_qdii_lookthrough_fails_closed_when_status_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = load_forward_portfolio_evidence(
                tmp,
                expected_account_ids=("us_exposure", "hk_exposure"),
                require_lookthrough=True,
            )

        self.assertEqual(
            evidence["lookthrough_evidence_status"],
            "insufficient_evidence",
        )
        self.assertIn("shadow_status_missing", evidence["lookthrough_evidence_gaps"])


if __name__ == "__main__":
    unittest.main()
