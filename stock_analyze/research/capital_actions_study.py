"""Preregistered model-free A-share capital-actions event study."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .capital_actions_backfill import load_capital_action_events
from .earnings_drift_study import (
    _load_benchmark,
    build_lightweight_event_price_panel,
)


POSITIVE_FAMILIES = (
    "repurchase_completed",
    "holder_company_increase",
    "holder_management_increase",
    "holder_individual_increase",
)
DIAGNOSTIC_FAMILIES = (
    "repurchase_plan",
    "repurchase_stopped",
    "holder_company_decrease",
    "holder_management_decrease",
    "holder_individual_decrease",
)


@dataclass(frozen=True)
class CapitalActionsContract:
    protocol_version: str
    market: str
    development_start: str
    development_end: str
    historical_diagnostic_start: str
    live_oos_start: str
    horizons: tuple[int, ...]
    primary_horizon: int
    round_trip_cost: float
    stress_cost_multiple: float
    repurchase_market_cap_ratio: float
    holder_change_ratio: float
    minimum_total_events: int
    minimum_unique_securities: int
    minimum_event_years: int
    minimum_scope_events: int
    minimum_positive_year_fraction: float
    minimum_bootstrap_probability: float
    maximum_year_contribution_share: float
    bootstrap_samples: int
    bootstrap_seed: int
    positive_families: tuple[str, ...]
    diagnostic_families: tuple[str, ...]


_FROZEN_CONTRACT: dict[str, Any] = {
    "protocol_version": "capital-actions-preregistered-v1",
    "market": "a_share",
    "development_start": "20180101",
    "development_end": "20241231",
    "historical_diagnostic_start": "20250101",
    "live_oos_start": "20260818",
    "horizons": (5, 20, 60),
    "primary_horizon": 20,
    "round_trip_cost": 0.0021,
    "stress_cost_multiple": 1.5,
    "repurchase_market_cap_ratio": 0.005,
    "holder_change_ratio": 0.001,
    "minimum_total_events": 60,
    "minimum_unique_securities": 30,
    "minimum_event_years": 3,
    "minimum_scope_events": 15,
    "minimum_positive_year_fraction": 0.6666666667,
    "minimum_bootstrap_probability": 0.95,
    "maximum_year_contribution_share": 0.50,
    "bootstrap_samples": 5000,
    "bootstrap_seed": 20260818,
    "positive_families": POSITIVE_FAMILIES,
    "diagnostic_families": DIAGNOSTIC_FAMILIES,
}


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _iso_date(value: str) -> str:
    key = _date_key(value)
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def load_contract(path: str | Path) -> CapitalActionsContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    contract = CapitalActionsContract(
        **{
            **payload,
            "horizons": tuple(int(value) for value in payload["horizons"]),
            "positive_families": tuple(payload["positive_families"]),
            "diagnostic_families": tuple(payload["diagnostic_families"]),
        }
    )
    actual = asdict(contract)
    for field, expected in _FROZEN_CONTRACT.items():
        if actual[field] != expected:
            raise ValueError(f"capital_actions_contract_frozen:{field}")
    if not (
        contract.development_start
        <= contract.development_end
        < contract.historical_diagnostic_start
        < contract.live_oos_start
    ):
        raise ValueError("capital_actions_windows")
    return contract


def build_return_panel(
    events: pd.DataFrame,
    prices: pd.DataFrame,
    benchmarks: Mapping[str, pd.DataFrame],
    contract: CapitalActionsContract,
) -> pd.DataFrame:
    """Measure benchmark-relative returns from the next eligible open."""

    event_columns = {
        "event_id", "family", "code", "ann_date", "eligible",
        "materiality",
    }
    if event_columns.difference(events.columns):
        raise ValueError("capital_actions_event_columns")
    selected = events.loc[events["eligible"].fillna(False)].copy()
    selected["code"] = selected["code"].astype("string").str.zfill(6)
    selected["ann_date"] = (
        selected["ann_date"].astype("string").map(_date_key)
    )
    selected = selected.loc[
        selected["ann_date"].between(
            contract.development_start, contract.development_end
        )
    ].copy()
    if selected.empty:
        return pd.DataFrame()

    price_columns = {"account_id", "code", "trade_date", "open", "close"}
    if price_columns.difference(prices.columns):
        raise ValueError("capital_actions_price_columns")
    working = prices.copy()
    working["code"] = (
        working["code"].astype("string").str.split(".").str[0].str.zfill(6)
    )
    working["trade_date"] = (
        working["trade_date"].astype("string").map(_date_key)
    )
    working = working.loc[
        working["trade_date"].between(
            contract.development_start, contract.development_end
        )
    ].copy()
    open_column = "adjusted_open" if "adjusted_open" in working else "open"
    close_column = (
        "adjusted_close" if "adjusted_close" in working else "close"
    )
    working["return_open"] = pd.to_numeric(working[open_column], errors="coerce")
    working["return_close"] = pd.to_numeric(
        working[close_column], errors="coerce"
    )

    benchmark_maps: dict[str, tuple[dict[str, float], dict[str, float]]] = {}
    for scope, raw in benchmarks.items():
        frame = raw.copy()
        if {"trade_date", "open", "close"}.difference(frame.columns):
            raise ValueError(f"capital_actions_benchmark_columns:{scope}")
        frame["trade_date"] = frame["trade_date"].astype("string").map(_date_key)
        frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = (
            frame.dropna(subset=["trade_date", "open", "close"])
            .loc[lambda item: item["open"].gt(0) & item["close"].gt(0)]
            .drop_duplicates("trade_date", keep="last")
        )
        benchmark_maps[str(scope)] = (
            dict(zip(frame["trade_date"].astype(str), frame["open"].astype(float))),
            dict(zip(frame["trade_date"].astype(str), frame["close"].astype(float))),
        )

    events_by_code = {
        str(code): group for code, group in selected.groupby("code", sort=False)
    }
    rows: list[dict[str, Any]] = []
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
        benchmark_open, benchmark_close = benchmark_pair
        for _, event in event_group.iterrows():
            # Announcements are conservatively available after the close.
            entry_index = int(
                np.searchsorted(dates, str(event["ann_date"]), side="right")
            )
            if entry_index >= len(ordered):
                continue
            entry_date = str(dates[entry_index])
            entry_price = float(ordered.iloc[entry_index]["return_open"])
            benchmark_entry = benchmark_open.get(entry_date)
            if (
                not np.isfinite(entry_price) or entry_price <= 0.0
                or benchmark_entry is None or not np.isfinite(benchmark_entry)
                or benchmark_entry <= 0.0
            ):
                continue
            for horizon in contract.horizons:
                exit_index = entry_index + horizon - 1
                if exit_index >= len(ordered):
                    continue
                exit_date = str(dates[exit_index])
                exit_price = float(ordered.iloc[exit_index]["return_close"])
                benchmark_exit = benchmark_close.get(exit_date)
                if (
                    not np.isfinite(exit_price) or exit_price <= 0.0
                    or benchmark_exit is None or not np.isfinite(benchmark_exit)
                    or benchmark_exit <= 0.0
                ):
                    continue
                security_return = exit_price / entry_price - 1.0
                benchmark_return = benchmark_exit / benchmark_entry - 1.0
                active_return = security_return - benchmark_return
                rows.append({
                    "event_id": str(event["event_id"]),
                    "family": str(event["family"]),
                    "code": str(code),
                    "ann_date": str(event["ann_date"]),
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "event_year": str(event["ann_date"])[:4],
                    "account_scope": str(scope),
                    "materiality": float(event["materiality"]),
                    "horizon": int(horizon),
                    "security_return": security_return,
                    "benchmark_return": benchmark_return,
                    "active_return": active_return,
                    "net_active_return": active_return - contract.round_trip_cost,
                    "stress_net_active_return": (
                        active_return
                        - contract.round_trip_cost * contract.stress_cost_multiple
                    ),
                })
    return pd.DataFrame(rows)


def _cluster_bootstrap_probability(
    frame: pd.DataFrame,
    contract: CapitalActionsContract,
    *,
    family: str,
    horizon: int,
) -> float | None:
    if frame.empty:
        return None
    years = sorted(frame["event_year"].dropna().astype(str).unique())
    if len(years) < 2:
        return None
    groups = {
        year: pd.to_numeric(
            frame.loc[frame["event_year"].eq(year), "net_active_return"],
            errors="coerce",
        ).dropna().to_numpy(dtype=float)
        for year in years
    }
    family_seed = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16)
    seed = contract.bootstrap_seed + family_seed % 100_000 + int(horizon)
    rng = np.random.default_rng(seed)
    samples = max(1, int(contract.bootstrap_samples))
    positive = 0
    for _ in range(samples):
        selected = rng.choice(years, size=len(years), replace=True)
        values = np.concatenate([groups[str(year)] for year in selected])
        positive += int(len(values) > 0 and float(values.mean()) > 0.0)
    return positive / samples


def evaluate_panel(
    panel: pd.DataFrame,
    audit: Mapping[str, Any],
    contract: CapitalActionsContract,
) -> dict[str, Any]:
    """Evaluate each positive family independently under frozen gates."""

    family_results: list[dict[str, Any]] = []
    for family in contract.positive_families:
        family_panel = (
            panel.loc[panel["family"].eq(family)].copy()
            if not panel.empty
            else panel.copy()
        )
        mature = (
            family_panel.loc[
                family_panel["horizon"].eq(max(contract.horizons))
            ].copy()
            if not family_panel.empty
            else family_panel
        )
        scope_events = (
            mature.groupby("account_scope")["event_id"].nunique().to_dict()
            if not mature.empty
            else {}
        )
        years = (
            sorted(mature["event_year"].dropna().astype(str).unique())
            if not mature.empty
            else []
        )
        evidence = {
            "events": int(mature["event_id"].nunique()) if not mature.empty else 0,
            "securities": int(mature["code"].nunique()) if not mature.empty else 0,
            "years": years,
            "scope_events": {
                str(key): int(value) for key, value in scope_events.items()
            },
            "maturity_horizon": max(contract.horizons),
        }
        evidence_checks = {
            "backfill_complete": bool(audit.get("complete")),
            "events": evidence["events"] >= contract.minimum_total_events,
            "securities": (
                evidence["securities"] >= contract.minimum_unique_securities
            ),
            "years": len(years) >= contract.minimum_event_years,
            "scopes": (
                set(scope_events) == {"hs300", "zz500"}
                and all(
                    scope_events[scope] >= contract.minimum_scope_events
                    for scope in ("hs300", "zz500")
                )
            ),
        }

        horizon_results: list[dict[str, Any]] = []
        if all(evidence_checks.values()):
            # Every horizon uses the same account/event cohort that has a
            # complete 60-session outcome, preventing late-event sample drift.
            mature_keys = mature[["account_scope", "event_id"]].drop_duplicates()
            comparable = family_panel.merge(
                mature_keys, on=["account_scope", "event_id"], how="inner"
            )
            for horizon in contract.horizons:
                frame = comparable.loc[comparable["horizon"].eq(horizon)].copy()
                year_means = frame.groupby("event_year")["net_active_return"].mean()
                positive_fraction = (
                    float(year_means.gt(0.0).mean()) if len(year_means) else 0.0
                )
                contributions = (
                    frame.groupby("event_year")["net_active_return"]
                    .sum()
                    .clip(lower=0.0)
                )
                total_positive = float(contributions.sum())
                concentration = (
                    float(contributions.max() / total_positive)
                    if total_positive > 0.0
                    else 1.0
                )
                probability = _cluster_bootstrap_probability(
                    frame, contract, family=family, horizon=horizon
                )
                scope_means = (
                    frame.groupby("account_scope")["net_active_return"]
                    .mean()
                    .to_dict()
                )
                checks = {
                    "mean_positive": (
                        not frame.empty
                        and float(frame["net_active_return"].mean()) > 0.0
                    ),
                    "median_positive": (
                        not frame.empty
                        and float(frame["net_active_return"].median()) > 0.0
                    ),
                    "stress_positive": (
                        not frame.empty
                        and float(frame["stress_net_active_return"].mean()) > 0.0
                    ),
                    "year_stability": (
                        positive_fraction
                        >= contract.minimum_positive_year_fraction
                    ),
                    "bootstrap": (
                        probability is not None
                        and probability >= contract.minimum_bootstrap_probability
                    ),
                    "year_concentration": (
                        concentration <= contract.maximum_year_contribution_share
                    ),
                    "scope_consistency": (
                        set(scope_means) == {"hs300", "zz500"}
                        and all(value > 0.0 for value in scope_means.values())
                    ),
                }
                horizon_results.append({
                    "horizon": int(horizon),
                    "is_primary": horizon == contract.primary_horizon,
                    "observations": int(len(frame)),
                    "events": int(frame["event_id"].nunique()),
                    "mean_active_return": float(frame["active_return"].mean()),
                    "mean_net_active_return": float(
                        frame["net_active_return"].mean()
                    ),
                    "median_net_active_return": float(
                        frame["net_active_return"].median()
                    ),
                    "mean_stress_net_active_return": float(
                        frame["stress_net_active_return"].mean()
                    ),
                    "bootstrap_probability": probability,
                    "positive_year_fraction": positive_fraction,
                    "maximum_year_contribution_share": concentration,
                    "scope_mean_net_active_return": {
                        str(key): float(value) for key, value in scope_means.items()
                    },
                    "checks": checks,
                    "passed": bool(all(checks.values())),
                })

        primary = next(
            (row for row in horizon_results if row["is_primary"]), None
        )
        if not all(evidence_checks.values()):
            status = "insufficient_data"
        elif primary is not None and primary["passed"]:
            status = "transparent_baseline_candidate"
        else:
            status = "falsified"
        family_results.append({
            "family": family,
            "status": status,
            "evidence": evidence,
            "evidence_checks": evidence_checks,
            "horizons": horizon_results,
        })

    statuses = {row["status"] for row in family_results}
    if not bool(audit.get("complete")) or statuses == {"insufficient_data"}:
        overall_status = "insufficient_data"
    elif "transparent_baseline_candidate" in statuses:
        overall_status = "transparent_baseline_candidate"
    else:
        overall_status = "falsified"
    return {
        "protocol_version": contract.protocol_version,
        "status": overall_status,
        "families": family_results,
        "model_training_allowed": False,
        "formal_strategy_unchanged": True,
    }


def select_eligible_events(
    events: pd.DataFrame,
    contract: CapitalActionsContract,
) -> pd.DataFrame:
    """Apply the frozen family and materiality rules once, centrally."""

    required = {"family", "materiality", "eligible"}
    if required.difference(events.columns):
        raise ValueError("capital_actions_event_columns")
    selected = events.loc[
        events["family"].isin(contract.positive_families)
    ].copy()
    materiality = pd.to_numeric(selected["materiality"], errors="coerce")
    repurchase = selected["family"].eq("repurchase_completed")
    selected.loc[repurchase, "eligible"] &= materiality.loc[repurchase].ge(
        contract.repurchase_market_cap_ratio
    )
    holder = selected["family"].str.startswith("holder_", na=False)
    selected.loc[holder, "eligible"] &= materiality.loc[holder].ge(
        contract.holder_change_ratio
    )
    return selected.loc[selected["eligible"].fillna(False)].copy()


def _write_report(result: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Capital Actions Study",
        "",
        f"- Protocol: {result['protocol_version']}",
        f"- Status: {result['status']}",
        f"- Snapshot: {result.get('snapshot_date')}",
        "- Model training allowed: false",
        "- Formal strategy unchanged: true",
        "- Historical diagnostic opened: false",
        "",
    ]
    for family in result["families"]:
        evidence = family["evidence"]
        lines.extend([
            f"## {family['family']}",
            "",
            f"- Status: {family['status']}",
            f"- Mature events: {evidence['events']}",
            f"- Securities: {evidence['securities']}",
            f"- Years: {', '.join(evidence['years']) or 'none'}",
            "- Scope events: "
            + json.dumps(evidence["scope_events"], sort_keys=True),
            "",
        ])
        for row in family["horizons"]:
            lines.extend([
                f"### {row['horizon']} sessions",
                "",
                f"- Mean net active return: {row['mean_net_active_return']:.2%}",
                f"- Median net active return: {row['median_net_active_return']:.2%}",
                f"- Stress-cost mean: {row['mean_stress_net_active_return']:.2%}",
                f"- Bootstrap probability: {row['bootstrap_probability']}",
                f"- Passed: {str(row['passed']).lower()}",
                "",
            ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_capital_actions_study(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    contract_path: str | Path = "configs/research/capital_actions_study.yaml",
    output_root: str | Path = "reports/research",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract_file = Path(contract_path)
    if not contract_file.is_absolute():
        contract_file = root / contract_file
    contract = load_contract(contract_file)
    snapshot_key = _date_key(snapshot_date)
    events, audit = load_capital_action_events(
        root,
        start_date=_iso_date(contract.development_start),
        end_date=_iso_date(contract.development_end),
    )

    panel = pd.DataFrame()
    if bool(audit.get("complete")) and not events.empty:
        eligible = select_eligible_events(events, contract)
        if not eligible.empty:
            prices = build_lightweight_event_price_panel(
                root,
                snapshot_date=snapshot_key,
                development_start=contract.development_start,
                development_end=contract.development_end,
                event_codes=set(eligible["code"].astype(str)),
            )
            panel = build_return_panel(
                eligible,
                prices,
                {
                    "hs300": _load_benchmark(root, "000300", snapshot_key),
                    "zz500": _load_benchmark(root, "000905", snapshot_key),
                },
                contract,
            )

    result = evaluate_panel(panel, audit, contract)
    result.update({
        "snapshot_date": snapshot_key,
        "development_window": [
            contract.development_start,
            contract.development_end,
        ],
        "backfill": audit,
        "panel_rows": int(len(panel)),
        "historical_diagnostic_opened": False,
        "live_oos_start": contract.live_oos_start,
    })
    output = Path(output_root)
    if not output.is_absolute():
        output = root / output
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"capital_actions_{snapshot_key}.json"
    markdown_path = output / f"capital_actions_{snapshot_key}.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(result, markdown_path)
    return {
        **result,
        "report_json": str(json_path),
        "report_markdown": str(markdown_path),
    }
