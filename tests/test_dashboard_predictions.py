import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests.test_dashboard_app_api import _seed_detail_repo
from stock_analyze.dashboard_aggregator import (
    DashboardDataError,
    build_dashboard_detail_data,
    build_dashboard_instrument_data,
)


class DashboardPredictionsTest(unittest.TestCase):
    def test_detail_exposes_four_horizons_alerts_regime_and_source_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            prediction_dir = root / "data" / "cn_qdii_etf" / "codex" / "predictions"
            prediction_dir.mkdir(parents=True)
            rows = []
            for horizon in (3, 5, 10, 20):
                rows.append(
                    {
                        "as_of": "2026-07-10", "code": "513100", "horizon": horizon,
                        "p_up": 0.72, "p_flat": 0.18, "p_down": 0.10,
                        "confidence": 0.81, "expected_excess_return": 0.04,
                        "return_q10": -0.02, "return_q50": 0.03, "return_q90": 0.09,
                        "regime": "risk_on", "reasons": '["趋势加速", "资金流确认"]',
                        "invalidation": '["跌破20日均线"]', "model_version": "model-v1",
                        "active_status": "inactive",
                    }
                )
            pd.DataFrame(rows).to_parquet(prediction_dir / "20260710.parquet", index=False)
            regime_dir = root / "data" / "research" / "regimes" / "cn_qdii_etf"
            regime_dir.mkdir(parents=True)
            pd.DataFrame([{"trade_date": "20260710", "composite_regime": "risk_on", "regime_coverage": 0.8}]).to_parquet(regime_dir / "20260710.parquet", index=False)

            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

        self.assertEqual(payload["prediction_summary"]["status"], "available")
        self.assertEqual(payload["prediction_summary"]["horizons"], [3, 5, 10, 20])
        self.assertEqual(payload["prediction_summary"]["rows"][0]["confidence"], 0.81)
        self.assertTrue(payload["alerts"])
        self.assertEqual(payload["regimes"]["current"]["composite_regime"], "risk_on")
        self.assertEqual({item["source"] for item in payload["source_health"]}, {"news", "announcement", "policy"})

    def test_missing_prediction_file_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")
        self.assertEqual(payload["prediction_summary"]["status"], "unavailable")
        self.assertEqual(payload["model_health"]["status"], "unavailable")

    def test_corrupt_prediction_file_raises_dashboard_data_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            prediction_dir = root / "data" / "cn_qdii_etf" / "codex" / "predictions"
            prediction_dir.mkdir(parents=True)
            (prediction_dir / "20260710.parquet").write_text("broken", encoding="utf-8")
            with self.assertRaisesRegex(DashboardDataError, "prediction_artifact"):
                build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

    def test_instrument_includes_predictions_and_event_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
            pd.DataFrame([
                {"ts_code": "513100.SH", "trade_date": "20260710", "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2, "vol": 1000, "amount": 1200},
                {"ts_code": "513100.SH", "trade_date": "20260709", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "vol": 900, "amount": 1000},
            ]).to_csv(cache / "fund_daily_513100_SH_20260710.csv", index=False)
            predictions = root / "data" / "cn_qdii_etf" / "codex" / "predictions"
            predictions.mkdir(parents=True)
            pd.DataFrame([{"as_of": "2026-07-10", "code": "513100", "horizon": 5, "p_up": 0.7, "p_flat": 0.2, "p_down": 0.1, "confidence": 0.8}]).to_parquet(predictions / "20260710.parquet", index=False)
            events = root / "data" / "research" / "events" / "cn_qdii_etf"
            events.mkdir(parents=True)
            pd.DataFrame([{"event_id": "e1", "event": "macd_golden_cross", "code": "513100", "trade_date": "20260710", "regime": "risk_on"}]).to_parquet(events / "20260710.parquet", index=False)

            payload = build_dashboard_instrument_data(repo_root=root, market="cn_qdii_etf", agent="codex", code="513100")

        self.assertEqual(payload["predictions"][0]["horizon"], 5)
        self.assertEqual(payload["event_evidence"][0]["event"], "macd_golden_cross")
        self.assertTrue(payload["source_health"])


if __name__ == "__main__":
    unittest.main()
