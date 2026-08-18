from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.dividend_growth_backfill import (
    _fetch_day, dividend_partitions, load_dividend_growth_events,
    run_dividend_growth_backfill,
)
from stock_analyze.research.dividend_growth_study import (
    load_contract, run_dividend_growth_study, select_study_events,
)

CONTRACT = Path(__file__).resolve().parents[1] / "configs/research/dividend_growth_study.yaml"


def dividend_row(day, end, cash, base, code="000001.SZ", record="20200701", ex="20200702"):
    return {
        "ts_code":code,"end_date":end,"ann_date":day,"div_proc":"实施",
        "cash_div":cash,"cash_div_tax":cash,"record_date":record,
        "ex_date":ex,"pay_date":ex,"imp_ann_date":day,
        "base_date":record,"base_share":base,
    }


class DividendClient:
    def __init__(self, rows=None, paged=False):
        self.rows=list(rows or []);self.paged=paged;self.offsets=[]
    def dividend(self,**kwargs):
        self.offsets.append(kwargs.get("offset",0))
        if self.paged:
            count=2000 if kwargs.get("offset",0)==0 else 1
            return pd.DataFrame([dividend_row(kwargs["imp_ann_date"],"20191231",.1,10)]*count)
        return pd.DataFrame([x for x in self.rows if x["imp_ann_date"]==kwargs["imp_ann_date"]],columns=list(dividend_row("20200101","20191231",.1,10)))


def write_market_value(root, day, total_mv=1000.0):
    path=Path(root)/"data/shared/backtest_cache/daily_basic";path.mkdir(parents=True,exist_ok=True)
    pd.DataFrame([{"ts_code":"000001.SZ","total_mv":total_mv}]).to_csv(path/f"{day}.csv",index=False)


class DividendGrowthBackfillTest(unittest.TestCase):
    def test_daily_partitions_and_pagination(self):
        self.assertEqual(len(dividend_partitions("2018-01-01","2024-12-31")),2557)
        c=DividendClient(paged=True);f=_fetch_day(c,"20200101")
        self.assertEqual(len(f),2001);self.assertEqual(c.offsets,[0,2000])

    def test_exact_lifecycle_duplicates_collapse_and_units_are_yuan(self):
        rows=[
            dividend_row("20190701","20181231",.1,100),
            dividend_row("20200701","20191231",.2,100),
            dividend_row("20200701","20191231",.2,100),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_market_value(tmp,"20200701",1000) # 10m yuan market cap
            run_dividend_growth_backfill(tmp,DividendClient(rows),start_date="2019-01-01",end_date="2020-12-31")
            events,audit=load_dividend_growth_events(tmp,start_date="2019-01-01",end_date="2020-12-31")
        self.assertEqual(len(events),1);e=events.iloc[0]
        self.assertEqual(e.total_cash_yuan,200000.0)
        self.assertAlmostEqual(e.dividend_growth,1.0)
        self.assertAlmostEqual(e.dividend_market_cap_ratio,.02)
        self.assertEqual(audit["ambiguous_fiscal_years"],0)

    def test_multiple_distinct_implementation_facts_exclude_year(self):
        rows=[
            dividend_row("20190701","20181231",.1,100),
            dividend_row("20200701","20191231",.2,100),
            dividend_row("20200801","20191231",.1,100,record="20200801",ex="20200802"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_market_value(tmp,"20200801")
            run_dividend_growth_backfill(tmp,DividendClient(rows),start_date="2019-01-01",end_date="2020-12-31")
            events,audit=load_dividend_growth_events(tmp,start_date="2019-01-01",end_date="2020-12-31")
        self.assertTrue(events.empty);self.assertEqual(audit["ambiguous_fiscal_years"],1)

    def test_missing_immediately_previous_fiscal_year_cannot_be_skipped(self):
        rows=[dividend_row("20180701","20171231",.1,100),dividend_row("20200701","20191231",.2,100)]
        with tempfile.TemporaryDirectory() as tmp:
            write_market_value(tmp,"20200701");run_dividend_growth_backfill(tmp,DividendClient(rows),start_date="2018-01-01",end_date="2020-12-31")
            events,_=load_dividend_growth_events(tmp,start_date="2018-01-01",end_date="2020-12-31")
        self.assertTrue(events.empty)

    def test_partition_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dividend_growth_backfill(tmp,DividendClient(),start_date="2020-01-01",end_date="2020-01-01")
            path=Path(tmp)/"data/research/dividend_growth_structured/v1/dividend/20200101.parquet"
            path.write_bytes(path.read_bytes()+b"tamper")
            with self.assertRaisesRegex(ValueError,"tampered"):
                load_dividend_growth_events(tmp,start_date="2020-01-01",end_date="2020-01-01")


class DividendGrowthStudyTest(unittest.TestCase):
    def test_contract_frozen_and_threshold_selection(self):
        payload=json.loads(CONTRACT.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"c.json";path.write_text(json.dumps({**payload,"dividend_growth_threshold":.1}))
            with self.assertRaisesRegex(ValueError,"frozen:dividend_growth_threshold"):load_contract(path)
        c=load_contract(CONTRACT);events=pd.DataFrame([
            {"family":"annual_dividend_growth","dividend_growth":.2,"dividend_market_cap_ratio":.01},
            {"family":"annual_dividend_cut","dividend_growth":-.2,"dividend_market_cap_ratio":.01},
            {"family":"annual_dividend_growth","dividend_growth":.5,"dividend_market_cap_ratio":.009},
        ])
        s=select_study_events(events,c);self.assertEqual(s.family.tolist(),["annual_dividend_growth","annual_dividend_cut"] )

    def test_incomplete_backfill_stops_before_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);contract=root/"contract.json";contract.write_bytes(CONTRACT.read_bytes())
            r=run_dividend_growth_study(root,snapshot_date="20260814",contract_path=contract,output_root=root/"reports")
        self.assertEqual(r["status"],"insufficient_data");self.assertEqual(r["panel_rows"],0);self.assertFalse(r["model_training_allowed"])

if __name__=="__main__":unittest.main()
