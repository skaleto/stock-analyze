from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.intelligence.cross_source import (
    CrossSourceAuditor,
    announcement_comparison_key,
    compare_announcement_rows,
    compare_market_frames,
    ifind_announcement_document,
    ifind_report_rows,
    normalize_ifind_hq,
)
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class FakeTransport:
    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.actions: list[dict] = []

    def execute(self, actions):
        self.actions = [dict(action) for action in actions]
        return {
            "status": "success",
            "login_code": 0,
            "logout_code": 0,
            "results": self.results,
        }

    @staticmethod
    def history_action(**kwargs):
        return {
            "id": kwargs["action_id"],
            "op": "hq",
            "codes": list(kwargs["codes"]),
            "start_date": kwargs["start_date"],
            "end_date": kwargs["end_date"],
        }

    @staticmethod
    def announcement_action(**kwargs):
        return {
            "id": kwargs["action_id"],
            "op": "report_query",
            "codes": list(kwargs.get("codes") or ()),
            "start_date": kwargs["start_date"],
            "end_date": kwargs["end_date"],
            "full_market": kwargs.get("full_market", False),
        }


class IntelligenceCrossSourceTest(unittest.TestCase):
    def test_issuer_prefix_does_not_create_false_announcement_gap(self) -> None:
        self.assertEqual(
            announcement_comparison_key(
                "000034.SZ",
                "神州数码：关于为子公司担保的进展公告",
                "神州数码",
            ),
            announcement_comparison_key(
                "000034.SZ",
                "关于为子公司担保的进展公告",
                "神州数码",
            ),
        )

    def test_announcement_comparison_reports_both_source_directions(self) -> None:
        primary = [
            {
                "source_id": "p1",
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "title": "关于回购股份的公告",
            },
            {
                "source_id": "p2",
                "ts_code": "000002.SZ",
                "name": "万科A",
                "title": "董事会决议公告",
            },
        ]
        secondary = [
            {
                "seq": "s1",
                "thscode": "000001.SZ",
                "secName": "平安银行",
                "reportTitle": "平安银行：关于回购股份的公告",
            },
            {
                "seq": "s3",
                "thscode": "000003.SZ",
                "secName": "国华网安",
                "reportTitle": "监管函",
            },
        ]

        result = compare_announcement_rows(primary, secondary)

        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["counts"]["primary_only"], 1)
        self.assertEqual(result["counts"]["secondary_only"], 1)
        self.assertEqual(
            {item["comparison_status"] for item in result["items"]},
            {"matched", "primary_only", "secondary_only"},
        )

    def test_ifind_document_never_persists_tokenized_pdf_url(self) -> None:
        document = ifind_announcement_document(
            {
                "seq": "5208176189",
                "reportDate": "2026-07-24",
                "ctime": "2026-07-24 22:08:38",
                "thscode": "001266.SZ",
                "secName": "宏英智能",
                "reportTitle": "投资者关系活动记录表",
                "pdfURL": (
                    "http://ft.10jqka.com.cn/download?"
                    "seq=5208176189&token=secret&userid=123"
                ),
            },
            seen_at="2026-07-27T00:00:00+00:00",
        )

        self.assertEqual(document.source, "ifind_announcement")
        self.assertEqual(document.source_id, "5208176189")
        self.assertEqual(document.source_url, "")
        self.assertNotIn("token", str(document.metadata).casefold())
        self.assertTrue(document.metadata["pdf_available"])
        self.assertEqual(
            document.metadata["security_codes"],
            ["001266.SZ"],
        )

    def test_ifind_report_rows_excludes_b_and_nonstandard_codes(self) -> None:
        payload = {
            "tables": [
                {
                    "table": {
                        "thscode": [
                            "000001.SZ",
                            "200016.SZ",
                            "900901.SH",
                            "A00359.SZ",
                            "430047.BJ",
                        ],
                        "reportTitle": [
                            "A股公告",
                            "深市B股公告",
                            "沪市B股公告",
                            "境外代码公告",
                            "北交所公告",
                        ],
                        "seq": ["1", "2", "3", "4", "5"],
                    }
                }
            ]
        }

        rows = ifind_report_rows(payload)

        self.assertEqual(
            [row["thscode"] for row in rows],
            ["000001.SZ", "430047.BJ"],
        )

    def test_ifind_hq_normalizes_units_and_market_comparison(self) -> None:
        payload = {
            "tables": [
                {
                    "thscode": "513100.SH",
                    "time": ["2026-07-24"],
                    "table": {
                        "open": [2.1],
                        "high": [2.11],
                        "low": [2.095],
                        "close": [2.104],
                        "volume": [143085380],
                        "amount": [301019967],
                    },
                }
            ]
        }
        secondary = normalize_ifind_hq(payload)
        primary = pd.DataFrame(
            [
                {
                    "ts_code": "513100.SH",
                    "trade_date": "20260724",
                    "open": 2.1,
                    "high": 2.11,
                    "low": 2.095,
                    "close": 2.104,
                    "volume_shares": 143085380,
                    "amount_yuan": 301019967,
                }
            ]
        )

        result = compare_market_frames(primary, secondary)

        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["counts"]["mismatch"], 0)
        self.assertEqual(result["items"][0]["comparison_status"], "matched")

    def test_auditor_persists_evidence_and_supplements_ifind_only_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntelligenceStore(
                root / "data" / "shared" / "intelligence"
            )
            store.insert_document(
                SourceDocument(
                    source="tushare_announcement",
                    source_id="primary-1",
                    title="关于回购股份的公告",
                    published_at="2026-07-23T16:00:00+00:00",
                    first_seen_at="2026-07-24T01:00:00+00:00",
                    effective_at="2026-07-23T16:00:00+00:00",
                    source_url="https://example.test/primary-1",
                    content=b"primary-1",
                    mime_type="text/plain",
                    metadata={
                        "content_scope": "title_metadata",
                        "ann_date": "20260724",
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                    },
                )
            )
            transport = FakeTransport(
                [
                    {
                        "id": "statistics_before",
                        "errorcode": 0,
                        "data": {"tables": {"noticeData": {"usage": 2}}},
                    },
                    {
                        "id": "announcements",
                        "errorcode": 0,
                        "data": {
                            "tables": [
                                {
                                    "table": {
                                        "reportDate": [
                                            "2026-07-24",
                                            "2026-07-24",
                                        ],
                                        "thscode": [
                                            "000001.SZ",
                                            "000003.SZ",
                                        ],
                                        "secName": ["平安银行", "国华网安"],
                                        "ctime": [
                                            "2026-07-24 09:30:00",
                                            "2026-07-24 10:00:00",
                                        ],
                                        "reportTitle": [
                                            "平安银行：关于回购股份的公告",
                                            "监管函",
                                        ],
                                        "pdfURL": [
                                            None,
                                            "https://x.test/a?token=secret",
                                        ],
                                        "seq": ["secondary-1", "secondary-2"],
                                    }
                                }
                            ]
                        },
                    },
                    {
                        "id": "statistics_after",
                        "errorcode": 0,
                        "data": {"tables": {"noticeData": {"usage": 4}}},
                    },
                ]
            )

            result = CrossSourceAuditor(
                root,
                transport=transport,
            ).run(
                as_of="2026-07-24",
                datasets={"announcement"},
                full_market_announcements=True,
                supplement=True,
            )

            self.assertEqual(
                result["datasets"]["announcement"]["counts"],
                {
                    "matched": 1,
                    "primary_only": 0,
                    "secondary_only": 1,
                    "supplemented": 1,
                },
            )
            with store.connect() as connection:
                ifind_rows = connection.execute(
                    """
                    SELECT source_id, source_url, metadata_json
                    FROM documents
                    WHERE source='ifind_announcement'
                    """
                ).fetchall()
                audit_runs = connection.execute(
                    "SELECT status FROM source_audit_runs"
                ).fetchall()
                audit_items = connection.execute(
                    """
                    SELECT comparison_status
                    FROM source_audit_items
                    ORDER BY item_key
                    """
                ).fetchall()
            self.assertEqual(len(ifind_rows), 1)
            self.assertEqual(ifind_rows[0]["source_id"], "secondary-2")
            self.assertEqual(ifind_rows[0]["source_url"], "")
            self.assertNotIn(
                "token",
                ifind_rows[0]["metadata_json"].casefold(),
            )
            self.assertEqual([row["status"] for row in audit_runs], ["success"])
            self.assertEqual(
                {row["comparison_status"] for row in audit_items},
                {"matched", "supplemented"},
            )
            self.assertTrue(Path(result["report_path"]).exists())

    def test_auditor_appends_missing_a_share_day_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "data" / "shared" / "cache"
            cache.mkdir(parents=True)
            (root / "data" / "shared" / "market_snapshot_2026-07-24.json").write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-24",
                        "status": "partial",
                        "target_codes": ["000001"],
                    }
                ),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "日期": "2026-07-23",
                        "开盘": 10.0,
                        "收盘": 10.2,
                        "最高": 10.3,
                        "最低": 9.9,
                        "成交量": 100.0,
                        "成交额": 102000.0,
                        "停牌": False,
                        "is_st": False,
                        "source": "tushare_daily",
                    }
                ]
            ).to_csv(
                cache / "history_000001_20260724_1098.csv",
                index=False,
            )
            transport = FakeTransport(
                [
                    {
                        "id": "statistics_before",
                        "errorcode": 0,
                        "data": {"tables": {}},
                    },
                    {
                        "id": "a_share_hq",
                        "errorcode": 0,
                        "data": {
                            "tables": [
                                {
                                    "thscode": "000001.SZ",
                                    "time": ["2026-07-24"],
                                    "table": {
                                        "open": [10.2],
                                        "high": [10.5],
                                        "low": [10.1],
                                        "close": [10.4],
                                        "volume": [12000],
                                        "amount": [124800],
                                    },
                                }
                            ]
                        },
                    },
                    {
                        "id": "statistics_after",
                        "errorcode": 0,
                        "data": {"tables": {}},
                    },
                ]
            )

            result = CrossSourceAuditor(
                root,
                transport=transport,
            ).run(
                as_of="2026-07-24",
                datasets={"market"},
                supplement=True,
            )

            repaired = pd.read_csv(
                cache / "history_000001_20260724_1098.csv",
                dtype={"日期": str},
            )
            row = repaired[
                repaired["日期"].str.replace("-", "", regex=False)
                == "20260724"
            ].iloc[0]
            self.assertEqual(row["source"], "ifind_hq_fallback")
            self.assertEqual(row["成交量"], 120.0)
            self.assertEqual(
                result["datasets"]["a_share_market"]["counts"][
                    "supplemented"
                ],
                1,
            )

    def test_auditor_compares_qdii_cache_with_correct_tushare_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "data" / "cn_qdii_etf" / "shared"
            cache = shared / "cache"
            cache.mkdir(parents=True)
            (shared / "market_snapshot_2026-07-24.json").write_text(
                json.dumps(
                    {
                        "as_of": "2026-07-24",
                        "status": "success",
                        "fresh_codes": 1,
                        "latest_trade_dates": {
                            "513100.SH": "20260724"
                        },
                    }
                ),
                encoding="utf-8",
            )
            pd.DataFrame(
                [
                    {
                        "ts_code": "513100.SH",
                        "trade_date": "20260724",
                        "open": 2.1,
                        "high": 2.11,
                        "low": 2.095,
                        "close": 2.104,
                        "vol": 1430853.8,
                        "amount": 301019.967,
                    }
                ]
            ).to_csv(
                cache / "fund_daily_513100_SH_20260724.csv",
                index=False,
            )
            transport = FakeTransport(
                [
                    {
                        "id": "statistics_before",
                        "errorcode": 0,
                        "data": {"tables": {}},
                    },
                    {
                        "id": "qdii_hq",
                        "errorcode": 0,
                        "data": {
                            "tables": [
                                {
                                    "thscode": "513100.SH",
                                    "time": ["2026-07-24"],
                                    "table": {
                                        "open": [2.1],
                                        "high": [2.11],
                                        "low": [2.095],
                                        "close": [2.104],
                                        "volume": [143085380],
                                        "amount": [301019967],
                                    },
                                }
                            ]
                        },
                    },
                    {
                        "id": "statistics_after",
                        "errorcode": 0,
                        "data": {"tables": {}},
                    },
                ]
            )

            result = CrossSourceAuditor(
                root,
                transport=transport,
            ).run(
                as_of="2026-07-24",
                datasets={"market"},
                supplement=True,
            )

            self.assertEqual(
                result["datasets"]["qdii_market"]["counts"],
                {
                    "matched": 1,
                    "mismatch": 0,
                    "primary_only": 0,
                    "secondary_only": 0,
                    "supplemented": 0,
                },
            )


if __name__ == "__main__":
    unittest.main()
