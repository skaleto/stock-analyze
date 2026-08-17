import json
import tempfile
import unittest
from pathlib import Path

from stock_analyze.research.full_history_windows import (
    build_full_history_windows,
    load_full_history_config,
    open_historical_test_once,
    seal_full_history_manifest,
)


class FullHistoryWindowsTest(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "protocol": "full-history-rebuild-v1",
            "source_start": "20180101",
            "development_end": "20241231",
            "historical_test_start": "20250101",
            "outer_folds": [
                {"train_start": "20180101", "train_end": "20201231", "validation_start": "20210101", "validation_end": "20211231"},
                {"train_start": "20180101", "train_end": "20211231", "validation_start": "20220101", "validation_end": "20221231"},
                {"train_start": "20180101", "train_end": "20221231", "validation_start": "20230101", "validation_end": "20231231"},
                {"train_start": "20180101", "train_end": "20231231", "validation_start": "20240101", "validation_end": "20241231"},
            ],
            "inner_splits": 3,
            "scopes": {
                "hs300": {"market": "a_share", "horizon": 20, "max_features": 12},
                "us_exposure": {"market": "cn_qdii_etf", "horizon": 10, "max_features": 8},
            },
            "minimum_feature_coverage": 0.70,
            "minimum_feature_stability": 0.75,
            "maximum_variants_per_family": 3,
            "candidates": {
                "a_share": {"elastic_net": [{"alpha": 0.1}]},
                "cn_qdii_etf": {"additive": [{"decay": 0.98}]},
            },
        }

    def test_load_rejects_overlapping_test_and_development(self) -> None:
        config = self._config()
        config["historical_test_start"] = "20241231"
        with self.assertRaisesRegex(ValueError, "full_history_test_overlap"):
            load_full_history_config(config)

    def test_load_rejects_more_than_three_variants(self) -> None:
        config = self._config()
        config["candidates"]["a_share"]["elastic_net"] = [{}, {}, {}, {}]
        with self.assertRaisesRegex(ValueError, "full_history_candidate_variants"):
            load_full_history_config(config)

    def test_builds_four_purged_outer_folds(self) -> None:
        contract = load_full_history_config(self._config())
        dates = [f"{year}0630" for year in range(2018, 2026)]
        label_end_dates = [f"{year}0715" for year in range(2018, 2026)]
        windows = build_full_history_windows(
            dates,
            label_end_dates,
            contract=contract,
            scope="hs300",
        )
        self.assertEqual(len(windows), 4)
        for fold in windows:
            self.assertTrue(set(fold.train_dates).isdisjoint(fold.validation_dates))
            self.assertTrue(all(day < fold.validation_start for day in fold.train_label_end_dates))
            self.assertGreaterEqual(fold.embargo_sessions, 20)
            self.assertEqual(len(fold.inner_folds), 3)
            for inner in fold.inner_folds:
                self.assertTrue(set(inner.validation_dates).isdisjoint(fold.validation_dates))

    def test_historical_test_manifest_opens_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            payload = {
                "protocol": "full-history-rebuild-v1",
                "scope": "hs300",
                "development_end": "20241231",
                "historical_test_start": "20250101",
                "historical_test_end": "20260814",
                "data_fingerprint": "abc123",
            }
            sealed = seal_full_history_manifest(path, payload)
            opened = open_historical_test_once(path, sealed["declaration_id"])
            repeated = open_historical_test_once(path, sealed["declaration_id"])
            self.assertEqual(opened, repeated)
            self.assertEqual(opened["historical_test_open_count"], 1)
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["historical_test_open_count"], 1)
            with self.assertRaisesRegex(ValueError, "full_history_manifest_declaration"):
                open_historical_test_once(path, "wrong")


if __name__ == "__main__":
    unittest.main()
