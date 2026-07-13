import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.pipeline import ResearchPipeline
from stock_analyze.research.source_features import SourceCollection
from stock_analyze.research.schemas import PredictionRecord


class ResearchPipelineTest(unittest.TestCase):
    @staticmethod
    def _passing_gate_metrics() -> dict:
        return {
            "feature_coverage": 0.97,
            "point_in_time_audit": True,
            "oos_predictions": 500,
            "rank_ic": 0.04,
            "icir": 0.55,
            "brier_improvement": 0.06,
            "hit_rate_uplift": 0.06,
            "auc": 0.59,
            "net_excess_return": 0.03,
            "max_drawdown": 0.12,
            "annual_turnover": 4.0,
            "ablation_stability": 0.82,
        }

    def _write_history(self, root: Path, rows: int = 140, code: str = "000001") -> None:
        cache = root / "data" / "shared" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        dates = pd.date_range("2026-01-01", periods=rows, freq="B")
        close = 10.0 + np.sin(np.arange(rows) / 5.0) + np.arange(rows) * 0.01
        pd.DataFrame(
            {
                "日期": dates.strftime("%Y-%m-%d"),
                "开盘": close - 0.1,
                "最高": close + 0.3,
                "最低": close - 0.3,
                "收盘": close,
                "成交量": 1_000_000 + np.arange(rows) * 1000,
                "成交额": 20_000_000 + np.arange(rows) * 10_000,
            }
        ).to_csv(cache / f"history_{code}_20260710_1098.csv", index=False)

    def test_prepare_is_idempotent_and_research_runs_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)

            first = pipeline.prepare_data()
            second = pipeline.prepare_data()
            research = pipeline.run_research()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(first["status"], "built")
        self.assertEqual(second["status"], "cached")
        self.assertEqual(snapshot.iloc[0]["code"], "000001")
        self.assertGreater(research["labels_rows"], 0)
        self.assertGreater(research["events_rows"], 0)
        self.assertEqual(research["stages"], ["features", "labels", "events", "regimes", "event_study"])

    def test_prediction_model_failure_writes_fallback_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.prepare_data()
            with patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=ValueError("bad model")):
                result = pipeline.predict()

            self.assertEqual(result["status"], "fallback")
            self.assertTrue(Path(result["health_path"]).exists())

    def test_prediction_writes_all_four_horizons_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([{"code": "000001", "trade_date": "20260710", "factor": 1.0}]),
            )
            bundles = [SimpleNamespace(horizon=value, model_version=f"m{value}", metrics={}) for value in (3, 5, 10, 20)]

            def prediction(bundle, features, **kwargs):
                del features
                return [PredictionRecord(code="000001", as_of="2026-07-10", horizon=bundle.horizon, p_up=0.5, p_flat=0.3, p_down=0.2)]

            with (
                patch.object(pipeline, "_resolve_model", return_value=(Path("model.joblib"), "research")),
                patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=bundles) as load,
                patch("stock_analyze.research.pipeline.generate_predictions", side_effect=prediction),
            ):
                result = pipeline.predict()
            output = pd.read_parquet(root / "data" / "a_share" / "codex" / "predictions" / "20260710.parquet")

        self.assertEqual(load.call_count, 4)
        self.assertEqual(set(output["horizon"]), {3, 5, 10, 20})
        self.assertEqual(result["predictions"], 4)

    def test_prediction_uses_latest_persisted_regime_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([{"code": "000001", "trade_date": "20260710", "factor": 1.0}]),
            )
            regimes = pd.DataFrame({
                "trade_date": pd.date_range("2026-06-29", periods=10, freq="B").strftime("%Y%m%d"),
                "composite_regime": ["risk_on"] * 10,
                "regime_coverage": [0.8] * 10,
            })
            pipeline.store.write_parquet_atomic(pipeline._artifact_path("regimes"), regimes)
            observed = {}

            def prediction(bundle, features, **kwargs):
                del bundle, features
                observed.update(kwargs)
                return [PredictionRecord(code="000001", as_of="2026-07-10", horizon=5, p_up=0.5, p_flat=0.3, p_down=0.2)]

            with (
                patch.object(pipeline, "_resolve_model", return_value=(Path("model.joblib"), "research")),
                patch("stock_analyze.research.pipeline.load_model_bundle", return_value=SimpleNamespace(horizon=5, model_version="m5", metrics={})),
                patch("stock_analyze.research.pipeline.generate_predictions", side_effect=prediction),
            ):
                result = pipeline.predict(horizon=5)

        self.assertEqual(observed["regime"], "risk_on")
        self.assertGreater(observed["regime_stability"], 0.9)
        self.assertEqual(result["regime"], "risk_on")

    def test_research_loads_macro_and_global_context_from_shared_raw_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.prepare_data()
            a_raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            qdii_raw = root / "data" / "research" / "raw" / "cn_qdii_etf" / "20260710"
            a_raw.mkdir(parents=True)
            qdii_raw.mkdir(parents=True)
            pd.DataFrame([
                {"MONTH": "202604", "PMI010000": 49.0},
                {"MONTH": "202605", "PMI010000": 50.0},
            ]).to_parquet(a_raw / "cn_pmi.parquet", index=False)
            pd.DataFrame([
                {"date": "20260610", "y2": 4.0, "y10": 4.4},
                {"date": "20260710", "y2": 4.1, "y10": 4.6},
            ]).to_parquet(a_raw / "us_tycr.parquet", index=False)
            pd.DataFrame([
                {"ts_code": "SPX", "trade_date": "20260610", "close": 100.0},
                {"ts_code": "SPX", "trade_date": "20260709", "close": 105.0},
                {"ts_code": "SPX", "trade_date": "20260710", "close": 110.0},
            ]).to_parquet(qdii_raw / "index_global.parquet", index=False)

            pipeline.run_research()
            regimes = pd.read_parquet(pipeline._artifact_path("regimes"))
            latest = regimes.sort_values("trade_date").iloc[-1]

        self.assertNotEqual(latest["macro_regime"], "unknown")
        self.assertNotEqual(latest["global_risk_regime"], "unknown")
        self.assertEqual(float(latest["regime_coverage"]), 1.0)

    def test_research_persists_market_and_industry_regimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            rows = []
            for offset, trade_date in enumerate(pd.date_range("2026-05-25", periods=35, freq="B")):
                for code, industry, tilt in (
                    ("000001", "科技", 0.02), ("000002", "科技", 0.01),
                    ("600000", "银行", -0.01), ("600001", "银行", -0.02),
                ):
                    rows.append({
                        "code": code,
                        "trade_date": trade_date.strftime("%Y%m%d"),
                        "close": 10.0 + offset * (0.01 + tilt),
                        "momentum_20": tilt + offset / 1000.0,
                        "realized_volatility_20": 0.15 + abs(tilt),
                        "volume_ratio_5_20": 1.0 + tilt,
                        "industry": industry,
                    })
            pipeline.store.write_feature_snapshot("a_share", "2026-07-10", pd.DataFrame(rows))

            pipeline.run_research()
            regimes = pd.read_parquet(pipeline._artifact_path("regimes"))

        self.assertIn("market", set(regimes["scope"]))
        self.assertIn("industry:科技", set(regimes["scope"]))
        self.assertIn("industry:银行", set(regimes["scope"]))

    def test_resolve_model_prefers_latest_registration_not_hash_sort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")
            model_root = root / "data" / "research" / "models" / "a_share" / "3"
            model_root.mkdir(parents=True)
            registry = {
                "models": {
                    "f999": {"status": "research", "artifact": str(model_root / "older.joblib")},
                    "a111": {"status": "research", "artifact": str(model_root / "newer.joblib")},
                }
            }
            (model_root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

            artifact, status = pipeline._resolve_model(3)

        self.assertEqual(artifact.name, "newer.joblib")
        self.assertEqual(status, "research")

    def test_resolve_model_prefers_shadow_over_newer_failed_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")
            model_root = root / "data" / "research" / "models" / "a_share" / "5"
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "shadow-v1": {
                        "status": "shadow", "artifact": str(model_root / "shadow.joblib"),
                        "registered_at": "2026-06-01T00:00:00+00:00",
                    },
                    "failed-v2": {
                        "status": "research", "artifact": str(model_root / "failed.joblib"),
                        "registered_at": "2026-07-01T00:00:00+00:00",
                    },
                },
            }), encoding="utf-8")

            artifact, status = pipeline._resolve_model(5)

        self.assertEqual(artifact.name, "shadow.joblib")
        self.assertEqual(status, "shadow")

    def test_training_records_research_to_shadow_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([{"code": "000001", "trade_date": "20260710", "factor": 1.0}])
            )
            pipeline.store.write_label_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([{"code": "000001", "trade_date": "20260710", "horizon": 3, "label": "up"}])
            )

            def bundle(*_args, horizon, **_kwargs):
                return SimpleNamespace(horizon=horizon, model_version=f"m{horizon}", metrics=self._passing_gate_metrics())

            with (
                patch("stock_analyze.research.pipeline.train_model_bundle", side_effect=bundle),
                patch("stock_analyze.research.pipeline.save_model_bundle"),
            ):
                result = pipeline.train_models()
            registry = json.loads((root / "data" / "research" / "models" / "a_share" / "3" / "registry.json").read_text())

        self.assertEqual(result["status"], "complete")
        self.assertEqual(registry["models"]["m3"]["status"], "shadow")
        self.assertTrue(registry["models"]["m3"]["gate_history"][-1]["passed"])

    def test_fourth_shadow_prediction_cycle_promotes_model_for_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([{"code": "000001", "trade_date": "20260710", "factor": 1.0}])
            )
            model_root = root / "data" / "research" / "models" / "a_share" / "5"
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {"m5": {"status": "shadow", "artifact": str(model_root / "model.joblib"), "gate_history": []}},
            }), encoding="utf-8")
            from stock_analyze.research.activation import ShadowCycleTracker
            tracker = ShadowCycleTracker(model_root / "shadow_cycles.json")
            for as_of in ("2026-06-12", "2026-06-19", "2026-06-26"):
                tracker.record("m5", as_of, {"predictions": 1})
            model = SimpleNamespace(horizon=5, model_version="m5", metrics=self._passing_gate_metrics())

            def prediction(*_args, **_kwargs):
                return [PredictionRecord(code="000001", as_of="2026-07-10", horizon=5, p_up=0.5, p_flat=0.3, p_down=0.2)]

            with (
                patch("stock_analyze.research.pipeline.load_model_bundle", return_value=model),
                patch("stock_analyze.research.pipeline.generate_predictions", side_effect=prediction),
            ):
                pipeline.predict(horizon=5)
            registry = json.loads((model_root / "registry.json").read_text())

        self.assertEqual(registry["champion_model_version"], "m5")
        self.assertEqual(registry["models"]["m5"]["status"], "active")

    def test_shadow_challenger_runs_alongside_existing_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([{"code": "000001", "trade_date": "20260710", "factor": 1.0}])
            )
            model_root = root / "data" / "research" / "models" / "a_share" / "5"
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": "champion",
                "models": {
                    "champion": {"status": "active", "artifact": str(model_root / "champion.joblib"), "gate_history": []},
                    "challenger": {"status": "shadow", "artifact": str(model_root / "challenger.joblib"), "gate_history": []},
                },
            }), encoding="utf-8")
            bundles = {
                "champion.joblib": SimpleNamespace(horizon=5, model_version="champion", metrics=self._passing_gate_metrics()),
                "challenger.joblib": SimpleNamespace(horizon=5, model_version="challenger", metrics=self._passing_gate_metrics()),
            }

            def load(path):
                return bundles[Path(path).name]

            def prediction(bundle, *_args, **_kwargs):
                return [PredictionRecord(
                    code="000001", as_of="2026-07-10", horizon=5, p_up=0.5, p_flat=0.3, p_down=0.2,
                    model_version=bundle.model_version,
                )]

            with (
                patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=load),
                patch("stock_analyze.research.pipeline.generate_predictions", side_effect=prediction),
            ):
                pipeline.predict(horizon=5)
            main = pd.read_parquet(root / "data" / "a_share" / "codex" / "predictions" / "20260710.parquet")
            shadow = pd.read_parquet(root / "data" / "research" / "shadow_predictions" / "a_share" / "5" / "challenger" / "20260710.parquet")
            cycles = json.loads((model_root / "shadow_cycles.json").read_text())

        self.assertEqual(main.iloc[0]["model_version"], "champion")
        self.assertEqual(shadow.iloc[0]["model_version"], "challenger")
        self.assertEqual(len(cycles["models"]["challenger"]["cycles"]), 1)

    def test_online_prepare_persists_normalized_source_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")
            sources = SourceCollection(
                frames={
                    "daily_basic": pd.DataFrame([
                        {"ts_code": "000001.SZ", "trade_date": "20260710", "pe_ttm": 12.0, "source": "tushare:daily_basic", "observed_at": "2026-07-10T18:00:00+08:00"}
                    ])
                },
                health=pd.DataFrame([{"source": "daily_basic", "failed": False, "rows": 1}]),
            )
            with patch.object(pipeline, "_collect_sources", return_value=sources):
                result = pipeline.prepare_data()

            raw_path = root / "data" / "research" / "raw" / "a_share" / "20260710" / "daily_basic.parquet"
            raw_exists = raw_path.exists()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(result["sources"], 1)
        self.assertTrue(raw_exists)
        self.assertEqual(float(snapshot.loc[snapshot["trade_date"] == "20260710", "pe_ttm"].iloc[-1]), 12.0)

    def test_offline_force_prepare_reuses_persisted_raw_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            pd.DataFrame([{
                "ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331",
                "roe": 10.0, "grossprofit_margin": 30.0, "roic": 8.0,
                "netprofit_margin": 12.0, "debt_to_assets": 40.0, "assets_turn": 0.8,
                "q_sales_yoy": 15.0, "netprofit_yoy": 18.0, "q_op_qoq": 3.0,
            }]).to_parquet(raw / "fina_indicator.parquet", index=False)
            pd.DataFrame([{
                "ts_code": "000001.SZ", "l1_name": "银行", "l2_name": "股份行",
                "in_date": "20000101", "out_date": None,
            }]).to_parquet(raw / "index_member_all.parquet", index=False)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)

            result = pipeline.prepare_data(force=True)
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")
            latest = snapshot.sort_values("trade_date").iloc[-1]

        self.assertEqual(result["sources"], 2)
        self.assertAlmostEqual(float(latest["roe"]), 10.0)
        self.assertEqual(latest["industry"], "银行")

    def test_a_share_source_collection_limits_financial_deep_fetch(self):
        class FakeProvider:
            pro = object()

            def _safe_pro_call(self, label, call):
                return call()

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(Path(tmp), market="a_share", agent="codex", as_of="2026-07-10")
            empty = SourceCollection(frames={}, health=pd.DataFrame())
            with (
                patch("stock_analyze.markets.a_share.data_provider.make_provider", return_value=FakeProvider()),
                patch("stock_analyze.markets.a_share.market_data.collect_research_sources", return_value=empty) as collect,
            ):
                pipeline._collect_sources([f"{index:06d}" for index in range(100)])

        self.assertEqual(len(collect.call_args.kwargs["codes"]), 40)

    def test_a_share_keeps_full_history_sample_and_latest_row_for_all_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for code in ("000001", "000002", "000003"):
                self._write_history(root, rows=80, code=code)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
                max_full_history_instruments=1,
            )

            result = pipeline.prepare_data()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(result["instruments"], 3)
        self.assertEqual(snapshot.loc[snapshot["history_role"] == "full", "code"].nunique(), 1)
        self.assertEqual(snapshot.loc[snapshot["history_role"] == "latest_only", "code"].nunique(), 2)
        self.assertEqual(len(snapshot.loc[snapshot["history_role"] == "latest_only"]), 2)

    def test_a_share_history_sample_is_stable_and_not_code_prefix_biased(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(
                Path(tmp), market="a_share", agent="codex", max_full_history_instruments=10
            )
            codes = [f"{index:06d}" for index in range(100)]
            first = pipeline._full_history_codes(codes)
            second = pipeline._full_history_codes(list(reversed(codes)))

        self.assertEqual(first, second)
        self.assertEqual(len(first), 10)
        self.assertGreater(max(int(code) for code in first), 50)

    def test_a_share_cache_selection_prefers_three_year_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root, rows=80, code="000001")
            cache = root / "data" / "shared" / "cache"
            (cache / "history_000001_20260710_220.csv").write_text(
                (cache / "history_000001_20260710_1098.csv").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")

            selected = pipeline._history_files()

        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].name.endswith("_1098.csv"))

    def test_default_a_share_full_history_budget_is_sixty_instruments(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(Path(tmp), market="a_share", agent="codex")
            selected = pipeline._full_history_codes([f"{index:06d}" for index in range(100)])
        self.assertEqual(len(selected), 60)


if __name__ == "__main__":
    unittest.main()
