"""Point-in-time membership for mainland-listed QDII research universes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

import pandas as pd


@dataclass(frozen=True)
class CatalogMembershipResult:
    frame: pd.DataFrame
    metadata: dict[str, Any]


_OBSERVATION_FIELDS = (
    "observation_date",
    "observed_at",
    "first_seen_at",
    "catalog_as_of",
)


def _day(value: Any) -> pd.Timestamp:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT
    if isinstance(value, date):
        return pd.Timestamp(value).normalize()
    text = str(value).strip()
    if not text:
        return pd.NaT
    compact = text.replace("-", "")[:8]
    if compact.isdigit() and len(compact) == 8:
        return pd.to_datetime(compact, format="%Y%m%d", errors="coerce")
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return pd.NaT
    return parsed.tz_convert(None).normalize()


def _records(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if rows is None:
        return []
    if isinstance(rows, pd.DataFrame):
        return [dict(row) for row in rows.to_dict(orient="records")]
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _normalize_rows(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    *,
    default_observation: Any = None,
) -> pd.DataFrame:
    normalized: list[dict[str, Any]] = []
    for order, raw in enumerate(_records(rows)):
        code = str(raw.get("code") or raw.get("ts_code") or "").strip().upper()
        if not code:
            continue
        row = dict(raw)
        row["code"] = code
        if not row.get("scope") and row.get("research_scope"):
            row["scope"] = row.get("research_scope")
        observation_value = None
        observation_source = ""
        for field in _OBSERVATION_FIELDS:
            observed_at = _day(row.get(field))
            if pd.notna(observed_at):
                observation_value = row.get(field)
                observation_source = field
                break
        if observation_value is None and pd.notna(_day(default_observation)):
            observation_value = default_observation
            observation_source = "payload_as_of"
        row["_list_date"] = _day(row.get("list_date"))
        row["_delist_date"] = _day(row.get("delist_date"))
        row["_observed_at"] = _day(observation_value)
        row["_observation_source"] = observation_source
        row["_row_order"] = order
        normalized.append(row)
    return pd.DataFrame(normalized)


def _latest_rows(frame: pd.DataFrame, target: pd.Timestamp) -> tuple[pd.DataFrame, set[str]]:
    observed = frame.loc[
        frame["_observed_at"].notna() & frame["_observed_at"].le(target)
    ].copy()
    if observed.empty:
        available = observed
    else:
        available = (
            observed.sort_values(["code", "_observed_at", "_row_order"])
            .groupby("code", as_index=False, sort=False)
            .tail(1)
        )
    available_codes = set(available["code"].astype(str))

    missing = frame.loc[~frame["code"].astype(str).isin(available_codes)].copy()
    fallback_rows: list[pd.Series] = []
    for _, group in missing.groupby("code", sort=False):
        future = group.loc[group["_observed_at"].notna()].sort_values(
            ["_observed_at", "_row_order"]
        )
        fallback_rows.append(
            future.iloc[0]
            if not future.empty
            else group.sort_values("_row_order").iloc[-1]
        )
    fallback = pd.DataFrame(fallback_rows)
    if available.empty:
        selected = fallback
    elif fallback.empty:
        selected = available
    else:
        selected = pd.concat([available, fallback], ignore_index=True, sort=False)
    return selected, available_codes


def _inside_listing_interval(frame: pd.DataFrame, target: pd.Timestamp) -> pd.Series:
    listed = frame["_list_date"].notna() & frame["_list_date"].le(target)
    not_delisted = frame["_delist_date"].isna() | frame["_delist_date"].gt(target)
    status = (
        frame.get("status", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.upper()
    )
    status_allowed = ~status.eq("P")
    status_allowed &= ~(status.eq("D") & frame["_delist_date"].isna())
    return listed & not_delisted & status_allowed


def _public_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["code"])
    public_columns = [column for column in frame.columns if not column.startswith("_")]
    return (
        frame[public_columns]
        .sort_values(["code"])
        .drop_duplicates("code", keep="last")
        .reset_index(drop=True)
    )


def build_membership_as_of(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    *,
    as_of: str | date | pd.Timestamp,
    default_observation: Any = None,
) -> CatalogMembershipResult:
    """Resolve membership using only observations available by ``as_of``.

    When no contemporaneous observation exists, the interval projection is
    retained only as a diagnostic. Its metadata is fail-closed so downstream
    research cannot mistake a current-catalog replay for an unbiased universe.
    """

    target = _day(as_of)
    if pd.isna(target):
        raise ValueError("invalid_universe_as_of")
    normalized = _normalize_rows(rows, default_observation=default_observation)
    if normalized.empty:
        metadata = {
            "quality": "unavailable",
            "universe_as_of": target.strftime("%Y-%m-%d"),
            "provenance": {
                "mode": "catalog_observations",
                "membership_contract": "[list_date, delist_date)",
                "fallback": None,
            },
            "survivorship_bias": True,
            "unbiased_universe": False,
            "quality_reasons": ["empty_catalog"],
        }
        return CatalogMembershipResult(pd.DataFrame(columns=["code"]), metadata)

    selected, available_codes = _latest_rows(normalized, target)
    membership = selected.loc[_inside_listing_interval(selected, target)].copy()
    diagnostic_codes = set(membership["code"].astype(str)) - available_codes
    has_available_observation = bool(
        normalized["_observed_at"].notna().mul(
            normalized["_observed_at"].le(target)
        ).any()
    )
    quality_reasons: list[str] = []
    if not has_available_observation:
        quality_reasons.append("no_catalog_observation_at_or_before_as_of")
    if diagnostic_codes:
        quality_reasons.append("membership_reconstructed_from_future_observation")
    selected_status = (
        selected.get("status", pd.Series("", index=selected.index))
        .fillna("")
        .astype(str)
        .str.upper()
    )
    if bool((selected_status.eq("D") & selected["_delist_date"].isna()).any()):
        quality_reasons.append("delisted_status_without_delist_date")
    if bool(selected["_list_date"].isna().any()):
        quality_reasons.append("missing_list_date")
    quality = "available" if not quality_reasons else "unavailable"
    explicit_observation = normalized["_observation_source"].isin(_OBSERVATION_FIELDS).any()
    provenance = {
        "mode": "catalog_observations" if explicit_observation else "catalog_snapshot",
        "membership_contract": "[list_date, delist_date)",
        "observation_fields": list(_OBSERVATION_FIELDS),
        "fallback": "post_hoc_interval_diagnostic" if quality == "unavailable" else None,
    }
    observed_cutoff = normalized.loc[
        normalized["_observed_at"].notna()
        & normalized["_observed_at"].le(target),
        "_observed_at",
    ]
    metadata = {
        "quality": quality,
        "universe_as_of": target.strftime("%Y-%m-%d"),
        "provenance": provenance,
        "survivorship_bias": quality != "available",
        "unbiased_universe": quality == "available",
        "observation_cutoff": (
            observed_cutoff.max().strftime("%Y-%m-%d")
            if not observed_cutoff.empty
            else None
        ),
        "quality_reasons": quality_reasons,
    }
    return CatalogMembershipResult(_public_frame(membership), metadata)


def build_membership_calendar(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    *,
    dates: Iterable[Any],
    default_observation: Any = None,
) -> CatalogMembershipResult:
    """Build membership rows for each requested date."""

    normalized_dates = sorted({_day(value) for value in dates if pd.notna(_day(value))})
    frames: list[pd.DataFrame] = []
    daily_metadata: list[dict[str, Any]] = []
    for target in normalized_dates:
        result = build_membership_as_of(
            rows,
            as_of=target,
            default_observation=default_observation,
        )
        day_frame = result.frame.copy()
        day_frame.insert(0, "universe_date", target.strftime("%Y-%m-%d"))
        frames.append(day_frame)
        daily_metadata.append(result.metadata)

    frame = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame(columns=["universe_date", "code"])
    )
    quality_reasons = sorted(
        {
            reason
            for metadata in daily_metadata
            for reason in metadata.get("quality_reasons", [])
        }
    )
    quality = (
        "available"
        if daily_metadata and all(item.get("quality") == "available" for item in daily_metadata)
        else "unavailable"
    )
    any_explicit = any(
        item.get("provenance", {}).get("mode") == "catalog_observations"
        for item in daily_metadata
    )
    metadata = {
        "quality": quality,
        "universe_as_of": (
            normalized_dates[-1].strftime("%Y-%m-%d") if normalized_dates else None
        ),
        "provenance": {
            "mode": "catalog_observations" if any_explicit else "catalog_snapshot",
            "membership_contract": "[list_date, delist_date)",
            "observation_fields": list(_OBSERVATION_FIELDS),
            "fallback": "post_hoc_interval_diagnostic" if quality == "unavailable" else None,
        },
        "survivorship_bias": quality != "available",
        "unbiased_universe": quality == "available",
        "quality_reasons": quality_reasons,
    }
    return CatalogMembershipResult(frame, metadata)


__all__ = [
    "CatalogMembershipResult",
    "build_membership_as_of",
    "build_membership_calendar",
]
