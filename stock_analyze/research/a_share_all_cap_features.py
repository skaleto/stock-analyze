"""Decision-date and PIT membership adapters for A-share all-cap research."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import pandas as pd

from .a_share_all_cap_universe import MEMBERSHIP_COLUMNS
from .universe import (
    PointInTimeUniverseResult,
    decorate_point_in_time_universe,
)


ALL_CAP_UNIVERSE_CONTRACT_VERSION = "pit-all-cap-v1"
_MEMBERSHIP_SOURCE = "all_cap_quarterly"
_DEFAULT_BENCHMARKS = {
    "large": "000300.SH",
    "mid": "000905.SH",
    "small": "000852.SH",
    "micro": "932000.CSI",
}
_FROZEN_DECISION_INTERVALS = {
    "claude": {"large": 10, "mid": 10, "small": 10, "micro": 20},
    "codex": {"large": 5, "mid": 5, "small": 10, "micro": 20},
}
_SOURCE_DATE_COLUMNS = (
    "total_mv_source_date",
    "avg_amount_source_date",
    "industry_source_date",
)
_MEMBERSHIP_TEXT_COLUMNS = (
    "review_date",
    "effective_date",
    "code",
    "exclusion_reasons",
    "raw_sleeve",
    "stable_sleeve",
    "total_mv_source_date",
    "avg_amount_source_date",
    "industry_l1",
    "industry_l2",
    "industry_l3",
    "industry_source_date",
    "status_source",
    "universe_contract_version",
)


def _date_series(
    values: pd.Series,
    *,
    error: str,
    nullable: bool = False,
) -> pd.Series:
    raw = values.astype("string").str.strip().str.replace("-", "", regex=False)
    missing = raw.isna() | raw.eq("")
    parsed = pd.to_datetime(raw.mask(missing), format="%Y%m%d", errors="coerce")
    if ((~missing) & parsed.isna()).any() or (not nullable and missing.any()):
        raise ValueError(error)
    normalized = parsed.dt.strftime("%Y%m%d").astype("string")
    return normalized.mask(missing, pd.NA)


def _code_series(values: pd.Series, *, error: str) -> pd.Series:
    normalized = (
        values.astype("string")
        .str.strip()
        .str.upper()
        .str.split(".")
        .str[0]
        .str.zfill(6)
    )
    if normalized.isna().any() or not normalized.str.fullmatch(r"[0-9]{6}").all():
        raise ValueError(error)
    return normalized.astype("string")


def _contract_raw(contract: object) -> Mapping[str, object]:
    raw = getattr(contract, "raw", contract)
    if not isinstance(raw, Mapping):
        raise ValueError("all_cap_decision_calendar_contract")
    return raw


def _benchmarks(contract: object | None) -> dict[str, str]:
    if contract is None:
        return dict(_DEFAULT_BENCHMARKS)
    sleeves = getattr(contract, "sleeves", None)
    if sleeves is not None:
        result = {
            str(sleeve.name): str(sleeve.benchmark)
            for sleeve in sleeves
        }
    else:
        raw_sleeves = _contract_raw(contract).get("sleeves")
        if not isinstance(raw_sleeves, Mapping):
            raise ValueError("all_cap_membership_benchmarks")
        result = {}
        for name, value in raw_sleeves.items():
            if not isinstance(value, Mapping):
                raise ValueError("all_cap_membership_benchmarks")
            benchmark = value.get("benchmark")
            if not isinstance(benchmark, str) or not benchmark.strip():
                raise ValueError("all_cap_membership_benchmarks")
            result[str(name)] = benchmark.strip()
    if not result or any(not name or not benchmark for name, benchmark in result.items()):
        raise ValueError("all_cap_membership_benchmarks")
    return result


def build_decision_calendar(
    open_dates: Iterable[object],
    contract: object,
) -> pd.DataFrame:
    """Return each strategy/sleeve's frozen decision sessions."""

    raw_dates = pd.Series(list(open_dates), dtype="string")
    dates = _date_series(raw_dates, error="all_cap_decision_calendar_dates")
    if dates.duplicated().any() or dates.tolist() != sorted(dates.tolist()):
        raise ValueError("all_cap_decision_calendar_dates")

    raw = _contract_raw(contract)
    candidates = raw.get("candidates")
    if not isinstance(candidates, Mapping):
        raise ValueError("all_cap_decision_calendar_contract")
    sleeve_names = tuple(_benchmarks(contract))
    if (
        set(candidates) != set(_FROZEN_DECISION_INTERVALS)
        or any(
            set(intervals) != set(sleeve_names)
            for intervals in _FROZEN_DECISION_INTERVALS.values()
        )
    ):
        raise ValueError("all_cap_decision_calendar_contract")
    rows: list[dict[str, object]] = []
    for agent in candidates:
        candidate = candidates[agent]
        if not isinstance(agent, str) or not agent or not isinstance(candidate, Mapping):
            raise ValueError("all_cap_decision_calendar_contract")
        intervals = _FROZEN_DECISION_INTERVALS[agent]
        for sleeve in sleeve_names:
            interval = intervals[sleeve]
            for session_index in range(0, len(dates), interval):
                rows.append(
                    {
                        "trade_date": dates.iloc[session_index],
                        "agent": agent,
                        "stable_sleeve": sleeve,
                        "decision_interval_sessions": interval,
                    }
                )
    result = pd.DataFrame(
        rows,
        columns=(
            "trade_date",
            "agent",
            "stable_sleeve",
            "decision_interval_sessions",
        ),
    )
    for column in ("trade_date", "agent", "stable_sleeve"):
        result[column] = result[column].astype("string")
    result["decision_interval_sessions"] = result[
        "decision_interval_sessions"
    ].astype("Int64")
    return result


def _validated_membership(membership: pd.DataFrame) -> pd.DataFrame:
    missing = set(MEMBERSHIP_COLUMNS).difference(membership.columns)
    if missing:
        raise ValueError("all_cap_membership_schema")
    frame = membership.loc[:, list(MEMBERSHIP_COLUMNS)].copy()
    frame["code"] = _code_series(
        frame["code"],
        error="all_cap_membership_schema",
    )
    for column in ("review_date", "effective_date"):
        frame[column] = _date_series(
            frame[column],
            error="all_cap_membership_date",
        )
    for column in _SOURCE_DATE_COLUMNS:
        frame[column] = _date_series(
            frame[column],
            error="all_cap_membership_source_date",
            nullable=column == "industry_source_date",
        )
    if frame.duplicated(["code", "effective_date"], keep=False).any():
        raise ValueError("all_cap_membership_duplicate")
    if not frame["effective_date"].gt(frame["review_date"]).all():
        raise ValueError("all_cap_membership_date")
    if frame["eligible"].isna().any():
        raise ValueError("all_cap_membership_schema")
    eligible = frame["eligible"].eq(True)  # noqa: E712 - require explicit truth
    required_evidence = (
        "size_rank",
        "raw_sleeve",
        "stable_sleeve",
        "total_mv",
        "circ_mv",
        "total_mv_source_date",
        "avg_amount_252",
        "avg_amount_source_date",
        "non_trading_days_252",
        "industry_l1",
        "industry_l2",
        "industry_l3",
        "status_source",
        "universe_contract_version",
    )
    if any(
        (
            frame.loc[eligible, column].isna()
            | frame.loc[eligible, column].astype("string").str.strip().eq("")
        ).any()
        for column in required_evidence
    ):
        raise ValueError("all_cap_membership_schema")
    for column in _MEMBERSHIP_TEXT_COLUMNS:
        frame[column] = frame[column].astype("string")
    return frame


def attach_all_cap_membership(
    features: pd.DataFrame,
    membership: pd.DataFrame,
    *,
    contract: object | None = None,
) -> PointInTimeUniverseResult:
    """Attach the latest effective eligible sleeve to each exact feature date."""

    if {"code", "trade_date"}.difference(features.columns):
        raise ValueError("all_cap_feature_schema")
    prepared = features.copy()
    prepared["code"] = _code_series(
        prepared["code"],
        error="all_cap_feature_schema",
    )
    prepared["trade_date"] = _date_series(
        prepared["trade_date"],
        error="all_cap_feature_date",
    )
    if "signal_date" in prepared.columns:
        prepared["signal_date"] = _date_series(
            prepared["signal_date"],
            error="all_cap_feature_date",
        )
    prepared["_row_id"] = range(len(prepared))
    prepared["_asof_key"] = pd.to_datetime(
        prepared["trade_date"],
        format="%Y%m%d",
    )

    validated = _validated_membership(membership)
    benchmarks = _benchmarks(contract)
    eligible = validated.loc[
        validated["eligible"].eq(True)
        & validated["stable_sleeve"].isin(benchmarks)
    ].copy()
    eligible = eligible.rename(
        columns={"universe_contract_version": "membership_contract_version"}
    )
    eligible["_effective_key"] = pd.to_datetime(
        eligible["effective_date"],
        format="%Y%m%d",
    )

    membership_output_columns = [
        column for column in eligible.columns
        if column not in {"code", "_effective_key"}
    ]
    feature_collisions = [
        column for column in membership_output_columns
        if column in prepared.columns
    ]
    prepared = prepared.drop(columns=feature_collisions)

    joined: list[pd.DataFrame] = []
    for code, feature_rows in prepared.groupby("code", sort=False):
        membership_rows = eligible.loc[eligible["code"].eq(code)]
        if membership_rows.empty:
            continue
        attached = pd.merge_asof(
            feature_rows.sort_values("_asof_key", kind="stable"),
            membership_rows.drop(columns=["code"]).sort_values(
                "_effective_key",
                kind="stable",
            ),
            left_on="_asof_key",
            right_on="_effective_key",
            direction="backward",
            allow_exact_matches=True,
        )
        joined.append(attached.loc[attached["effective_date"].notna()])

    if joined:
        result = pd.concat(joined, ignore_index=True, sort=False)
        result = result.sort_values("_row_id", kind="stable").reset_index(drop=True)
    else:
        result = prepared.iloc[:0].copy()
        for column in membership_output_columns:
            result[column] = pd.Series(dtype=eligible[column].dtype)

    cutoffs = ["trade_date"]
    if "signal_date" in result.columns:
        cutoffs.append("signal_date")
    for source_column in _SOURCE_DATE_COLUMNS:
        source_dates = result[source_column].astype("string")
        populated = source_dates.notna() & source_dates.ne("")
        if any(
            (
                populated
                & source_dates.gt(result[cutoff].astype("string"))
            ).any()
            for cutoff in cutoffs
        ):
            raise ValueError("all_cap_membership_future_source")

    result["account_id"] = result["stable_sleeve"]
    result["research_scope"] = result["stable_sleeve"]
    result["benchmark_code"] = result["stable_sleeve"].map(benchmarks)
    result["benchmark"] = result["benchmark_code"]
    result["membership_snapshot"] = result["review_date"]
    result = result.drop(columns=["_row_id", "_asof_key", "_effective_key"])
    string_columns = (
        "code",
        "trade_date",
        "signal_date",
        "review_date",
        "effective_date",
        "raw_sleeve",
        "stable_sleeve",
        "total_mv_source_date",
        "avg_amount_source_date",
        "industry_source_date",
        "membership_contract_version",
        "account_id",
        "research_scope",
        "benchmark_code",
        "benchmark",
        "membership_snapshot",
    )
    for column in string_columns:
        if column in result.columns:
            result[column] = result[column].astype("string")
    return decorate_point_in_time_universe(
        result,
        source=_MEMBERSHIP_SOURCE,
        unbiased=bool(len(result)),
        input_rows=len(features),
        contract_version=ALL_CAP_UNIVERSE_CONTRACT_VERSION,
    )


__all__ = [
    "ALL_CAP_UNIVERSE_CONTRACT_VERSION",
    "attach_all_cap_membership",
    "build_decision_calendar",
]
