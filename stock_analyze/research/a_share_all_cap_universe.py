"""PIT all-cap universe membership for isolated A-share research."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import a_share_all_cap_universe_store as universe_store
from .a_share_all_cap_contract import AllCapContract
from .a_share_all_cap_sources import load_verified_all_cap_sources


MEMBERSHIP_COLUMNS = (
    "review_date",
    "effective_date",
    "code",
    "eligible",
    "exclusion_reasons",
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
    "industry_source_date",
    "status_source",
    "universe_contract_version",
)
DAILY_HARD_STATUS_COLUMNS = (
    "trade_date",
    "code",
    "listed",
    "st",
    "delisting",
    "suspended",
    "limit_up",
    "limit_down",
    "at_limit_up",
    "at_limit_down",
    "status_complete",
    "status_conflict",
    "buy_executable",
    "sell_executable",
    "prohibit_new_position",
    "status_source",
    "hard_status_version",
)

_FUNDED_SLEEVES = ("large", "mid", "small", "micro")
_ALL_SLEEVES = (*_FUNDED_SLEEVES, "nano_watch")
UNIVERSE_CONTRACT_VERSION = "a-share-all-cap-universe-v1"
HARD_STATUS_VERSION = "a-share-all-cap-hard-status-v1"
_REBUILDABLE_PUBLICATION_ERRORS = {
    "all_cap_universe_cache_identity_mismatch",
    "all_cap_universe_manifest_schema",
}

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


def _validated_boundaries(boundaries: Sequence[int]) -> tuple[int, int, int, int]:
    if (
        not isinstance(boundaries, (tuple, list))
        or len(boundaries) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in boundaries)
    ):
        raise ValueError("all_cap_universe_sleeve:boundaries")
    parsed = tuple(boundaries)
    if parsed[0] < 1 or any(left >= right for left, right in zip(parsed, parsed[1:])):
        raise ValueError("all_cap_universe_sleeve:boundaries")
    return parsed


def raw_sleeve_for_rank(
    size_rank: int,
    boundaries: Sequence[int] = (300, 800, 1800, 3800),
) -> str:
    """Return the frozen raw size sleeve for a one-based market-cap rank."""

    parsed = _validated_boundaries(boundaries)
    if isinstance(size_rank, bool) or not isinstance(size_rank, int) or size_rank < 1:
        raise ValueError("all_cap_universe_sleeve:rank")
    for boundary, sleeve in zip(parsed, _FUNDED_SLEEVES, strict=True):
        if size_rank <= boundary:
            return sleeve
    return "nano_watch"


def assign_stable_sleeve(
    *,
    size_rank: int,
    previous: str | None,
    boundaries: Sequence[int],
    buffer_fraction: float,
) -> str:
    """Apply the frozen rank-boundary buffer to a previously eligible sleeve."""

    parsed = _validated_boundaries(boundaries)
    raw = raw_sleeve_for_rank(size_rank, parsed)
    if (
        isinstance(buffer_fraction, bool)
        or not isinstance(buffer_fraction, (int, float))
        or not math.isfinite(float(buffer_fraction))
        or not 0.0 <= float(buffer_fraction) < 1.0
    ):
        raise ValueError("all_cap_universe_sleeve:buffer")
    if previous is not None and previous not in _ALL_SLEEVES:
        raise ValueError("all_cap_universe_sleeve:previous")
    if previous is None or previous == raw:
        return raw

    index = _ALL_SLEEVES.index(previous)
    lower_boundary = 1 if index == 0 else parsed[index - 1] + 1
    if previous == "nano_watch":
        upper_boundary = math.inf
    else:
        upper_boundary = parsed[index]
    lower = max(1, math.floor(lower_boundary * (1.0 - float(buffer_fraction))))
    upper = math.inf if math.isinf(upper_boundary) else math.floor(
        upper_boundary * (1.0 + float(buffer_fraction))
    )
    return previous if lower <= size_rank <= upper else raw


def _date_key(value: object, *, error: str) -> str:
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        raw = str(value or "").strip()
        try:
            parsed = (
                date.fromisoformat(raw)
                if "-" in raw
                else datetime.strptime(raw, "%Y%m%d").date()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(error) from exc
    return parsed.strftime("%Y%m%d")


def _input_frame(
    inputs: Mapping[str, object],
    name: str,
    *,
    required: Sequence[str],
) -> pd.DataFrame:
    value = inputs.get(name)
    if not isinstance(value, pd.DataFrame):
        raise ValueError(f"all_cap_universe_insufficient_data:{name}")
    frame = value.copy(deep=True)
    if "ts_code" in frame.columns:
        frame["code"] = frame["ts_code"].astype("string[pyarrow]").str.strip()
    elif "code" in frame.columns:
        frame["code"] = frame["code"].astype("string[pyarrow]").str.strip()
    if set(required).difference(frame.columns):
        raise ValueError(f"all_cap_universe_schema:{name}")
    if "code" in required and (frame["code"].isna().any() or frame["code"].eq("").any()):
        raise ValueError(f"all_cap_universe_schema:{name}")
    return frame


def _normalize_date_column(
    frame: pd.DataFrame,
    column: str,
    *,
    nullable: bool = False,
    error: str,
) -> pd.DataFrame:
    result = frame.copy()
    normalized: list[object] = []
    for value in result[column]:
        if nullable and (value is None or pd.isna(value) or not str(value).strip()):
            normalized.append(pd.NA)
        else:
            normalized.append(_date_key(value, error=error))
    result[column] = pd.Series(
        normalized,
        index=result.index,
        dtype="string[pyarrow]",
    )
    return result


def _as_of_source_rows(
    frame: pd.DataFrame,
    review_key: str,
    *,
    source_date_column: str = "source_date",
) -> pd.DataFrame:
    if source_date_column not in frame:
        return frame.copy()
    normalized = _normalize_date_column(
        frame,
        source_date_column,
        error="all_cap_universe_source_date",
    )
    return normalized.loc[normalized[source_date_column].le(review_key)].copy()


def _single_latest_master(frame: pd.DataFrame, review_key: str) -> pd.DataFrame:
    master = _as_of_source_rows(frame, review_key)
    if master.empty:
        raise ValueError("all_cap_universe_insufficient_data:stock_master")
    if "source_date" in master:
        latest = master.groupby("code", sort=False)["source_date"].transform("max")
        master = master.loc[master["source_date"].eq(latest)].copy()
    comparable = [
        column
        for column in ("list_date", "delist_date", "list_status", "source_date")
        if column in master
    ]
    if master.duplicated("code", keep=False).any():
        conflicts = master.groupby("code", dropna=False)[comparable].nunique(dropna=False)
        if (conflicts.gt(1).any(axis=1)).any():
            raise ValueError("all_cap_universe_stock_master_ambiguous")
        master = master.drop_duplicates("code", keep="first")
    return master.sort_values("code", kind="stable").reset_index(drop=True)


def _board_for_code(code: str) -> str | None:
    symbol, dot, exchange = code.partition(".")
    if dot != "." or len(symbol) != 6 or not symbol.isdigit():
        return None
    if exchange == "BJ" and symbol.startswith(("4", "8", "9")):
        return "bse"
    if exchange == "SH":
        if symbol.startswith("688"):
            return "star"
        if symbol.startswith("900"):
            return None
        if symbol.startswith(("600", "601", "603", "605")):
            return "main_board"
    if exchange == "SZ":
        if symbol.startswith(("300", "301")):
            return "chinext"
        if symbol.startswith("200"):
            return None
        if symbol.startswith(("000", "001", "002", "003")):
            return "main_board"
    return None


def _contract_universe(contract: object) -> Mapping[str, Any]:
    raw = getattr(contract, "raw", None)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("universe"), Mapping):
        raise ValueError("all_cap_universe_contract")
    return raw["universe"]


def _open_sessions(
    calendar: pd.DataFrame,
    review_key: str,
    lookback: int,
) -> tuple[list[str], str]:
    normalized = _normalize_date_column(
        calendar,
        "cal_date",
        error="all_cap_universe_calendar",
    )
    if normalized.duplicated("cal_date", keep=False).any():
        raise ValueError("all_cap_universe_calendar_ambiguous")
    is_open = normalized["is_open"].astype(str).str.strip().isin({"1", "True", "true"})
    opens = sorted(normalized.loc[is_open, "cal_date"].astype(str).tolist())
    if review_key not in opens:
        raise ValueError("all_cap_universe_review_not_open")
    next_opens = [value for value in opens if value > review_key]
    if not next_opens:
        raise ValueError("all_cap_universe_next_open")
    trailing = [value for value in opens if value <= review_key][-lookback:]
    return trailing, next_opens[0]


def _flag(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    raw = str(value).strip().lower()
    if raw in {"1", "true", "y", "yes"}:
        return True
    if raw in {"0", "false", "n", "no"}:
        return False
    return None


def _review_status(
    status: pd.DataFrame,
    review_key: str,
    codes: Sequence[str],
) -> dict[str, dict[str, object] | None]:
    current = _as_of_source_rows(status, review_key)
    current = _normalize_date_column(
        current,
        "trade_date",
        error="all_cap_universe_status_date",
    )
    current = current.loc[current["trade_date"].eq(review_key)].copy()
    if "source_date" in current:
        latest = current.groupby("code", sort=False)["source_date"].transform("max")
        current = current.loc[current["source_date"].eq(latest)].copy()
    result: dict[str, dict[str, object] | None] = {}
    fields = ("is_st", "tradestatus", "is_delisting", "status_source")
    for code in codes:
        rows = current.loc[current["code"].eq(code)]
        if len(rows) != 1:
            result[code] = None
            continue
        row = rows.iloc[0]
        is_st = _flag(row["is_st"])
        is_delisting = _flag(row["is_delisting"])
        trade_status = _flag(row["tradestatus"])
        source = str(row["status_source"] or "").strip()
        if is_st is None or is_delisting is None or trade_status is None or not source:
            result[code] = None
            continue
        result[code] = {
            "is_st": is_st,
            "is_delisting": is_delisting,
            "suspended": not trade_status,
            "status_source": source,
        }
    return result


def _daily_liquidity(
    daily: pd.DataFrame,
    review_key: str,
    sessions: Sequence[str],
    codes: Sequence[str],
    list_dates: Mapping[str, str],
) -> dict[str, tuple[float, object, int, bool, int]]:
    normalized = _normalize_date_column(
        daily,
        "trade_date",
        error="all_cap_universe_daily_date",
    )
    normalized = normalized.loc[
        normalized["trade_date"].le(review_key)
        & normalized["trade_date"].isin(sessions)
        & normalized["code"].isin(codes)
    ].copy()
    if normalized.duplicated(["trade_date", "code"], keep=False).any():
        raise ValueError("all_cap_universe_daily_ambiguous")
    normalized["amount_numeric"] = pd.to_numeric(normalized["amount"], errors="coerce")
    result: dict[str, tuple[float, object, int, bool, int]] = {}
    for code in codes:
        code_sessions = [
            trade_key for trade_key in sessions if trade_key >= list_dates[code]
        ]
        rows = normalized.loc[normalized["code"].eq(code)].set_index("trade_date")
        values = rows["amount_numeric"].reindex(code_sessions)
        invalid = bool(
            values.dropna().map(lambda value: not math.isfinite(float(value)) or float(value) < 0).any()
        )
        observed_dates = values.index[values.notna()].tolist()
        source_date: object = max(observed_dates) if observed_dates else pd.NA
        if invalid or values.notna().sum() == 0:
            result[code] = (
                math.nan,
                source_date,
                int(values.isna().sum()),
                False,
                len(code_sessions),
            )
            continue
        filled = values.fillna(0.0).astype("float64")
        result[code] = (
            float(filled.mean()),
            source_date,
            int(filled.le(0.0).sum()),
            True,
            len(code_sessions),
        )
    return result


def _latest_market_caps(
    daily_basic: pd.DataFrame,
    review_key: str,
    codes: Sequence[str],
) -> dict[str, tuple[float, float, object]]:
    current = _as_of_source_rows(daily_basic, review_key)
    current = _normalize_date_column(
        current,
        "trade_date",
        error="all_cap_universe_daily_basic_date",
    )
    current = current.loc[
        current["trade_date"].le(review_key) & current["code"].isin(codes)
    ].copy()
    result: dict[str, tuple[float, float, object]] = {}
    for code in codes:
        rows = current.loc[current["code"].eq(code)]
        if rows.empty:
            result[code] = (math.nan, math.nan, pd.NA)
            continue
        latest_date = rows["trade_date"].max()
        latest = rows.loc[rows["trade_date"].eq(latest_date)]
        pairs = latest.loc[:, ["total_mv", "circ_mv"]].drop_duplicates()
        if len(pairs) != 1:
            result[code] = (math.nan, math.nan, latest_date)
            continue
        total = pd.to_numeric(pd.Series([pairs.iloc[0]["total_mv"]]), errors="coerce").iloc[0]
        circ = pd.to_numeric(pd.Series([pairs.iloc[0]["circ_mv"]]), errors="coerce").iloc[0]
        result[code] = (float(total), float(circ), latest_date)
    return result


def _industry_as_of(
    industry: pd.DataFrame,
    review_key: str,
    codes: Sequence[str],
) -> dict[str, tuple[str, str, str, str] | str]:
    current = _as_of_source_rows(industry, review_key)
    current = _normalize_date_column(
        current,
        "in_date",
        error="all_cap_universe_industry_date",
    )
    current = _normalize_date_column(
        current,
        "out_date",
        nullable=True,
        error="all_cap_universe_industry_date",
    )
    current = current.loc[
        current["in_date"].le(review_key)
        & (current["out_date"].isna() | current["out_date"].gt(review_key))
    ].copy()
    result: dict[str, tuple[str, str, str, str] | str] = {}
    fields = ["l1_code", "l2_code", "l3_code", "in_date"]
    for code in codes:
        rows = current.loc[current["code"].eq(code)].copy()
        if rows.empty:
            result[code] = ("unclassified", "unclassified", "unclassified", pd.NA)
        elif len(rows) != 1:
            result[code] = "industry_ambiguous"
        else:
            row = rows.iloc[0]
            values = tuple(str(row[field] or "").strip() for field in fields[:3])
            if not all(values):
                result[code] = ("unclassified", "unclassified", "unclassified", pd.NA)
            else:
                source_date = str(row["in_date"])
                result[code] = (*values, source_date)
    return result


def _previous_sleeves(
    previous: pd.DataFrame,
    review_key: str,
) -> dict[str, str]:
    if previous.empty:
        return {}
    frame = _normalize_date_column(
        previous,
        "review_date",
        error="all_cap_universe_previous_date",
    )
    frame = frame.loc[frame["review_date"].lt(review_key)].copy()
    if frame.empty:
        return {}
    latest = frame.groupby("code", sort=False)["review_date"].transform("max")
    frame = frame.loc[frame["review_date"].eq(latest)]
    if frame.duplicated("code", keep=False).any():
        conflicts = frame.groupby("code")[["eligible", "stable_sleeve"]].nunique(
            dropna=False
        )
        if conflicts.gt(1).any(axis=1).any():
            raise ValueError("all_cap_universe_previous_ambiguous")
        frame = frame.drop_duplicates("code")
    eligible = frame["eligible"].map(_flag)
    if eligible.isna().any():
        raise ValueError("all_cap_universe_previous_ambiguous")
    frame = frame.loc[eligible.eq(True)].copy()  # noqa: E712 - explicit boolean
    result = {
        str(row.code): str(row.stable_sleeve)
        for row in frame.itertuples(index=False)
    }
    if any(value not in _ALL_SLEEVES for value in result.values()):
        raise ValueError("all_cap_universe_sleeve:previous")
    return result


def _percentile_positions(
    rows: Sequence[tuple[str, float]],
) -> dict[str, int]:
    ordered = sorted(rows, key=lambda item: (-item[1], item[0]))
    return {code: index for index, (code, _) in enumerate(ordered, 1)}


def _membership_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.loc[:, list(MEMBERSHIP_COLUMNS)].copy()
    for column in _MEMBERSHIP_TEXT_COLUMNS:
        result[column] = result[column].astype("string[pyarrow]")
    result["eligible"] = result["eligible"].astype(bool)
    result["size_rank"] = result["size_rank"].astype("Int64")
    for column in ("total_mv", "circ_mv", "avg_amount_252"):
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("float64")
    result["non_trading_days_252"] = result["non_trading_days_252"].astype("Int64")
    return result


def build_review_membership(
    inputs: Mapping[str, object],
    review_date: date | str,
    contract: object,
) -> pd.DataFrame:
    """Build one quarterly, PIT-safe all-cap membership cross-section."""

    if not isinstance(inputs, Mapping):
        raise ValueError("all_cap_universe_inputs")
    review_key = _date_key(review_date, error="all_cap_universe_review_date")
    universe = _contract_universe(contract)
    try:
        lookback = int(universe["liquidity_lookback_sessions"])
        maximum_non_trading = int(universe["maximum_non_trading_days"])
        new_percentile = float(universe["new_entry_minimum_amount_percentile"])
        retention_percentile = float(universe["retention_minimum_amount_percentile"])
        listing_age = universe["listing_age"]
        age_by_board = {
            "main_board": int(listing_age["main_board_days"]),
            "chinext": int(listing_age["chinext_days"]),
            "star": int(listing_age["star_days"]),
            "bse": int(listing_age["bse_days"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("all_cap_universe_contract") from exc
    if (
        lookback <= 0
        or maximum_non_trading < 0
        or not 0.0 <= retention_percentile <= new_percentile < 1.0
    ):
        raise ValueError("all_cap_universe_contract")

    calendar = _input_frame(
        inputs, "trade_calendar", required=("cal_date", "is_open")
    )
    sessions, effective_key = _open_sessions(calendar, review_key, lookback)
    master = _input_frame(
        inputs,
        "stock_master",
        required=("code", "list_date", "delist_date", "list_status"),
    )
    master = _single_latest_master(master, review_key)
    master = _normalize_date_column(
        master,
        "list_date",
        error="all_cap_universe_stock_master_date",
    )
    master = _normalize_date_column(
        master,
        "delist_date",
        nullable=True,
        error="all_cap_universe_stock_master_date",
    )
    codes = master["code"].astype(str).tolist()

    daily = _input_frame(
        inputs, "daily", required=("code", "trade_date", "amount")
    )
    daily_basic = _input_frame(
        inputs,
        "daily_basic",
        required=("code", "trade_date", "total_mv", "circ_mv"),
    )
    status = _input_frame(
        inputs,
        "status",
        required=(
            "code",
            "trade_date",
            "is_st",
            "tradestatus",
            "is_delisting",
            "status_source",
        ),
    )
    industry = _input_frame(
        inputs,
        "industry_membership",
        required=("code", "l1_code", "l2_code", "l3_code", "in_date", "out_date"),
    )
    previous = _input_frame(
        inputs,
        "previous_membership",
        required=("code", "review_date", "eligible", "stable_sleeve"),
    )
    forced_raw = inputs.get("forced_exclusions", {})
    if not isinstance(forced_raw, Mapping):
        raise ValueError("all_cap_universe_inputs:forced_exclusions")
    forced_exclusions: dict[str, set[str]] = {}
    for code, reasons in forced_raw.items():
        if not isinstance(reasons, (list, tuple, set, frozenset)):
            raise ValueError("all_cap_universe_inputs:forced_exclusions")
        normalized_reasons = {
            str(reason).strip() for reason in reasons if str(reason).strip()
        }
        forced_exclusions[str(code)] = normalized_reasons

    status_by_code = _review_status(status, review_key, codes)
    liquidity_by_code = _daily_liquidity(
        daily,
        review_key,
        sessions,
        codes,
        {
            str(row.code): str(row.list_date)
            for row in master.itertuples(index=False)
        },
    )
    caps_by_code = _latest_market_caps(daily_basic, review_key, codes)
    industry_by_code = _industry_as_of(industry, review_key, codes)
    previous_by_code = _previous_sleeves(previous, review_key)

    review_day = datetime.strptime(review_key, "%Y%m%d").date()
    rows: dict[str, dict[str, object]] = {}
    base_liquidity: list[tuple[str, float]] = []
    for master_row in master.itertuples(index=False):
        code = str(master_row.code)
        reasons = set(forced_exclusions.get(code, set()))
        list_key = str(master_row.list_date)
        delist_key = None if pd.isna(master_row.delist_date) else str(master_row.delist_date)
        board = _board_for_code(code)
        if list_key > review_key:
            reasons.add("not_listed")
        if delist_key is not None and review_key > delist_key:
            reasons.add("delisted")
        if board is None:
            reasons.add("unsupported_board")
        elif list_key <= review_key:
            list_day = datetime.strptime(list_key, "%Y%m%d").date()
            if (review_day - list_day).days < age_by_board[board]:
                reasons.add("listing_age")
        current_status = status_by_code[code]
        status_source: object = pd.NA
        if current_status is None:
            reasons.add("status_missing")
        else:
            status_source = current_status["status_source"]
            if current_status["is_st"]:
                reasons.add("st")
            if current_status["is_delisting"]:
                reasons.add("delisted")
            if current_status["suspended"]:
                reasons.add("suspended")

        (
            average_amount,
            average_amount_source_date,
            non_trading_days,
            amount_valid,
            available_sessions,
        ) = liquidity_by_code[code]
        if not amount_valid:
            reasons.add("amount_invalid")
        allowed_non_trading = math.floor(
            maximum_non_trading * available_sessions / lookback
        )
        if non_trading_days > allowed_non_trading:
            reasons.add("non_trading_days")

        total_mv, circ_mv, total_mv_source_date = caps_by_code[code]
        if not math.isfinite(total_mv) or total_mv <= 0:
            reasons.add("total_mv_invalid")
        if not math.isfinite(circ_mv) or circ_mv <= 0:
            reasons.add("circ_mv_invalid")

        industry_value = industry_by_code[code]
        if isinstance(industry_value, str):
            reasons.add(industry_value)
            industry_l1 = industry_l2 = industry_l3 = industry_source_date = pd.NA
        else:
            industry_l1, industry_l2, industry_l3, industry_source_date = industry_value
        if not reasons:
            base_liquidity.append((code, average_amount))
        rows[code] = {
            "review_date": review_key,
            "effective_date": effective_key,
            "code": code,
            "eligible": False,
            "exclusion_reasons": reasons,
            "size_rank": pd.NA,
            "raw_sleeve": pd.NA,
            "stable_sleeve": pd.NA,
            "total_mv": total_mv,
            "circ_mv": circ_mv,
            "total_mv_source_date": total_mv_source_date,
            "avg_amount_252": average_amount,
            "avg_amount_source_date": average_amount_source_date,
            "non_trading_days_252": non_trading_days,
            "industry_l1": industry_l1,
            "industry_l2": industry_l2,
            "industry_l3": industry_l3,
            "industry_source_date": industry_source_date,
            "status_source": status_source,
            "universe_contract_version": UNIVERSE_CONTRACT_VERSION,
        }

    liquidity_positions = _percentile_positions(base_liquidity)
    new_cutoff = math.ceil((1.0 - new_percentile) * len(base_liquidity))
    retention_cutoff = math.ceil((1.0 - retention_percentile) * len(base_liquidity))
    for code, position in liquidity_positions.items():
        cutoff = retention_cutoff if code in previous_by_code else new_cutoff
        if position > cutoff:
            rows[code]["exclusion_reasons"].add("liquidity")

    eligible_rows = [
        row
        for row in rows.values()
        if not row["exclusion_reasons"]
    ]
    eligible_rows.sort(key=lambda row: (-float(row["total_mv"]), str(row["code"])))
    boundaries = getattr(contract, "size_boundaries", None)
    buffer_fraction = getattr(contract, "boundary_buffer_fraction", None)
    for size_rank, row in enumerate(eligible_rows, 1):
        raw_sleeve = raw_sleeve_for_rank(size_rank, boundaries)
        row["eligible"] = True
        row["size_rank"] = size_rank
        row["raw_sleeve"] = raw_sleeve
        row["stable_sleeve"] = assign_stable_sleeve(
            size_rank=size_rank,
            previous=previous_by_code.get(str(row["code"])),
            boundaries=boundaries,
            buffer_fraction=buffer_fraction,
        )

    output_rows = []
    for code in sorted(rows):
        row = rows[code]
        row["exclusion_reasons"] = ";".join(sorted(row["exclusion_reasons"]))
        output_rows.append(row)
    return _membership_dtypes(pd.DataFrame(output_rows, columns=MEMBERSHIP_COLUMNS))


def _hard_status_day_frame(
    frame: pd.DataFrame,
    *,
    name: str,
    trade_key: str,
) -> pd.DataFrame:
    normalized = _normalize_date_column(
        frame,
        "trade_date",
        error=f"all_cap_hard_status_date:{name}",
    )
    if normalized["trade_date"].gt(trade_key).any():
        raise ValueError(f"all_cap_hard_status_future:{name}")
    current = normalized.loc[normalized["trade_date"].eq(trade_key)].copy()
    if current.duplicated(["trade_date", "code"], keep=False).any():
        raise ValueError(f"all_cap_hard_status_duplicate:{name}")
    return current.set_index("code", drop=False)


def _full_day_suspension(row: pd.Series | None) -> bool:
    if row is None:
        return False
    timing = str(row.get("suspend_timing", "") or "").strip().replace(" ", "")
    return timing in {"09:30-15:00", "09:30~15:00", "全天", "FULL_DAY"}


def _index_namechange_intervals(
    frame: pd.DataFrame,
) -> Mapping[str, tuple[tuple[str, object, object, object], ...]]:
    normalized = _input_frame(
        {"namechange": frame},
        "namechange",
        required=("code", "name", "start_date", "end_date", "ann_date"),
    )
    normalized = _normalize_date_column(
        normalized,
        "start_date",
        error="all_cap_hard_status_date:namechange",
    )
    for column in ("end_date", "ann_date"):
        normalized = _normalize_date_column(
            normalized,
            column,
            nullable=True,
            error="all_cap_hard_status_date:namechange",
        )
    identity = ["code", "name", "start_date", "end_date", "ann_date"]
    if normalized.duplicated(identity, keep=False).any():
        raise ValueError("all_cap_hard_status_duplicate:namechange")
    indexed: dict[str, list[tuple[str, object, object, object]]] = {}
    for row in normalized.itertuples(index=False):
        indexed.setdefault(str(row.code), []).append(
            (str(row.start_date), row.end_date, row.ann_date, row.name)
        )
    return {
        code: tuple(sorted(intervals, key=lambda value: value[0]))
        for code, intervals in indexed.items()
    }


def _namechange_st_by_code(
    index: Mapping[str, tuple[tuple[str, object, object, object], ...]],
    trade_key: str,
    codes: Sequence[str],
) -> dict[str, bool | None]:
    result: dict[str, bool | None] = {}
    for code in codes:
        intervals = [
            interval
            for interval in index.get(code, ())
            if interval[0] <= trade_key
            and (pd.isna(interval[1]) or str(interval[1]) >= trade_key)
            and not pd.isna(interval[2])
            and str(interval[2]) <= trade_key
        ]
        if len(intervals) != 1:
            result[code] = None
        else:
            raw_name = intervals[0][3]
            name = "" if pd.isna(raw_name) else str(raw_name).strip().upper()
            if not name:
                result[code] = None
            else:
                result[code] = re.match(r"^\*?ST", name) is not None
    return result


def _hard_status_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.loc[:, list(DAILY_HARD_STATUS_COLUMNS)].copy()
    for column in (
        "trade_date",
        "code",
        "status_source",
        "hard_status_version",
    ):
        result[column] = result[column].astype("string[pyarrow]")
    boolean_columns = set(DAILY_HARD_STATUS_COLUMNS).difference(
        {"trade_date", "code", "limit_up", "limit_down", "status_source", "hard_status_version"}
    )
    for column in boolean_columns:
        result[column] = result[column].astype(bool)
    result["limit_up"] = pd.to_numeric(result["limit_up"], errors="coerce").astype("float64")
    result["limit_down"] = pd.to_numeric(result["limit_down"], errors="coerce").astype("float64")
    return result


def build_daily_hard_status(
    *,
    trade_date: date | str,
    stock_master: pd.DataFrame,
    daily: pd.DataFrame,
    stk_limit: pd.DataFrame,
    baostock_status: pd.DataFrame,
    namechange: pd.DataFrame,
    suspend_d: pd.DataFrame,
    _namechange_index: Mapping[
        str, tuple[tuple[str, object, object, object], ...]
    ] | None = None,
) -> pd.DataFrame:
    """Build execution gates without changing quarterly sleeve membership."""

    trade_key = _date_key(trade_date, error="all_cap_hard_status_date")
    inputs: Mapping[str, object] = {
        "stock_master": stock_master,
        "daily": daily,
        "stk_limit": stk_limit,
        "baostock_status": baostock_status,
        "namechange": namechange,
        "suspend_d": suspend_d,
    }
    master = _input_frame(
        inputs,
        "stock_master",
        required=("code", "list_date", "delist_date", "list_status"),
    )
    if "source_date" in master:
        master = _normalize_date_column(
            master,
            "source_date",
            error="all_cap_hard_status_date:stock_master",
        )
        if master["source_date"].gt(trade_key).any():
            raise ValueError("all_cap_hard_status_future:stock_master")
        latest = master.groupby("code", sort=False)["source_date"].transform("max")
        master = master.loc[master["source_date"].eq(latest)].copy()
    if master.duplicated("code", keep=False).any():
        raise ValueError("all_cap_hard_status_duplicate:stock_master")
    master = _normalize_date_column(
        master,
        "list_date",
        error="all_cap_hard_status_date:stock_master",
    )
    master = _normalize_date_column(
        master,
        "delist_date",
        nullable=True,
        error="all_cap_hard_status_date:stock_master",
    )

    daily_frame = _input_frame(
        inputs, "daily", required=("code", "trade_date", "open")
    )
    limit_frame = _input_frame(
        inputs,
        "stk_limit",
        required=("code", "trade_date", "up_limit", "down_limit"),
    )
    status_frame = _input_frame(
        inputs,
        "baostock_status",
        required=("code", "trade_date", "tradestatus", "is_st", "st_source"),
    )
    namechange_frame = _input_frame(
        inputs,
        "namechange",
        required=("code", "name", "start_date", "end_date", "ann_date"),
    )
    suspension_frame = _input_frame(
        inputs,
        "suspend_d",
        required=("code", "trade_date", "suspend_timing", "suspend_type"),
    )
    by_name = {
        "daily": _hard_status_day_frame(daily_frame, name="daily", trade_key=trade_key),
        "stk_limit": _hard_status_day_frame(limit_frame, name="stk_limit", trade_key=trade_key),
        "baostock_status": _hard_status_day_frame(
            status_frame, name="baostock_status", trade_key=trade_key
        ),
        "suspend_d": _hard_status_day_frame(
            suspension_frame, name="suspend_d", trade_key=trade_key
        ),
    }
    namechange_index = (
        _index_namechange_intervals(namechange_frame)
        if _namechange_index is None
        else _namechange_index
    )
    namechange_st = _namechange_st_by_code(
        namechange_index,
        trade_key,
        master["code"].astype(str).tolist(),
    )

    output: list[dict[str, object]] = []
    for master_row in master.sort_values("code", kind="stable").itertuples(index=False):
        code = str(master_row.code)
        list_key = str(master_row.list_date)
        delist_key = None if pd.isna(master_row.delist_date) else str(master_row.delist_date)
        listed = list_key <= trade_key and (delist_key is None or trade_key <= delist_key)
        delisting = delist_key is not None and trade_key >= delist_key

        daily_row = by_name["daily"].loc[code] if code in by_name["daily"].index else None
        limit_row = by_name["stk_limit"].loc[code] if code in by_name["stk_limit"].index else None
        status_row = (
            by_name["baostock_status"].loc[code]
            if code in by_name["baostock_status"].index
            else None
        )
        suspension_row = (
            by_name["suspend_d"].loc[code]
            if code in by_name["suspend_d"].index
            else None
        )
        full_day_suspension = _full_day_suspension(suspension_row)

        missing: list[str] = []
        if limit_row is None:
            missing.append("stk_limit")
        if status_row is None:
            missing.append("baostock_status")
        status_complete = not missing
        status_conflict = False
        conflicts: list[str] = []
        st = False
        suspended = full_day_suspension
        status_source_parts: list[str] = ["tushare_namechange"]
        name_st = namechange_st[code]
        if name_st is None:
            status_complete = False
            status_conflict = True
            conflicts.append("namechange_ambiguous")

        if status_row is not None:
            st_flag = _flag(status_row["is_st"])
            trading_flag = _flag(status_row["tradestatus"])
            source = str(status_row["st_source"] or "").strip()
            if st_flag is None or trading_flag is None or not source:
                status_complete = False
                missing.append("baostock_status_invalid")
            else:
                st = st_flag
                baostock_suspended = not trading_flag
                if baostock_suspended != full_day_suspension:
                    status_conflict = True
                    conflicts.append("baostock+suspend_d")
                if name_st is not None and st != name_st:
                    status_conflict = True
                    conflicts.append("baostock+namechange")
                suspended = baostock_suspended or full_day_suspension
                status_source_parts.append(source)

        limit_up = limit_down = open_price = math.nan
        if limit_row is not None:
            limit_up = float(pd.to_numeric(pd.Series([limit_row["up_limit"]]), errors="coerce").iloc[0])
            limit_down = float(pd.to_numeric(pd.Series([limit_row["down_limit"]]), errors="coerce").iloc[0])
            if (
                not math.isfinite(limit_up)
                or not math.isfinite(limit_down)
                or limit_up <= limit_down
                or limit_down <= 0
            ):
                status_complete = False
                missing.append("stk_limit_invalid")
        if daily_row is None:
            if not suspended:
                status_complete = False
                missing.append("daily")
        else:
            open_price = float(pd.to_numeric(pd.Series([daily_row["open"]]), errors="coerce").iloc[0])
            if not math.isfinite(open_price) or open_price <= 0:
                status_complete = False
                missing.append("daily_invalid")

        at_limit_up = bool(
            status_complete
            and (
                math.isclose(
                    open_price,
                    limit_up,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or open_price > limit_up
            )
        )
        at_limit_down = bool(
            status_complete
            and (
                math.isclose(
                    open_price,
                    limit_down,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                or open_price < limit_down
            )
        )
        fail_closed = not status_complete or status_conflict or suspended or not listed
        buy_executable = bool(
            not fail_closed and not st and not delisting and not at_limit_up
        )
        sell_executable = bool(not fail_closed and not at_limit_down)
        prohibit_new = not buy_executable
        if suspension_row is not None:
            status_source_parts.append("suspend_d")
        if limit_row is not None:
            status_source_parts.append("stk_limit")
        status_source_parts.extend(f"missing:{name}" for name in sorted(set(missing)))
        status_source_parts.extend(f"conflict:{name}" for name in conflicts)

        output.append(
            {
                "trade_date": trade_key,
                "code": code,
                "listed": listed,
                "st": st,
                "delisting": delisting,
                "suspended": suspended,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "at_limit_up": at_limit_up,
                "at_limit_down": at_limit_down,
                "status_complete": status_complete and not status_conflict,
                "status_conflict": status_conflict,
                "buy_executable": buy_executable,
                "sell_executable": sell_executable,
                "prohibit_new_position": prohibit_new,
                "status_source": ";".join(status_source_parts),
                "hard_status_version": HARD_STATUS_VERSION,
            }
        )
    result = pd.DataFrame(output, columns=DAILY_HARD_STATUS_COLUMNS)
    if result.duplicated(["trade_date", "code"], keep=False).any():
        raise ValueError("all_cap_hard_status_duplicate:output")
    return _hard_status_dtypes(result)


@dataclass(frozen=True)
class _VerifiedBacktestCache:
    cache_root: Path
    calendar: pd.DataFrame
    stock_master: pd.DataFrame
    daily_by_date: Mapping[str, Path]
    daily_basic_by_date: Mapping[str, Path]
    suspend_by_date: Mapping[str, Path]
    baostock_status_by_code: Mapping[str, Path | None]
    namechange: pd.DataFrame
    open_dates: tuple[str, ...]
    missing_baostock_status_codes: tuple[str, ...]
    missing_namechange_codes: tuple[str, ...]
    missing_adj_factor_codes: tuple[str, ...]
    missing_daily_by_date: Mapping[str, frozenset[str]]
    missing_daily_basic_by_date: Mapping[str, frozenset[str]]
    missing_adjustment_by_date: Mapping[str, frozenset[str]]
    coverage: Mapping[str, object]
    stock_master_sha256: str
    content_identity: Mapping[str, object]


def _cache_error(kind: str, relative: str) -> ValueError:
    return ValueError(f"all_cap_universe_insufficient_data:{kind}:{relative}")


def _read_cache_csv(
    path: Path,
    *,
    cache_root: Path,
    required: Sequence[str],
    identity_records: list[dict[str, object]] | None = None,
    raw_hashes: dict[str, str] | None = None,
) -> pd.DataFrame:
    try:
        relative = path.relative_to(cache_root).as_posix()
    except ValueError as exc:
        raise ValueError("all_cap_universe_cache_path") from exc
    universe_store.assert_cache_path(path, cache_root, must_exist=False)
    if path.is_symlink() or not path.is_file():
        raise _cache_error("missing", relative)
    try:
        payload = path.read_bytes()
        frame = pd.read_csv(
            io.BytesIO(payload),
            dtype=str,
            keep_default_na=False,
        )
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise _cache_error("invalid", relative) from exc
    if set(required).difference(frame.columns):
        raise _cache_error("schema", relative)
    if identity_records is not None:
        identity_records.append(
            universe_store.cache_csv_identity_record(relative, frame)
        )
    if raw_hashes is not None:
        raw_hashes[relative] = hashlib.sha256(payload).hexdigest()
    return frame


def _cache_date_frame(
    frame: pd.DataFrame,
    *,
    date_column: str,
    error: str,
) -> pd.DataFrame:
    normalized = _normalize_date_column(
        frame,
        date_column,
        error=f"all_cap_universe_cache_date:{error}",
    )
    if "ts_code" in normalized:
        normalized["ts_code"] = (
            normalized["ts_code"].astype("string[pyarrow]").str.strip()
        )
        if not normalized["ts_code"].str.fullmatch(
            r"[0-9]{6}\.(?:SH|SZ|BJ)"
        ).all():
            raise ValueError("all_cap_universe_cache_code")
    return normalized


def _partition_path(cache_root: Path, dataset: str, trade_key: str) -> Path:
    dashed = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
    return cache_root / dataset / f"{dashed}.csv"


_CACHE_PARTITION_COLUMNS = {
    "daily": ("ts_code", "trade_date", "open", "amount"),
    "daily_basic": ("ts_code", "trade_date", "total_mv", "circ_mv"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    "baostock_status": (
        "ts_code",
        "trade_date",
        "tradestatus",
        "is_st",
        "st_source",
    ),
}


def _load_cache_partition(
    value: Path | pd.DataFrame,
    dataset: str,
) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    path = Path(value)
    frame = _read_cache_csv(
        path,
        cache_root=path.parents[1],
        required=_CACHE_PARTITION_COLUMNS[dataset],
    )
    return _cache_date_frame(
        frame,
        date_column="trade_date",
        error=path.relative_to(path.parents[1]).as_posix(),
    )


def _verify_daily_partition_coverage(
    *,
    cache_root: Path,
    master: pd.DataFrame,
    open_dates: Sequence[str],
    daily_by_date: Mapping[str, Path],
    basic_by_date: Mapping[str, Path],
    suspend_by_date: Mapping[str, Path],
    baostock_suspended_by_date: Mapping[str, set[str]],
    minimum_daily_coverage: float,
    minimum_daily_basic_coverage: float,
) -> tuple[dict[str, object], dict[str, dict[str, frozenset[str]]]]:
    coverage: dict[str, object] = {}
    missing: dict[str, dict[str, frozenset[str]]] = {
        "daily": {},
        "daily_basic": {},
    }
    for dataset, partitions, minimum_coverage in (
        ("daily", daily_by_date, minimum_daily_coverage),
        ("daily_basic", basic_by_date, minimum_daily_basic_coverage),
    ):
        expected_rows = observed_rows = 0
        minimum = 1.0
        dataset_dates = sorted(partitions)
        for trade_key in dataset_dates:
            expected = {
                str(row.ts_code)
                for row in master.itertuples(index=False)
                if str(row.list_date) <= trade_key
                and (pd.isna(row.delist_date) or trade_key <= str(row.delist_date))
                and _board_for_code(str(row.ts_code)) is not None
            }
            confirmed_suspended: set[str] = set()
            if trade_key in suspend_by_date:
                suspension = _load_cache_partition(
                    suspend_by_date[trade_key],
                    "suspend_d",
                )
                confirmed_suspended = baostock_suspended_by_date.get(
                    trade_key, set()
                ).intersection(
                    str(row.ts_code)
                    for _, row in suspension.iterrows()
                    if _full_day_suspension(row)
                )
            required = expected.difference(confirmed_suspended)
            partition = _load_cache_partition(
                partitions[trade_key],
                dataset,
            )
            observed = set(partition["ts_code"].astype(str)).intersection(required)
            missing_codes = frozenset(required.difference(observed))
            missing[dataset][trade_key] = missing_codes
            daily_coverage = (
                1.0 if not required else len(observed) / len(required)
            )
            minimum = min(minimum, daily_coverage)
            expected_rows += len(required)
            observed_rows += len(observed)
            if daily_coverage + 1e-12 < minimum_coverage:
                raise _cache_error("coverage", f"{dataset}:{trade_key}")
        coverage[dataset] = {
            "threshold": minimum_coverage,
            "minimum": minimum,
            "expected_rows": expected_rows,
            "observed_rows": observed_rows,
            "missing_rows": expected_rows - observed_rows,
        }
    return coverage, missing


def _adjustment_coverage(
    *,
    master: pd.DataFrame,
    open_dates: Sequence[str],
    observed_by_date: Mapping[str, set[str]],
    minimum_coverage: float,
) -> tuple[dict[str, object], dict[str, frozenset[str]]]:
    expected_rows = observed_rows = 0
    minimum = 1.0
    missing: dict[str, frozenset[str]] = {}
    for trade_key in open_dates:
        expected = {
            str(row.ts_code)
            for row in master.itertuples(index=False)
            if str(row.list_date) <= trade_key
            and (pd.isna(row.delist_date) or trade_key <= str(row.delist_date))
            and _board_for_code(str(row.ts_code)) is not None
        }
        observed = expected.intersection(observed_by_date.get(trade_key, set()))
        missing[trade_key] = frozenset(expected.difference(observed))
        daily_coverage = (
            1.0 if not expected else len(observed) / len(expected)
        )
        minimum = min(minimum, daily_coverage)
        expected_rows += len(expected)
        observed_rows += len(observed)
        if daily_coverage + 1e-12 < minimum_coverage:
            raise _cache_error("coverage", f"adjustment:{trade_key}")
    return (
        {
            "threshold": minimum_coverage,
            "minimum": minimum,
            "expected_rows": expected_rows,
            "observed_rows": observed_rows,
            "missing_rows": expected_rows - observed_rows,
        },
        missing,
    )


def verify_shared_backtest_cache(
    repo_root: Path,
    development_start: date,
    development_end: date,
    *,
    minimum_daily_coverage: float = 0.99,
    minimum_daily_basic_coverage: float,
    minimum_adjustment_coverage: float = 0.98,
    liquidity_lookback_sessions: int = 252,
) -> _VerifiedBacktestCache:
    """Verify the existing full-market cache without provider fallbacks."""

    if (
        not isinstance(development_start, date)
        or not isinstance(development_end, date)
        or development_start > development_end
        or isinstance(minimum_daily_basic_coverage, bool)
        or not isinstance(minimum_daily_basic_coverage, (int, float))
        or not math.isfinite(float(minimum_daily_basic_coverage))
        or not 0.0 <= float(minimum_daily_basic_coverage) <= 1.0
        or isinstance(minimum_daily_coverage, bool)
        or not isinstance(minimum_daily_coverage, (int, float))
        or not 0.0 <= float(minimum_daily_coverage) <= 1.0
        or isinstance(minimum_adjustment_coverage, bool)
        or not isinstance(minimum_adjustment_coverage, (int, float))
        or not 0.0 <= float(minimum_adjustment_coverage) <= 1.0
        or isinstance(liquidity_lookback_sessions, bool)
        or not isinstance(liquidity_lookback_sessions, int)
        or liquidity_lookback_sessions <= 0
    ):
        raise ValueError("all_cap_universe_cache_window")
    root = Path(repo_root).absolute()
    cache_root = root / "data/shared/backtest_cache"
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise _cache_error("missing", "data/shared/backtest_cache")
    universe_store.assert_cache_root(cache_root, root)
    meta_path = cache_root / "_meta.json"
    if meta_path.is_symlink() or not meta_path.is_file():
        raise _cache_error("missing", "_meta.json")
    try:
        meta_payload = meta_path.read_bytes()
        meta = json.loads(meta_payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _cache_error("invalid", "_meta.json") from exc
    if (
        not isinstance(meta, Mapping)
        or meta.get("stock_basic_done") is not True
        or not isinstance(meta.get("stock_basic_statuses_done"), list)
        or not {"L", "D", "P"}.issubset(
            {str(value) for value in meta["stock_basic_statuses_done"]}
        )
    ):
        raise _cache_error("stock_master_meta", "_meta.json")
    identity_paths = ["_meta.json", "trade_cal.csv", "stock_basic.csv"]
    identity_records = [
        universe_store.cache_json_identity_record("_meta.json", meta)
    ]
    raw_hashes: dict[str, str] = {}

    calendar = _read_cache_csv(
        cache_root / "trade_cal.csv",
        cache_root=cache_root,
        required=("cal_date", "is_open"),
        identity_records=identity_records,
        raw_hashes=raw_hashes,
    )
    calendar = _cache_date_frame(
        calendar,
        date_column="cal_date",
        error="trade_cal.csv",
    )
    if calendar.duplicated("cal_date", keep=False).any():
        raise _cache_error("duplicate", "trade_cal.csv")
    open_mask = calendar["is_open"].astype(str).str.strip().isin(
        {"1", "True", "true"}
    )
    all_open_dates = tuple(
        sorted(calendar.loc[open_mask, "cal_date"].astype(str).tolist())
    )
    start_key = development_start.strftime("%Y%m%d")
    end_key = development_end.strftime("%Y%m%d")
    open_dates = tuple(
        value for value in all_open_dates if start_key <= value <= end_key
    )
    if not open_dates or open_dates[0] != start_key or open_dates[-1] != end_key:
        raise _cache_error("calendar", "trade_cal.csv")
    if not any(value > end_key for value in all_open_dates):
        raise _cache_error("calendar_next_open", "trade_cal.csv")
    first_review = _quarter_review_dates(calendar, open_dates)[0]
    first_review_window = [
        value
        for value in all_open_dates
        if value <= first_review
    ][-liquidity_lookback_sessions:]

    master = _read_cache_csv(
        cache_root / "stock_basic.csv",
        cache_root=cache_root,
        required=("ts_code", "list_date", "delist_date", "list_status"),
        identity_records=identity_records,
        raw_hashes=raw_hashes,
    )
    master["ts_code"] = master["ts_code"].astype("string[pyarrow]").str.strip()
    master = _cache_date_frame(
        master,
        date_column="list_date",
        error="stock_basic.csv",
    )
    master = _normalize_date_column(
        master,
        "delist_date",
        nullable=True,
        error="all_cap_universe_cache_date:stock_basic.csv",
    )
    if (
        master.empty
        or master["ts_code"].eq("").any()
        or master.duplicated("ts_code", keep=False).any()
        or not set(master["list_status"]).issubset({"L", "D", "P"})
    ):
        raise _cache_error("stock_master", "stock_basic.csv")
    if not master["ts_code"].str.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)").all():
        raise ValueError("all_cap_universe_cache_code")
    supported_codes = {
        str(value)
        for value in master["ts_code"]
        if _board_for_code(str(value)) is not None
    }
    liquidity_dates = set(open_dates)
    for row in master.itertuples(index=False):
        code = str(row.ts_code)
        if (
            code in supported_codes
            and str(row.list_date) <= first_review
            and (pd.isna(row.delist_date) or first_review <= str(row.delist_date))
        ):
            liquidity_dates.update(
                value
                for value in first_review_window
                if value >= str(row.list_date)
            )
    liquidity_dates = tuple(sorted(liquidity_dates))
    producer_codes = {
        code
        for code in supported_codes
        if code.endswith((".SH", ".SZ"))
    }
    completed_namechanges = meta.get("namechange_codes_done")
    if (
        not isinstance(completed_namechanges, list)
        or not producer_codes.issubset(
            {str(value) for value in completed_namechanges}
        )
    ):
        raise _cache_error("namechange_meta", "_meta.json")
    completed_namechange_codes = {
        str(value) for value in completed_namechanges
    }
    for field in ("daily_dates_done", "daily_basic_dates_done"):
        completed = meta.get(field)
        if not isinstance(completed, list):
            raise _cache_error(f"{field}_meta", "_meta.json")

    daily_by_date: dict[str, Path] = {}
    basic_by_date: dict[str, Path] = {}
    suspend_by_date: dict[str, Path] = {}
    partition_specs = {
        dataset: columns
        for dataset, columns in _CACHE_PARTITION_COLUMNS.items()
        if dataset != "baostock_status"
    }
    targets = {
        "daily": daily_by_date,
        "daily_basic": basic_by_date,
        "suspend_d": suspend_by_date,
    }
    for trade_key in liquidity_dates:
        for dataset, required in partition_specs.items():
            if dataset != "daily" and trade_key < start_key:
                continue
            path = _partition_path(cache_root, dataset, trade_key)
            identity_paths.append(path.relative_to(cache_root).as_posix())
            frame = _read_cache_csv(
                path,
                cache_root=cache_root,
                required=required,
                identity_records=identity_records,
                raw_hashes=raw_hashes,
            )
            frame = _cache_date_frame(
                frame,
                date_column="trade_date",
                error=path.relative_to(cache_root).as_posix(),
            )
            if (
                (not frame.empty and set(frame["trade_date"].astype(str)) != {trade_key})
                or frame.duplicated(["trade_date", "ts_code"], keep=False).any()
            ):
                raise _cache_error("partition", path.relative_to(cache_root).as_posix())
            targets[dataset][trade_key] = path

    status_by_code: dict[str, Path | None] = {}
    baostock_suspended_by_date: dict[str, set[str]] = {}
    namechange_frames: list[pd.DataFrame] = []
    missing_baostock_status_codes: list[str] = []
    missing_namechange_codes: list[str] = []
    missing_adj_factor_codes: list[str] = []
    adjustment_observed_by_date: dict[str, set[str]] = {}
    for row in master.itertuples(index=False):
        code = str(row.ts_code)
        supported = code in supported_codes
        status_path = cache_root / "baostock_status" / f"{code}.csv"
        if supported:
            identity_paths.append(status_path.relative_to(cache_root).as_posix())
        if not supported or (
            not status_path.exists() and not status_path.is_symlink()
        ):
            if supported:
                missing_baostock_status_codes.append(code)
                identity_records.append(
                    universe_store.missing_cache_identity_record(
                        status_path.relative_to(cache_root).as_posix()
                    )
                )
            status = pd.DataFrame(
                columns=("ts_code", "trade_date", "tradestatus", "is_st", "st_source")
            )
            status["ts_code"] = status["ts_code"].astype("string[pyarrow]")
            status["trade_date"] = status["trade_date"].astype("string[pyarrow]")
            status_by_code[code] = None
        else:
            status = _read_cache_csv(
                status_path,
                cache_root=cache_root,
                required=("ts_code", "trade_date", "tradestatus", "is_st", "st_source"),
                identity_records=identity_records,
                raw_hashes=raw_hashes,
            )
            status = _cache_date_frame(
                status,
                date_column="trade_date",
                error=status_path.relative_to(cache_root).as_posix(),
            )
            status_by_code[code] = status_path
        adjustment_path = cache_root / "adj_factor" / f"{code}.csv"
        if supported:
            identity_paths.append(adjustment_path.relative_to(cache_root).as_posix())
        if not supported:
            adjustment = pd.DataFrame(
                columns=("ts_code", "trade_date", "adj_factor")
            )
        elif not adjustment_path.exists() and not adjustment_path.is_symlink():
            missing_adj_factor_codes.append(code)
            identity_records.append(
                universe_store.missing_cache_identity_record(
                    adjustment_path.relative_to(cache_root).as_posix()
                )
            )
            adjustment = pd.DataFrame(
                columns=("ts_code", "trade_date", "adj_factor")
            )
        else:
            adjustment = _read_cache_csv(
                adjustment_path,
                cache_root=cache_root,
                required=("ts_code", "trade_date", "adj_factor"),
                identity_records=identity_records,
                raw_hashes=raw_hashes,
            )
            adjustment = _cache_date_frame(
                adjustment,
                date_column="trade_date",
                error=adjustment_path.relative_to(cache_root).as_posix(),
            )
        verified_partitions: list[tuple[str, pd.DataFrame, Path]] = []
        if supported and code not in missing_baostock_status_codes:
            verified_partitions.append(("baostock_status", status, status_path))
        for dataset, frame, path in verified_partitions:
            if (
                not frame.empty
                and set(frame["ts_code"].astype(str)) != {code}
                or frame.duplicated(["trade_date", "ts_code"], keep=False).any()
            ):
                raise _cache_error("partition", path.relative_to(cache_root).as_posix())
            lifecycle_dates = {
                value
                for value in open_dates
                if str(row.list_date) <= value
                and (pd.isna(row.delist_date) or value <= str(row.delist_date))
            }
            observed = set(frame["trade_date"].astype(str)).intersection(open_dates)
            if observed != lifecycle_dates:
                raise _cache_error("partial", path.relative_to(cache_root).as_posix())
        if supported and code not in missing_adj_factor_codes:
            if (
                not adjustment.empty
                and set(adjustment["ts_code"].astype(str)) != {code}
                or adjustment.duplicated(
                    ["trade_date", "ts_code"],
                    keep=False,
                ).any()
            ):
                raise _cache_error(
                    "partition",
                    adjustment_path.relative_to(cache_root).as_posix(),
                )
            valid_adjustment = pd.to_numeric(
                adjustment["adj_factor"],
                errors="coerce",
            )
            valid_rows = adjustment.loc[
                valid_adjustment.gt(0.0)
                & valid_adjustment.map(
                    lambda value: math.isfinite(float(value))
                    if not pd.isna(value)
                    else False
                )
            ]
            for trade_key in valid_rows["trade_date"].astype(str):
                if trade_key in open_dates:
                    adjustment_observed_by_date.setdefault(
                        trade_key,
                        set(),
                    ).add(code)
        for status_row in status.itertuples(index=False):
            if (
                str(status_row.trade_date) in open_dates
                and _flag(status_row.tradestatus) is False
            ):
                baostock_suspended_by_date.setdefault(
                    str(status_row.trade_date),
                    set(),
                ).add(code)
        if supported:
            namechange_path = cache_root / "namechange" / f"{code}.csv"
            identity_paths.append(namechange_path.relative_to(cache_root).as_posix())
            namechange_complete = (
                code in completed_namechange_codes
                and (namechange_path.exists() or namechange_path.is_symlink())
            )
            if not namechange_complete and code not in producer_codes:
                missing_namechange_codes.append(code)
                identity_records.append(
                    universe_store.missing_cache_identity_record(
                        namechange_path.relative_to(cache_root).as_posix()
                    )
                )
                continue
            names = _read_cache_csv(
                namechange_path,
                cache_root=cache_root,
                required=(
                    "ts_code",
                    "name",
                    "start_date",
                    "end_date",
                    "ann_date",
                    "change_reason",
                ),
                identity_records=identity_records,
                raw_hashes=raw_hashes,
            )
            names = _cache_date_frame(
                names,
                date_column="start_date",
                error=namechange_path.relative_to(cache_root).as_posix(),
            )
            for column in ("end_date", "ann_date"):
                names = _normalize_date_column(
                    names,
                    column,
                    nullable=True,
                    error=f"all_cap_universe_cache_date:{namechange_path.relative_to(cache_root).as_posix()}",
                )
            if (
                (not names.empty and set(names["ts_code"].astype(str)) != {code})
                or names.duplicated(
                    ["ts_code", "name", "start_date", "end_date", "ann_date"],
                    keep=False,
                ).any()
                or (names["end_date"].notna() & names["start_date"].gt(names["end_date"])).any()
            ):
                raise _cache_error("partition", namechange_path.relative_to(cache_root).as_posix())
            namechange_frames.append(names)

    declared_dates = {
        "daily_dates_done": liquidity_dates,
        "daily_basic_dates_done": open_dates,
    }
    for field, required_dates in declared_dates.items():
        declared_open_iso = {
            f"{value[:4]}-{value[4:6]}-{value[6:]}"
            for value in required_dates
        }
        if not declared_open_iso.issubset({str(value) for value in meta[field]}):
            raise _cache_error(f"{field}_meta", "_meta.json")
    coverage, missing_by_dataset = _verify_daily_partition_coverage(
        cache_root=cache_root,
        master=master,
        open_dates=open_dates,
        daily_by_date=daily_by_date,
        basic_by_date=basic_by_date,
        suspend_by_date=suspend_by_date,
        baostock_suspended_by_date=baostock_suspended_by_date,
        minimum_daily_coverage=float(minimum_daily_coverage),
        minimum_daily_basic_coverage=float(minimum_daily_basic_coverage),
    )
    adjustment_coverage, missing_adjustment_by_date = _adjustment_coverage(
        master=master,
        open_dates=open_dates,
        observed_by_date=adjustment_observed_by_date,
        minimum_coverage=float(minimum_adjustment_coverage),
    )
    coverage["adjustment"] = adjustment_coverage

    if {str(record["path"]) for record in identity_records} != set(identity_paths):
        raise ValueError("all_cap_universe_cache_identity_manifest")
    return _VerifiedBacktestCache(
        cache_root=cache_root,
        calendar=calendar,
        stock_master=master,
        daily_by_date=daily_by_date,
        daily_basic_by_date=basic_by_date,
        suspend_by_date=suspend_by_date,
        baostock_status_by_code=status_by_code,
        namechange=(
            pd.concat(namechange_frames, ignore_index=True)
            if namechange_frames
            else pd.DataFrame(columns=("ts_code", "name", "start_date", "end_date", "ann_date"))
        ),
        open_dates=open_dates,
        missing_baostock_status_codes=tuple(sorted(missing_baostock_status_codes)),
        missing_namechange_codes=tuple(sorted(missing_namechange_codes)),
        missing_adj_factor_codes=tuple(sorted(missing_adj_factor_codes)),
        missing_daily_by_date=missing_by_dataset["daily"],
        missing_daily_basic_by_date=missing_by_dataset["daily_basic"],
        missing_adjustment_by_date=missing_adjustment_by_date,
        coverage=coverage,
        stock_master_sha256=raw_hashes["stock_basic.csv"],
        content_identity=universe_store.build_cache_identity_from_records(
            identity_records,
        ),
    )


def _quarter_review_dates(
    calendar: pd.DataFrame,
    open_dates: Sequence[str],
) -> list[str]:
    all_open = sorted(
        calendar.loc[
            calendar["is_open"].astype(str).str.strip().isin({"1", "True", "true"}),
            "cal_date",
        ].astype(str)
    )
    next_by_date = {
        current: following for current, following in zip(all_open, all_open[1:])
    }

    def quarter(value: str) -> tuple[str, int]:
        return value[:4], (int(value[4:6]) - 1) // 3 + 1

    reviews = [
        value
        for value in open_dates
        if value in next_by_date and quarter(value) != quarter(next_by_date[value])
    ]
    if not reviews:
        raise ValueError("all_cap_universe_insufficient_data:quarter_reviews")
    return reviews


def _load_source_limits(
    sources: object,
    years: Sequence[str],
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    loader = getattr(sources, "load_stk_limit_year", None)
    if not callable(loader):
        raise ValueError("all_cap_universe_insufficient_data:sources:stk_limit")
    for year in sorted(set(years)):
        try:
            frame = loader(year)
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"all_cap_universe_insufficient_data:sources:stk_limit:{year}"
            ) from exc
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("all_cap_universe_insufficient_data:sources:stk_limit")
        normalized = _input_frame(
            {"stk_limit": frame},
            "stk_limit",
            required=("code", "trade_date", "up_limit", "down_limit"),
        )
        normalized = _normalize_date_column(
            normalized,
            "trade_date",
            error="all_cap_universe_source_limit_date",
        )
        if (
            not normalized["trade_date"].str.startswith(year).all()
            or normalized.duplicated(["trade_date", "code"], keep=False).any()
        ):
            raise ValueError("all_cap_universe_insufficient_data:sources:stk_limit")
        result[year] = normalized
    return result


def _verify_source_identity(
    *,
    repo_root: Path,
    metadata: Mapping[str, object],
    cache: _VerifiedBacktestCache,
    contract: AllCapContract,
) -> str:
    source_hash = str(metadata.get("manifest_sha256") or "")
    master_hash = str(metadata.get("stock_master_sha256") or "")
    start_key = str(metadata.get("start_date") or "")
    end_key = str(metadata.get("end_date") or "")
    expected_master_hash = cache.stock_master_sha256
    declared_open = metadata.get("open_trade_dates")
    if (
        re.fullmatch(r"[a-f0-9]{64}", source_hash) is None
        or master_hash != expected_master_hash
        or start_key > contract.development_start.strftime("%Y%m%d")
        or end_key < contract.development_end.strftime("%Y%m%d")
        or not isinstance(declared_open, (list, tuple))
    ):
        raise ValueError("all_cap_universe_insufficient_data:sources:identity")
    try:
        normalized_open = [
            _date_key(value, error="all_cap_universe_source_calendar")
            for value in declared_open
        ]
    except ValueError as exc:
        raise ValueError(
            "all_cap_universe_insufficient_data:sources:calendar"
        ) from exc
    if normalized_open != sorted(set(normalized_open)):
        raise ValueError("all_cap_universe_insufficient_data:sources:calendar")
    development_open = tuple(
        value
        for value in normalized_open
        if contract.development_start.strftime("%Y%m%d")
        <= value
        <= contract.development_end.strftime("%Y%m%d")
    )
    if development_open != cache.open_dates:
        raise ValueError("all_cap_universe_insufficient_data:sources:calendar")
    return source_hash


def _verify_source_limit_coverage(
    cache: _VerifiedBacktestCache,
    limits: Mapping[str, pd.DataFrame],
) -> None:
    master = cache.stock_master
    for trade_key in cache.open_dates:
        expected = {
            str(row.ts_code)
            for row in master.itertuples(index=False)
            if str(row.list_date) <= trade_key
            and (pd.isna(row.delist_date) or trade_key <= str(row.delist_date))
            and _board_for_code(str(row.ts_code)) is not None
        }
        observed = set(
            limits[trade_key[:4]]
            .loc[limits[trade_key[:4]]["trade_date"].eq(trade_key), "code"]
            .astype(str)
        )
        if not expected.issubset(observed):
            raise ValueError(
                f"all_cap_universe_insufficient_data:sources:stk_limit:{trade_key}"
            )


def _hard_status_for_cache_year(
    cache: _VerifiedBacktestCache,
    limits: Mapping[str, pd.DataFrame],
    year: str,
    *,
    namechange_index: Mapping[
        str, tuple[tuple[str, object, object, object], ...]
    ],
) -> pd.DataFrame:
    trade_dates = [
        trade_key for trade_key in cache.open_dates if trade_key.startswith(year)
    ]
    if not trade_dates:
        raise ValueError("all_cap_universe_partition")
    status_frames: list[pd.DataFrame] = []
    for status_path in cache.baostock_status_by_code.values():
        if status_path is None:
            continue
        status = _load_cache_partition(status_path, "baostock_status")
        status = status.loc[status["trade_date"].str.startswith(year)].copy()
        if not status.empty:
            status_frames.append(status)
    status_year = (
        pd.concat(status_frames, ignore_index=True)
        if status_frames
        else pd.DataFrame(columns=_CACHE_PARTITION_COLUMNS["baostock_status"])
    )
    status_by_date = {
        str(trade_key): frame.copy()
        for trade_key, frame in status_year.groupby("trade_date", sort=False)
    }
    limit_by_date = {
        str(trade_key): frame.copy()
        for trade_key, frame in limits[year].groupby("trade_date", sort=False)
    }
    empty_status = status_year.iloc[0:0].copy()
    frames: list[pd.DataFrame] = []
    for trade_key in trade_dates:
        frames.append(
            build_daily_hard_status(
                trade_date=trade_key,
                stock_master=cache.stock_master,
                daily=_load_cache_partition(
                    cache.daily_by_date[trade_key],
                    "daily",
                ),
                stk_limit=limit_by_date.get(
                    trade_key,
                    limits[year].iloc[0:0].copy(),
                ),
                baostock_status=status_by_date.get(trade_key, empty_status),
                namechange=cache.namechange,
                suspend_d=_load_cache_partition(
                    cache.suspend_by_date[trade_key],
                    "suspend_d",
                ),
                _namechange_index=namechange_index,
            )
        )
    result = pd.concat(frames, ignore_index=True)
    if result.duplicated(["trade_date", "code"], keep=False).any():
        raise ValueError("all_cap_universe_duplicate:daily_hard_status")
    return _hard_status_dtypes(result)


def _membership_from_cache(
    cache: _VerifiedBacktestCache,
    hard_status: pd.DataFrame,
    industry: pd.DataFrame,
    contract: AllCapContract,
) -> pd.DataFrame:
    reviews = _quarter_review_dates(cache.calendar, cache.open_dates)
    lookback = int(_contract_universe(contract)["liquidity_lookback_sessions"])
    calendar = _normalize_date_column(
        cache.calendar,
        "cal_date",
        error="all_cap_universe_calendar",
    )
    calendar_open = sorted(
        calendar.loc[
            calendar["is_open"].astype(str).str.strip().isin({"1", "True", "true"}),
            "cal_date",
        ].astype(str)
    )
    list_dates = {
        str(row.ts_code): str(row.list_date)
        for row in cache.stock_master.itertuples(index=False)
        if _board_for_code(str(row.ts_code)) is not None
    }
    previous = pd.DataFrame(
        columns=("review_date", "code", "eligible", "stable_sleeve")
    )
    outputs: list[pd.DataFrame] = []
    for review_key in reviews:
        review_sessions = [
            trade_key for trade_key in calendar_open if trade_key <= review_key
        ][-lookback:]
        if not review_sessions:
            raise ValueError(
                "all_cap_universe_insufficient_data:liquidity_warmup"
            )
        earliest_session = review_sessions[0]
        required_review_sessions: set[str] = set()
        for code, list_key in list_dates.items():
            if list_key > review_key:
                continue
            required_sessions = [
                trade_key
                for trade_key in review_sessions
                if trade_key >= list_key
            ]
            required_review_sessions.update(required_sessions)
            if (
                len(review_sessions) < lookback
                and list_key < earliest_session
            ) or any(
                trade_key not in cache.daily_by_date
                for trade_key in required_sessions
            ):
                raise ValueError(
                    "all_cap_universe_insufficient_data:"
                    f"liquidity_warmup:{review_key}:{code}"
                )
        daily = pd.concat(
            [
                _load_cache_partition(
                    cache.daily_by_date[trade_key],
                    "daily",
                )
                for trade_key in sorted(required_review_sessions)
            ],
            ignore_index=True,
        )
        basic_sessions = [
            trade_key
            for trade_key in cache.daily_basic_by_date
            if trade_key <= review_key
        ][-lookback:]
        if not basic_sessions:
            raise ValueError(
                "all_cap_universe_insufficient_data:daily_basic"
            )
        daily_basic = pd.concat(
            [
                _load_cache_partition(
                    cache.daily_basic_by_date[trade_key],
                    "daily_basic",
                )
                for trade_key in basic_sessions
            ],
            ignore_index=True,
        )
        review_status = hard_status.loc[
            hard_status["trade_date"].eq(review_key)
            & hard_status["status_complete"]
            & ~hard_status["status_conflict"]
        ].copy()
        status = pd.DataFrame(
            {
                "ts_code": review_status["code"],
                "trade_date": review_status["trade_date"],
                "source_date": review_status["trade_date"],
                "is_st": review_status["st"].astype("int64").astype(str),
                "tradestatus": (~review_status["suspended"]).astype("int64").astype(str),
                "is_delisting": review_status["delisting"].astype("int64").astype(str),
                "status_source": review_status["status_source"],
            }
        )
        forced_exclusions: dict[str, set[str]] = {}
        missing_daily = getattr(cache, "missing_daily_by_date", {})
        for trade_key in review_sessions:
            for code in missing_daily.get(trade_key, frozenset()):
                forced_exclusions.setdefault(code, set()).add(
                    "daily_missing"
                )
        for code in getattr(cache, "missing_daily_basic_by_date", {}).get(
            review_key,
            frozenset(),
        ):
            forced_exclusions.setdefault(code, set()).add(
                "daily_basic_missing"
            )
        for code in getattr(cache, "missing_adjustment_by_date", {}).get(
            review_key,
            frozenset(),
        ):
            forced_exclusions.setdefault(code, set()).add(
                "adjustment_missing"
            )
        membership = build_review_membership(
            {
                "trade_calendar": cache.calendar,
                "stock_master": cache.stock_master,
                "daily": daily,
                "daily_basic": daily_basic,
                "status": status,
                "industry_membership": industry,
                "previous_membership": previous,
                "forced_exclusions": forced_exclusions,
            },
            review_date=review_key,
            contract=contract,
        )
        outputs.append(membership)
        previous = pd.concat(
            [
                previous,
                membership.loc[
                    :, ["review_date", "code", "eligible", "stable_sleeve"]
                ],
            ],
            ignore_index=True,
        )
    result = pd.concat(outputs, ignore_index=True)
    if result.duplicated(["review_date", "code"], keep=False).any():
        raise ValueError("all_cap_universe_duplicate:membership")
    return _membership_dtypes(result)


def _minimum_free_fraction(contract: AllCapContract) -> float:
    try:
        storage = contract.raw["storage"]
        floor = float(storage["minimum_filesystem_free_fraction_after_publish"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("all_cap_universe_contract:storage") from exc
    if not math.isfinite(floor) or floor < 0.15 or floor > 1.0:
        raise ValueError("all_cap_universe_contract:storage")
    return floor


def _coverage_thresholds(contract: AllCapContract) -> dict[str, float]:
    try:
        gates = contract.raw["data_gates"]
        thresholds = {
            "membership": float(gates["critical_membership_coverage"]),
            "daily": float(gates["daily_bar_coverage"]),
            "daily_basic": float(gates["daily_basic_coverage"]),
            "adjustment": float(gates["adjustment_coverage"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("all_cap_universe_contract:data_gates") from exc
    frozen = {
        "membership": 1.0,
        "daily": 0.99,
        "daily_basic": 0.99,
        "adjustment": 0.98,
    }
    if any(
        not math.isfinite(value)
        or not math.isclose(value, frozen[name], abs_tol=1e-12)
        for name, value in thresholds.items()
    ):
        raise ValueError("all_cap_universe_contract:data_gates")
    return thresholds


def _projected_space_check(
    *,
    repo_root: Path,
    contract: AllCapContract,
    estimated_bytes: int,
) -> None:
    usage = shutil.disk_usage(repo_root)
    if usage.total <= 0:
        raise ValueError("all_cap_universe_free_space")
    projected_free = usage.free - estimated_bytes
    if projected_free / usage.total < _minimum_free_fraction(contract):
        raise ValueError("all_cap_universe_free_space")


def load_verified_all_cap_universe(
    repo_root: str | Path,
) -> universe_store.VerifiedAllCapUniverse:
    """Load the latest universe only after physical and semantic verification."""

    return universe_store.load_latest(repo_root)


def _result_for_publication(
    verified: universe_store.VerifiedAllCapUniverse,
    *,
    cache: _VerifiedBacktestCache,
    estimated_bytes: int,
    reused: bool,
) -> dict[str, object]:
    return {
        "status": "complete",
        "publication_id": str(verified.metadata["publication_id"]),
        "manifest": str(verified.publication_dir / "manifest.json"),
        "membership_rows": int(verified.metadata["row_counts"]["membership"]),
        "daily_hard_status_rows": int(
            verified.metadata["row_counts"]["daily_hard_status"]
        ),
        "estimated_bytes": estimated_bytes,
        "missing_baostock_status_codes": list(
            cache.missing_baostock_status_codes
        ),
        "missing_namechange_codes": list(cache.missing_namechange_codes),
        "missing_adj_factor_codes": list(cache.missing_adj_factor_codes),
        "coverage": cache.coverage,
        "reused": reused,
    }


def _contract_hash(contract: AllCapContract) -> str:
    return universe_store.canonical_hash(
        {
            "campaign_id": contract.campaign_id,
            "development_start": contract.development_start.isoformat(),
            "development_end": contract.development_end.isoformat(),
            "holdout_start": contract.holdout_start.isoformat(),
            "holdout_end": contract.holdout_end.isoformat(),
            "holdout_policy": contract.holdout_policy,
            "size_boundaries": list(contract.size_boundaries),
            "boundary_buffer_fraction": contract.boundary_buffer_fraction,
            "sleeves": [
                {
                    "name": sleeve.name,
                    "rank_min": sleeve.rank_min,
                    "rank_max": sleeve.rank_max,
                    "benchmark": sleeve.benchmark,
                    "capital_weight": sleeve.capital_weight,
                }
                for sleeve in contract.sleeves
            ],
            "raw": contract.raw,
        }
    )


def materialize_all_cap_universe(
    *,
    repo_root: Path,
    contract: AllCapContract,
) -> dict[str, object]:
    """Materialize quarterly membership and daily gates from verified local inputs."""

    root = Path(repo_root).absolute()
    if not isinstance(contract, AllCapContract):
        raise ValueError("all_cap_universe_contract")
    try:
        sources = load_verified_all_cap_sources(root)
    except ValueError as exc:
        raise ValueError(f"all_cap_universe_insufficient_data:sources:{exc}") from exc
    coverage_thresholds = _coverage_thresholds(contract)
    cache = verify_shared_backtest_cache(
        root,
        contract.development_start,
        contract.development_end,
        minimum_daily_coverage=coverage_thresholds["daily"],
        minimum_daily_basic_coverage=coverage_thresholds["daily_basic"],
        minimum_adjustment_coverage=coverage_thresholds["adjustment"],
        liquidity_lookback_sessions=int(
            _contract_universe(contract)["liquidity_lookback_sessions"]
        ),
    )
    source_metadata = getattr(sources, "metadata", None)
    industry = getattr(sources, "industry_membership", None)
    if not isinstance(source_metadata, Mapping) or not isinstance(industry, pd.DataFrame):
        raise ValueError("all_cap_universe_insufficient_data:sources")
    source_hash = _verify_source_identity(
        repo_root=root,
        metadata=source_metadata,
        cache=cache,
        contract=contract,
    )
    contract_hash = _contract_hash(contract)
    universe_root = universe_store.universe_root(root)
    latest_path = universe_root / "latest.json"
    _projected_space_check(
        repo_root=root,
        contract=contract,
        estimated_bytes=0,
    )
    if latest_path.exists() or latest_path.is_symlink():
        try:
            existing = load_verified_all_cap_universe(root)
        except ValueError as exc:
            if str(exc) not in _REBUILDABLE_PUBLICATION_ERRORS:
                raise
        else:
            metadata = existing.metadata
            if (
                metadata.get("campaign_id") == contract.campaign_id
                and metadata.get("start_date")
                == contract.development_start.strftime("%Y%m%d")
                and metadata.get("end_date")
                == contract.development_end.strftime("%Y%m%d")
                and metadata.get("source_manifest_sha256") == source_hash
                and metadata.get("contract_sha256") == contract_hash
                and isinstance(metadata.get("cache_identity"), Mapping)
                and metadata["cache_identity"].get("sha256")
                == cache.content_identity.get("sha256")
            ):
                return _result_for_publication(
                    existing,
                    cache=cache,
                    estimated_bytes=0,
                    reused=True,
                )

    limits = _load_source_limits(
        sources,
        [trade_key[:4] for trade_key in cache.open_dates],
    )
    _verify_source_limit_coverage(cache, limits)
    if latest_path.exists() or latest_path.is_symlink():
        try:
            load_verified_all_cap_universe(root)
        except ValueError as exc:
            if str(exc) not in _REBUILDABLE_PUBLICATION_ERRORS:
                raise
    reviews = _quarter_review_dates(cache.calendar, cache.open_dates)
    estimated_rows = len(cache.stock_master) * (len(cache.open_dates) + len(reviews))
    estimated_bytes = max(
        1,
        estimated_rows
        * (
            len(DAILY_HARD_STATUS_COLUMNS)
            + len(MEMBERSHIP_COLUMNS)
        )
        * 16,
    )
    _projected_space_check(
        repo_root=root,
        contract=contract,
        estimated_bytes=estimated_bytes,
    )

    publication_id = (
        f"{contract.development_start:%Y%m%d}_{contract.development_end:%Y%m%d}_"
        f"{uuid.uuid4().hex}"
    )
    publications = universe_store.publications_root(root)
    publications.mkdir(parents=True, exist_ok=True)
    staging = publications / f".all-cap-universe-{publication_id}"
    if staging.exists() or staging.is_symlink():
        raise ValueError("all_cap_universe_staging_path")
    staging.mkdir()
    installed = False
    try:
        partitions: dict[str, list[dict[str, object]]] = {
            dataset: [] for dataset in universe_store.DATASETS
        }
        review_keys = set(reviews)
        review_status_frames: list[pd.DataFrame] = []
        namechange_index = _index_namechange_intervals(cache.namechange)
        for year in sorted({trade_key[:4] for trade_key in cache.open_dates}):
            hard_status_year = _hard_status_for_cache_year(
                cache,
                limits,
                year,
                namechange_index=namechange_index,
            )
            partitions["daily_hard_status"].append(
                universe_store.write_partition(
                    staging,
                    "daily_hard_status",
                    year,
                    hard_status_year,
                )
            )
            review_status_frames.append(
                hard_status_year.loc[
                    hard_status_year["trade_date"].isin(review_keys)
                ].copy()
            )
        review_status = pd.concat(review_status_frames, ignore_index=True)
        membership = _membership_from_cache(
            cache,
            review_status,
            industry,
            contract,
        )
        membership_years = membership["review_date"].astype(str).str[:4]
        for year in sorted(membership_years.unique()):
            partition = membership.loc[
                membership_years.eq(year)
            ].reset_index(drop=True)
            partitions["membership"].append(
                universe_store.write_partition(
                    staging,
                    "membership",
                    year,
                    partition,
                )
            )
        estimated_bytes = sum(
            int(record["bytes"])
            for records in partitions.values()
            for record in records
        )
        universe_store.verify_cache_identity(
            cache.cache_root,
            cache.content_identity,
            start_date=contract.development_start.strftime("%Y%m%d"),
            end_date=contract.development_end.strftime("%Y%m%d"),
        )
        manifest: dict[str, object] = {
            "schema_version": universe_store.SCHEMA_VERSION,
            "contract_version": universe_store.CONTRACT_VERSION,
            "status": "complete",
            "publication_id": publication_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "campaign_id": contract.campaign_id,
            "start_date": contract.development_start.strftime("%Y%m%d"),
            "end_date": contract.development_end.strftime("%Y%m%d"),
            "source_manifest_sha256": source_hash,
            "contract_sha256": contract_hash,
            "cache_identity": cache.content_identity,
            "readiness": {
                "missing_baostock_status_codes": list(
                    cache.missing_baostock_status_codes
                ),
                "missing_namechange_codes": list(cache.missing_namechange_codes),
                "missing_adj_factor_codes": list(cache.missing_adj_factor_codes),
            },
            "coverage": cache.coverage,
            "cross_sections": universe_store.build_cross_section_contract(
                codes=tuple(cache.stock_master["ts_code"].astype(str)),
                membership_dates=tuple(reviews),
                daily_dates=cache.open_dates,
            ),
            "dataset_schemas": {
                dataset: universe_store.schema_contract(dataset)
                for dataset in universe_store.DATASETS
            },
            "row_counts": {
                dataset: sum(int(record["rows"]) for record in records)
                for dataset, records in partitions.items()
            },
            "partitions": partitions,
        }
        universe_store.write_manifest(staging, manifest)
        universe_store.verify_publication(staging)
        usage = shutil.disk_usage(root)
        if usage.total <= 0 or usage.free / usage.total < _minimum_free_fraction(contract):
            raise ValueError("all_cap_universe_free_space")
        destination = universe_store.install_publication(staging, publication_id)
        installed = True
        verified = universe_store.verify_publication(destination)
        universe_store.publish_latest_if_cache_unchanged(
            root,
            verified.metadata,
            cache_root=cache.cache_root,
            cache_identity=cache.content_identity,
            start_date=contract.development_start.strftime("%Y%m%d"),
            end_date=contract.development_end.strftime("%Y%m%d"),
        )
        return _result_for_publication(
            verified,
            cache=cache,
            estimated_bytes=estimated_bytes,
            reused=False,
        )
    finally:
        if not installed and staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
