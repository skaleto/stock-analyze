"""Preregistered, model-free A-share earnings-announcement drift study."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..intelligence.store import IntelligenceStore
from .storage import ResearchStore
from .earnings_structured_backfill import load_structured_earnings_events


@dataclass(frozen=True)
class EarningsDriftContract:
    protocol_version: str
    market: str
    development_start: str
    development_end: str
    historical_diagnostic_start: str
    live_oos_start: str
    event_types: tuple[str, ...]
    horizons: tuple[int, ...]
    minimum_confidence: float
    minimum_strength: float
    maximum_entry_lag_calendar_days: int
    round_trip_cost: float
    stress_cost_multiple: float
    bootstrap_samples: int
    bootstrap_seed: int
    minimum_total_events: int
    minimum_positive_events: int
    minimum_unique_securities: int
    minimum_event_years: int
    minimum_scope_events: int
    minimum_positive_year_fraction: float
    minimum_bootstrap_probability: float
    maximum_year_contribution_share: float


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def load_contract(path: str | Path) -> EarningsDriftContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    contract = EarningsDriftContract(
        **{
            **payload,
            "event_types": tuple(payload["event_types"]),
            "horizons": tuple(int(value) for value in payload["horizons"]),
        }
    )
    if contract.protocol_version != "earnings-drift-preregistered-v1":
        raise ValueError("earnings_drift_protocol")
    if contract.market != "a_share":
        raise ValueError("earnings_drift_market")
    if not (
        contract.development_start <= contract.development_end
        < contract.historical_diagnostic_start
        < contract.live_oos_start
    ):
        raise ValueError("earnings_drift_windows")
    if contract.horizons != (5, 20, 60):
        raise ValueError("earnings_drift_horizons")
    if set(contract.event_types) != {"earnings_forecast", "earnings_flash"}:
        raise ValueError("earnings_drift_event_types")
    if contract.round_trip_cost < 0.0 or contract.stress_cost_multiple < 1.0:
        raise ValueError("earnings_drift_costs")
    return contract


def normalize_events(
    events: pd.DataFrame,
    contract: EarningsDriftContract,
) -> pd.DataFrame:
    required = {
        "event_id", "event_type", "direction", "strength", "confidence",
        "available_at", "entity_type", "entity_id",
    }
    if required.difference(events.columns):
        raise ValueError("earnings_drift_event_columns")
    frame = events.loc[
        events["event_type"].isin(contract.event_types)
        & events["entity_type"].eq("security")
        & events["entity_id"].notna()
    ].copy()
    if frame.empty:
        return frame
    frame["code"] = (
        frame["entity_id"].astype("string").str.split(".").str[0].str.zfill(6)
    )
    available = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    frame["available_date"] = (
        available.dt.tz_convert("Asia/Shanghai").dt.strftime("%Y%m%d")
    )
    frame = frame.loc[
        frame["available_date"].between(
            contract.development_start,
            contract.development_end,
        )
    ].copy()
    for column in ("direction", "strength", "confidence"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["signal_score"] = (
        frame["direction"] * frame["strength"] * frame["confidence"]
    )
    strong = (
        frame["strength"].ge(contract.minimum_strength)
        & frame["confidence"].ge(contract.minimum_confidence)
    )
    frame["signal_side"] = np.select(
        [strong & frame["direction"].gt(0), strong & frame["direction"].lt(0)],
        ["positive", "negative"],
        default="weak",
    )
    frame["event_year"] = frame["available_date"].str[:4]
    return (
        frame.sort_values(
            ["code", "available_date", "event_type", "signal_score"],
            kind="stable",
        )
        .drop_duplicates(["code", "available_date", "event_type"], keep="last")
        .reset_index(drop=True)
    )


def build_event_return_panel(
    events: pd.DataFrame,
    prices: pd.DataFrame,
    benchmarks: Mapping[str, pd.DataFrame],
    contract: EarningsDriftContract,
) -> pd.DataFrame:
    """Create next-open active returns without reading post-development rows."""

    normalized = normalize_events(events, contract)
    if normalized.empty or prices.empty:
        return pd.DataFrame()
    required = {"code", "trade_date", "account_id", "open", "close"}
    if required.difference(prices.columns):
        raise ValueError("earnings_drift_price_columns")
    working = prices.copy()
    working["code"] = (
        working["code"].astype("string").str.split(".").str[0].str.zfill(6)
    )
    working["trade_date"] = working["trade_date"].astype("string").map(_date_key)
    working = working.loc[
        working["trade_date"].between(
            contract.development_start,
            contract.development_end,
        )
    ].copy()
    open_column = "adjusted_open" if "adjusted_open" in working else "open"
    close_column = "adjusted_close" if "adjusted_close" in working else "close"
    working["return_open"] = pd.to_numeric(working[open_column], errors="coerce")
    working["return_close"] = pd.to_numeric(working[close_column], errors="coerce")

    benchmark_maps: dict[str, tuple[dict[str, float], dict[str, float]]] = {}
    for scope, raw in benchmarks.items():
        frame = raw.copy()
        frame["trade_date"] = frame["trade_date"].astype("string").map(_date_key)
        frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = (
            frame.dropna(subset=["trade_date", "open", "close"])
            .drop_duplicates("trade_date", keep="last")
        )
        benchmark_maps[str(scope)] = (
            dict(zip(frame["trade_date"].astype(str), frame["open"].astype(float))),
            dict(zip(frame["trade_date"].astype(str), frame["close"].astype(float))),
        )

    rows: list[dict[str, Any]] = []
    events_by_code = {
        str(code): group for code, group in normalized.groupby("code", sort=False)
    }
    for (scope, code), group in working.groupby(
        ["account_id", "code"], sort=False
    ):
        event_group = events_by_code.get(str(code))
        benchmark_pair = benchmark_maps.get(str(scope))
        if event_group is None or benchmark_pair is None:
            continue
        ordered = (
            group.sort_values("trade_date", kind="stable")
            .drop_duplicates("trade_date", keep="last")
            .reset_index(drop=True)
        )
        dates = ordered["trade_date"].astype(str).to_numpy()
        for _, event in event_group.iterrows():
            entry_index = int(
                np.searchsorted(dates, str(event["available_date"]), side="right")
            )
            if entry_index >= len(ordered):
                continue
            entry_date = str(dates[entry_index])
            lag = (
                pd.Timestamp(entry_date) - pd.Timestamp(str(event["available_date"]))
            ).days
            if lag > contract.maximum_entry_lag_calendar_days:
                continue
            entry_price = float(ordered.iloc[entry_index]["return_open"])
            benchmark_open, benchmark_close = benchmark_pair
            if entry_price <= 0.0 or entry_date not in benchmark_open:
                continue
            for horizon in contract.horizons:
                exit_index = entry_index + horizon - 1
                if exit_index >= len(ordered):
                    continue
                exit_date = str(dates[exit_index])
                exit_price = float(ordered.iloc[exit_index]["return_close"])
                if exit_price <= 0.0 or exit_date not in benchmark_close:
                    continue
                security_return = exit_price / entry_price - 1.0
                benchmark_return = (
                    benchmark_close[exit_date] / benchmark_open[entry_date] - 1.0
                )
                active_return = security_return - benchmark_return
                rows.append({
                    "event_id": str(event["event_id"]),
                    "event_type": str(event["event_type"]),
                    "code": str(code),
                    "account_scope": str(scope),
                    "available_date": str(event["available_date"]),
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "event_year": str(event["event_year"]),
                    "signal_side": str(event["signal_side"]),
                    "signal_score": float(event["signal_score"]),
                    "horizon": int(horizon),
                    "security_return": security_return,
                    "benchmark_return": benchmark_return,
                    "active_return": active_return,
                    "net_active_return": active_return - contract.round_trip_cost,
                    "stress_net_active_return": active_return
                    - contract.round_trip_cost * contract.stress_cost_multiple,
                })
    return pd.DataFrame(rows)


def _cluster_bootstrap_probability(
    frame: pd.DataFrame,
    *,
    samples: int,
    seed: int,
) -> float | None:
    years = sorted(frame["event_year"].dropna().astype(str).unique())
    if len(years) < 2 or frame.empty:
        return None
    groups = {
        year: pd.to_numeric(
            frame.loc[frame["event_year"].eq(year), "net_active_return"],
            errors="coerce",
        ).dropna().to_numpy(dtype=float)
        for year in years
    }
    rng = np.random.default_rng(seed)
    positive = 0
    for _ in range(max(1, int(samples))):
        selected = rng.choice(years, size=len(years), replace=True)
        values = np.concatenate([groups[str(year)] for year in selected])
        positive += int(len(values) > 0 and float(values.mean()) > 0.0)
    return positive / max(1, int(samples))


def evaluate_panel(
    panel: pd.DataFrame,
    contract: EarningsDriftContract,
) -> dict[str, Any]:
    mature = (
        panel.loc[panel["horizon"].eq(max(contract.horizons))].copy()
        if not panel.empty else panel
    )
    positives = mature.loc[mature.get("signal_side", pd.Series(dtype=str)).eq("positive")]
    years = sorted(
        mature.get("event_year", pd.Series(dtype=str)).dropna().astype(str).unique()
    )
    scope_observations = (
        positives.groupby("account_scope")["event_id"].nunique().astype(int).to_dict()
        if not positives.empty else {}
    )
    evidence = {
        "total_events": int(mature["event_id"].nunique()) if not mature.empty else 0,
        "positive_events": int(positives["event_id"].nunique()) if not positives.empty else 0,
        "unique_securities": int(mature["code"].nunique()) if not mature.empty else 0,
        "event_years": years,
        "scope_observations": scope_observations,
    }
    evidence_checks = {
        "total_events": evidence["total_events"] >= contract.minimum_total_events,
        "positive_events": evidence["positive_events"] >= contract.minimum_positive_events,
        "unique_securities": evidence["unique_securities"] >= contract.minimum_unique_securities,
        "event_years": len(years) >= contract.minimum_event_years,
        "scope_events": set(scope_observations) == {"hs300", "zz500"}
        and all(
            scope_observations[scope] >= contract.minimum_scope_events
            for scope in ("hs300", "zz500")
        ),
    }
    base = {
        "protocol_version": contract.protocol_version,
        "evidence": evidence,
        "evidence_checks": evidence_checks,
        "model_training_allowed": False,
        "formal_strategy_unchanged": True,
    }
    if not all(evidence_checks.values()):
        return {**base, "status": "insufficient_data", "horizons": []}

    results: list[dict[str, Any]] = []
    for horizon in contract.horizons:
        frame = panel.loc[
            panel["horizon"].eq(horizon)
            & panel["signal_side"].eq("positive")
        ].copy()
        year_means = frame.groupby("event_year")["net_active_return"].mean()
        positive_fraction = float(year_means.gt(0).mean()) if len(year_means) else 0.0
        contributions = (
            frame.groupby("event_year")["net_active_return"].sum().clip(lower=0.0)
        )
        total_positive = float(contributions.sum())
        concentration = (
            float(contributions.max() / total_positive)
            if total_positive > 0.0 else 1.0
        )
        probability = _cluster_bootstrap_probability(
            frame,
            samples=contract.bootstrap_samples,
            seed=contract.bootstrap_seed + int(horizon),
        )
        scope_means = (
            frame.groupby("account_scope")["net_active_return"].mean().to_dict()
        )
        checks = {
            "net_positive": float(frame["net_active_return"].mean()) > 0.0,
            "stress_positive": float(frame["stress_net_active_return"].mean()) > 0.0,
            "year_stability": (
                positive_fraction >= contract.minimum_positive_year_fraction
            ),
            "bootstrap": (
                probability is not None
                and probability >= contract.minimum_bootstrap_probability
            ),
            "year_concentration": (
                concentration <= contract.maximum_year_contribution_share
            ),
            "scope_consistency": bool(scope_means)
            and all(value > 0.0 for value in scope_means.values()),
        }
        results.append({
            "horizon": int(horizon),
            "observations": int(len(frame)),
            "mean_active_return": float(frame["active_return"].mean()),
            "mean_net_active_return": float(frame["net_active_return"].mean()),
            "mean_stress_net_active_return": float(
                frame["stress_net_active_return"].mean()
            ),
            "median_net_active_return": float(frame["net_active_return"].median()),
            "positive_year_fraction": positive_fraction,
            "bootstrap_probability": probability,
            "maximum_year_contribution_share": concentration,
            "scope_mean_net_active_return": scope_means,
            "checks": checks,
            "passed": all(checks.values()),
        })
    passed = all(row["passed"] for row in results)
    return {
        **base,
        "status": "transparent_baseline_candidate" if passed else "falsified",
        "horizons": results,
    }


def _load_benchmark(
    repo_root: Path,
    code: str,
    snapshot_date: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    canonical = (
        repo_root / "data" / "shared" / "backtest_cache"
        / "benchmark_daily" / f"{code}.csv"
    )
    observed = (
        repo_root / "data" / "research" / "raw" / "a_share"
        / snapshot_date / f"benchmark_{code}.parquet"
    )
    if canonical.exists():
        frames.append(
            pd.read_csv(canonical, dtype={"ts_code": str, "trade_date": str})
        )
    if observed.exists():
        frames.append(pd.read_parquet(observed))
    if not frames:
        raise FileNotFoundError(f"earnings_drift_benchmark_missing:{code}")
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined["trade_date"] = combined["trade_date"].astype("string").map(
        _date_key
    )
    return combined.drop_duplicates(["ts_code", "trade_date"], keep="last")


def _write_report(result: dict[str, Any], path: Path) -> None:
    evidence = result["evidence"]
    lines = [
        "# Earnings Drift Study",
        "",
        f"- Protocol: {result['protocol_version']}",
        f"- Status: {result['status']}",
        f"- Snapshot: {result.get('snapshot_date')}",
        "- Model training allowed: false",
        "- Formal strategy unchanged: true",
        "",
        "## Evidence",
        "",
        f"- Total events: {evidence['total_events']}",
        f"- Positive events: {evidence['positive_events']}",
        f"- Unique securities: {evidence['unique_securities']}",
        f"- Event years: {', '.join(evidence['event_years']) or 'none'}",
        "- Scope observations: "
        + json.dumps(evidence["scope_observations"], sort_keys=True),
        "",
        "## Horizon results",
        "",
    ]
    if not result["horizons"]:
        lines.append(
            "No economic result was produced because the preregistered "
            "evidence gate failed."
        )
    for row in result["horizons"]:
        lines.extend([
            f"### {row['horizon']} sessions",
            "",
            f"- Observations: {row['observations']}",
            f"- Mean net active return: {row['mean_net_active_return']:.2%}",
            f"- Stress-cost mean: {row['mean_stress_net_active_return']:.2%}",
            f"- Bootstrap probability: {row['bootstrap_probability']}",
            f"- Passed: {str(row['passed']).lower()}",
            "",
        ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_earnings_drift_study(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    contract_path: str | Path = "configs/research/earnings_drift_study.yaml",
    output_root: str | Path = "reports/research",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract_file = Path(contract_path)
    if not contract_file.is_absolute():
        contract_file = root / contract_file
    contract = load_contract(contract_file)
    snapshot_key = _date_key(snapshot_date)
    feature_path = ResearchStore(
        root / "data" / "research"
    ).feature_snapshot_path("a_share", snapshot_key)
    if not feature_path.exists():
        raise FileNotFoundError(
            f"earnings_drift_feature_snapshot_missing:{snapshot_key}"
        )
    feature_columns = [
        "code", "trade_date", "account_id", "open", "close",
        "adjusted_open", "adjusted_close",
    ]
    available_columns = set(pq.read_schema(feature_path).names)
    prices = pd.read_parquet(
        feature_path,
        columns=[
            column for column in feature_columns if column in available_columns
        ],
        filters=[
            ("trade_date", ">=", contract.development_start),
            ("trade_date", "<=", contract.development_end),
        ],
    )
    intelligence_root = root / "data" / "shared" / "intelligence"
    store = IntelligenceStore(intelligence_root)
    cutoff = (
        pd.Timestamp(contract.development_end)
        .tz_localize("Asia/Shanghai")
        .replace(hour=23, minute=59, second=59)
        .tz_convert("UTC")
        .isoformat()
    )
    events = store.events_as_of(
        cutoff,
        market="a_share",
        availability_policy="research",
    )
    structured_events, structured_audit = load_structured_earnings_events(
        root,
        start_date="2018-01-01",
        end_date="2024-12-31",
    )
    if not structured_events.empty:
        events = pd.concat([events, structured_events], ignore_index=True, sort=False)
    panel = build_event_return_panel(
        events,
        prices,
        {
            "hs300": _load_benchmark(root, "000300", snapshot_key),
            "zz500": _load_benchmark(root, "000905", snapshot_key),
        },
        contract,
    )
    result = evaluate_panel(panel, contract)
    if not structured_audit["complete"]:
        result["status"] = "insufficient_data"
        result["model_training_allowed"] = False
        result["horizons"] = []
        result["evidence_checks"]["structured_backfill_complete"] = False
    else:
        result["evidence_checks"]["structured_backfill_complete"] = True
    result.update({
        "snapshot_date": snapshot_key,
        "development_window": [
            contract.development_start,
            contract.development_end,
        ],
        "historical_diagnostic_opened": False,
        "live_oos_start": contract.live_oos_start,
        "panel_rows": int(len(panel)),
        "structured_backfill": structured_audit,
    })
    output = Path(output_root)
    if not output.is_absolute():
        output = root / output
    json_path = output / f"earnings_drift_{snapshot_key}.json"
    markdown_path = output / f"earnings_drift_{snapshot_key}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_report(result, markdown_path)
    return {
        **result,
        "report_json": str(json_path),
        "report_markdown": str(markdown_path),
    }
