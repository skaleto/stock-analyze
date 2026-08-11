from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from stock_analyze.intelligence.backfill import (
    AnnouncementBackfill,
    TushareTradingCalendarResolver,
)
from stock_analyze.intelligence.sources.official import TushareAnnouncementAdapter
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.store import (
    BackfillConfigurationConflict,
    BackfillGenerationConflict,
)
from stock_analyze.intelligence.tushare_transport import (
    TushareRetryableError,
    TushareTerminalError,
)
from stock_analyze.intelligence.types import SourceDocument


FIELDS = "ann_date,ts_code,name,title,url,rec_time"
STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,list_date,delist_date,list_status"
)
FUND_BASIC_FIELDS = "ts_code,list_date,delist_date,status"


def announcement(
    announcement_id: str,
    *,
    ann_date: str = "20260105",
    rec_time: str | None = "2026-01-05 10:15:00",
    ts_code: str = "000001.SZ",
    url: str | None = None,
) -> dict[str, object]:
    return {
        "ann_date": ann_date,
        "ts_code": ts_code,
        "name": "平安银行",
        "title": f"Announcement {announcement_id}",
        "url": url or (
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            f"announcementId={announcement_id}"
        ),
        "rec_time": rec_time,
    }


def basic_row(ts_code: str, status: str = "L") -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "symbol": ts_code.split(".")[0],
        "name": ts_code,
        "list_date": "19901219",
        "delist_date": "",
        "list_status": status,
    }


def fund_row(
    ts_code: str,
    *,
    status: str = "L",
    list_date: str = "20200101",
    delist_date: str = "",
) -> dict[str, object]:
    return {
        "ts_code": ts_code,
        "list_date": list_date,
        "delist_date": delist_date,
        "status": status,
    }


class FakeTushareClient:
    def __init__(
        self,
        responses: dict[tuple[str, str, str], object],
        *,
        universe: dict[str, list[dict[str, object]]] | None = None,
        fund_universe: dict[str, list[dict[str, object]]] | None = None,
    ) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []
        self.trade_cal_calls: list[dict[str, object]] = []
        self.stock_basic_calls: list[dict[str, object]] = []
        self.fund_basic_calls: list[dict[str, object]] = []
        self.missing_calendar_dates: set[str] = set()
        self.empty_calendar_years: set[int] = set()
        self.open_dates = [
            "19901220",
            "20260102",
            "20260105",
            "20260106",
            "20260202",
            "20260302",
            "20260721",
        ]
        self.universe = universe or {
            "L": [basic_row("000001.SZ", "L")],
            "D": [],
            "P": [],
            "G": [],
        }
        self.fund_universe = fund_universe or {
            "": [],
            "L": [],
            "D": [],
            "I": [],
        }

    def anns_d(self, **kwargs):
        self.calls.append(dict(kwargs))
        start = str(kwargs.get("start_date") or kwargs.get("ann_date"))
        end = str(kwargs.get("end_date") or kwargs.get("ann_date"))
        ts_code = str(kwargs.get("ts_code") or "")
        key = (start, end, ts_code)
        response = self.responses.get(key, pd.DataFrame())
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        return response.copy()

    def trade_cal(self, **kwargs):
        self.trade_cal_calls.append(dict(kwargs))
        start = str(kwargs["start_date"])
        end = str(kwargs["end_date"])
        start_date = date(
            int(start[:4]),
            int(start[4:6]),
            int(start[6:8]),
        )
        end_date = date(
            int(end[:4]),
            int(end[4:6]),
            int(end[6:8]),
        )
        if start_date.year in self.empty_calendar_years:
            return pd.DataFrame(columns=["cal_date", "is_open"])
        rows = []
        current = start_date
        while current <= end_date:
            value = current.strftime("%Y%m%d")
            if value not in self.missing_calendar_dates:
                rows.append({
                    "cal_date": value,
                    "is_open": int(value in self.open_dates),
                })
            current += timedelta(days=1)
        return pd.DataFrame(rows)

    def stock_basic(self, **kwargs):
        self.stock_basic_calls.append(dict(kwargs))
        status = str(kwargs["list_status"])
        return pd.DataFrame(self.universe.get(status, [])).copy()

    def fund_basic(self, **kwargs):
        self.fund_basic_calls.append(dict(kwargs))
        status = str(kwargs.get("status") or "")
        return pd.DataFrame(
            self.fund_universe.get(status, [])
        ).copy()


class FailingLeafStore(IntelligenceStore):
    def _before_backfill_leaf_checkpoint(self, connection, documents) -> None:
        del connection, documents
        raise RuntimeError("injected_leaf_transaction_failure")


class FailingProbeStore(IntelligenceStore):
    def _before_backfill_probe_checkpoint(self, connection, documents) -> None:
        del connection, documents
        raise RuntimeError("injected_probe_transaction_failure")


class AnnouncementBackfillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def adapter(
        client,
        *,
        page_size: int = 2,
    ) -> TushareAnnouncementAdapter:
        return TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=page_size,
        )

    @staticmethod
    def calendar(*open_dates: str) -> TushareTradingCalendarResolver:
        parsed = tuple(date.fromisoformat(value) for value in open_dates)
        return TushareTradingCalendarResolver(
            parsed,
            coverage_start=min(parsed),
            coverage_end=max(parsed),
        )

    def store(
        self,
        *,
        resolver: TushareTradingCalendarResolver | None = None,
        store_type=IntelligenceStore,
    ) -> IntelligenceStore:
        return store_type(
            self.root / "data",
            historical_cutoff="2026-07-17T23:59:59+08:00",
            next_market_open_resolver=resolver,
        )

    def backfill(
        self,
        client,
        *,
        store: IntelligenceStore,
        page_size: int = 2,
        sensitive_values: tuple[str, ...] = (),
        verification_rounds: int = 1,
    ) -> AnnouncementBackfill:
        return AnnouncementBackfill(
            store=store,
            adapter=self.adapter(client, page_size=page_size),
            sensitive_values=sensitive_values,
            universe_page_size=8000,
            verification_rounds=verification_rounds,
        )

    def test_short_month_leaf_is_atomic_and_complete(self) -> None:
        client = FakeTushareClient({
            ("20260101", "20260131", ""): pd.DataFrame([
                announcement("1"),
                announcement("2"),
            ]),
        })
        store = self.store(
            resolver=self.calendar("2026-01-05", "2026-01-06")
        )

        result = self.backfill(
            client,
            store=store,
            page_size=3,
        ).run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(store.backfill_partition_count(status="complete"), 1)
        self.assertEqual([call["offset"] for call in client.calls], [0])
        self.assertTrue(all(call["fields"] == FIELDS for call in client.calls))
        metadata = json.loads(store.documents().iloc[0]["metadata_json"])
        self.assertEqual(metadata["ingestion_mode"], "history")

    def test_progress_counts_month_roots_not_yet_claimed_by_budget(self) -> None:
        client = FakeTushareClient({
            ("20260101", "20260131", ""): pd.DataFrame(),
        })
        store = self.store()

        result = self.backfill(client, store=store).run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 28),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["partitions_remaining"], 1)
        self.assertEqual(
            store.backfill_partition_count(status="complete"),
            1,
        )

    def test_short_leaf_atomically_upserts_all_document_security_links(self) -> None:
        shared_url = "https://example.test/shared-short-leaf.pdf"
        client = FakeTushareClient({
            ("20260105", "20260105", ""): pd.DataFrame([
                {
                    **announcement(
                        "shared-a",
                        ts_code="300114.SZ",
                        url=shared_url,
                    ),
                    "name": "中航电测",
                },
                {
                    **announcement(
                        "shared-b",
                        ts_code="831152.BJ",
                        url=shared_url,
                    ),
                    "name": "昆工科技",
                },
            ]),
        })
        store = self.store(
            resolver=self.calendar("2026-01-06")
        )

        result = self.backfill(
            client,
            store=store,
            page_size=3,
        ).run(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        documents = store.documents()
        self.assertEqual(len(documents), 1)
        self.assertEqual(
            [
                row["ts_code"]
                for row in store.document_security_links(
                    int(documents.iloc[0]["id"])
                )
            ],
            ["300114.SZ", "831152.BJ"],
        )

    def test_unstable_offset_pages_are_discarded_and_ranges_split_without_loss(self) -> None:
        parent_key = ("20260101", "20260104", "")
        client = FakeTushareClient({
            parent_key: [
                pd.DataFrame([
                    announcement("parent-first-a"),
                    announcement("parent-first-b"),
                ]),
                pd.DataFrame([
                    announcement("parent-second-a"),
                    announcement("parent-second-b"),
                ]),
            ],
            ("20260101", "20260102", ""): pd.DataFrame([
                announcement("left-probe-a"),
                announcement("left-probe-b"),
            ]),
            ("20260103", "20260104", ""): pd.DataFrame([
                announcement("right-probe-a"),
                announcement("right-probe-b"),
            ]),
            ("20260101", "20260101", ""): pd.DataFrame([
                announcement("leaf-1", ann_date="20260101"),
            ]),
            ("20260102", "20260102", ""): pd.DataFrame([
                announcement("leaf-2", ann_date="20260102"),
            ]),
            ("20260103", "20260103", ""): pd.DataFrame([
                announcement("leaf-3", ann_date="20260103"),
            ]),
            ("20260104", "20260104", ""): pd.DataFrame([
                announcement("leaf-4", ann_date="20260104"),
            ]),
        })
        store = self.store(
            resolver=self.calendar(
                "2026-01-02",
                "2026-01-05",
                "2026-01-06",
            )
        )
        coordinator = self.backfill(client, store=store)

        first = coordinator.run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 4),
            max_partitions=1,
            resume=True,
        )
        self.assertEqual(first["status"], "partial")
        self.assertTrue(store.documents().empty)

        second = coordinator.run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 4),
            max_partitions=10,
            resume=False,
        )

        self.assertEqual(second["status"], "complete")
        self.assertTrue(all(call["offset"] == 0 for call in client.calls))
        self.assertEqual(
            set(store.documents()["source_id"]),
            {"leaf-1", "leaf-2", "leaf-3", "leaf-4"},
        )
        self.assertEqual(
            sum(
                call.get("start_date") == "20260101"
                and call.get("end_date") == "20260104"
                for call in client.calls
            ),
            2,
        )

    def test_saturated_day_uses_fixed_universe_and_completes_all_items(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement("day-probe-1", ann_date="20230621"),
                    announcement("day-probe-2", ann_date="20230621"),
                ]),
                ("20230621", "20230621", "000001.SZ"): pd.DataFrame([
                    announcement(
                        "security-a",
                        ann_date="20230621",
                        ts_code="000001.SZ",
                    ),
                ]),
                ("20230621", "20230621", "600000.SH"): pd.DataFrame([
                    announcement(
                        "security-b",
                        ann_date="20230621",
                        ts_code="600000.SH",
                    ),
                ]),
            },
            universe={
                "L": [
                    basic_row("600000.SH", "L"),
                    basic_row("200001.SZ", "L"),
                ],
                "D": [basic_row("000001.SZ", "D")],
                "P": [basic_row("900901.SH", "P")],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22", "2023-06-23")
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=10,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            set(store.documents()["source_id"]),
            {
                "day-probe-1",
                "day-probe-2",
                "security-a",
                "security-b",
            },
        )
        binding = store.backfill_universe_for_partition(
            "tushare_announcement", "2023-06-21", "2023-06-21"
        )
        self.assertEqual(binding["security_count"], 2)
        items = store.backfill_partition_items(
            "tushare_announcement", "2023-06-21", "2023-06-21"
        )
        self.assertEqual(
            [(item["ts_code"], item["status"]) for item in items],
            [("000001.SZ", "complete"), ("600000.SH", "complete")],
        )
        self.assertEqual(
            {call["list_status"] for call in client.stock_basic_calls},
            {"L", "D", "P", "G"},
        )
        self.assertTrue(
            all(call["fields"] == STOCK_BASIC_FIELDS for call in client.stock_basic_calls)
        )

    def test_saturated_day_announcement_universe_does_not_filter_by_dates(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement(
                        "fund-a",
                        ann_date="20230621",
                        ts_code="516390.SH",
                    ),
                    announcement(
                        "fund-b",
                        ann_date="20230621",
                        ts_code="516780.SH",
                    ),
                ]),
                ("20230621", "20230621", "516390.SH"): pd.DataFrame([
                    announcement(
                        "fund-a",
                        ann_date="20230621",
                        ts_code="516390.SH",
                    ),
                ]),
                ("20230621", "20230621", "516780.SH"): pd.DataFrame([
                    announcement(
                        "fund-b",
                        ann_date="20230621",
                        ts_code="516780.SH",
                    ),
                ]),
            },
            universe={
                "L": [{
                    **basic_row("600001.SH", "L"),
                    "list_date": "20240101",
                }],
                "D": [{
                    **basic_row("000002.SZ", "D"),
                    "delist_date": "20221231",
                }],
                "P": [],
            },
            fund_universe={
                "L": [
                    fund_row("516390.SH", list_date="20210209"),
                    fund_row("588999.SH", list_date="20240101"),
                ],
                "D": [
                    fund_row(
                        "516780.SH",
                        status="D",
                        list_date="20210301",
                        delist_date="20231231",
                    ),
                    fund_row(
                        "500001.SH",
                        status="D",
                        list_date="20000101",
                        delist_date="20221231",
                    ),
                ],
                "I": [
                    fund_row(
                        "589999.SH",
                        status="I",
                        list_date="",
                    ),
                ],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=10,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            [item["ts_code"] for item in store.backfill_partition_items(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
            )],
            [
                "000002.SZ",
                "500001.SH",
                "516390.SH",
                "516780.SH",
                "588999.SH",
                "589999.SH",
                "600001.SH",
            ],
        )
        self.assertEqual(
            {
                call["status"]
                for call in client.fund_basic_calls
                if "status" in call
            },
            {"L", "D", "I"},
        )
        self.assertEqual(
            sum("status" not in call for call in client.fund_basic_calls),
            1,
        )
        self.assertTrue(
            all(
                call["market"] == "E"
                and call["limit"] == 15000
                and call["fields"] == FUND_BASIC_FIELDS
                for call in client.fund_basic_calls
            )
        )
        probe = store.backfill_probe_documents(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(
            {row["ts_code"] for row in probe},
            {"516390.SH", "516780.SH"},
        )

    def test_announcement_universe_covers_historical_stocks_and_unfiltered_funds(self) -> None:
        codes = (
            "300114.SZ",
            "831152.BJ",
            "159756.SZ",
            "159799.SZ",
            "512830.SH",
        )
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement(
                        f"probe-{code}",
                        ann_date="20230621",
                        ts_code=code,
                    )
                    for code in codes
                ]),
                **{
                    ("20230621", "20230621", code): pd.DataFrame()
                    for code in codes
                },
            },
            universe={
                "L": [{
                    **basic_row("300114.SZ", "L"),
                    "list_date": "20240101",
                }],
                "D": [],
                "P": [],
                "G": [{
                    **basic_row("831152.BJ", "G"),
                    "list_date": "",
                }],
            },
            fund_universe={
                "": [
                    fund_row(
                        "159756.SZ",
                        status="",
                        list_date="",
                    ),
                    fund_row(
                        "159799.SZ",
                        status="",
                        list_date="",
                    ),
                    fund_row(
                        "512830.SH",
                        status="",
                        list_date="",
                    ),
                ],
                "L": [],
                "D": [],
                "I": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )

        result = self.backfill(
            client,
            store=store,
            page_size=5,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=7,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            {
                item["ts_code"]
                for item in store.backfill_partition_items(
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                )
            },
            set(codes),
        )
        self.assertIn(
            "G",
            {call["list_status"] for call in client.stock_basic_calls},
        )
        unfiltered_fund_calls = [
            call
            for call in client.fund_basic_calls
            if "status" not in call
        ]
        self.assertEqual(len(unfiltered_fund_calls), 1)
        self.assertEqual(unfiltered_fund_calls[0]["market"], "E")
        self.assertEqual(unfiltered_fund_calls[0]["limit"], 15000)

    def test_missing_directory_codes_converge_via_catalog_and_verification_rounds(self) -> None:
        initial_codes = ("300114.SZ", "831152.BJ")
        discovered_codes = ("831689.BJ", "833429.BJ")
        initial_page = pd.DataFrame([
            {
                **announcement(
                    f"initial-{code}",
                    ann_date="20230621",
                    ts_code=code,
                ),
                "name": f"Name {code}",
            }
            for code in initial_codes
        ])
        discovered_page = pd.DataFrame([
            {
                **announcement(
                    f"discovered-{code}",
                    ann_date="20230621",
                    ts_code=code,
                ),
                "name": f"Name {code}",
            }
            for code in discovered_codes
        ])
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): [
                    initial_page,
                    discovered_page,
                    discovered_page,
                    discovered_page,
                ],
                ("20230621", "20230621", "000001.SZ"):
                    pd.DataFrame(),
                **{
                    ("20230621", "20230621", code):
                        pd.DataFrame()
                    for code in (
                        *initial_codes,
                        *discovered_codes,
                    )
                },
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )

        result = self.backfill(
            client,
            store=store,
            page_size=2,
            verification_rounds=2,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=12,
            resume=True,
        )

        expected_codes = {
            "000001.SZ",
            *initial_codes,
            *discovered_codes,
        }
        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            {
                item["ts_code"]
                for item in store.backfill_partition_items(
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                )
            },
            expected_codes,
        )
        with store.connect() as connection:
            catalog = {
                str(row["ts_code"])
                for row in connection.execute(
                    """
                    SELECT ts_code
                    FROM announcement_security_catalog
                    WHERE source='tushare_announcement'
                    """
                )
            }
            rounds = connection.execute(
                """
                SELECT round_no, probe_hash, new_documents,
                       new_security_codes, stable_rounds
                FROM backfill_verification_rounds
                ORDER BY round_no
                """
            ).fetchall()
        self.assertTrue(expected_codes - {"000001.SZ"} <= catalog)
        self.assertEqual(
            [
                (
                    row["round_no"],
                    row["new_documents"],
                    row["new_security_codes"],
                    row["stable_rounds"],
                )
                for row in rounds
            ],
            [
                (1, 2, 2, 0),
                (2, 0, 0, 1),
                (3, 0, 0, 2),
            ],
        )
        self.assertEqual(
            result["coverage_basis"],
            "catalog_items_plus_stable_offset0_reprobes",
        )
        self.assertEqual(result["items_total"], 5)
        self.assertEqual(result["items_complete"], 5)
        self.assertEqual(result["items_remaining"], 0)
        self.assertEqual(result["items_failed"], 0)
        self.assertEqual(
            result["verification"]["required_stable_rounds"],
            2,
        )
        self.assertEqual(
            result["verification"]["rounds_total"],
            3,
        )
        self.assertEqual(
            result["verification"]["partitions"],
            [{
                "partition_start": "2023-06-21",
                "partition_end": "2023-06-21",
                "rounds_total": 3,
                "stable_rounds": 2,
                "last_probe_hash": rounds[-1]["probe_hash"],
                "last_new_documents": 0,
                "last_new_security_codes": 0,
            }],
        )

    def test_job_snapshot_is_fixed_and_overlap_reuses_partition_evidence(self) -> None:
        store = self.store()
        source = "tushare_announcement"
        first_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-21",
            end_date="2023-06-21",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        same_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-21",
            end_date="2023-06-21",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        first_claim = store.start_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2,
            job_id=str(first_job["job_id"]),
        )
        first_binding = store.bind_backfill_universe(
            source,
            "2023-06-21",
            "2023-06-21",
            security_codes=("000001.SZ",),
            request_limit=2,
            job_id=str(first_job["job_id"]),
        )
        store.finish_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            generation=int(first_claim["generation"]),
            status="failed_overflow",
        )
        resumed_binding = store.bind_backfill_universe(
            source,
            "2023-06-21",
            "2023-06-21",
            security_codes=("000001.SZ", "831689.BJ"),
            request_limit=2,
            job_id=str(first_job["job_id"]),
        )

        second_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-21",
            end_date="2023-06-22",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        store.start_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2,
            job_id=str(second_job["job_id"]),
        )
        second_binding = store.bind_backfill_universe(
            source,
            "2023-06-21",
            "2023-06-21",
            security_codes=("000001.SZ", "831689.BJ"),
            request_limit=2,
            job_id=str(second_job["job_id"]),
        )

        self.assertEqual(first_job["job_id"], same_job["job_id"])
        self.assertEqual(
            first_binding["snapshot_id"],
            resumed_binding["snapshot_id"],
        )
        self.assertNotEqual(
            first_job["job_id"],
            second_job["job_id"],
        )
        self.assertEqual(
            first_binding["snapshot_id"],
            second_binding["snapshot_id"],
        )

    def test_overlapping_jobs_share_partition_evidence_without_reownership(self) -> None:
        store = self.store()
        source = "tushare_announcement"
        first_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-01",
            end_date="2023-06-30",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        second_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-21",
            end_date="2023-07-31",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        first_claim = store.start_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2,
            job_id=str(first_job["job_id"]),
        )
        first_binding = store.bind_backfill_universe(
            source,
            "2023-06-21",
            "2023-06-21",
            security_codes=("000001.SZ", "831152.BJ"),
            request_limit=2,
            job_id=str(first_job["job_id"]),
        )
        store.finish_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            generation=int(first_claim["generation"]),
            status="failed_retryable",
            error="temporary",
        )

        second_claim = store.start_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2,
            job_id=str(second_job["job_id"]),
        )
        second_binding = store.bind_backfill_universe(
            source,
            "2023-06-21",
            "2023-06-21",
            security_codes=("000001.SZ", "831152.BJ", "833429.BJ"),
            request_limit=2,
            job_id=str(second_job["job_id"]),
        )

        with store.connect() as connection:
            refs = connection.execute(
                """
                SELECT job_id
                FROM backfill_job_partition_refs
                WHERE source=? AND partition_start=? AND partition_end=?
                ORDER BY job_id
                """,
                (source, "2023-06-21", "2023-06-21"),
            ).fetchall()
            items = connection.execute(
                """
                SELECT ts_code
                FROM backfill_partition_items
                WHERE source=? AND partition_start=? AND partition_end=?
                ORDER BY ts_code
                """,
                (source, "2023-06-21", "2023-06-21"),
            ).fetchall()
            evidence = connection.execute(
                """
                SELECT evidence_config_hash, job_id
                FROM backfill_partitions
                WHERE source=? AND partition_start=? AND partition_end=?
                """,
                (source, "2023-06-21", "2023-06-21"),
            ).fetchone()

        self.assertEqual(
            {str(row["job_id"]) for row in refs},
            {
                str(first_job["job_id"]),
                str(second_job["job_id"]),
            },
        )
        self.assertEqual(
            [str(row["ts_code"]) for row in items],
            ["000001.SZ", "831152.BJ"],
        )
        self.assertEqual(
            first_binding["snapshot_id"],
            second_binding["snapshot_id"],
        )
        self.assertEqual(evidence["evidence_config_hash"], "config-a")
        self.assertEqual(evidence["job_id"], "")
        self.assertGreater(
            int(second_claim["generation"]),
            int(first_claim["generation"]),
        )
        self.assertEqual(
            store.backfill_job_progress(
                str(first_job["job_id"])
            )["partitions_total"],
            1,
        )
        self.assertEqual(
            store.backfill_job_progress(
                str(second_job["job_id"])
            )["partitions_total"],
            1,
        )

    def test_overlapping_job_reuses_complete_partition_without_new_generation(self) -> None:
        store = self.store()
        source = "tushare_announcement"
        first_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-01",
            end_date="2023-06-30",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        second_job = store.ensure_backfill_job(
            source,
            start_date="2023-06-21",
            end_date="2023-07-31",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        first_claim = store.start_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2,
            job_id=str(first_job["job_id"]),
        )
        store.finish_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            generation=int(first_claim["generation"]),
            status="complete",
        )

        reused = store.start_backfill_partition(
            source,
            "2023-06-21",
            "2023-06-21",
            resume=False,
            request_limit=2,
            job_id=str(second_job["job_id"]),
        )

        self.assertEqual(reused["status"], "complete")
        self.assertEqual(
            int(reused["generation"]),
            int(first_claim["generation"]),
        )
        self.assertEqual(
            store.backfill_job_progress(
                str(first_job["job_id"])
            )["partitions_complete"],
            1,
        )
        self.assertEqual(
            store.backfill_job_progress(
                str(second_job["job_id"])
            )["partitions_complete"],
            1,
        )

    def _complete_partition_with_evidence(
        self,
        store: IntelligenceStore,
        *,
        exact_hash: str,
        compatibility_hash: str,
        request_limit: int,
    ) -> tuple[dict[str, object], dict[str, object]]:
        job = store.ensure_backfill_job(
            "tushare_announcement",
            start_date="2023-06-01",
            end_date="2023-06-30",
            config_hash=exact_hash,
            compatibility_hash=compatibility_hash,
            config_json=(
                '{"page_size":'
                f"{request_limit},"
                '"verification_rounds":2}'
            ),
            request_limit=request_limit,
            verification_required=2,
        )
        claim = store.start_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=request_limit,
            job_id=str(job["job_id"]),
        )
        store.finish_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            generation=int(claim["generation"]),
            status="complete",
        )
        return job, claim

    def _new_evidence_job(
        self,
        store: IntelligenceStore,
        *,
        exact_hash: str,
        compatibility_hash: str,
        request_limit: int,
    ) -> dict[str, object]:
        return store.ensure_backfill_job(
            "tushare_announcement",
            start_date="2023-06-21",
            end_date="2023-07-31",
            config_hash=exact_hash,
            compatibility_hash=compatibility_hash,
            config_json=(
                '{"page_size":'
                f"{request_limit},"
                '"verification_rounds":2}'
            ),
            request_limit=request_limit,
            verification_required=2,
        )

    def test_complete_evidence_rejects_page_limit_decrease(self) -> None:
        store = self.store()
        self._complete_partition_with_evidence(
            store,
            exact_hash="exact-2000",
            compatibility_hash="compat-shared",
            request_limit=2000,
        )
        new_job = self._new_evidence_job(
            store,
            exact_hash="exact-1000",
            compatibility_hash="compat-shared",
            request_limit=1000,
        )

        with self.assertRaisesRegex(
            BackfillConfigurationConflict,
            "evidence_revalidation_required",
        ):
            store.start_backfill_partition(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
                resume=True,
                request_limit=1000,
                job_id=str(new_job["job_id"]),
            )

        with store.connect() as connection:
            partition = connection.execute(
                """
                SELECT evidence_config_hash, evidence_request_limit
                FROM backfill_partitions
                """
            ).fetchone()
            reference = connection.execute(
                """
                SELECT evidence_status
                FROM backfill_job_partition_refs
                WHERE job_id=?
                """,
                (str(new_job["job_id"]),),
            ).fetchone()
            job = connection.execute(
                """
                SELECT status, evidence_status
                FROM backfill_jobs
                WHERE job_id=?
                """,
                (str(new_job["job_id"]),),
            ).fetchone()
        self.assertEqual(tuple(partition), ("exact-2000", 2000))
        self.assertEqual(reference["evidence_status"], "needs_revalidation")
        self.assertEqual(tuple(job), ("partial", "needs_revalidation"))

    def test_complete_evidence_allows_only_page_limit_increase(self) -> None:
        store = self.store()
        _, original_claim = self._complete_partition_with_evidence(
            store,
            exact_hash="exact-1000",
            compatibility_hash="compat-shared",
            request_limit=1000,
        )
        new_job = self._new_evidence_job(
            store,
            exact_hash="exact-2000",
            compatibility_hash="compat-shared",
            request_limit=2000,
        )

        reused = store.start_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2000,
            job_id=str(new_job["job_id"]),
        )

        with store.connect() as connection:
            partition = connection.execute(
                """
                SELECT evidence_config_hash, evidence_request_limit
                FROM backfill_partitions
                """
            ).fetchone()
            reference = connection.execute(
                """
                SELECT evidence_status
                FROM backfill_job_partition_refs
                WHERE job_id=?
                """,
                (str(new_job["job_id"]),),
            ).fetchone()
        self.assertEqual(reused["status"], "complete")
        self.assertEqual(
            int(reused["generation"]),
            int(original_claim["generation"]),
        )
        self.assertEqual(tuple(partition), ("exact-1000", 1000))
        self.assertEqual(
            reference["evidence_status"],
            "compatible_limit_upgrade",
        )

    def test_complete_evidence_rejects_other_config_change(self) -> None:
        store = self.store()
        self._complete_partition_with_evidence(
            store,
            exact_hash="exact-old",
            compatibility_hash="compat-old",
            request_limit=1000,
        )
        new_job = self._new_evidence_job(
            store,
            exact_hash="exact-new",
            compatibility_hash="compat-new",
            request_limit=2000,
        )

        with self.assertRaisesRegex(
            BackfillConfigurationConflict,
            "evidence_revalidation_required",
        ):
            store.reference_backfill_partition(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
                job_id=str(new_job["job_id"]),
            )

        partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(
            partition["evidence_config_hash"],
            "exact-old",
        )
        self.assertEqual(partition["evidence_request_limit"], 1000)

    def test_catalog_growth_reopens_completed_saturated_partition(self) -> None:
        probe_page = pd.DataFrame([
            announcement(
                "probe-1",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
            announcement(
                "probe-2",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
        ])
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): [
                    probe_page.copy(),
                    probe_page.copy(),
                    probe_page.copy(),
                ],
                ("20230621", "20230621", "000001.SZ"):
                    pd.DataFrame(),
                ("20230621", "20230621", "833429.BJ"):
                    pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )
        first = self.backfill(
            client,
            store=store,
            page_size=2,
            verification_rounds=1,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=5,
            resume=True,
        )
        first_partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        first_revision = int(first_partition["catalog_revision"])
        first_hash = str(first_partition["catalog_hash"])

        store.insert_document(SourceDocument(
            source="tushare_announcement",
            source_id="catalog-growth-833429",
            title="新增历史证券公告",
            published_at="2023-06-21T01:30:00+00:00",
            first_seen_at="2026-07-25T01:00:00+00:00",
            effective_at="2023-06-21T01:30:00+00:00",
            source_url="https://example.test/833429.pdf",
            content=b"catalog-growth-833429",
            metadata={
                "ingestion_mode": "live",
                "security_links": [{
                    "ts_code": "833429.BJ",
                    "name": "康比特",
                    "provenance": "live_discovery",
                }],
            },
        ))
        catalog = store.announcement_catalog_state(
            "tushare_announcement"
        )

        second = self.backfill(
            client,
            store=store,
            page_size=3,
            verification_rounds=1,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=5,
            resume=True,
        )
        second_partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        items = store.backfill_partition_items(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )

        self.assertEqual(first["status"], "complete")
        self.assertGreater(first_revision, 0)
        self.assertTrue(first_hash)
        self.assertGreater(int(catalog["revision"]), first_revision)
        self.assertNotEqual(str(catalog["content_hash"]), first_hash)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(
            int(second_partition["catalog_revision"]),
            int(catalog["revision"]),
        )
        self.assertEqual(
            second_partition["catalog_hash"],
            catalog["content_hash"],
        )
        self.assertIn(
            ("833429.BJ", "complete"),
            {
                (str(item["ts_code"]), str(item["status"]))
                for item in items
            },
        )
        self.assertTrue(any(
            str(call.get("ts_code") or "") == "833429.BJ"
            for call in client.calls
        ))

    def test_catalog_growth_with_insufficient_budget_downgrades_job_until_revalidated(
        self,
    ) -> None:
        probe_page = pd.DataFrame([
            announcement(
                "budget-probe-1",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
            announcement(
                "budget-probe-2",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
        ])
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): [
                    probe_page.copy(),
                    probe_page.copy(),
                    probe_page.copy(),
                ],
                ("20230621", "20230621", "000001.SZ"):
                    pd.DataFrame(),
                ("20230621", "20230621", "833429.BJ"):
                    pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )
        coordinator = self.backfill(
            client,
            store=store,
            page_size=2,
            verification_rounds=1,
        )
        first = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=5,
            resume=True,
        )
        with store.connect() as connection:
            completed_job = connection.execute(
                """
                SELECT exact_config_hash, compatibility_hash, config_json
                FROM backfill_jobs
                """
            ).fetchone()
        overlapping_job = store.ensure_backfill_job(
            "tushare_announcement",
            start_date="2023-06-20",
            end_date="2023-06-21",
            config_hash=str(completed_job["exact_config_hash"]),
            compatibility_hash=str(
                completed_job["compatibility_hash"]
            ),
            config_json=str(completed_job["config_json"]),
            request_limit=2,
            verification_required=1,
        )
        store.reference_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            job_id=str(overlapping_job["job_id"]),
            job_generation=int(overlapping_job["generation"]),
        )
        self.assertEqual(
            store.finish_backfill_job(
                str(overlapping_job["job_id"]),
                generation=int(overlapping_job["generation"]),
                status="complete",
            ),
            "complete",
        )
        store.insert_document(SourceDocument(
            source="tushare_announcement",
            source_id="budget-growth-833429",
            title="新增历史证券公告",
            published_at="2023-06-21T01:30:00+00:00",
            first_seen_at="2026-07-25T01:00:00+00:00",
            effective_at="2023-06-21T01:30:00+00:00",
            source_url="https://example.test/budget-833429.pdf",
            content=b"budget-growth-833429",
            metadata={
                "ingestion_mode": "live",
                "security_links": [{
                    "ts_code": "833429.BJ",
                    "name": "康比特",
                    "provenance": "live_discovery",
                }],
            },
        ))

        second = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )
        store.reference_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            job_id=str(overlapping_job["job_id"]),
            job_generation=int(overlapping_job["generation"]),
        )
        with store.connect() as connection:
            jobs = connection.execute(
                """
                SELECT job_id, status, evidence_status, generation
                FROM backfill_jobs
                ORDER BY job_id
                """
            ).fetchall()
            job = next(
                row for row in jobs
                if str(row["job_id"]) !=
                str(overlapping_job["job_id"])
            )
            reference = connection.execute(
                """
                SELECT evidence_status
                FROM backfill_job_partition_refs
                WHERE job_id=?
                """,
                (str(job["job_id"]),),
            ).fetchone()

        self.assertEqual(first["status"], "complete")
        self.assertEqual(second["status"], "partial")
        self.assertEqual(job["status"], "partial")
        self.assertEqual(job["evidence_status"], "needs_revalidation")
        self.assertEqual(
            {
                (str(row["status"]), str(row["evidence_status"]))
                for row in jobs
            },
            {("partial", "needs_revalidation")},
        )
        self.assertEqual(
            reference["evidence_status"],
            "needs_revalidation",
        )

        third = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=2,
            resume=True,
        )
        with store.connect() as connection:
            recovered = connection.execute(
                """
                SELECT status, evidence_status, generation
                FROM backfill_jobs
                WHERE job_id=?
                """,
                (str(job["job_id"]),),
            ).fetchone()
        self.assertEqual(third["status"], "complete")
        self.assertEqual(tuple(recovered)[:2], ("complete", "current"))
        self.assertGreater(
            int(recovered["generation"]),
            int(job["generation"]),
        )

    def test_catalog_growth_reopens_real_split_tree_and_descends_new_item(
        self,
    ) -> None:
        saturated_page = pd.DataFrame([
            announcement(
                "split-probe-1",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
            announcement(
                "split-probe-2",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
        ])
        client = FakeTushareClient(
            {
                ("20230620", "20230621", ""): pd.DataFrame([
                    announcement(
                        "split-root-1",
                        ann_date="20230620",
                    ),
                    announcement(
                        "split-root-2",
                        ann_date="20230621",
                    ),
                ]),
                ("20230620", "20230620", ""): pd.DataFrame([
                    announcement(
                        "split-short",
                        ann_date="20230620",
                    ),
                ]),
                ("20230621", "20230621", ""): [
                    saturated_page.copy(),
                    saturated_page.copy(),
                    saturated_page.copy(),
                ],
                ("20230621", "20230621", "000001.SZ"):
                    pd.DataFrame(),
                ("20230621", "20230621", "833429.BJ"):
                    pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )
        coordinator = self.backfill(
            client,
            store=store,
            page_size=2,
            verification_rounds=1,
        )
        first = coordinator.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=8,
            resume=True,
        )
        parent_before = store.backfill_partition(
            "tushare_announcement",
            "2023-06-20",
            "2023-06-21",
        )
        store.insert_document(SourceDocument(
            source="tushare_announcement",
            source_id="split-growth-833429",
            title="新增历史证券公告",
            published_at="2023-06-21T01:30:00+00:00",
            first_seen_at="2026-07-25T01:00:00+00:00",
            effective_at="2023-06-21T01:30:00+00:00",
            source_url="https://example.test/split-833429.pdf",
            content=b"split-growth-833429",
            metadata={
                "ingestion_mode": "live",
                "security_links": [{
                    "ts_code": "833429.BJ",
                    "name": "康比特",
                    "provenance": "live_discovery",
                }],
            },
        ))
        client.calls.clear()

        second = coordinator.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )
        parent_reopened = store.backfill_partition(
            "tushare_announcement",
            "2023-06-20",
            "2023-06-21",
        )
        leaf_reopened = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        third = coordinator.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )
        parent_after = store.backfill_partition(
            "tushare_announcement",
            "2023-06-20",
            "2023-06-21",
        )
        leaf_after = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        catalog = store.announcement_catalog_state(
            "tushare_announcement"
        )

        self.assertEqual(first["status"], "complete")
        self.assertEqual(parent_before["completion_basis"], "split_children")
        self.assertEqual(second["status"], "partial")
        self.assertEqual(parent_reopened["status"], "failed_overflow")
        self.assertEqual(
            parent_reopened["completion_basis"],
            "split_children_revalidation",
        )
        self.assertEqual(leaf_reopened["status"], "failed_overflow")
        self.assertEqual(
            leaf_reopened["completion_basis"],
            "saturated_catalog_revalidation",
        )
        self.assertEqual(third["status"], "complete")
        self.assertEqual(parent_after["status"], "complete")
        self.assertEqual(parent_after["completion_basis"], "split_children")
        self.assertEqual(
            int(parent_after["catalog_revision"]),
            int(catalog["revision"]),
        )
        self.assertEqual(parent_after["catalog_hash"], catalog["content_hash"])
        self.assertEqual(leaf_after["status"], "complete")
        self.assertIn(
            ("833429.BJ", "complete"),
            {
                (str(item["ts_code"]), str(item["status"]))
                for item in store.backfill_partition_items(
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                )
            },
        )
        self.assertTrue(any(
            str(call.get("ts_code") or "") == "833429.BJ"
            for call in client.calls
        ))

    def _masked_catalog_growth_split(
        self,
        *,
        recovery_page_size: int,
    ) -> tuple[
        FakeTushareClient,
        IntelligenceStore,
        AnnouncementBackfill,
    ]:
        saturated_page = pd.DataFrame([
            announcement(
                "masked-probe-1",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
            announcement(
                "masked-probe-2",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
        ])
        client = FakeTushareClient(
            {
                ("20230620", "20230621", ""): pd.DataFrame([
                    announcement(
                        "masked-root-1",
                        ann_date="20230620",
                    ),
                    announcement(
                        "masked-root-2",
                        ann_date="20230621",
                    ),
                ]),
                ("20230620", "20230620", ""): pd.DataFrame([
                    announcement(
                        "masked-short",
                        ann_date="20230620",
                    ),
                ]),
                ("20230621", "20230621", ""): [
                    saturated_page.copy(),
                    saturated_page.copy(),
                    saturated_page.copy(),
                ],
                ("20230621", "20230621", "000001.SZ"):
                    pd.DataFrame(),
                ("20230621", "20230621", "833429.BJ"):
                    pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )
        initial = self.backfill(
            client,
            store=store,
            page_size=2,
            verification_rounds=1,
        )
        first = initial.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=8,
            resume=True,
        )
        self.assertEqual(first["status"], "complete")
        store.insert_document(SourceDocument(
            source="tushare_announcement",
            source_id="masked-growth-833429",
            title="新增历史证券公告",
            published_at="2023-06-21T01:30:00+00:00",
            first_seen_at="2026-07-25T01:00:00+00:00",
            effective_at="2023-06-21T01:30:00+00:00",
            source_url="https://example.test/masked-833429.pdf",
            content=b"masked-growth-833429",
            metadata={
                "ingestion_mode": "live",
                "security_links": [{
                    "ts_code": "833429.BJ",
                    "name": "康比特",
                    "provenance": "live_discovery",
                }],
            },
        ))
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE backfill_partitions
                SET status='failed_overflow',
                    error='catalog_growth_revalidation',
                    completion_strategy_version=0,
                    completion_basis='saturated_catalog_revalidation'
                WHERE source='tushare_announcement'
                  AND partition_start='2023-06-21'
                  AND partition_end='2023-06-21'
                """
            )
            connection.execute(
                """
                UPDATE backfill_partition_verification_state
                SET stable_rounds=0, last_probe_hash='',
                    last_new_documents=0,
                    last_new_security_codes=0
                WHERE source='tushare_announcement'
                  AND partition_start='2023-06-21'
                  AND partition_end='2023-06-21'
                """
            )
            connection.execute(
                """
                UPDATE backfill_job_partition_refs
                SET evidence_status='exact'
                """
            )
            connection.execute(
                """
                UPDATE backfill_jobs
                SET status='complete', evidence_status='current'
                """
            )
        client.calls.clear()
        recovery = self.backfill(
            client,
            store=store,
            page_size=recovery_page_size,
            verification_rounds=1,
        )
        return client, store, recovery

    def test_masked_failed_catalog_leaf_exact_resume_must_request(
        self,
    ) -> None:
        client, store, coordinator = (
            self._masked_catalog_growth_split(
                recovery_page_size=2,
            )
        )

        result = coordinator.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=2,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertGreaterEqual(len(client.calls), 2)
        self.assertTrue(any(
            str(call.get("ts_code") or "") == "833429.BJ"
            for call in client.calls
        ))
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement",
                "2023-06-20",
                "2023-06-21",
            )["status"],
            "complete",
        )

    def test_masked_failed_catalog_leaf_limit_upgrade_must_request(
        self,
    ) -> None:
        client, store, coordinator = (
            self._masked_catalog_growth_split(
                recovery_page_size=3,
            )
        )

        result = coordinator.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=2,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertGreaterEqual(len(client.calls), 2)
        self.assertTrue(any(
            str(call.get("ts_code") or "") == "833429.BJ"
            for call in client.calls
        ))
        with store.connect() as connection:
            upgraded_job = connection.execute(
                """
                SELECT status
                FROM backfill_jobs
                WHERE request_limit=3
                """
            ).fetchone()
        self.assertEqual(upgraded_job["status"], "complete")

    def _masked_invalid_probe_descendant(
        self,
        *,
        descendant_status: str,
        descendant_error: str,
        descendant_strategy: int,
        recovery_page_size: int,
        case_name: str,
    ) -> tuple[
        FakeTushareClient,
        IntelligenceStore,
        AnnouncementBackfill,
    ]:
        saturated_page = pd.DataFrame([
            announcement(
                f"{case_name}-probe-1",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
            announcement(
                f"{case_name}-probe-2",
                ann_date="20230621",
                ts_code="000001.SZ",
            ),
        ])
        client = FakeTushareClient(
            {
                ("20230620", "20230621", ""): pd.DataFrame([
                    announcement(
                        f"{case_name}-root-1",
                        ann_date="20230620",
                    ),
                    announcement(
                        f"{case_name}-root-2",
                        ann_date="20230621",
                    ),
                ]),
                ("20230620", "20230620", ""): pd.DataFrame([
                    announcement(
                        f"{case_name}-short",
                        ann_date="20230620",
                    ),
                ]),
                ("20230621", "20230621", ""): [
                    saturated_page.copy()
                    for _ in range(8)
                ],
                ("20230621", "20230621", "000001.SZ"):
                    pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = IntelligenceStore(
            self.root / f"masked-invalid-{case_name}",
            historical_cutoff="2026-07-17T23:59:59+08:00",
            next_market_open_resolver=self.calendar("2023-06-22"),
        )
        initial = self.backfill(
            client,
            store=store,
            page_size=2,
            verification_rounds=1,
        )
        first = initial.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=8,
            resume=True,
        )
        self.assertEqual(first["status"], "complete")
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE backfill_partitions
                SET status=?, error=?,
                    completion_strategy_version=?
                WHERE source='tushare_announcement'
                  AND partition_start='2023-06-21'
                  AND partition_end='2023-06-21'
                  AND probe_manifest_version>=1
                """,
                (
                    descendant_status,
                    descendant_error,
                    descendant_strategy,
                ),
            )
            connection.execute(
                """
                UPDATE backfill_job_partition_refs
                SET evidence_status='exact'
                """
            )
            connection.execute(
                """
                UPDATE backfill_jobs
                SET status='complete', evidence_status='current'
                """
            )
        client.calls.clear()
        recovery = self.backfill(
            client,
            store=store,
            page_size=recovery_page_size,
            verification_rounds=1,
        )
        return client, store, recovery

    def _assert_masked_invalid_probe_descendant_recovers(
        self,
        *,
        descendant_status: str,
        descendant_error: str,
        descendant_strategy: int,
        recovery_page_size: int,
        case_name: str,
    ) -> None:
        client, store, coordinator = (
            self._masked_invalid_probe_descendant(
                descendant_status=descendant_status,
                descendant_error=descendant_error,
                descendant_strategy=descendant_strategy,
                recovery_page_size=recovery_page_size,
                case_name=case_name,
            )
        )

        result = coordinator.run(
            start_date=date(2023, 6, 20),
            end_date=date(2023, 6, 21),
            max_partitions=6,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertGreater(len(client.calls), 0)
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement",
                "2023-06-20",
                "2023-06-21",
            )["status"],
            "complete",
        )
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
            )["status"],
            "complete",
        )
        with store.connect() as connection:
            recovered_job = connection.execute(
                """
                SELECT status, evidence_status
                FROM backfill_jobs
                WHERE request_limit=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (recovery_page_size,),
            ).fetchone()
        self.assertEqual(tuple(recovered_job), ("complete", "current"))

    def test_masked_invalid_probe_descendant_exact_resume_matrix(
        self,
    ) -> None:
        cases = (
            ("failed_retryable", "opaque_retry_state", 3),
            ("failed_terminal", "opaque_terminal_state", 3),
            (
                "failed_overflow",
                "security_items_incomplete",
                3,
            ),
            ("complete", "opaque_stale_strategy", 0),
        )
        for status, error, strategy in cases:
            with self.subTest(
                status=status,
                error=error,
                strategy=strategy,
            ):
                self._assert_masked_invalid_probe_descendant_recovers(
                    descendant_status=status,
                    descendant_error=error,
                    descendant_strategy=strategy,
                    recovery_page_size=2,
                    case_name=f"exact-{status}-{strategy}",
                )

    def test_masked_invalid_probe_descendant_limit_upgrade_matrix(
        self,
    ) -> None:
        cases = (
            ("failed_retryable", "opaque_retry_state", 3),
            ("failed_terminal", "opaque_terminal_state", 3),
            (
                "failed_overflow",
                "security_items_incomplete",
                3,
            ),
            ("complete", "opaque_stale_strategy", 0),
        )
        for status, error, strategy in cases:
            with self.subTest(
                status=status,
                error=error,
                strategy=strategy,
            ):
                self._assert_masked_invalid_probe_descendant_recovers(
                    descendant_status=status,
                    descendant_error=error,
                    descendant_strategy=strategy,
                    recovery_page_size=3,
                    case_name=f"upgrade-{status}-{strategy}",
                )

    def test_catalog_growth_does_not_reopen_short_leaf(self) -> None:
        client = FakeTushareClient({
            ("20230621", "20230621", ""): pd.DataFrame([
                announcement("short-1", ann_date="20230621"),
            ]),
        })
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )
        first = self.backfill(
            client,
            store=store,
            page_size=3,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )
        store.insert_document(SourceDocument(
            source="tushare_announcement",
            source_id="short-leaf-new-code",
            title="新增代码",
            published_at="2023-06-21T01:30:00+00:00",
            first_seen_at="2026-07-25T01:00:00+00:00",
            effective_at="2023-06-21T01:30:00+00:00",
            source_url="https://example.test/short-new.pdf",
            content=b"short-leaf-new-code",
            metadata={
                "security_links": [{
                    "ts_code": "833429.BJ",
                    "name": "康比特",
                }],
            },
        ))
        call_count = len(client.calls)

        second = self.backfill(
            client,
            store=store,
            page_size=4,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(first["status"], "complete")
        self.assertEqual(second["status"], "complete")
        self.assertEqual(len(client.calls), call_count)
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
            )["probe_manifest_version"],
            0,
        )

    def test_complete_backfill_job_cannot_be_downgraded_by_stale_worker(self) -> None:
        store = self.store()
        job = store.ensure_backfill_job(
            "tushare_announcement",
            start_date="2023-06-21",
            end_date="2023-06-21",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        claim = store.start_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            resume=True,
            request_limit=2,
            job_id=str(job["job_id"]),
            job_generation=int(job["generation"]),
        )
        store.finish_backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
            generation=int(claim["generation"]),
            status="complete",
        )

        self.assertEqual(
            store.finish_backfill_job(
                str(job["job_id"]),
                generation=int(job["generation"]),
                status="complete",
            ),
            "complete",
        )
        newer = store.ensure_backfill_job(
            "tushare_announcement",
            start_date="2023-06-21",
            end_date="2023-06-21",
            config_hash="config-a",
            request_limit=2,
            verification_required=2,
        )
        with self.assertRaisesRegex(
            BackfillGenerationConflict,
            "job_finish_conflict",
        ):
            store.finish_backfill_job(
                str(job["job_id"]),
                generation=int(job["generation"]),
                status="partial",
            )

        with store.connect() as connection:
            status = connection.execute(
                """
                SELECT status FROM backfill_jobs WHERE job_id=?
                """,
                (str(job["job_id"]),),
            ).fetchone()["status"]
        self.assertEqual(status, "running")
        self.assertGreater(
            int(newer["generation"]),
            int(job["generation"]),
        )

    def test_distinct_backfill_jobs_refresh_announcement_universe(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement("probe-day-1", ann_date="20230621"),
                ]),
                ("20230621", "20230621", "000001.SZ"): pd.DataFrame(),
                ("20230622", "20230622", ""): pd.DataFrame([
                    announcement("probe-day-2", ann_date="20230622"),
                ]),
                ("20230622", "20230622", "000001.SZ"): pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ")],
                "D": [],
                "P": [],
                "G": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22", "2023-06-23")
        )
        coordinator = self.backfill(
            client,
            store=store,
            page_size=1,
        )

        first = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=3,
            resume=True,
        )
        stock_calls = len(client.stock_basic_calls)
        fund_calls = len(client.fund_basic_calls)
        second = coordinator.run(
            start_date=date(2023, 6, 22),
            end_date=date(2023, 6, 22),
            max_partitions=3,
            resume=True,
        )

        self.assertEqual(first["status"], "complete")
        self.assertEqual(second["status"], "complete")
        self.assertGreater(len(client.stock_basic_calls), stock_calls)
        self.assertGreater(len(client.fund_basic_calls), fund_calls)
        first_binding = store.backfill_universe_for_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        second_binding = store.backfill_universe_for_partition(
            "tushare_announcement",
            "2023-06-22",
            "2023-06-22",
        )
        self.assertNotEqual(
            first_binding["snapshot_id"],
            second_binding["snapshot_id"],
        )

    def test_unknown_probe_security_expands_catalog_and_completes(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement(
                        "known-fund",
                        ann_date="20230621",
                        ts_code="516390.SH",
                    ),
                    announcement(
                        "unknown-security",
                        ann_date="20230621",
                        ts_code="999999.SH",
                    ),
                ]),
            },
            universe={"L": [], "D": [], "P": []},
            fund_universe={
                "L": [fund_row("516390.SH")],
                "D": [],
                "I": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=10,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(partition["status"], "complete")
        self.assertEqual(
            {row["source_id"] for row in store.backfill_probe_documents(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
            )},
            {"known-fund", "unknown-security"},
        )
        self.assertEqual(
            set(store.documents()["source_id"]),
            {"known-fund", "unknown-security"},
        )
        self.assertEqual(
            {
                str(call["ts_code"])
                for call in client.calls
                if call.get("ts_code")
            },
            {"516390.SH", "999999.SH"},
        )
        self.assertEqual(
            {
                row["ts_code"]
                for row in store.announcement_security_catalog(
                    "tushare_announcement"
                )
            },
            {"516390.SH", "999999.SH"},
        )

    def test_same_probe_document_still_validates_every_security_code(self) -> None:
        shared_url = "https://example.test/shared-announcement.pdf"
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement(
                        "shared-known",
                        ann_date="20230621",
                        ts_code="516390.SH",
                        url=shared_url,
                    ),
                    announcement(
                        "shared-unknown",
                        ann_date="20230621",
                        ts_code="999999.SH",
                        url=shared_url,
                    ),
                ]),
            },
            universe={"L": [], "D": [], "P": []},
            fund_universe={
                "L": [fund_row("516390.SH")],
                "D": [],
                "I": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=10,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(partition["status"], "complete")
        self.assertEqual(
            {
                row["ts_code"]
                for row in store.backfill_probe_documents(
                    "tushare_announcement",
                    "2023-06-21",
                    "2023-06-21",
                )
            },
            {"516390.SH", "999999.SH"},
        )
        document_id = int(store.documents().iloc[0]["id"])
        self.assertEqual(
            {
                row["ts_code"]
                for row in store.document_security_links(document_id)
            },
            {"516390.SH", "999999.SH"},
        )

    def test_universe_snapshot_is_not_rebuilt_when_resume_reaches_items(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement("probe-1", ann_date="20230621"),
                    announcement("probe-2", ann_date="20230621"),
                ]),
                ("20230621", "20230621", "000001.SZ"): pd.DataFrame(),
                ("20230621", "20230621", "600000.SH"): pd.DataFrame(),
            },
            universe={
                "L": [basic_row("000001.SZ"), basic_row("600000.SH")],
                "D": [],
                "P": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22", "2023-06-23")
        )
        coordinator = self.backfill(client, store=store)

        coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )
        first_snapshot = store.backfill_universe_for_partition(
            "tushare_announcement", "2023-06-21", "2023-06-21"
        )
        client.universe = {
            "L": [basic_row("300001.SZ")],
            "D": [],
            "P": [],
        }
        stock_basic_call_count = len(client.stock_basic_calls)

        result = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=3,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(client.stock_basic_calls), stock_basic_call_count)
        self.assertEqual(
            store.backfill_universe_for_partition(
                "tushare_announcement", "2023-06-21", "2023-06-21"
            )["snapshot_id"],
            first_snapshot["snapshot_id"],
        )
        self.assertEqual(
            [item["ts_code"] for item in store.backfill_partition_items(
                "tushare_announcement", "2023-06-21", "2023-06-21"
            )],
            ["000001.SZ", "600000.SH"],
        )

    def test_stock_basic_provider_cap_is_fail_closed(self) -> None:
        client = FakeTushareClient(
            {},
            universe={
                "L": [
                    basic_row(f"{index:06d}.SZ")
                    for index in range(6000)
                ],
                "D": [],
                "P": [],
            },
        )
        coordinator = self.backfill(
            client,
            store=self.store(
                resolver=self.calendar("2023-06-22")
            ),
        )

        with self.assertRaisesRegex(
            TushareTerminalError,
            "tushare_stock_basic_saturated:L",
        ):
            coordinator._load_universe_codes()

        self.assertEqual(client.stock_basic_calls[0]["limit"], 6000)

    def test_fund_basic_provider_cap_is_fail_closed(self) -> None:
        client = FakeTushareClient(
            {},
            fund_universe={
                "L": [
                    fund_row(f"{510000 + index:06d}.SH")
                    for index in range(15000)
                ],
                "D": [],
                "I": [],
            },
        )
        coordinator = self.backfill(
            client,
            store=self.store(
                resolver=self.calendar("2023-06-22")
            ),
        )

        with self.assertRaisesRegex(
            TushareTerminalError,
            "tushare_fund_basic_saturated:L",
        ):
            coordinator._load_universe_members()

        self.assertEqual(client.fund_basic_calls[0]["limit"], 15000)

    def test_saturated_security_item_remains_failed_overflow(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement("probe-1", ann_date="20230621"),
                    announcement("probe-2", ann_date="20230621"),
                ]),
                ("20230621", "20230621", "000001.SZ"): pd.DataFrame([
                    announcement("item-1", ann_date="20230621"),
                    announcement("item-2", ann_date="20230621"),
                ]),
            },
            universe={"L": [basic_row("000001.SZ")], "D": [], "P": []},
        )
        store = self.store(
            resolver=self.calendar("2023-06-22", "2023-06-23")
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=5,
            resume=True,
        )

        self.assertEqual(result["status"], "partial")
        item = store.backfill_partition_items(
            "tushare_announcement", "2023-06-21", "2023-06-21"
        )[0]
        self.assertEqual(item["status"], "failed_overflow")
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement", "2023-06-21", "2023-06-21"
            )["status"],
            "failed_overflow",
        )
        self.assertEqual(
            set(store.documents()["source_id"]),
            {"probe-1", "probe-2"},
        )

    def test_failed_item_can_reopen_explicitly_or_when_limit_changes(self) -> None:
        responses = {
            ("20230621", "20230621", ""): pd.DataFrame([
                announcement("probe-1", ann_date="20230621"),
                announcement("probe-2", ann_date="20230621"),
            ]),
            ("20230621", "20230621", "000001.SZ"): pd.DataFrame([
                announcement("item-1", ann_date="20230621"),
                announcement("item-2", ann_date="20230621"),
            ]),
        }
        client = FakeTushareClient(
            responses,
            universe={"L": [basic_row("000001.SZ")], "D": [], "P": []},
        )
        store = self.store(
            resolver=self.calendar("2023-06-22", "2023-06-23")
        )
        self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=5,
            resume=True,
        )

        client.responses[
            ("20230621", "20230621", "000001.SZ")
        ] = pd.DataFrame([announcement("item-1", ann_date="20230621")])
        client.responses[
            ("20230621", "20230621", "")
        ] = pd.DataFrame([
            announcement("probe-1", ann_date="20230621"),
            announcement("probe-2", ann_date="20230621"),
            announcement("item-1", ann_date="20230621"),
        ])
        result = self.backfill(
            client,
            store=store,
            page_size=3,
        ).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=3,
            resume=True,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            set(store.documents()["source_id"]),
            {"probe-1", "probe-2", "item-1"},
        )

    def test_failed_item_reopens_with_resume_false_at_same_limit(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement("probe-1", ann_date="20230621"),
                    announcement("probe-2", ann_date="20230621"),
                ]),
                ("20230621", "20230621", "000001.SZ"): pd.DataFrame([
                    announcement("item-1", ann_date="20230621"),
                    announcement("item-2", ann_date="20230621"),
                ]),
            },
            universe={"L": [basic_row("000001.SZ")], "D": [], "P": []},
        )
        store = self.store(
            resolver=self.calendar("2023-06-22", "2023-06-23")
        )
        coordinator = self.backfill(client, store=store)
        coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=5,
            resume=True,
        )
        client.responses[
            ("20230621", "20230621", "000001.SZ")
        ] = pd.DataFrame([announcement("item-1", ann_date="20230621")])

        result = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=3,
            resume=False,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            set(store.documents()["source_id"]),
            {"probe-1", "probe-2", "item-1"},
        )

    def test_backfill_never_reads_or_writes_live_cursor(self) -> None:
        store = self.store(
            resolver=self.calendar("2026-01-05", "2026-02-02", "2026-03-02")
        )
        store.start_run("live", "tushare_announcement")
        store.finish_run(
            "live",
            status="success",
            cursor="2026-07-24T07:25:00+00:00",
            fetched=1,
        )
        original_live_cursor = store.cursor("tushare_announcement")
        client = FakeTushareClient({})
        coordinator = self.backfill(client, store=store)
        original_cursor_method = store.cursor
        store.cursor = Mock(side_effect=AssertionError("backfill read live cursor"))

        result = coordinator.run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            max_partitions=3,
            resume=True,
        )

        store.cursor = original_cursor_method
        self.assertEqual(store.cursor("tushare_announcement"), original_live_cursor)
        self.assertEqual(store.backfill_partition_count(status="complete"), 3)
        self.assertTrue(result["live_cursor_unchanged"])

    def test_retryable_supplier_failure_opens_circuit_for_remaining_partitions(self) -> None:
        client = FakeTushareClient({
            ("20260101", "20260131", ""): TushareRetryableError(
                "provider unavailable"
            ),
        })
        store = self.store(
            resolver=self.calendar("2026-01-05", "2026-02-02", "2026-03-02")
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            max_partitions=3,
            resume=True,
        )

        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement", "2026-01-01", "2026-01-31"
            )["status"],
            "failed_retryable",
        )
        self.assertEqual(store.backfill_partition_count(), 1)

    def test_terminal_supplier_failure_is_classified_and_stops_run(self) -> None:
        client = FakeTushareClient({
            ("20260101", "20260131", ""): TushareTerminalError(
                "permission denied"
            ),
        })
        store = self.store(resolver=self.calendar("2026-01-05"))

        self.backfill(client, store=store).run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 28),
            max_partitions=2,
            resume=True,
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            store.backfill_partition(
                "tushare_announcement", "2026-01-01", "2026-01-31"
            )["status"],
            "failed_terminal",
        )

    def test_retryable_error_persistence_redacts_sensitive_values(self) -> None:
        client = FakeTushareClient({
            ("20260101", "20260131", ""): TushareRetryableError(
                "provider rejected secret-token-value"
            ),
        })
        store = self.store(resolver=self.calendar("2026-01-05"))

        self.backfill(
            client,
            store=store,
            sensitive_values=("secret-token-value",),
        ).run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            max_partitions=1,
            resume=True,
        )

        partition = store.backfill_partition(
            "tushare_announcement", "2026-01-01", "2026-01-31"
        )
        self.assertNotIn("secret-token-value", partition["error"])
        self.assertIn("[REDACTED]", partition["error"])

    def test_complete_partition_is_skipped_without_request(self) -> None:
        client = FakeTushareClient({})
        store = self.store(resolver=self.calendar("2026-02-02"))
        coordinator = self.backfill(client, store=store)
        kwargs = {
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 1, 31),
            "max_partitions": 1,
            "resume": True,
        }
        coordinator.run(**kwargs)
        client.calls.clear()

        result = coordinator.run(**kwargs)

        self.assertEqual(client.calls, [])
        self.assertEqual(result["status"], "complete")

    def test_leaf_transaction_failure_rolls_back_documents_and_statistics(self) -> None:
        client = FakeTushareClient({
            ("20260105", "20260105", ""): pd.DataFrame([
                announcement("atomic"),
            ]),
        })
        store = self.store(
            resolver=self.calendar("2026-01-06"),
            store_type=FailingLeafStore,
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(store.documents().empty)
        partition = store.backfill_partition(
            "tushare_announcement", "2026-01-05", "2026-01-05"
        )
        self.assertEqual(partition["status"], "failed_retryable")
        self.assertEqual(partition["fetched"], 0)
        self.assertEqual(partition["inserted"], 0)

    def test_probe_transaction_failure_rolls_back_documents_manifest_and_statistics(self) -> None:
        client = FakeTushareClient({
            ("20230621", "20230621", ""): pd.DataFrame([
                announcement("probe-atomic-1", ann_date="20230621"),
                announcement("probe-atomic-2", ann_date="20230621"),
            ]),
        })
        store = self.store(
            resolver=self.calendar("2023-06-22"),
            store_type=FailingProbeStore,
        )

        result = self.backfill(client, store=store).run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["status"], "partial")
        self.assertTrue(store.documents().empty)
        self.assertEqual(
            store.backfill_probe_documents(
                "tushare_announcement",
                "2023-06-21",
                "2023-06-21",
            ),
            [],
        )
        partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(partition["status"], "failed_retryable")
        self.assertEqual(partition["probe_manifest_version"], 0)
        self.assertEqual(partition["fetched"], 0)
        self.assertEqual(partition["inserted"], 0)

    def test_parent_completion_rejects_probe_document_hash_drift(self) -> None:
        client = FakeTushareClient(
            {
                ("20230621", "20230621", ""): pd.DataFrame([
                    announcement(
                        "probe-a",
                        ann_date="20230621",
                        ts_code="000001.SZ",
                    ),
                    announcement(
                        "probe-b",
                        ann_date="20230621",
                        ts_code="600000.SH",
                    ),
                ]),
                ("20230621", "20230621", "000001.SZ"): pd.DataFrame([
                    announcement(
                        "item-a",
                        ann_date="20230621",
                        ts_code="000001.SZ",
                    ),
                ]),
                ("20230621", "20230621", "600000.SH"): pd.DataFrame([
                    announcement(
                        "item-b",
                        ann_date="20230621",
                        ts_code="600000.SH",
                    ),
                ]),
            },
            universe={
                "L": [
                    basic_row("000001.SZ"),
                    basic_row("600000.SH"),
                ],
                "D": [],
                "P": [],
            },
        )
        store = self.store(
            resolver=self.calendar("2023-06-22")
        )
        coordinator = self.backfill(client, store=store)

        first = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=2,
            resume=True,
        )
        self.assertEqual(first["status"], "partial")
        probe = store.backfill_probe_documents(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )[0]
        with store.connect() as connection:
            connection.execute(
                "UPDATE documents SET content_hash=? WHERE id=?",
                ("tampered-content-hash", int(probe["document_id"])),
            )

        resumed = coordinator.run(
            start_date=date(2023, 6, 21),
            end_date=date(2023, 6, 21),
            max_partitions=2,
            resume=True,
        )

        self.assertEqual(resumed["status"], "partial")
        partition = store.backfill_partition(
            "tushare_announcement",
            "2023-06-21",
            "2023-06-21",
        )
        self.assertEqual(partition["status"], "failed_overflow")
        self.assertEqual(partition["error"], "security_items_incomplete")

    def test_rec_time_before_cutoff_reconstructs_exact_availability(self) -> None:
        client = FakeTushareClient({
            ("20260105", "20260105", ""): pd.DataFrame([
                announcement("precise", rec_time="2026-01-05 10:15:03"),
            ]),
        })
        store = self.store(resolver=self.calendar("2026-01-06"))

        self.backfill(client, store=store).run(
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            max_partitions=1,
            resume=True,
        )

        document_id = int(store.documents().iloc[0]["id"])
        availability = store.document_availability(document_id)
        self.assertEqual(
            availability["availability_provenance"],
            "reconstructed_rec_time",
        )
        self.assertEqual(
            availability["research_available_at"],
            "2026-01-05T02:15:03+00:00",
        )

    def test_date_only_uses_next_real_trading_day_at_0930_shanghai(self) -> None:
        client = FakeTushareClient({
            ("20260102", "20260102", ""): pd.DataFrame([
                announcement("date-only", ann_date="20260102", rec_time=None),
            ]),
        })
        store = self.store(
            resolver=self.calendar("2026-01-02", "2026-01-05")
        )

        self.backfill(client, store=store).run(
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 2),
            max_partitions=1,
            resume=True,
        )

        availability = store.document_availability(
            int(store.documents().iloc[0]["id"])
        )
        self.assertEqual(
            availability["availability_provenance"],
            "reconstructed_next_open",
        )
        self.assertEqual(
            availability["research_available_at"],
            "2026-01-05T01:30:00+00:00",
        )

    def test_full_history_start_date_preserves_1990_publication_time(self) -> None:
        client = FakeTushareClient({
            ("19901219", "19901219", ""): pd.DataFrame([
                announcement(
                    "market-open-era",
                    ann_date="19901219",
                    rec_time=None,
                ),
            ]),
        })
        store = self.store(
            resolver=self.calendar("1990-12-19", "1990-12-20")
        )

        self.backfill(client, store=store).run(
            start_date=date(1990, 12, 19),
            end_date=date(1990, 12, 19),
            max_partitions=1,
            resume=True,
        )

        document = store.documents().iloc[0]
        self.assertEqual(
            document["published_at"],
            "1990-12-18T16:00:00+00:00",
        )
        availability = store.document_availability(int(document["id"]))
        self.assertEqual(
            availability["research_available_at"],
            "1990-12-20T01:30:00+00:00",
        )

    def test_calendar_fetch_is_year_partitioned_and_validated(self) -> None:
        client = FakeTushareClient({})
        client.open_dates = ["20251231", "20260105"]

        resolver = TushareTradingCalendarResolver.from_tushare(
            client,
            start_date=date(2025, 12, 30),
            end_date=date(2026, 1, 6),
        )

        self.assertEqual(
            resolver("2025-12-31T08:00:00+00:00"),
            "2026-01-05T01:30:00+00:00",
        )
        self.assertEqual(
            [
                (call["start_date"], call["end_date"])
                for call in client.trade_cal_calls
            ],
            [
                ("20251230", "20251231"),
                ("20260101", "20260106"),
            ],
        )
        self.assertTrue(
            all("is_open" not in call for call in client.trade_cal_calls)
        )
        self.assertTrue(
            all(
                call["fields"] == "cal_date,is_open"
                for call in client.trade_cal_calls
            )
        )

    def test_calendar_rejects_partial_empty_or_missing_columns(self) -> None:
        empty_year = FakeTushareClient({})
        empty_year.open_dates = ["20251231", "20260105"]
        empty_year.empty_calendar_years = {2025}
        with self.assertRaisesRegex(
            ValueError,
            "trade_cal_chunk_empty:2025",
        ):
            TushareTradingCalendarResolver.from_tushare(
                empty_year,
                start_date=date(2025, 12, 30),
                end_date=date(2026, 1, 6),
            )

        missing_columns = Mock()
        missing_columns.trade_cal.return_value = pd.DataFrame([
            {"cal_date": "20260105"},
        ])
        with self.assertRaisesRegex(
            ValueError,
            "trade_cal_columns_invalid",
        ):
            TushareTradingCalendarResolver.from_tushare(
                missing_columns,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 6),
            )

    def test_calendar_accepts_1999_spring_festival_long_closure(self) -> None:
        client = FakeTushareClient({})
        client.open_dates = ["19990209", "19990301"]

        resolver = TushareTradingCalendarResolver.from_tushare(
            client,
            start_date=date(1999, 2, 9),
            end_date=date(1999, 3, 1),
        )

        self.assertEqual(
            resolver("1999-02-09T08:00:00+08:00"),
            "1999-03-01T01:30:00+00:00",
        )

    def test_calendar_rejects_unreasonable_multi_year_closed_span(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "trade_cal_next_open_gap_too_large",
        ):
            TushareTradingCalendarResolver(
                (
                    date(1990, 12, 20),
                    date(2026, 1, 5),
                ),
                coverage_start=date(1990, 12, 19),
                coverage_end=date(2026, 1, 5),
                max_next_open_gap_days=45,
            )

    def test_calendar_missing_one_natural_day_fails_closed(self) -> None:
        client = FakeTushareClient({})
        client.open_dates = ["20260105"]
        client.missing_calendar_dates = {"20260103"}

        with self.assertRaisesRegex(
            ValueError,
            "trade_cal_natural_day_gap:20260103",
        ):
            TushareTradingCalendarResolver.from_tushare(
                client,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 6),
            )

    def test_calendar_cache_reuses_days_and_fetches_only_missing_gap(self) -> None:
        cache_path = self.root / "calendar" / "sse.csv"
        first = FakeTushareClient({})
        first.open_dates = ["20260105", "20260109"]
        TushareTradingCalendarResolver.from_tushare(
            first,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            cache_path=cache_path,
        )
        self.assertEqual(len(first.trade_cal_calls), 1)
        self.assertTrue(cache_path.exists())

        resumed = FakeTushareClient({})
        resumed.open_dates = ["20260112"]
        TushareTradingCalendarResolver.from_tushare(
            resumed,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 12),
            cache_path=cache_path,
        )

        self.assertEqual(
            [
                (call["start_date"], call["end_date"])
                for call in resumed.trade_cal_calls
            ],
            [("20260111", "20260112")],
        )

    def test_calendar_rejects_out_of_coverage_boundaries(self) -> None:
        resolver = TushareTradingCalendarResolver(
            (date(2026, 1, 5),),
            coverage_start=date(2026, 1, 1),
            coverage_end=date(2026, 1, 6),
        )
        with self.assertRaisesRegex(
            ValueError,
            "trade_cal_published_out_of_coverage",
        ):
            resolver("2025-12-31T08:00:00+00:00")
        with self.assertRaisesRegex(
            ValueError,
            "trade_cal_next_open_missing",
        ):
            resolver("2026-01-06T00:00:00+08:00")

    def test_post_cutoff_document_remains_observed(self) -> None:
        client = FakeTushareClient({
            ("20260720", "20260720", ""): pd.DataFrame([
                announcement(
                    "post-cutoff",
                    ann_date="20260720",
                    rec_time="2026-07-20 09:35:00",
                ),
            ]),
        })
        store = self.store(resolver=self.calendar("2026-07-21"))

        self.backfill(client, store=store).run(
            start_date=date(2026, 7, 20),
            end_date=date(2026, 7, 20),
            max_partitions=1,
            resume=True,
        )

        availability = store.document_availability(
            int(store.documents().iloc[0]["id"])
        )
        self.assertEqual(availability["availability_provenance"], "observed")

    def test_b_share_filter_count_is_persisted_and_reported(self) -> None:
        client = FakeTushareClient({
            ("20260101", "20260131", ""): pd.DataFrame([
                announcement("sz-b", ts_code="200001.SZ"),
                announcement("sh-b", ts_code="900901.SH"),
                announcement("a", ts_code="600000.SH"),
            ]),
        })
        store = self.store(resolver=self.calendar("2026-01-05"))

        result = self.backfill(
            client,
            store=store,
            page_size=10,
        ).run(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            max_partitions=1,
            resume=True,
        )

        self.assertEqual(result["b_share_filtered"], 2)
        partition = store.backfill_partition(
            "tushare_announcement", "2026-01-01", "2026-01-31"
        )
        self.assertEqual(partition["b_share_filtered"], 2)
        self.assertEqual(len(store.documents()), 1)


if __name__ == "__main__":
    unittest.main()
