"""Point-in-time event factors for research and candidate models."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .store import IntelligenceStore


LEGACY_EVENT_FACTOR_COLUMNS = (
    "event_positive_decay_5d",
    "event_negative_decay_5d",
    "announcement_novelty_20d",
    "policy_industry_exposure_20d",
    "news_volume_abnormal_20d",
    "event_source_confirmation",
    "event_price_volume_confirmation",
    "event_data_coverage",
)

EVENT_SPECIFIC_FACTOR_COLUMNS = (
    "event_relevance_20d",
    "event_materiality_positive_20d",
    "event_materiality_negative_20d",
    "event_certainty_20d",
    "event_revision_risk_20d",
    "earnings_event_score_20d",
    "buyback_event_score_20d",
    "shareholder_flow_event_score_20d",
    "contract_event_score_60d",
    "corporate_action_event_score_60d",
    "legal_risk_event_score_60d",
    "delisting_risk_event_score_60d",
    "capital_structure_event_score_60d",
)

EVENT_DERIVED_FACTOR_COLUMNS = (
    "event_net_strength_5d",
    "event_net_materiality_20d",
)

EVENT_LITE_FACTOR_COLUMNS = (
    "event_net_strength_5d",
    "event_net_materiality_20d",
    "event_relevance_20d",
    "event_certainty_20d",
    "event_revision_risk_20d",
    "announcement_novelty_20d",
    "event_source_confirmation",
    "event_data_coverage",
)

EVENT_FACTOR_COLUMNS = (
    LEGACY_EVENT_FACTOR_COLUMNS
    + EVENT_SPECIFIC_FACTOR_COLUMNS
    + EVENT_DERIVED_FACTOR_COLUMNS
)

EVENT_SCORE_GROUPS = {
    "earnings_event_score_20d": (
        frozenset({"earnings_forecast", "earnings_flash"}),
        20,
    ),
    "buyback_event_score_20d": (frozenset({"buyback"}), 20),
    "shareholder_flow_event_score_20d": (
        frozenset({"shareholder_change"}),
        20,
    ),
    "contract_event_score_60d": (frozenset({"major_contract"}), 60),
    "corporate_action_event_score_60d": (
        frozenset({"dividend", "merger_restructuring", "capacity_project"}),
        60,
    ),
    "legal_risk_event_score_60d": (
        frozenset({
            "guarantee",
            "pledge_freeze",
            "litigation_arbitration",
            "investigation_penalty",
        }),
        60,
    ),
    "delisting_risk_event_score_60d": (
        frozenset({"risk_warning_delisting"}),
        60,
    ),
    "capital_structure_event_score_60d": (
        frozenset({"equity_financing", "control_change"}),
        60,
    ),
}


def attach_event_features(
    features: pd.DataFrame,
    intelligence_root: str | Path,
    *,
    market: str,
    as_of: str,
    availability_policy: str = "observed",
    copy: bool = True,
) -> pd.DataFrame:
    result = features.copy() if copy else features
    defaults: dict[str, np.ndarray] = {}
    for column in LEGACY_EVENT_FACTOR_COLUMNS:
        if column not in result.columns:
            defaults[column] = (
                np.zeros(len(result), dtype=np.float32)
                if column == "event_data_coverage"
                else np.full(len(result), np.nan, dtype=np.float32)
            )
    for column in EVENT_SPECIFIC_FACTOR_COLUMNS:
        if column not in result.columns:
            defaults[column] = np.zeros(len(result), dtype=np.float32)
    if defaults:
        result = pd.concat(
            [result, pd.DataFrame(defaults, index=result.index)],
            axis=1,
        )
    db_path = Path(intelligence_root) / "intelligence.sqlite3"
    if not db_path.exists() or result.empty:
        return _finalize_event_features(result)
    store = IntelligenceStore(intelligence_root)
    market_timezone = (
        "Asia/Shanghai" if market in {"a_share", "cn_qdii_etf"} else "UTC"
    )
    as_of_date = pd.to_datetime(
        str(as_of).replace("-", "")[:8],
        format="%Y%m%d",
        errors="raise",
    )
    cutoff = (
        as_of_date.tz_localize(market_timezone)
        + pd.Timedelta(hours=23, minutes=59, seconds=59)
    ).tz_convert("UTC").isoformat()
    result["_event_date"] = pd.to_datetime(
        result["trade_date"].astype("string").str.replace(
            "-",
            "",
            regex=False,
        ).str[:8],
        format="%Y%m%d",
        errors="coerce",
    ).dt.tz_localize(market_timezone)
    _assign_semantic_coverage(
        result,
        store,
        cutoff=cutoff,
        availability_policy=availability_policy,
        market_timezone=market_timezone,
    )
    events = store.events_as_of(
        cutoff,
        market=market,
        availability_policy=availability_policy,
    )
    if events.empty:
        return _finalize_event_features(result)
    with store.connect() as connection:
        scores = pd.read_sql_query(
            """
            SELECT event_id, relevance, materiality, certainty, direction,
                   confidence
            FROM event_scores
            """,
            connection,
        )
        lifecycles = pd.read_sql_query(
            """
            SELECT canonical_event_id AS event_id, lifecycle
            FROM event_candidates
            WHERE validation_status='canonical'
            """,
            connection,
        )
    if not scores.empty:
        events = events.merge(scores, on="event_id", how="left", suffixes=("", "_score"))
    else:
        for column in ("relevance", "materiality", "certainty", "direction_score", "confidence_score"):
            events[column] = np.nan
    if not lifecycles.empty:
        lifecycles = lifecycles.drop_duplicates("event_id", keep="last")
        events = events.merge(lifecycles, on="event_id", how="left")
    else:
        events["lifecycle"] = ""
    events["_event_date"] = (
        pd.to_datetime(events["available_at"], errors="coerce", utc=True)
        .dt.tz_convert(market_timezone)
        .dt.normalize()
    )
    events["entity_id"] = events["entity_id"].astype("string").str.split(".").str[0]
    security_events = events.loc[events["entity_type"].isin(["security", "etf"]) & events["entity_id"].notna()]
    security_codes = set(security_events["entity_id"].dropna().astype(str))
    for code, indices in result.groupby("code", sort=False).groups.items():
        normalized_code = str(code).split(".")[0]
        if normalized_code not in security_codes:
            continue
        group_events = security_events.loc[security_events["entity_id"].eq(normalized_code)]
        _assign_security_group(result, list(indices), group_events)
        _assign_event_specific_group(result, list(indices), group_events)
    _assign_policy_features(result, events)
    return _finalize_event_features(result)


def _finalize_event_features(result: pd.DataFrame) -> pd.DataFrame:
    positive = pd.to_numeric(
        result["event_positive_decay_5d"],
        errors="coerce",
    )
    negative = pd.to_numeric(
        result["event_negative_decay_5d"],
        errors="coerce",
    )
    positive_materiality = pd.to_numeric(
        result["event_materiality_positive_20d"],
        errors="coerce",
    )
    negative_materiality = pd.to_numeric(
        result["event_materiality_negative_20d"],
        errors="coerce",
    )
    derived = pd.DataFrame(
        {
            "event_net_strength_5d": (positive - negative).astype(np.float32),
            "event_net_materiality_20d": (
                positive_materiality - negative_materiality
            ).astype(np.float32),
        },
        index=result.index,
    )
    result = result.drop(
        columns=[column for column in derived.columns if column in result.columns],
        errors="ignore",
    )
    result = pd.concat([result, derived], axis=1)
    if "_event_date" in result.columns:
        result = result.drop(columns="_event_date")
    return result


def _assign_security_group(
    result: pd.DataFrame,
    indices: list[int],
    security_events: pd.DataFrame,
) -> None:
    row_dates = result.loc[indices, "_event_date"]
    valid_rows = row_dates.notna()
    group_events = security_events.loc[
        security_events["_event_date"].notna()
    ].copy()
    if not valid_rows.any() or group_events.empty:
        return
    target_indices = list(row_dates.index[valid_rows])
    row_days = _local_day_numbers(row_dates.loc[target_indices])
    event_days = _local_day_numbers(group_events["_event_date"])
    ages = row_days[:, None] - event_days[None, :]
    applicable = ages >= 0
    active_rows = applicable.any(axis=1)
    if not active_rows.any():
        return
    target_indices = [
        index
        for index, active in zip(target_indices, active_rows)
        if active
    ]
    ages = ages[active_rows]
    applicable = applicable[active_rows]
    decay5 = np.where(
        applicable,
        np.exp(-math.log(2.0) * np.maximum(ages, 0) / 5.0),
        0.0,
    )
    recent20 = applicable & (ages <= 20)
    direction = _numeric_array(group_events["direction"])
    strength = _numeric_array(group_events["strength"])
    confidence = _numeric_array(group_events["confidence"])
    novelty = _numeric_array(group_events["novelty"])
    base = strength * confidence
    event_net = (decay5 * (direction * base)[None, :]).sum(axis=1)
    result.loc[target_indices, "event_positive_decay_5d"] = (
        decay5 * (np.clip(direction, 0.0, None) * base)[None, :]
    ).sum(axis=1).astype(np.float32)
    result.loc[target_indices, "event_negative_decay_5d"] = (
        decay5 * (-np.clip(direction, None, 0.0) * base)[None, :]
    ).sum(axis=1).astype(np.float32)
    result.loc[target_indices, "announcement_novelty_20d"] = (
        recent20 * (novelty * base)[None, :]
    ).sum(axis=1).astype(np.float32)
    count20 = recent20.sum(axis=1)
    result.loc[target_indices, "news_volume_abnormal_20d"] = np.log1p(
        count20
    ).astype(np.float32)
    source_ids, _ = pd.factorize(
        group_events["source"].fillna("").astype(str),
        sort=False,
    )
    unique_sources = np.zeros(len(target_indices), dtype=np.float32)
    for source_id in np.unique(source_ids):
        unique_sources += recent20[:, source_ids == source_id].any(
            axis=1
        )
    result.loc[target_indices, "event_source_confirmation"] = (
        unique_sources / np.maximum(1, count20)
    ).astype(np.float32)
    momentum = (
        pd.to_numeric(
            result.loc[target_indices, "momentum_20"],
            errors="coerce",
        ).fillna(0.0).to_numpy()
        if "momentum_20" in result.columns
        else np.zeros(len(target_indices))
    )
    volume_ratio = (
        pd.to_numeric(
            result.loc[target_indices, "volume_ratio_5_20"],
            errors="coerce",
        ).fillna(1.0).to_numpy()
        if "volume_ratio_5_20" in result.columns
        else np.ones(len(target_indices))
    )
    result.loc[target_indices, "event_price_volume_confirmation"] = (
        np.sign(event_net)
        * np.sign(momentum)
        * np.clip(volume_ratio, 0.0, 2.0)
    ).astype(np.float32)


def _assign_event_specific_group(
    result: pd.DataFrame,
    indices: list[int],
    security_events: pd.DataFrame,
) -> None:
    score_columns = {
        "relevance",
        "materiality",
        "certainty",
        "direction_score",
        "confidence_score",
    }
    if not score_columns.issubset(security_events.columns):
        return
    scored_events = security_events.loc[
        security_events["relevance"].notna()
        & security_events["certainty"].notna()
        & security_events["direction_score"].notna()
        & security_events["confidence_score"].notna()
    ].copy()
    if scored_events.empty:
        return
    row_dates = result.loc[indices, "_event_date"]
    valid_rows = row_dates.notna()
    scored_events = scored_events.loc[
        scored_events["_event_date"].notna()
    ]
    if not valid_rows.any() or scored_events.empty:
        return
    target_indices = list(row_dates.index[valid_rows])
    row_days = _local_day_numbers(row_dates.loc[target_indices])
    event_days = _local_day_numbers(scored_events["_event_date"])
    ages = row_days[:, None] - event_days[None, :]
    applicable = ages >= 0
    active_rows = applicable.any(axis=1)
    if not active_rows.any():
        return
    target_indices = [
        index
        for index, active in zip(target_indices, active_rows)
        if active
    ]
    ages = ages[active_rows]
    applicable = applicable[active_rows]
    recent20 = applicable & (ages <= 20)
    decay20 = np.where(
        applicable,
        np.exp(-math.log(2.0) * np.maximum(ages, 0) / 20.0),
        0.0,
    )
    relevance = _numeric_array(scored_events["relevance"])
    materiality = _numeric_array(scored_events["materiality"])
    certainty = _numeric_array(scored_events["certainty"])
    direction = _numeric_array(scored_events["direction_score"])
    confidence = _numeric_array(scored_events["confidence_score"])
    weighted20 = decay20 * confidence[None, :]
    result.loc[target_indices, "event_relevance_20d"] = (
        recent20 * weighted20 * relevance[None, :]
    ).sum(axis=1).astype(np.float32)
    result.loc[target_indices, "event_materiality_positive_20d"] = (
        recent20
        * weighted20
        * (np.clip(direction, 0.0, None) * materiality)[None, :]
    ).sum(axis=1).astype(np.float32)
    result.loc[target_indices, "event_materiality_negative_20d"] = (
        recent20
        * weighted20
        * (-np.clip(direction, None, 0.0) * materiality)[None, :]
    ).sum(axis=1).astype(np.float32)
    result.loc[target_indices, "event_certainty_20d"] = (
        recent20
        * weighted20
        * (certainty * relevance)[None, :]
    ).sum(axis=1).astype(np.float32)
    revision_weight = (
        scored_events["lifecycle"]
        .fillna("")
        .astype(str)
        .map({"revised": 0.5, "uncertain": 0.75, "cancelled": 1.0})
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    result.loc[target_indices, "event_revision_risk_20d"] = (
        recent20
        * weighted20
        * (revision_weight * relevance)[None, :]
    ).sum(axis=1).astype(np.float32)
    event_type = scored_events["event_type"].fillna("").astype(str).to_numpy()
    base_score = (
        direction * materiality * relevance * certainty * confidence
    )
    for column, (event_types, window) in EVENT_SCORE_GROUPS.items():
        in_window = (
            applicable
            & (ages <= window)
            & np.isin(event_type, tuple(event_types))[None, :]
        )
        decay = np.where(
            in_window,
            np.exp(
                -math.log(2.0)
                * np.maximum(ages, 0)
                / float(window)
            ),
            0.0,
        )
        result.loc[target_indices, column] = (
            decay * base_score[None, :]
        ).sum(axis=1).astype(np.float32)


def _numeric_array(values: pd.Series) -> np.ndarray:
    return (
        pd.to_numeric(values, errors="coerce")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )


def _local_day_numbers(values: pd.Series) -> np.ndarray:
    return (
        pd.DatetimeIndex(values)
        .tz_localize(None)
        .to_numpy(dtype="datetime64[D]")
        .astype(np.int64)
    )


def _assign_policy_features(result: pd.DataFrame, events: pd.DataFrame) -> None:
    policy = events.loc[
        events["event_type"].astype(str).str.startswith(("industry_", "monetary_"))
        & events["_event_date"].notna()
    ].copy()
    if policy.empty:
        return
    if "industry" not in result:
        result["industry"] = ""
    dates = pd.DatetimeIndex(result["_event_date"].dropna().unique()).sort_values()
    generic_scores: dict[pd.Timestamp, float] = {}
    industry_scores: dict[str, dict[pd.Timestamp, float]] = {}
    event_columns = ["_event_date", "direction", "strength", "confidence", "industry", "entity_id"]
    for event_date, direction, strength, confidence, industry_value, entity_id in policy[
        event_columns
    ].itertuples(index=False, name=None):
        applicable_dates = dates[(dates >= event_date) & (dates <= event_date + pd.Timedelta(days=60))]
        if applicable_dates.empty:
            continue
        base = float(direction or 0.0) * float(strength or 0.0) * float(confidence or 0.0)
        ages = (applicable_dates - event_date).days
        contributions = base * np.exp(-math.log(2.0) * ages / 20.0)
        industry = "" if pd.isna(industry_value) else str(industry_value)
        target = generic_scores if not industry or pd.isna(entity_id) else industry_scores.setdefault(industry, {})
        for trade_date, contribution in zip(applicable_dates, contributions):
            target[trade_date] = target.get(trade_date, 0.0) + float(contribution)
    if not generic_scores and not industry_scores:
        return
    row_dates = result["_event_date"]
    row_industries = result["industry"].fillna("").astype(str)
    generic = row_dates.map(generic_scores)
    values = generic.fillna(0.0).astype(np.float32)
    covered = generic.notna()
    for industry, scores in industry_scores.items():
        industry_mask = row_industries.eq(industry)
        mapped = row_dates.loc[industry_mask].map(scores)
        matched = mapped.notna()
        if matched.any():
            matched_index = mapped.index[matched]
            values.loc[matched_index] += mapped.loc[matched_index].astype(np.float32)
            covered.loc[matched_index] = True
    result.loc[covered, "policy_industry_exposure_20d"] = values.loc[covered]


def _assign_semantic_coverage(
    result: pd.DataFrame,
    store: IntelligenceStore,
    *,
    cutoff: str,
    availability_policy: str,
    market_timezone: str,
) -> None:
    availability_expression = "d.first_seen_at"
    if availability_policy == "research":
        availability_expression = """
            CASE
                WHEN d.published_at <= (
                       SELECT value FROM intelligence_settings
                       WHERE key='historical_cutoff'
                     )
                 AND a.availability_provenance IN (
                     'reconstructed_rec_time',
                     'reconstructed_next_open'
                 )
                 AND a.research_available_at >= d.published_at
                 AND a.research_available_at <= d.first_seen_at
                THEN a.research_available_at
                ELSE d.first_seen_at
            END
        """
    with store.connect() as connection:
        processed = pd.read_sql_query(
            f"""
            SELECT l.ts_code, {availability_expression} AS available_at
            FROM semantic_runs s
            JOIN documents d ON d.id=s.document_id
            JOIN document_security_links l ON l.document_id=d.id
            LEFT JOIN document_availability a ON a.document_id=d.id
            WHERE {availability_expression}<=?
              AND (
                s.status='no_event'
                OR (
                  s.status='succeeded'
                  AND EXISTS (
                    SELECT 1
                    FROM event_candidates c
                    WHERE c.run_id=s.run_id
                      AND c.validation_status='canonical'
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM event_candidates c
                    WHERE c.run_id=s.run_id
                      AND c.validation_status<>'canonical'
                  )
                )
              )
            """,
            connection,
            params=(cutoff,),
        )
    if processed.empty:
        return
    processed["code"] = (
        processed["ts_code"].astype("string").str.split(".").str[0]
    )
    processed["_processed_date"] = (
        pd.to_datetime(
            processed["available_at"],
            errors="coerce",
            utc=True,
        )
        .dt.tz_convert(market_timezone)
        .dt.normalize()
    )
    first_processed = (
        processed.dropna(subset=["code", "_processed_date"])
        .groupby("code", sort=False)["_processed_date"]
        .min()
        .to_dict()
    )
    normalized_codes = result["code"].astype("string").str.split(".").str[0]
    available_dates = normalized_codes.map(first_processed)
    covered = (
        pd.Series(available_dates, index=result.index).notna()
        & result["_event_date"].notna()
        & pd.Series(available_dates, index=result.index).le(
            result["_event_date"]
        )
    )
    result.loc[covered, "event_data_coverage"] = np.float32(1.0)
