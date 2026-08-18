"""Preregistered model-free restricted-share unlock avoidance study."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .capital_actions_study import _build_batched_return_panel, evaluate_panel
from .earnings_drift_study import _load_benchmark
from .share_unlock_backfill import load_share_unlock_events


@dataclass(frozen=True)
class ShareUnlockContract:
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
    confirmation_lookback_calendar_days: int
    maximum_ratio_disagreement: float
    large_unlock_ratio: float
    small_unlock_ratio: float
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


_FROZEN: dict[str, Any] = {
    "protocol_version": "share-unlock-avoidance-preregistered-v1",
    "market": "a_share",
    "development_start": "20180101",
    "development_end": "20241231",
    "historical_diagnostic_start": "20250101",
    "live_oos_start": "20260818",
    "horizons": (5, 20, 60),
    "primary_horizon": 20,
    "round_trip_cost": 0.0021,
    "stress_cost_multiple": 1.5,
    "confirmation_lookback_calendar_days": 30,
    "maximum_ratio_disagreement": 0.01,
    "large_unlock_ratio": 0.05,
    "small_unlock_ratio": 0.01,
    "minimum_total_events": 100,
    "minimum_unique_securities": 50,
    "minimum_event_years": 4,
    "minimum_scope_events": 25,
    "minimum_positive_year_fraction": 0.6666666667,
    "minimum_bootstrap_probability": 0.95,
    "maximum_year_contribution_share": 0.50,
    "bootstrap_samples": 5000,
    "bootstrap_seed": 20260818,
    "positive_families": ("large_unlock_avoidance",),
    "diagnostic_families": ("small_unlock_avoidance",),
}


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _iso_date(value: str) -> str:
    key = _date_key(value)
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def load_contract(path: str | Path) -> ShareUnlockContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    contract = ShareUnlockContract(
        **{
            **payload,
            "horizons": tuple(int(value) for value in payload["horizons"]),
            "positive_families": tuple(payload["positive_families"]),
            "diagnostic_families": tuple(payload["diagnostic_families"]),
        }
    )
    actual = asdict(contract)
    for field, expected in _FROZEN.items():
        if actual[field] != expected:
            raise ValueError(f"share_unlock_contract_frozen:{field}")
    return contract


def select_study_events(
    events: pd.DataFrame, contract: ShareUnlockContract
) -> pd.DataFrame:
    required = {"unlock_ratio", "family"}
    if required.difference(events.columns):
        raise ValueError("share_unlock_event_columns")
    ratio = pd.to_numeric(events["unlock_ratio"], errors="coerce")
    selected = events.loc[
        ratio.ge(contract.large_unlock_ratio)
        | (ratio.gt(0.0) & ratio.le(contract.small_unlock_ratio))
    ].copy()
    selected["family"] = "large_unlock_avoidance"
    selected.loc[
        pd.to_numeric(selected["unlock_ratio"], errors="coerce")
        .le(contract.small_unlock_ratio),
        "family",
    ] = "small_unlock_avoidance"
    selected["eligible"] = True
    return selected


def to_avoidance_panel(
    long_panel: pd.DataFrame, contract: ShareUnlockContract
) -> pd.DataFrame:
    if long_panel.empty:
        return long_panel.copy()
    required = {"security_return", "benchmark_return"}
    if required.difference(long_panel.columns):
        raise ValueError("share_unlock_return_columns")
    panel = long_panel.copy()
    panel["active_return"] = (
        panel["benchmark_return"] - panel["security_return"]
    )
    panel["net_active_return"] = (
        panel["active_return"] - contract.round_trip_cost
    )
    panel["stress_net_active_return"] = (
        panel["active_return"]
        - contract.round_trip_cost * contract.stress_cost_multiple
    )
    panel["return_interpretation"] = "benchmark_substitution_avoidance"
    return panel


def _write_report(result: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Share Unlock Avoidance Study", "",
        f"- Protocol: {result['protocol_version']}",
        f"- Status: {result['status']}",
        f"- Snapshot: {result.get('snapshot_date')}",
        "- Return: benchmark substitution minus stock minus round-trip cost",
        "- Model training allowed: false",
        "- Formal strategy unchanged: true",
        "- Historical diagnostic opened: false", "",
    ]
    for family in result["families"]:
        evidence = family["evidence"]
        lines.extend([
            f"## {family['family']}", "", f"- Status: {family['status']}",
            f"- Mature events: {evidence['events']}",
            f"- Securities: {evidence['securities']}", "",
        ])
        for row in family["horizons"]:
            lines.extend([
                f"### {row['horizon']} sessions", "",
                f"- Mean net avoidance return: {row['mean_net_active_return']:.2%}",
                f"- Median net avoidance return: {row['median_net_active_return']:.2%}",
                f"- Stress-cost mean: {row['mean_stress_net_active_return']:.2%}",
                f"- Bootstrap probability: {row['bootstrap_probability']}",
                f"- Passed: {str(row['passed']).lower()}", "",
            ])
    for family in result["diagnostics"]:
        lines.extend([
            f"## {family['family']} (diagnostic only)", "",
            "- Candidate eligible: false",
            f"- Mature events: {family['evidence']['events']}", "",
        ])
        for row in family["horizons"]:
            lines.extend([
                f"### {row['horizon']} sessions", "",
                f"- Mean net avoidance return: {row['mean_net_active_return']:.2%}",
                f"- Median net avoidance return: {row['median_net_active_return']:.2%}",
                "",
            ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_share_unlock_study(
    repo_root: str | Path, *, snapshot_date: str,
    contract_path: str | Path = "configs/research/share_unlock_avoidance_study.yaml",
    output_root: str | Path = "reports/research",
) -> dict[str, Any]:
    root = Path(repo_root).resolve(); contract_file = Path(contract_path)
    if not contract_file.is_absolute(): contract_file = root / contract_file
    contract = load_contract(contract_file); snapshot_key = _date_key(snapshot_date)
    events, audit = load_share_unlock_events(
        root, start_date=_iso_date(contract.development_start),
        end_date=_iso_date(contract.development_end),
        confirmation_days=contract.confirmation_lookback_calendar_days,
        maximum_ratio_disagreement=contract.maximum_ratio_disagreement,
    )
    selected = select_study_events(events, contract) if not events.empty else events
    panel = pd.DataFrame()
    if bool(audit.get("complete")) and not selected.empty:
        long_panel = _build_batched_return_panel(
            root, selected, snapshot_date=snapshot_key, contract=contract,
            benchmarks={
                "hs300": _load_benchmark(root, "000300", snapshot_key),
                "zz500": _load_benchmark(root, "000905", snapshot_key),
            },
        )
        panel = to_avoidance_panel(long_panel, contract)
    candidate_panel = panel.loc[panel["family"].eq("large_unlock_avoidance")].copy() if not panel.empty else panel.copy()
    diagnostic_panel = panel.loc[panel["family"].eq("small_unlock_avoidance")].copy() if not panel.empty else panel.copy()
    result = evaluate_panel(candidate_panel, audit, contract, diagnostic_panel=diagnostic_panel)
    result.update({
        "snapshot_date": snapshot_key,
        "development_window": [contract.development_start, contract.development_end],
        "return_interpretation": "benchmark_substitution_avoidance",
        "backfill": audit, "panel_rows": int(len(panel)),
        "candidate_panel_rows": int(len(candidate_panel)),
        "diagnostic_panel_rows": int(len(diagnostic_panel)),
        "historical_diagnostic_opened": False,
        "live_oos_start": contract.live_oos_start,
    })
    output = Path(output_root); output = output if output.is_absolute() else root / output
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"share_unlock_avoidance_{snapshot_key}.json"
    markdown_path = output / f"share_unlock_avoidance_{snapshot_key}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_report(result, markdown_path)
    return {**result, "report_json": str(json_path), "report_markdown": str(markdown_path)}
