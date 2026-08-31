"""Idempotent forward paper tracking for the two permanent portfolios."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ...store import PortfolioStore
from ...utils import append_csv, write_text_atomic
from .contract import canonical_hash, load_contract
from .engine import ReplayResult, replay_strategy
from .signals import dynamic_target_weights, fixed_target_weights
from .workflow import (
    _assert_market_accounting,
    _code_evidence,
    _latest_market_with_evidence,
    _momentum_observations,
    _report_relative,
    _study_root,
)


RUN_COLUMNS = (
    "run_id",
    "as_of",
    "strategy",
    "status",
    "completed_at",
)
ACCOUNT_FILES = (
    "state.json",
    "positions.csv",
    "trades.csv",
    "daily_nav.csv",
    "pending_orders.json",
    "latest_signals.csv",
    "runs.csv",
)


def account_paths(
    repo_root: str | Path,
    *,
    study_id: str = "permanent_portfolio_v1",
) -> dict[str, Path]:
    if study_id not in {"permanent_portfolio_v1", "permanent_portfolio_v2"}:
        raise ValueError("permanent_portfolio_study_id")
    version = study_id.rsplit("_", 1)[-1]
    root = Path(repo_root).resolve() / "data/research/paper_portfolios"
    return {
        "fixed": root / f"permanent_fixed_{version}",
        "dynamic": root / f"permanent_dynamic_{version}",
    }


def _date_key(value: str) -> str:
    key = str(value).replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"permanent_portfolio_paper_date:{value}")
    return key


def _completed(path: Path, run_id: str) -> bool:
    runs_path = path / "runs.csv"
    if not runs_path.is_file():
        return False
    runs = pd.read_csv(runs_path, dtype=str)
    return bool(
        (
            runs["run_id"].eq(run_id)
            & runs["status"].eq("complete")
        ).any()
    )


def _initialize_store(path: Path, strategy: str) -> PortfolioStore:
    store = PortfolioStore(path)
    store.initialize(
        {
            "strategy_id": f"permanent_{strategy}_v1",
            "accounts": [
                {
                    "id": strategy,
                    "name": f"永久组合-{strategy}",
                    "scope": "permanent_portfolio",
                    "benchmark": "511880.SH",
                    "cash": 200000.0,
                }
            ],
        }
    )
    return store


def _append_terminal_run(
    path: Path,
    *,
    run_id: str,
    as_of: str,
    strategy: str,
) -> None:
    append_csv(
        path / "runs.csv",
        [
            {
                "run_id": run_id,
                "as_of": as_of,
                "strategy": strategy,
                "status": "complete",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        list(RUN_COLUMNS),
    )


def _snapshot_accounts(paths: dict[str, Path]) -> dict[Path, bytes | None]:
    return {
        path / name: (
            (path / name).read_bytes()
            if (path / name).is_file()
            else None
        )
        for path in paths.values()
        for name in ACCOUNT_FILES
    }


def _restore_accounts(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def _persist_evaluation(
    store: PortfolioStore,
    *,
    strategy: str,
    result: ReplayResult,
) -> None:
    nav = result.nav
    positions = result.positions
    trades = result.trades
    if nav.empty:
        raise ValueError("permanent_portfolio_paper_nav")
    latest = nav.iloc[-1]
    state = store.load_state()
    account = state["accounts"][strategy]
    account["cash"] = float(latest["cash"])
    account["positions"] = {}
    for row in positions.to_dict(orient="records"):
        shares = int(row["shares"])
        if shares <= 0:
            continue
        account["positions"][str(row["code"])] = {
            "name": str(row["role"]),
            "industry": "permanent_portfolio",
            "shares": shares,
            "available_shares": shares,
            "avg_cost": float(row["last_price"]),
            "last_price": float(row["last_price"]),
            "market_value": float(row["market_value"]),
            "unrealized_pnl": 0.0,
            "score": 0.0,
            "reason": "permanent_portfolio",
        }
    store.save_state(state)
    store.write_positions(state)
    trade_rows: list[dict[str, Any]] = [
        {
            "trade_date": row["trade_date"],
            "account_id": strategy,
            "code": row["code"],
            "name": row["role"],
            "side": row["side"],
            "shares": row["shares"],
            "price": row["price"],
            "gross_amount": row["gross_amount"],
            "commission": row["commission"],
            "stamp_tax": row["stamp_tax"],
            "slippage": row["slippage"],
            "net_amount": row["net_amount"],
            "cash_after": row["cash_after"],
            "reason": row["reason"],
        }
        for row in trades.to_dict(orient="records")
    ]
    store.append_trades(trade_rows)
    store.append_nav(
        [
            {
                "date": row["date"],
                "account_id": strategy,
                "cash": row["cash"],
                "settlement_receivable": 0.0,
                "market_value": row["market_value"],
                "total_value": row["total_value"],
                "benchmark_code": "511880.SH",
                "benchmark_close": None,
                "benchmark_date": row["date"],
                "notes": "permanent_portfolio_forward",
            }
            for row in nav.to_dict(orient="records")
        ]
    )
    store.save_pending(result.pending.to_dict(orient="records"))
    store.save_signals(result.targets)


def _last_nav_date(path: Path) -> str | None:
    nav_path = path / "daily_nav.csv"
    if not nav_path.is_file():
        return None
    nav = pd.read_csv(nav_path, dtype={"date": str})
    if nav.empty:
        return None
    return _date_key(str(nav["date"].max()))


def _initial_account(
    store: PortfolioStore,
    *,
    strategy: str,
    role_by_code: dict[str, str],
) -> tuple[float, dict[str, int], str | None, dict[str, float] | None]:
    state = store.load_state()
    account = state["accounts"][strategy]
    positions = {role: 0 for role in role_by_code.values()}
    for code, position in account.get("positions", {}).items():
        role = role_by_code.get(str(code))
        if role is None:
            raise ValueError("permanent_portfolio_paper_position")
        positions[role] = int(position["shares"])
    pending = store.load_pending()
    if not pending:
        return float(account["cash"]), positions, None, None
    signal_dates = {
        _date_key(str(row["signal_date"]))
        for row in pending
    }
    if len(signal_dates) != 1:
        raise ValueError("permanent_portfolio_paper_pending")
    signal_date = signal_dates.pop()
    target = {
        str(row["role"]): float(row["target_weight"])
        for row in pending
    }
    return float(account["cash"]), positions, signal_date, target


def _paper_result(
    *,
    strategy: str,
    market: pd.DataFrame,
    new_market: pd.DataFrame,
    store: PortfolioStore,
    contract: Any,
) -> ReplayResult:
    role_by_code = {asset.code: asset.role for asset in contract.assets}
    cash, positions, pending_signal, pending_target = _initial_account(
        store,
        strategy=strategy,
        role_by_code=role_by_code,
    )
    first_date = str(new_market["trade_date"].min())
    target_schedule: dict[str, dict[str, float]] = {}
    if _last_nav_date(store.data_dir) is None and pending_signal is None:
        initial_signal = (
            pd.Timestamp(first_date) - pd.Timedelta(days=1)
        ).strftime("%Y%m%d")
        if strategy == "fixed":
            initial_target = {
                asset.role: contract.fixed_target_weight
                for asset in contract.assets
            }
        else:
            initial_target = dynamic_target_weights(
                _momentum_observations(market, as_of=initial_signal)
            )
        target_schedule[initial_signal] = initial_target

    common_dates = (
        market.groupby("trade_date")["role"]
        .nunique()
        .loc[lambda values: values.eq(len(contract.assets))]
        .index.astype(str)
    )
    dated = pd.Series(
        pd.to_datetime(common_dates, format="%Y%m%d"),
        index=common_dates,
    )
    month_ends = set(
        dated.groupby(dated.dt.to_period("M"))
        .idxmax()
        .astype(str)
    )

    def policy(
        date: str,
        weights: dict[str, float],
        _history: pd.DataFrame,
    ) -> dict[str, float] | None:
        if strategy == "fixed":
            return fixed_target_weights(
                weights,
                lower=contract.lower_band,
                upper=contract.upper_band,
            )
        if date not in month_ends:
            return None
        return dynamic_target_weights(
            _momentum_observations(market, as_of=date)
        )

    return replay_strategy(
        new_market,
        strategy=strategy,
        initial_cash=cash,
        initial_positions=positions,
        initial_pending_signal=pending_signal,
        initial_pending_target=pending_target,
        target_schedule=target_schedule,
        target_policy=policy,
        lot_size=contract.lot_size,
        commission_rate=contract.commission_rate,
        minimum_commission=contract.minimum_commission,
        slippage_rate=contract.slippage_rate,
        stamp_tax_rate=contract.stamp_tax_rate,
    )


def _verify_paper_gate(
    *,
    state: dict[str, Any],
    contract: Any,
    study_root: Path,
    market_bundle_sha256: str,
) -> None:
    unsigned_state = dict(state)
    recorded_state_sha256 = unsigned_state.pop("state_sha256", None)
    code_evidence = _code_evidence()
    expected = {
        "contract_sha256": canonical_hash(contract.raw),
        "market_bundle_sha256": market_bundle_sha256,
        "code_sha256": code_evidence["code_sha256"],
        "git_revision": code_evidence["git_revision"],
    }
    if (
        state.get("status") not in {"holdout_complete", "forward_ready"}
        or recorded_state_sha256 != canonical_hash(unsigned_state)
        or any(state.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("permanent_portfolio_paper_binding")
    holdout_path = Path(str(state.get("holdout_artifact") or "")).resolve()
    try:
        holdout_path.relative_to((study_root / "results/holdout").resolve())
        holdout = json.loads(holdout_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("permanent_portfolio_paper_binding") from exc
    unsigned_holdout = dict(holdout)
    recorded_holdout_sha256 = unsigned_holdout.pop("artifact_sha256", None)
    if (
        recorded_holdout_sha256 != state.get("holdout_sha256")
        or canonical_hash(unsigned_holdout) != recorded_holdout_sha256
        or holdout.get("status") != "holdout_complete"
        or any(holdout.get(key) != value for key, value in expected.items())
        or holdout.get("development_sha256")
        != state.get("development_sha256")
        or holdout.get("holdout_marker_sha256")
        != state.get("holdout_marker_sha256")
    ):
        raise ValueError("permanent_portfolio_paper_binding")


def run_paper_day(
    repo_root: str | Path,
    *,
    as_of: str,
    fixture_mode: bool = False,
    contract_path: str | Path = "configs/research/permanent_portfolio_v1.yaml",
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    as_of_key = _date_key(as_of)
    run_id = f"permanent-portfolio-{as_of_key}"
    paths = account_paths(repo_root, study_id=contract.study_id)
    if fixture_mode and all(
        _completed(path, run_id) for path in paths.values()
    ):
        return {
            "run_id": run_id,
            "as_of": as_of_key,
            "status": "already_complete",
        }
    if fixture_mode:
        stores = {
            strategy: _initialize_store(path, strategy)
            for strategy, path in paths.items()
        }
        for strategy, path in paths.items():
            _append_terminal_run(
                path,
                run_id=run_id,
                as_of=as_of_key,
                strategy=strategy,
            )
        return {"run_id": run_id, "as_of": as_of_key, "status": "complete"}

    study_root = _study_root(repo_root, contract=contract)
    state_path = study_root / "manifests" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    holdout_end = _date_key(str(state["holdout_end"]))
    market, market_evidence = _latest_market_with_evidence(
        study_root,
        partition="holdout",
    )
    _assert_market_accounting(contract, market_evidence)
    _verify_paper_gate(
        state=state,
        contract=contract,
        study_root=study_root,
        market_bundle_sha256=market_evidence["market_bundle_sha256"],
    )
    report_path = Path(repo_root).resolve() / _report_relative(contract)
    dashboard = json.loads(report_path.read_text(encoding="utf-8"))
    unsigned_dashboard = dict(dashboard)
    recorded_dashboard_sha256 = unsigned_dashboard.pop(
        "dashboard_sha256",
        None,
    )
    if canonical_hash(unsigned_dashboard) != recorded_dashboard_sha256:
        raise ValueError("permanent_portfolio_paper_binding")
    stores = {
        strategy: _initialize_store(path, strategy)
        for strategy, path in paths.items()
    }
    eligible_market = market.loc[
        market["trade_date"].astype(str).gt(holdout_end)
        & market["trade_date"].astype(str).le(as_of_key),
    ].copy()
    if eligible_market.empty:
        raise ValueError("permanent_portfolio_paper_market")
    results: dict[str, ReplayResult] = {}
    forward_as_of = str(eligible_market["trade_date"].max())
    if all(
        _completed(path, run_id)
        and _last_nav_date(path) == forward_as_of
        for path in paths.values()
    ):
        return {
            "run_id": run_id,
            "as_of": as_of_key,
            "status": "already_complete",
        }
    for strategy, store in stores.items():
        if (
            _completed(paths[strategy], run_id)
            and _last_nav_date(paths[strategy]) == forward_as_of
        ):
            continue
        last_date = _last_nav_date(paths[strategy])
        new_market = eligible_market.loc[
            eligible_market["trade_date"].astype(str).gt(
                last_date or holdout_end
            )
        ].copy()
        if new_market.empty:
            raise ValueError("permanent_portfolio_paper_no_new_market")
        result = _paper_result(
            strategy=strategy,
            market=market,
            new_market=new_market,
            store=store,
            contract=contract,
        )
        results[strategy] = result
    snapshot = _snapshot_accounts(paths)
    try:
        for strategy, result in results.items():
            _persist_evaluation(
                stores[strategy],
                strategy=strategy,
                result=result,
            )
            _append_terminal_run(
                paths[strategy],
                run_id=run_id,
                as_of=as_of_key,
                strategy=strategy,
            )
        if any(
            _last_nav_date(path) != forward_as_of
            for path in paths.values()
        ):
            raise ValueError("permanent_portfolio_paper_account_alignment")
    except Exception:
        _restore_accounts(snapshot)
        raise
    state.pop("state_sha256", None)
    state["status"] = "forward_ready"
    state["forward_as_of"] = forward_as_of
    state["state_sha256"] = canonical_hash(state)
    write_text_atomic(
        state_path,
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    dashboard["study"] = state
    previous_forward = dashboard.get("forward")
    previous_portfolios = (
        dict(previous_forward.get("portfolios") or {})
        if isinstance(previous_forward, dict)
        else {}
    )
    previous_portfolios.update({
        strategy: {
            "nav": result.nav.to_dict(orient="records"),
            "trades": result.trades.to_dict(orient="records"),
            "targets": result.targets.to_dict(orient="records"),
            "positions": result.positions.to_dict(orient="records"),
            "pending": result.pending.to_dict(orient="records"),
        }
        for strategy, result in results.items()
    })
    dashboard["forward"] = {
        "status": "available",
        "start_date": (
            previous_forward.get("start_date")
            if isinstance(previous_forward, dict)
            and previous_forward.get("start_date")
            else min(
                str(result.nav["date"].min())
                for result in results.values()
            )
        ),
        "end_date": forward_as_of,
        "portfolios": previous_portfolios,
    }
    dashboard.pop("dashboard_sha256", None)
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
    return {"run_id": run_id, "as_of": as_of_key, "status": "complete"}
