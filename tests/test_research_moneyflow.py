import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

from stock_analyze.research.moneyflow import (
    attach_moneyflow_point_in_time_features,
    backfill_moneyflow_history,
    load_moneyflow_cache,
)
from stock_analyze.research.account_features import (
    alpha158_lite_feature_columns,
    build_alpha158_lite_feature_view,
)
from stock_analyze.research.feature_registry import DEFAULT_REGISTRY
from stock_analyze.research.pipeline import ResearchPipeline
from stock_analyze.research.tabular_ranker import load_tabular_ranker_config


class _FakePro:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def moneyflow(self, *, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        self.calls.append(ts_code)
        return pd.DataFrame([
            {
                "ts_code": ts_code,
                "trade_date": "20260102",
                "buy_lg_amount": 12.0,
                "buy_elg_amount": 8.0,
                "sell_lg_amount": 4.0,
                "sell_elg_amount": 3.0,
                "net_mf_amount": 13.0,
            },
            {
                "ts_code": ts_code,
                "trade_date": "20260105",
                "buy_lg_amount": 10.0,
                "buy_elg_amount": 6.0,
                "sell_lg_amount": 7.0,
                "sell_elg_amount": 5.0,
                "net_mf_amount": 4.0,
            },
        ])


class ResearchMoneyflowTest(unittest.TestCase):
    def test_backfill_is_atomic_and_resumes_completed_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _FakePro()
            first = backfill_moneyflow_history(
                tmp,
                codes=["000009", "600000.SH"],
                start_date="20260102",
                end_date="20260105",
                pro=client,
                max_workers=2,
                requests_per_minute=0,
            )
            second = backfill_moneyflow_history(
                tmp,
                codes=["000009", "600000.SH"],
                start_date="20260102",
                end_date="20260105",
                pro=client,
                max_workers=2,
                requests_per_minute=0,
            )
            root = Path(tmp) / "data" / "shared" / "backtest_cache" / "moneyflow"
            manifest = json.loads((root / "manifest.json").read_text())
            files = sorted(root.glob("[0-9]*.parquet"))

        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["completed_codes"], 2)
        self.assertEqual(first["rows"], 4)
        self.assertEqual(second["status"], "cached")
        self.assertEqual(sorted(client.calls), ["000009.SZ", "600000.SH"])
        self.assertEqual(len(files), 2)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["start_date"], "20260102")
        self.assertEqual(manifest["end_date"], "20260105")

    def test_compact_model_cache_supports_filtered_reads_without_per_code_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            backfill_moneyflow_history(
                tmp,
                codes=["000009", "600000.SH"],
                start_date="20260102",
                end_date="20260105",
                pro=_FakePro(),
                requests_per_minute=0,
            )
            root = Path(tmp) / "data" / "shared" / "backtest_cache" / "moneyflow"
            for path in root.glob("[0-9]*.parquet"):
                path.unlink()

            selected = load_moneyflow_cache(
                tmp,
                codes=["600000"],
                start_date="20260105",
                end_date="20260105",
            )

        self.assertEqual(selected["ts_code"].astype(str).unique().tolist(), ["600000.SH"])
        self.assertEqual(selected["trade_date"].astype(str).tolist(), ["20260105"])

    def test_backfill_does_not_reuse_completed_files_for_a_different_date_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _FakePro()
            backfill_moneyflow_history(
                tmp,
                codes=["000009"],
                start_date="20260102",
                end_date="20260105",
                pro=client,
                requests_per_minute=0,
            )
            backfill_moneyflow_history(
                tmp,
                codes=["000009"],
                start_date="20260102",
                end_date="20260106",
                pro=client,
                requests_per_minute=0,
            )

        self.assertEqual(client.calls, ["000009.SZ", "000009.SZ"])

    def test_point_in_time_features_are_exact_date_and_rolling_scale_free(self):
        dates = pd.date_range("2026-01-02", periods=5, freq="B")
        prices = pd.DataFrame({
            "code": ["000009"] * 5,
            "trade_date": dates.strftime("%Y%m%d"),
            "amount_yuan": [1_000_000.0] * 5,
        })
        moneyflow = pd.DataFrame({
            "ts_code": ["000009.SZ"] * 6,
            "trade_date": [*dates.strftime("%Y%m%d"), "20260112"],
            "net_mf_amount": [10.0, -5.0, 20.0, 0.0, 5.0, 1_000.0],
            "buy_lg_amount": [8.0, 2.0, 10.0, 4.0, 5.0, 1_000.0],
            "buy_elg_amount": [4.0, 1.0, 5.0, 2.0, 3.0, 1_000.0],
            "sell_lg_amount": [2.0, 5.0, 2.0, 3.0, 1.0, 0.0],
            "sell_elg_amount": [1.0, 3.0, 1.0, 2.0, 1.0, 0.0],
        })

        featured = attach_moneyflow_point_in_time_features(prices, moneyflow)
        last = featured.iloc[-1]

        self.assertAlmostEqual(float(featured.iloc[0]["moneyflow_net_ratio_1"]), 0.10)
        self.assertAlmostEqual(float(last["moneyflow_net_ratio_5"]), 0.06)
        self.assertAlmostEqual(float(last["moneyflow_positive_days_5"]), 0.60)
        self.assertEqual(int(last["moneyflow_observed"]), 1)
        self.assertLess(float(last["moneyflow_large_imbalance_5"]), 1.0)
        self.assertNotIn("20260112", set(featured["trade_date"].astype(str)))

    def test_moneyflow_does_not_carry_forward_across_missing_trade_date(self):
        prices = pd.DataFrame([
            {"code": "000009", "trade_date": "20260102", "amount_yuan": 1_000_000.0},
            {"code": "000009", "trade_date": "20260105", "amount_yuan": 1_000_000.0},
        ])
        moneyflow = pd.DataFrame([
            {"ts_code": "000009.SZ", "trade_date": "20260102", "net_mf_amount": 10.0},
        ])

        featured = attach_moneyflow_point_in_time_features(prices, moneyflow)

        self.assertEqual(featured["moneyflow_observed"].tolist(), [1, 0])
        self.assertTrue(pd.isna(featured.iloc[1]["moneyflow_net_ratio_1"]))

    def test_moneyflow_features_require_explicit_v2_model_contract(self):
        frame = pd.DataFrame([
            {
                "code": "000009",
                "trade_date": "20260102",
                "research_scope": "zz500",
                "moneyflow_net_ratio_5": 0.10,
                "moneyflow_net_ratio_20": 0.05,
                "moneyflow_positive_days_5": 0.80,
                "moneyflow_large_imbalance_5": 0.20,
            },
            {
                "code": "600000",
                "trade_date": "20260102",
                "research_scope": "zz500",
                "moneyflow_net_ratio_5": -0.10,
                "moneyflow_net_ratio_20": -0.05,
                "moneyflow_positive_days_5": 0.20,
                "moneyflow_large_imbalance_5": -0.20,
            },
        ])
        featured = build_alpha158_lite_feature_view(
            frame,
            account_scope="zz500",
        )

        v1 = alpha158_lite_feature_columns(featured)
        v2 = alpha158_lite_feature_columns(
            featured,
            feature_set="alpha158_lite_moneyflow_v2",
        )

        self.assertNotIn("moneyflow_net_ratio_20_cs_rank", v1)
        self.assertIn("moneyflow_net_ratio_20_cs_rank", v2)
        self.assertIn("moneyflow_net_ratio_20_missing", v2)

    def test_moneyflow_feature_lineage_is_registered(self):
        definitions = {item.name: item for item in DEFAULT_REGISTRY}

        self.assertEqual(
            definitions["moneyflow_net_ratio_20"].source,
            "tushare_moneyflow",
        )
        self.assertEqual(
            definitions["moneyflow_net_ratio_20"].availability_lag,
            0,
        )

    def test_a_share_feature_batches_attach_historical_moneyflow_by_exact_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "shared" / "backtest_cache" / "moneyflow"
            cache.mkdir(parents=True)
            pd.DataFrame([
                {
                    "ts_code": "000009.SZ",
                    "trade_date": "20260102",
                    "net_mf_amount": 10.0,
                    "buy_lg_amount": 8.0,
                    "buy_elg_amount": 4.0,
                    "sell_lg_amount": 2.0,
                    "sell_elg_amount": 1.0,
                },
            ]).to_parquet(cache / "000009.SZ.parquet", index=False)
            pipeline = ResearchPipeline(
                root,
                market="a_share",
                agent="codex",
                as_of="2026-01-05",
                offline=True,
            )
            features = pd.DataFrame([
                {
                    "code": "000009",
                    "trade_date": "20260102",
                    "close": 10.0,
                    "amount_yuan": 1_000_000.0,
                },
                {
                    "code": "000009",
                    "trade_date": "20260105",
                    "close": 10.1,
                    "amount_yuan": 1_000_000.0,
                },
            ])
            destination = root / "batches"

            count = pipeline._write_a_share_enriched_feature_batches(
                features,
                {},
                destination,
                batch_size=1,
            )
            result = pd.read_parquet(destination).sort_values("trade_date")

        self.assertEqual(count, 1)
        self.assertEqual(result["moneyflow_observed"].tolist(), [1, 0])
        self.assertAlmostEqual(float(result.iloc[0]["moneyflow_net_ratio_1"]), 0.10)
        self.assertTrue(pd.isna(result.iloc[1]["moneyflow_net_ratio_1"]))

    def test_tabular_config_requires_an_explicit_supported_feature_set(self):
        base = yaml.safe_load(
            Path("configs/research/classical_model.yaml").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            accepted = Path(tmp) / "accepted.yaml"
            rejected = Path(tmp) / "rejected.yaml"
            base["feature_set"] = "alpha158_lite_moneyflow_v2"
            base["training"]["minimum_moneyflow_coverage"] = 0.80
            accepted.write_text(yaml.safe_dump(base), encoding="utf-8")
            load_tabular_ranker_config(accepted)
            base["feature_set"] = "unknown"
            rejected.write_text(yaml.safe_dump(base), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "tabular_config_feature_set"):
                load_tabular_ranker_config(rejected)


if __name__ == "__main__":
    unittest.main()
