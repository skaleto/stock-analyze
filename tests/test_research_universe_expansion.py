"""Research-only universe catalog tests.

The catalog is deliberately separate from the formal competition accounts: it
records provenance and research eligibility, but never exposes an execution
instruction.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from stock_analyze.research.universe_expansion import (
    A_SHARE_INDEXES,
    build_a_share_research_catalog,
    build_fund_research_catalog,
    refresh_research_universes,
)


class AShareResearchCatalogTests(unittest.TestCase):
    def test_keeps_only_latest_available_membership_per_index(self) -> None:
        catalog = build_a_share_research_catalog(
            {
                "hs300": [
                    {"con_code": "000001.SZ", "trade_date": "20260701", "weight": 1.0},
                    {"con_code": "000002.SZ", "trade_date": "20260803", "weight": 1.1},
                ],
                "zz500": [
                    {"con_code": "000003.SZ", "trade_date": "20260803", "weight": 0.4},
                ],
                "csi1000": [
                    {"con_code": "000004.SZ", "trade_date": "20260803", "weight": 0.2},
                    {"con_code": "000005.SZ", "trade_date": "20260901", "weight": 0.3},
                ],
            },
            stock_basics=[
                {"ts_code": "000002.SZ", "name": "万科A"},
                {"ts_code": "000003.SZ", "name": "测试三号"},
                {"ts_code": "000004.SZ", "name": "测试四号"},
            ],
            as_of="20260822",
        )

        self.assertEqual(catalog["schema_version"], "research-universe-catalog-v1")
        self.assertEqual(catalog["as_of"], "20260822")
        self.assertEqual(catalog["summary"]["scope_counts"], {
            "hs300": 1,
            "zz500": 1,
            "csi1000": 1,
        })
        self.assertEqual(
            catalog["summary"]["index_codes"],
            A_SHARE_INDEXES,
        )
        records = {record["ts_code"]: record for record in catalog["records"]}
        self.assertNotIn("000001.SZ", records)
        self.assertEqual(records["000002.SZ"]["membership_date"], "20260803")
        self.assertEqual(records["000002.SZ"]["name"], "万科A")
        self.assertEqual(records["000002.SZ"]["name_source"], "tushare_stock_basic")
        self.assertEqual(records["000004.SZ"]["research_scopes"], ["csi1000"])
        self.assertTrue(all(record["research_only"] for record in records.values()))
        self.assertTrue(all("execution" not in record for record in records.values()))

    def test_rejects_missing_or_future_only_index_membership(self) -> None:
        with self.assertRaisesRegex(ValueError, "a_share_membership_missing:csi1000"):
            build_a_share_research_catalog(
                {
                    "hs300": [{"con_code": "000001.SZ", "trade_date": "20260803"}],
                    "zz500": [{"con_code": "000002.SZ", "trade_date": "20260803"}],
                    "csi1000": [{"con_code": "000003.SZ", "trade_date": "20260901"}],
                },
                stock_basics=[],
                as_of="20260822",
            )

    def test_rejects_catalog_when_a_selected_member_has_no_master_name(self) -> None:
        memberships = {
            "hs300": [{"con_code": "000001.SZ", "trade_date": "20260803"}],
            "zz500": [{"con_code": "000002.SZ", "trade_date": "20260803"}],
            "csi1000": [{"con_code": "000003.SZ", "trade_date": "20260803"}],
        }

        with self.assertRaisesRegex(ValueError, "a_share_name_missing:000003.SZ"):
            build_a_share_research_catalog(
                memberships,
                stock_basics=[
                    {"ts_code": "000001.SZ", "name": "平安银行"},
                    {"ts_code": "000002.SZ", "name": "万科A"},
                    {"ts_code": "000003.SZ", "name": ""},
                ],
                as_of="20260822",
            )


class FundResearchCatalogTests(unittest.TestCase):
    def test_marks_otc_records_non_tradable_and_preserves_evidence(self) -> None:
        catalog = build_fund_research_catalog(
            exchange_basic=[
                {
                    "ts_code": "513100.SH",
                    "name": "纳斯达克100ETF",
                    "status": "L",
                    "invest_type": "被动指数型",
                    "benchmark": "NASDAQ 100",
                },
                {
                    "ts_code": "510300.SH",
                    "name": "沪深300ETF",
                    "status": "L",
                    "invest_type": "被动指数型",
                    "benchmark": "沪深300指数",
                },
            ],
            otc_basic=[
                {
                    "ts_code": "000001.OF",
                    "name": "全球精选混合(QDII)",
                    "status": "L",
                    "invest_type": "混合型",
                    "benchmark": "MSCI World",
                },
            ],
            as_of="20260822",
        )

        records = {record["ts_code"]: record for record in catalog["records"]}
        exchange = records["513100.SH"]
        self.assertEqual(exchange["market_source"], "exchange")
        self.assertEqual(exchange["tradability"], "exchange_research_only")
        self.assertEqual(exchange["overseas_scope"], "nasdaq_100")
        self.assertEqual(exchange["classification_status"], "name_benchmark_inferred")
        self.assertIn("benchmark:nasdaq", exchange["classification_evidence"])

        otc = records["000001.OF"]
        self.assertEqual(otc["market_source"], "otc")
        self.assertEqual(otc["tradability"], "otc_non_tradable_research_only")
        self.assertEqual(otc["classification_status"], "explicit_qdii")
        self.assertEqual(otc["overseas_scope"], "global_exposure")
        self.assertTrue(otc["research_only"])
        self.assertNotIn("execution", otc)

        self.assertEqual(catalog["summary"]["source_counts"], {
            "exchange": 2,
            "otc": 1,
        })
        self.assertEqual(catalog["summary"]["overseas_scope_counts"]["nasdaq_100"], 1)

    def test_does_not_misclassify_domestic_sp_china_fund_as_us_exposure(self) -> None:
        catalog = build_fund_research_catalog(
            exchange_basic=[
                {
                    "ts_code": "510999.SH",
                    "name": "标普中国A股ETF",
                    "status": "L",
                    "benchmark": "标普中国A股指数",
                },
            ],
            otc_basic=[],
            as_of="20260822",
        )

        record = catalog["records"][0]
        self.assertIsNone(record["overseas_scope"])
        self.assertEqual(record["classification_status"], "unclassified")

    def test_normalizes_missing_pandas_values_without_failing_catalog_refresh(self) -> None:
        catalog = build_fund_research_catalog(
            exchange_basic=[{
                "ts_code": "510999.SH", "name": "测试ETF", "status": "L",
                "benchmark": pd.NA, "fund_type": pd.NA,
            }],
            otc_basic=[],
            as_of="20260822",
        )

        self.assertEqual(catalog["records"][0]["benchmark"], "")
        self.assertEqual(catalog["records"][0]["fund_type"], "")


class _ResearchUniverseClient:
    def __init__(self, *, missing_index: str | None = None) -> None:
        self.missing_index = missing_index
        self.index_calls: list[dict[str, object]] = []
        self.fund_calls: list[dict[str, object]] = []
        self.stock_basic_calls: list[dict[str, object]] = []

    def index_weight(self, **kwargs):
        self.index_calls.append(kwargs)
        if kwargs["index_code"] == self.missing_index:
            return pd.DataFrame(columns=["con_code", "trade_date", "weight"])
        return pd.DataFrame(
            [{
                "con_code": {
                    "000300.SH": "000001.SZ",
                    "000905.SH": "000002.SZ",
                    "000852.SH": "000003.SZ",
                }[kwargs["index_code"]],
                "trade_date": "20260803",
                "weight": 1.0,
            }]
        )

    def fund_basic(self, **kwargs):
        self.fund_calls.append(kwargs)
        if kwargs["market"] == "E":
            return pd.DataFrame([{
                "ts_code": "513100.SH", "name": "纳斯达克100ETF",
                "status": "L", "benchmark": "NASDAQ 100",
            }])
        return pd.DataFrame([{
            "ts_code": "000001.OF", "name": "全球精选混合(QDII)",
            "status": "L", "benchmark": "MSCI World",
        }])

    def stock_basic(self, **kwargs):
        self.stock_basic_calls.append(kwargs)
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "name": "平安银行"},
            {"ts_code": "000002.SZ", "name": "万科A"},
            {"ts_code": "000003.SZ", "name": "测试三号"},
        ])


class ResearchUniverseRefreshTests(unittest.TestCase):
    def test_publishes_dated_and_latest_snapshot_only_after_all_sources_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            result = refresh_research_universes(
                repo_root=Path(tmp),
                pro_client=_ResearchUniverseClient(),
                as_of="20260822",
            )
            dated = Path(tmp) / "data/research/universe_catalogs/20260822/catalog.json"
            latest = Path(tmp) / "data/research/universe_catalogs/latest.json"

            self.assertEqual(result["status"], "complete")
            self.assertTrue(dated.exists())
            self.assertTrue(latest.exists())
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8"))["as_of"], "20260822")
            snapshot = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["a_share"]["records"][0]["name"], "平安银行")
            self.assertEqual(result["summary"]["funds"]["source_counts"], {
                "exchange": 1,
                "otc": 1,
            })

    def test_preserves_existing_latest_when_any_index_membership_is_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            latest = Path(tmp) / "data/research/universe_catalogs/latest.json"
            latest.parent.mkdir(parents=True)
            latest.write_text('{"as_of":"20260814"}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "a_share_membership_missing:csi1000"):
                refresh_research_universes(
                    repo_root=Path(tmp),
                    pro_client=_ResearchUniverseClient(missing_index="000852.SH"),
                    as_of="20260822",
                )

            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), {"as_of": "20260814"})
