import gc
import json
import tempfile
import unittest
import weakref
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
    def _scoped_feature(code: str = "000001") -> dict[str, object]:
        return {
            "code": code,
            "trade_date": "20260710",
            "factor": 1.0,
            "account_id": "hs300",
            "research_scope": "hs300",
            "benchmark_code": "000300",
            "universe_quality": "available",
            "unbiased_universe": True,
            "universe_contract_version": "pit-universe-v1",
            "membership_source": "monthly_index_weight",
        }

    def test_research_portfolio_contract_freezes_market_execution_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_share = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )._research_portfolio_contract()
            qdii = ResearchPipeline(
                root, market="cn_qdii_etf", agent="codex", as_of="2026-07-10", offline=True
            )._research_portfolio_contract()

        self.assertEqual(a_share["execution_policy"]["rank_buffer_pct"], 0.50)
        self.assertEqual(a_share["execution_policy"]["partial_adjustment_rate"], 0.35)
        self.assertEqual(a_share["execution_policy"]["max_daily_turnover"], 0.10)
        self.assertEqual(qdii["execution_policy"]["rank_buffer_pct"], 0.80)
        self.assertEqual(qdii["execution_policy"]["minimum_target_change"], 0.02)
        self.assertEqual(qdii["execution_policy"]["partial_adjustment_rate"], 0.25)
        self.assertEqual(qdii["execution_policy"]["max_daily_turnover"], 0.08)

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
            "subperiod_stability": 0.80,
            "seed_rank_ic_std": 0.01,
            "feature_selection_stability": 0.85,
            "unbiased_universe": True,
            "effective_dates": 180,
            "effective_non_overlapping_periods": 40,
            "simulator_version": "paper-parity-daily-v1",
            "all_accounts_positive_active": True,
            "valid_trial_count": 5,
            "trial_evidence_status": "available",
            "forward_evidence_status": "available",
            "forward_cycles": 12,
            "forward_net_excess_return": 0.02,
            "forward_max_drawdown": 0.10,
            "forward_all_accounts_positive_active": True,
            "governance": {
                "deflated_sharpe_probability": 0.99,
                "probability_of_backtest_overfit": 0.20,
                "pbo_trial_count": 6,
            },
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

    @staticmethod
    def _write_benchmarks(root: Path, dates: pd.DatetimeIndex) -> None:
        raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
        raw.mkdir(parents=True, exist_ok=True)
        for code, end_value in (("000300", 112.0), ("000905", 106.0)):
            pd.DataFrame({
                "ts_code": [f"{code}.SH"] * len(dates),
                "trade_date": dates.strftime("%Y%m%d"),
                "open": np.linspace(100.1, end_value + 0.1, len(dates)),
                "close": np.linspace(100.0, end_value, len(dates)),
            }).to_parquet(raw / f"benchmark_{code}.parquet", index=False)
        weights = root / "data" / "shared" / "backtest_cache" / "index_weight"
        weights.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"con_code": ["000001.SZ"]}).to_csv(
            weights / f"000300_{dates[0].strftime('%Y-%m')}.csv", index=False
        )
        pd.DataFrame({"con_code": ["000002.SZ"]}).to_csv(
            weights / f"000905_{dates[0].strftime('%Y-%m')}.csv", index=False
        )

    def test_prepare_is_idempotent_and_research_runs_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            self._write_benchmarks(root, pd.date_range("2026-01-01", periods=140, freq="B"))
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)

            first = pipeline.prepare_data()
            second = pipeline.prepare_data()
            research = pipeline.run_research()
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")
            labels = pipeline.store.read_label_snapshot("a_share", "2026-07-10")
            feature_metadata = json.loads(
                (pipeline.store.feature_snapshot_path("a_share", "2026-07-10").with_suffix(".metadata.json"))
                .read_text(encoding="utf-8")
            )

        self.assertEqual(first["status"], "built")
        self.assertEqual(second["status"], "cached")
        self.assertEqual(snapshot.iloc[0]["code"], "000001")
        float_columns = snapshot.select_dtypes(include=["floating"]).columns
        self.assertTrue(float_columns.any())
        self.assertTrue(all(snapshot[column].dtype.itemsize <= 4 for column in float_columns))
        self.assertGreater(research["labels_rows"], 0)
        self.assertGreater(research["events_rows"], 0)
        self.assertGreaterEqual(research["benchmark_coverage"], 0.95)
        self.assertTrue(first["universe"]["unbiased_universe"])
        self.assertEqual(research["stages"], ["features", "labels", "events", "regimes", "event_study"])

        self.assertTrue(labels["benchmark_return"].notna().all())
        self.assertFalse(np.allclose(labels["absolute_return"], labels["excess_return"]))
        self.assertTrue(labels["entry_date"].gt(labels["trade_date"]).all())
        self.assertEqual(set(labels["label_contract_version"]), {"next-open-v1"})
        self.assertEqual(set(labels["account_id"]), {"hs300"})
        self.assertEqual(len(feature_metadata["registry_hash"]), 16)
        self.assertIn("high_value_add_proxy", feature_metadata["registered_features"])

    def test_qdii_history_normalizes_tushare_amount_to_yuan_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "fund_daily_513100_SH_20260710.csv"
            pd.DataFrame([
                {
                    "ts_code": "513100.SH",
                    "trade_date": "20260710",
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.05,
                    "vol": 100.0,
                    "amount": 25.0,
                }
            ]).to_csv(path, index=False)
            pipeline = ResearchPipeline(
                root,
                market="cn_qdii_etf",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )

            normalized = pipeline._normalize_history(path)

        self.assertEqual(float(normalized.iloc[0]["amount_thousand_yuan"]), 25.0)
        self.assertEqual(float(normalized.iloc[0]["amount"]), 25_000.0)
        self.assertEqual(normalized.iloc[0]["amount_unit"], "yuan")

    def test_research_only_model_drift_does_not_invalidate_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = SimpleNamespace(
                model_version="research-v1",
                metrics={"class_balance": {"down": 0.34, "flat": 0.33, "up": 0.33}},
            )
            latest_records = []
            latest_assessment = {}
            for current in pd.date_range("2026-01-01", periods=7, freq="D"):
                as_of = current.strftime("%Y-%m-%d")
                pipeline = ResearchPipeline(
                    root,
                    market="a_share",
                    agent="codex",
                    as_of=as_of,
                    offline=True,
                )
                record = PredictionRecord(
                    code="000001",
                    as_of=as_of,
                    horizon=5,
                    p_up=0.05,
                    p_flat=0.05,
                    p_down=0.90,
                    model_version="research-v1",
                    classifier_status="research",
                    ranker_status="research",
                    portfolio_status="research",
                    metadata={
                        "feature_drift_mean_psi": 0.50,
                        "out_of_distribution_ratio": 0.35,
                    },
                )
                latest_records, latest_assessment = pipeline._assess_model_drift(
                    5,
                    bundle,
                    [record],
                    role_status={
                        "classifier": "research",
                        "ranker": "research",
                        "portfolio": "research",
                    },
                )

        self.assertEqual(latest_assessment["status"], "warning")
        self.assertEqual(latest_assessment["consecutive_breach_windows"], 0)
        self.assertFalse(latest_records[0].invalidated)
        self.assertNotIn("模型漂移触发隔离", latest_records[0].invalidation)

    def test_non_active_same_day_drift_recompute_reuses_append_only_observation(self):
        from stock_analyze.research.drift import DriftLifecycle, DriftObservation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            monitor = DriftLifecycle(
                pipeline._model_root(5) / "drift_lifecycle.json"
            )
            monitor.record(
                DriftObservation(
                    model_version="research-v1",
                    as_of="2026-07-10",
                    feature_psi=0.01,
                    ood_ratio=0.01,
                    prediction_distribution=(0.2, 0.3, 0.5),
                    reference_prediction_distribution=(0.3, 0.4, 0.3),
                )
            )
            bundle = SimpleNamespace(
                model_version="research-v1",
                metrics={"class_balance": {"down": 0.3, "flat": 0.4, "up": 0.3}},
            )
            record = PredictionRecord(
                code="000001",
                as_of="2026-07-10",
                horizon=5,
                p_up=0.70,
                p_flat=0.20,
                p_down=0.10,
                model_version="research-v1",
                metadata={
                    "feature_drift_mean_psi": 0.50,
                    "out_of_distribution_ratio": 0.35,
                },
            )

            records, assessment = pipeline._assess_model_drift(
                5,
                bundle,
                [record],
                role_status={
                    "classifier": "research",
                    "ranker": "research",
                    "portfolio": "research",
                },
            )

        self.assertEqual(assessment["observation_status"], "reused_same_day")
        self.assertIn("same_day_recompute_deferred", assessment["evidence_gaps"])
        self.assertFalse(records[0].invalidated)

    def test_active_same_day_drift_conflict_still_fails_closed(self):
        from stock_analyze.research.drift import DriftLifecycle, DriftObservation

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            DriftLifecycle(
                pipeline._model_root(5) / "drift_lifecycle.json"
            ).record(
                DriftObservation(
                    model_version="active-v1",
                    as_of="2026-07-10",
                    feature_psi=0.01,
                    ood_ratio=0.01,
                    prediction_distribution=(0.2, 0.3, 0.5),
                    reference_prediction_distribution=(0.3, 0.4, 0.3),
                )
            )
            bundle = SimpleNamespace(
                model_version="active-v1",
                metrics={"class_balance": {"down": 0.3, "flat": 0.4, "up": 0.3}},
            )
            record = PredictionRecord(
                code="000001",
                as_of="2026-07-10",
                horizon=5,
                p_up=0.70,
                p_flat=0.20,
                p_down=0.10,
                model_version="active-v1",
                metadata={
                    "feature_drift_mean_psi": 0.50,
                    "out_of_distribution_ratio": 0.35,
                },
            )

            with self.assertRaisesRegex(ValueError, "drift_observation_conflict"):
                pipeline._assess_model_drift(
                    5,
                    bundle,
                    [record],
                    role_status={
                        "classifier": "active",
                        "ranker": "active",
                        "portfolio": "active",
                    },
                )

    def test_historical_feature_panel_uses_research_event_availability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )

            with patch(
                "stock_analyze.research.pipeline.attach_event_features",
                side_effect=lambda features, *_args, **_kwargs: features,
            ) as attach:
                pipeline.prepare_data()

        self.assertEqual(attach.call_args.kwargs["availability_policy"], "research")

    def test_prepare_batches_history_feature_concatenation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                self._write_history(root, rows=40, code=f"{index + 1:06d}")
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            history_batch_sizes = []
            real_concat = pd.concat

            def observed_concat(frames, *args, **kwargs):
                materialized = list(frames)
                if materialized and all("history_role" in frame.columns for frame in materialized):
                    history_batch_sizes.append(len(materialized))
                return real_concat(materialized, *args, **kwargs)

            with (
                patch.object(ResearchPipeline, "_FEATURE_BATCH_SIZE", 2),
                patch("stock_analyze.research.pipeline.pd.concat", side_effect=observed_concat),
            ):
                result = pipeline.prepare_data(force=True)

        self.assertEqual(result["instruments"], 5)
        self.assertTrue(history_batch_sizes)
        self.assertLessEqual(max(history_batch_sizes), 2)

    def test_research_releases_stage_frames_before_next_large_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            dates = pd.date_range("2026-01-01", periods=140, freq="B")
            self._write_benchmarks(root, dates)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.prepare_data()
            label_ref: dict[str, weakref.ReferenceType[pd.DataFrame]] = {}
            feature_ref: dict[str, weakref.ReferenceType[pd.DataFrame]] = {}

            def fake_labels(prices, **_kwargs):
                latest = prices.sort_values("trade_date").iloc[-1]
                frame = pd.DataFrame([{
                    "code": latest["code"],
                    "trade_date": latest["trade_date"],
                    "horizon": 5,
                    "label": "up",
                    "excess_return": 0.01,
                }])
                label_ref["value"] = weakref.ref(frame)
                return frame

            def fake_event_writer(features, *, market, destination, regime_by_date):
                gc.collect()
                self.assertIsNone(label_ref["value"]())
                feature_ref["value"] = weakref.ref(features)
                latest = features.sort_values("trade_date").iloc[-1]
                self.assertIn(str(latest["trade_date"]), regime_by_date)
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                pd.DataFrame([{
                    "event_id": "event-1",
                    "event": "macd_golden_cross",
                    "market": market,
                    "code": latest["code"],
                    "trade_date": latest["trade_date"],
                    "direction": "up",
                    "regime": "unknown",
                    "industry": "unclassified",
                    "context": "{}",
                }]).to_parquet(destination, index=False)
                return 1

            def fake_event_study(_events, _labels):
                gc.collect()
                self.assertIsNone(feature_ref["value"]())
                return pd.DataFrame()

            with (
                patch("stock_analyze.research.pipeline.build_forward_labels", new=fake_labels),
                patch("stock_analyze.research.pipeline.write_events_incremental", new=fake_event_writer),
                patch("stock_analyze.research.pipeline.build_event_study_from_parquet", new=fake_event_study),
            ):
                result = pipeline.run_research()

        self.assertEqual(result["labels_rows"], 1)
        self.assertEqual(result["events_rows"], 1)

    def test_prediction_model_failure_writes_fallback_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            self._write_benchmarks(root, pd.date_range("2026-01-01", periods=140, freq="B"))
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.prepare_data()
            with patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=ValueError("bad model")):
                result = pipeline.predict()

            self.assertEqual(result["status"], "fallback")
            self.assertTrue(Path(result["health_path"]).exists())

    def test_prediction_accuracy_backfill_is_idempotent_and_uses_realized_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_label_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([{
                    "code": "000001", "trade_date": "20260701", "horizon": 5,
                    "label": "up", "excess_return": 0.04, "label_end_date": "20260708",
                }]),
            )
            prediction_dir = root / "data" / "a_share" / "codex" / "predictions"
            prediction_dir.mkdir(parents=True)
            pd.DataFrame([{
                "as_of": "2026-07-01", "code": "000001", "horizon": 5,
                "p_down": 0.10, "p_flat": 0.20, "p_up": 0.70,
                "expected_excess_return": 0.03, "confidence": 0.80,
                "model_version": "m1", "active_status": "inactive",
            }]).to_parquet(prediction_dir / "20260701.parquet", index=False)

            first = pipeline.backfill_prediction_accuracy()
            second = pipeline.backfill_prediction_accuracy()
            accuracy = pd.read_csv(
                root / "data" / "a_share" / "codex" / "prediction_accuracy.csv",
                dtype={"code": str, "as_of": str, "model_version": str},
            )

        self.assertEqual(first["evaluated"], 1)
        self.assertEqual(second["evaluated"], 1)
        self.assertEqual(len(accuracy), 1)
        self.assertTrue(bool(accuracy.iloc[0]["correct"]))
        self.assertAlmostEqual(float(accuracy.iloc[0]["brier_score"]), 0.14)
        self.assertAlmostEqual(float(accuracy.iloc[0]["return_error"]), -0.01)

    def test_prediction_writes_all_four_horizons_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([self._scoped_feature()]),
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

    def test_prediction_uses_one_current_market_cross_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            fresh = self._scoped_feature("000001")
            stale = self._scoped_feature("000002")
            stale["trade_date"] = "20260709"
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([stale, fresh]),
            )
            observed_codes: list[str] = []

            def prediction(bundle, features, **kwargs):
                del bundle, kwargs
                observed_codes.extend(features["code"].astype(str).tolist())
                return [
                    PredictionRecord(
                        code=str(row["code"]),
                        as_of="2026-07-10",
                        horizon=5,
                        p_up=0.5,
                        p_flat=0.3,
                        p_down=0.2,
                    )
                    for row in features.to_dict(orient="records")
                ]

            with (
                patch.object(
                    pipeline,
                    "_resolve_model",
                    return_value=(Path("model.joblib"), "research"),
                ),
                patch(
                    "stock_analyze.research.pipeline.load_model_bundle",
                    return_value=SimpleNamespace(
                        horizon=5,
                        model_version="m5",
                        metrics={},
                    ),
                ),
                patch(
                    "stock_analyze.research.pipeline.generate_predictions",
                    side_effect=prediction,
                ),
            ):
                result = pipeline.predict(horizon=5)

        self.assertEqual(observed_codes, ["000001"])
        self.assertEqual(result["prediction_universe"]["feature_snapshot_rows"], 2)
        self.assertEqual(result["prediction_universe"]["current_market_rows"], 1)
        self.assertEqual(result["prediction_universe"]["stale_rows_rejected"], 1)

    def test_prediction_uses_latest_persisted_regime_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([self._scoped_feature()]),
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

    def test_prediction_revalidates_stale_cached_qdii_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
            cache.mkdir(parents=True)
            pd.DataFrame([
                {
                    "ts_code": "513100.SH", "name": "纳斯达克100ETF(QDII)",
                    "benchmark": "纳斯达克100指数", "status": "L",
                    "list_date": "20130515", "delist_date": "",
                },
                {
                    "ts_code": "159920.SZ", "name": "恒生ETF",
                    "benchmark": "香港恒生指数", "status": "L",
                    "list_date": "20120809", "delist_date": "",
                },
                {
                    "ts_code": "520830.SH", "name": "沙特ETF(QDII)",
                    "benchmark": "富时沙特阿拉伯指数", "status": "L",
                    "list_date": "20240716", "delist_date": "",
                },
                {
                    "ts_code": "161116.SZ", "name": "黄金主题LOF",
                    "benchmark": "黄金价格", "status": "L",
                    "list_date": "20111108", "delist_date": "",
                },
                {
                    "ts_code": "513999.SH", "name": "历史ETF",
                    "benchmark": "历史指数", "status": "D",
                    "list_date": "20100101", "delist_date": "20200101",
                },
            ]).to_csv(cache / "fund_basic_E_v2.csv", index=False)
            pipeline = ResearchPipeline(
                root,
                market="cn_qdii_etf",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            stale = pd.DataFrame([
                {
                    "code": code,
                    "trade_date": "20260710",
                    "account_id": scope,
                    "research_scope": scope,
                }
                for code, scope in (
                    ("513100", "us_exposure"),
                    ("159920", "hk_exposure"),
                    ("520830", "saudi_exposure"),
                    ("161116", "commodity_precious_metals"),
                )
            ])
            accounts = [
                {"id": "us_exposure", "scope": "us_exposure", "benchmark": "513100.SH"},
                {"id": "hk_exposure", "scope": "hk_exposure", "benchmark": "159920.SZ"},
            ]

            with patch.object(pipeline, "_baseline_accounts", return_value=accounts):
                scoped, metadata = pipeline._prediction_universe(stale)

        self.assertEqual(set(scoped["code"]), {"513100", "159920"})
        self.assertEqual(set(scoped["account_id"]), {"us_exposure", "hk_exposure"})
        self.assertTrue(metadata["unbiased_universe"])
        self.assertEqual(metadata["rejected_rows"], 2)

    def test_prediction_rows_expose_portfolio_risk_dimensions(self):
        record = PredictionRecord(
            code="513400",
            as_of="2026-07-10",
            horizon=5,
            p_up=0.5,
            p_flat=0.3,
            p_down=0.2,
            metadata={
                "account_id": "us_exposure",
                "research_scope": "us_exposure",
                "benchmark_code": "513100.SH",
                "index_key": "dow_jones_industrial",
                "country": "美国",
                "theme": "道琼斯工业平均",
                "sector": "美国大盘",
                "asset_class": "equity",
            },
        )

        row = ResearchPipeline._prediction_rows([record])[0]

        self.assertEqual(row["index_key"], "dow_jones_industrial")
        self.assertEqual(row["country"], "美国")
        self.assertEqual(row["theme"], "道琼斯工业平均")
        self.assertEqual(row["sector"], "美国大盘")
        self.assertEqual(row["asset_class"], "equity")

    def test_research_loads_macro_and_global_context_from_shared_raw_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root)
            self._write_benchmarks(root, pd.date_range("2026-01-01", periods=140, freq="B"))
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.prepare_data()
            a_raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            qdii_raw = root / "data" / "research" / "raw" / "cn_qdii_etf" / "20260710"
            a_raw.mkdir(parents=True, exist_ok=True)
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
                        "open": 10.0 + offset * (0.01 + tilt) - 0.01,
                        "high": 10.0 + offset * (0.01 + tilt) + 0.02,
                        "low": 10.0 + offset * (0.01 + tilt) - 0.02,
                        "close": 10.0 + offset * (0.01 + tilt),
                        "momentum_20": tilt + offset / 1000.0,
                        "realized_volatility_20": 0.15 + abs(tilt),
                        "volume_ratio_5_20": 1.0 + tilt,
                        "industry": industry,
                    })
            pipeline.store.write_feature_snapshot("a_share", "2026-07-10", pd.DataFrame(rows))
            self._write_benchmarks(
                root,
                pd.date_range("2026-05-25", periods=35, freq="B"),
            )

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
                "a_share", "2026-07-10", pd.DataFrame([self._scoped_feature()])
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
            trials = (
                root / "data" / "research" / "models" / "a_share" / "3" / "trials.jsonl"
            ).read_text(encoding="utf-8").splitlines()

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["snapshot_date"], "20260710")
        self.assertEqual(registry["models"]["m3"]["status"], "rejected")
        self.assertEqual(registry["models"]["m3"]["role_status"]["classifier"], "shadow")
        self.assertEqual(registry["models"]["m3"]["role_status"]["ranker"], "research")
        self.assertEqual(registry["models"]["m3"]["role_status"]["portfolio"], "research")
        self.assertIn(
            "ranker:probability_of_backtest_overfit",
            registry["models"]["m3"]["rejection_reasons"],
        )
        self.assertFalse(registry["models"]["m3"]["gate_history"][-1]["passed"])
        self.assertIn(
            "probability_of_backtest_overfit",
            registry["models"]["m3"]["gate_history"][-1]["reasons"],
        )
        self.assertEqual(len(trials), 1)
        self.assertIn("governance", registry["models"]["m3"])

    def test_twelfth_prediction_cycle_does_not_promote_without_realized_forward_nav(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([self._scoped_feature()])
            )
            model_root = root / "data" / "research" / "models" / "a_share" / "5"
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {"m5": {"status": "shadow", "artifact": str(model_root / "model.joblib"), "gate_history": []}},
            }), encoding="utf-8")
            from stock_analyze.research.activation import ShadowCycleTracker
            tracker = ShadowCycleTracker(model_root / "shadow_cycles.json")
            for as_of in (
                "2026-04-24", "2026-05-01", "2026-05-08", "2026-05-15",
                "2026-05-22", "2026-05-29", "2026-06-05", "2026-06-12",
                "2026-06-19", "2026-06-26", "2026-07-03",
            ):
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

        self.assertIsNone(registry.get("champion_model_version"))
        self.assertEqual(registry["models"]["m5"]["status"], "shadow")
        self.assertIn(
            "forward_evidence_status",
            registry["models"]["m5"]["gate_history"][-1]["reasons"],
        )

    def test_pinned_iteration_candidate_runs_alongside_existing_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([self._scoped_feature()])
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
            challenger = pd.read_parquet(
                root
                / "data"
                / "research"
                / "iteration_predictions"
                / "a_share"
                / "5"
                / "challenger"
                / "20260710.parquet"
            )
            cycles = json.loads((model_root / "shadow_cycles.json").read_text())
            iteration_state = json.loads(
                (
                    root
                    / "data"
                    / "model_iterations"
                    / "a_share"
                    / "5"
                    / "iteration_state.json"
                ).read_text()
            )

        self.assertEqual(main.iloc[0]["model_version"], "champion")
        self.assertEqual(challenger.iloc[0]["model_version"], "challenger")
        self.assertEqual(len(cycles["models"]["challenger"]["cycles"]), 1)
        self.assertEqual(iteration_state["current_candidate"]["model_version"], "challenger")

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
            manifest_path = raw_path.parent / "snapshot_manifest.json"
            raw_exists = raw_path.exists()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            snapshot = pipeline.store.read_feature_snapshot("a_share", "2026-07-10")

        self.assertEqual(result["sources"], 1)
        self.assertTrue(raw_exists)
        self.assertEqual(manifest["mode"], "cumulative")
        self.assertEqual(manifest["sources"], ["daily_basic"])
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
                "ts_code": "000002.SZ", "l1_name": "银行", "l2_name": "股份行",
                "in_date": "20000101", "out_date": None,
            }]).to_parquet(raw / "index_member_all.parquet", index=False)
            pd.DataFrame([{
                "code": "000001", "name": "平安银行", "industry": "银行", "list_date": "19910403",
            }]).to_csv(root / "data" / "shared" / "cache" / "stock_basic_20260710.csv", index=False)
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

    def test_persisted_sources_accumulate_instrument_coverage_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run_key, code in (("20260709", "000001.SZ"), ("20260710", "000002.SZ")):
                raw = root / "data" / "research" / "raw" / "a_share" / run_key
                raw.mkdir(parents=True)
                pd.DataFrame([
                    {
                        "ts_code": code,
                        "ann_date": "20260425",
                        "end_date": "20260331",
                        "roe": 10.0,
                        "observed_at": f"{run_key}T18:00:00+08:00",
                    }
                ]).to_parquet(raw / "fina_indicator.parquet", index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            frames = pipeline._load_persisted_source_frames()

        self.assertEqual(
            set(frames["fina_indicator"]["ts_code"].astype(str)),
            {"000001.SZ", "000002.SZ"},
        )

    def test_persisted_sources_use_only_latest_declared_cumulative_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_raw = root / "data" / "research" / "raw" / "a_share" / "20260709"
            latest_raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            old_raw.mkdir(parents=True)
            latest_raw.mkdir(parents=True)
            (old_raw / "fina_indicator.parquet").write_text(
                "must not be read", encoding="utf-8"
            )
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331", "roe": 10.0},
                    {"ts_code": "000002.SZ", "ann_date": "20260425", "end_date": "20260331", "roe": 11.0},
                ]
            ).to_parquet(latest_raw / "fina_indicator.parquet", index=False)
            (latest_raw / "snapshot_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": "cumulative",
                        "as_of": "2026-07-10",
                        "sources": ["fina_indicator"],
                    }
                ),
                encoding="utf-8",
            )
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            frames = pipeline._load_persisted_source_frames()

        self.assertEqual(
            set(frames["fina_indicator"]["ts_code"].astype(str)),
            {"000001.SZ", "000002.SZ"},
        )

    def test_persisted_sources_merge_runs_after_cumulative_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_raw = root / "data" / "research" / "raw" / "a_share" / "20260709"
            latest_raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            base_raw.mkdir(parents=True)
            latest_raw.mkdir(parents=True)
            pd.DataFrame(
                [{"ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331", "roe": 10.0}]
            ).to_parquet(base_raw / "fina_indicator.parquet", index=False)
            (base_raw / "snapshot_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": "cumulative",
                        "as_of": "2026-07-09",
                        "sources": ["fina_indicator"],
                    }
                ),
                encoding="utf-8",
            )
            pd.DataFrame(
                [{"ts_code": "000002.SZ", "ann_date": "20260425", "end_date": "20260331", "roe": 11.0}]
            ).to_parquet(latest_raw / "fina_indicator.parquet", index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            frames = pipeline._load_persisted_source_frames()

        self.assertEqual(
            set(frames["fina_indicator"]["ts_code"].astype(str)),
            {"000001.SZ", "000002.SZ"},
        )

    def test_research_source_batch_prioritizes_missing_financial_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260709"
            raw.mkdir(parents=True)
            pd.DataFrame([
                {"ts_code": "000001.SZ", "ann_date": "20260425", "end_date": "20260331", "roe": 10.0}
            ]).to_parquet(raw / "fina_indicator.parquet", index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            ordered = pipeline._research_source_codes(
                ["000001", "000002", "000003"],
                {"000001", "000002", "000003"},
            )

        self.assertEqual(ordered[:2], ["000002", "000003"])
        self.assertEqual(ordered[-1], "000001")

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

    def test_a_share_history_sample_prioritizes_codes_with_persisted_financials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            financial_codes = [f"{index:06d}.SZ" for index in range(90, 100)]
            pd.DataFrame([
                {"ts_code": code, "ann_date": "20260425", "end_date": "20260331", "roe": 10.0}
                for code in financial_codes
            ]).to_parquet(raw / "fina_indicator.parquet", index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10",
                offline=True, max_full_history_instruments=10,
            )

            selected = pipeline._full_history_codes([f"{index:06d}" for index in range(100)])

        self.assertEqual(selected, {f"{index:06d}" for index in range(90, 100)})

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

    def test_default_a_share_keeps_full_history_for_every_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(Path(tmp), market="a_share", agent="codex")
            selected = pipeline._full_history_codes([f"{index:06d}" for index in range(100)])
        self.assertEqual(len(selected), 100)


if __name__ == "__main__":
    unittest.main()
