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
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "configs" / "strategy_competition.json").write_text(
                json.dumps(
                    {
                        "season_id": "s1",
                        "name": "双策略对抗",
                        "effective_date": "2026-07-11",
                        "factor_distance_floor": 0.45,
                        "slots": {
                            "claude": {"label": "稳健防守", "description": "", "color": "#d6a84b"},
                            "codex": {"label": "趋势进攻", "description": "", "color": "#22d3ee"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            path = root / "codex_cn_qdii_etf.yaml"
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

            profile = build_strategy_profile(path, repo_root=root)

        self.assertEqual(profile["agent_label"], "趋势进攻")
        self.assertEqual(profile["strategy_id"], "codex-etf")
        self.assertEqual(profile["factors"][0]["key"], "momentum_20")
        self.assertEqual(profile["factors"][0]["label"], "近20日动量")
        self.assertIn("20个交易日", profile["factors"][0]["explanation"])

    def test_profitability_ratios_are_exposed_as_percentages(self) -> None:
        from stock_analyze.dashboard_finance import build_history_metrics

        metrics = build_history_metrics(
            [{"date": "2026-07-10", "close": 10.0, "amount": 100_000.0}],
            {"roe": 0.1532, "pe": 12.4, "pb": 1.8},
        )
        formats = {metric["key"]: metric["format"] for metric in metrics}

        self.assertEqual(formats["roe"], "percent")
        self.assertEqual(formats["pe"], "number")
        self.assertEqual(formats["pb"], "number")


if __name__ == "__main__":
    unittest.main()
