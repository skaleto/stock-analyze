import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from stock_analyze.workflow_notifications import build_workflow_summary, collect_prediction_notifications


class PredictionNotificationTest(unittest.TestCase):
    def test_daily_includes_only_new_material_high_confidence_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "a_share" / "codex" / "predictions"
            path.mkdir(parents=True)
            pd.DataFrame([
                {"code": "000001", "horizon": 5, "p_up": 0.75, "p_down": 0.10, "confidence": 0.82, "model_version": "m1", "active_status": "inactive"},
                {"code": "000002", "horizon": 5, "p_up": 0.10, "p_down": 0.68, "confidence": 0.78, "model_version": "m1", "active_status": "active"},
                {"code": "000003", "horizon": 5, "p_up": 0.80, "p_down": 0.05, "confidence": 0.69, "model_version": "m1", "active_status": "inactive"},
            ]).to_parquet(path / "20260713.parquet", index=False)

            ids, lines = collect_prediction_notifications(root, "2026-07-13")
            _, repeated = collect_prediction_notifications(root, "2026-07-13", seen_alert_ids=set(ids))

        self.assertEqual(len(lines), 2)
        self.assertTrue(any("上行" in line for line in lines))
        self.assertTrue(any("下行" in line for line in lines))
        self.assertFalse(any("000003" in line for line in lines))
        self.assertEqual(repeated, [])

    def test_daily_summary_adds_compact_prediction_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "cn_qdii_etf" / "codex" / "predictions"
            path.mkdir(parents=True)
            pd.DataFrame([
                {"code": "513100", "horizon": 5, "p_up": 0.72, "p_down": 0.10, "confidence": 0.81, "model_version": "m2", "active_status": "inactive"},
            ]).to_parquet(path / "20260713.parquet", index=False)

            text = build_workflow_summary("daily", root, today_d=date(2026, 7, 13), target="2026-07-13")

        self.assertIn("新增预测提醒", text)
        self.assertIn("513100", text)
        self.assertNotIn("预测明细表", text)


if __name__ == "__main__":
    unittest.main()
