"""PIT all-cap universe membership for isolated A-share research."""

from __future__ import annotations

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
    "avg_amount_252",
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

_MEMBERSHIP_TEXT_COLUMNS = (
    "review_date",
    "effective_date",
    "code",
    "exclusion_reasons",
    "raw_sleeve",
    "stable_sleeve",
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
) -> dict[str, tuple[float, int, bool, int]]:
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
    result: dict[str, tuple[float, int, bool, int]] = {}
    for code in codes:
        code_sessions = [
            trade_key for trade_key in sessions if trade_key >= list_dates[code]
        ]
        rows = normalized.loc[normalized["code"].eq(code)].set_index("trade_date")
        values = rows["amount_numeric"].reindex(code_sessions)
        invalid = bool(
            values.dropna().map(lambda value: not math.isfinite(float(value)) or float(value) < 0).any()
        )
        if invalid or values.notna().sum() == 0:
            result[code] = (
                math.nan,
                int(values.isna().sum()),
                False,
                len(code_sessions),
            )
            continue
        filled = values.fillna(0.0).astype("float64")
        result[code] = (
            float(filled.mean()),
            int(filled.le(0.0).sum()),
            True,
            len(code_sessions),
        )
    return result


def _latest_market_caps(
    daily_basic: pd.DataFrame,
    review_key: str,
    codes: Sequence[str],
) -> dict[str, tuple[float, float]]:
    current = _as_of_source_rows(daily_basic, review_key)
    current = _normalize_date_column(
        current,
        "trade_date",
        error="all_cap_universe_daily_basic_date",
    )
    current = current.loc[
        current["trade_date"].le(review_key) & current["code"].isin(codes)
    ].copy()
    result: dict[str, tuple[float, float]] = {}
    for code in codes:
        rows = current.loc[current["code"].eq(code)]
        if rows.empty:
            result[code] = (math.nan, math.nan)
            continue
        latest_date = rows["trade_date"].max()
        latest = rows.loc[rows["trade_date"].eq(latest_date)]
        pairs = latest.loc[:, ["total_mv", "circ_mv"]].drop_duplicates()
        if len(pairs) != 1:
            result[code] = (math.nan, math.nan)
            continue
        total = pd.to_numeric(pd.Series([pairs.iloc[0]["total_mv"]]), errors="coerce").iloc[0]
        circ = pd.to_numeric(pd.Series([pairs.iloc[0]["circ_mv"]]), errors="coerce").iloc[0]
        result[code] = (float(total), float(circ))
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
            result[code] = "industry_missing"
        elif len(rows) != 1:
            result[code] = "industry_ambiguous"
        else:
            row = rows.iloc[0]
            values = tuple(str(row[field] or "").strip() for field in fields[:3])
            if not all(values):
                result[code] = "industry_missing"
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
        reasons: set[str] = set()
        list_key = str(master_row.list_date)
        delist_key = None if pd.isna(master_row.delist_date) else str(master_row.delist_date)
        board = _board_for_code(code)
        lifecycle_status_age_eligible = True
        if list_key > review_key:
            reasons.add("not_listed")
            lifecycle_status_age_eligible = False
        if delist_key is not None and review_key > delist_key:
            reasons.add("delisted")
            lifecycle_status_age_eligible = False
        if board is None:
            reasons.add("unsupported_board")
            lifecycle_status_age_eligible = False
        elif list_key <= review_key:
            list_day = datetime.strptime(list_key, "%Y%m%d").date()
            if (review_day - list_day).days < age_by_board[board]:
                reasons.add("listing_age")
                lifecycle_status_age_eligible = False
        current_status = status_by_code[code]
        status_source: object = pd.NA
        if current_status is None:
            reasons.add("status_missing")
            lifecycle_status_age_eligible = False
        else:
            status_source = current_status["status_source"]
            if current_status["is_st"]:
                reasons.add("st")
                lifecycle_status_age_eligible = False
            if current_status["is_delisting"]:
                reasons.add("delisted")
                lifecycle_status_age_eligible = False
            if current_status["suspended"]:
                reasons.add("suspended")
                lifecycle_status_age_eligible = False

        (
            average_amount,
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
        if lifecycle_status_age_eligible and amount_valid:
            base_liquidity.append((code, average_amount))

        total_mv, circ_mv = caps_by_code[code]
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
            "avg_amount_252": average_amount,
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
    suspend_d: pd.DataFrame,
) -> pd.DataFrame:
    """Build execution gates without changing quarterly sleeve membership."""

    trade_key = _date_key(trade_date, error="all_cap_hard_status_date")
    inputs: Mapping[str, object] = {
        "stock_master": stock_master,
        "daily": daily,
        "stk_limit": stk_limit,
        "baostock_status": baostock_status,
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
        st = False
        suspended = full_day_suspension
        status_source_parts: list[str] = []

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
                status_conflict = baostock_suspended != full_day_suspension
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
        if status_conflict:
            status_source_parts.append("conflict:baostock+suspend_d")

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
    calendar: pd.DataFrame
    stock_master: pd.DataFrame
    daily_by_date: Mapping[str, pd.DataFrame]
    daily_basic_by_date: Mapping[str, pd.DataFrame]
    suspend_by_date: Mapping[str, pd.DataFrame]
    baostock_status: pd.DataFrame
    open_dates: tuple[str, ...]


def _cache_error(kind: str, relative: str) -> ValueError:
    return ValueError(f"all_cap_universe_insufficient_data:{kind}:{relative}")


def _read_cache_csv(
    path: Path,
    *,
    cache_root: Path,
    required: Sequence[str],
) -> pd.DataFrame:
    try:
        relative = path.relative_to(cache_root).as_posix()
    except ValueError as exc:
        raise ValueError("all_cap_universe_cache_path") from exc
    if path.is_symlink() or not path.is_file():
        raise _cache_error("missing", relative)
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise _cache_error("invalid", relative) from exc
    if set(required).difference(frame.columns):
        raise _cache_error("schema", relative)
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
        normalized["ts_code"] = normalized["ts_code"].astype("string[pyarrow]")
    return normalized


def _partition_path(cache_root: Path, dataset: str, trade_key: str) -> Path:
    dashed = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
    return cache_root / dataset / f"{dashed}.csv"


def verify_shared_backtest_cache(
    repo_root: Path,
    development_start: date,
    development_end: date,
) -> _VerifiedBacktestCache:
    """Verify the existing full-market cache without provider fallbacks."""

    if (
        not isinstance(development_start, date)
        or not isinstance(development_end, date)
        or development_start > development_end
    ):
        raise ValueError("all_cap_universe_cache_window")
    root = Path(repo_root).absolute()
    cache_root = root / "data/shared/backtest_cache"
    if cache_root.is_symlink() or not cache_root.is_dir():
        raise _cache_error("missing", "data/shared/backtest_cache")
    meta_path = cache_root / "_meta.json"
    if meta_path.is_symlink() or not meta_path.is_file():
        raise _cache_error("missing", "_meta.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
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

    calendar = _read_cache_csv(
        cache_root / "trade_cal.csv",
        cache_root=cache_root,
        required=("cal_date", "is_open"),
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

    master = _read_cache_csv(
        cache_root / "stock_basic.csv",
        cache_root=cache_root,
        required=("ts_code", "list_date", "delist_date", "list_status"),
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

    daily_by_date: dict[str, pd.DataFrame] = {}
    basic_by_date: dict[str, pd.DataFrame] = {}
    suspend_by_date: dict[str, pd.DataFrame] = {}
    partition_specs = {
        "daily": ("ts_code", "trade_date", "open", "amount"),
        "daily_basic": ("ts_code", "trade_date", "total_mv", "circ_mv"),
        "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    }
    targets = {
        "daily": daily_by_date,
        "daily_basic": basic_by_date,
        "suspend_d": suspend_by_date,
    }
    for trade_key in open_dates:
        for dataset, required in partition_specs.items():
            path = _partition_path(cache_root, dataset, trade_key)
            frame = _read_cache_csv(
                path,
                cache_root=cache_root,
                required=required,
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
            targets[dataset][trade_key] = frame

    status_frames: list[pd.DataFrame] = []
    for row in master.itertuples(index=False):
        code = str(row.ts_code)
        status_path = cache_root / "baostock_status" / f"{code}.csv"
        status = _read_cache_csv(
            status_path,
            cache_root=cache_root,
            required=("ts_code", "trade_date", "tradestatus", "is_st", "st_source"),
        )
        status = _cache_date_frame(
            status,
            date_column="trade_date",
            error=status_path.relative_to(cache_root).as_posix(),
        )
        adjustment_path = cache_root / "adj_factor" / f"{code}.csv"
        adjustment = _read_cache_csv(
            adjustment_path,
            cache_root=cache_root,
            required=("ts_code", "trade_date", "adj_factor"),
        )
        adjustment = _cache_date_frame(
            adjustment,
            date_column="trade_date",
            error=adjustment_path.relative_to(cache_root).as_posix(),
        )
        for dataset, frame, path in (
            ("baostock_status", status, status_path),
            ("adj_factor", adjustment, adjustment_path),
        ):
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
        status_frames.append(status)

    return _VerifiedBacktestCache(
        calendar=calendar,
        stock_master=master,
        daily_by_date=daily_by_date,
        daily_basic_by_date=basic_by_date,
        suspend_by_date=suspend_by_date,
        baostock_status=pd.concat(status_frames, ignore_index=True),
        open_dates=open_dates,
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
    expected_master_hash = universe_store.sha256(
        repo_root / "data/shared/backtest_cache/stock_basic.csv"
    )
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


def _hard_status_for_cache(
    cache: _VerifiedBacktestCache,
    limits: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for trade_key in cache.open_dates:
        status = cache.baostock_status.loc[
            cache.baostock_status["trade_date"].eq(trade_key)
        ].copy()
        limit = limits[trade_key[:4]].loc[
            limits[trade_key[:4]]["trade_date"].eq(trade_key)
        ].copy()
        frames.append(
            build_daily_hard_status(
                trade_date=trade_key,
                stock_master=cache.stock_master,
                daily=cache.daily_by_date[trade_key],
                stk_limit=limit,
                baostock_status=status,
                suspend_d=cache.suspend_by_date[trade_key],
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
    daily = pd.concat(list(cache.daily_by_date.values()), ignore_index=True)
    daily_basic = pd.concat(list(cache.daily_basic_by_date.values()), ignore_index=True)
    previous = pd.DataFrame(
        columns=("review_date", "code", "eligible", "stable_sleeve")
    )
    outputs: list[pd.DataFrame] = []
    for review_key in reviews:
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
        membership = build_review_membership(
            {
                "trade_calendar": cache.calendar,
                "stock_master": cache.stock_master,
                "daily": daily,
                "daily_basic": daily_basic,
                "status": status,
                "industry_membership": industry,
                "previous_membership": previous,
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
    cache = verify_shared_backtest_cache(
        root,
        contract.development_start,
        contract.development_end,
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

    limits = _load_source_limits(
        sources,
        [trade_key[:4] for trade_key in cache.open_dates],
    )
    _verify_source_limit_coverage(cache, limits)
    hard_status = _hard_status_for_cache(cache, limits)
    membership = _membership_from_cache(
        cache,
        hard_status,
        industry,
        contract,
    )
    estimated_bytes = (
        universe_store.estimate_frame_bytes("membership", membership)
        + universe_store.estimate_frame_bytes("daily_hard_status", hard_status)
    )
    universe_root = universe_store.universe_root(root)
    latest_path = universe_root / "latest.json"
    if latest_path.exists() or latest_path.is_symlink():
        load_verified_all_cap_universe(root)
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
        for dataset, frame, date_column in (
            ("membership", membership, "review_date"),
            ("daily_hard_status", hard_status, "trade_date"),
        ):
            years = frame[date_column].astype(str).str[:4]
            for year in sorted(years.unique()):
                partition = frame.loc[years.eq(year)].reset_index(drop=True)
                partitions[dataset].append(
                    universe_store.write_partition(staging, dataset, year, partition)
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
        universe_store.write_latest(root, verified.metadata)
        return {
            "status": "complete",
            "publication_id": publication_id,
            "manifest": str(destination / "manifest.json"),
            "membership_rows": int(verified.metadata["row_counts"]["membership"]),
            "daily_hard_status_rows": int(
                verified.metadata["row_counts"]["daily_hard_status"]
            ),
            "estimated_bytes": estimated_bytes,
        }
    finally:
        if not installed and staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
