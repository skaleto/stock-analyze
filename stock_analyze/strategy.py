from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .data_provider import DataProvider
from .factor_pipeline import UNCLASSIFIED, process_factors
from .utils import now_iso, parse_date, safe_float


@dataclass
class SignalResult:
    account_id: str
    pool: str
    generated_at: str
    candidates: pd.DataFrame
    selected: pd.DataFrame
    warnings: list[str]
    factor_table: pd.DataFrame = field(default_factory=pd.DataFrame)


def build_signals(config: dict[str, Any], account: dict[str, Any], provider: DataProvider, as_of: str | None = None) -> SignalResult:
    warnings: list[str] = []
    scope = str(account["scope"])
    universe = provider.universe(scope)
    if universe.empty:
        raise RuntimeError(f"No universe data for {scope}")
    universe = preselect_universe(universe, config.get("filters", {}))

    rows: list[dict[str, Any]] = []
    filters = config.get("filters", {})
    for _, stock in universe.iterrows():
        code = str(stock["code"]).zfill(6)
        name = str(stock["name"])
        if filters.get("exclude_st") and "ST" in name.upper():
            continue

        basic = provider.basic_info(code)
        valuation = provider.valuation_metrics(code)
        metrics = provider.financial_metrics(code, as_of=as_of)
        snapshot = provider.price_snapshot(code, as_of=as_of, spot_row=stock.to_dict())
        dividend_yield = provider.dividend_yield(code, as_of=as_of)
        data_warnings: list[str] = []
        if valuation.get("pe") is None and safe_float(stock.get("pe")) is None:
            data_warnings.append("pe_missing")
        if valuation.get("pb") is None and safe_float(stock.get("pb")) is None:
            data_warnings.append("pb_missing")
        if metrics.get("fetch_error"):
            data_warnings.append("financial_fetch_failed")
        if snapshot.warning:
            data_warnings.append(snapshot.warning)
        listing_date = basic.get("listing_date")
        listing_age_days = listing_age(listing_date, as_of)
        if listing_age_days is None:
            data_warnings.append("listing_date_missing")
        market_cap_yi = safe_float(stock.get("market_cap_yi")) or safe_float(basic.get("market_cap_yi"))
        row = {
            "code": code,
            "name": name if name and name != "nan" else basic.get("name", ""),
            "industry": (basic.get("industry") or UNCLASSIFIED),
            "latest_price": safe_float(stock.get("latest_price")) or snapshot.close,
            "pe": safe_float(stock.get("pe")) or valuation.get("pe"),
            "pb": safe_float(stock.get("pb")) or valuation.get("pb"),
            "market_cap_yi": market_cap_yi,
            "listing_date": listing_date,
            "listing_age_days": listing_age_days,
            "roe": safe_float(metrics.get("roe")),
            "gross_margin": safe_float(metrics.get("gross_margin")),
            "debt_ratio": safe_float(metrics.get("debt_ratio")),
            "net_profit_growth": safe_float(metrics.get("net_profit_growth")),
            "momentum_20": snapshot.momentum_20,
            "momentum_60": snapshot.momentum_60,
            "low_volatility_60": snapshot.low_volatility_60,
            "dividend_yield": dividend_yield,
            "avg_amount_20": snapshot.avg_amount_20,
            "paused": snapshot.paused,
            "data_warnings": ";".join(data_warnings),
        }
        rows.append(row)

    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise RuntimeError(f"No candidates left after basic filters for {scope}")

    filtered = apply_hard_filters(candidates, filters)
    if filtered.empty:
        warnings.append("hard_filters_empty_relaxed")
        filtered = apply_relaxed_filters(candidates, filters)
    candidates = filtered
    if candidates.empty:
        raise RuntimeError(f"No candidates left after hard filters for {scope}")

    scored, factor_table = process_factors(candidates, config.get("factors", {}), config.get("factor_processing"))
    if scored.get("insufficient_factor_coverage", pd.Series([], dtype=bool)).any():
        scored = scored[~scored["insufficient_factor_coverage"]].copy()
    if scored.empty:
        raise RuntimeError(f"No candidates left after factor coverage filtering for {scope}")

    scored = scored.sort_values("score", ascending=False).reset_index(drop=True)
    initial_selected = scored.head(int(account.get("top_n", 10))).copy()
    initial_selected["account_id"] = account["id"]
    initial_selected["pool"] = scope
    if not factor_table.empty:
        factor_table = factor_table.copy()
        factor_table["account_id"] = account["id"]
        factor_table["signal_date"] = as_of or ""

    return SignalResult(
        account_id=str(account["id"]),
        pool=scope,
        generated_at=now_iso(),
        candidates=scored,
        selected=initial_selected,
        warnings=warnings,
        factor_table=factor_table,
    )


def preselect_universe(universe: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    max_candidates = int(filters.get("max_fetch_candidates") or 0)
    if max_candidates <= 0 or len(universe) <= max_candidates:
        return universe

    out = universe.copy()
    out["pe_rank_seed"] = pd.to_numeric(out.get("pe"), errors="coerce")
    out["pb_rank_seed"] = pd.to_numeric(out.get("pb"), errors="coerce")
    out["market_cap_rank_seed"] = pd.to_numeric(out.get("market_cap_yi"), errors="coerce")
    min_pe = safe_float(filters.get("min_pe"))
    if min_pe is not None:
        out = out[(out["pe_rank_seed"].isna()) | (out["pe_rank_seed"] > min_pe)]
    if out.empty:
        return universe.head(max_candidates)
    out["pre_score"] = 0.0
    has_seed = False
    if out["pe_rank_seed"].notna().any():
        out["pre_score"] += (1 - out["pe_rank_seed"].rank(pct=True)).fillna(0) * 0.35
        has_seed = True
    if out["pb_rank_seed"].notna().any():
        out["pre_score"] += (1 - out["pb_rank_seed"].rank(pct=True)).fillna(0) * 0.35
        has_seed = True
    if out["market_cap_rank_seed"].notna().any():
        out["pre_score"] += out["market_cap_rank_seed"].rank(pct=True).fillna(0) * 0.30
        has_seed = True
    if not has_seed:
        return universe.head(max_candidates)
    return out.sort_values("pre_score", ascending=False).head(max_candidates)


def apply_hard_filters(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    min_pe = safe_float(filters.get("min_pe"))
    if min_pe is not None and "pe" in out:
        out = out[(out["pe"].isna()) | (out["pe"] > min_pe)]

    min_amount = safe_float(filters.get("min_avg_amount_20"))
    if min_amount is not None and "avg_amount_20" in out:
        out = out[(out["avg_amount_20"].isna()) | (out["avg_amount_20"] >= min_amount)]

    min_cap = safe_float(filters.get("min_market_cap_yi"))
    if min_cap is not None and "market_cap_yi" in out:
        out = out[(out["market_cap_yi"].isna()) | (out["market_cap_yi"] >= min_cap)]

    max_cap = safe_float(filters.get("max_market_cap_yi"))
    if max_cap is not None and "market_cap_yi" in out:
        out = out[(out["market_cap_yi"].isna()) | (out["market_cap_yi"] <= max_cap)]

    min_listing_days = safe_float(filters.get("min_listing_days"))
    if min_listing_days is not None and "listing_age_days" in out:
        out = out[(out["listing_age_days"].isna()) | (out["listing_age_days"] >= min_listing_days)]

    if "paused" in out:
        out = out[~out["paused"].fillna(False)]

    for field_name in filters.get("require_fields", []):
        if field_name in out:
            out = out[out[field_name].notna()]
    return out


def apply_relaxed_filters(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    min_pe = safe_float(filters.get("min_pe"))
    if min_pe is not None and "pe" in out:
        out = out[(out["pe"].isna()) | (out["pe"] > min_pe)]
    if "paused" in out:
        out = out[~out["paused"].fillna(False)]
    fallback_fields = filters.get("fallback_require_fields") or ["pe", "pb", "momentum_20", "momentum_60"]
    for field_name in fallback_fields:
        if field_name in out:
            out = out[out[field_name].notna()]
    return out


def listing_age(listing_date: Any, as_of: str | None) -> int | None:
    if not listing_date:
        return None
    try:
        return (parse_date(as_of) - parse_date(str(listing_date))).days
    except Exception:  # noqa: BLE001
        return None


def score_candidates(df: pd.DataFrame, factors: dict[str, Any], factor_processing: dict[str, Any] | None = None) -> pd.DataFrame:
    """Legacy entry-point that delegates to the new pipeline.

    Kept for compatibility with prior callers and unit tests that scored
    candidates outside of `build_signals`.
    """

    scored, _ = process_factors(df, factors, factor_processing)
    return scored
