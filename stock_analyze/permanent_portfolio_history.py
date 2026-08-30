"""Build a continuous historical permanent-portfolio Dashboard artifact."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .research.permanent_portfolio.contract import canonical_hash, load_contract
from .research.permanent_portfolio.workflow import (
    REPORT_RELATIVE,
    _latest_market_with_evidence,
    _market_index,
    _study_root,
    evaluate_window,
)
from .utils import write_text_atomic


def merge_historical_market(
    development: pd.DataFrame,
    holdout: pd.DataFrame,
    *,
    holdout_start: str,
) -> pd.DataFrame:
    """Join immutable partitions without duplicating the holdout warm-up rows."""
    boundary = str(holdout_start).replace("-", "")[:8]
    development_dates = development["trade_date"].astype(str).str.replace("-", "")
    holdout_dates = holdout["trade_date"].astype(str).str.replace("-", "")
    combined = pd.concat(
        [
            development.loc[development_dates.lt(boundary)],
            holdout.loc[holdout_dates.ge(boundary)],
        ],
        ignore_index=True,
    )
    key_columns = ["trade_date", "role"] if "role" in combined.columns else [
        "trade_date",
        "ts_code",
    ]
    if combined.duplicated(key_columns).any():
        raise ValueError("permanent_portfolio_historical_duplicates")
    return combined.sort_values(key_columns).reset_index(drop=True)


def rebuild_historical_dashboard(
    *,
    repo_root: str | Path,
    contract_path: str | Path,
) -> dict[str, Any]:
    """Replay development and holdout as one account and replace Dashboard views."""
    root = Path(repo_root).resolve()
    contract = load_contract(contract_path)
    study_root = _study_root(root)
    state_path = study_root / "manifests/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    unsigned_state = dict(state)
    recorded_state_sha256 = unsigned_state.pop("state_sha256", None)
    if (
        state.get("status") not in {"holdout_complete", "forward_ready"}
        or recorded_state_sha256 != canonical_hash(unsigned_state)
    ):
        raise ValueError("permanent_portfolio_historical_state")

    market_index, bundle_sha256 = _market_index(study_root)
    if state.get("market_bundle_sha256") != bundle_sha256:
        raise ValueError("permanent_portfolio_historical_market_binding")
    development, development_evidence = _latest_market_with_evidence(
        study_root,
        partition="development",
        market_index=market_index,
    )
    holdout, holdout_evidence = _latest_market_with_evidence(
        study_root,
        partition="holdout",
        market_index=market_index,
    )
    market = merge_historical_market(
        development,
        holdout,
        holdout_start=contract.holdout_start,
    )
    holdout_end = str(state.get("holdout_end") or "")
    historical = evaluate_window(
        market,
        contract=contract,
        start_date=contract.development_start,
        end_date=holdout_end,
    )
    historical["stage_boundaries"] = [
        {
            "date": contract.holdout_start,
            "before_label": "开发期",
            "after_label": "盲测期",
        }
    ]
    historical["source_evidence"] = {
        "development_data_sha256": development_evidence[
            "partition_data_sha256"
        ],
        "holdout_data_sha256": holdout_evidence["partition_data_sha256"],
    }

    report_path = root / REPORT_RELATIVE
    existing = json.loads(report_path.read_text(encoding="utf-8"))
    unsigned_existing = dict(existing)
    recorded_dashboard_sha256 = unsigned_existing.pop("dashboard_sha256", None)
    if recorded_dashboard_sha256 != canonical_hash(unsigned_existing):
        raise ValueError("permanent_portfolio_historical_dashboard_binding")
    forward = existing.get("forward")
    dashboard = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "study": state,
        "historical": historical,
        "forward": (
            forward if isinstance(forward, dict) else {"status": "unavailable"}
        ),
    }
    dashboard["dashboard_sha256"] = canonical_hash(dashboard)
    write_text_atomic(
        report_path,
        json.dumps(
            dashboard,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "status": "complete",
        "start_date": historical["start_date"],
        "end_date": historical["end_date"],
        "boundary_date": contract.holdout_start,
        "dashboard": str(report_path),
    }
