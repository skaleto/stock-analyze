import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.full_history_rebuild import (
    audit_full_history_dataset,
    retire_legacy_rebuild_shadows,
)


class FullHistoryRebuildAuditTest(unittest.TestCase):
    def _features(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": pd.Series(["000001", "000002"], dtype="string"),
                "trade_date": pd.Series(["20180102", "20180102"], dtype="string"),
                "list_date": pd.Series(["19910403", "20170101"], dtype="string"),
                "feature_observed_at": pd.Series(["20260814", "20260814"], dtype="string"),
                "fundamental_available_date": pd.Series(["20180102", "20180102"], dtype="string"),
                "benchmark_code": pd.Series(["000300", "000300"], dtype="string"),
                "research_scope": ["hs300", "hs300"],
                "momentum_20": [0.1, 0.2],
            }
        )

    def test_accepts_complete_point_in_time_frame(self) -> None:
        result = audit_full_history_dataset(self._features(), market="a_share", required_start="20180101", required_end="20180102")
        self.assertTrue(result["passed"])
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["instruments"], 2)

    def test_rejects_duplicate_keys(self) -> None:
        frame = pd.concat([self._features(), self._features().iloc[[0]]], ignore_index=True)
        result = audit_full_history_dataset(frame, market="a_share", required_start="20180101", required_end="20180102")
        self.assertFalse(result["passed"])
        self.assertIn("duplicate_keys", result["reasons"])

    def test_rejects_prelisting_and_future_observation(self) -> None:
        frame = self._features()
        frame.loc[1, "list_date"] = "20190101"
        frame.loc[0, "fundamental_available_date"] = "20180103"
        result = audit_full_history_dataset(frame, market="a_share", required_start="20180101", required_end="20180102")
        self.assertIn("prelisting_rows", result["reasons"])
        self.assertIn("future_observation_rows", result["reasons"])

    def test_rejects_date_shortfall_and_missing_benchmark(self) -> None:
        frame = self._features()
        frame["benchmark_code"] = pd.NA
        result = audit_full_history_dataset(frame, market="a_share", required_start="20170101", required_end="20190101")
        self.assertIn("start_date_shortfall", result["reasons"])
        self.assertIn("end_date_shortfall", result["reasons"])
        self.assertIn("benchmark_coverage", result["reasons"])

    def test_rejects_non_textual_codes(self) -> None:
        frame = self._features()
        frame["code"] = [1, 2]
        result = audit_full_history_dataset(frame, market="a_share", required_start="20180101", required_end="20180102")
        self.assertIn("code_dtype", result["reasons"])

    def test_retirement_is_scoped_read_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "data/research/models/a_share/hs300/20/registry.json"
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps({
                "champion_model_version": "active-v1",
                "formal_strategy_activated": True,
                "models": {
                    "active-v1": {"status": "active", "candidate_kind": "transparent_rule"},
                    "legacy-rule": {"status": "shadow", "candidate_kind": "transparent_rule", "role_status": {"ranker": "shadow", "portfolio": "shadow"}},
                    "ml-shadow": {"status": "shadow", "candidate_kind": "classical"},
                },
            }), encoding="utf-8")

            preview = retire_legacy_rebuild_shadows(root, apply=False)
            unchanged = json.loads(registry_path.read_text(encoding="utf-8"))
            first = retire_legacy_rebuild_shadows(root, apply=True)
            second = retire_legacy_rebuild_shadows(root, apply=True)
            changed = json.loads(registry_path.read_text(encoding="utf-8"))

        self.assertEqual(preview["flagged"], 1)
        self.assertEqual(unchanged["models"]["legacy-rule"]["status"], "shadow")
        self.assertEqual(first["changed"], 1)
        self.assertEqual(second["changed"], 0)
        self.assertEqual(changed["models"]["legacy-rule"]["status"], "retired")
        self.assertEqual(changed["models"]["ml-shadow"]["status"], "shadow")
        self.assertEqual(changed["models"]["active-v1"]["status"], "active")
        self.assertEqual(changed["champion_model_version"], "active-v1")
        self.assertTrue(changed["formal_strategy_activated"])


if __name__ == "__main__":
    unittest.main()


class FullHistoryRebuildCliTest(unittest.TestCase):
    def test_parser_accepts_audit_and_retirement_commands(self) -> None:
        from stock_analyze.cli import build_parser

        audit = build_parser().parse_args([
            "audit-full-history-rebuild-data",
            "--market", "a_share",
            "--snapshot", "/tmp/features.parquet",
            "--required-start", "20180101",
            "--required-end", "20260814",
        ])
        retire = build_parser().parse_args([
            "retire-full-history-legacy-shadows",
            "--repo-root", "/tmp/repo",
            "--apply",
        ])
        self.assertEqual(audit.command, "audit-full-history-rebuild-data")
        self.assertEqual(retire.command, "retire-full-history-legacy-shadows")
        self.assertTrue(retire.apply)


class FullHistoryDatasetLoaderTest(unittest.TestCase):
    def test_loader_joins_one_scope_and_horizon_without_cross_scope_rows(self) -> None:
        from stock_analyze.research.full_history_rebuild import load_scope_dataset

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features = pd.DataFrame({
                "code": ["000001", "000002"], "trade_date": ["20200102", "20200102"],
                "research_scope": ["hs300", "zz500"], "close": [10.0, 20.0],
                "momentum_20": [0.1, 0.2], "momentum_60": [0.2, 0.3],
                "realized_volatility_20": [0.1, 0.2], "natr_14": [0.1, 0.2],
            })
            labels = pd.DataFrame({
                "code": ["000001", "000002"], "trade_date": ["20200102", "20200102"],
                "account_id": ["hs300", "zz500"], "research_scope": ["hs300", "zz500"],
                "horizon": [20, 20], "entry_date": ["20200103", "20200103"],
                "entry_price": [10.1, 20.1], "benchmark_entry_price": [100.0, 100.0],
                "label_end_date": ["20200203", "20200203"], "excess_return": [0.01, -0.01],
                "label_contract_version": [
                    "next-open-v3-adjusted", "next-open-v3-adjusted",
                ],
            })
            feature_path = root / "features.parquet"
            label_path = root / "labels.parquet"
            features.to_parquet(feature_path, index=False)
            labels.to_parquet(label_path, index=False)
            result, allowed = load_scope_dataset(feature_path, label_path, market="a_share", scope="hs300", horizon=20)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["account_id"], "hs300")
        self.assertIn("momentum_20", allowed)

    def test_loader_rejects_stale_label_contract(self) -> None:
        from stock_analyze.research.full_history_rebuild import load_scope_dataset

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features = pd.DataFrame({
                "code": ["000001"], "trade_date": ["20200102"],
                "research_scope": ["hs300"], "close": [10.0],
                "momentum_20": [0.1],
            })
            labels = pd.DataFrame({
                "code": ["000001"], "trade_date": ["20200102"],
                "account_id": ["hs300"], "research_scope": ["hs300"],
                "horizon": [20], "label_end_date": ["20200203"],
                "excess_return": [0.01],
                "label_contract_version": ["next-open-v2"],
            })
            feature_path = root / "features.parquet"
            label_path = root / "labels.parquet"
            features.to_parquet(feature_path, index=False)
            labels.to_parquet(label_path, index=False)

            with self.assertRaisesRegex(
                ValueError, "full_history_label_contract_invalid",
            ):
                load_scope_dataset(
                    feature_path,
                    label_path,
                    market="a_share",
                    scope="hs300",
                    horizon=20,
                )


class FullHistoryRebuildRunCliTest(unittest.TestCase):
    def test_parser_accepts_full_rebuild_runner(self) -> None:
        from stock_analyze.cli import build_parser

        args = build_parser().parse_args([
            "run-full-history-model-rebuild",
            "--repo-root", "/tmp/repo",
            "--snapshot-date", "20260814",
            "--scopes", "hs300", "zz500",
        ])
        self.assertEqual(args.command, "run-full-history-model-rebuild")
        self.assertEqual(args.snapshot_date, "20260814")
        self.assertEqual(args.scopes, ["hs300", "zz500"])
