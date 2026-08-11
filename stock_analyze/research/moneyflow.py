"""Point-in-time A-share money-flow features and resumable history backfill."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..markets.a_share.data_provider import ts_code_for_stock
from ..utils import write_text_atomic


MONEYFLOW_CACHE_VERSION = "tushare-moneyflow-history-v1"
MONEYFLOW_FEATURE_VERSION = "moneyflow-pit-v1"
MONEYFLOW_MODEL_CACHE_VERSION = "tushare-moneyflow-model-columns-v1"
MONEYFLOW_MODEL_COLUMNS = (
    "ts_code",
    "trade_date",
    "net_mf_amount",
    "buy_lg_amount",
    "buy_elg_amount",
    "sell_lg_amount",
    "sell_elg_amount",
)
MONEYFLOW_FEATURE_COLUMNS = (
    "moneyflow_net_ratio_1",
    "moneyflow_net_ratio_5",
    "moneyflow_net_ratio_20",
    "moneyflow_positive_days_5",
    "moneyflow_large_imbalance_5",
    "moneyflow_observed",
)


class _RequestRateLimiter:
    def __init__(self, requests_per_minute: float) -> None:
        rate = float(requests_per_minute)
        self._interval = 0.0 if rate <= 0.0 else 60.0 / rate
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self) -> None:
        if self._interval <= 0.0:
            return
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._interval
        if delay:
            time.sleep(delay)


def _date_key(value: str) -> str:
    normalized = str(value).replace("-", "")[:8]
    if not re.fullmatch(r"\d{8}", normalized):
        raise ValueError(f"moneyflow_date_invalid:{value}")
    return normalized


def _normalize_codes(codes: Iterable[str]) -> list[str]:
    normalized = {
        ts_code_for_stock(str(code))
        for code in codes
        if ts_code_for_stock(str(code))
    }
    if not normalized:
        raise ValueError("moneyflow_codes_empty")
    return sorted(normalized)


def _rolling_sum(
    values: pd.Series,
    codes: pd.Series,
    *,
    window: int,
    minimum: int,
) -> pd.Series:
    return (
        values.groupby(codes, sort=False)
        .rolling(window, min_periods=minimum)
        .sum()
        .reset_index(level=0, drop=True)
        .sort_index()
    )


def _rolling_mean(
    values: pd.Series,
    codes: pd.Series,
    *,
    window: int,
    minimum: int,
) -> pd.Series:
    return (
        values.groupby(codes, sort=False)
        .rolling(window, min_periods=minimum)
        .mean()
        .reset_index(level=0, drop=True)
        .sort_index()
    )


def attach_moneyflow_point_in_time_features(
    prices: pd.DataFrame,
    moneyflow: pd.DataFrame,
) -> pd.DataFrame:
    """Attach after-close money flow by exact stock/date, never by carry-forward."""

    result = prices.drop(columns=list(MONEYFLOW_FEATURE_COLUMNS), errors="ignore").copy()
    if result.empty:
        for column in MONEYFLOW_FEATURE_COLUMNS:
            result[column] = pd.Series(dtype="int8" if column == "moneyflow_observed" else float)
        return result
    required = {"code", "trade_date"}
    if required.difference(result.columns):
        raise ValueError("moneyflow_feature_price_keys_missing")
    result["_moneyflow_order"] = np.arange(len(result), dtype=np.int64)
    result["_moneyflow_code"] = (
        result["code"].astype("string").str.split(".").str[0].str.zfill(6)
    )
    result["_moneyflow_date"] = (
        result["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
    )
    source_required = {"ts_code", "trade_date", "net_mf_amount"}
    if moneyflow.empty or source_required.difference(moneyflow.columns):
        for column in MONEYFLOW_FEATURE_COLUMNS[:-1]:
            result[column] = np.nan
        result["moneyflow_observed"] = np.int8(0)
        return result.drop(
            columns=["_moneyflow_order", "_moneyflow_code", "_moneyflow_date"]
        )

    source = moneyflow.copy()
    source["_moneyflow_code"] = (
        source["ts_code"].astype("string").str.split(".").str[0].str.zfill(6)
    )
    source["_moneyflow_date"] = (
        source["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
    )
    wanted_codes = set(result["_moneyflow_code"].dropna().astype(str))
    source = source.loc[
        source["_moneyflow_code"].isin(wanted_codes)
        & source["_moneyflow_date"].le(str(result["_moneyflow_date"].max()))
    ].copy()
    amount_columns = (
        "net_mf_amount",
        "buy_lg_amount",
        "buy_elg_amount",
        "sell_lg_amount",
        "sell_elg_amount",
    )
    for column in amount_columns:
        source[column] = pd.to_numeric(
            source[column] if column in source.columns else np.nan,
            errors="coerce",
        )
    source = source.drop_duplicates(
        ["_moneyflow_code", "_moneyflow_date"], keep="last"
    )
    source = source.loc[:, ["_moneyflow_code", "_moneyflow_date", *amount_columns]]
    result = result.merge(
        source,
        on=["_moneyflow_code", "_moneyflow_date"],
        how="left",
        validate="one_to_one",
    ).sort_values(["_moneyflow_code", "_moneyflow_date"], kind="stable")

    if "amount_yuan" in result.columns:
        traded_amount = pd.to_numeric(result["amount_yuan"], errors="coerce")
    elif "amount" in result.columns:
        traded_amount = pd.to_numeric(result["amount"], errors="coerce")
        if "amount_unit" in result.columns:
            units = result["amount_unit"].astype("string")
            traded_amount = traded_amount.where(~units.eq("thousand_yuan"), traded_amount * 1_000.0)
    else:
        traded_amount = pd.Series(np.nan, index=result.index, dtype=float)

    observed = result["net_mf_amount"].notna() & traded_amount.gt(0.0)
    net_yuan = result["net_mf_amount"] * 10_000.0
    net_yuan = net_yuan.where(observed)
    observed_amount = traded_amount.where(observed)
    result["moneyflow_net_ratio_1"] = net_yuan / observed_amount
    codes = result["_moneyflow_code"]
    for window, minimum in ((5, 3), (20, 10)):
        net_sum = _rolling_sum(net_yuan, codes, window=window, minimum=minimum)
        amount_sum = _rolling_sum(
            observed_amount,
            codes,
            window=window,
            minimum=minimum,
        )
        result[f"moneyflow_net_ratio_{window}"] = net_sum / amount_sum.replace(0.0, np.nan)

    positive = net_yuan.gt(0.0).astype(float).where(observed)
    result["moneyflow_positive_days_5"] = _rolling_mean(
        positive,
        codes,
        window=5,
        minimum=3,
    )
    large_buy = result["buy_lg_amount"].fillna(0.0) + result["buy_elg_amount"].fillna(0.0)
    large_sell = result["sell_lg_amount"].fillna(0.0) + result["sell_elg_amount"].fillna(0.0)
    large_net = (large_buy - large_sell).where(observed)
    large_total = (large_buy + large_sell).where(observed)
    result["moneyflow_large_imbalance_5"] = _rolling_sum(
        large_net,
        codes,
        window=5,
        minimum=3,
    ) / _rolling_sum(
        large_total,
        codes,
        window=5,
        minimum=3,
    ).replace(0.0, np.nan)
    result["moneyflow_observed"] = observed.astype("int8")
    return result.sort_values("_moneyflow_order", kind="stable").drop(
        columns=[
            "_moneyflow_order",
            "_moneyflow_code",
            "_moneyflow_date",
            *amount_columns,
        ]
    ).reset_index(drop=True)


def _contract_hash(codes: list[str], start_date: str, end_date: str) -> str:
    payload = {
        "version": MONEYFLOW_CACHE_VERSION,
        "codes": codes,
        "start_date": start_date,
        "end_date": end_date,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        frame.to_parquet(temporary_name, index=False, compression="zstd")
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_moneyflow_model_cache(
    repo_root: str | Path,
    *,
    codes: Iterable[str],
    source_contract_hash: str,
) -> dict[str, Any]:
    """Compact raw per-code history into one predicate-friendly model cache."""

    normalized_codes = _normalize_codes(codes)
    root = Path(repo_root) / "data" / "shared" / "backtest_cache" / "moneyflow"
    model_root = root / "model"
    model_root.mkdir(parents=True, exist_ok=True)
    destination = model_root / "moneyflow.parquet"
    manifest_path = model_root / "manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        previous = {}
    if (
        previous.get("version") == MONEYFLOW_MODEL_CACHE_VERSION
        and previous.get("source_contract_hash") == source_contract_hash
        and int(previous.get("source_files") or 0) == len(normalized_codes)
        and destination.is_file()
        and destination.stat().st_size == int(previous.get("bytes") or -1)
    ):
        return {
            "status": "cached",
            "rows": int(previous.get("rows") or 0),
            "bytes": int(previous.get("bytes") or 0),
            "path": str(destination),
            "sha256": str(previous.get("sha256") or ""),
        }

    temporary_name: str | None = None
    writer: pq.ParquetWriter | None = None
    rows = 0
    try:
        with tempfile.NamedTemporaryFile(
            dir=model_root,
            prefix=".moneyflow.parquet.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        for code in normalized_codes:
            path = root / f"{code}.parquet"
            if not path.is_file():
                raise FileNotFoundError(f"moneyflow_raw_cache_missing:{code}")
            available = set(pq.ParquetFile(path).schema_arrow.names)
            missing = set(MONEYFLOW_MODEL_COLUMNS).difference(available)
            if missing:
                raise ValueError(
                    f"moneyflow_model_columns_missing:{code}:"
                    + ",".join(sorted(missing))
                )
            table = pq.read_table(path, columns=list(MONEYFLOW_MODEL_COLUMNS))
            if writer is None:
                writer = pq.ParquetWriter(
                    temporary_name,
                    table.schema,
                    compression="zstd",
                    compression_level=9,
                    use_dictionary=True,
                    write_statistics=True,
                )
            elif table.schema != writer.schema:
                table = table.cast(writer.schema, safe=False)
            writer.write_table(table)
            rows += int(table.num_rows)
        if writer is None:
            raise ValueError("moneyflow_model_cache_empty")
        writer.close()
        writer = None
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if writer is not None:
            writer.close()
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)

    payload = {
        "schema_version": 1,
        "version": MONEYFLOW_MODEL_CACHE_VERSION,
        "source_contract_hash": source_contract_hash,
        "source_files": len(normalized_codes),
        "rows": rows,
        "columns": list(MONEYFLOW_MODEL_COLUMNS),
        "bytes": destination.stat().st_size,
        "sha256": _file_sha256(destination),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_text_atomic(
        manifest_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "status": "built",
        "rows": rows,
        "bytes": int(payload["bytes"]),
        "path": str(destination),
        "sha256": str(payload["sha256"]),
    }


def _fetch_one(
    pro: Any,
    *,
    ts_code: str,
    start_date: str,
    end_date: str,
    destination: Path,
    retries: int,
    rate_limiter: _RequestRateLimiter,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            rate_limiter.wait()
            frame = pro.moneyflow(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                raise ValueError("moneyflow_source_empty")
            missing = {"ts_code", "trade_date", "net_mf_amount"}.difference(frame.columns)
            if missing:
                raise ValueError(
                    "moneyflow_source_columns_missing:" + ",".join(sorted(missing))
                )
            normalized = frame.copy()
            normalized["ts_code"] = normalized["ts_code"].astype("string")
            normalized["trade_date"] = normalized["trade_date"].astype("string").str[:8]
            normalized = normalized.loc[
                normalized["ts_code"].eq(ts_code)
                & normalized["trade_date"].between(start_date, end_date)
            ].drop_duplicates(["ts_code", "trade_date"], keep="last")
            if normalized.empty:
                raise ValueError("moneyflow_source_scope_empty")
            normalized["source"] = "tushare:moneyflow"
            normalized["observed_at"] = datetime.now(timezone.utc).isoformat()
            normalized = normalized.sort_values("trade_date", kind="stable").reset_index(drop=True)
            _atomic_parquet(destination, normalized)
            return {
                "status": "complete",
                "rows": int(len(normalized)),
                "min_date": str(normalized["trade_date"].min()),
                "max_date": str(normalized["trade_date"].max()),
                "file": destination.name,
            }
        except Exception as exc:  # noqa: BLE001 - persisted bounded failure
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(min(2.0 ** attempt, 20.0))
    message = re.sub(
        r"(?i)(token|api_key|apikey)=([^&\s]+)",
        r"\1=<redacted>",
        str(last_error or "unknown"),
    )
    return {"status": "failed", "rows": 0, "error": message[:240]}


def backfill_moneyflow_history(
    repo_root: str | Path,
    *,
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    pro: Any | None = None,
    max_workers: int = 4,
    retries: int = 3,
    requests_per_minute: float = 180,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch one bounded per-stock history file and checkpoint every result."""

    start = _date_key(start_date)
    end = _date_key(end_date)
    if start > end:
        raise ValueError("moneyflow_date_range_invalid")
    normalized_codes = _normalize_codes(codes)
    contract_hash = _contract_hash(normalized_codes, start, end)
    root = Path(repo_root) / "data" / "shared" / "backtest_cache" / "moneyflow"
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        previous = {}
    previous_results = previous.get("results") if isinstance(previous, dict) else {}
    if not isinstance(previous_results, dict):
        previous_results = {}
    if (
        not force
        and previous.get("status") == "complete"
        and previous.get("contract_hash") == contract_hash
        and all((root / f"{code}.parquet").is_file() for code in normalized_codes)
    ):
        model_cache = build_moneyflow_model_cache(
            repo_root,
            codes=normalized_codes,
            source_contract_hash=contract_hash,
        )
        return {
            "status": "cached",
            "contract_hash": contract_hash,
            "target_codes": len(normalized_codes),
            "completed_codes": len(normalized_codes),
            "failed_codes": 0,
            "rows": int(previous.get("rows") or 0),
            "path": str(root),
            "model_cache": model_cache,
        }

    previous_range_matches = (
        previous.get("version") == MONEYFLOW_CACHE_VERSION
        and previous.get("start_date") == start
        and previous.get("end_date") == end
    )
    results: dict[str, dict[str, Any]] = {
        str(code): dict(record)
        for code, record in previous_results.items()
        if (
            not force
            and previous_range_matches
            and code in normalized_codes
            and isinstance(record, dict)
            and record.get("status") == "complete"
            and (root / f"{code}.parquet").is_file()
        )
    }
    pending = [code for code in normalized_codes if code not in results]
    if pending and pro is None:
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            raise ValueError("moneyflow_tushare_token_missing")
        import tushare as ts

        pro = ts.pro_api(token)

    created_at = str(previous.get("created_at") or datetime.now(timezone.utc).isoformat())
    rate_limiter = _RequestRateLimiter(requests_per_minute)

    def checkpoint(status: str) -> None:
        completed = sum(record.get("status") == "complete" for record in results.values())
        failed = sum(record.get("status") == "failed" for record in results.values())
        rows = sum(int(record.get("rows") or 0) for record in results.values())
        payload = {
            "schema_version": 1,
            "version": MONEYFLOW_CACHE_VERSION,
            "status": status,
            "contract_hash": contract_hash,
            "start_date": start,
            "end_date": end,
            "target_codes": len(normalized_codes),
            "completed_codes": completed,
            "failed_codes": failed,
            "rows": rows,
            "created_at": created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "results": {code: results[code] for code in sorted(results)},
        }
        write_text_atomic(
            manifest_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    checkpoint("running")
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = {
            pool.submit(
                _fetch_one,
                pro,
                ts_code=code,
                start_date=start,
                end_date=end,
                destination=root / f"{code}.parquet",
                retries=retries,
                rate_limiter=rate_limiter,
            ): code
            for code in pending
        }
        for index, future in enumerate(as_completed(futures), start=1):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception as exc:  # noqa: BLE001 - checkpoint unexpected worker error
                results[code] = {
                    "status": "failed",
                    "rows": 0,
                    "error": f"worker:{type(exc).__name__}:{str(exc)[:180]}",
                }
            if index % 20 == 0:
                checkpoint("running")

    completed = sum(record.get("status") == "complete" for record in results.values())
    failed = len(normalized_codes) - completed
    status = "complete" if failed == 0 else "partial"
    checkpoint(status)
    model_cache = (
        build_moneyflow_model_cache(
            repo_root,
            codes=normalized_codes,
            source_contract_hash=contract_hash,
        )
        if status == "complete"
        else None
    )
    return {
        "status": status,
        "contract_hash": contract_hash,
        "target_codes": len(normalized_codes),
        "completed_codes": completed,
        "failed_codes": failed,
        "rows": sum(int(record.get("rows") or 0) for record in results.values()),
        "path": str(root),
        "model_cache": model_cache,
    }


def load_moneyflow_cache(
    repo_root: str | Path,
    *,
    codes: Iterable[str],
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    root = Path(repo_root) / "data" / "shared" / "backtest_cache" / "moneyflow"
    normalized_codes = _normalize_codes(codes)
    consolidated = root / "model" / "moneyflow.parquet"
    if consolidated.is_file():
        filters: list[tuple[str, str, object]] = [
            ("ts_code", "in", normalized_codes),
        ]
        if start_date:
            filters.append(("trade_date", ">=", _date_key(start_date)))
        if end_date:
            filters.append(("trade_date", "<=", _date_key(end_date)))
        return pd.read_parquet(
            consolidated,
            columns=list(MONEYFLOW_MODEL_COLUMNS),
            filters=filters,
        ).reset_index(drop=True)
    parts: list[pd.DataFrame] = []
    for code in normalized_codes:
        path = root / f"{code}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        if start_date:
            frame = frame.loc[frame["trade_date"].astype(str).ge(_date_key(start_date))]
        if end_date:
            frame = frame.loc[frame["trade_date"].astype(str).le(_date_key(end_date))]
        if not frame.empty:
            parts.append(frame)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False).drop_duplicates(
        ["ts_code", "trade_date"], keep="last"
    ).reset_index(drop=True)


__all__ = [
    "MONEYFLOW_CACHE_VERSION",
    "MONEYFLOW_FEATURE_COLUMNS",
    "MONEYFLOW_FEATURE_VERSION",
    "MONEYFLOW_MODEL_CACHE_VERSION",
    "MONEYFLOW_MODEL_COLUMNS",
    "attach_moneyflow_point_in_time_features",
    "backfill_moneyflow_history",
    "build_moneyflow_model_cache",
    "load_moneyflow_cache",
]
