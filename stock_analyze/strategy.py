from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .data_provider import AkshareProvider
from .utils import now_iso, safe_float


@dataclass
class SignalResult:
    account_id: str
    pool: str
    generated_at: str
    candidates: pd.DataFrame
    selected: pd.DataFrame
    warnings: list[str]


def build_signals(config: dict[str, Any], account: dict[str, Any], provider: AkshareProvider, as_of: str | None = None) -> SignalResult:
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
        metrics = provider.financial_metrics(code)
        snapshot = provider.price_snapshot(code, as_of=as_of, spot_row=stock.to_dict())
        data_warnings = []
        if valuation.get("pe") is None and safe_float(stock.get("pe")) is None:
            data_warnings.append("pe_missing")
        if valuation.get("pb") is None and safe_float(stock.get("pb")) is None:
            data_warnings.append("pb_missing")
        if metrics.get("fetch_error"):
            data_warnings.append("financial_fetch_failed")
        if snapshot.warning:
            data_warnings.append(snapshot.warning)
        row = {
            "code": code,
            "name": name if name and name != "nan" else basic.get("name", ""),
            "latest_price": safe_float(stock.get("latest_price")) or snapshot.close,
            "pe": safe_float(stock.get("pe")) or valuation.get("pe"),
            "pb": safe_float(stock.get("pb")) or valuation.get("pb"),
            "market_cap_yi": safe_float(stock.get("market_cap_yi")) or safe_float(basic.get("market_cap_yi")),
            "roe": safe_float(metrics.get("roe")),
            "gross_margin": safe_float(metrics.get("gross_margin")),
            "debt_ratio": safe_float(metrics.get("debt_ratio")),
            "net_profit_growth": safe_float(metrics.get("net_profit_growth")),
            "momentum_20": snapshot.momentum_20,
            "momentum_60": snapshot.momentum_60,
            "avg_amount_20": snapshot.avg_amount_20,
            "paused": snapshot.paused,
            "data_warnings": ";".join(data_warnings),
        }
        rows.append(row)

    candidates = pd.DataFrame(rows)
    if candidates.empty:
        raise RuntimeError(f"No candidates left after basic filters for {scope}")

    candidates = apply_hard_filters(candidates, filters)
    if candidates.empty:
        raise RuntimeError(f"No candidates left after hard filters for {scope}")

    candidates = score_candidates(candidates, config.get("factors", {}))
    selected = candidates.sort_values("score", ascending=False).head(int(account.get("top_n", 10))).copy()
    selected["account_id"] = account["id"]
    selected["pool"] = scope

    return SignalResult(
        account_id=str(account["id"]),
        pool=scope,
        generated_at=now_iso(),
        candidates=candidates,
        selected=selected,
        warnings=warnings,
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
        return universe
    return out.sort_values("pre_score", ascending=False).head(max_candidates)


def apply_hard_filters(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    min_pe = safe_float(filters.get("min_pe"))
    if min_pe is not None and "pe" in out:
        out = out[(out["pe"].isna()) | (out["pe"] > min_pe)]

    min_amount = safe_float(filters.get("min_avg_amount_20"))
    if min_amount is not None and "avg_amount_20" in out:
        out = out[(out["avg_amount_20"].isna()) | (out["avg_amount_20"] >= min_amount)]

    if "paused" in out:
        out = out[~out["paused"].fillna(False)]

    for field in filters.get("require_fields", []):
        if field in out:
            out = out[out[field].notna()]
    return out


def score_candidates(df: pd.DataFrame, factors: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    out["score"] = 0.0
    factor_notes: list[list[str]] = [[] for _ in range(len(out))]

    for factor, spec in factors.items():
        if factor not in out:
            continue
        weight = float(spec.get("weight", 0))
        direction = spec.get("direction", "high")
        numeric = pd.to_numeric(out[factor], errors="coerce")
        valid = numeric.notna()
        if valid.sum() == 0:
            continue
        pct_rank = numeric.rank(pct=True)
        if direction == "low":
            pct_rank = 1 - pct_rank
        score_part = pct_rank.fillna(0) * weight * 100
        out["score"] += score_part
        for index, value in enumerate(score_part.tolist()):
            if value > 0:
                factor_notes[index].append(f"{factor}:{value:.1f}")

    out["score"] = out["score"].round(2)
    out["score_detail"] = ["; ".join(items) for items in factor_notes]
    return out
