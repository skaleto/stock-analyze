from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.share_unlock_backfill import (
    _fetch_pages, load_share_unlock_events, run_share_unlock_backfill,
    share_unlock_partitions,
)
from stock_analyze.research.share_unlock_study import (
    load_contract, run_share_unlock_study, select_study_events,
    to_avoidance_panel,
)

CONTRACT = Path(__file__).resolve().parents[1] / "configs/research/share_unlock_avoidance_study.yaml"


class UnlockClient:
    def __init__(self, rows=None, paged=False):
        self.rows = list(rows or []); self.paged = paged; self.offsets = []

    def share_float(self, **kwargs):
        self.offsets.append(kwargs["offset"])
        if self.paged:
            count = 2000 if kwargs["offset"] == 0 else 1
            return pd.DataFrame([unlock_row(kwargs["start_date"], kwargs["start_date"], 10, 1.0)] * count)
        return pd.DataFrame(
            [x for x in self.rows if kwargs["start_date"] <= x["float_date"] <= kwargs["end_date"]],
            columns=["ts_code","ann_date","float_date","float_share","float_ratio","holder_name","share_type"],
        )


class MonthlyFailDailyClient:
    def __init__(self):
        self.calls = []

    def share_float(self, **kwargs):
        self.calls.append((kwargs["start_date"], kwargs["end_date"], kwargs["offset"]))
        if kwargs["start_date"] != kwargs["end_date"]:
            raise ValueError("provider_month_too_large")
        rows = (
            [unlock_row(kwargs["start_date"], kwargs["start_date"], 10, 1.0)]
            if kwargs["start_date"].endswith("02") else []
        )
        return pd.DataFrame(
            rows, columns=["ts_code","ann_date","float_date","float_share","float_ratio","holder_name","share_type"]
        )


def unlock_row(ann, floating, shares, ratio, holder="A", share_type="定增股份", code="000001.SZ"):
    return {"ts_code":code,"ann_date":ann,"float_date":floating,"float_share":shares,"float_ratio":ratio,"holder_name":holder,"share_type":share_type}


def write_basic(root: str, trade_date: str = "20200131", total_share: float = 10.0):
    path = Path(root) / "data/shared/backtest_cache/daily_basic"; path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"ts_code":"000001.SZ","total_share":total_share}]).to_csv(path / f"{trade_date}.csv", index=False)


class ShareUnlockBackfillTest(unittest.TestCase):
    def test_monthly_partitions_and_pagination(self):
        self.assertEqual(len(share_unlock_partitions("2018-01-01", "2024-12-31")), 84)
        client = UnlockClient(paged=True); frame = _fetch_pages(client, "20200101", "20200131")
        self.assertEqual(len(frame), 2001); self.assertEqual(client.offsets, [0,2000])

    def test_large_month_falls_back_to_days_but_stays_one_partition(self):
        client = MonthlyFailDailyClient()
        frame = _fetch_pages(client, "20200101", "20200103")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["float_date"], "20200102")
        self.assertEqual(client.calls[0], ("20200101", "20200103", 0))
        self.assertIn(("20200102", "20200102", 0), client.calls)

    def test_latest_confirmation_replaces_old_snapshot_and_stale_plan(self):
        rows = [
            unlock_row("20190101","20200131",5000,5.0),
            unlock_row("20200120","20200131",6000,6.0),
            unlock_row("20200125","20200131",6000,6.0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_basic(tmp)
            run_share_unlock_backfill(tmp,UnlockClient(rows),start_date="2020-01-01",end_date="2020-01-31")
            events,audit=load_share_unlock_events(tmp,start_date="2020-01-01",end_date="2020-01-31")
        self.assertEqual(len(events),1); self.assertAlmostEqual(events.iloc[0].unlock_ratio,.06)
        self.assertEqual(events.iloc[0].confirmation_date,"20200125")
        self.assertEqual(audit["stale_rows_excluded"],1)

    def test_conflicting_holder_snapshot_invalidates_tranche(self):
        rows=[unlock_row("20200125","20200131",5000,5.0),unlock_row("20200125","20200131",6000,6.0)]
        with tempfile.TemporaryDirectory() as tmp:
            write_basic(tmp); run_share_unlock_backfill(tmp,UnlockClient(rows),start_date="2020-01-01",end_date="2020-01-31")
            events,audit=load_share_unlock_events(tmp,start_date="2020-01-01",end_date="2020-01-31")
        self.assertTrue(events.empty); self.assertEqual(audit["invalid_tranches"],1)

    def test_ratio_disagreement_fails_closed(self):
        rows=[unlock_row("20200125","20200131",5000,20.0)]
        with tempfile.TemporaryDirectory() as tmp:
            write_basic(tmp); run_share_unlock_backfill(tmp,UnlockClient(rows),start_date="2020-01-01",end_date="2020-01-31")
            events,audit=load_share_unlock_events(tmp,start_date="2020-01-01",end_date="2020-01-31")
        self.assertTrue(events.empty); self.assertEqual(audit["ratio_disagreements"],1)

    def test_partition_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_basic(tmp); run_share_unlock_backfill(tmp,UnlockClient(),start_date="2020-01-01",end_date="2020-01-31")
            path=Path(tmp)/"data/research/share_unlock_structured/v1/share_float/20200101_20200131.parquet"
            path.write_bytes(path.read_bytes()+b"tamper")
            with self.assertRaisesRegex(ValueError,"tampered"):
                load_share_unlock_events(tmp,start_date="2020-01-01",end_date="2020-01-31")


class ShareUnlockStudyTest(unittest.TestCase):
    def test_contract_frozen_and_family_thresholds(self):
        payload=json.loads(CONTRACT.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"c.json"; path.write_text(json.dumps({**payload,"large_unlock_ratio":.03}))
            with self.assertRaisesRegex(ValueError,"frozen:large_unlock_ratio"): load_contract(path)
        contract=load_contract(CONTRACT)
        events=pd.DataFrame([{"family":"share_unlock","unlock_ratio":x} for x in (.005,.02,.05)])
        selected=select_study_events(events,contract)
        self.assertEqual(selected.family.tolist(),["small_unlock_avoidance","large_unlock_avoidance"] )

    def test_avoidance_return_reverses_gross_active_then_subtracts_cost(self):
        contract=load_contract(CONTRACT)
        long=pd.DataFrame([{"security_return":.10,"benchmark_return":.04,"active_return":.06,"net_active_return":.0579,"stress_net_active_return":.05685}])
        out=to_avoidance_panel(long,contract).iloc[0]
        self.assertAlmostEqual(out.active_return,-.06)
        self.assertAlmostEqual(out.net_active_return,-.0621)
        self.assertAlmostEqual(out.stress_net_active_return,-.06315)
        self.assertEqual(out.return_interpretation,"benchmark_substitution_avoidance")

    def test_incomplete_backfill_stops_before_price_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); contract_path=root/"contract.json"
            contract_path.write_bytes(CONTRACT.read_bytes())
            result=run_share_unlock_study(
                root,snapshot_date="20260814",contract_path=contract_path,
                output_root=root/"reports",
            )
        self.assertEqual(result["status"],"insufficient_data")
        self.assertEqual(result["panel_rows"],0)
        self.assertFalse(result["backfill"]["complete"])
        self.assertFalse(result["model_training_allowed"])

if __name__=="__main__": unittest.main()
