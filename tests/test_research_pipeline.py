import gc
import hashlib
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from stock_analyze.research.pipeline import (
    ResearchPipeline,
    _baseline_first_deployment_gate,
    _shadow_stop_reason,
)
from stock_analyze.research.classical_specs import mainline_specs
from stock_analyze.research.feature_registry import DEFAULT_REGISTRY_HASH
from stock_analyze.research.source_features import SourceCollection
from stock_analyze.research.schemas import PredictionRecord
from stock_analyze.research.labels import LABEL_CONTRACT_VERSION


class ResearchPipelineTest(unittest.TestCase):
    def test_qdii_source_collection_requests_full_history_context(self):
        from unittest.mock import patch

        pipeline = ResearchPipeline(
            Path("."), market="cn_qdii_etf", agent="codex",
            as_of="2026-08-18", offline=False,
        )
        provider = SimpleNamespace(
            collect_research_sources=lambda _codes: SourceCollection(
                frames={}, health=pd.DataFrame()
            )
        )
        with patch(
            "stock_analyze.markets.cn_qdii_etf.data_provider.make_provider",
            return_value=provider,
        ) as make_provider:
            pipeline._collect_sources(["513100"])

        self.assertEqual(make_provider.call_args.kwargs["history_start"], "20180101")

    @staticmethod
    def _canonical_digest(value: object) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

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

    def test_classical_tournament_rejects_stale_label_contract_before_fitting(self):
        labels = pd.DataFrame({
            "code": ["513100", "513500"],
            "label_contract_version": ["next-open-v1", "next-open-v1"],
        })

        with self.assertRaisesRegex(
            ValueError,
            "research_label_contract_stale:required=next-open-v3-adjusted:observed=next-open-v1",
        ):
            ResearchPipeline._validate_current_label_contract(labels)

    def test_classical_tournament_rejects_mixed_label_contracts(self):
        labels = pd.DataFrame({
            "code": ["513100", "513500"],
            "label_contract_version": [LABEL_CONTRACT_VERSION, "next-open-v1"],
        })

        with self.assertRaisesRegex(
            ValueError,
            "observed=next-open-v1,next-open-v3-adjusted",
        ):
            ResearchPipeline._validate_current_label_contract(labels)

    def test_classical_tournament_accepts_current_label_contract(self):
        labels = pd.DataFrame({
            "code": ["513100", "513500"],
            "label_contract_version": [LABEL_CONTRACT_VERSION] * 2,
        })

        ResearchPipeline._validate_current_label_contract(labels)

    def test_classical_tournament_rejects_partially_missing_label_contract(self):
        labels = pd.DataFrame({
            "code": ["513100", "513500"],
            "label_contract_version": [LABEL_CONTRACT_VERSION, None],
        })

        with self.assertRaisesRegex(
            ValueError,
            "observed=missing,next-open-v3-adjusted",
        ):
            ResearchPipeline._validate_current_label_contract(labels)

    def test_qdii_history_normalization_merges_latest_adjustment_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "cn_qdii_etf" / "shared" / "cache"
            cache.mkdir(parents=True)
            daily_path = cache / "fund_daily_513100_SH_20220117.csv"
            pd.DataFrame({
                "ts_code": ["513100.SH"] * 2,
                "trade_date": ["20220111", "20220114"],
                "open": [5.1, 1.02], "high": [5.2, 1.03],
                "low": [5.0, 1.01], "close": [5.1, 1.02],
                "vol": [1000.0, 1000.0], "amount": [5000.0, 1000.0],
            }).to_csv(daily_path, index=False)
            pd.DataFrame({
                "ts_code": ["513100.SH"] * 2,
                "trade_date": ["20220111", "20220114"],
                "adj_factor": [1.0, 5.0],
            }).to_csv(cache / "fund_adj_513100_SH_20220117.csv", index=False)
            pipeline = ResearchPipeline(
                root, market="cn_qdii_etf", agent="codex",
                as_of="2022-01-17", offline=True,
            )

            normalized = pipeline._normalize_history(daily_path)

        self.assertEqual(normalized["adj_factor"].tolist(), [1.0, 5.0])

    def test_qdii_technical_history_preserves_adjusted_execution_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(
                Path(tmp), market="cn_qdii_etf", agent="codex",
                as_of="2022-01-14", offline=True,
            )
            history = pd.DataFrame({
                "code": ["513100"] * 4,
                "trade_date": ["20220110", "20220111", "20220114", "20220117"],
                "open": [5.0, 5.1, 1.02, 1.04],
                "high": [5.1, 5.2, 1.03, 1.05],
                "low": [4.9, 5.0, 1.01, 1.03],
                "close": [5.0, 5.1, 1.02, 1.04],
                "volume": [1000.0] * 4,
                "amount": [5000.0] * 4,
                "adj_factor": [1.0, 1.0, 5.0, 5.0],
            })

            featured = pipeline._compute_technical_history(history)

        self.assertIn("adjusted_close", featured.columns)
        self.assertAlmostEqual(float(featured.iloc[1]["adjusted_close"]), 5.1)
        self.assertAlmostEqual(float(featured.iloc[2]["adjusted_close"]), 5.1)
        self.assertAlmostEqual(float(featured.iloc[2]["close"]), 1.02)

    def test_qdii_benchmark_history_prefers_adjusted_feature_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(
                Path(tmp), market="cn_qdii_etf", agent="codex",
                as_of="2022-01-17", offline=True,
            )
            raw = pd.DataFrame({
                "code": ["513100"] * 4,
                "trade_date": ["20220110", "20220111", "20220114", "20220117"],
                "open": [5.0, 5.1, 1.02, 1.04],
                "high": [5.1, 5.2, 1.03, 1.05],
                "low": [4.9, 5.0, 1.01, 1.03],
                "close": [5.0, 5.1, 1.02, 1.04],
                "volume": [1000.0] * 4,
                "amount": [5000.0] * 4,
                "adj_factor": [1.0, 1.0, 5.0, 5.0],
            })
            features = pipeline._compute_technical_history(raw)
            features["account_id"] = "us_exposure"
            with patch.object(
                pipeline, "_load_persisted_source_frames",
                return_value={"benchmark_513100": raw},
            ):
                benchmark, coverage = pipeline._benchmark_history(
                    features,
                    account={
                        "id": "us_exposure", "scope": "us_exposure",
                        "benchmark": "513100.SH", "cash": 500000,
                    },
                )

        values = benchmark.set_index("trade_date")
        self.assertEqual(coverage, 1.0)
        self.assertAlmostEqual(float(values.loc["20220111", "close"]), 5.1 / 5.0)
        self.assertAlmostEqual(float(values.loc["20220114", "close"]), 5.1 / 5.0)

    def test_a_share_benchmark_history_merges_full_canonical_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "shared" / "backtest_cache" / "benchmark_daily"
            cache.mkdir(parents=True)
            pd.DataFrame({
                "ts_code": ["000300.SH"] * 4,
                "trade_date": ["20180102", "20190102", "20240102", "20260817"],
                "open": [100.0, 101.0, 102.0, 103.0],
                "close": [100.0, 101.0, 102.0, 103.0],
            }).to_csv(cache / "000300.csv", index=False)
            truncated = pd.DataFrame({
                "ts_code": ["000300.SH"] * 2,
                "trade_date": ["20240102", "20260817"],
                "open": [202.0, 203.0],
                "close": [202.0, 203.0],
                "observed_at": ["2026-08-17", "2026-08-17"],
            })
            features = pd.DataFrame({
                "code": ["000001"] * 4,
                "trade_date": ["20180102", "20190102", "20240102", "20260817"],
                "account_id": ["hs300"] * 4,
            })
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex",
                as_of="2026-08-17", offline=True,
            )
            with patch.object(
                pipeline,
                "_load_persisted_source_frames",
                return_value={"benchmark_000300": truncated},
            ):
                benchmark, coverage = pipeline._benchmark_history(
                    features,
                    account={
                        "id": "hs300", "scope": "hs300",
                        "benchmark": "000300.SH", "cash": 500000,
                    },
                )

        values = benchmark.set_index("trade_date")
        self.assertEqual(coverage, 1.0)
        self.assertEqual(set(values.index), set(features["trade_date"]))
        self.assertAlmostEqual(float(values.loc["20240102", "close"]), 202.0 / 100.0)

    def test_a_share_prepare_sources_include_account_benchmarks(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(
                Path(tmp), market="a_share", agent="codex",
                as_of="2026-08-17", offline=True,
            )

            names = pipeline._a_share_prep_source_names()

        self.assertIn("benchmark_000300", names)
        self.assertIn("benchmark_000905", names)

    def test_refresh_labels_uses_latest_feature_snapshot_before_non_trading_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="cn_qdii_etf",
                agent="codex",
                as_of="2026-08-09",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "cn_qdii_etf",
                "2026-08-07",
                pd.DataFrame([self._scoped_feature("513100")]),
            )
            labels = pd.DataFrame([{
                "code": "513100",
                "trade_date": "20260807",
                "horizon": 10,
                "label": "up",
                "label_contract_version": LABEL_CONTRACT_VERSION,
            }])

            with patch.object(
                pipeline,
                "_build_forward_label_snapshot",
                return_value=(labels, 1.0),
            ):
                result = pipeline.refresh_labels()

            written = pipeline.store.read_label_snapshot(
                "cn_qdii_etf", "2026-08-07"
            )

        self.assertEqual(result["snapshot_date"], "20260807")
        self.assertEqual(result["label_contract_version"], LABEL_CONTRACT_VERSION)
        self.assertEqual(len(written), 1)

    def test_a_share_labels_use_continuous_prices_across_membership_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-01-12",
                offline=True,
            )
            dates = pd.date_range("2026-01-01", periods=8, freq="B")
            prices = np.linspace(10.0, 10.7, len(dates))
            history_path = root / "history_000001_20260112_8.csv"
            pd.DataFrame({
                "日期": dates.strftime("%Y-%m-%d"),
                "开盘": prices,
                "最高": prices + 0.1,
                "最低": prices - 0.1,
                "收盘": prices,
                "成交量": 1_000_000,
                "成交额": 10_000_000,
            }).to_csv(history_path, index=False)
            member_rows = [0, 1, 5, 6]
            features = pd.DataFrame({
                "code": ["000001"] * len(member_rows),
                "trade_date": dates[member_rows].strftime("%Y%m%d"),
                "open": prices[member_rows],
                "high": prices[member_rows] + 0.1,
                "low": prices[member_rows] - 0.1,
                "close": prices[member_rows],
                "account_id": ["hs300"] * len(member_rows),
                "universe_quality": ["available"] * len(member_rows),
                "unbiased_universe": [True] * len(member_rows),
                "universe_contract_version": ["pit-universe-v1"] * len(member_rows),
                "membership_source": ["materialized_index_weight"] * len(member_rows),
            })
            benchmark = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "open": np.linspace(100.0, 100.7, len(dates)),
                "close": np.linspace(100.0, 100.7, len(dates)),
            })
            account = {
                "id": "hs300",
                "scope": "hs300",
                "benchmark": "000300.SH",
            }
            with (
                patch.object(pipeline, "_baseline_accounts", return_value=[account]),
                patch.object(pipeline, "_benchmark_history", return_value=(benchmark, 1.0)),
                patch.object(pipeline, "_history_files", return_value=[history_path]),
            ):
                labels, _ = pipeline._build_forward_label_snapshot(features)

        row = labels.loc[
            labels["trade_date"].eq(dates[0].strftime("%Y%m%d"))
            & labels["horizon"].eq(3)
        ].iloc[0]
        self.assertEqual(row["label_end_date"], dates[3].strftime("%Y%m%d"))

    def test_baseline_first_winner_is_trained_only_on_development_and_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-07",
                offline=True,
            )
            dataset = pd.DataFrame({
                "trade_date": ["20240102", "20241231", "20250102"],
                "horizon": [20, 20, 20],
                "label_end_date": ["20240201", "20250131", "20250201"],
                "label": ["up", "flat", "down"],
                "excess_return": [0.01, 0.0, -0.01],
                "momentum_20": [0.1, 0.0, -0.1],
                "momentum_60": [0.2, 0.0, -0.2],
            })
            bundle = SimpleNamespace(
                model_version="baseline-first-model-v1",
                metrics={
                    **self._passing_gate_metrics(),
                    "training_protocol_version": "fixture-v1",
                    "replay_contract": "model",
                },
            )
            result = {
                "evaluation_contract": "baseline-first-incremental-v2",
                "training_input": {
                    "market": "a_share",
                    "source_fingerprint": "a" * 64,
                },
                "report_path": str(root / "reports" / "research" / "report.md"),
                "trial_declaration_id": "trial-v1",
                "incremental_gate": {"net_excess_return_delta": 0.02},
                "baseline": {
                    "net_excess_return": 0.02,
                    "max_drawdown": 0.12,
                    "annual_turnover": 4.0,
                    "subperiods": [
                        {"fold": fold, "net_excess_return": 0.02}
                        for fold in range(3)
                    ],
                },
            }
            spec = mainline_specs("a_share", "hs300")[0]

            with patch(
                "stock_analyze.research.pipeline.train_model_bundle",
                return_value=bundle,
            ) as train, patch(
                "stock_analyze.research.pipeline.save_model_bundle",
            ), patch.object(
                __import__(
                    "stock_analyze.research.pipeline",
                    fromlist=["ModelRegistry"],
                ).ModelRegistry,
                "admit_development_shadow",
                return_value={
                    "models": {
                        bundle.model_version: {"status": "shadow"},
                    }
                },
            ):
                frozen = pipeline._freeze_baseline_first_candidate(
                    dataset=dataset,
                    feature_columns=("momentum_20", "momentum_60"),
                    model_spec=spec,
                    portfolio_contract={"accounts": [], "trading": {}},
                    account_scope="hs300",
                    development_start="20240101",
                    development_end="20241231",
                    evaluation=result,
                )
            transfer_report = Path(frozen["transfer_report"])
            transfer_payload = json.loads(
                transfer_report.read_text(encoding="utf-8")
            )

        trained = train.call_args.args[0]
        self.assertEqual(trained["trade_date"].tolist(), ["20240102", "20241231"])
        self.assertEqual(frozen["status"], "shadow")
        self.assertEqual(frozen["model_version"], bundle.model_version)
        self.assertIn("tournaments", transfer_report.parts)
        self.assertEqual(transfer_payload["formal_strategy_activated"], False)
        self.assertEqual(
            transfer_payload["candidates"][0]["model_version"],
            bundle.model_version,
        )

    def test_baseline_first_winner_is_not_admitted_when_exact_bundle_is_not_deployable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-07",
                offline=True,
            )
            dataset = pd.DataFrame({
                "trade_date": ["20240102", "20241231"],
                "horizon": [20, 20],
                "label_end_date": ["20240201", "20250131"],
                "label": ["up", "flat"],
                "excess_return": [0.01, 0.0],
                "momentum_20": [0.1, 0.0],
                "momentum_60": [0.2, 0.0],
            })
            bundle = SimpleNamespace(
                model_version="not-deployable",
                metrics={
                    **self._passing_gate_metrics(),
                    "net_excess_return": -0.01,
                    "trade_count": 0,
                    "replay_contract": "model",
                },
            )
            evaluation = {
                "evaluation_contract": "baseline-first-incremental-v2",
                "training_input": {
                    "market": "a_share",
                    "source_fingerprint": "a" * 64,
                },
                "trial_declaration_id": "trial-v1",
                "incremental_gate": {"net_excess_return_delta": 0.02},
                "baseline": {
                    "net_excess_return": 0.01,
                    "max_drawdown": 0.12,
                    "annual_turnover": 4.0,
                },
            }

            with (
                patch(
                    "stock_analyze.research.pipeline.train_model_bundle",
                    return_value=bundle,
                ),
                patch("stock_analyze.research.pipeline.save_model_bundle") as save,
                patch.object(
                    __import__(
                        "stock_analyze.research.pipeline",
                        fromlist=["ModelRegistry"],
                    ).ModelRegistry,
                    "admit_development_shadow",
                ) as admit,
            ):
                result = pipeline._freeze_baseline_first_candidate(
                    dataset=dataset,
                    feature_columns=("momentum_20", "momentum_60"),
                    model_spec=mainline_specs("a_share", "hs300")[0],
                    portfolio_contract={"accounts": [], "trading": {}},
                    account_scope="hs300",
                    development_start="20240101",
                    development_end="20241231",
                    evaluation=evaluation,
                )

        self.assertFalse(result["admitted"])
        self.assertEqual(result["status"], "research")
        self.assertIn("positive_deployable_net_return", result["deployment_gate"]["reasons"])
        save.assert_not_called()
        admit.assert_not_called()

    def test_exact_bundle_gate_requires_three_folds_and_majority_increment(self):
        baseline = {
            "net_excess_return": 0.01,
            "max_drawdown": 0.10,
            "annual_turnover": 4.0,
            "subperiods": [
                {"fold": fold, "net_excess_return": 0.01}
                for fold in range(3)
            ],
        }
        metrics = {
            **self._passing_gate_metrics(),
            "replay_contract": "model",
            "deployable_subperiods": [
                {"fold": 0, "net_excess_return": 0.02},
                {"fold": 1, "net_excess_return": 0.00},
            ],
        }

        gate = _baseline_first_deployment_gate(metrics, baseline)

        self.assertFalse(gate["passed"])
        self.assertIn("deployable_eligible_folds", gate["reasons"])
        self.assertIn("deployable_positive_fold_majority", gate["reasons"])

    def test_baseline_first_without_verified_input_is_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(
                Path(tmp),
                market="a_share",
                agent="codex",
                as_of="2026-08-07",
                offline=True,
            )
            dataset = pd.DataFrame({
                "trade_date": ["20240102"],
                "label_end_date": ["20240201"],
            })
            with patch(
                "stock_analyze.research.pipeline.train_model_bundle"
            ) as train:
                result = pipeline._freeze_baseline_first_candidate(
                    dataset=dataset,
                    feature_columns=("momentum_20",),
                    model_spec=mainline_specs("a_share", "hs300")[0],
                    portfolio_contract={"accounts": [], "trading": {}},
                    account_scope="hs300",
                    development_start="20240101",
                    development_end="20241231",
                    evaluation={"baseline": {}},
                )

        self.assertFalse(result["admitted"])
        self.assertEqual(
            result["deployment_gate"]["reasons"],
            ["training_input_provenance"],
        )
        train.assert_not_called()

    def test_baseline_first_window_is_frozen_without_legacy_tournament(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-07",
                offline=True,
            )
            dates = pd.date_range("2020-01-02", periods=300, freq="B")
            dataset = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "label_end_date": (
                    dates + pd.offsets.BDay(20)
                ).strftime("%Y%m%d"),
                "research_scope": "hs300",
            })

            first = pipeline._baseline_first_window_payload(
                dataset=dataset,
                account_scope="hs300",
                horizon=20,
            )
            extended = pd.concat([
                dataset,
                pd.DataFrame({
                    "trade_date": ["20260810"],
                    "label_end_date": ["20260907"],
                    "research_scope": ["hs300"],
                }),
            ], ignore_index=True)
            second = pipeline._baseline_first_window_payload(
                dataset=extended,
                account_scope="hs300",
                horizon=20,
            )

        self.assertEqual(first, second)
        self.assertEqual(first["source"], "deterministic_initialization")
        self.assertTrue(first["historically_consumed"])

    def test_baseline_first_uses_exact_training_input_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-08",
                offline=True,
            )
            for snapshot in ("20260807", "20260808"):
                pipeline.store.write_feature_snapshot(
                    "a_share",
                    snapshot,
                    pd.DataFrame([{"code": "000001", "trade_date": snapshot}]),
                )
                pipeline.store.write_label_snapshot(
                    "a_share",
                    snapshot,
                    pd.DataFrame([{"code": "000001", "trade_date": snapshot}]),
                )

            selected = pipeline._baseline_first_snapshot_date({
                "market": "a_share",
                "snapshot_date": "20260807",
                "source_fingerprint": "a" * 64,
                "files": [
                    str(path.relative_to(root))
                    for path in (
                        pipeline.store.feature_snapshot_path(
                            "a_share", "20260807"
                        ),
                        pipeline.store.label_snapshot_path(
                            "a_share", "20260807"
                        ),
                    )
                ],
            })

        self.assertEqual(selected, "20260807")

    def test_training_bundle_ignores_unlisted_local_window_manifest(self):
        dates = pd.date_range("2020-01-02", periods=300, freq="B")
        dataset = pd.DataFrame({
            "trade_date": dates.strftime("%Y%m%d"),
            "label_end_date": (
                dates + pd.offsets.BDay(20)
            ).strftime("%Y%m%d"),
            "research_scope": "hs300",
        })
        training_input = {
            "market": "a_share",
            "snapshot_date": "20260807",
            "source_fingerprint": "a" * 64,
            "files": [],
        }
        payloads = []
        with tempfile.TemporaryDirectory() as clean_tmp, tempfile.TemporaryDirectory() as dirty_tmp:
            dirty_root = Path(dirty_tmp)
            stale = (
                dirty_root
                / "data/research/baseline_first/a_share/hs300/window_manifest.json"
            )
            stale.parent.mkdir(parents=True)
            stale.write_text('{"stale":true}', encoding="utf-8")
            for root in (Path(clean_tmp), dirty_root):
                pipeline = ResearchPipeline(
                    root,
                    market="a_share",
                    agent="codex",
                    as_of="2026-08-07",
                    offline=True,
                )
                payload = pipeline._baseline_first_window_payload(
                    dataset=dataset,
                    account_scope="hs300",
                    horizon=20,
                    training_input=training_input,
                )
                payloads.append({
                    key: value
                    for key, value in payload.items()
                    if not key.startswith("_window_manifest_")
                })

        self.assertEqual(payloads[0], payloads[1])

    def test_existing_scope_without_frozen_window_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-07",
                offline=True,
            )
            model_root = pipeline._model_root(20, "hs300")
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(
                '{"models":{"legacy":{"status":"rejected"}}}',
                encoding="utf-8",
            )
            dates = pd.date_range("2020-01-02", periods=300, freq="B")
            dataset = pd.DataFrame({
                "trade_date": dates.strftime("%Y%m%d"),
                "label_end_date": (
                    dates + pd.offsets.BDay(20)
                ).strftime("%Y%m%d"),
                "research_scope": "hs300",
            })

            with self.assertRaisesRegex(
                ValueError,
                "baseline_first_window_manifest_missing_existing_scope",
            ):
                pipeline._baseline_first_window_payload(
                    dataset=dataset,
                    account_scope="hs300",
                    horizon=20,
                )

    def test_shadow_stop_reason_extends_missing_evidence_but_caps_at_week_sixteen(self):
        reports = {
            "ranker": SimpleNamespace(reasons=("forward_evidence_status", "forward_cycles")),
            "portfolio": SimpleNamespace(reasons=("forward_evidence_status", "forward_cycles")),
        }

        self.assertIsNone(_shadow_stop_reason(12, reports))
        self.assertEqual(
            _shadow_stop_reason(16, reports),
            "shadow_evidence_cap_reached",
        )

    def test_shadow_stop_reason_rejects_hard_quality_failure_at_week_twelve(self):
        reports = {
            "ranker": SimpleNamespace(reasons=("forward_net_excess_return",)),
            "portfolio": SimpleNamespace(reasons=("forward_net_excess_return",)),
        }

        self.assertEqual(
            _shadow_stop_reason(12, reports),
            "shadow_quality_gate_failed:forward_net_excess_return",
        )

    def test_materialized_stock_basic_repairs_unclassified_industry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260807"
            raw.mkdir(parents=True)
            pd.DataFrame([
                {"ts_code": "000001.SZ", "industry": "银行"},
                {"ts_code": "000002.SZ", "industry": "房地产"},
            ]).to_parquet(raw / "stock_basic.parquet", index=False)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-07",
                offline=True,
            )
            features = pd.DataFrame({
                "code": ["000001", "000002"],
                "industry": ["unclassified", "unclassified"],
                "industry_l2": ["unclassified", "unclassified"],
            })

            repaired = pipeline._attach_a_share_industry_fallback(features)

        self.assertEqual(repaired["industry"].tolist(), ["银行", "房地产"])
        self.assertEqual(
            repaired["industry_source"].tolist(),
            ["tushare_stock_basic_snapshot", "tushare_stock_basic_snapshot"],
        )

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
            "trade_count": 25,
            "capital_utilization": 0.92,
            "valid_trial_count": 5,
            "trial_evidence_status": "available",
            "execution_evidence_status": "available",
            "missing_liquidity_notional_ratio": 0.0,
            "impact_capped_notional_ratio": 0.0,
            "edge_calibration_available": True,
            "attribution_status": "reconciled",
            "diagnostic_net_excess_return": 0.03,
            "diagnostic_max_drawdown": 0.12,
            "diagnostic_annual_turnover": 4.0,
            "diagnostic_trade_count": 25,
            "diagnostic_capital_utilization": 0.92,
            "diagnostic_all_accounts_positive_active": True,
            "diagnostic_simulator_version": "paper-parity-daily-v1",
            "diagnostic_execution_evidence_status": "available",
            "diagnostic_missing_liquidity_notional_ratio": 0.0,
            "diagnostic_impact_capped_notional_ratio": 0.0,
            "diagnostic_attribution_status": "reconciled",
            "forward_evidence_status": "available",
            "forward_cycles": 12,
            "forward_net_excess_return": 0.02,
            "forward_max_drawdown": 0.10,
            "forward_all_accounts_positive_active": True,
            "deployable_subperiods": [
                {"fold": fold, "net_excess_return": 0.03}
                for fold in range(3)
            ],
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
        self.assertEqual(getattr(snapshot["code"].dtype, "storage", None), "pyarrow")
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
        float_columns = labels.select_dtypes(include=["floating"]).columns
        self.assertTrue(
            all(labels[column].dtype.itemsize <= 4 for column in float_columns)
        )
        self.assertEqual(set(labels["label_contract_version"]), {LABEL_CONTRACT_VERSION})
        self.assertEqual(set(labels["account_id"]), {"hs300"})
        self.assertEqual(len(feature_metadata["registry_hash"]), 16)
        self.assertIn("high_value_add_proxy", feature_metadata["registered_features"])

    def test_cached_prepare_uses_parquet_footer_when_manifest_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex",
                as_of="2026-07-10", offline=True,
            )
            destination = pipeline.store.feature_snapshot_path(
                "a_share", "2026-07-10"
            )
            destination.parent.mkdir(parents=True)
            pd.DataFrame({
                "code": ["000001", "000002"],
                "trade_date": ["20260710", "20260710"],
                "open": [10.0, 20.0],
            }).to_parquet(destination, index=False)
            destination.with_suffix(".metadata.json").write_text(
                json.dumps({"registry_hash": DEFAULT_REGISTRY_HASH}),
                encoding="utf-8",
            )
            with patch.object(
                pipeline.store,
                "read_feature_snapshot",
                side_effect=AssertionError("wide parquet must not be loaded"),
            ):
                result = pipeline.prepare_data()

        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["rows"], 2)

    def test_research_stage_projection_excludes_unrelated_wide_features(self):
        columns = set(ResearchPipeline._RESEARCH_STAGE_FEATURE_COLUMNS)

        self.assertIn("adjusted_open", columns)
        self.assertIn("macd_hist", columns)
        self.assertIn("industry_breadth", columns)
        self.assertNotIn("earnings_stability", columns)
        self.assertNotIn("event_materiality_positive_20d", columns)

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

    def test_a_share_technical_features_use_adjusted_prices_but_keep_raw_execution_prices(self):
        dates = pd.date_range("2026-01-01", periods=80, freq="B")
        raw_close = np.r_[np.full(40, 100.0), np.full(40, 50.0)]
        adj_factor = np.r_[np.ones(40), np.full(40, 2.0)]
        history = pd.DataFrame({
            "code": "000001",
            "trade_date": dates.strftime("%Y%m%d"),
            "open": raw_close,
            "high": raw_close,
            "low": raw_close,
            "close": raw_close,
            "volume": 1_000.0,
            "amount": 10_000_000.0,
            "amount_unit": "yuan",
            "adj_factor": adj_factor,
        })
        pipeline = ResearchPipeline(
            Path("."), market="a_share", agent="codex", as_of="2026-04-30", offline=True
        )

        featured = pipeline._compute_technical_history(history)

        self.assertEqual(float(featured.iloc[40]["close"]), 50.0)
        self.assertAlmostEqual(float(featured.iloc[40]["return_1"]), 0.0, places=12)
        self.assertAlmostEqual(float(featured.iloc[60]["momentum_20"]), 0.0, places=12)
        self.assertNotIn("adj_factor", featured.columns)

    def test_regime_tabular_dataset_uses_adjusted_features_and_frozen_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feature_path = root / "data" / "research" / "features" / "a_share" / "20260807.parquet"
            label_path = root / "data" / "research" / "labels" / "a_share" / "20260807.parquet"
            raw_root = root / "data" / "research" / "raw" / "a_share" / "20260807"
            feature_path.parent.mkdir(parents=True)
            label_path.parent.mkdir(parents=True)
            raw_root.mkdir(parents=True)
            dates = pd.date_range("2020-01-01", periods=140, freq="B")
            date_keys = dates.strftime("%Y%m%d")
            feature_rows = []
            label_rows = []
            adjustment_rows = []
            for code, scope in (("000001", "zz500"), ("000002", "hs300")):
                raw_close = np.r_[np.full(70, 100.0), np.full(70, 50.0)]
                factor = np.r_[np.ones(70), np.full(70, 2.0)]
                for index, trade_date in enumerate(date_keys):
                    feature_rows.append({
                        "code": code,
                        "trade_date": trade_date,
                        "open": raw_close[index],
                        "high": raw_close[index],
                        "low": raw_close[index],
                        "close": raw_close[index],
                        "volume": 1_000.0,
                        "amount": 10_000_000.0,
                        "amount_unit": "yuan",
                        "turnover_rate": 1.0,
                        "industry": "测试行业",
                        "total_mv": 1_000_000.0,
                        "account_id": scope,
                        "research_scope": scope,
                        "roe": 10.0,
                    })
                    adjustment_rows.append({
                        "ts_code": f"{code}.SZ",
                        "trade_date": trade_date,
                        "adj_factor": factor[index],
                    })
                    label_rows.append({
                        "code": code,
                        "trade_date": trade_date,
                        "horizon": 20,
                        "entry_date": trade_date,
                        "entry_price": raw_close[index],
                        "benchmark_entry_price": 100.0,
                        "label_end_date": trade_date,
                        "excess_return": 0.01,
                        "account_id": scope,
                        "research_scope": scope,
                    })
                    label_rows.append({
                        **label_rows[-1],
                        "horizon": 3,
                    })
            pd.DataFrame(feature_rows).to_parquet(feature_path, index=False)
            pd.DataFrame(label_rows).to_parquet(label_path, index=False)
            pd.DataFrame(adjustment_rows).to_parquet(raw_root / "adj_factor.parquet", index=False)
            pd.DataFrame([
                {
                    "index_code": "000905.SH",
                    "con_code": "000001.SZ",
                    "trade_date": str(date_keys[0]),
                    "weight": 100.0,
                }
            ]).to_parquet(raw_root / "index_weight.parquet", index=False)
            pd.DataFrame({
                "ts_code": "000905.SH",
                "trade_date": date_keys,
                "close": np.linspace(100.0, 110.0, len(date_keys)),
            }).to_parquet(raw_root / "benchmark_000905.parquet", index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-08-07", offline=True
            )
            config = {
                "market": "a_share",
                "account_scope": "zz500",
                "horizon": 20,
                "development": {
                    "start": str(date_keys[0]),
                    "end": str(date_keys[-1]),
                },
            }

            dataset, feature_columns = pipeline._load_regime_tabular_dataset(
                snapshot_date="20260807",
                config=config,
            )

        self.assertEqual(set(dataset["research_scope"].astype(str)), {"zz500"})
        self.assertEqual(set(pd.to_numeric(dataset["horizon"])), {20})
        split_row = dataset.loc[dataset["trade_date"].astype(str).eq(str(date_keys[70]))].iloc[0]
        self.assertEqual(float(split_row["close"]), 50.0)
        self.assertAlmostEqual(float(split_row["return_1"]), 0.0, places=12)
        self.assertIn("momentum_120_cs_rank", feature_columns)
        self.assertAlmostEqual(float(dataset["benchmark_weight"].dropna().iloc[-1]), 1.0)
        self.assertFalse(dataset.duplicated(["code", "trade_date", "account_id"]).any())

    def test_materialized_status_fields_survive_history_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "history_000001_20260710_2.csv"
            pd.DataFrame([
                {
                    "trade_date": "2026-07-09",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.1,
                    "volume": 1000.0,
                    "amount": 10_000.0,
                    "name": "平安银行",
                    "industry": "银行",
                    "list_date": "19910403",
                    "delist_date": pd.NA,
                    "security_status": "listed",
                    "is_st": False,
                    "is_suspended": False,
                    "is_tradable": True,
                    "status_conflict": False,
                    "status_source": "baostock_history_isST_v1",
                    "suspension_status_source": "baostock",
                    "tushare_suspend_timing": pd.NA,
                    "tushare_suspend_type": pd.NA,
                },
                {
                    "trade_date": "2026-07-10",
                    "open": pd.NA,
                    "high": pd.NA,
                    "low": pd.NA,
                    "close": pd.NA,
                    "volume": pd.NA,
                    "amount": pd.NA,
                    "name": "平安银行",
                    "industry": "银行",
                    "list_date": "19910403",
                    "delist_date": pd.NA,
                    "security_status": "suspended",
                    "is_st": False,
                    "is_suspended": True,
                    "is_tradable": False,
                    "status_conflict": False,
                    "status_source": "baostock_history_isST_v1",
                    "suspension_status_source": "baostock+tushare",
                    "tushare_suspend_timing": "09:30-15:00",
                    "tushare_suspend_type": "S",
                },
            ]).to_csv(path, index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            normalized = pipeline._normalize_history(path)

        expected = {
            "name", "industry", "list_date", "delist_date", "security_status",
            "is_st", "is_suspended", "status_source",
            "is_tradable", "status_conflict", "suspension_status_source",
            "tushare_suspend_timing", "tushare_suspend_type",
        }
        self.assertTrue(expected.issubset(normalized.columns))
        suspended = normalized.loc[normalized["trade_date"].eq("20260710")].iloc[0]
        self.assertTrue(bool(suspended["is_suspended"]))
        self.assertTrue(pd.isna(suspended["open"]))

    def test_materialized_history_has_one_canonical_volume_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "history_000001_20260710_1.csv"
            pd.DataFrame([{
                "trade_date": "20260710",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "vol": 1234.0,
                "volume": 1234.0,
                "amount": 10_000.0,
            }]).to_csv(path, index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            normalized = pipeline._normalize_history(path)

        self.assertEqual(list(normalized.columns).count("volume"), 1)
        self.assertEqual(float(normalized.iloc[0]["volume"]), 1234.0)

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

    def test_a_share_source_enrichment_writes_bounded_code_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_root = root / "batches"
            batch_root.mkdir()
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10",
                offline=True,
            )
            features = pd.DataFrame([
                {"code": "000001", "trade_date": "20260709", "close": 10.0},
                {"code": "000001", "trade_date": "20260710", "close": 11.0},
                {"code": "000002", "trade_date": "20260709", "close": 20.0},
                {"code": "000002", "trade_date": "20260710", "close": 21.0},
            ])
            sources = {
                "daily_basic": pd.DataFrame([
                    {"ts_code": "000001.SZ", "trade_date": "20260709", "pe_ttm": 10.0},
                    {"ts_code": "000002.SZ", "trade_date": "20260709", "pe_ttm": 20.0},
                ]),
                "fina_indicator": pd.DataFrame([
                    {"ts_code": "000001.SZ", "ann_date": "20260709", "end_date": "20260630", "roe": 8.0},
                    {"ts_code": "000002.SZ", "ann_date": "20260709", "end_date": "20260630", "roe": 9.0},
                ]),
            }

            count = pipeline._write_a_share_enriched_feature_batches(
                features,
                sources,
                batch_root,
                batch_size=1,
            )
            produced = len(list(batch_root.glob("batch-*.parquet")))
            enriched = pd.read_parquet(batch_root).sort_values(
                ["code", "trade_date"]
            )

        self.assertEqual(count, 2)
        self.assertEqual(produced, 2)
        self.assertEqual(enriched["code"].nunique(), 2)
        self.assertEqual(set(enriched["pe_ttm"].dropna()), {10.0, 20.0})
        self.assertEqual(set(enriched["roe"].dropna()), {8.0, 9.0})

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
            model_root = (
                root / "data" / "research" / "models" / "a_share" / "hs300" / "5"
            )
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

            artifact, status = pipeline._resolve_model(5, "hs300")

        self.assertEqual(artifact.name, "shadow.joblib")
        self.assertEqual(status, "shadow")

    def test_resolve_model_falls_back_to_market_registry_when_scoped_artifact_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
            )
            scoped_root = (
                root / "data" / "research" / "models" / "a_share" / "hs300" / "5"
            )
            scoped_root.mkdir(parents=True)
            (scoped_root / "registry.json").write_text(json.dumps({
                "champion_model_version": "scoped-v1",
                "models": {
                    "scoped-v1": {
                        "status": "active",
                        "artifact": str(scoped_root / "missing-scoped.joblib"),
                    },
                },
            }), encoding="utf-8")
            market_root = (
                root / "data" / "research" / "models" / "a_share" / "5"
            )
            market_root.mkdir(parents=True)
            market_artifact = market_root / "market-v1.joblib"
            market_artifact.touch()
            (market_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "market-v1": {
                        "status": "research",
                        "artifact": str(market_artifact),
                    },
                },
            }), encoding="utf-8")

            artifact, statuses, provenance = (
                pipeline._resolve_model_roles_with_provenance(5, "hs300")
            )

        self.assertEqual(artifact, market_artifact)
        self.assertEqual(statuses["ranker"], "research")
        self.assertEqual(provenance["requested_scope"], "hs300")
        self.assertEqual(provenance["selected_scope"], "")
        self.assertEqual(provenance["resolution"], "market_fallback")

    def test_predict_routes_each_account_to_its_scoped_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="test",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([
                    self._scoped_feature("000001"),
                    {
                        **self._scoped_feature("000002"),
                        "account_id": "zz500",
                        "research_scope": "zz500",
                        "benchmark_code": "000905",
                    },
                ]),
            )
            for scope in ("hs300", "zz500"):
                model_root = (
                    root
                    / "data/research/models/a_share"
                    / scope
                    / "5"
                )
                model_root.mkdir(parents=True)
                (model_root / "registry.json").write_text(
                    json.dumps({
                        "champion_model_version": f"{scope}-v1",
                        "models": {
                            f"{scope}-v1": {
                                "status": "active",
                                "role_status": {
                                    "classifier": "active",
                                    "ranker": "active",
                                    "portfolio": "active",
                                },
                                "artifact": str(model_root / f"{scope}.joblib"),
                            }
                        },
                    }),
                    encoding="utf-8",
                )

            def load(path):
                scope = Path(path).stem
                return SimpleNamespace(
                    horizon=5,
                    account_scope=scope,
                    model_version=f"{scope}-v1",
                    metrics={},
                )

            def prediction(bundle, features, *_args, **_kwargs):
                self.assertEqual(set(features["account_id"].astype(str)), {bundle.account_scope})
                row = features.iloc[0]
                return [PredictionRecord(
                    code=str(row["code"]),
                    as_of="2026-07-10",
                    horizon=5,
                    p_up=0.5,
                    p_flat=0.3,
                    p_down=0.2,
                    model_version=bundle.model_version,
                    account_scope=bundle.account_scope,
                    metadata={
                        "account_id": bundle.account_scope,
                        "research_scope": bundle.account_scope,
                    },
                )]

            with (
                patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=load),
                patch("stock_analyze.research.pipeline.generate_predictions", side_effect=prediction),
                patch.object(
                    pipeline,
                    "_assess_model_drift",
                    side_effect=lambda _h, _b, records, **_kwargs: (
                        records,
                        {"status": "current"},
                    ),
                ),
            ):
                result = pipeline.predict(horizon=5)

            predictions = pd.read_parquet(
                root / "data/a_share/test/predictions/20260710.parquet"
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            set(predictions["model_version"]),
            {"hs300-v1", "zz500-v1"},
        )
        self.assertEqual(
            set(predictions["account_scope"]),
            {"hs300", "zz500"},
        )

    def test_predict_runs_market_fallback_once_then_partitions_account_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="test",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([
                    self._scoped_feature("000001"),
                    {
                        **self._scoped_feature("000002"),
                        "account_id": "zz500",
                        "research_scope": "zz500",
                        "benchmark_code": "000905",
                    },
                ]),
            )
            market_root = (
                root / "data/research/models/a_share/5"
            )
            market_root.mkdir(parents=True)
            artifact = market_root / "market-active.joblib"
            artifact.touch()
            (market_root / "registry.json").write_text(
                json.dumps({
                    "champion_model_version": "market-active",
                    "models": {
                        "market-active": {
                            "status": "active",
                            "role_status": {
                                "classifier": "active",
                                "ranker": "active",
                                "portfolio": "active",
                            },
                            "artifact": str(artifact),
                        }
                    },
                }),
                encoding="utf-8",
            )
            bundle = SimpleNamespace(
                horizon=5,
                account_scope=None,
                model_version="market-active",
                metrics={},
            )
            prediction_calls = []
            drift_calls = []

            def prediction(_bundle, features, *_args, **_kwargs):
                prediction_calls.append(len(features))
                return [
                    PredictionRecord(
                        code=str(row["code"]),
                        as_of="2026-07-10",
                        horizon=5,
                        p_up=0.5,
                        p_flat=0.3,
                        p_down=0.2,
                        model_version="market-active",
                        account_scope="",
                        metadata={
                            "account_id": str(row["account_id"]),
                            "research_scope": str(row["research_scope"]),
                        },
                    )
                    for _, row in features.iterrows()
                ]

            def drift(_horizon, _bundle, records, **kwargs):
                drift_calls.append((len(records), kwargs["account_scope"]))
                return records, {"status": "current"}

            with (
                patch(
                    "stock_analyze.research.pipeline.load_model_bundle",
                    return_value=bundle,
                ),
                patch(
                    "stock_analyze.research.pipeline.generate_predictions",
                    side_effect=prediction,
                ),
                patch.object(
                    pipeline,
                    "_assess_model_drift",
                    side_effect=drift,
                ),
            ):
                result = pipeline.predict(horizon=5)

            predictions = pd.read_parquet(
                root / "data/a_share/test/predictions/20260710.parquet"
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(prediction_calls, [2])
        self.assertEqual(drift_calls, [(2, None)])
        self.assertEqual(
            set(predictions["research_scope"]),
            {"hs300", "zz500"},
        )
        self.assertEqual(
            set(predictions["account_scope"]),
            {"hs300", "zz500"},
        )
        self.assertTrue(
            all(
                item["resolution"] == "market_fallback"
                for item in result["model_resolution"].values()
            )
        )

    def test_training_records_research_to_shadow_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10")
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([self._scoped_feature()])
            )
            pipeline.store.write_label_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([{
                    "code": "000001",
                    "trade_date": "20260710",
                    "horizon": 3,
                    "label": "up",
                    "label_contract_version": LABEL_CONTRACT_VERSION,
                }])
            )

            def bundle(*_args, horizon, **_kwargs):
                return SimpleNamespace(horizon=horizon, model_version=f"m{horizon}", metrics=self._passing_gate_metrics())

            with (
                patch("stock_analyze.research.pipeline.train_model_bundle", side_effect=bundle),
                patch("stock_analyze.research.pipeline.save_model_bundle"),
            ):
                result = pipeline.train_models()
            registry = json.loads(
                (
                    root
                    / "data"
                    / "research"
                    / "models"
                    / "a_share"
                    / "hs300"
                    / "3"
                    / "registry.json"
                ).read_text()
            )
            trials = (
                root
                / "data"
                / "research"
                / "models"
                / "a_share"
                / "hs300"
                / "3"
                / "trials.jsonl"
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

    def test_training_writes_independent_account_scope_registries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
            )
            features = pd.DataFrame([
                self._scoped_feature("000001"),
                {
                    **self._scoped_feature("000002"),
                    "account_id": "zz500",
                    "research_scope": "zz500",
                    "benchmark_code": "000905",
                },
            ])
            labels = pd.DataFrame([
                {
                    "code": row["code"],
                    "trade_date": "20260710",
                    "account_id": row["account_id"],
                    "horizon": 3,
                    "label": "up",
                    "label_contract_version": LABEL_CONTRACT_VERSION,
                }
                for row in features.to_dict(orient="records")
            ])
            pipeline.store.write_feature_snapshot("a_share", "2026-07-10", features)
            pipeline.store.write_label_snapshot("a_share", "2026-07-10", labels)
            trained_scopes = []

            def bundle(dataset, *_args, horizon, account_scope, **_kwargs):
                trained_scopes.append(account_scope)
                self.assertEqual(set(dataset["account_id"].astype(str)), {account_scope})
                return SimpleNamespace(
                    horizon=horizon,
                    account_scope=account_scope,
                    model_version=f"{account_scope}-m{horizon}",
                    metrics=self._passing_gate_metrics(),
                )

            with (
                patch("stock_analyze.research.pipeline.train_model_bundle", side_effect=bundle),
                patch("stock_analyze.research.pipeline.save_model_bundle"),
            ):
                result = pipeline.train_models()

            hs300_registry = json.loads(
                (
                    root
                    / "data/research/models/a_share/hs300/3/registry.json"
                ).read_text(encoding="utf-8")
            )
            zz500_registry = json.loads(
                (
                    root
                    / "data/research/models/a_share/zz500/3/registry.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(sorted(trained_scopes), ["hs300", "zz500"])
        self.assertEqual(
            {item["account_scope"] for item in result["trained"]},
            {"hs300", "zz500"},
        )
        self.assertIn("hs300-m3", hs300_registry["models"])
        self.assertIn("zz500-m3", zz500_registry["models"])

    def test_twelfth_prediction_cycle_does_not_promote_without_realized_forward_nav(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([self._scoped_feature()])
            )
            model_root = (
                root / "data" / "research" / "models" / "a_share" / "hs300" / "5"
            )
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
            model = SimpleNamespace(
                horizon=5,
                account_scope="hs300",
                model_version="m5",
                metrics=self._passing_gate_metrics(),
            )

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
        self.assertEqual(registry["models"]["m5"]["gate_history"], [])

    def test_shadow_cycle_only_evaluates_roles_currently_in_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            model_root = pipeline._model_root(20, "hs300")
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(
                json.dumps({
                    "champion_model_version": None,
                    "models": {
                        "m20": {
                            "status": "shadow",
                            "role_status": {
                                "classifier": "research",
                                "ranker": "shadow",
                                "portfolio": "shadow",
                            },
                            "gate_history": [],
                        }
                    },
                }),
                encoding="utf-8",
            )
            bundle = SimpleNamespace(
                model_version="m20",
                metrics=self._passing_gate_metrics(),
            )

            with (
                patch(
                    "stock_analyze.research.pipeline.load_forward_portfolio_evidence",
                    return_value={
                        "forward_evidence_status": "available",
                        "forward_cycles": 1,
                    },
                ),
                patch(
                    "stock_analyze.research.pipeline.ShadowCycleTracker.record",
                    return_value={"is_new_cycle": True, "count": 1},
                ),
                patch(
                    "stock_analyze.research.pipeline.ShadowCycleTracker.record_usable_count",
                    return_value=1,
                ),
            ):
                result = pipeline._advance_shadow_cycle(
                    20,
                    bundle,
                    10,
                    account_scope="hs300",
                )

            registry = json.loads((model_root / "registry.json").read_text())

        model = registry["models"]["m20"]
        self.assertEqual(result["status"], "shadow")
        self.assertEqual(model["role_status"]["classifier"], "research")
        self.assertEqual(
            [gate["model_role"] for gate in model["gate_history"]],
            ["ranker", "portfolio"],
        )

    def test_shadow_cycle_remaining_uses_realized_forward_weeks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            model_root = pipeline._model_root(20, "hs300")
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(json.dumps({
                "models": {
                    "m20": {
                        "status": "shadow",
                        "role_status": {
                            "ranker": "shadow",
                            "portfolio": "shadow",
                        },
                        "gate_history": [],
                    }
                }
            }), encoding="utf-8")
            bundle = SimpleNamespace(
                model_version="m20",
                metrics=self._passing_gate_metrics(),
            )

            with (
                patch(
                    "stock_analyze.research.pipeline.load_forward_portfolio_evidence",
                    return_value={
                        "forward_evidence_status": "available",
                        "forward_cycles": 5,
                    },
                ),
                patch(
                    "stock_analyze.research.pipeline.ShadowCycleTracker.record",
                    return_value={
                        "is_new_cycle": True,
                        "count": 12,
                        "remaining": 0,
                        "cycles": [],
                    },
                ),
                patch(
                    "stock_analyze.research.pipeline.ShadowCycleTracker.record_usable_count",
                    return_value=5,
                ),
            ):
                result = pipeline._advance_shadow_cycle(
                    20,
                    bundle,
                    10,
                    account_scope="hs300",
                )

        self.assertEqual(result["count"], 5)
        self.assertEqual(result["remaining"], 7)

    def test_admitted_transparent_rule_writes_version_pinned_signal_rows(self):
        from dataclasses import asdict

        from stock_analyze.research.classical_specs import transparent_strategy_specs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-08-14",
                offline=True,
            )
            spec = next(
                item
                for item in transparent_strategy_specs("a_share", "zz500")
                if item.spec_id == "A_MOM_02"
            )
            model_root = pipeline._model_root(20, "zz500")
            artifact_path = model_root / "shadow_candidates/rule-a-mom.json"
            artifact_path.parent.mkdir(parents=True)
            artifact_path.write_text(json.dumps({
                "schema_version": 1,
                "candidate_kind": "transparent_rule",
                "runtime_contract": "transparent-rule-shadow-v1",
                "admission_contract": "evidence-first-shadow-v2",
                "model_version": "rule-a-mom",
                "market": "a_share",
                "account_scope": "zz500",
                "horizon": 20,
                "spec": asdict(spec),
                "portfolio_contract": {
                    "rebalance_frequency": "monthly",
                },
            }), encoding="utf-8")
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "rule-a-mom": {
                        "status": "shadow",
                        "candidate_kind": "transparent_rule",
                        "runtime_contract": "transparent-rule-shadow-v1",
                        "spec_id": spec.spec_id,
                        "spec_hash": spec.spec_hash,
                        "artifact": str(artifact_path),
                        "development_admission": {
                            "contract": "evidence-first-shadow-v2",
                            "active_evidence_passed": True,
                        },
                    }
                },
            }), encoding="utf-8")
            features = pd.DataFrame([
                {
                    "code": "000001",
                    "trade_date": "20260813",
                    "account_id": "zz500",
                    "research_scope": "zz500",
                    "momentum_20": 0.03,
                    "momentum_60": 0.08,
                    "momentum_120": 0.12,
                },
                {
                    "code": "000002",
                    "trade_date": "20260813",
                    "account_id": "zz500",
                    "research_scope": "zz500",
                    "momentum_20": -0.01,
                    "momentum_60": 0.01,
                    "momentum_120": 0.02,
                },
            ])

            with patch.object(
                pipeline,
                "_advance_shadow_cycle",
                return_value={"status": "shadow", "count": 0, "remaining": 12},
            ) as advance:
                result = pipeline._write_iteration_candidate_predictions(
                    20,
                    features,
                    canonical_bundle=None,
                    canonical_records=None,
                    regime="range",
                    regime_stability=1.0,
                    account_scope="zz500",
                )

            signals = pd.read_parquet(result["prediction_path"])

        self.assertEqual(result["model_version"], "rule-a-mom")
        self.assertEqual(result["predictions"], 2)
        self.assertEqual(set(signals["signal_kind"]), {"transparent_rule"})
        self.assertEqual(set(signals["spec_id"]), {"A_MOM_02"})
        self.assertEqual(set(signals["spec_hash"]), {spec.spec_hash})
        self.assertEqual(set(signals["account_id"]), {"zz500"})
        self.assertIn("rule_eligible", signals.columns)
        self.assertIn("target_risky_exposure", signals.columns)
        self.assertNotIn("p_up", signals.columns)
        self.assertFalse(advance.call_args.kwargs["promotion_enabled"])

    def test_rule_iteration_signal_survives_missing_formal_model_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([self._scoped_feature()]),
            )

            def write_rule(*_args, account_scope=None, **_kwargs):
                if account_scope == "hs300":
                    return {
                        "model_version": "rule-a-mom",
                        "status": "shadow",
                        "shadow_cycles": 0,
                        "prediction_path": "rule.parquet",
                        "predictions": 1,
                    }
                return None

            with (
                patch.object(
                    pipeline,
                    "_resolve_model_roles_with_provenance",
                    side_effect=FileNotFoundError("formal-model-missing"),
                ),
                patch.object(
                    pipeline,
                    "_write_iteration_candidate_predictions",
                    side_effect=write_rule,
                ) as write_candidate,
            ):
                result = pipeline.predict(horizon=20)

        self.assertEqual(result["status"], "partial")
        self.assertIn("hs300:20", result["iteration_candidates"])
        self.assertTrue(
            any(call.kwargs.get("account_scope") == "hs300" for call in write_candidate.call_args_list)
        )

    def test_unregistered_formal_model_is_unavailable_not_failed_when_rule_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([self._scoped_feature()]),
            )

            with (
                patch.object(
                    pipeline,
                    "_resolve_model_roles_with_provenance",
                    return_value=(
                        root / "missing.joblib",
                        {
                            "classifier": "research",
                            "ranker": "research",
                            "portfolio": "research",
                        },
                        {
                            "requested_scope": "hs300",
                            "selected_scope": "hs300",
                            "resolution": "missing",
                            "fallback_reason": "registered_model_unavailable",
                        },
                    ),
                ),
                patch.object(
                    pipeline,
                    "_write_iteration_candidate_predictions",
                    return_value={
                        "model_version": "rule-a-mom",
                        "status": "shadow",
                        "shadow_cycles": 0,
                        "prediction_path": "rule.parquet",
                        "predictions": 1,
                    },
                ),
            ):
                result = pipeline.predict(horizon=20)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["unavailable_models"][0]["account_scope"], "hs300")
        self.assertIn("hs300:20", result["iteration_candidates"])

    def test_pinned_iteration_candidate_runs_alongside_existing_champion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(root, market="a_share", agent="codex", as_of="2026-07-10", offline=True)
            pipeline.store.write_feature_snapshot(
                "a_share", "2026-07-10", pd.DataFrame([self._scoped_feature()])
            )
            model_root = (
                root / "data" / "research" / "models" / "a_share" / "hs300" / "5"
            )
            model_root.mkdir(parents=True)
            (model_root / "registry.json").write_text(json.dumps({
                "champion_model_version": "champion",
                "models": {
                    "champion": {"status": "active", "artifact": str(model_root / "champion.joblib"), "gate_history": []},
                    "challenger": {"status": "shadow", "artifact": str(model_root / "challenger.joblib"), "gate_history": []},
                },
            }), encoding="utf-8")
            bundles = {
                "champion.joblib": SimpleNamespace(
                    horizon=5,
                    account_scope="hs300",
                    model_version="champion",
                    metrics=self._passing_gate_metrics(),
                ),
                "challenger.joblib": SimpleNamespace(
                    horizon=5,
                    account_scope="hs300",
                    model_version="challenger",
                    metrics=self._passing_gate_metrics(),
                ),
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
                / "hs300"
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
                    / "hs300"
                    / "5"
                    / "iteration_state.json"
                ).read_text()
            )

        self.assertEqual(main.iloc[0]["model_version"], "champion")
        self.assertEqual(challenger.iloc[0]["model_version"], "challenger")
        self.assertEqual(len(cycles["models"]["challenger"]["cycles"]), 1)
        self.assertEqual(iteration_state["current_candidate"]["model_version"], "challenger")

    def test_market_candidate_prediction_contains_all_account_scopes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([
                    self._scoped_feature("000001"),
                    {
                        **self._scoped_feature("000002"),
                        "account_id": "zz500",
                        "research_scope": "zz500",
                        "benchmark_code": "000905",
                    },
                ]),
            )
            bundles = {}
            for scope in ("hs300", "zz500"):
                model_root = (
                    root / "data" / "research" / "models"
                    / "a_share" / scope / "5"
                )
                model_root.mkdir(parents=True)
                artifact = model_root / f"{scope}.joblib"
                artifact.touch()
                (model_root / "registry.json").write_text(json.dumps({
                    "champion_model_version": f"{scope}-active",
                    "models": {
                        f"{scope}-active": {
                            "status": "active",
                            "artifact": str(artifact),
                        },
                    },
                }), encoding="utf-8")
                bundles[artifact.name] = SimpleNamespace(
                    horizon=5,
                    account_scope=scope,
                    model_version=f"{scope}-active",
                    metrics={},
                )
            market_root = (
                root / "data" / "research" / "models" / "a_share" / "5"
            )
            market_root.mkdir(parents=True)
            market_artifact = market_root / "market-candidate.joblib"
            market_artifact.touch()
            (market_root / "registry.json").write_text(json.dumps({
                "champion_model_version": None,
                "models": {
                    "market-candidate": {
                        "status": "research",
                        "artifact": str(market_artifact),
                        "registered_at": "2026-07-10T00:00:00+00:00",
                    },
                },
            }), encoding="utf-8")
            bundles[market_artifact.name] = SimpleNamespace(
                horizon=5,
                account_scope=None,
                model_version="market-candidate",
                metrics={},
            )
            candidate_batch_sizes = []

            def load(path):
                return bundles[Path(path).name]

            def prediction(bundle, features, *_args, **_kwargs):
                if bundle.model_version == "market-candidate":
                    candidate_batch_sizes.append(len(features))
                return [
                    PredictionRecord(
                        code=str(row["code"]),
                        as_of="2026-07-10",
                        horizon=5,
                        p_up=0.5,
                        p_flat=0.3,
                        p_down=0.2,
                        model_version=bundle.model_version,
                        account_scope=str(bundle.account_scope or ""),
                        metadata={
                            "account_id": str(row["account_id"]),
                            "research_scope": str(row["research_scope"]),
                        },
                    )
                    for _, row in features.iterrows()
                ]

            with (
                patch("stock_analyze.research.pipeline.load_model_bundle", side_effect=load),
                patch("stock_analyze.research.pipeline.generate_predictions", side_effect=prediction),
                patch.object(
                    pipeline,
                    "_assess_model_drift",
                    side_effect=lambda _h, _b, records, **_kwargs: (
                        records,
                        {"status": "current"},
                    ),
                ),
            ):
                result = pipeline.predict(horizon=5)

            candidate = pd.read_parquet(
                root
                / "data" / "research" / "iteration_predictions"
                / "a_share" / "5" / "market-candidate" / "20260710.parquet"
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(candidate_batch_sizes, [2])
        self.assertEqual(set(candidate["research_scope"]), {"hs300", "zz500"})
        self.assertEqual(len(candidate), 2)

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

    def test_online_prepare_rejects_immutable_materialized_snapshot_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            outputs: dict[str, object] = {}
            manifest = raw / "materialization_manifest.json"
            manifest.write_text(
                json.dumps({
                    "schema_version": "a-share-materialization-v1",
                    "status": "complete",
                    "as_of": "20260710",
                    "historical_union_codes": [],
                    "historical_union_count": 0,
                    "outputs": outputs,
                    "output_digest": self._canonical_digest(outputs),
                }),
                encoding="utf-8",
            )
            before = manifest.read_bytes()
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10"
            )

            with self.assertRaisesRegex(
                ValueError, "a_share_materialized_online_prepare_forbidden"
            ):
                pipeline.prepare_data(force=True)

            self.assertEqual(manifest.read_bytes(), before)
            self.assertEqual(list(raw.glob("*.parquet")), [])

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

    def test_materialized_raw_sources_are_manifest_whitelisted_and_hash_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            declared = raw / "daily_basic.parquet"
            pd.DataFrame([{
                "ts_code": "000001.SZ",
                "trade_date": "20260710",
                "pe_ttm": 12.0,
            }]).to_parquet(declared, index=False)
            relative = declared.relative_to(root).as_posix()
            outputs = {
                relative: {
                    "path": relative,
                    "rows": 1,
                    "min_date": "20260710",
                    "max_date": "20260710",
                    "sha256": hashlib.sha256(declared.read_bytes()).hexdigest(),
                }
            }
            (raw / "materialization_manifest.json").write_text(
                json.dumps({
                    "schema_version": "a-share-materialization-v1",
                    "status": "complete",
                    "as_of": "20260710",
                    "historical_union_codes": [],
                    "historical_union_count": 0,
                    "outputs": outputs,
                    "output_digest": self._canonical_digest(outputs),
                }),
                encoding="utf-8",
            )
            pd.DataFrame([{"value": 1}]).to_parquet(
                raw / "undeclared.parquet", index=False
            )
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            with self.assertRaisesRegex(
                ValueError, "a_share_materialization_undeclared_raw_output"
            ):
                pipeline._load_persisted_source_frames()

            (raw / "undeclared.parquet").unlink()
            frames = pipeline._load_persisted_source_frames()
            self.assertEqual(set(frames), {"daily_basic"})

            pd.DataFrame([{
                "ts_code": "000001.SZ",
                "trade_date": "20260710",
                "pe_ttm": 99.0,
            }]).to_parquet(declared, index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )
            with self.assertRaisesRegex(
                ValueError, "a_share_materialized_output_hash_mismatch"
            ):
                pipeline._load_persisted_source_frames()

    def test_materialization_manifest_rejects_populated_statement_cache_missing_from_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            declared = raw / "fina_indicator.parquet"
            pd.DataFrame([{
                "ts_code": "000001.SZ",
                "ann_date": "20260425",
                "end_date": "20260331",
                "roe": 10.0,
            }]).to_parquet(declared, index=False)
            relative = declared.relative_to(root).as_posix()
            outputs = {
                relative: {
                    "path": relative,
                    "rows": 1,
                    "min_date": "20260425",
                    "max_date": "20260425",
                    "sha256": hashlib.sha256(declared.read_bytes()).hexdigest(),
                }
            }
            (raw / "materialization_manifest.json").write_text(
                json.dumps({
                    "schema_version": "a-share-materialization-v1",
                    "status": "complete",
                    "as_of": "20260710",
                    "historical_union_codes": ["000001"],
                    "historical_union_count": 1,
                    "outputs": outputs,
                    "output_digest": self._canonical_digest(outputs),
                }),
                encoding="utf-8",
            )
            cache = root / "data" / "shared" / "backtest_cache"
            for endpoint in ("income", "balancesheet", "cashflow"):
                endpoint_root = cache / endpoint
                endpoint_root.mkdir(parents=True)
                pd.DataFrame([{
                    "ts_code": "000001.SZ",
                    "ann_date": "20260425",
                    "end_date": "20260331",
                }]).to_csv(endpoint_root / "000001.SZ.csv", index=False)
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            with self.assertRaisesRegex(
                ValueError,
                "a_share_materialization_stale:missing_endpoint=balancesheet,cashflow,income",
            ):
                pipeline._a_share_materialization_manifest()

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

    def test_materialized_historical_union_is_not_truncated_by_sample_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            union_codes = [f"{index:06d}" for index in range(20)]
            outputs: dict[str, object] = {}
            (raw / "materialization_manifest.json").write_text(
                json.dumps({
                    "schema_version": "a-share-materialization-v1",
                    "status": "complete",
                    "as_of": "20260710",
                    "historical_union_count": len(union_codes),
                    "historical_union_codes": union_codes,
                    "outputs": outputs,
                    "output_digest": self._canonical_digest(outputs),
                }),
                encoding="utf-8",
            )
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
                max_full_history_instruments=5,
            )

            selected = pipeline._full_history_codes(union_codes)

        self.assertEqual(selected, set(union_codes))

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

    def test_a_share_materialization_marker_blocks_history_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            (raw / ".materialization_in_progress").write_text("running\n")
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10"
            )

            with self.assertRaisesRegex(
                ValueError, "a_share_materialization_in_progress"
            ):
                pipeline._history_files()

    def test_unified_arena_requires_a_share_materialization_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-07-10",
                offline=True,
            )
            pipeline.store.write_feature_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([self._scoped_feature()]),
            )
            pipeline.store.write_label_snapshot(
                "a_share",
                "2026-07-10",
                pd.DataFrame([{
                    "code": "000001",
                    "trade_date": "20260710",
                    "horizon": 20,
                    "label": "up",
                }]),
            )

            with self.assertRaisesRegex(
                ValueError,
                "unified_arena_a_share_materialization_required:20260710",
            ):
                pipeline.run_unified_model_arena()

    def test_a_share_history_files_follow_materialization_manifest_whitelist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "shared" / "cache"
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            cache.mkdir(parents=True)
            raw.mkdir(parents=True)
            declared = cache / "history_000001_20260710_2.csv"
            rogue = cache / "history_000002_20260710_2.csv"
            declared.write_text("code,trade_date\n000001,20260709\n")
            rogue.write_text("code,trade_date\n000002,20260709\n")
            relative = declared.relative_to(root).as_posix()
            outputs = {
                relative: {
                    "path": relative,
                    "rows": 1,
                    "min_date": "20260709",
                    "max_date": "20260709",
                    "sha256": hashlib.sha256(declared.read_bytes()).hexdigest(),
                }
            }
            (raw / "materialization_manifest.json").write_text(
                json.dumps({
                    "schema_version": "a-share-materialization-v1",
                    "status": "complete",
                    "as_of": "20260710",
                    "historical_union_codes": ["000001"],
                    "historical_union_count": 1,
                    "outputs": outputs,
                    "output_digest": self._canonical_digest(outputs),
                }),
                encoding="utf-8",
            )
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10"
            )

            selected = pipeline._history_files()
            declared.write_text("code,trade_date\n000001,20260708\n")
            with self.assertRaisesRegex(
                ValueError, "a_share_materialized_history_hash_mismatch"
            ):
                pipeline._history_files()

        self.assertEqual(selected, [declared])

    def test_materialized_prepare_fails_closed_without_declared_index_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_history(root, rows=80, code="000001")
            history = next(
                (root / "data" / "shared" / "cache").glob(
                    "history_000001_20260710_*.csv"
                )
            )
            normalized = pd.read_csv(history).rename(columns={
                "日期": "trade_date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
            })
            normalized.to_csv(history, index=False)
            dates = normalized["trade_date"].astype(str).str.replace("-", "", regex=False)
            relative = history.relative_to(root).as_posix()
            outputs = {
                relative: {
                    "path": relative,
                    "rows": 80,
                    "min_date": str(dates.min()),
                    "max_date": str(dates.max()),
                    "sha256": hashlib.sha256(history.read_bytes()).hexdigest(),
                }
            }
            raw = root / "data" / "research" / "raw" / "a_share" / "20260710"
            raw.mkdir(parents=True)
            (raw / "materialization_manifest.json").write_text(
                json.dumps({
                    "schema_version": "a-share-materialization-v1",
                    "status": "complete",
                    "as_of": "20260710",
                    "historical_union_codes": ["000001"],
                    "historical_union_count": 1,
                    "outputs": outputs,
                    "output_digest": self._canonical_digest(outputs),
                }),
                encoding="utf-8",
            )
            pipeline = ResearchPipeline(
                root, market="a_share", agent="codex", as_of="2026-07-10", offline=True
            )

            with self.assertRaisesRegex(
                ValueError, "a_share_materialized_universe_unavailable"
            ):
                pipeline.prepare_data(force=True)

    def test_default_a_share_keeps_full_history_for_every_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ResearchPipeline(Path(tmp), market="a_share", agent="codex")
            selected = pipeline._full_history_codes([f"{index:06d}" for index in range(100)])
        self.assertEqual(len(selected), 100)


if __name__ == "__main__":
    unittest.main()
