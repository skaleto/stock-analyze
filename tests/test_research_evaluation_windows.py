import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.evaluation_windows import (
    build_account_windows,
    open_final_gate,
    seal_evaluation_manifest,
)


class ResearchEvaluationWindowsTest(unittest.TestCase):
    @staticmethod
    def _rows() -> pd.DataFrame:
        dates = pd.date_range("2025-01-02", periods=160, freq="B")
        return pd.DataFrame({
            "trade_date": dates.strftime("%Y%m%d"),
            "label_end_date": (dates + pd.offsets.BDay(3)).strftime("%Y%m%d"),
            "account_id": "hs300",
        })

    def test_purged_windows_do_not_overlap_future_labels(self):
        windows = build_account_windows(
            self._rows(),
            account_scope="hs300",
            horizon=3,
            n_splits=4,
        )

        self.assertEqual(len(windows.folds), 4)
        for fold in windows.folds:
            self.assertLess(fold.train_label_end, fold.validation_start)

    def test_manifest_is_idempotent_and_rejects_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            payload = {
                "market": "a_share",
                "account_scope": "hs300",
                "horizon": 3,
                "spec_hashes": ["a1", "a2"],
                "data_fingerprint": "data-v1",
            }

            first = seal_evaluation_manifest(path, payload)
            second = seal_evaluation_manifest(path, payload)
            with self.assertRaisesRegex(ValueError, "sealed_manifest_mismatch"):
                seal_evaluation_manifest(
                    path,
                    {**payload, "spec_hashes": ["a1", "a2", "a3"]},
                )

        self.assertEqual(first["declaration_id"], second["declaration_id"])
        self.assertEqual(first["final_gate_open_count"], 0)

    def test_final_gate_opens_once_per_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            manifest = seal_evaluation_manifest(
                path,
                {
                    "market": "a_share",
                    "account_scope": "hs300",
                    "horizon": 3,
                    "spec_hashes": ["a1"],
                    "data_fingerprint": "data-v1",
                },
            )

            opened = open_final_gate(path, manifest["declaration_id"])
            with self.assertRaisesRegex(ValueError, "final_gate_already_opened"):
                open_final_gate(path, manifest["declaration_id"])
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(opened["final_gate_open_count"], 1)
        self.assertEqual(persisted["final_gate_open_count"], 1)


if __name__ == "__main__":
    unittest.main()
