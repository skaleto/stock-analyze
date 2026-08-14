from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_analyze.research.unified_arena import build_unified_arena_report


class UnifiedModelArenaTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict, dict[str, dict], dict]:
        tournament_root = (
            root / "data" / "research" / "models" / "cn_qdii_etf"
            / "us_exposure" / "5" / "tournaments" / "20260813"
        )
        candidate_root = tournament_root / "candidates" / "ridge"
        candidate_root.mkdir(parents=True)
        final = pd.DataFrame([
            {
                "code": code,
                "trade_date": day,
                "account_id": "us_exposure",
                "research_scope": "us_exposure",
                "horizon": 5,
                "entry_date": day,
                "entry_price": 10.0,
                "benchmark_entry_price": 10.0,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "volume": 1000.0,
                "momentum_20": score,
                "momentum_60": score,
                "low_volatility_60": 0.1,
                "avg_amount_20": 100000.0,
                "discount_premium": 0.0,
            }
            for day in ("20260701", "20260702")
            for code, score in (("513100", 0.2), ("513500", 0.1))
        ])
        final.to_parquet(candidate_root / "final_predictions.parquet", index=False)
        pd.DataFrame({
            "signal_date": ["20260701", "20260702"],
        }).to_parquet(candidate_root / "final_periods.parquet", index=False)
        report_path = tournament_root / "report.json"
        report = {
            "market": "cn_qdii_etf",
            "account_scope": "us_exposure",
            "horizon": 5,
            "report_path": str(report_path),
            "final_window": ["20260701", "20260702"],
            "candidates": [{
                "spec_id": "ridge",
                "model_version": "model-v1",
                "status": "research",
                "metrics": {
                    "net_return": 0.03,
                    "benchmark_return": 0.01,
                    "net_excess_return": 0.02,
                    "information_ratio": 0.5,
                    "max_drawdown": 0.01,
                    "annual_turnover": 2.0,
                    "trade_count": 2,
                },
            }],
            "baselines": [{
                "spec_id": "baseline_cash",
                "net_excess_return": -0.01,
                "sharpe": -0.2,
            }],
        }
        overlays = {
            "defensive": {"name": "稳健防守", "factors": {"low_volatility_60": {"weight": 1.0}}},
            "trend": {"name": "趋势进攻", "factors": {"momentum_20": {"weight": 1.0}}},
        }
        baseline = {
            "accounts": [{
                "id": "us_exposure",
                "scope": "us_exposure",
                "top_n": 1,
                "cash": 500000,
                "benchmark": "513100.SH",
            }],
            "trading": {},
            "performance": {},
        }
        return report, overlays, baseline

    def test_report_compares_rules_models_and_baselines_on_identical_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, overlays, baseline = self._fixture(root)
            periods = pd.DataFrame({"signal_date": ["20260701", "20260702"]})
            metrics = {
                "net_return": 0.02,
                "benchmark_return": 0.01,
                "net_excess_return": 0.01,
                "information_ratio": 0.3,
                "max_drawdown": 0.01,
                "annual_turnover": 1.0,
                "trade_count": 1,
            }

            with patch(
                "stock_analyze.research.unified_arena._run_overlay",
                return_value=(metrics, pd.DataFrame(), pd.DataFrame(), periods),
            ):
                result = build_unified_arena_report(
                    root,
                    market="cn_qdii_etf",
                    horizon=5,
                    as_of="20260813",
                    tournament_reports=[report],
                    overlays=overlays,
                    baseline=baseline,
                )

            saved = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))

        participants = saved["scopes"][0]["participants"]
        self.assertEqual(
            {item["participant_type"] for item in participants},
            {"formal_rule", "candidate_model", "baseline"},
        )
        self.assertEqual(
            {tuple(item["evaluation_dates"]) for item in participants},
            {("20260701", "20260702")},
        )
        self.assertEqual(saved["evidence_type"], "historical_diagnostic")
        self.assertEqual(saved["scopes"][0]["winner"]["participant_id"], "model:model-v1")

    def test_report_rejects_rule_period_date_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, overlays, baseline = self._fixture(root)
            periods = pd.DataFrame({"signal_date": ["20260630"]})

            with patch(
                "stock_analyze.research.unified_arena._run_overlay",
                return_value=({}, pd.DataFrame(), pd.DataFrame(), periods),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "unified_arena_date_mismatch",
                ):
                    build_unified_arena_report(
                        root,
                        market="cn_qdii_etf",
                        horizon=5,
                        as_of="20260813",
                        tournament_reports=[report],
                        overlays=overlays,
                        baseline=baseline,
                    )


if __name__ == "__main__":
    unittest.main()
