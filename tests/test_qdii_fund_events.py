from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_analyze.markets.cn_qdii_etf.fund_events import (
    active_event_state,
    classify_title,
    refresh_event_store,
)
from stock_analyze.markets.cn_qdii_etf.data_provider import CNQDIETFProvider
from stock_analyze.markets.cn_qdii_etf.strategy import _risk_rejection_reason


class QDIIFundEventTests(unittest.TestCase):
    def test_classifies_hard_warning_and_clearing_events(self) -> None:
        restricted = classify_title("关于暂停大额申购及定期定额投资业务的公告")
        resumed = classify_title("关于恢复申购、赎回业务的公告")
        premium = classify_title("交易价格溢价风险提示公告")
        liquidation = classify_title("基金合同终止及基金财产清算公告")

        self.assertEqual((restricted.event_type, restricted.severity), ("purchase_restriction", "hard"))
        self.assertTrue(restricted.hard_block)
        self.assertEqual(resumed.event_type, "resume")
        self.assertTrue(resumed.clears_temporary_blocks)
        self.assertEqual((premium.event_type, premium.severity), ("premium_warning", "warning"))
        self.assertEqual((liquidation.event_type, liquidation.expires_days), ("termination", None))

    def test_active_state_obeys_observed_time_and_resume_clears_block(self) -> None:
        events = pd.DataFrame(
            [
                {
                    "code": "513100.SH",
                    "published_at": "2026-07-01T00:00:00",
                    "observed_at": "2026-07-01T08:00:00",
                    "effective_at": "2026-07-01T00:00:00",
                    "event_type": "purchase_restriction",
                    "severity": "hard",
                    "hard_block": True,
                    "clears_temporary_blocks": False,
                    "expires_at": "2026-07-31T23:59:59",
                    "title": "暂停大额申购",
                    "source_url": "https://example.test/a",
                    "raw_content_hash": "a",
                    "parser_version": "v1",
                },
                {
                    "code": "513100.SH",
                    "published_at": "2026-07-03T00:00:00",
                    "observed_at": "2026-07-05T08:00:00",
                    "effective_at": "2026-07-03T00:00:00",
                    "event_type": "resume",
                    "severity": "info",
                    "hard_block": False,
                    "clears_temporary_blocks": True,
                    "expires_at": "",
                    "title": "恢复申购赎回",
                    "source_url": "https://example.test/b",
                    "raw_content_hash": "b",
                    "parser_version": "v1",
                },
            ]
        )

        before_observation = active_event_state(events, "513100.SH", "2026-07-04T23:59:59")
        after_observation = active_event_state(events, "513100.SH", "2026-07-06T23:59:59")

        self.assertTrue(before_observation["hard_block"])
        self.assertFalse(after_observation["hard_block"])
        self.assertEqual(after_observation["latest_event_type"], "resume")

    def test_refresh_store_is_deterministic_and_deduplicated(self) -> None:
        payload = [
            {
                "FUNDCODE": "513100",
                "TITLE": "交易价格溢价风险提示公告",
                "ShortTitle": "纳指ETF",
                "PUBLISHDATE": "2026-07-10T00:00:00",
                "ID": "AN-1",
                "NEWCATEGORY": "1",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fund_events.csv"
            first = refresh_event_store(
                ["513100.SH"],
                path,
                fetcher=lambda _code: payload,
                observed_at=datetime(2026, 7, 11, 8, 0, 0),
            )
            second = refresh_event_store(
                ["513100.SH"],
                path,
                fetcher=lambda _code: payload,
                observed_at=datetime(2026, 7, 12, 8, 0, 0),
            )

            persisted = pd.read_csv(path, dtype={"code": str, "report_id": str})

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted.iloc[0]["observed_at"], "2026-07-11T08:00:00")
        self.assertEqual(persisted.iloc[0]["event_type"], "premium_warning")
        self.assertTrue(str(persisted.iloc[0]["source_url"]).endswith("AN-1.html"))

    def test_live_risk_gate_rejects_active_hard_event(self) -> None:
        row = pd.Series(
            {
                "paused": False,
                "avg_amount_20": 20_000_000,
                "listing_age_days": 1000,
                "discount_premium": 0.01,
                "fund_size_yuan": 2_000_000_000,
                "active_hard_event": True,
            }
        )

        self.assertEqual(
            _risk_rejection_reason(row, {"min_avg_amount_20_yuan": 1_000_000}),
            "active_fund_event_block",
        )

    def test_provider_attaches_observable_event_state_to_universe_rows(self) -> None:
        payload = [
            {
                "FUNDCODE": "513100",
                "TITLE": "关于暂停申购、赎回业务的公告",
                "ShortTitle": "纳指ETF",
                "PUBLISHDATE": "2026-07-10T00:00:00",
                "ID": "AN-HARD",
                "NEWCATEGORY": "1",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            refresh_event_store(
                ["513100.SH"],
                Path(tmp) / "fund_events.csv",
                fetcher=lambda _code: payload,
                observed_at=datetime(2026, 7, 10, 8, 0, 0),
            )
            provider = CNQDIETFProvider(pro_client=object(), cache_dir=cache, as_of="2026-07-11")
            scopes = {"us_exposure": [{"code": "513100.SH"}]}

            provider._attach_event_states(scopes, "20260711")

        row = scopes["us_exposure"][0]
        self.assertTrue(row["active_hard_event"])
        self.assertEqual(row["latest_event_type"], "suspension")
        self.assertEqual(len(row["recent_fund_events"]), 1)


if __name__ == "__main__":
    unittest.main()
