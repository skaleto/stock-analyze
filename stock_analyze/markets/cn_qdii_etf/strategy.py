"""Signal builder for domestic cross-border ETF universes."""

from __future__ import annotations

import logging
import math
from collections import Counter
from datetime import date
from typing import Any

import pandas as pd

from ...factor_pipeline import process_factors
from .data_provider import CNQDIETFProvider


logger = logging.getLogger(__name__)

ETF_FACTOR_DIRECTIONS: dict[str, str] = {
    "momentum_20": "high",
    "momentum_60": "high",
    "low_volatility_60": "low",
    "avg_amount_20": "high",
    "discount_premium": "low",
}

DEFAULT_MAX_ABS_PREMIUM = 0.08
DEFAULT_MIN_FUND_SIZE_YUAN = 100_000_000.0
DEFAULT_MAX_PEER_TRACKING_ERROR_60 = 0.20
DEFAULT_MAX_MANAGEMENT_FEE_PCT = 1.0
LEGACY_TUSHARE_AMOUNT_MULTIPLIER = 1_000.0


def resolve_min_amount_yuan(filters: dict[str, Any]) -> float:
    """Resolve the liquidity floor in RMB yuan.

    Existing overlays were authored while ``fund_daily.amount`` was still in
    Tushare's thousand-yuan unit. The explicit ``*_yuan`` key is the new
    contract; the old key remains compatible by multiplying by 1,000.
    """

    if filters.get("min_avg_amount_20_yuan") is not None:
        return float(filters["min_avg_amount_20_yuan"])
    return float(filters.get("min_avg_amount_20", 0.0)) * LEGACY_TUSHARE_AMOUNT_MULTIPLIER


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) and math.isfinite(number) else None


def _risk_rejection_reason(row: pd.Series, filters: dict[str, Any]) -> str | None:
    if bool(row.get("active_hard_event", False)):
        return "active_fund_event_block"
    if bool(row.get("paused", False)):
        return "paused_or_stale"
    amount = _number(row.get("avg_amount_20"))
    if amount is None or amount < resolve_min_amount_yuan(filters):
        return "liquidity_below_floor"
    listing_age = _number(row.get("listing_age_days"))
    if listing_age is not None and listing_age < int(filters.get("min_listing_days", 0)):
        return "listing_too_recent"
    premium = _number(row.get("discount_premium"))
    max_premium = float(filters.get("max_abs_premium", DEFAULT_MAX_ABS_PREMIUM))
    if premium is not None and abs(premium) > max_premium:
        return "abnormal_premium"
    fund_size = _number(row.get("fund_size_yuan"))
    min_fund_size = float(filters.get("min_fund_size_yuan", DEFAULT_MIN_FUND_SIZE_YUAN))
    if fund_size is not None and fund_size < min_fund_size:
        return "fund_size_below_floor"
    tracking_error = _number(row.get("peer_tracking_error_60"))
    max_tracking_error = float(
        filters.get("max_peer_tracking_error_60", DEFAULT_MAX_PEER_TRACKING_ERROR_60)
    )
    if tracking_error is not None and tracking_error > max_tracking_error:
        return "peer_tracking_error_high"
    management_fee = _number(row.get("management_fee"))
    max_fee = float(filters.get("max_management_fee_pct", DEFAULT_MAX_MANAGEMENT_FEE_PCT))
    if management_fee is not None and management_fee > max_fee:
        return "management_fee_high"
    return None


def _apply_risk_gates(
    frame: pd.DataFrame,
    filters: dict[str, Any],
) -> tuple[pd.DataFrame, Counter[str]]:
    rejected: Counter[str] = Counter()
    keep: list[Any] = []
    for index, row in frame.iterrows():
        reason = _risk_rejection_reason(row, filters)
        if reason is None:
            keep.append(index)
        else:
            rejected[reason] += 1
    return frame.loc[keep].copy(), rejected


def _record_funnel(
    provider: Any,
    scope: str,
    spot_df: pd.DataFrame,
    risk_ready: pd.DataFrame,
    capped: pd.DataFrame,
    factor_ready: pd.DataFrame,
    rejected: Counter[str],
) -> None:
    recorder = getattr(provider, "record_selection_funnel", None)
    if not callable(recorder):
        return
    universe_hash = next(
        (
            str(value)
            for value in spot_df.get("universe_hash", pd.Series(dtype=str)).tolist()
            if str(value) not in {"", "nan", "None"}
        ),
        None,
    )
    data_gaps = {
        key: int(spot_df[key].isna().sum()) if key in spot_df.columns else len(spot_df)
        for key in ("discount_premium", "fund_size_yuan")
    }
    if "peer_tracking_error_60" in spot_df.columns:
        applicable = spot_df.get(
            "peer_tracking_error_applicable",
            pd.Series(False, index=spot_df.index),
        ).fillna(False).astype(bool)
        data_gaps["peer_tracking_error_60"] = int(
            (applicable & spot_df["peer_tracking_error_60"].isna()).sum()
        )
    else:
        data_gaps["peer_tracking_error_60"] = 0
    data_gaps["history_incomplete"] = (
        int((~spot_df["history_complete"].fillna(False).astype(bool)).sum())
        if "history_complete" in spot_df.columns
        else len(spot_df)
    )
    recent_events: list[dict[str, Any]] = []
    for row in spot_df.to_dict(orient="records"):
        for event in row.get("recent_fund_events") or []:
            recent_events.append({"code": row.get("code"), "name": row.get("name"), **event})
    recent_events.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    recorder(
        scope,
        {
            "universe_hash": universe_hash,
            "stages": [
                {"key": "catalog", "label": "动态目录", "count": len(spot_df)},
                {"key": "risk_ready", "label": "风险闸门后", "count": len(risk_ready)},
                {"key": "candidate_cap", "label": "候选上限后", "count": len(capped)},
                {"key": "factor_ready", "label": "因子可评分", "count": len(factor_ready)},
            ],
            "rejections": [
                {"reason": reason, "count": count}
                for reason, count in sorted(rejected.items())
            ],
            "data_gaps": data_gaps,
            "recent_events": recent_events[:20],
            "active_hard_blocks": int(
                spot_df.get("active_hard_event", pd.Series(False, index=spot_df.index))
                .fillna(False)
                .astype(bool)
                .sum()
            ),
            "ranked": [
                {
                    key: row.get(key)
                    for key in (
                        "code",
                        "name",
                        "index_key",
                        "theme",
                        "score",
                        "avg_amount_20",
                        "fund_size_yuan",
                        "discount_premium",
                        "peer_tracking_error_60",
                        "history_start",
                        "history_end",
                        "history_complete",
                        "active_hard_event",
                        "latest_event_type",
                        "latest_event_published_at",
                    )
                    if key in row
                }
                for row in factor_ready.to_dict(orient="records")
            ],
        },
    )


def build_signals(
    config: dict[str, Any],
    provider: CNQDIETFProvider,
    *,
    as_of: date | None = None,
    repo_root: str | None = None,
) -> list[dict[str, Any]]:
    as_of = as_of or date.today()
    rows: list[dict[str, Any]] = []
    factors_spec = _factor_spec(config.get("factors", {}) or {})
    factor_processing = dict(config.get("factor_processing", {}) or {})
    factor_processing.setdefault("neutralize_industry", False)
    filters = dict(config.get("filters", {}) or {})

    for account in config.get("accounts", []) or []:
        scope = account["scope"]
        spot_df = provider.spot(scope)
        if spot_df.empty:
            logger.warning("cn_qdii_etf %s universe spot is empty", scope)
            continue
        eligible, rejected = _apply_risk_gates(spot_df.copy(), filters)
        risk_ready = eligible.copy()
        max_candidates = int(filters.get("max_fetch_candidates", len(eligible)) or 0)
        if max_candidates > 0 and len(eligible) > max_candidates:
            if "avg_amount_20" in eligible.columns:
                eligible = eligible.assign(
                    _liquidity=pd.to_numeric(eligible["avg_amount_20"], errors="coerce")
                ).nlargest(max_candidates, "_liquidity").drop(columns="_liquidity")
            else:
                eligible = eligible.head(max_candidates)
            rejected["candidate_cap"] += len(risk_ready) - len(eligible)
        capped = eligible.copy()
        active = [name for name in factors_spec if name in eligible.columns]
        if not active:
            logger.warning("cn_qdii_etf %s: none of overlay factors found", scope)
            _record_funnel(
                provider,
                scope,
                spot_df,
                risk_ready,
                capped,
                pd.DataFrame(),
                rejected,
            )
            continue
        frame_cols = ["code"] + active
        if "industry" in spot_df.columns:
            frame_cols.append("industry")
        scored, _factor_table = process_factors(
            eligible[frame_cols].copy(),
            factors=factors_spec,
            factor_processing=factor_processing,
        )
        if "insufficient_factor_coverage" in scored.columns:
            rejected["insufficient_factor_coverage"] += int(
                scored["insufficient_factor_coverage"].fillna(False).astype(bool).sum()
            )
            scored = scored.loc[~scored["insufficient_factor_coverage"]].copy()
        metadata_by_code = {
            str(row["code"]): row
            for row in eligible.to_dict(orient="records")
        }
        output_rows: list[dict[str, Any]] = []
        for _, r in scored.iterrows():
            code = str(r["code"])
            source = metadata_by_code.get(code, {})
            output = {
                "code": code,
                "account_id": account["id"],
                "score": float(r["score"]),
                "reason": _format_reason(r, active),
            }
            for key in (
                "name",
                "scope",
                "exposure_group",
                "country",
                "sector",
                "theme",
                "index_key",
                "benchmark",
                "avg_amount_20",
                "fund_size_yuan",
                "discount_premium",
                "peer_tracking_error_60",
                "management_fee",
                "universe_hash",
                "history_start",
                "history_end",
                "history_complete",
            ):
                if key in source:
                    output[key] = source.get(key)
            rows.append(output)
            output_rows.append(output)
        factor_ready = pd.DataFrame(output_rows)
        _record_funnel(
            provider,
            scope,
            spot_df,
            risk_ready,
            capped,
            factor_ready,
            rejected,
        )
    return rows


def _factor_spec(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if isinstance(value, dict):
            out[name] = value
        else:
            out[name] = {
                "weight": float(value),
                "direction": ETF_FACTOR_DIRECTIONS.get(name, "high"),
            }
    return out


def _format_reason(row: pd.Series, factor_cols: list[str]) -> str:
    parts: list[str] = []
    for col in factor_cols[:3]:
        if col in row and pd.notna(row[col]):
            parts.append(f"{col}={float(row[col]):+.4f}")
    return "; ".join(parts) if parts else "top_score"


__all__ = ["ETF_FACTOR_DIRECTIONS", "build_signals", "resolve_min_amount_yuan"]
