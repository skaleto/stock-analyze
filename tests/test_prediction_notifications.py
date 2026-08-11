import tempfile
import unittest
import json
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
                {"code": "000004", "horizon": 5, "p_up": 0.85, "p_down": 0.05, "confidence": 0.90, "model_version": "m1", "active_status": "active", "invalidated": True},
            ]).to_parquet(path / "20260713.parquet", index=False)

            ids, lines = collect_prediction_notifications(root, "2026-07-13")
            _, repeated = collect_prediction_notifications(root, "2026-07-13", seen_alert_ids=set(ids))

        self.assertEqual(len(lines), 2)
        self.assertTrue(any("上行" in line for line in lines))
        self.assertTrue(any("下行" in line for line in lines))
        self.assertFalse(any("000003" in line for line in lines))
        self.assertFalse(any("000004" in line for line in lines))
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

    def test_daily_summary_caps_prediction_lines_but_marks_all_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "a_share" / "codex" / "predictions"
            path.mkdir(parents=True)
            pd.DataFrame([
                {
                    "code": f"00000{index}",
                    "horizon": 5,
                    "p_up": 0.60 + index * 0.04,
                    "p_down": 0.05,
                    "confidence": 0.70 + index * 0.03,
                    "model_version": "m3",
                    "active_status": "active",
                }
                for index in range(1, 6)
            ]).to_parquet(path / "20260713.parquet", index=False)

            alert_ids, lines = collect_prediction_notifications(root, "2026-07-13")
            text = build_workflow_summary(
                "daily",
                root,
                today_d=date(2026, 7, 13),
                target="2026-07-13",
            )

        self.assertEqual(len(alert_ids), 5)
        self.assertEqual(len(lines), 5)
        self.assertIn("新增预测提醒: 5 条，仅展示置信度最高的 3 条", text)
        self.assertIn("000005", text)
        self.assertIn("000004", text)
        self.assertIn("000003", text)
        self.assertNotIn("000001", text)
        self.assertNotIn("000002", text)

    def test_daily_summary_adds_one_compact_model_iteration_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lifecycle_dir = root / "data" / "model_iterations" / "cn_qdii_etf" / "5"
            data_dir = lifecycle_dir / "model-v3"
            data_dir.mkdir(parents=True)
            pd.DataFrame([
                {
                    "date": "2026-07-17",
                    "account_id": "model_shadow",
                    "total_value": 1_000_900,
                }
            ]).to_csv(data_dir / "daily_nav.csv", index=False)
            pd.DataFrame([
                {"account_id": "model_shadow", "code": "513100", "shares": 1000}
            ]).to_csv(data_dir / "positions.csv", index=False)
            (data_dir / "pending_orders.json").write_text(
                json.dumps([{"code": "513100", "side": "buy"}]),
                encoding="utf-8",
            )
            (lifecycle_dir / "current_status.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "horizon": 5,
                        "model_version": "model-v3",
                        "display_version": "Q5-V003",
                        "lifecycle_status_label": "模拟验证",
                        "shadow_cycles": 2,
                        "shadow_cycles_remaining": 10,
                        "prediction_as_of": "2026-07-17",
                        "cash_only": False,
                    }
                ),
                encoding="utf-8",
            )

            text = build_workflow_summary(
                "daily",
                root,
                today_d=date(2026, 7, 17),
                target="2026-07-17",
            )

        self.assertEqual(text.count("模型迭代:"), 1)
        self.assertIn("跨境ETF: Q5-V003（模拟验证 2/12）", text)
        self.assertIn("¥1,000,900", text)
        self.assertIn("持仓 1，待执行 1", text)
        self.assertIn("5日模型 2026-07-17", text)


if __name__ == "__main__":
    unittest.main()
