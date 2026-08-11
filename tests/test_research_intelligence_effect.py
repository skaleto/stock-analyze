from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.intelligence_effect import (
    evaluate_latest_intelligence_effect,
    evaluate_intelligence_increment,
    refresh_latest_intelligence_features,
)
from stock_analyze.research.storage import ResearchStore


class FakeBundle:
    def __init__(self, feature_columns, *, candidate: bool) -> None:
        self.feature_columns = tuple(feature_columns)
        uplift = 0.02 if candidate else 0.0
        self.metrics = {
            "rank_ic": 0.04 + uplift,
            "portfolio_sharpe": 0.50 + uplift,
            "brier_improvement": 0.01 + uplift,
            "net_excess_return": 0.03 + uplift,
            "max_drawdown": 0.10 - uplift,
            "annual_turnover": 3.0 + uplift,
            "selected_features": list(feature_columns),
            "portfolio_period_return_dates": [
                "20260101",
                "20260106",
                "20260111",
            ],
            "portfolio_period_returns": [
                0.01 + uplift,
                -0.002 + uplift,
                0.006 + uplift,
            ],
        }

    def predict_excess_return(self, frame: pd.DataFrame) -> np.ndarray:
        result = pd.to_numeric(
            frame["base_signal"],
            errors="coerce",
        ).fillna(0.0).to_numpy()
        if "event_net_strength_5d" in self.feature_columns:
            result = result + pd.to_numeric(
                frame["event_net_strength_5d"],
                errors="coerce",
            ).fillna(0.0).to_numpy()
        return result


class IntelligenceEffectTest(unittest.TestCase):
    def test_refresh_uses_canonical_data_research_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResearchStore(root / "data" / "research")
            features = pd.DataFrame(
                {
                    "code": ["000001"],
                    "trade_date": ["20260728"],
                    "base_signal": [1.0],
                }
            )
            labels = pd.DataFrame(
                {
                    "code": ["000001"],
                    "trade_date": ["20260728"],
                    "horizon": [5],
                    "label": ["flat"],
                }
            )
            store.write_feature_snapshot("a_share", "20260728", features)
            store.write_label_snapshot("a_share", "20260728", labels)

            report = refresh_latest_intelligence_features(
                root,
                market="a_share",
                as_of="20260728",
            )

            self.assertEqual(report["status"], "complete")
            self.assertIn(
                str(root / "data" / "research"),
                report["snapshot_path"],
            )
            self.assertFalse((root / "data" / "shared" / "research").exists())

    def test_latest_effect_skips_large_labels_when_event_support_is_sparse(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResearchStore(root / "data" / "research")
            features = pd.DataFrame(
                {
                    "code": ["000001"] * 12,
                    "trade_date": [
                        f"202607{day:02d}" for day in range(1, 13)
                    ],
                    "base_signal": range(12),
                    "event_net_strength_5d": [0.0] * 12,
                    "event_data_coverage": [0.0] * 12,
                }
            )
            labels = pd.DataFrame(
                {
                    "code": ["000001"],
                    "trade_date": ["20260701"],
                    "horizon": [5],
                    "label": ["flat"],
                }
            )
            store.write_feature_snapshot("a_share", "20260728", features)
            store.write_label_snapshot("a_share", "20260728", labels)

            with patch.object(
                ResearchStore,
                "read_label_snapshot",
                side_effect=AssertionError("labels should not be read"),
            ):
                report = evaluate_latest_intelligence_effect(
                    root,
                    market="a_share",
                    as_of="20260728",
                )

            self.assertEqual(report["status"], "insufficient_support")
            self.assertEqual(report["qualified_horizons"], 0)

    def test_paired_evaluation_uses_same_dataset_seed_and_reports_delta(
        self,
    ) -> None:
        rows = []
        for day in range(1, 31):
            for code in range(6):
                event = float(code - 2)
                rows.append({
                    "code": f"{code:06d}",
                    "trade_date": f"202606{day:02d}",
                    "label_end_date": f"202607{day:02d}",
                    "label": "up" if code >= 3 else "down",
                    "excess_return": event * 0.01,
                    "base_signal": float(code),
                    "event_net_strength_5d": event,
                    "event_data_coverage": 1.0,
                })
        calls = []

        def trainer(data, *, feature_columns, horizon, random_state):
            calls.append({
                "rows": len(data),
                "features": tuple(feature_columns),
                "horizon": horizon,
                "random_state": random_state,
            })
            return FakeBundle(
                feature_columns,
                candidate="event_net_strength_5d" in feature_columns,
            )

        report = evaluate_intelligence_increment(
            pd.DataFrame(rows),
            base_features=("base_signal",),
            event_features=(
                "event_net_strength_5d",
                "event_data_coverage",
            ),
            horizon=5,
            trainer=trainer,
            random_state=71,
        )

        self.assertEqual(report["status"], "complete")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["rows"], calls[1]["rows"])
        self.assertEqual(calls[0]["random_state"], 71)
        self.assertEqual(calls[1]["random_state"], 71)
        self.assertAlmostEqual(report["deltas"]["rank_ic"], 0.02)
        self.assertGreater(
            report["paired_period_return"]["mean_delta"],
            0.0,
        )
        self.assertIn(
            "event_net_strength_5d",
            report["permutation_importance"],
        )
        self.assertEqual(
            report["feature_admission"]["status"],
            "shadow_eligible",
        )
        self.assertTrue(report["feature_admission"]["passed"])
        self.assertEqual(
            report["feature_admission"]["maximum_consumer_status"],
            "shadow",
        )

    def test_sparse_event_history_does_not_train_or_claim_uplift(
        self,
    ) -> None:
        frame = pd.DataFrame({
            "code": ["000001"] * 12,
            "trade_date": [f"202606{day:02d}" for day in range(1, 13)],
            "label_end_date": [f"202607{day:02d}" for day in range(1, 13)],
            "label": ["flat"] * 12,
            "excess_return": [0.0] * 12,
            "base_signal": range(12),
            "event_net_strength_5d": [0.0] * 12,
            "event_data_coverage": [0.0] * 12,
        })

        report = evaluate_intelligence_increment(
            frame,
            base_features=("base_signal",),
            event_features=("event_net_strength_5d",),
            horizon=5,
            trainer=lambda *args, **kwargs: self.fail("must not train"),
        )

        self.assertEqual(report["status"], "insufficient_support")
        self.assertEqual(report["support"]["active_dates"], 0)
        self.assertNotIn("deltas", report)


if __name__ == "__main__":
    unittest.main()
