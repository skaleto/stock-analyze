from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import yaml

from stock_analyze.intelligence.http import IntelligenceHttpClient
from stock_analyze.intelligence.source_registry import build_adapters
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.sources.official import (
    NdrcApiAdapter,
    OfficialHtmlAdapter,
    TushareAnnouncementAdapter,
)


class IntelligenceSourceTest(unittest.TestCase):
    def test_unimplemented_contract_sources_are_disabled_in_production(
        self,
    ) -> None:
        config = yaml.safe_load(
            Path("configs/intelligence_sources.yaml").read_text(
                encoding="utf-8"
            )
        )

        contract_sources = {
            name: source
            for name, source in config["sources"].items()
            if source["type"] == "contract_only"
        }

        self.assertTrue(contract_sources)
        self.assertTrue(
            all(not source["enabled"] for source in contract_sources.values())
        )

    def test_http_rejects_non_allowlisted_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = IntelligenceHttpClient(allowed_hosts={"allowed.test"}, cache_dir=tmp)
            with self.assertRaisesRegex(ValueError, "host_not_allowed"):
                client.get("https://blocked.test/a")

    def test_official_adapter_normalizes_links_and_dates(self) -> None:
        response = Mock()
        response.content = b'<a href="/policy/20260718/a.html">2026-07-18 Industry policy update</a>'
        response.headers = {"Content-Type": "text/html"}
        client = Mock()
        client.get.return_value = response
        adapter = OfficialHtmlAdapter(
            "policy", ("https://official.test/list",), client,
            include_path=r"/policy/",
        )
        result = adapter.fetch_since("2026-07-17T00:00:00Z", "2026-07-19T00:00:00Z")
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.documents[0].source_url, "https://official.test/policy/20260718/a.html")
        self.assertTrue(result.documents[0].published_at.startswith("2026-07-18"))

    def test_official_adapter_skips_documents_without_authoritative_publish_time(self) -> None:
        response = Mock()
        response.content = b'<a href="/policy/article.html">Industry policy update</a>'
        response.headers = {"Content-Type": "text/html"}
        client = Mock()
        client.get.return_value = response
        result = OfficialHtmlAdapter(
            "policy", ("https://official.test/list",), client, include_path=r"/policy/",
        ).fetch_since("2026-07-17T00:00:00Z", "2026-07-19T00:00:00Z")
        self.assertEqual(result.documents, ())
        self.assertIn("publish_time_missing", result.warnings[0])

    def test_official_adapter_reads_government_first_published_meta(self) -> None:
        listing = Mock()
        listing.content = b'<a href="/zhengce/content/a.htm">Industry policy update</a>'
        listing.headers = {"Content-Type": "text/html"}
        listing.url = "https://www.gov.cn/zhengce/"
        detail = Mock()
        detail.content = (
            b'<meta name="firstpublishedtime" content="2026-07-13-17:19:00">'
            b'<article>policy</article>'
        )
        detail.headers = {"Content-Type": "text/html"}
        detail.url = "https://www.gov.cn/zhengce/content/a.htm"
        client = Mock()
        client.get.side_effect = [listing, detail]
        result = OfficialHtmlAdapter(
            "gov_policy", ("https://www.gov.cn/zhengce/",), client,
            include_path=r"/zhengce/",
        ).fetch_since("2026-07-01T00:00:00Z", "2026-07-19T00:00:00Z")
        self.assertEqual(len(result.documents), 1)
        self.assertTrue(result.documents[0].published_at.startswith("2026-07-13"))

    def test_official_adapter_rejects_detail_redirect_to_home(self) -> None:
        listing = Mock()
        listing.content = b'<a href="/csrc/c100028/old.htm">Industry policy update</a>'
        listing.headers = {"Content-Type": "text/html"}
        listing.url = "https://www.csrc.gov.cn/csrc/list.htm"
        detail = Mock()
        detail.content = b'<meta name="publishdate" content="2026-07-18">'
        detail.headers = {"Content-Type": "text/html"}
        detail.url = "https://www.csrc.gov.cn/"
        client = Mock()
        client.get.side_effect = [listing, detail]
        result = OfficialHtmlAdapter(
            "csrc_policy", (listing.url,), client, include_path=r"/csrc/",
        ).fetch_since("2026-07-01T00:00:00Z", "2026-07-19T00:00:00Z")
        self.assertEqual(result.documents, ())
        self.assertIn("detail_redirected_to_home", result.warnings[0])

    def test_ndrc_api_adapter_paginates_official_results(self) -> None:
        first = Mock()
        first.json.return_value = {
            "code": 200,
            "data": {
                "totalHits": 2,
                "resultList": [{
                    "reference": "a", "docDate": "2026-07-18", "title": "Industry policy A",
                    "url": "https://www.ndrc.gov.cn/a", "summary": "support advanced manufacturing",
                }],
            },
        }
        second = Mock()
        second.json.return_value = {
            "code": 200,
            "data": {
                "totalHits": 2,
                "resultList": [{
                    "reference": "b", "docDate": "2026-07-17", "title": "Industry policy B",
                    "url": "https://www.ndrc.gov.cn/b",
                    "myValues": {"QUICKDESCRIPTION": "restrict obsolete capacity"},
                }],
            },
        }
        client = Mock()
        client.get.side_effect = [first, second]
        result = NdrcApiAdapter(
            client, endpoint="https://fwfx.ndrc.gov.cn/api/query",
            site_code="site", api_key="key", page_size=1,
        ).fetch_since("2026-07-16T00:00:00Z", "2026-07-19T00:00:00Z")
        self.assertEqual([document.source_id for document in result.documents], ["a", "b"])
        self.assertEqual(client.get.call_count, 2)

    def test_tushare_entitlement_is_fail_closed(self) -> None:
        client = Mock()
        result = TushareAnnouncementAdapter(client, enabled=False).fetch_since("", "2026-07-18T00:00:00Z")
        client.anns_d.assert_not_called()
        self.assertIn("entitlement_disabled", result.warnings[0])

    def test_tushare_rows_preserve_security_metadata(self) -> None:
        client = Mock()
        client.anns_d.side_effect = [
            pd.DataFrame([
                {
                    "ts_code": "000001.SZ",
                    "ann_date": "20260718",
                    "rec_time": "2026-07-18 09:31:45",
                    "title": "Buyback plan",
                    "url": (
                        "http://www.cninfo.com.cn/new/disclosure/detail?"
                        "stockCode=000001&announcementId=1212345678"
                    ),
                }
            ]),
            pd.DataFrame(),
        ]
        result = TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=1,
        ).fetch_since(
            "2026-07-18T12:00:00+08:00", "2026-07-18T23:59:59+08:00"
        )
        self.assertEqual(result.documents[0].metadata["ts_code"], "000001.SZ")
        self.assertEqual(result.documents[0].metadata["ann_date"], "20260718")
        self.assertEqual(
            result.documents[0].metadata["rec_time"],
            "2026-07-18 09:31:45",
        )
        self.assertEqual(result.documents[0].source_id, "1212345678")
        self.assertEqual(result.documents[0].metadata["announcement_id"], "1212345678")
        self.assertEqual(
            result.documents[0].published_at,
            "2026-07-18T01:31:45+00:00",
        )
        self.assertEqual(
            client.anns_d.call_args_list[0].kwargs,
            {
                "ann_date": "20260718",
                "limit": 1,
                "offset": 0,
                "fields": "ann_date,ts_code,name,title,url,rec_time",
            },
        )

    def test_tushare_nan_rec_time_is_stored_as_missing(self) -> None:
        client = Mock()
        client.anns_d.side_effect = [
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "ann_date": "20260729",
                        "rec_time": float("nan"),
                        "title": "Announcement",
                        "url": (
                            "https://www.cninfo.com.cn/detail?"
                            "announcementId=nan-rec-time"
                        ),
                    }
                ]
            ),
            pd.DataFrame(),
        ]

        result = TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=1,
        ).fetch_since(
            "2026-07-29T00:00:00+08:00",
            "2026-07-29T23:59:59+08:00",
        )

        self.assertEqual(result.documents[0].metadata["rec_time"], "")

    def test_tushare_live_saturated_day_keeps_first_page_and_continues_with_offset_zero(self) -> None:
        def row(announcement_id: str, date: str) -> dict:
            return {
                "ts_code": "000001.SZ",
                "ann_date": date,
                "title": f"Announcement {announcement_id}",
                "url": (
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    f"announcementId={announcement_id}"
                ),
            }

        def anns_d(**kwargs):
            if kwargs["ann_date"] == "20260717":
                if kwargs["offset"] == 0:
                    return pd.DataFrame([
                        row("1", "20260717"),
                        row("2", "20260717"),
                    ])
                return pd.DataFrame([row("drift", "20260717")])
            return pd.DataFrame([row("3", "20260718")])

        client = Mock()
        client.anns_d.side_effect = anns_d
        original_cursor = "2026-07-17T12:00:00+08:00"
        result = TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=2,
        ).fetch_since(
            original_cursor, "2026-07-18T23:59:59+08:00"
        )

        self.assertFalse(result.complete)
        self.assertEqual(result.cursor, original_cursor)
        self.assertEqual([item.source_id for item in result.documents], ["1", "2", "3"])
        self.assertEqual(result.warnings, ("day_saturated:20260717",))
        self.assertEqual(
            [call.kwargs for call in client.anns_d.call_args_list],
            [
                {
                    "ann_date": "20260717", "limit": 2, "offset": 0,
                    "fields": "ann_date,ts_code,name,title,url,rec_time",
                },
                {
                    "ann_date": "20260718", "limit": 2, "offset": 0,
                    "fields": "ann_date,ts_code,name,title,url,rec_time",
                },
            ],
        )

    def test_tushare_live_short_page_advances_cursor(self) -> None:
        client = Mock()
        client.anns_d.return_value = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "ann_date": "20260718",
            "title": "Short page",
            "url": "https://x.test/?announcementId=short-page",
        }])

        result = TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=2,
        ).fetch_since(
            "2026-07-18T00:00:00+08:00",
            "2026-07-18T23:59:59+08:00",
        )

        self.assertTrue(result.complete)
        self.assertEqual(result.cursor, "2026-07-18T15:59:59+00:00")
        self.assertEqual(client.anns_d.call_count, 1)
        self.assertEqual(client.anns_d.call_args.kwargs["offset"], 0)

    def test_tushare_empty_cursor_uses_bounded_initial_lookback(self) -> None:
        client = Mock()
        client.anns_d.return_value = pd.DataFrame()
        TushareAnnouncementAdapter(
            client,
            enabled=True,
            initial_lookback_days=2,
        ).fetch_since("", "2026-07-24T08:00:00Z")

        self.assertEqual(
            [call.kwargs["ann_date"] for call in client.anns_d.call_args_list],
            ["20260723", "20260724"],
        )

    def test_tushare_uses_china_calendar_day_near_utc_midnight(self) -> None:
        client = Mock()
        client.anns_d.return_value = pd.DataFrame()
        TushareAnnouncementAdapter(
            client,
            enabled=True,
            initial_lookback_days=1,
        ).fetch_since("", "2026-07-23T16:30:00Z")

        self.assertEqual(
            [call.kwargs["ann_date"] for call in client.anns_d.call_args_list],
            ["20260724"],
        )

    def test_tushare_excludes_b_share_rows_at_ingestion_boundary(self) -> None:
        client = Mock()
        client.anns_d.return_value = pd.DataFrame([
            {
                "ts_code": "200512.SZ",
                "ann_date": "20260724",
                "title": "B share warning",
                "url": "https://x.test/?announcementId=b-share",
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260724",
                "title": "A share announcement",
                "url": "https://x.test/?announcementId=a-share",
            },
            {
                "ts_code": "900901.SH",
                "ann_date": "20260724",
                "title": "Shanghai B share warning",
                "url": "https://x.test/?announcementId=b-share-sh",
            },
        ])
        result = TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=2000,
        ).fetch_since(
            "2026-07-24T00:00:00+08:00",
            "2026-07-24T23:59:59+08:00",
        )

        self.assertEqual([item.source_id for item in result.documents], ["a-share"])
        self.assertEqual(result.metrics["b_share_filtered"], 2)
        self.assertEqual(
            client.anns_d.call_args.kwargs["fields"],
            "ann_date,ts_code,name,title,url,rec_time",
        )

    def test_tushare_fallback_identity_uses_normalized_url_not_rec_time(self) -> None:
        rows = (
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260724",
                "rec_time": None,
                "title": "Annual Report",
                "url": (
                    "HTTPS://STATIC.CNINFO.COM.CN/finalpage/report.PDF?"
                    "b=2&a=1#fragment"
                ),
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260724",
                "rec_time": "2026-07-24 09:31:00",
                "title": "Annual Report",
                "url": (
                    "https://static.cninfo.com.cn/finalpage/report.PDF?"
                    "a=1&b=2"
                ),
            },
        )
        source_ids = []
        for row in rows:
            client = Mock()
            client.anns_d.return_value = pd.DataFrame([row])
            result = TushareAnnouncementAdapter(
                client,
                enabled=True,
                page_size=2,
            ).fetch_since(
                "2026-07-24T00:00:00+08:00",
                "2026-07-24T23:59:59+08:00",
            )
            source_ids.append(result.documents[0].source_id)

        self.assertEqual(source_ids[0], source_ids[1])

    def test_tushare_identity_without_url_uses_code_title_and_ann_date(self) -> None:
        source_ids = []
        for rec_time in (None, "2026-07-24 09:31:00"):
            client = Mock()
            client.anns_d.return_value = pd.DataFrame([{
                "ts_code": "000001.SZ",
                "ann_date": "20260724",
                "rec_time": rec_time,
                "title": "  Annual   Report  ",
                "url": "",
            }])
            result = TushareAnnouncementAdapter(
                client,
                enabled=True,
                page_size=2,
            ).fetch_since(
                "2026-07-24T00:00:00+08:00",
                "2026-07-24T23:59:59+08:00",
            )
            source_ids.append(result.documents[0].source_id)

        self.assertEqual(source_ids[0], source_ids[1])

    def test_same_pdf_url_does_not_duplicate_store_record(self) -> None:
        documents = []
        for rec_time in (None, "2026-07-24 09:31:00"):
            client = Mock()
            client.anns_d.return_value = pd.DataFrame([{
                "ts_code": "000001.SZ",
                "ann_date": "20260724",
                "rec_time": rec_time,
                "title": "Annual Report",
                "url": (
                    "https://static.cninfo.com.cn/finalpage/report.pdf"
                ),
            }])
            documents.append(
                TushareAnnouncementAdapter(
                    client,
                    enabled=True,
                    page_size=2,
                ).fetch_since(
                    "2026-07-24T00:00:00+08:00",
                    "2026-07-24T23:59:59+08:00",
                ).documents[0]
            )

        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            first_id, first_inserted = store.insert_document(documents[0])
            second_id, second_inserted = store.insert_document(documents[1])

        self.assertTrue(first_inserted)
        self.assertFalse(second_inserted)
        self.assertEqual(first_id, second_id)

    def test_shared_url_rows_are_order_independent_and_link_every_security(self) -> None:
        shared_url = "https://static.cninfo.com.cn/finalpage/shared.pdf"
        rows = [
            {
                "ts_code": "600000.SH",
                "name": "浦发银行",
                "ann_date": "20260724",
                "rec_time": "2026-07-24 09:31:00",
                "title": "联合公告",
                "url": shared_url,
            },
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "ann_date": "20260724",
                "rec_time": "2026-07-24 09:31:00",
                "title": "联合公告",
                "url": shared_url,
            },
        ]
        documents = []
        for ordered_rows in (rows, list(reversed(rows))):
            client = Mock()
            client.anns_d.return_value = pd.DataFrame(ordered_rows)
            batch = TushareAnnouncementAdapter(
                client,
                enabled=True,
                page_size=3,
            ).fetch_since(
                "2026-07-24T00:00:00+08:00",
                "2026-07-24T23:59:59+08:00",
            )
            self.assertEqual(len(batch.documents), 1)
            documents.append(batch.documents[0])

        self.assertEqual(documents[0].content, documents[1].content)
        self.assertEqual(documents[0].metadata, documents[1].metadata)
        self.assertEqual(
            documents[0].metadata["security_codes"],
            ["000001.SZ", "600000.SH"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            first_id, first_inserted = store.insert_document(documents[0])
            second_id, second_inserted = store.insert_document(documents[1])
            links = store.document_security_links(first_id)

        self.assertTrue(first_inserted)
        self.assertFalse(second_inserted)
        self.assertEqual(first_id, second_id)
        self.assertEqual(
            [(row["ts_code"], row["name"]) for row in links],
            [
                ("000001.SZ", "平安银行"),
                ("600000.SH", "浦发银行"),
            ],
        )

    def test_same_url_content_identity_is_stable_when_title_changes(self) -> None:
        shared_url = "https://static.cninfo.com.cn/finalpage/stable.pdf"
        documents = []
        for title in ("公告标题乙", "公告标题甲"):
            client = Mock()
            client.anns_d.return_value = pd.DataFrame([{
                "ts_code": "300114.SZ",
                "name": "中航电测",
                "ann_date": "20260724",
                "rec_time": "2026-07-24 09:31:00",
                "title": title,
                "url": shared_url,
            }])
            batch = TushareAnnouncementAdapter(
                client,
                enabled=True,
                page_size=2,
            ).fetch_since(
                "2026-07-24T00:00:00+08:00",
                "2026-07-24T23:59:59+08:00",
            )
            documents.append(batch.documents[0])

        self.assertEqual(
            documents[0].source_id,
            documents[1].source_id,
        )
        self.assertEqual(documents[0].content, documents[1].content)
        self.assertNotEqual(documents[0].title, documents[1].title)
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            first_id, first_inserted = store.insert_document(
                documents[0]
            )
            second_id, second_inserted = store.insert_document(
                documents[1]
            )
        self.assertTrue(first_inserted)
        self.assertFalse(second_inserted)
        self.assertEqual(first_id, second_id)

    def test_tushare_live_saturated_first_page_is_fail_closed(self) -> None:
        frame = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260718",
                "title": "Announcement",
                "url": f"https://x.test/?announcementId={index}",
            }
            for index in range(2)
        ])
        client = Mock()
        client.anns_d.return_value = frame
        result = TushareAnnouncementAdapter(
            client,
            enabled=True,
            page_size=2,
            max_pages_per_day=2,
        ).fetch_since(
            "2026-07-18T00:00:00+08:00", "2026-07-18T23:59:59+08:00"
        )

        self.assertFalse(result.complete)
        self.assertEqual(result.cursor, "2026-07-18T00:00:00+08:00")
        self.assertEqual(result.warnings, ("day_saturated:20260718",))
        self.assertEqual(client.anns_d.call_count, 1)
        self.assertEqual(client.anns_d.call_args.kwargs["offset"], 0)

    def test_tushare_registry_applies_entitled_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text(
                """
                schema_version: 1
                sources:
                  tushare_announcement:
                    type: tushare_announcement
                    enabled: true
                    entitled: true
                    initial_lookback_days: 3
                    page_size: 500
                    max_pages_per_day: 9
                """,
                encoding="utf-8",
            )
            transport = Mock()
            with (
                patch.dict("os.environ", {"TUSHARE_TOKEN": "secret"}),
                patch(
                    "stock_analyze.intelligence.source_registry.TushareProTransport",
                    return_value=transport,
                ) as transport_type,
            ):
                adapter = build_adapters(root, config)[0]

        self.assertEqual(adapter.initial_lookback_days, 3)
        self.assertEqual(adapter.page_size, 500)
        self.assertEqual(adapter.max_pages_per_day, 9)
        self.assertIs(adapter.client, transport)
        transport_type.assert_called_once_with(
            "secret",
            endpoint="https://api.tushare.pro",
        )

    def test_ndrc_registry_accepts_classified_public_search_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text(
                """
                schema_version: 1
                sources:
                  ndrc_policy:
                    type: ndrc_api
                    enabled: true
                    allowed_hosts: [official.test]
                    endpoint: https://official.test/api/query
                    site_code: public-site
                    public_site_key: public-client
                    credential_class: public_client_identifier
                """,
                encoding="utf-8",
            )
            try:
                adapter = build_adapters(root, config)[0]
            except Exception as exc:
                self.fail(f"classified public identifier was rejected: {exc!r}")

        self.assertIsInstance(adapter, NdrcApiAdapter)
        self.assertEqual(adapter.api_key, "public-client")

    def test_ndrc_registry_rejects_unclassified_public_search_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text(
                """
                schema_version: 1
                sources:
                  ndrc_policy:
                    type: ndrc_api
                    enabled: true
                    allowed_hosts: [official.test]
                    endpoint: https://official.test/api/query
                    site_code: public-site
                    public_site_key: public-client
                """,
                encoding="utf-8",
            )
            try:
                build_adapters(root, config)
            except Exception as exc:
                self.assertIsInstance(exc, ValueError)
                self.assertRegex(
                    str(exc),
                    "ndrc_public_site_key_credential_class_invalid",
                )
            else:
                self.fail("unclassified public identifier was accepted")

    def test_contract_only_source_is_observable_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text(
                """
                schema_version: 1
                sources:
                  official_port:
                    type: contract_only
                    enabled: true
                    unavailable_reason: adapter_pending
                """,
                encoding="utf-8",
            )

            adapters = build_adapters(root, config)
            result = adapters[0].fetch_since(
                "2026-07-01T00:00:00Z",
                "2026-07-24T00:00:00Z",
            )

        self.assertEqual(adapters[0].source, "official_port")
        self.assertFalse(result.complete)
        self.assertEqual(result.cursor, "2026-07-01T00:00:00Z")
        self.assertEqual(
            result.warnings,
            ("source_unavailable:adapter_pending",),
        )


if __name__ == "__main__":
    unittest.main()
