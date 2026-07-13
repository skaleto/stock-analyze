"""Research-source normalization and point-in-time derived features."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SourceSpec:
    keys: tuple[str, ...]
    required: tuple[str, ...]


@dataclass
class SourceCollection:
    frames: dict[str, pd.DataFrame]
    health: pd.DataFrame


SOURCE_SPECS = {
    "daily_basic": SourceSpec(("trade_date", "ts_code"), ("trade_date", "ts_code")),
    "moneyflow": SourceSpec(("trade_date", "ts_code"), ("trade_date", "ts_code")),
    "fund_nav": SourceSpec(("ts_code", "nav_date", "ann_date"), ("ts_code", "nav_date")),
    "fund_share": SourceSpec(("ts_code", "trade_date"), ("ts_code", "trade_date", "fd_share")),
    "macro_releases": SourceSpec(("series", "source_date"), ("series", "source_date", "value")),
}


def _scrub_error(error: Exception) -> str:
    text = str(error)
    text = re.sub(r"(?i)(token|api_key|apikey)=([^&\s]+)", r"\1=<redacted>", text)
    return text[:240]


def normalize_source_frame(
    name: str,
    frame: pd.DataFrame | None,
    observed_at: str,
) -> pd.DataFrame:
    normalized = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    for column in ("ts_code", "code", "trade_date", "ann_date", "nav_date", "end_date"):
        if column in normalized.columns:
            normalized[column] = normalized[column].astype("string")
    date_column = next(
        (column for column in ("source_date", "trade_date", "ann_date", "nav_date", "end_date", "month", "date") if column in normalized.columns),
        None,
    )
    if date_column:
        normalized["source_date"] = normalized[date_column].astype("string")
    else:
        normalized["source_date"] = pd.Series([pd.NA] * len(normalized), dtype="string")
    normalized["source"] = f"tushare:{name}"
    normalized["observed_at"] = observed_at
    return normalized


def collect_source_calls(
    calls: Mapping[str, Iterable[Callable[[], pd.DataFrame]]],
    *,
    observed_at: str,
) -> SourceCollection:
    frames: dict[str, pd.DataFrame] = {}
    health_rows: list[dict[str, Any]] = []
    for name, endpoint_calls in calls.items():
        pieces: list[pd.DataFrame] = []
        failures: list[str] = []
        for endpoint_call in endpoint_calls:
            try:
                piece = endpoint_call()
                if isinstance(piece, pd.DataFrame) and not piece.empty:
                    pieces.append(piece)
            except Exception as exc:  # noqa: BLE001 - source failure is persisted
                failures.append(_scrub_error(exc))
        combined = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
        frames[name] = normalize_source_frame(name, combined, observed_at)
        health_rows.append(
            {
                "source": name,
                "observed_at": observed_at,
                "rows": len(combined),
                "failed": bool(failures),
                "error": " | ".join(failures),
            }
        )
    return SourceCollection(frames=frames, health=pd.DataFrame(health_rows))


def _latest(frame: pd.DataFrame, date_columns: tuple[str, ...] = ("trade_date", "ann_date", "nav_date")) -> pd.DataFrame:
    if frame.empty or "ts_code" not in frame.columns:
        return pd.DataFrame()
    date_column = next((column for column in date_columns if column in frame.columns), None)
    ordered = frame.sort_values(date_column) if date_column else frame
    return ordered.groupby("ts_code", as_index=False, dropna=False).tail(1)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def build_source_features(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Collapse normalized raw sources into one latest feature row per instrument."""

    instrument_sources = ("daily_basic", "moneyflow", "margin_detail", "fina_indicator", "income", "balancesheet", "cashflow", "fina_mainbz", "fund_nav", "fund_share")
    codes: set[str] = set()
    for source in instrument_sources:
        frame = frames.get(source, pd.DataFrame())
        if not frame.empty and "ts_code" in frame.columns:
            codes.update(frame["ts_code"].dropna().astype(str))
    if not codes:
        return pd.DataFrame(columns=["code", "ts_code"])
    output = pd.DataFrame({"ts_code": sorted(codes)})
    output["code"] = output["ts_code"].str.split(".").str[0]

    daily = frames.get("daily_basic", pd.DataFrame()).copy()
    if not daily.empty:
        daily["turnover_rate"] = pd.to_numeric(daily.get("turnover_rate"), errors="coerce")
        turnover_change = daily.sort_values("trade_date").groupby("ts_code")["turnover_rate"].agg(
            lambda values: values.iloc[-1] / values.iloc[0] - 1.0 if len(values) > 1 and values.iloc[0] else np.nan
        )
        latest_daily = _latest(daily).copy()
        latest_daily["turnover_change"] = latest_daily["ts_code"].map(turnover_change)
        for column in ("pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate"):
            if column in latest_daily.columns:
                latest_daily[column] = pd.to_numeric(latest_daily[column], errors="coerce")
        for column in ("pe_ttm", "pb", "total_mv"):
            if column in latest_daily.columns:
                latest_daily[f"{column}_percentile"] = latest_daily[column].rank(pct=True)
        keep = [column for column in latest_daily.columns if column in {"ts_code", "pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate", "turnover_change", "pe_ttm_percentile", "pb_percentile", "total_mv_percentile"}]
        output = output.merge(latest_daily[keep], on="ts_code", how="left")

    flow = frames.get("moneyflow", pd.DataFrame()).copy()
    if not flow.empty:
        for column in ("buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"):
            flow[column] = pd.to_numeric(flow.get(column, 0.0), errors="coerce").fillna(0.0)
        flow["flow_net_large"] = flow["buy_lg_amount"] + flow["buy_elg_amount"] - flow["sell_lg_amount"] - flow["sell_elg_amount"]
        flow["flow_persistence_5"] = flow.sort_values("trade_date").groupby("ts_code")["flow_net_large"].transform(lambda values: values.tail(5).gt(0).mean())
        output = output.merge(_latest(flow)[["ts_code", "flow_net_large", "flow_persistence_5"]], on="ts_code", how="left")

    income = _latest(frames.get("income", pd.DataFrame()), ("ann_date", "end_date"))
    cashflow = _latest(frames.get("cashflow", pd.DataFrame()), ("ann_date", "end_date"))
    balance = _latest(frames.get("balancesheet", pd.DataFrame()), ("ann_date", "end_date"))
    if not income.empty:
        financial = income.copy()
        if not cashflow.empty:
            financial = financial.merge(cashflow, on="ts_code", how="left", suffixes=("", "_cash"))
        if not balance.empty:
            financial = financial.merge(balance, on="ts_code", how="left", suffixes=("", "_balance"))
        n_income = pd.to_numeric(financial.get("n_income"), errors="coerce")
        operating_cash = pd.to_numeric(financial.get("n_cashflow_act"), errors="coerce")
        financial["cash_flow_quality"] = _safe_ratio(operating_cash, n_income.abs())
        if "total_assets" in financial.columns and "revenue" in financial.columns:
            financial["asset_turnover"] = _safe_ratio(pd.to_numeric(financial["revenue"], errors="coerce"), pd.to_numeric(financial["total_assets"], errors="coerce"))
        keep = [column for column in ("ts_code", "cash_flow_quality", "asset_turnover") if column in financial.columns]
        output = output.merge(financial[keep], on="ts_code", how="left")

    fund_share = frames.get("fund_share", pd.DataFrame()).copy()
    if not fund_share.empty:
        fund_share["fd_share"] = pd.to_numeric(fund_share["fd_share"], errors="coerce")
        share_change = fund_share.sort_values("trade_date").groupby("ts_code")["fd_share"].agg(
            lambda values: values.iloc[-1] / values.iloc[0] - 1.0 if len(values) > 1 and values.iloc[0] else np.nan
        )
        output["fund_share_change"] = output["ts_code"].map(share_change)

    for source, target in (("index_global", "global_index_momentum"), ("fx_daily", "rmb_depreciation")):
        frame = frames.get(source, pd.DataFrame()).copy()
        if not frame.empty and "close" in frame.columns:
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            series = frame.sort_values("trade_date").groupby("ts_code")["close"].agg(
                lambda values: values.iloc[-1] / values.iloc[0] - 1.0 if len(values) > 1 and values.iloc[0] else np.nan
            )
            output[target] = float(series.mean()) if not series.empty else np.nan
    return output
