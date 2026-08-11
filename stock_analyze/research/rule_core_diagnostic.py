"""Falsification-first replay for the two predeclared rule cores."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..factor_pipeline import process_factors
from ..utils import write_text_atomic
from . import portfolio_replay
from .storage import ResearchStore


DEVELOPMENT_FRACTION = 0.60
DATA_COVERAGE_FLOOR = 0.95
PRICE_COVERAGE_FLOOR = 0.98
A_SHARE_MIN_HISTORY_DAYS = int(365.25 * 8)
TRADING_DATE_DENSITY_FLOOR = 0.85


@dataclass(frozen=True)
class DataAudit:
    passes: bool
    checks: dict[str, Any]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuleCoreSpec:
    market: str
    horizon: int
    intended_overlay: str
    control_overlay: str
    baseline: str
    expected_account_sizes: tuple[tuple[str, int], ...] = ()


RULE_CORE_SPECS = (
    RuleCoreSpec(
        market="a_share",
        horizon=3,
        intended_overlay="configs/agents/claude_a_share.yaml",
        control_overlay="configs/agents/codex_a_share.yaml",
        baseline="configs/competition_a_share.yaml",
        expected_account_sizes=(("hs300", 300), ("zz500", 500)),
    ),
    RuleCoreSpec(
        market="cn_qdii_etf",
        horizon=10,
        intended_overlay="configs/agents/codex_cn_qdii_etf.yaml",
        control_overlay="configs/agents/claude_cn_qdii_etf.yaml",
        baseline="configs/competition_cn_qdii_etf.yaml",
    ),
)


FACTOR_ALIASES = {
    "pe": "pe_ttm",
    "net_profit_growth": "profit_growth",
}

A_SHARE_DAILY_BASIC_FACTORS = {"pe", "pb", "dividend_yield", "turnover_rate"}
A_SHARE_STRUCTURALLY_NULLABLE_FACTORS = {"pe", "dividend_yield"}
A_SHARE_ROLLING_FACTOR_WARMUPS = {
    "momentum_20": 20,
    "momentum_60": 60,
    "low_volatility_60": 60,
}
A_SHARE_FUNDAMENTAL_FACTORS = {
    "roe", "gross_margin", "debt_ratio", "net_profit_growth",
    "profit_growth", "revenue_growth", "cash_conversion",
}
A_SHARE_RESTATEMENT_POLICIES = {
    "latest_revision_visible_on_announcement_date",
}


def _date_text(values: pd.Series) -> pd.Series:
    normalized = (
        values.astype("string").str.strip().str.replace("-", "", regex=False)
    )
    normalized = normalized.where(normalized.str.fullmatch(r"\d{8}").fillna(False))
    parsed = pd.to_datetime(
        normalized,
        format="%Y%m%d",
        errors="coerce",
    )
    return parsed.dt.strftime("%Y%m%d").astype("string")


def _code_text(values: pd.Series) -> pd.Series:
    return values.astype("string").str.split(".").str[0].str.zfill(6)


def _boolean_flags(
    values: pd.Series | None,
    *,
    index: pd.Index,
    unknown: bool,
) -> pd.Series:
    """Normalize nullable storage dtypes before applying risk filters."""

    if values is None:
        return pd.Series(bool(unknown), index=index, dtype=bool)
    normalized = values.astype("string").str.strip().str.lower()
    result = pd.Series(bool(unknown), index=values.index, dtype=bool)
    result.loc[normalized.isin({"1", "1.0", "true", "t", "yes", "y"})] = True
    result.loc[normalized.isin({"0", "0.0", "false", "f", "no", "n"})] = False
    return result.reindex(index, fill_value=bool(unknown))


def select_development_dates(
    frame: pd.DataFrame,
    *,
    fraction: float = DEVELOPMENT_FRACTION,
) -> tuple[str, ...]:
    if "trade_date" not in frame.columns:
        raise ValueError("rule_core_trade_date_missing")
    dates = sorted(_date_text(frame["trade_date"]).dropna().unique())
    if not dates:
        raise ValueError("rule_core_trade_dates_empty")
    count = max(1, int(len(dates) * float(fraction)))
    return tuple(str(value) for value in dates[:count])


def _factor_column(name: str) -> str:
    return FACTOR_ALIASES.get(name, name)


def _with_rule_factor_aliases(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for factor, source in FACTOR_ALIASES.items():
        if factor not in result.columns and source in result.columns:
            result[factor] = pd.to_numeric(result[source], errors="coerce")
    if "low_volatility_60" not in result.columns and "return_1" in result.columns:
        result = result.sort_values(["code", "trade_date"], kind="stable")
        result["low_volatility_60"] = result.groupby("code", sort=False)["return_1"].transform(
            lambda values: pd.to_numeric(values, errors="coerce")
            .rolling(60, min_periods=40)
            .std()
            * np.sqrt(252.0)
        )
    if "dividend_yield" not in result.columns and "dv_ttm" in result.columns:
        result["dividend_yield"] = pd.to_numeric(result["dv_ttm"], errors="coerce")
    return result


def _normalized_join_frames(
    features: pd.DataFrame,
    labels: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    left = features.copy()
    right = labels.copy()
    for frame in (left, right):
        frame["trade_date"] = _date_text(frame["trade_date"])
        frame["code"] = _code_text(frame["code"])
        frame["account_id"] = frame["account_id"].astype("string")
    return left, right


def attach_entry_execution_constraints(
    features: pd.DataFrame,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    """Attach next-open tradability fields without changing label returns."""

    result = labels.copy()
    required = {"code", "trade_date", "entry_date"}
    if required.difference(result.columns) or {"code", "trade_date"}.difference(features.columns):
        return result
    feature_frame = features.copy()
    feature_frame["code"] = _code_text(feature_frame["code"])
    feature_frame["trade_date"] = _date_text(feature_frame["trade_date"])
    result["code"] = _code_text(result["code"])
    result["trade_date"] = _date_text(result["trade_date"])
    result["entry_date"] = _date_text(result["entry_date"])
    price_columns = [
        column for column in ("open", "high", "low", "close", "volume")
        if column in feature_frame.columns
    ]
    daily = feature_frame[["code", "trade_date", *price_columns]].drop_duplicates(
        ["code", "trade_date"], keep="last"
    )
    signal_close = daily[["code", "trade_date", "close"]].rename(
        columns={"close": "_signal_close"}
    ) if "close" in daily.columns else pd.DataFrame(columns=["code", "trade_date", "_signal_close"])
    entry = daily.rename(columns={
        "trade_date": "entry_date",
        "open": "entry_price_from_feature",
        "high": "entry_high",
        "low": "entry_low",
        "close": "entry_close",
        "volume": "entry_volume",
    })
    replace = {
        column for column in (
            "entry_high", "entry_low", "entry_close", "entry_volume",
            "entry_return_from_prev_close", "entry_one_price_limit_up",
            "entry_one_price_limit_down", "entry_buy_allowed", "entry_sell_allowed",
        ) if column in result.columns
    }
    result = result.drop(columns=sorted(replace), errors="ignore")
    result = result.merge(entry, on=["code", "entry_date"], how="left")
    result = result.merge(signal_close, on=["code", "trade_date"], how="left")
    def numeric(column: str, default: float = np.nan) -> pd.Series:
        source = result[column] if column in result.columns else pd.Series(default, index=result.index)
        return pd.to_numeric(source, errors="coerce")

    entry_price = numeric("entry_price")
    if entry_price.isna().all():
        entry_price = numeric("entry_price_from_feature")
    entry_high = numeric("entry_high")
    entry_low = numeric("entry_low")
    entry_close = numeric("entry_close")
    entry_volume = numeric("entry_volume")
    signal_close_values = numeric("_signal_close")
    result["entry_return_from_prev_close"] = entry_price / signal_close_values - 1.0
    one_price = entry_high.notna() & entry_low.notna() & entry_high.eq(entry_low) & entry_high.eq(entry_price)
    result["entry_one_price_limit_up"] = one_price & result["entry_return_from_prev_close"].ge(0.095)
    result["entry_one_price_limit_down"] = one_price & result["entry_return_from_prev_close"].le(-0.095)
    evidence_complete = (
        entry_price.gt(0.0)
        & entry_high.gt(0.0)
        & entry_low.gt(0.0)
        & entry_close.gt(0.0)
        & entry_volume.notna()
        & signal_close_values.gt(0.0)
    )
    tradable = evidence_complete & entry_volume.gt(0.0)
    buy_allowed = pd.Series(pd.NA, index=result.index, dtype="boolean")
    sell_allowed = pd.Series(pd.NA, index=result.index, dtype="boolean")
    buy_allowed.loc[evidence_complete] = (
        tradable & ~result["entry_one_price_limit_up"]
    ).loc[evidence_complete]
    sell_allowed.loc[evidence_complete] = (
        tradable & ~result["entry_one_price_limit_down"]
    ).loc[evidence_complete]
    result["entry_buy_allowed"] = buy_allowed
    result["entry_sell_allowed"] = sell_allowed
    return result.drop(columns=["entry_price_from_feature", "_signal_close"], errors="ignore")


def audit_rule_core_data(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    market: str,
    overlay: Mapping[str, Any],
    development_dates: tuple[str, ...],
    names_by_code: Mapping[str, str],
    expected_account_sizes: Mapping[str, int] | None = None,
) -> DataAudit:
    prepared = _with_rule_factor_aliases(features)
    prepared, label_frame = _normalized_join_frames(prepared, labels)
    full_feature_dates = pd.to_datetime(
        prepared["trade_date"], format="%Y%m%d", errors="coerce"
    ).dropna()
    prepared = prepared.loc[prepared["trade_date"].isin(development_dates)].copy()
    label_frame = label_frame.loc[label_frame["trade_date"].isin(development_dates)].copy()
    label_frame = attach_entry_execution_constraints(prepared, label_frame)
    reasons: list[str] = []
    checks: dict[str, Any] = {
        "development_start": min(development_dates),
        "development_end": max(development_dates),
        "development_dates": len(development_dates),
        "feature_rows": int(len(prepared)),
        "label_rows": int(len(label_frame)),
    }

    parsed_dates = pd.to_datetime(pd.Series(development_dates), format="%Y%m%d", errors="coerce").dropna()
    development_history_days = int((parsed_dates.max() - parsed_dates.min()).days) if len(parsed_dates) > 1 else 0
    full_history_days = int((full_feature_dates.max() - full_feature_dates.min()).days) if len(full_feature_dates) > 1 else 0
    checks["development_history_calendar_days"] = development_history_days
    checks["full_history_calendar_days"] = full_history_days
    unique_full_dates = pd.DatetimeIndex(full_feature_dates.unique()).sort_values()
    if len(unique_full_dates) > 1:
        business_days = pd.bdate_range(unique_full_dates.min(), unique_full_dates.max())
        trading_date_density = min(
            float(len(unique_full_dates) / max(len(business_days), 1)),
            1.0,
        )
        yearly_density: dict[str, float] = {}
        for year in range(unique_full_dates.min().year, unique_full_dates.max().year + 1):
            interval_start = max(unique_full_dates.min(), pd.Timestamp(year=year, month=1, day=1))
            interval_end = min(unique_full_dates.max(), pd.Timestamp(year=year, month=12, day=31))
            expected = pd.bdate_range(interval_start, interval_end)
            observed = unique_full_dates[unique_full_dates.year == year]
            yearly_density[str(year)] = min(
                float(len(observed) / max(len(expected), 1)),
                1.0,
            )
    else:
        trading_date_density = 0.0
        yearly_density = {}
    checks["trading_calendar_source"] = "business_day_density_proxy"
    checks["trading_date_density"] = trading_date_density
    checks["trading_date_density_by_year"] = yearly_density
    if trading_date_density < TRADING_DATE_DENSITY_FLOOR or any(
        value < TRADING_DATE_DENSITY_FLOOR for value in yearly_density.values()
    ):
        reasons.append("trading_date_density_below_floor")
    if market == "a_share" and full_history_days < A_SHARE_MIN_HISTORY_DAYS:
        reasons.append("a_share_history_shorter_than_eight_years")

    universe_flags = _boolean_flags(
        prepared.get("unbiased_universe"),
        index=prepared.index,
        unknown=False,
    )
    quality_flags = prepared.get(
        "universe_quality",
        pd.Series("unavailable", index=prepared.index),
    ).astype(str).eq("available")
    contract_coverage = float((universe_flags & quality_flags).mean()) if len(prepared) else 0.0
    checks["point_in_time_universe_coverage"] = contract_coverage
    if contract_coverage < DATA_COVERAGE_FLOOR:
        reasons.append("membership_contract_coverage_below_floor")
    prepared["_year"] = prepared["trade_date"].astype(str).str[:4]
    yearly_membership = {
        str(year): float((universe_flags.loc[group.index] & quality_flags.loc[group.index]).mean())
        for year, group in prepared.groupby("_year", sort=True)
    }
    checks["membership_coverage_by_year"] = yearly_membership

    expected_sizes = dict(expected_account_sizes or {})
    if expected_sizes and not prepared.empty:
        size_ratios: dict[str, float] = {}
        grouped = prepared.groupby(["account_id", "trade_date"], sort=True).size()
        for account_id, expected in expected_sizes.items():
            observed = grouped.loc[account_id] if account_id in grouped.index.get_level_values(0) else pd.Series(dtype=float)
            ratio = float((observed / max(int(expected), 1)).clip(upper=1.0).median()) if len(observed) else 0.0
            size_ratios[str(account_id)] = ratio
        membership_coverage = min(size_ratios.values()) if size_ratios else 0.0
        checks["membership_size_coverage"] = membership_coverage
        checks["membership_size_coverage_by_account"] = size_ratios
        if membership_coverage < DATA_COVERAGE_FLOOR:
            reasons.append("membership_size_coverage_below_floor")

    join_keys = ["code", "trade_date", "account_id"]
    price_columns = join_keys + [
        "entry_date", "entry_price", "benchmark_entry_price",
        "entry_buy_allowed", "entry_sell_allowed",
    ]
    available_price_columns = [column for column in price_columns if column in label_frame.columns]
    joined = prepared[join_keys].drop_duplicates().merge(
        label_frame[available_price_columns].drop_duplicates(join_keys),
        on=join_keys,
        how="left",
    )
    for column in ("entry_price", "benchmark_entry_price"):
        coverage = (
            float(pd.to_numeric(joined.get(column), errors="coerce").gt(0).mean())
            if column in joined.columns and len(joined)
            else 0.0
        )
        checks[f"{column}_coverage"] = coverage
        if coverage < PRICE_COVERAGE_FLOOR:
            reasons.append(f"{column}_coverage_below_floor")
    execution_constraint_coverage = (
        float(joined[["entry_buy_allowed", "entry_sell_allowed"]].notna().all(axis=1).mean())
        if {"entry_buy_allowed", "entry_sell_allowed"}.issubset(joined.columns) and len(joined)
        else 0.0
    )
    checks["entry_execution_constraint_coverage"] = execution_constraint_coverage
    if execution_constraint_coverage < PRICE_COVERAGE_FLOOR:
        reasons.append("entry_execution_constraint_coverage_below_floor")
    if "entry_date" in joined.columns and len(joined):
        common_entry = joined.groupby(["account_id", "trade_date"])["entry_date"].transform(
            lambda values: values.mode().sort_values().iloc[0] if not values.mode().empty else pd.NA
        )
        entry_session_alignment_coverage = float(
            joined["entry_date"].astype("string").eq(common_entry.astype("string")).mean()
        )
        checks["entry_session_alignment_coverage"] = entry_session_alignment_coverage
        if entry_session_alignment_coverage < PRICE_COVERAGE_FLOOR:
            reasons.append("entry_session_alignment_coverage_below_floor")

    normalized_names = {
        str(code).split(".")[0].zfill(6): str(name).strip()
        for code, name in names_by_code.items()
        if str(name).strip() and str(name).strip().lower() != "nan"
    }
    unique_codes = prepared["code"].dropna().astype(str).unique()
    name_coverage = (
        float(sum(code in normalized_names for code in unique_codes) / len(unique_codes))
        if len(unique_codes)
        else 0.0
    )
    checks["name_coverage"] = name_coverage
    if name_coverage < 1.0:
        reasons.append("name_coverage_below_floor")

    factors = dict(overlay.get("factors") or {})
    configured_weight = sum(max(float(spec.get("weight") or 0.0), 0.0) for spec in factors.values())
    factor_observation_coverage = {
        name: (
            float(pd.to_numeric(prepared.get(name), errors="coerce").notna().mean())
            if name in prepared.columns and len(prepared) else 0.0
        )
        for name in factors
    }
    available_weight = sum(
        max(float(spec.get("weight") or 0.0), 0.0)
        for name, spec in factors.items()
        if factor_observation_coverage.get(name, 0.0) > 0.0
    )
    definition_coverage = (
        available_weight / configured_weight if configured_weight > 0 else 0.0
    )
    weighted_observation_coverage = (
        sum(
            max(float(spec.get("weight") or 0.0), 0.0)
            * factor_observation_coverage.get(name, 0.0)
            for name, spec in factors.items()
        ) / configured_weight
        if configured_weight > 0 else 0.0
    )
    checks["configured_factor_weight_coverage"] = definition_coverage
    checks["configured_factor_observation_coverage"] = weighted_observation_coverage
    checks["configured_factor_observation_coverage_by_factor"] = factor_observation_coverage
    if definition_coverage < DATA_COVERAGE_FLOOR:
        reasons.append("configured_factor_weight_coverage_below_floor")
    minimum_factor_coverage = float(
        (overlay.get("factor_processing") or {}).get("min_factor_coverage") or 0.0
    )
    if weighted_observation_coverage < minimum_factor_coverage:
        reasons.append("configured_factor_observation_coverage_below_floor")

    if market == "a_share":
        required = list((overlay.get("filters") or {}).get("require_fields") or [])
        factor_coverage: dict[str, float] = {}
        factor_data_coverage: dict[str, float] = {}
        ordered = prepared.sort_values(["code", "trade_date"], kind="stable")
        history_observations = (
            ordered.groupby("code", sort=False).cumcount() + 1
        ).reindex(prepared.index)
        daily_basic_dates = _date_text(
            prepared.get(
                "daily_basic_trade_date", pd.Series(pd.NA, index=prepared.index)
            )
        )
        daily_basic_evidence = (
            daily_basic_dates.notna()
            & daily_basic_dates.eq(_date_text(prepared["trade_date"]))
        )
        for factor in required:
            column = _factor_column(str(factor))
            if str(factor) in prepared.columns:
                column = str(factor)
            observed = (
                pd.to_numeric(prepared.get(column), errors="coerce").notna()
                if column in prepared.columns
                else pd.Series(False, index=prepared.index)
            )
            coverage = float(observed.mean()) if len(prepared) else 0.0
            factor_name = str(factor)
            data_coverage = coverage
            if factor_name in A_SHARE_STRUCTURALLY_NULLABLE_FACTORS:
                data_coverage = (
                    float(daily_basic_evidence.mean()) if len(prepared) else 0.0
                )
            elif factor_name in A_SHARE_ROLLING_FACTOR_WARMUPS:
                warmup = A_SHARE_ROLLING_FACTOR_WARMUPS[factor_name]
                ready = observed | history_observations.ge(warmup)
                data_coverage = (
                    float(observed.loc[ready].mean()) if ready.any() else 0.0
                )
            factor_coverage[factor_name] = coverage
            factor_data_coverage[factor_name] = data_coverage
            if data_coverage < DATA_COVERAGE_FLOOR:
                reasons.append(f"factor_point_in_time_coverage_below_floor:{factor}")
        checks["required_factor_point_in_time_coverage"] = factor_coverage
        checks["required_factor_data_coverage"] = factor_data_coverage

        daily_basic_required = any(str(factor) in A_SHARE_DAILY_BASIC_FACTORS for factor in required)
        if daily_basic_required:
            daily_basic_coverage = (
                float(daily_basic_evidence.mean()) if len(prepared) else 0.0
            )
            checks["daily_basic_point_in_time_coverage"] = daily_basic_coverage
            if daily_basic_coverage < DATA_COVERAGE_FLOOR:
                reasons.append("daily_basic_point_in_time_coverage_below_floor")

        fundamental_required = any(str(factor) in A_SHARE_FUNDAMENTAL_FACTORS for factor in required)
        if fundamental_required:
            available_dates = _date_text(
                prepared.get("fundamental_available_date", pd.Series(pd.NA, index=prepared.index))
            )
            publication_coverage = float(
                (available_dates.notna() & available_dates.le(prepared["trade_date"])).mean()
            ) if len(prepared) else 0.0
            checks["financial_publication_date_coverage"] = publication_coverage
            if publication_coverage < DATA_COVERAGE_FLOOR:
                reasons.append("financial_publication_date_coverage_below_floor")
            restatement = prepared.get(
                "fundamental_restatement_policy", pd.Series(pd.NA, index=prepared.index)
            ).astype("string")
            restatement_coverage = (
                float(restatement.isin(A_SHARE_RESTATEMENT_POLICIES).mean())
                if len(prepared) else 0.0
            )
            checks["financial_restatement_policy_coverage"] = restatement_coverage
            if restatement_coverage < DATA_COVERAGE_FLOOR:
                reasons.append("financial_restatement_policy_coverage_below_floor")

        for column, check_name in (
            ("security_status", "security_status_coverage"),
            ("is_st", "st_flag_coverage"),
            ("is_suspended", "suspension_flag_coverage"),
        ):
            coverage = float(prepared.get(column, pd.Series(pd.NA, index=prepared.index)).notna().mean()) if len(prepared) else 0.0
            checks[check_name] = coverage
            if coverage < DATA_COVERAGE_FLOOR:
                reasons.append(f"{check_name}_below_floor")

    if market == "cn_qdii_etf":
        membership_source = prepared.get(
            "membership_source", pd.Series("", index=prepared.index)
        ).astype(str)
        interval_coverage = float(
            membership_source.eq("tushare_fund_basic_listing_interval").mean()
        ) if len(prepared) else 0.0
        checks["qdii_listing_interval_membership_coverage"] = interval_coverage
        checks["qdii_membership_coverage_by_year"] = yearly_membership
        if interval_coverage < DATA_COVERAGE_FLOOR or any(
            coverage < DATA_COVERAGE_FLOOR for coverage in yearly_membership.values()
        ):
            reasons.append("qdii_listed_delisted_membership_coverage_below_floor")
        ready_mask = (
            pd.to_numeric(prepared["momentum_60"], errors="coerce").notna()
            if "momentum_60" in prepared.columns
            else pd.Series(True, index=prepared.index)
        )
        strategy_ready = prepared.loc[ready_mask].copy()
        checks["qdii_strategy_ready_rows"] = int(len(strategy_ready))
        qdii_checks = {
            "qdii_underlying_exposure_coverage": ("index_key", "theme"),
            "qdii_nav_coverage": ("unit_nav",),
            "qdii_premium_coverage": ("discount_premium",),
            "qdii_liquidity_coverage": ("avg_amount_20",),
            "qdii_tracking_coverage": ("tracking_error_20", "tracking_difference_20"),
        }
        for check_name, columns in qdii_checks.items():
            coverage_frame = (
                prepared
                if check_name == "qdii_underlying_exposure_coverage"
                else strategy_ready
            )
            present = [column for column in columns if column in coverage_frame.columns]
            if not present:
                coverage = 0.0
            elif check_name == "qdii_underlying_exposure_coverage":
                coverage = float(coverage_frame[present].notna().all(axis=1).mean())
            else:
                coverage = float(coverage_frame[present].notna().any(axis=1).mean()) if len(coverage_frame) else 0.0
            checks[check_name] = coverage
            if coverage < DATA_COVERAGE_FLOOR:
                reasons.append(f"{check_name}_below_floor")

    prepared.drop(columns="_year", inplace=True, errors="ignore")

    return DataAudit(
        passes=not reasons,
        checks=checks,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def development_hypothesis_status(
    metrics: Mapping[str, Any],
    audit: DataAudit,
) -> str:
    if not audit.passes:
        return "data_blocked"
    if int(metrics.get("trade_count") or 0) == 0:
        return "negative_hypothesis"
    if float(metrics.get("net_return") or 0.0) <= 0.0:
        return "negative_hypothesis"
    if float(metrics.get("annualized_excess_wealth") or 0.0) <= 0.0:
        return "negative_hypothesis"
    gross_profit = metrics.get("gross_profit_amount")
    if gross_profit is not None and float(metrics.get("total_execution_cost") or 0.0) >= float(gross_profit):
        return "negative_hypothesis"
    return "proceed"


def stage1_aggregate_status(decision: Mapping[str, str]) -> str:
    statuses = tuple(str(value) for value in decision.values())
    if any(value == "data_blocked" for value in statuses):
        return "data_repair_required"
    proceed_count = sum(value == "proceed" for value in statuses)
    if statuses and proceed_count == len(statuses):
        return "ready_for_stage2"
    if proceed_count:
        return "partial_proceed"
    return "stopped"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_names(
    repo_root: Path,
    market: str,
    features: pd.DataFrame,
    *,
    development_end: str,
) -> dict[str, str]:
    if "name" in features.columns:
        eligible = features.loc[_date_text(features["trade_date"]).le(development_end)].copy()
        names = eligible[["code", "name"]].dropna().drop_duplicates("code", keep="last")
        return dict(zip(_code_text(names["code"]), names["name"].astype(str)))
    if market == "cn_qdii_etf":
        cache = repo_root / "data" / market / "shared" / "cache"
        for filename in ("fund_basic_E_v2.csv", "fund_basic_E.csv"):
            path = cache / filename
            if not path.exists():
                continue
            frame = pd.read_csv(
                path,
                dtype={"ts_code": str, "list_date": str, "delist_date": str},
            )
            if {"ts_code", "name"}.issubset(frame.columns):
                if "list_date" in frame.columns:
                    frame = frame.loc[_date_text(frame["list_date"]).le(development_end)]
                return dict(zip(_code_text(frame["ts_code"]), frame["name"].astype(str)))
    if market == "a_share":
        cache = repo_root / "data" / "shared" / "cache"
        candidates = sorted(cache.glob("stock_basic_*.csv")) if cache.exists() else []
        eligible_paths = [
            path for path in candidates
            if path.stem.rsplit("_", 1)[-1].replace("-", "") <= development_end
        ]
        if eligible_paths:
            frame = pd.read_csv(
                eligible_paths[-1],
                dtype={"ts_code": str, "code": str, "list_date": str},
            )
            code_column = "ts_code" if "ts_code" in frame.columns else "code"
            if {code_column, "name"}.issubset(frame.columns):
                return dict(zip(_code_text(frame[code_column]), frame["name"].astype(str)))
    return {}


def _apply_filters(
    frame: pd.DataFrame,
    *,
    market: str,
    overlay: Mapping[str, Any],
) -> pd.DataFrame:
    result = frame.copy()
    filters = dict(overlay.get("filters") or {})
    if filters.get("exclude_st"):
        st_flag = _boolean_flags(
            result.get("is_st"),
            index=result.index,
            unknown=True,
        )
        result = result.loc[~st_flag]
    if filters.get("min_listing_days") is not None and "list_date" in result.columns:
        trade_dates = pd.to_datetime(_date_text(result["trade_date"]), format="%Y%m%d", errors="coerce")
        list_dates = pd.to_datetime(_date_text(result["list_date"]), format="%Y%m%d", errors="coerce")
        listing_days = (trade_dates - list_dates).dt.days
        result = result.loc[listing_days.ge(int(filters["min_listing_days"]))]
    if "avg_amount_20" in result.columns and filters.get("min_avg_amount_20") is not None:
        floor = float(filters["min_avg_amount_20"])
        if market == "cn_qdii_etf" and filters.get("min_avg_amount_20_yuan") is None:
            floor *= 1_000.0
        result = result.loc[pd.to_numeric(result["avg_amount_20"], errors="coerce") >= floor]
    if market == "a_share":
        if filters.get("min_pe") is not None and "pe" in result.columns:
            result = result.loc[pd.to_numeric(result["pe"], errors="coerce") > float(filters["min_pe"])]
        if "total_mv" in result.columns:
            result["market_cap_yi"] = pd.to_numeric(result["total_mv"], errors="coerce") / 10_000.0
            if filters.get("min_market_cap_yi") is not None:
                result = result.loc[result["market_cap_yi"] >= float(filters["min_market_cap_yi"])]
            if filters.get("max_market_cap_yi") is not None:
                result = result.loc[result["market_cap_yi"] <= float(filters["max_market_cap_yi"])]
    required = [str(value) for value in filters.get("require_fields") or []]
    fallback = [str(value) for value in filters.get("fallback_require_fields") or []]
    required_columns = [_factor_column(value) if value not in result.columns else value for value in required]
    if required_columns and all(column in result.columns for column in required_columns):
        result = result.dropna(subset=required_columns)
    elif fallback:
        fallback_columns = [_factor_column(value) if value not in result.columns else value for value in fallback]
        if all(column in result.columns for column in fallback_columns):
            result = result.dropna(subset=fallback_columns)
        else:
            return result.iloc[0:0].copy()
    return result


def _score_rule_frame(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    market: str,
    overlay: Mapping[str, Any],
    development_dates: tuple[str, ...],
    names_by_code: Mapping[str, str],
) -> pd.DataFrame:
    prepared = _with_rule_factor_aliases(features)
    prepared, label_frame = _normalized_join_frames(prepared, labels)
    prepared = prepared.loc[prepared["trade_date"].isin(development_dates)].copy()
    label_frame = label_frame.loc[label_frame["trade_date"].isin(development_dates)].copy()
    calendar = (
        label_frame.groupby(["account_id", "trade_date"], as_index=False, sort=True)
        .agg({
            "entry_date": lambda values: (
                values.dropna().astype(str).mode().sort_values().iloc[0]
                if not values.dropna().empty else pd.NA
            ),
            "benchmark_entry_price": "median",
        })
    )
    calendar["code"] = "CASH::" + calendar["account_id"].astype(str)
    calendar["name"] = "现金占位"
    calendar["entry_price"] = np.nan
    calendar["score"] = -np.inf
    calendar["industry"] = "cash"
    calendar["_is_cash_placeholder"] = True
    calendar["_eligible_for_selection"] = False
    prepared["name"] = prepared["code"].map(
        {str(code).split(".")[0].zfill(6): str(name) for code, name in names_by_code.items()}
    )
    all_market_rows = prepared.copy()
    prepared = _apply_filters(all_market_rows, market=market, overlay=overlay)
    max_candidates = int((overlay.get("filters") or {}).get("max_fetch_candidates") or 0)
    if max_candidates > 0 and len(prepared):
        liquidity = pd.to_numeric(
            prepared.get("avg_amount_20", pd.Series(0.0, index=prepared.index)), errors="coerce"
        ).fillna(0.0)
        prepared = (
            prepared.assign(_liquidity=liquidity)
            .sort_values(
                ["account_id", "trade_date", "_liquidity", "code"],
                ascending=[True, True, False, True],
                kind="stable",
            )
            .groupby(["account_id", "trade_date"], sort=False)
            .head(max_candidates)
            .drop(columns="_liquidity")
        )
    join_keys = ["code", "trade_date", "account_id"]
    label_columns = join_keys + [
        column for column in (
            "entry_date", "entry_price", "benchmark_entry_price",
            "entry_high", "entry_low", "entry_close", "entry_volume",
            "entry_buy_allowed", "entry_sell_allowed",
            "entry_one_price_limit_up", "entry_one_price_limit_down",
        ) if column in label_frame.columns
    ]
    evaluation = prepared.merge(
        label_frame[label_columns].drop_duplicates(join_keys),
        on=join_keys,
        how="inner",
    )
    market_evaluation = all_market_rows.merge(
        label_frame[label_columns].drop_duplicates(join_keys),
        on=join_keys,
        how="inner",
    ).drop_duplicates(join_keys, keep="last")
    factors = dict(overlay.get("factors") or {})
    factor_processing = dict(overlay.get("factor_processing") or {})
    scored_parts: list[pd.DataFrame] = []
    for _, group in evaluation.groupby(["account_id", "trade_date"], sort=True):
        active_factors = {name: spec for name, spec in factors.items() if name in group.columns}
        if not active_factors:
            continue
        columns = ["code", *active_factors]
        if "industry" in group.columns:
            columns.append("industry")
        scored, _ = process_factors(
            group[columns].copy(),
            active_factors,
            factor_processing,
        )
        scored = scored.loc[~scored["insufficient_factor_coverage"].fillna(False)].copy()
        metadata = group.drop(columns=["score"], errors="ignore")
        scored_parts.append(
            metadata.merge(
                scored[["code", "score", "factor_coverage_ratio"]],
                on="code",
                how="inner",
            )
        )
    scored = pd.concat(scored_parts, ignore_index=True) if scored_parts else pd.DataFrame()
    if not scored.empty:
        scored["_is_cash_placeholder"] = False
        scored["_eligible_for_selection"] = True
        scored_keys = scored[join_keys].drop_duplicates().assign(_scored=True)
        quote_rows = market_evaluation.merge(scored_keys, on=join_keys, how="left")
        quote_rows = quote_rows.loc[quote_rows["_scored"].isna()].drop(columns="_scored")
    else:
        quote_rows = market_evaluation.copy()
    if not quote_rows.empty:
        quote_rows["score"] = -np.inf
        quote_rows["factor_coverage_ratio"] = 0.0
        quote_rows["_is_cash_placeholder"] = False
        quote_rows["_eligible_for_selection"] = False
    columns = list(dict.fromkeys([
        *scored.columns,
        *quote_rows.columns,
        *calendar.columns,
    ]))
    # pandas historically ignored all-NA columns when resolving concat dtypes.
    # Remove them per block and restore the union afterwards so the behavior is
    # explicit and stable across the pandas 2.x -> 3.x transition.
    blocks = [
        block.dropna(axis=1, how="all")
        for block in (scored, quote_rows, calendar)
        if not block.empty
    ]
    return pd.concat(blocks, ignore_index=True, sort=False).reindex(columns=columns)


def _portfolio_contract(
    baseline: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    controls = dict(overlay.get("portfolio_controls") or {})
    hold_buffer = float(controls.get("hold_buffer_pct") or 0.0)
    accounts = [
        {**dict(account), "hold_buffer_pct": hold_buffer}
        for account in baseline.get("accounts") or []
    ]
    return {
        "accounts": accounts,
        "trading": dict(baseline.get("trading") or {}),
        "performance": dict(baseline.get("performance") or {}),
        "rule_execution_policy": {
            "version": "mechanical-rule-v1",
            "rank_buffer_pct": hold_buffer,
            "minimum_target_change": 0.01,
            "partial_adjustment_rate": 1.0,
            "max_daily_turnover": 0.08,
            "max_industry_weight": float(controls.get("max_industry_weight") or 1.0),
            "max_holding_days": int(controls.get("max_holding_days") or 0),
            "industry_column": "industry",
            "industry_unclassified_label": str(
                controls.get("industry_unclassified_label") or "unclassified"
            ),
        },
    }


def _run_overlay(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    market: str,
    overlay: Mapping[str, Any],
    baseline: Mapping[str, Any],
    development_dates: tuple[str, ...],
    names_by_code: Mapping[str, str],
    equal_weight_control: bool = False,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    evaluation = _score_rule_frame(
        features,
        labels,
        market=market,
        overlay=overlay,
        development_dates=development_dates,
        names_by_code=names_by_code,
    )
    if evaluation.empty:
        return {"trade_count": 0, "net_return": 0.0, "annualized_excess_wealth": 0.0}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    if equal_weight_control:
        evaluation["score"] = 0.0
        contract = _portfolio_contract(baseline, overlay)
    else:
        contract = _portfolio_contract(baseline, overlay)
    replay = portfolio_replay.replay_rule_portfolio(
        evaluation,
        contract=contract,
    )
    metrics = dict(replay.metrics)
    initial_cash = {
        str(account.get("id")): float(account.get("cash") or 0.0)
        for account in baseline.get("accounts") or []
    }
    if replay.nav.empty:
        final_nav = 0.0
    else:
        latest = replay.nav.sort_values("date").groupby("account_id", as_index=False).tail(1)
        final_nav = float(pd.to_numeric(latest["nav"], errors="coerce").fillna(0.0).sum())
    net_profit = final_nav - sum(initial_cash.values())
    metrics["net_profit_amount"] = net_profit
    metrics["gross_profit_amount"] = net_profit + float(metrics.get("total_execution_cost") or 0.0)
    if equal_weight_control:
        metrics["control_contract"] = "investable_equal_weight_top_n_v1"
    return _compact_metrics(metrics), replay.nav, replay.trades, replay.periods


def _compact_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    series_fields = {
        "portfolio_nav", "benchmark_nav", "portfolio_period_returns",
        "portfolio_daily_returns", "benchmark_period_returns",
        "portfolio_period_return_dates",
    }
    compact = {
        key: value for key, value in metrics.items()
        if key not in series_fields
    }
    account_metrics = compact.get("account_metrics")
    if isinstance(account_metrics, Mapping):
        compact["account_metrics"] = {
            str(account): {
                key: value for key, value in dict(values).items()
                if key not in {"portfolio_nav", "benchmark_nav"}
            }
            for account, values in account_metrics.items()
            if isinstance(values, Mapping)
        }
    return compact


def _benchmark_control(metrics: Mapping[str, Any]) -> dict[str, Any]:
    benchmark_cagr = float(metrics.get("benchmark_cagr", metrics.get("benchmark_return", 0.0)) or 0.0)
    return {
        "portfolio_cagr": benchmark_cagr,
        "benchmark_cagr": benchmark_cagr,
        "annualized_excess_wealth": 0.0,
        "cumulative_relative_wealth": 0.0,
        "trade_count": 0,
        "total_execution_cost": 0.0,
        "reference_only": True,
    }


def _return_attribution(periods: pd.DataFrame) -> pd.DataFrame:
    if periods.empty:
        return pd.DataFrame(columns=[
            "market", "role", "account_id", "signal_date", "gross_active_return",
            "commission_return", "stamp_tax_return", "slippage_return",
            "net_active_return", "reconciliation_error",
        ])
    result = periods.copy()
    total_cost = (
        pd.to_numeric(result.get("commission"), errors="coerce").fillna(0.0)
        + pd.to_numeric(result.get("stamp_tax"), errors="coerce").fillna(0.0)
        + pd.to_numeric(result.get("slippage"), errors="coerce").fillna(0.0)
    )
    cost_return = pd.to_numeric(result.get("cost_return"), errors="coerce").fillna(0.0)
    for amount_column, return_column in (
        ("commission", "commission_return"),
        ("stamp_tax", "stamp_tax_return"),
        ("slippage", "slippage_return"),
    ):
        amount = pd.to_numeric(result.get(amount_column), errors="coerce").fillna(0.0)
        result[return_column] = np.where(total_cost.gt(0.0), -cost_return * amount / total_cost, 0.0)
    result["gross_active_return"] = (
        pd.to_numeric(result["gross_return"], errors="coerce")
        - pd.to_numeric(result["benchmark_return"], errors="coerce")
    )
    result["net_active_return"] = pd.to_numeric(result["active_return"], errors="coerce")
    result["reconciliation_error"] = (
        result["gross_active_return"]
        + result["commission_return"]
        + result["stamp_tax_return"]
        + result["slippage_return"]
        - result["net_active_return"]
    )
    columns = [
        "market", "role", "account_id", "signal_date", "entry_date",
        "gross_return", "benchmark_return", "gross_active_return",
        "commission_return", "stamp_tax_return", "slippage_return",
        "net_return", "net_active_return", "turnover", "trade_count",
        "reconciliation_error",
    ]
    return result[[column for column in columns if column in result.columns]]


def collect_model_gate_diagnostics(
    repo_root: str | Path,
    *,
    market: str,
    horizon: int,
    as_of: str,
) -> dict[str, Any]:
    """Summarize current model economic gates without affecting rule replay."""

    root = Path(repo_root) / "data" / "research" / "models" / market
    account_rows: list[dict[str, Any]] = []
    if not root.exists():
        return {"market": market, "status": "unavailable", "accounts": []}
    for account_root in sorted(path for path in root.iterdir() if path.is_dir()):
        tournament_root = account_root / str(horizon) / "tournaments"
        candidates = sorted(
            path for path in tournament_root.glob("*/report.json")
            if path.parent.name <= as_of
        ) if tournament_root.exists() else []
        if not candidates:
            continue
        report_path = candidates[-1]
        report = _read_json(report_path)
        candidate_rows: list[dict[str, Any]] = []
        for candidate in report.get("candidates") or []:
            metrics = dict(candidate.get("metrics") or {})
            artifact = Path(str(candidate.get("artifact") or ""))
            candidate_id = str(metrics.get("spec_id") or artifact.parent.name or "unknown")
            decision_path = report_path.parent / "candidates" / candidate_id / "final_decisions.parquet"
            decisions = pd.read_parquet(decision_path) if decision_path.exists() else pd.DataFrame()
            reason_counts = (
                {
                    str(reason): int(count)
                    for reason, count in decisions.get(
                        "no_trade_reason", pd.Series(dtype=str)
                    ).fillna("unknown").replace("", "allowed").value_counts().items()
                }
                if not decisions.empty else {}
            )

            def median(column: str) -> float | None:
                source = decisions[column] if column in decisions.columns else pd.Series(dtype=float)
                values = pd.to_numeric(source, errors="coerce").dropna()
                return float(values.median()) if len(values) else None

            candidate_rows.append({
                "candidate_id": candidate_id,
                "model_version": metrics.get("model_version"),
                "calibration_available": bool(metrics.get("edge_calibration_available", False)),
                "calibration_reason": metrics.get("edge_calibration_reason"),
                "median_expected_benefit_bps": median("gross_expected_edge_bps"),
                "median_round_trip_cost_bps": median("round_trip_cost_bps"),
                "median_uncertainty_bps": median("uncertainty_bps"),
                "trade_allowed_count": int(
                    decisions.get("trade_allowed", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()
                ) if not decisions.empty else 0,
                "decision_count": int(len(decisions)),
                "rejection_reasons": reason_counts,
            })
        account_rows.append({
            "account_scope": str(report.get("account_scope") or account_root.name),
            "report_as_of": str(report.get("as_of") or report_path.parent.name),
            "formal_strategy_activated": bool(report.get("formal_strategy_activated", False)),
            "status": str(report.get("status") or "unknown"),
            "candidates": candidate_rows,
        })
    return {
        "market": market,
        "status": "available" if account_rows else "unavailable",
        "accounts": account_rows,
    }


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _report_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Rule Core Diagnostic",
        "",
        f"- as_of: `{payload['as_of']}`",
        f"- status: `{payload['status']}`",
        f"- development_fraction: `{DEVELOPMENT_FRACTION:.0%}`",
        "",
        "| Market | Intended core | Status | Net return | Annualized excess wealth | Trades |",
        "|---|---|---|---:|---:|---:|",
    ]
    for market, item in payload["markets"].items():
        metrics = item.get("intended", {}).get("metrics") or {}
        lines.append(
            "| {market} | {name} | {status} | {net:.2%} | {excess:.2%} | {trades} |".format(
                market=market,
                name=item.get("intended", {}).get("name", ""),
                status=item.get("status", "unknown"),
                net=float(metrics.get("net_return") or 0.0),
                excess=float(metrics.get("annualized_excess_wealth") or 0.0),
                trades=int(metrics.get("trade_count") or 0),
            )
        )
    lines.extend(["", "## Stop Reasons", ""])
    for market, item in payload["markets"].items():
        reasons = item.get("audit", {}).get("reasons") or []
        lines.append(f"- `{market}`: " + (", ".join(reasons) if reasons else item.get("status", "")))
    return "\n".join(lines) + "\n"


def run_rule_core_diagnostic(
    repo_root: str | Path,
    *,
    as_of: str,
    offline: bool = True,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    store = ResearchStore(root / "data" / "research")
    as_of_key = str(as_of).replace("-", "")[:8]
    destination = Path(output_root) if output_root else (
        root / "data" / "research" / "rule_core_diagnostics" / as_of_key
    )
    destination.mkdir(parents=True, exist_ok=True)
    markets: dict[str, Any] = {}
    all_nav: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_periods: list[pd.DataFrame] = []

    for spec in RULE_CORE_SPECS:
        snapshot_date = store.latest_common_snapshot_date(spec.market, as_of=as_of_key)
        feature_path = store.feature_snapshot_path(spec.market, snapshot_date)
        label_path = store.label_snapshot_path(spec.market, snapshot_date)
        features = store.read_feature_snapshot(spec.market, snapshot_date)
        labels = store.read_label_snapshot(spec.market, snapshot_date)
        labels = labels.loc[pd.to_numeric(labels.get("horizon"), errors="coerce").eq(spec.horizon)].copy()
        labels = attach_entry_execution_constraints(features, labels)
        common_dates = sorted(
            set(_date_text(features["trade_date"]))
            .intersection(set(_date_text(labels["trade_date"])))
        )
        development_dates = select_development_dates(pd.DataFrame({"trade_date": common_dates}))
        intended_overlay = _read_json(root / spec.intended_overlay)
        control_overlay = _read_json(root / spec.control_overlay)
        baseline = _read_json(root / spec.baseline)
        names = _load_names(
            root,
            spec.market,
            features,
            development_end=development_dates[-1],
        )
        audit = audit_rule_core_data(
            features,
            labels,
            market=spec.market,
            overlay=intended_overlay,
            development_dates=development_dates,
            names_by_code=names,
            expected_account_sizes=dict(spec.expected_account_sizes),
        )
        control_audit = audit_rule_core_data(
            features,
            labels,
            market=spec.market,
            overlay=control_overlay,
            development_dates=development_dates,
            names_by_code=names,
            expected_account_sizes=dict(spec.expected_account_sizes),
        )
        intended: dict[str, Any] = {
            "overlay": spec.intended_overlay,
            "strategy_id": intended_overlay.get("strategy_id"),
            "name": intended_overlay.get("name"),
        }
        alternate_control: dict[str, Any] = {
            "control_id": "alternate_overlay",
            "overlay": spec.control_overlay,
            "strategy_id": control_overlay.get("strategy_id"),
            "name": control_overlay.get("name"),
            "audit": control_audit.as_dict(),
        }
        one_over_n_control: dict[str, Any] = {
            "control_id": "one_over_n",
            "name": "1/N 等权基线",
        }
        benchmark_control: dict[str, Any] = {
            "control_id": "benchmark",
            "name": "账户基准",
        }
        if audit.passes:
            metrics, nav, trades, periods = _run_overlay(
                features,
                labels,
                market=spec.market,
                overlay=intended_overlay,
                baseline=baseline,
                development_dates=development_dates,
                names_by_code=names,
            )
            intended["metrics"] = metrics
            intended_status = development_hypothesis_status(metrics, audit)
            equal_metrics, _, _, _ = _run_overlay(
                features,
                labels,
                market=spec.market,
                overlay=intended_overlay,
                baseline=baseline,
                development_dates=development_dates,
                names_by_code=names,
                equal_weight_control=True,
            )
            one_over_n_control["metrics"] = equal_metrics
            one_over_n_control["status"] = "reference"
            benchmark_control["metrics"] = _benchmark_control(metrics)
            benchmark_control["status"] = "reference"
            for frame, target in ((nav, all_nav), (trades, all_trades), (periods, all_periods)):
                if not frame.empty:
                    target.append(frame.assign(market=spec.market, role="intended"))
        else:
            intended_status = "data_blocked"
            intended["metrics"] = {}
            for control in (one_over_n_control, benchmark_control):
                control["status"] = "not_run_data_blocked"
                control["metrics"] = {}
        if control_audit.passes:
            control_metrics, _, _, _ = _run_overlay(
                features,
                labels,
                market=spec.market,
                overlay=control_overlay,
                baseline=baseline,
                development_dates=development_dates,
                names_by_code=names,
            )
            alternate_control["metrics"] = control_metrics
            alternate_control["status"] = development_hypothesis_status(
                control_metrics,
                control_audit,
            )
        else:
            alternate_control["metrics"] = {}
            alternate_control["status"] = "not_run_data_blocked"
        intended["status"] = intended_status
        markets[spec.market] = {
            "snapshot_date": snapshot_date,
            "feature_snapshot_sha256": _file_hash(feature_path),
            "label_snapshot_sha256": _file_hash(label_path),
            "development_window": [development_dates[0], development_dates[-1]],
            "development_dates": len(development_dates),
            "audit": audit.as_dict(),
            "control_audit": control_audit.as_dict(),
            "status": intended_status,
            "intended": intended,
            "controls": [alternate_control, one_over_n_control, benchmark_control],
        }
        artifact_prefix = "qdii" if spec.market == "cn_qdii_etf" else spec.market
        _write_json(destination / f"{artifact_prefix}_intended.json", intended)
        _write_json(
            destination / f"{artifact_prefix}_controls.json",
            {"controls": [alternate_control, one_over_n_control, benchmark_control]},
        )

    decision = {market: item["status"] for market, item in markets.items()}
    status = stage1_aggregate_status(decision)
    model_gate_diagnostics = {
        spec.market: collect_model_gate_diagnostics(
            root,
            market=spec.market,
            horizon=spec.horizon,
            as_of=as_of_key,
        )
        for spec in RULE_CORE_SPECS
    }
    payload = {
        "schema_version": "rule-core-diagnostic-v2",
        "as_of": as_of_key,
        "offline": bool(offline),
        "status": status,
        "decision": decision,
        "markets": markets,
        "model_gate_diagnostics": model_gate_diagnostics,
        "stage2_allowed_markets": (
            sorted(market for market, value in decision.items() if value == "proceed")
            if status in {"ready_for_stage2", "partial_proceed"} else []
        ),
    }
    _write_json(
        destination / "data_audit.json",
        {
            market: {
                **item["audit"],
                "control_audits": {
                    "alternate_overlay": item["control_audit"],
                },
            }
            for market, item in markets.items()
        },
    )
    _write_json(destination / "decision.json", payload)
    _write_json(destination / "model_gate_diagnostics.json", model_gate_diagnostics)
    parquet_store = ResearchStore(destination)
    parquet_store.write_parquet_atomic(
        destination / "nav.parquet",
        pd.concat(all_nav, ignore_index=True) if all_nav else pd.DataFrame(),
    )
    parquet_store.write_parquet_atomic(
        destination / "trades.parquet",
        pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(),
    )
    parquet_store.write_parquet_atomic(
        destination / "attribution.parquet",
        _return_attribution(
            pd.concat(all_periods, ignore_index=True) if all_periods else pd.DataFrame()
        ),
    )
    write_text_atomic(destination / "report.md", _report_markdown(payload), encoding="utf-8")
    payload["output_root"] = str(destination)
    return payload


__all__ = [
    "DataAudit",
    "RULE_CORE_SPECS",
    "attach_entry_execution_constraints",
    "audit_rule_core_data",
    "collect_model_gate_diagnostics",
    "development_hypothesis_status",
    "run_rule_core_diagnostic",
    "select_development_dates",
    "stage1_aggregate_status",
]
