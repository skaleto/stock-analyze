"""Offline historical panel builder for QDII capacity research."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .data_provider import MAX_NAV_AGE_DAYS, TUSHARE_AMOUNT_TO_YUAN


class ResearchPanelError(ValueError):
    """The cached research inputs cannot produce an auditable panel."""


@dataclass(frozen=True)
class ResearchPanelResult:
    frame: pd.DataFrame
    metadata: dict[str, Any]


_DAILY_DTYPES = {"ts_code": str, "trade_date": str}
_ADJ_DTYPES = {"ts_code": str, "trade_date": str}
_NAV_DTYPES = {"ts_code": str, "ann_date": str, "nav_date": str}
_SHARE_DTYPES = {"ts_code": str, "trade_date": str}


def _date_text(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.replace("-", "", regex=False).str[:8]
    return pd.to_datetime(text, format="%Y%m%d", errors="coerce")


def _latest_cache(cache_dir: Path, kind: str, code: str) -> Path | None:
    safe_code = str(code).replace(".", "_")
    pattern = re.compile(rf"^{re.escape(kind)}_{re.escape(safe_code)}_(\d{{8}})\.csv$")
    candidates: list[tuple[str, Path]] = []
    for path in cache_dir.glob(f"{kind}_{safe_code}_*.csv"):
        match = pattern.match(path.name)
        if match:
            candidates.append((match.group(1), path))
    return max(candidates, default=("", None), key=lambda item: item[0])[1]


def _read_csv(path: Path | None, dtype: dict[str, type]) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=dtype, keep_default_na=False)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ResearchPanelError(f"unreadable_cache:{path.name}") from exc


def _load_universe(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchPanelError(f"unreadable_universe:{path}") from exc
    scopes = payload.get("scopes") if isinstance(payload, dict) else None
    if not isinstance(scopes, dict):
        raise ResearchPanelError("empty_universe")
    rows: list[dict[str, Any]] = []
    for scope, raw_rows in scopes.items():
        if not isinstance(raw_rows, list):
            continue
        for raw in raw_rows:
            if not isinstance(raw, dict) or not raw.get("code"):
                continue
            rows.append({**raw, "scope": str(raw.get("scope") or scope)})
    deduped = {str(row["code"]): row for row in rows}
    if not deduped:
        raise ResearchPanelError("empty_universe")
    return list(deduped.values()), payload


def _merge_adjustments(daily: pd.DataFrame, adj: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    if adj.empty or not {"trade_date", "adj_factor"}.issubset(adj.columns):
        frame["adj_close"] = frame["close"]
        return frame
    factors = adj[["trade_date", "adj_factor"]].copy()
    factors["trade_date"] = _date_text(factors["trade_date"])
    factors["adj_factor"] = pd.to_numeric(factors["adj_factor"], errors="coerce")
    frame = frame.merge(factors, on="trade_date", how="left")
    frame["adj_close"] = frame["close"] * frame["adj_factor"].fillna(1.0)
    return frame


def _merge_nav(daily: pd.DataFrame, nav: pd.DataFrame) -> pd.DataFrame:
    frame = daily.sort_values("trade_date").copy()
    frame["nav"] = float("nan")
    frame["nav_date"] = pd.NaT
    if nav.empty or "nav_date" not in nav.columns:
        return frame
    values = nav.copy()
    values["nav_date"] = _date_text(values["nav_date"])
    if "ann_date" in values.columns:
        values["ann_date"] = _date_text(values["ann_date"])
    else:
        values["ann_date"] = values["nav_date"]
    values["observed_at"] = values[["nav_date", "ann_date"]].max(axis=1)
    unit_nav = pd.to_numeric(values.get("unit_nav"), errors="coerce")
    adj_nav = pd.to_numeric(values.get("adj_nav"), errors="coerce")
    values["nav"] = unit_nav.where(unit_nav.notna() & unit_nav.ne(0), adj_nav)
    values = values.dropna(subset=["observed_at", "nav"]).sort_values(
        ["observed_at", "nav_date"]
    )
    if values.empty:
        return frame
    frame = pd.merge_asof(
        daily.sort_values("trade_date"),
        values[["observed_at", "nav_date", "nav"]].sort_values("observed_at"),
        left_on="trade_date",
        right_on="observed_at",
        direction="backward",
    )
    age = (frame["trade_date"] - frame["nav_date"]).dt.days
    frame.loc[age.lt(0) | age.gt(MAX_NAV_AGE_DAYS), "nav"] = float("nan")
    return frame.drop(columns=["observed_at"])


def _merge_shares(daily: pd.DataFrame, shares: pd.DataFrame) -> pd.DataFrame:
    frame = daily.sort_values("trade_date").copy()
    frame["fund_share"] = float("nan")
    if shares.empty or not {"trade_date", "fd_share"}.issubset(shares.columns):
        return frame
    values = shares[["trade_date", "fd_share"]].copy()
    values["share_date"] = _date_text(values.pop("trade_date"))
    values["fund_share"] = pd.to_numeric(values.pop("fd_share"), errors="coerce")
    values = values.dropna(subset=["share_date", "fund_share"]).sort_values("share_date")
    if values.empty:
        return frame
    return pd.merge_asof(
        frame.drop(columns=["fund_share"]),
        values,
        left_on="trade_date",
        right_on="share_date",
        direction="backward",
    )


def _coverage(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    return round(float(frame[column].notna().mean()), 6)


def build_research_panel(
    cache_dir: str | Path,
    universe_path: str | Path,
    *,
    start: str,
    end: str,
) -> ResearchPanelResult:
    """Build a network-free daily panel from the newest complete cache files."""

    cache_root = Path(cache_dir)
    universe_file = Path(universe_path)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        raise ResearchPanelError("invalid_date_range")
    universe, universe_payload = _load_universe(universe_file)

    daily_paths: dict[str, Path] = {}
    missing: list[str] = []
    for row in universe:
        code = str(row["code"])
        path = _latest_cache(cache_root, "fund_daily", code)
        if path is None:
            missing.append(code)
        else:
            daily_paths[code] = path
    if missing:
        raise ResearchPanelError(f"missing_daily_history:{','.join(sorted(missing))}")

    frames: list[pd.DataFrame] = []
    optional_files: dict[str, dict[str, str | None]] = {}
    metadata_keys = (
        "name",
        "scope",
        "index_key",
        "theme",
        "sector",
        "country",
        "exposure_group",
        "benchmark",
        "list_date",
        "management_fee",
    )
    for row in universe:
        code = str(row["code"])
        daily = _read_csv(daily_paths[code], _DAILY_DTYPES)
        required = {"trade_date", "open", "close", "amount"}
        if daily.empty or not required.issubset(daily.columns):
            raise ResearchPanelError(f"invalid_daily_history:{code}")
        daily["trade_date"] = _date_text(daily["trade_date"])
        for column in ("open", "high", "low", "close", "vol", "amount"):
            if column in daily.columns:
                daily[column] = pd.to_numeric(daily[column], errors="coerce")
        daily = daily.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
        list_date = pd.to_datetime(row.get("list_date"), errors="coerce")
        if pd.notna(list_date):
            daily = daily.loc[daily["trade_date"].ge(list_date)]
        daily = daily.loc[daily["trade_date"].between(start_ts, end_ts)].copy()

        adj_path = _latest_cache(cache_root, "fund_adj", code)
        nav_path = _latest_cache(cache_root, "fund_nav", code)
        share_path = _latest_cache(cache_root, "fund_share", code)
        daily = _merge_adjustments(daily, _read_csv(adj_path, _ADJ_DTYPES))
        daily = _merge_nav(daily, _read_csv(nav_path, _NAV_DTYPES))
        daily = _merge_shares(daily, _read_csv(share_path, _SHARE_DTYPES))
        daily["code"] = code
        daily["amount_yuan"] = daily["amount"] * TUSHARE_AMOUNT_TO_YUAN
        daily["discount_premium"] = daily["close"] / daily["nav"] - 1.0
        daily["fund_size_yuan"] = daily["fund_share"] * 10_000.0 * daily["nav"]
        for key in metadata_keys:
            daily[key] = row.get(key)
        frames.append(daily)
        optional_files[code] = {
            "adj": adj_path.name if adj_path else None,
            "nav": nav_path.name if nav_path else None,
            "share": share_path.name if share_path else None,
        }

    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame.empty:
        raise ResearchPanelError("empty_panel")
    frame = frame.sort_values(["trade_date", "scope", "code"]).reset_index(drop=True)
    frame["trade_date"] = frame["trade_date"].dt.strftime("%Y-%m-%d")
    metadata = {
        "schema_version": 1,
        "source_contract": "current-catalog historical replay",
        "survivorship_bias": True,
        "universe_hash": universe_payload.get("universe_hash"),
        "catalog_as_of": universe_payload.get("as_of"),
        "source_status": universe_payload.get("source_status"),
        "start": str(frame["trade_date"].min()),
        "end": str(frame["trade_date"].max()),
        "codes": int(frame["code"].nunique()),
        "rows": int(len(frame)),
        "nav_coverage": _coverage(frame, "nav"),
        "share_coverage": _coverage(frame, "fund_share"),
        "daily_files": {code: path.name for code, path in sorted(daily_paths.items())},
        "optional_files": optional_files,
    }
    return ResearchPanelResult(frame=frame, metadata=metadata)


__all__ = ["ResearchPanelError", "ResearchPanelResult", "build_research_panel"]
