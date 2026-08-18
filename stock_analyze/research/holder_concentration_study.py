"""Preregistered model-free A-share shareholder concentration study."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .capital_actions_study import (
    _build_batched_return_panel,
    evaluate_panel,
)
from .earnings_drift_study import _load_benchmark
from .holder_concentration_backfill import load_holder_concentration_events


@dataclass(frozen=True)
class HolderConcentrationContract:
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
    concentration_change_threshold: float
    dispersion_change_threshold: float
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
    "protocol_version": "holder-concentration-preregistered-v1",
    "market": "a_share",
    "development_start": "20180101",
    "development_end": "20241231",
    "historical_diagnostic_start": "20250101",
    "live_oos_start": "20260818",
    "horizons": (5, 20, 60),
    "primary_horizon": 20,
    "round_trip_cost": 0.0021,
    "stress_cost_multiple": 1.5,
    "concentration_change_threshold": -0.10,
    "dispersion_change_threshold": 0.10,
    "minimum_total_events": 100,
    "minimum_unique_securities": 50,
    "minimum_event_years": 4,
    "minimum_scope_events": 25,
    "minimum_positive_year_fraction": 0.6666666667,
    "minimum_bootstrap_probability": 0.95,
    "maximum_year_contribution_share": 0.50,
    "bootstrap_samples": 5000,
    "bootstrap_seed": 20260818,
    "positive_families": ("holder_concentration",),
    "diagnostic_families": ("holder_dispersion",),
}


def _date_key(value: object) -> str:
    return str(value).replace("-", "")[:8]


def _iso_date(value: str) -> str:
    key = _date_key(value)
    return f"{key[:4]}-{key[4:6]}-{key[6:8]}"


def load_contract(path: str | Path) -> HolderConcentrationContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    contract = HolderConcentrationContract(
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
            raise ValueError(f"holder_concentration_contract_frozen:{field}")
    return contract


def _write_report(result: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Holder Concentration Study",
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
    for family in result["diagnostics"]:
        evidence = family["evidence"]
        lines.extend([
            f"## {family['family']} (diagnostic only)",
            "",
            "- Candidate eligible: false",
            f"- Mature events: {evidence['events']}",
            "",
        ])
        for row in family["horizons"]:
            lines.extend([
                f"### {row['horizon']} sessions",
                "",
                f"- Mean net active return: {row['mean_net_active_return']:.2%}",
                f"- Median net active return: {row['median_net_active_return']:.2%}",
                "",
            ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select_study_events(
    events: pd.DataFrame,
    contract: HolderConcentrationContract,
) -> pd.DataFrame:
    required = {"family", "holder_count_change"}
    if required.difference(events.columns):
        raise ValueError("holder_concentration_event_columns")
    change = pd.to_numeric(events["holder_count_change"], errors="coerce")
    selected = events.loc[
        (
            events["family"].eq("holder_concentration")
            & change.le(contract.concentration_change_threshold)
        )
        | (
            events["family"].eq("holder_dispersion")
            & change.ge(contract.dispersion_change_threshold)
        )
    ].copy()
    selected["eligible"] = True
    return selected


def run_holder_concentration_study(
    repo_root: str | Path,
    *,
    snapshot_date: str,
    contract_path: str | Path = "configs/research/holder_concentration_study.yaml",
    output_root: str | Path = "reports/research",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    contract_file = Path(contract_path)
    if not contract_file.is_absolute():
        contract_file = root / contract_file
    contract = load_contract(contract_file)
    snapshot_key = _date_key(snapshot_date)
    events, audit = load_holder_concentration_events(
        root,
        start_date=_iso_date(contract.development_start),
        end_date=_iso_date(contract.development_end),
    )
    events = select_study_events(events, contract) if not events.empty else events
    panel = pd.DataFrame()
    if bool(audit.get("complete")) and not events.empty:
        panel = _build_batched_return_panel(
            root,
            events,
            snapshot_date=snapshot_key,
            contract=contract,
            benchmarks={
                "hs300": _load_benchmark(root, "000300", snapshot_key),
                "zz500": _load_benchmark(root, "000905", snapshot_key),
            },
        )
    candidate_panel = (
        panel.loc[panel["family"].eq("holder_concentration")].copy()
        if not panel.empty else panel.copy()
    )
    diagnostic_panel = (
        panel.loc[panel["family"].eq("holder_dispersion")].copy()
        if not panel.empty else panel.copy()
    )
    result = evaluate_panel(
        candidate_panel, audit, contract, diagnostic_panel=diagnostic_panel
    )
    result.update({
        "snapshot_date": snapshot_key,
        "development_window": [
            contract.development_start, contract.development_end
        ],
        "backfill": audit,
        "panel_rows": int(len(panel)),
        "candidate_panel_rows": int(len(candidate_panel)),
        "diagnostic_panel_rows": int(len(diagnostic_panel)),
        "historical_diagnostic_opened": False,
        "live_oos_start": contract.live_oos_start,
    })
    output = Path(output_root)
    if not output.is_absolute():
        output = root / output
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"holder_concentration_{snapshot_key}.json"
    markdown_path = output / f"holder_concentration_{snapshot_key}.md"
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
