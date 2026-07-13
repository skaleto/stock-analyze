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

    def test_regime_summary_keeps_market_current_and_lists_industries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            regime_dir = root / "data" / "research" / "regimes" / "cn_qdii_etf"
            regime_dir.mkdir(parents=True)
            pd.DataFrame([
                {"trade_date": "20260709", "scope": "market", "composite_regime": "mixed", "regime_coverage": 1.0},
                {"trade_date": "20260710", "scope": "market", "composite_regime": "risk_on", "regime_coverage": 1.0},
                {"trade_date": "20260710", "scope": "industry:科技", "composite_regime": "risk_on", "regime_coverage": 1.0},
                {"trade_date": "20260710", "scope": "industry:银行", "composite_regime": "risk_off", "regime_coverage": 1.0},
            ]).to_parquet(regime_dir / "20260710.parquet", index=False)

            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

        self.assertEqual(payload["regimes"]["current"]["scope"], "market")
        self.assertEqual(len(payload["regimes"]["history"]), 2)
        self.assertEqual({row["scope"] for row in payload["regimes"]["industries"]}, {"industry:科技", "industry:银行"})

    def test_missing_prediction_file_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")
        self.assertEqual(payload["prediction_summary"]["status"], "unavailable")
        self.assertEqual(payload["model_health"]["status"], "unavailable")

    def test_model_health_exposes_shadow_cycles_remaining(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            model_root = root / "data" / "research" / "models" / "cn_qdii_etf" / "5"
            model_root.mkdir(parents=True)
            (model_root / "model.metadata.json").write_text(json.dumps({"model_version": "m1", "horizon": 5, "metrics": {"brier_score": 0.2}}), encoding="utf-8")
            (model_root / "registry.json").write_text(json.dumps({"champion_model_version": "old", "models": {"m1": {"status": "shadow"}}}), encoding="utf-8")
            (model_root / "shadow_cycles.json").write_text(json.dumps({"models": {"m1": {"cycles": [{"week": "2026-W28"}, {"week": "2026-W29"}]}}}), encoding="utf-8")

            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

        self.assertEqual(payload["model_health"]["models"][0]["status"], "shadow")
        self.assertEqual(payload["model_health"]["models"][0]["shadow_cycles_remaining"], 2)

    def test_model_health_hides_superseded_research_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            model_root = root / "data" / "research" / "models" / "cn_qdii_etf" / "3"
            model_root.mkdir(parents=True)
            (model_root / "older.metadata.json").write_text(
                json.dumps({"model_version": "f999", "horizon": 3, "metrics": {"log_loss": 1.2}}),
                encoding="utf-8",
            )
            (model_root / "newer.metadata.json").write_text(
                json.dumps({"model_version": "a111", "horizon": 3, "metrics": {"log_loss": 1.0}}),
                encoding="utf-8",
            )
            (model_root / "registry.json").write_text(
                json.dumps({
                    "models": {
                        "f999": {"status": "research", "artifact": str(model_root / "older.joblib")},
                        "a111": {
                            "status": "research",
                            "artifact": str(model_root / "newer.joblib"),
                            "gate_history": [{"passed": False, "reasons": ["auc", "brier_improvement"]}],
                        },
                    }
                }),
                encoding="utf-8",
            )

            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

        models = payload["model_health"]["models"]
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["model_version"], "a111")
        self.assertEqual(models[0]["metrics"]["log_loss"], 1.0)
        self.assertEqual(models[0]["gate_reasons"], ["auc", "brier_improvement"])

    def test_model_health_prefers_shadow_over_newer_failed_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_detail_repo(root)
            model_root = root / "data" / "research" / "models" / "cn_qdii_etf" / "5"
            model_root.mkdir(parents=True)
            for version in ("shadow-v1", "failed-v2"):
                (model_root / f"{version}.metadata.json").write_text(
                    json.dumps({"model_version": version, "horizon": 5, "metrics": {"log_loss": 1.0}}),
                    encoding="utf-8",
                )
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "shadow-v1": {
                        "status": "shadow", "artifact": str(model_root / "shadow-v1.joblib"),
                        "registered_at": "2026-06-01T00:00:00+00:00",
                    },
                    "failed-v2": {
                        "status": "research", "artifact": str(model_root / "failed-v2.joblib"),
                        "registered_at": "2026-07-01T00:00:00+00:00",
                    },
                },
            }), encoding="utf-8")

            payload = build_dashboard_detail_data(repo_root=root, market="cn_qdii_etf", agent="codex")

        self.assertEqual(payload["model_health"]["models"][0]["model_version"], "shadow-v1")

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
