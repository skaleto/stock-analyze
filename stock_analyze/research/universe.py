"""Point-in-time account universes for classical model research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


UNIVERSE_CONTRACT_VERSION = "pit-universe-v1"


@dataclass(frozen=True)
class PointInTimeUniverseResult:
    frame: pd.DataFrame
    metadata: dict[str, Any]


def _date_key(values: pd.Series) -> pd.Series:
    return values.astype("string").str.replace("-", "", regex=False).str[:8]


def _code_key(values: pd.Series) -> pd.Series:
    return values.astype("string").str.upper().str.split(".").str[0].str.zfill(6)


def _account_rows(accounts: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for order, account in enumerate(accounts):
        account_id = str(account.get("id") or account.get("scope") or "")
        if not account_id:
            continue
        rows.append({
            "account_id": account_id,
            "research_scope": str(account.get("scope") or account_id),
            "benchmark_code": str(account.get("benchmark") or ""),
            "_account_order": str(order).zfill(4),
        })
    return rows


def _unavailable(frame: pd.DataFrame, reason: str) -> PointInTimeUniverseResult:
    result = frame.copy()
    result["account_id"] = "unscoped"
    result["research_scope"] = result.get(
        "research_scope", pd.Series("unscoped", index=result.index)
    ).fillna("unscoped")
    result["benchmark_code"] = ""
    result["universe_quality"] = "unavailable"
    result["unbiased_universe"] = False
    result["universe_contract_version"] = UNIVERSE_CONTRACT_VERSION
    result["membership_source"] = "unavailable"
    return PointInTimeUniverseResult(result, {
        "quality": "unavailable",
        "unbiased_universe": False,
        "membership_source": "unavailable",
        "universe_contract_version": UNIVERSE_CONTRACT_VERSION,
        "coverage": 0.0,
        "quality_reasons": [reason],
    })


def _decorate(
    frame: pd.DataFrame,
    *,
    source: str,
    unbiased: bool,
    input_rows: int,
    reasons: list[str] | None = None,
) -> PointInTimeUniverseResult:
    result = frame.copy()
    quality = "available" if unbiased else "unavailable"
    result["universe_quality"] = quality
    result["unbiased_universe"] = bool(unbiased)
    result["universe_contract_version"] = UNIVERSE_CONTRACT_VERSION
    result["membership_source"] = source
    coverage = len(result) / max(input_rows, 1)
    return PointInTimeUniverseResult(result, {
        "quality": quality,
        "unbiased_universe": bool(unbiased),
        "membership_source": source,
        "universe_contract_version": UNIVERSE_CONTRACT_VERSION,
        "coverage": float(coverage),
        "input_rows": int(input_rows),
        "eligible_rows": int(len(result)),
        "quality_reasons": list(reasons or []),
    })


def _attach_a_share(
    frame: pd.DataFrame,
    *,
    repo_root: Path,
    accounts: list[dict[str, str]],
) -> PointInTimeUniverseResult:
    weight_root = repo_root / "data" / "shared" / "backtest_cache" / "index_weight"
    if not weight_root.exists() or not accounts:
        return _unavailable(frame, "index_weight_history_missing")
    prepared = frame.copy()
    prepared["code"] = _code_key(prepared["code"])
    prepared["trade_date"] = _date_key(prepared["trade_date"])
    prepared["_month"] = prepared["trade_date"].str[:6]
    memberships: list[pd.DataFrame] = []
    missing_months: list[str] = []
    for account in accounts:
        prefix = str(account["benchmark_code"]).split(".")[0].zfill(6)
        snapshots = sorted(weight_root.glob(f"{prefix}_????-??.csv"))
        parsed = [
            (path.stem.rsplit("_", 1)[-1].replace("-", ""), path)
            for path in snapshots
        ]
        for month in sorted(prepared["_month"].dropna().unique()):
            eligible = [item for item in parsed if item[0] <= str(month)]
            if not eligible:
                missing_months.append(f"{account['account_id']}:{month}")
                continue
            snapshot_month, path = eligible[-1]
            weights = pd.read_csv(
                path,
                dtype={"con_code": str, "index_code": str, "trade_date": str},
            )
            if "con_code" not in weights.columns:
                missing_months.append(f"{account['account_id']}:{month}:schema")
                continue
            if weights.empty or not weights["con_code"].dropna().astype(str).str.strip().any():
                missing_months.append(f"{account['account_id']}:{month}:empty")
                continue
            members = pd.DataFrame({
                "_month": str(month),
                "code": _code_key(weights["con_code"]),
            }).drop_duplicates()
            for key, value in account.items():
                members[key] = value
            members["membership_snapshot"] = snapshot_month
            memberships.append(members)
    if not memberships:
        return _unavailable(frame, "index_weight_history_unusable")
    prepared["_row_id"] = range(len(prepared))
    membership = pd.concat(memberships, ignore_index=True, sort=False)
    merged = prepared.merge(membership, on=["_month", "code"], how="inner")
    merged = (
        merged.sort_values(["_row_id", "_account_order"], kind="stable")
        .drop_duplicates("_row_id", keep="first")
        .drop(columns=["_row_id", "_month", "_account_order"])
        .reset_index(drop=True)
    )
    reasons = [
        (
            f"empty_snapshot:{item.removesuffix(':empty')}"
            if item.endswith(":empty")
            else f"missing_snapshot:{item}"
        )
        for item in sorted(set(missing_months))
    ]
    return _decorate(
        merged,
        source="monthly_index_weight",
        unbiased=bool(len(merged) and not reasons),
        input_rows=len(frame),
        reasons=reasons,
    )


def _historical_qdii_catalog(basic: pd.DataFrame, *, as_of: str) -> pd.DataFrame:
    from ..markets.cn_qdii_etf.universe import build_catalog_candidates

    source = basic.copy()
    source["ts_code"] = source["ts_code"].astype("string")
    source["_original_status"] = source.get("status", "")
    source["_original_delist_date"] = source.get("delist_date", "")
    source["status"] = "L"
    source["delist_date"] = ""
    catalog = pd.DataFrame(build_catalog_candidates(source, as_of=as_of))
    if catalog.empty:
        return catalog
    original = basic.copy()
    original["code"] = _code_key(original["ts_code"])
    original = original.drop_duplicates("code", keep="last").set_index("code")
    catalog["code"] = _code_key(catalog["code"])
    catalog["list_date"] = catalog["code"].map(original.get("list_date"))
    catalog["delist_date"] = catalog["code"].map(original.get("delist_date"))
    catalog["historical_status"] = catalog["code"].map(original.get("status"))
    return catalog


def _attach_qdii(
    frame: pd.DataFrame,
    *,
    repo_root: Path,
    accounts: list[dict[str, str]],
    as_of: str,
) -> PointInTimeUniverseResult:
    cache = repo_root / "data" / "cn_qdii_etf" / "shared" / "cache"
    path = next(
        (cache / name for name in ("fund_basic_E_v2.csv", "fund_basic_E.csv") if (cache / name).exists()),
        None,
    )
    if path is None or not accounts:
        return _unavailable(frame, "fund_basic_history_missing")
    basic = pd.read_csv(
        path,
        dtype={"ts_code": str, "status": str, "list_date": str, "delist_date": str},
    )
    catalog = _historical_qdii_catalog(basic, as_of=as_of)
    if catalog.empty:
        return _unavailable(frame, "fund_basic_catalog_unusable")
    account_by_scope = {row["research_scope"]: row for row in accounts}
    catalog = catalog.loc[catalog["scope"].isin(account_by_scope)].copy()
    for column in ("account_id", "benchmark_code", "_account_order"):
        catalog[column] = catalog["scope"].map({
            scope: account[column] for scope, account in account_by_scope.items()
        })
    catalog["research_scope"] = catalog["scope"]
    catalog["_list_date"] = _date_key(catalog["list_date"])
    catalog["_delist_date"] = _date_key(catalog["delist_date"])
    metadata_columns = [
        column for column in (
            "code", "account_id", "research_scope", "benchmark_code",
            "index_key", "theme", "sector", "country", "asset_class",
            "_list_date", "_delist_date", "_account_order",
        ) if column in catalog.columns
    ]
    prepared = frame.copy()
    prepared["code"] = _code_key(prepared["code"])
    prepared["trade_date"] = _date_key(prepared["trade_date"])
    prepared["_row_id"] = range(len(prepared))
    existing_metadata = [
        column for column in metadata_columns
        if column != "code" and column in prepared.columns
    ]
    prepared = prepared.drop(columns=existing_metadata)
    merged = prepared.merge(
        catalog[metadata_columns].drop_duplicates("code", keep="first"),
        on="code",
        how="inner",
    )
    listed = merged["_list_date"].notna() & merged["_list_date"].le(merged["trade_date"])
    active = merged["_delist_date"].isna() | merged["_delist_date"].eq("") | merged["_delist_date"].gt(merged["trade_date"])
    merged = (
        merged.loc[listed & active]
        .sort_values(["_row_id", "_account_order"], kind="stable")
        .drop_duplicates("_row_id", keep="first")
        .drop(columns=["_row_id", "_list_date", "_delist_date", "_account_order"])
        .reset_index(drop=True)
    )
    statuses = set(basic.get("status", pd.Series(dtype=str)).dropna().astype(str).str.upper())
    complete_master = {"L", "D"}.issubset(statuses) and bool(catalog["list_date"].notna().all())
    reasons = [] if complete_master else ["fund_basic_status_history_incomplete"]
    return _decorate(
        merged,
        source="tushare_fund_basic_listing_interval",
        unbiased=bool(complete_master and len(merged)),
        input_rows=len(frame),
        reasons=reasons,
    )


def attach_point_in_time_universe(
    features: pd.DataFrame,
    *,
    repo_root: str | Path,
    market: str,
    accounts: Iterable[Mapping[str, Any]],
    as_of: str,
) -> PointInTimeUniverseResult:
    """Filter a feature panel to the account membership known for each date."""

    required = {"code", "trade_date"}
    if required.difference(features.columns):
        raise ValueError("research_universe_schema")
    account_rows = _account_rows(accounts)
    if market == "a_share":
        return _attach_a_share(features, repo_root=Path(repo_root), accounts=account_rows)
    if market == "cn_qdii_etf":
        return _attach_qdii(
            features,
            repo_root=Path(repo_root),
            accounts=account_rows,
            as_of=as_of,
        )
    return _unavailable(features, f"unsupported_market:{market}")


__all__ = [
    "PointInTimeUniverseResult",
    "UNIVERSE_CONTRACT_VERSION",
    "attach_point_in_time_universe",
]
