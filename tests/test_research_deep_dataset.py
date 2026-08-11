import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from stock_analyze.research.deep.dataset import prepare_tabular_dataset


class DeepDatasetTest(unittest.TestCase):
    @staticmethod
    def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
        dates = pd.bdate_range("2024-01-02", periods=60)
        feature_rows = []
        label_rows = []
        for date_index, date in enumerate(dates):
            for code_index, code in enumerate(("000001", "000002", "000003", "000004")):
                feature_rows.append(
                    {
                        "code": code,
                        "trade_date": date.strftime("%Y%m%d"),
                        "open": 10.0 + date_index,
                        "dense_signal": date_index * 0.1 + code_index,
                        "train_shift_probe": float(date_index % 2) if date_index < 40 else 10.0,
                        "constant_signal": 1.0,
                        "sparse_event": 2.0 if date_index == 3 and code_index == 0 else 0.0,
                        "event_net_strength_5d": float((date_index + code_index) % 5),
                        "low_coverage": date_index if date_index % 5 == 0 else np.nan,
                    }
                )
                end_index = date_index + 5
                if end_index >= len(dates):
                    continue
                excess_return = 0.01 * ((code_index % 3) - 1) + 0.0001 * date_index
                label_rows.append(
                    {
                        "code": code,
                        "trade_date": date.strftime("%Y%m%d"),
                        "horizon": 5,
                        "label_end_date": dates[end_index].strftime("%Y%m%d"),
                        "label": ("down", "flat", "up")[code_index % 3],
                        "excess_return": excess_return,
                    }
                )
        return pd.DataFrame(feature_rows), pd.DataFrame(label_rows)

    def test_cleaning_drops_unusable_features_and_is_deterministic(self):
        features, labels = self._frames()

        first = prepare_tabular_dataset(
            features,
            labels,
            horizon=5,
            min_coverage=0.55,
            min_nonzero_rows=8,
            min_nonzero_ratio=0.02,
        )
        second = prepare_tabular_dataset(
            features,
            labels,
            horizon=5,
            min_coverage=0.55,
            min_nonzero_rows=8,
            min_nonzero_ratio=0.02,
        )

        self.assertEqual(first.feature_columns, second.feature_columns)
        self.assertEqual(first.feature_columns, ("dense_signal", "train_shift_probe"))
        self.assertEqual(first.audit["dropped"]["constant_signal"], "constant")
        self.assertEqual(first.audit["dropped"]["sparse_event"], "insufficient_nonzero_support")
        self.assertEqual(first.audit["dropped"]["low_coverage"], "low_coverage")
        self.assertEqual(
            first.audit["dropped"]["event_net_strength_5d"],
            "intelligence_lifecycle_not_promoted",
        )
        np.testing.assert_allclose(first.train.x, second.train.x)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "qualified.json"
            report.write_text("{}", encoding="utf-8")
            config_root = root / "configs"
            config_root.mkdir()
            lifecycle = config_root / "intelligence_factors.json"
            lifecycle.write_text(
                json.dumps(
                    {
                        "factors": {
                            "event_net_strength_5d": {
                                "state": "model_iteration",
                                "evidence": {
                                    "status": "qualified",
                                    "report_path": "qualified.json",
                                    "report_hash": hashlib.sha256(report.read_bytes()).hexdigest(),
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            promoted = prepare_tabular_dataset(
                features,
                labels,
                horizon=5,
                min_coverage=0.55,
                min_nonzero_rows=8,
                min_nonzero_ratio=0.02,
                intelligence_lifecycle_path=lifecycle,
            )
            self.assertIn("event_net_strength_5d", promoted.feature_columns)
            self.assertEqual(
                promoted.audit["intelligence_lifecycle"]["config_hash"],
                hashlib.sha256(lifecycle.read_bytes()).hexdigest(),
            )

        bounded = prepare_tabular_dataset(
            features,
            labels,
            horizon=5,
            min_nonzero_rows=8,
            min_nonzero_ratio=0.02,
            max_features=1,
        )
        self.assertEqual(len(bounded.feature_columns), 1)
        self.assertEqual(bounded.audit["feature_selection"]["selected_count"], 1)

    def test_split_is_point_in_time_safe_and_transform_is_fit_on_train(self):
        features, labels = self._frames()
        prepared = prepare_tabular_dataset(
            features,
            labels,
            horizon=5,
            min_nonzero_rows=8,
            min_nonzero_ratio=0.02,
        )

        self.assertLess(
            prepared.train.metadata["label_end_date"].max(),
            prepared.calibration.metadata["trade_date"].min(),
        )
        self.assertLess(
            prepared.calibration.metadata["label_end_date"].max(),
            prepared.validation.metadata["trade_date"].min(),
        )
        self.assertEqual(prepared.transform.fit_end_date, prepared.train.metadata["trade_date"].max())

        shift_index = prepared.feature_columns.index("train_shift_probe")
        self.assertTrue(np.isfinite(prepared.validation.x[:, shift_index]).all())
        self.assertGreater(float(prepared.validation.x[:, shift_index].mean()), -10.0)

    def test_rejects_unknown_labels_and_duplicate_keys(self):
        features, labels = self._frames()
        invalid = labels.copy()
        invalid.loc[invalid.index[0], "label"] = "strong_up"
        with self.assertRaisesRegex(ValueError, "deep_dataset_label"):
            prepare_tabular_dataset(features, invalid, horizon=5)

        duplicate_features = pd.concat([features, features.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "deep_dataset_duplicate_feature_key"):
            prepare_tabular_dataset(duplicate_features, labels, horizon=5)


if __name__ == "__main__":
    unittest.main()
