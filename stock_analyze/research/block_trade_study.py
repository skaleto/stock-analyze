"""Preregistered model-free block-trade premium event study."""

from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping
import pandas as pd
from .block_trade_backfill import load_block_trade_events
from .capital_actions_study import _build_batched_return_panel, evaluate_panel
from .earnings_drift_study import _load_benchmark

@dataclass(frozen=True)
class BlockTradeContract:
    protocol_version:str; market:str; development_start:str; development_end:str
    historical_diagnostic_start:str; live_oos_start:str; horizons:tuple[int,...]
    primary_horizon:int; round_trip_cost:float; stress_cost_multiple:float
    premium_threshold:float; discount_threshold:float
    minimum_amount_market_cap_ratio:float; maximum_amount_absolute_error_wan:float
    maximum_amount_relative_error:float; minimum_total_events:int
    minimum_unique_securities:int; minimum_event_years:int; minimum_scope_events:int
    minimum_positive_year_fraction:float; minimum_bootstrap_probability:float
    maximum_year_contribution_share:float; bootstrap_samples:int; bootstrap_seed:int
    positive_families:tuple[str,...]; diagnostic_families:tuple[str,...]

_FROZEN={
    "protocol_version":"block-trade-premium-preregistered-v1","market":"a_share",
    "development_start":"20180101","development_end":"20241231",
    "historical_diagnostic_start":"20250101","live_oos_start":"20260818",
    "horizons":(5,20,60),"primary_horizon":20,"round_trip_cost":.0021,
    "stress_cost_multiple":1.5,"premium_threshold":.02,"discount_threshold":-.05,
    "minimum_amount_market_cap_ratio":.001,"maximum_amount_absolute_error_wan":5.0,
    "maximum_amount_relative_error":.001,"minimum_total_events":100,
    "minimum_unique_securities":50,"minimum_event_years":4,"minimum_scope_events":25,
    "minimum_positive_year_fraction":.6666666667,"minimum_bootstrap_probability":.95,
    "maximum_year_contribution_share":.5,"bootstrap_samples":5000,
    "bootstrap_seed":20260818,"positive_families":("block_trade_premium",),
    "diagnostic_families":("block_trade_discount",),
}

def _date_key(v:object)->str:return str(v).replace("-","")[:8]
def _iso_date(v:str)->str:
    k=_date_key(v);return f"{k[:4]}-{k[4:6]}-{k[6:8]}"

def load_contract(path:str|Path)->BlockTradeContract:
    p=json.loads(Path(path).read_text());c=BlockTradeContract(**{**p,"horizons":tuple(map(int,p["horizons"])),"positive_families":tuple(p["positive_families"]),"diagnostic_families":tuple(p["diagnostic_families"])})
    a=asdict(c)
    for k,v in _FROZEN.items():
        if a[k]!=v:raise ValueError(f"block_trade_contract_frozen:{k}")
    return c

def select_study_events(events:pd.DataFrame,c:BlockTradeContract)->pd.DataFrame:
    required={"premium","amount_market_cap_ratio"}
    if required.difference(events.columns):raise ValueError("block_trade_event_columns")
    p=pd.to_numeric(events.premium,errors="coerce");m=pd.to_numeric(events.amount_market_cap_ratio,errors="coerce")
    s=events.loc[m.ge(c.minimum_amount_market_cap_ratio)&(p.ge(c.premium_threshold)|p.le(c.discount_threshold))].copy()
    s["family"]="block_trade_premium";s.loc[p.loc[s.index].le(c.discount_threshold),"family"]="block_trade_discount";s["eligible"]=True
    return s

def _write_report(r:Mapping[str,Any],path:Path)->None:
    lines=["# Block Trade Premium Study","",f"- Status: {r['status']}","- Model training allowed: false","- Formal strategy unchanged: true",""]
    for family in [*r["families"],*r["diagnostics"]]:
        lines.extend([f"## {family['family']}","",f"- Status: {family['status']}",f"- Mature events: {family['evidence']['events']}",""]);
        for h in family["horizons"]:lines.extend([f"### {h['horizon']} sessions","",f"- Mean net active return: {h['mean_net_active_return']:.2%}",f"- Median net active return: {h['median_net_active_return']:.2%}",""] )
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text("\n".join(lines)+"\n")

def run_block_trade_study(repo_root:str|Path,*,snapshot_date:str,contract_path:str|Path="configs/research/block_trade_premium_study.yaml",output_root:str|Path="reports/research")->dict[str,Any]:
    root=Path(repo_root).resolve();cp=Path(contract_path);cp=cp if cp.is_absolute() else root/cp;c=load_contract(cp);snap=_date_key(snapshot_date)
    events,audit=load_block_trade_events(root,snapshot_date=snap,start_date=_iso_date(c.development_start),end_date=_iso_date(c.development_end),maximum_amount_absolute_error_wan=c.maximum_amount_absolute_error_wan,maximum_amount_relative_error=c.maximum_amount_relative_error)
    selected=select_study_events(events,c) if not events.empty else events;panel=pd.DataFrame()
    if audit.get("complete") and not selected.empty:panel=_build_batched_return_panel(root,selected,snapshot_date=snap,contract=c,benchmarks={"hs300":_load_benchmark(root,"000300",snap),"zz500":_load_benchmark(root,"000905",snap)})
    cpnl=panel.loc[panel.family.eq("block_trade_premium")].copy() if not panel.empty else panel.copy();dpnl=panel.loc[panel.family.eq("block_trade_discount")].copy() if not panel.empty else panel.copy()
    result=evaluate_panel(cpnl,audit,c,diagnostic_panel=dpnl);result.update({"snapshot_date":snap,"development_window":[c.development_start,c.development_end],"backfill":audit,"panel_rows":len(panel),"candidate_panel_rows":len(cpnl),"diagnostic_panel_rows":len(dpnl),"historical_diagnostic_opened":False,"live_oos_start":c.live_oos_start})
    out=Path(output_root);out=out if out.is_absolute() else root/out;out.mkdir(parents=True,exist_ok=True);jp=out/f"block_trade_premium_{snap}.json";mp=out/f"block_trade_premium_{snap}.md";jp.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str));_write_report(result,mp);return {**result,"report_json":str(jp),"report_markdown":str(mp)}
