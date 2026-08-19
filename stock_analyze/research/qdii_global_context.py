"""Checksummed QDII global-index and FX point-in-time context."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from ..utils import write_text_atomic


ASSET_ROOT = Path("data/research/qdii_global_context/v1")
FEATURE_COLUMNS = (
    "global_index_momentum",
    "global_volatility",
    "rmb_depreciation",
    "global_source_index_code",
    "global_mapping_kind",
    "global_source_trade_date",
    "global_available_date",
    "fx_source_code",
    "fx_source_trade_date",
    "fx_available_date",
)


def _date_key(value: object) -> str:
    key = str(value or "").replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"qdii_global_context_date:{value}")
    datetime.strptime(key, "%Y%m%d")
    return key


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("protocol") != "qdii-global-context-v1":
        raise ValueError("qdii_global_context_contract")
    if int(payload.get("availability_lag_calendar_days") or 0) != 1:
        raise ValueError("qdii_global_context_availability_lag")
    index_sources = dict(payload.get("index_sources") or {})
    fx_sources = dict(payload.get("fx_sources") or {})
    if set(index_sources) != {"SPX", "IXIC", "DJI", "HSI"}:
        raise ValueError("qdii_global_context_index_sources")
    if set(fx_sources) != {"USDCNH.FXCM"}:
        raise ValueError("qdii_global_context_fx_sources")
    exact = dict(payload.get("exact_mappings") or {})
    proxy = dict(payload.get("family_proxy_mappings") or {})
    if set(exact).intersection(proxy):
        raise ValueError("qdii_global_context_mapping_overlap")
    allowed = set(index_sources)
    if any(str(code) not in allowed for code in [*exact.values(), *proxy.values()]):
        raise ValueError("qdii_global_context_mapping_source")
    return {**payload, "contract_sha256": _canonical_hash(payload)}


def mapping_for_index_key(
    index_key: object, contract: Mapping[str, Any]
) -> tuple[str, str] | None:
    key = str(index_key or "").strip()
    exact = dict(contract.get("exact_mappings") or {})
    proxy = dict(contract.get("family_proxy_mappings") or {})
    if key in exact:
        return str(exact[key]), "exact"
    if key in proxy:
        return str(proxy[key]), "family_proxy"
    return None


def _normalize_index(frame: pd.DataFrame, *, observed_at: str) -> pd.DataFrame:
    required = {"ts_code", "trade_date", "close"}
    if frame.empty or required.difference(frame.columns):
        raise ValueError("qdii_global_context_index_schema")
    result = frame.copy()
    result["ts_code"] = result["ts_code"].astype("string")
    result["trade_date"] = result["trade_date"].map(_date_key).astype("string")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["close"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )
    result["source"] = "tushare:index_global"
    result["observed_at"] = observed_at
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def _normalize_fx(frame: pd.DataFrame, *, observed_at: str) -> pd.DataFrame:
    required = {"ts_code", "trade_date"}
    if frame.empty or required.difference(frame.columns):
        raise ValueError("qdii_global_context_fx_schema")
    close = "bid_close" if "bid_close" in frame.columns else "close"
    if close not in frame.columns:
        raise ValueError("qdii_global_context_fx_close")
    result = frame.copy()
    result["ts_code"] = result["ts_code"].astype("string")
    result["trade_date"] = result["trade_date"].map(_date_key).astype("string")
    result[close] = pd.to_numeric(result[close], errors="coerce")
    result = result.dropna(subset=[close]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    )
    result["source"] = "tushare:fx_daily"
    result["observed_at"] = observed_at
    return result.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, suffix=".parquet", delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def backfill_global_context(
    repo_root: str | Path,
    pro: Any,
    *,
    start_date: str,
    end_date: str,
    contract_path: str | Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    config = Path(contract_path)
    if not config.is_absolute():
        config = root / config
    contract = load_contract(config)
    start = _date_key(start_date)
    end = _date_key(end_date)
    if start != _date_key(contract["start_date"]) or end < start:
        raise ValueError("qdii_global_context_window")
    try:
        load_verified_global_context(root, contract_path=config, as_of=end)
        cached = json.loads(
            (root / ASSET_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        return {**cached, "status": "cached"}
    except ValueError:
        pass
    observed_at = datetime.now(timezone.utc).isoformat()
    pieces = []
    for code in contract["index_sources"]:
        frame = pro.index_global(ts_code=code, start_date=start, end_date=end)
        pieces.append(_normalize_index(frame, observed_at=observed_at))
    indexes = pd.concat(pieces, ignore_index=True, sort=False)
    fx_raw = pro.fx_daily(
        ts_code=next(iter(contract["fx_sources"])),
        start_date=start,
        end_date=end,
    )
    fx = _normalize_fx(fx_raw, observed_at=observed_at)

    minimum_index = int(contract["minimum_rows_per_index"])
    counts = indexes.groupby("ts_code").size().astype(int).to_dict()
    if set(counts) != set(contract["index_sources"]) or any(
        int(counts.get(code, 0)) < minimum_index for code in contract["index_sources"]
    ):
        raise ValueError("qdii_global_context_index_coverage")
    if len(fx) < int(contract["minimum_fx_rows"]):
        raise ValueError("qdii_global_context_fx_coverage")
    end_timestamp = datetime.strptime(end, "%Y%m%d")
    for code, group in indexes.groupby("ts_code"):
        observed_end = datetime.strptime(str(group["trade_date"].max()), "%Y%m%d")
        if (
            str(group["trade_date"].min()) > "20180108"
            or (end_timestamp - observed_end).days > 4
        ):
            raise ValueError(f"qdii_global_context_index_bounds:{code}")
    fx_end = datetime.strptime(str(fx["trade_date"].max()), "%Y%m%d")
    if (
        str(fx["trade_date"].min()) > "20180108"
        or (end_timestamp - fx_end).days > 4
    ):
        raise ValueError("qdii_global_context_fx_bounds")

    asset_root = root / ASSET_ROOT
    index_path = asset_root / "index_global.parquet"
    fx_path = asset_root / "fx_daily.parquet"
    _atomic_parquet(indexes, index_path)
    _atomic_parquet(fx, fx_path)
    files = {}
    for path, frame in ((index_path, indexes), (fx_path, fx)):
        relative = str(path.relative_to(root))
        files[relative] = {
            "path": relative,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "rows": len(frame),
            "min_date": str(frame["trade_date"].min()),
            "max_date": str(frame["trade_date"].max()),
        }
    manifest = {
        "schema_version": 1,
        "protocol": contract["protocol"],
        "contract_sha256": contract["contract_sha256"],
        "start_date": start,
        "end_date": end,
        "observed_at": observed_at,
        "index_counts": counts,
        "fx_rows": len(fx),
        "files": files,
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    write_text_atomic(
        asset_root / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**manifest, "status": "complete"}


def load_verified_global_context(
    repo_root: str | Path,
    *,
    contract_path: str | Path,
    as_of: str | None = None,
) -> dict[str, pd.DataFrame]:
    root = Path(repo_root).resolve()
    config = Path(contract_path)
    if not config.is_absolute():
        config = root / config
    contract = load_contract(config)
    asset_root = root / ASSET_ROOT
    try:
        manifest = json.loads((asset_root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("qdii_global_context_manifest_missing") from exc
    expected_manifest_hash = manifest.pop("manifest_sha256", None)
    if expected_manifest_hash != _canonical_hash(manifest):
        raise ValueError("qdii_global_context_manifest_hash")
    if (
        manifest.get("protocol") != contract["protocol"]
        or manifest.get("contract_sha256") != contract["contract_sha256"]
    ):
        raise ValueError("qdii_global_context_manifest_contract")
    if as_of is not None and str(manifest.get("end_date")) < _date_key(as_of):
        raise ValueError("qdii_global_context_stale")
    frames = {}
    for relative, record in (manifest.get("files") or {}).items():
        path = root / relative
        if not path.is_file() or _sha256(path) != record.get("sha256"):
            raise ValueError(f"qdii_global_context_file_hash:{relative}")
        frame = pd.read_parquet(path)
        if len(frame) != int(record.get("rows") or -1):
            raise ValueError(f"qdii_global_context_file_rows:{relative}")
        frames[path.stem] = frame
    if set(frames) != {"index_global", "fx_daily"}:
        raise ValueError("qdii_global_context_files")
    return frames


def _index_metrics(indexes: pd.DataFrame, lag_days: int) -> pd.DataFrame:
    parts = []
    for code, group in indexes.groupby("ts_code", sort=True):
        ordered = group.sort_values("trade_date").copy()
        close = pd.to_numeric(ordered["close"], errors="coerce")
        returns = close.pct_change(fill_method=None)
        ordered["global_index_momentum"] = close.pct_change(20, fill_method=None)
        ordered["global_volatility"] = (
            returns.rolling(20, min_periods=10).std() * np.sqrt(252.0)
        )
        source_dates = pd.to_datetime(ordered["trade_date"], format="%Y%m%d")
        ordered["global_source_trade_date"] = ordered["trade_date"].astype("string")
        ordered["global_available_date"] = (
            source_dates + pd.to_timedelta(lag_days, unit="D")
        ).dt.strftime("%Y%m%d")
        ordered["_available_at"] = pd.to_datetime(
            ordered["global_available_date"], format="%Y%m%d"
        )
        ordered["global_source_index_code"] = str(code)
        parts.append(ordered[[
            "global_source_index_code", "global_source_trade_date",
            "global_available_date", "global_index_momentum",
            "global_volatility", "_available_at",
        ]])
    return pd.concat(parts, ignore_index=True, sort=False)


def _fx_metrics(fx: pd.DataFrame, lag_days: int) -> pd.DataFrame:
    close_column = "bid_close" if "bid_close" in fx.columns else "close"
    ordered = fx.sort_values("trade_date").copy()
    close = pd.to_numeric(ordered[close_column], errors="coerce")
    ordered["rmb_depreciation"] = close.pct_change(20, fill_method=None)
    ordered["fx_source_code"] = ordered["ts_code"].astype("string")
    ordered["fx_source_trade_date"] = ordered["trade_date"].astype("string")
    ordered["fx_available_date"] = (
        pd.to_datetime(ordered["trade_date"], format="%Y%m%d")
        + pd.to_timedelta(lag_days, unit="D")
    ).dt.strftime("%Y%m%d")
    ordered["_available_at"] = pd.to_datetime(
        ordered["fx_available_date"], format="%Y%m%d"
    )
    return ordered[[
        "fx_source_code", "fx_source_trade_date",
        "fx_available_date", "rmb_depreciation",
        "_available_at",
    ]]


def attach_global_context(
    features: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
    *,
    contract: Mapping[str, Any],
) -> pd.DataFrame:
    required = {"trade_date", "index_key"}
    if required.difference(features.columns):
        raise ValueError("qdii_global_context_feature_schema")
    indexes = frames.get("index_global", pd.DataFrame())
    fx = frames.get("fx_daily", pd.DataFrame())
    if indexes.empty or fx.empty:
        raise ValueError("qdii_global_context_frames")
    lag_days = int(contract["availability_lag_calendar_days"])
    metrics = _index_metrics(indexes, lag_days)
    fx_values = _fx_metrics(fx, lag_days)
    result = features.drop(columns=list(FEATURE_COLUMNS), errors="ignore").copy()
    result["_row_order"] = np.arange(len(result))
    result["trade_date"] = result["trade_date"].astype("string").str[:8]
    result["_target_at"] = pd.to_datetime(
        result["trade_date"], format="%Y%m%d", errors="coerce"
    )
    mappings = result["index_key"].map(
        lambda value: mapping_for_index_key(value, contract)
    )
    result["global_source_index_code"] = mappings.map(
        lambda value: value[0] if value else pd.NA
    ).astype("string")
    result["global_mapping_kind"] = mappings.map(
        lambda value: value[1] if value else pd.NA
    ).astype("string")
    outputs = []
    for code, part in result.groupby("global_source_index_code", dropna=False, sort=False):
        left = part.copy()
        if pd.isna(code):
            left["global_source_trade_date"] = pd.Series(
                pd.NA, index=left.index, dtype="string"
            )
            left["global_available_date"] = pd.Series(
                pd.NA, index=left.index, dtype="string"
            )
            left["global_index_momentum"] = np.nan
            left["global_volatility"] = np.nan
            outputs.append(left)
            continue
        right = metrics.loc[metrics["global_source_index_code"].eq(str(code))].copy()
        merged = pd.merge_asof(
            left.sort_values("_target_at"),
            right.drop(columns="global_source_index_code").sort_values("_available_at"),
            left_on="_target_at", right_on="_available_at",
            direction="backward", allow_exact_matches=True,
        )
        outputs.append(merged)
    if not outputs:
        raise ValueError("qdii_global_context_no_rows")
    result = pd.concat(outputs, ignore_index=True, sort=False)
    fx_target = result[["_target_at", "_row_order"]].copy()
    fx_attached = pd.merge_asof(
        fx_target.sort_values("_target_at"),
        fx_values.sort_values("_available_at"),
        left_on="_target_at", right_on="_available_at",
        direction="backward", allow_exact_matches=True,
    ).sort_values("_row_order")
    result = result.sort_values("_row_order")
    for column in (
        "fx_source_code", "fx_source_trade_date",
        "fx_available_date", "rmb_depreciation",
    ):
        result[column] = fx_attached[column].to_numpy()
    return result.drop(
        columns=["_row_order", "_target_at", "_available_at"],
        errors="ignore",
    ).reset_index(drop=True)


def repair_feature_snapshot(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    contract_path: str | Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    snapshot = _date_key(snapshot_date)
    config = Path(contract_path)
    if not config.is_absolute():
        config = root / config
    contract = load_contract(config)
    frames = load_verified_global_context(root, contract_path=config, as_of=snapshot)
    path = root / "data/research/features/cn_qdii_etf" / f"{snapshot}.parquet"
    original = pd.read_parquet(path)
    repaired = attach_global_context(original, frames, contract=contract)
    original_identity = [
        (str(code), _date_key(trade_date))
        for code, trade_date in zip(original["code"], original["trade_date"])
    ]
    repaired_identity = [
        (str(code), _date_key(trade_date))
        for code, trade_date in zip(repaired["code"], repaired["trade_date"])
    ]
    if len(repaired) != len(original) or repaired_identity != original_identity:
        raise ValueError("qdii_global_context_repair_identity")
    old_hash = _sha256(path)
    backup = (
        root / "data/research/feature_revisions/cn_qdii_etf"
        / snapshot / f"{old_hash}.parquet"
    )
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    _atomic_parquet(repaired, path)
    coverage = {
        column: float(pd.to_numeric(repaired[column], errors="coerce").notna().mean())
        for column in ("global_index_momentum", "global_volatility", "rmb_depreciation")
    }
    mapped_coverage = float(repaired["global_mapping_kind"].notna().mean())
    if mapped_coverage < float(contract["minimum_mapped_row_coverage"]):
        raise ValueError("qdii_global_context_mapping_coverage")
    weak = [
        column for column, value in coverage.items()
        if value < float(contract["minimum_feature_coverage"])
    ]
    if weak:
        raise ValueError(
            "qdii_global_context_feature_coverage:" + ",".join(weak)
        )
    valid_index = repaired["global_source_trade_date"].notna()
    if bool((
        repaired.loc[valid_index, "global_source_trade_date"].astype(str)
        >= repaired.loc[valid_index, "trade_date"].astype(str)
    ).any()) or bool((
        repaired.loc[valid_index, "global_available_date"].astype(str)
        > repaired.loc[valid_index, "trade_date"].astype(str)
    ).any()):
        raise ValueError("qdii_global_context_index_leakage")
    valid_fx = repaired["fx_source_trade_date"].notna()
    if bool((
        repaired.loc[valid_fx, "fx_source_trade_date"].astype(str)
        >= repaired.loc[valid_fx, "trade_date"].astype(str)
    ).any()) or bool((
        repaired.loc[valid_fx, "fx_available_date"].astype(str)
        > repaired.loc[valid_fx, "trade_date"].astype(str)
    ).any()):
        raise ValueError("qdii_global_context_fx_leakage")
    audit = {
        "status": "complete",
        "protocol": contract["protocol"],
        "contract_sha256": contract["contract_sha256"],
        "snapshot_date": snapshot,
        "rows": len(repaired),
        "old_sha256": old_hash,
        "new_sha256": _sha256(path),
        "backup": str(backup.relative_to(root)),
        "coverage": coverage,
        "mapped_row_coverage": mapped_coverage,
        "exact_rows": int(repaired["global_mapping_kind"].eq("exact").sum()),
        "proxy_rows": int(repaired["global_mapping_kind"].eq("family_proxy").sum()),
        "unmapped_rows": int(repaired["global_mapping_kind"].isna().sum()),
    }
    audit_path = (
        root / "data/research/qdii_global_context/v1/repairs"
        / f"{snapshot}.json"
    )
    write_text_atomic(
        audit_path, json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**audit, "audit_path": str(audit_path)}


__all__ = [
    "ASSET_ROOT", "FEATURE_COLUMNS", "attach_global_context",
    "backfill_global_context", "load_contract",
    "load_verified_global_context", "mapping_for_index_key",
    "repair_feature_snapshot",
]
