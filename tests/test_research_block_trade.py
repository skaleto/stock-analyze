from __future__ import annotations
import json,tempfile,unittest
from pathlib import Path
import pandas as pd
from stock_analyze.research.block_trade_backfill import _fetch_day,block_trade_partitions,load_block_trade_events,run_block_trade_backfill
from stock_analyze.research.block_trade_study import load_contract,run_block_trade_study,select_study_events
CONTRACT=Path(__file__).resolve().parents[1]/"configs/research/block_trade_premium_study.yaml"

def row(day,price,vol,amount,code="000001.SZ",buyer="B",seller="S"):
    return {"ts_code":code,"trade_date":day,"price":price,"vol":vol,"amount":amount,"buyer":buyer,"seller":seller}
class Client:
    def __init__(self,rows=None,paged=False):self.rows=list(rows or []);self.paged=paged;self.offsets=[]
    def block_trade(self,**kw):
        self.offsets.append(kw["offset"]);rs=[x for x in self.rows if x["trade_date"]==kw["trade_date"]]
        if self.paged:rs=[row(kw["trade_date"],10,1,10)]*(2000 if kw["offset"]==0 else 1)
        return pd.DataFrame(rs,columns=list(row("20200101",1,1,1)))

def fixtures(root,day="20200102",close=10,total_mv=1000):
    daily=Path(root)/"data/shared/backtest_cache/daily_basic";daily.mkdir(parents=True,exist_ok=True);pd.DataFrame([{"ts_code":"000001.SZ","total_mv":total_mv}]).to_csv(daily/f"{day}.csv",index=False)
    hist=Path(root)/"data/shared/cache/history_000001_x.csv";hist.parent.mkdir(parents=True,exist_ok=True);pd.DataFrame([{"trade_date":day,"close":close}]).to_csv(hist,index=False)
    manifest=Path(root)/"data/research/raw/a_share/20260814/materialization_manifest.json";manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text(json.dumps({"status":"complete","outputs":{str(hist.relative_to(root)):{"path":str(hist.relative_to(root))}}}))

class BlockTradeBackfillTest(unittest.TestCase):
    def test_daily_partitions_and_pagination(self):
        self.assertEqual(len(block_trade_partitions("2018-01-01","2024-12-31")),2557);c=Client(paged=True);f=_fetch_day(c,"20200102");self.assertEqual(len(f),2001);self.assertEqual(c.offsets,[0,2000])
    def test_stock_day_aggregates_before_threshold_and_uses_raw_close(self):
        rows=[row("20200102",10.2,10,102,buyer="B1"),row("20200102",10.4,20,208,buyer="B2")]
        with tempfile.TemporaryDirectory() as tmp:
            fixtures(tmp);run_block_trade_backfill(tmp,Client(rows),start_date="2020-01-02",end_date="2020-01-02");e,a=load_block_trade_events(tmp,snapshot_date="20260814",start_date="2020-01-02",end_date="2020-01-02")
        self.assertEqual(len(e),1);x=e.iloc[0];self.assertAlmostEqual(x.block_vwap,310/30);self.assertAlmostEqual(x.premium,(310/30)/10-1);self.assertAlmostEqual(x.amount_market_cap_ratio,.31);self.assertEqual(x.buyer_count,2)
    def test_any_bad_unit_row_invalidates_whole_stock_day(self):
        rows=[row("20200102",10,10,100),row("20200102",10,10,200)]
        with tempfile.TemporaryDirectory() as tmp:
            fixtures(tmp);run_block_trade_backfill(tmp,Client(rows),start_date="2020-01-02",end_date="2020-01-02");e,a=load_block_trade_events(tmp,snapshot_date="20260814",start_date="2020-01-02",end_date="2020-01-02")
        self.assertTrue(e.empty);self.assertEqual(a["invalid_stock_days"],1)
    def test_partition_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures(tmp);run_block_trade_backfill(tmp,Client(),start_date="2020-01-02",end_date="2020-01-02");p=Path(tmp)/"data/research/block_trade_structured/v1/block_trade/20200102.parquet";p.write_bytes(p.read_bytes()+b"x")
            with self.assertRaisesRegex(ValueError,"tampered"):load_block_trade_events(tmp,snapshot_date="20260814",start_date="2020-01-02",end_date="2020-01-02")

class BlockTradeStudyTest(unittest.TestCase):
    def test_contract_frozen_and_selection(self):
        p=json.loads(CONTRACT.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/"c";f.write_text(json.dumps({**p,"premium_threshold":.01}))
            with self.assertRaisesRegex(ValueError,"frozen:premium_threshold"):load_contract(f)
        c=load_contract(CONTRACT);e=pd.DataFrame([{"premium":.02,"amount_market_cap_ratio":.001},{"premium":-.05,"amount_market_cap_ratio":.001},{"premium":.1,"amount_market_cap_ratio":.0009}]);s=select_study_events(e,c);self.assertEqual(s.family.tolist(),["block_trade_premium","block_trade_discount"])
    def test_incomplete_backfill_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);c=root/"c";c.write_bytes(CONTRACT.read_bytes());r=run_block_trade_study(root,snapshot_date="20260814",contract_path=c,output_root=root/"r")
        self.assertEqual(r["status"],"insufficient_data");self.assertEqual(r["panel_rows"],0);self.assertFalse(r["model_training_allowed"])
if __name__=="__main__":unittest.main()
