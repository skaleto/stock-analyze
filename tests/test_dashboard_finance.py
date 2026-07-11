from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class DashboardFinanceTests(unittest.TestCase):
    def test_qdii_metadata_uses_underlying_exposure_and_theme(self) -> None:
        from stock_analyze.dashboard_finance import instrument_metadata

        metadata = instrument_metadata(
            "cn_qdii_etf",
            "513100.SH",
            "国泰纳斯达克100ETF",
        )

        self.assertEqual(metadata["exposure_group"], "美国市场")
        self.assertEqual(metadata["theme"], "纳斯达克100")

    def test_a_share_metadata_uses_industry(self) -> None:
        from stock_analyze.dashboard_finance import instrument_metadata

        metadata = instrument_metadata(
            "a_share",
            "000001.SZ",
            "平安银行",
            industry="银行",
        )

        self.assertEqual(metadata["exposure_group"], "银行")
        self.assertEqual(metadata["theme"], "银行")

    def test_enrich_rows_adds_chinese_account_and_side_labels(self) -> None:
        from stock_analyze.dashboard_finance import enrich_rows

        rows = enrich_rows(
            "cn_qdii_etf",
            [
                {
                    "account_id": "us_exposure",
                    "code": "513100.SH",
                    "name": "纳指ETF",
                    "side": "buy",
                }
            ],
        )

        self.assertEqual(rows[0]["account_label"], "美国市场ETF账户")
        self.assertEqual(rows[0]["side_label"], "买入")
        self.assertEqual(rows[0]["theme"], "纳斯达克100")

    def test_activity_combines_completed_and_planned_events(self) -> None:
        from stock_analyze.dashboard_finance import build_activity

        events = build_activity(
            [
                {
                    "trade_date": "2026-07-10",
                    "code": "513100.SH",
                    "side": "buy",
                    "side_label": "买入",
                    "shares": 1000,
                }
            ],
            [
                {
                    "execute_after": "2026-07-13",
                    "code": "159920.SZ",
                    "side": "buy",
                    "side_label": "买入",
                    "shares": 2000,
                }
            ],
        )

        self.assertEqual([event["date"] for event in events], ["2026-07-13", "2026-07-10"])
        self.assertEqual(events[0]["status"], "planned")
        self.assertEqual(events[0]["status_label"], "计划买入")
        self.assertEqual(events[1]["status"], "completed")
        self.assertEqual(events[1]["status_label"], "已买入")

    def test_strategy_profile_translates_and_sorts_factors(self) -> None:
        from stock_analyze.dashboard_finance import build_strategy_profile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "codex_cn_qdii_etf.yaml"
            path.write_text(
                json.dumps(
                    {
                        "agent_id": "codex",
                        "strategy_id": "codex-etf",
                        "name": "Codex ETF",
                        "factors": {
                            "low_volatility_60": {"weight": 0.2, "direction": "low"},
                            "momentum_20": {"weight": 0.5, "direction": "high"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            profile = build_strategy_profile(path)

        self.assertEqual(profile["agent_label"], "Codex 策略")
        self.assertEqual(profile["strategy_id"], "codex-etf")
        self.assertEqual(profile["factors"][0]["key"], "momentum_20")
        self.assertEqual(profile["factors"][0]["label"], "近20日动量")
        self.assertIn("20个交易日", profile["factors"][0]["explanation"])


if __name__ == "__main__":
    unittest.main()
