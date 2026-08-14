"""Comparable historical diagnostics for formal rules and model candidates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from ..utils import write_text_atomic
from .rule_core_diagnostic import _run_overlay
from .storage import ResearchStore


ARENA_PROTOCOL_VERSION = "unified-model-arena-v1"
_METRIC_KEYS = (
    "net_return",
    "benchmark_return",
    "net_excess_return",
    "portfolio_cagr",
    "benchmark_cagr",
    "annualized_excess_wealth",
    "information_ratio",
    "portfolio_sharpe",
    "max_drawdown",
    "annual_turnover",
    "trade_count",
    "capital_utilization",
)
_LABEL_COLUMNS = {
    "horizon",
    "label",
    "label_end_date",
    "absolute_return",
    "benchmark_return",
    "excess_return",
    "entry_date",
    "entry_price",
    "entry_high",
    "entry_low",
    "entry_close",
    "entry_volume",
    "entry_return_from_prev_close",
    "entry_one_price_limit_up",
    "entry_one_price_limit_down",
    "entry_buy_allowed",
    "entry_sell_allowed",
    "benchmark_entry_price",
    "benchmark_exit_price",
    "exit_date",
    "exit_price",
}


def _compact_metrics(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: values.get(key)
        for key in _METRIC_KEYS
        if key in values
    }


def _date_values(frame: pd.DataFrame) -> tuple[str, ...]:
    column = next(
        (
            name
            for name in ("signal_date", "trade_date", "date")
            if name in frame.columns
        ),
        "",
    )
    if not column:
        return ()
    return tuple(
        sorted(
            frame[column]
            .dropna()
            .astype("string")
            .str.replace("-", "", regex=False)
            .str[:8]
            .unique()
        )
    )


def _candidate_artifacts(
    report: Mapping[str, Any],
) -> tuple[Path, pd.DataFrame, tuple[str, ...]]:
    report_path = Path(str(report["report_path"]))
    for candidate in report.get("candidates") or []:
        spec_id = str(candidate.get("spec_id") or "")
        if not spec_id:
            continue
        candidate_root = report_path.parent / "candidates" / spec_id
        predictions_path = candidate_root / "final_predictions.parquet"
        periods_path = candidate_root / "final_periods.parquet"
        if not predictions_path.exists() or not periods_path.exists():
            continue
        predictions = pd.read_parquet(predictions_path)
        dates = tuple(
            sorted(
                predictions["trade_date"]
                .dropna()
                .astype("string")
                .str.replace("-", "", regex=False)
                .str[:8]
                .unique()
            )
        )
        if not dates:
            continue
        period_dates = _date_values(pd.read_parquet(periods_path))
        if not set(period_dates).issubset(dates):
            raise ValueError(
                f"unified_arena_date_mismatch:model:{spec_id}"
            )
        return candidate_root, predictions, dates
    raise ValueError("unified_arena_candidate_artifacts_missing")


def _rule_inputs(evaluation: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    identity = {"code", "trade_date", "account_id", "research_scope"}
    label_columns = [
        column
        for column in evaluation.columns
        if column in _LABEL_COLUMNS or column in identity
    ]
    feature_columns = [
        column
        for column in evaluation.columns
        if column not in _LABEL_COLUMNS
    ]
    return evaluation[feature_columns].copy(), evaluation[label_columns].copy()


def _scope_baseline(
    baseline: Mapping[str, Any],
    account_scope: str,
) -> dict[str, Any]:
    accounts = [
        dict(account)
        for account in baseline.get("accounts") or []
        if str(account.get("id") or account.get("scope") or "") == account_scope
    ]
    if len(accounts) != 1:
        raise ValueError(
            f"unified_arena_account_contract_missing:{account_scope}"
        )
    return {
        **dict(baseline),
        "accounts": accounts,
        "initial_cash": float(accounts[0].get("cash") or 0.0),
    }


def _winner(participants: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    eligible = []
    for item in participants:
        value = (item.get("metrics") or {}).get("net_excess_return")
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        eligible.append((score, str(item["participant_id"]), item))
    if not eligible:
        return None
    _, _, best = max(eligible, key=lambda item: (item[0], item[1]))
    return {
        "participant_id": best["participant_id"],
        "name": best["name"],
        "net_excess_return": float(
            (best.get("metrics") or {}).get("net_excess_return") or 0.0
        ),
    }


def build_unified_arena_report(
    repo_root: str | Path,
    *,
    market: str,
    horizon: int,
    as_of: str,
    tournament_reports: Sequence[Mapping[str, Any]],
    overlays: Mapping[str, Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(repo_root)
    run_key = str(as_of).replace("-", "")[:8]
    destination = (
        root / "data" / "research" / "unified_arena"
        / str(market) / run_key
    )
    store = ResearchStore(root / "data" / "research")
    scopes: list[dict[str, Any]] = []

    for report in tournament_reports:
        account_scope = str(report.get("account_scope") or "")
        if not account_scope:
            raise ValueError("unified_arena_account_scope_missing")
        candidate_root, evaluation, evaluation_dates = _candidate_artifacts(
            report
        )
        declared_window = tuple(
            str(value).replace("-", "")[:8]
            for value in report.get("final_window") or ()
        )
        if (
            len(declared_window) != 2
            or declared_window[0] != evaluation_dates[0]
            or declared_window[-1] != evaluation_dates[-1]
        ):
            raise ValueError(
                f"unified_arena_date_mismatch:manifest:{account_scope}"
            )
        features, labels = _rule_inputs(evaluation)
        names = (
            dict(
                zip(
                    evaluation["code"].astype(str),
                    evaluation["name"].astype(str),
                )
            )
            if "name" in evaluation.columns
            else {
                str(code): str(code)
                for code in evaluation["code"].dropna().astype(str).unique()
            }
        )
        scoped_baseline = _scope_baseline(baseline, account_scope)
        participants: list[dict[str, Any]] = []
        scope_root = destination / account_scope

        for strategy_key, overlay in overlays.items():
            metrics, nav, trades, periods = _run_overlay(
                features,
                labels,
                market=market,
                overlay=overlay,
                baseline=scoped_baseline,
                development_dates=evaluation_dates,
                names_by_code=names,
            )
            decision_dates = _date_values(periods)
            if not set(decision_dates).issubset(evaluation_dates):
                raise ValueError(
                    f"unified_arena_date_mismatch:rule:{strategy_key}"
                )
            strategy_root = scope_root / "rules" / str(strategy_key)
            store.write_parquet_atomic(strategy_root / "nav.parquet", nav)
            store.write_parquet_atomic(
                strategy_root / "trades.parquet", trades
            )
            store.write_parquet_atomic(
                strategy_root / "periods.parquet", periods
            )
            participants.append({
                "participant_id": f"rule:{strategy_key}",
                "participant_type": "formal_rule",
                "name": str(overlay.get("name") or strategy_key),
                "strategy_id": overlay.get("strategy_id"),
                "status": "historical_replay",
                "evaluation_dates": list(evaluation_dates),
                "decision_dates": list(decision_dates),
                "metrics": _compact_metrics(metrics),
            })

        for candidate in report.get("candidates") or []:
            model_version = str(candidate.get("model_version") or "")
            spec_id = str(candidate.get("spec_id") or "")
            if not model_version or not spec_id:
                continue
            predictions_path = (
                Path(str(report["report_path"])).parent
                / "candidates" / spec_id / "final_predictions.parquet"
            )
            if not predictions_path.exists():
                continue
            candidate_dates = _date_values(pd.read_parquet(predictions_path))
            if candidate_dates != evaluation_dates:
                raise ValueError(
                    f"unified_arena_date_mismatch:model:{spec_id}"
                )
            candidate_decision_dates = _date_values(
                pd.read_parquet(
                    predictions_path.with_name("final_periods.parquet")
                )
            )
            if not set(candidate_decision_dates).issubset(evaluation_dates):
                raise ValueError(
                    f"unified_arena_date_mismatch:model_periods:{spec_id}"
                )
            participants.append({
                "participant_id": f"model:{model_version}",
                "participant_type": "candidate_model",
                "name": spec_id,
                "model_version": model_version,
                "status": str(candidate.get("status") or "research"),
                "gate_reasons": list(candidate.get("reasons") or []),
                "evaluation_dates": list(evaluation_dates),
                "decision_dates": list(candidate_decision_dates),
                "metrics": _compact_metrics(candidate.get("metrics") or {}),
            })

        for baseline_item in report.get("baselines") or []:
            spec_id = str(baseline_item.get("spec_id") or "baseline")
            baseline_dates = tuple(
                sorted(
                    str(item.get("date") or "")
                    .replace("-", "")[:8]
                    for item in baseline_item.get("oos_returns") or []
                    if item.get("date")
                )
            )
            if not set(baseline_dates).issubset(evaluation_dates):
                raise ValueError(
                    f"unified_arena_date_mismatch:baseline:{spec_id}"
                )
            participants.append({
                "participant_id": f"baseline:{spec_id}",
                "participant_type": "baseline",
                "name": spec_id,
                "status": "historical_replay",
                "evaluation_dates": list(evaluation_dates),
                "decision_dates": list(baseline_dates),
                "metrics": _compact_metrics(baseline_item),
            })

        participants.sort(key=lambda item: str(item["participant_id"]))
        scopes.append({
            "account_scope": account_scope,
            "horizon": int(horizon),
            "final_window": [evaluation_dates[0], evaluation_dates[-1]],
            "evaluation_date_count": len(evaluation_dates),
            "candidate_root": str(candidate_root.parent.parent),
            "participants": participants,
            "winner": _winner(participants),
        })

    payload = {
        "schema_version": 1,
        "protocol": ARENA_PROTOCOL_VERSION,
        "evidence_type": "historical_diagnostic",
        "status": "complete" if scopes else "unavailable",
        "market": str(market),
        "horizon": int(horizon),
        "as_of": run_key,
        "scopes": scopes,
        "source_reports": [
            str(report.get("report_path") or "")
            for report in tournament_reports
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = destination / "report.json"
    write_text_atomic(
        report_path,
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Unified Model Arena",
        "",
        f"- market: `{market}`",
        f"- horizon: `{int(horizon)}`",
        f"- evidence: `historical_diagnostic`",
        "",
        "| Account | Participant | Type | Net excess | Drawdown |",
        "|---|---|---|---:|---:|",
    ]
    for scope in scopes:
        for item in scope["participants"]:
            metrics = item.get("metrics") or {}
            lines.append(
                "| {scope} | {name} | {kind} | {excess:.2%} | {drawdown:.2%} |".format(
                    scope=scope["account_scope"],
                    name=item["name"],
                    kind=item["participant_type"],
                    excess=float(metrics.get("net_excess_return") or 0.0),
                    drawdown=float(metrics.get("max_drawdown") or 0.0),
                )
            )
    write_text_atomic(
        destination / "report.md",
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return {**payload, "report_path": str(report_path)}


__all__ = [
    "ARENA_PROTOCOL_VERSION",
    "build_unified_arena_report",
]
