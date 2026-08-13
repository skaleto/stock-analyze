from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .data_provider import DataProvider, ExecutionQuote
from ...factor_pipeline import UNCLASSIFIED
from .portfolio_controls import annotate_industries
from ...store import PortfolioStore
from ...research.execution_policy import estimate_market_impact_bps
from ...execution_costs import calculate_execution_fill
from ...research.strategy_ensemble import (
    load_provider_return_history,
    risk_adjusted_target_weights,
)
from .strategy import build_signals
from ...utils import next_business_day, now_iso, parse_date, safe_float


def initialize(config: dict[str, Any], store: PortfolioStore, force: bool = False) -> dict[str, Any]:
    return store.initialize(config, force=force)


def _resolve_as_of(as_of: str | date | None) -> str | None:
    """Normalize an as_of value to an ISO-format string (or None).

    The simulator's downstream contract uses ISO strings throughout
    (pending batch dates, NAV rows, etc.). The forward-mode call sites
    pass strings; the new backtest-mode call sites can pass datetime.date.
    """
    if as_of is None:
        return None
    if isinstance(as_of, date):
        return as_of.isoformat()
    return str(as_of)


def _override_store(store: PortfolioStore, data_root: Path | None) -> PortfolioStore:
    """If ``data_root`` is provided, build a fresh store rooted there.

    This is the integration seam for the backtest engine (Task 7): a single
    overlay run can be driven through a temporary data_root without
    polluting the agent's forward-mode state files.
    """
    if data_root is None:
        return store
    return PortfolioStore(data_root)


def _override_provider_cache(provider: DataProvider, market_data_root: Path | None) -> None:
    """If ``market_data_root`` is provided, rebind the provider's cache root.

    The abstract ``DataProvider`` base class defines a ``cache_dir`` attribute
    that controls where read-only market-data lookups land; every concrete
    provider (Tushare, Baostock, Akshare) inherits it. Backtest mode points
    this at a historical point-in-time cache; forward mode leaves it pointing
    at ``data/shared/cache/``. The ``hasattr`` guard tolerates lightweight
    test stubs that omit the attribute.
    """
    if market_data_root is None:
        return
    if hasattr(provider, "cache_dir"):
        provider.cache_dir = Path(market_data_root)


def generate_rebalance_orders(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: DataProvider,
    as_of: str | date | None = None,
    run_id: str | None = None,
    *,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    store = _override_store(store, data_root)
    _override_provider_cache(provider, market_data_root)
    as_of = _resolve_as_of(as_of)
    state = store.initialize(config)
    # repo_root controls where strategy.build_signals reads broadcast-factor
    # data (e.g. data/<agent>/alt_factors/market_sentiment.csv). When None,
    # strategy._resolve_default_repo_root() resolves via (in priority order):
    # SA_REPO_ROOT env > __file__-anchored walk > Path.cwd(). The
    # file-anchored fallback is robust to CWD drift (operator running CLI
    # from anywhere).
    all_selected: list[pd.DataFrame] = []
    all_factor_tables: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, Any]] = []
    pending_batches: list[dict[str, Any]] = []
    run_date = as_of or date.today().isoformat()
    execute_after = provider.next_trading_day(run_date) if hasattr(provider, "next_trading_day") else next_business_day(run_date)

    for account in config.get("accounts", []):
        account_id = str(account["id"])
        signal = build_signals(
            config, account, provider, as_of=as_of,
            repo_root=repo_root,
        )
        scored = signal.candidates.copy()
        account_state = state["accounts"][account_id]
        top_n = int(account.get("top_n", 10))
        annotate_industries(account_state.get("positions", {}), scored)
        candidate_pool, pool_warnings = _controlled_candidate_pool(
            scored,
            account_state,
            config,
            top_n,
            run_date=run_date,
        )
        warnings = list(signal.warnings) + pool_warnings

        coverage_rows.extend(_coverage_rows(scored, config.get("factors", {}), account_id, run_date))

        benchmark_code = str(account.get("benchmark") or "")
        history_codes = candidate_pool.get(
            "code", pd.Series(dtype=str)
        ).astype(str).tolist()
        if benchmark_code:
            history_codes.append(benchmark_code)
        return_history = load_provider_return_history(
            provider,
            history_codes,
            as_of=run_date,
        )
        optimizer_diagnostics: dict[str, object] = {}
        orders = build_target_orders(
            config,
            account_state,
            candidate_pool,
            max_positions=top_n,
            return_history=return_history,
            gross_exposure=float(
                candidate_pool.get("regime_gross_exposure", pd.Series([1.0])).iloc[0]
                if not candidate_pool.empty
                else 1.0
            ),
            benchmark_weights={benchmark_code: 1.0} if benchmark_code else None,
            optimizer_diagnostics=optimizer_diagnostics,
        )
        selected_codes = set(
            str(code).zfill(6)
            for code in optimizer_diagnostics.get("selected_codes", [])
        )
        if candidate_pool.empty or "code" not in candidate_pool.columns:
            selected = candidate_pool.copy()
        else:
            selected = candidate_pool[
                candidate_pool["code"].map(
                    lambda code: str(code).zfill(6) in selected_codes
                )
            ].copy()
        if not selected.empty:
            selected["target_weight"] = selected["code"].map(
                lambda code: optimizer_diagnostics["target_weights"].get(
                    str(code).zfill(6), 0.0
                )
            )
            selected["account_id"] = account_id
            selected["pool"] = account["scope"]
        all_selected.append(selected)

        factor_table = signal.factor_table
        if not factor_table.empty:
            factor_table = factor_table.copy()
            factor_table["signal_date"] = run_date
            factor_table["selected"] = factor_table["code"].map(
                lambda code: str(code).zfill(6) in selected_codes
            )
            scored_by_code = scored.copy()
            scored_by_code["_lineage_code"] = scored_by_code["code"].map(
                lambda code: str(code).split(".", 1)[0].zfill(6)
            )
            scored_by_code = scored_by_code.drop_duplicates(
                "_lineage_code", keep="last"
            ).set_index("_lineage_code")
            factor_codes = factor_table["code"].map(
                lambda code: str(code).split(".", 1)[0].zfill(6)
            )
            for column in (
                "prediction_applied",
                "prediction_confidence",
                "expected_excess_return",
                "prediction_horizons",
                "prediction_model_versions",
                "prediction_fallback_reason",
            ):
                if column in scored_by_code.columns:
                    factor_table[column] = factor_codes.map(
                        scored_by_code[column]
                    )
            all_factor_tables.append(factor_table)

        pending_batches.append(
            {
                "run_id": run_id or f"{config.get('strategy_id', 'strategy')}-{account_id}-{run_date}",
                "strategy_id": config.get("strategy_id", "strategy"),
                "account_id": account_id,
                "scope": account["scope"],
                "signal_date": run_date,
                "execute_after": execute_after,
                "created_at": now_iso(),
                "orders": orders,
                "warnings": warnings,
                "selected_codes": sorted(selected_codes),
                "target_weights": optimizer_diagnostics.get("target_weights", {}),
                "optimizer_diagnostics": optimizer_diagnostics,
            }
        )

    existing = [batch for batch in store.load_pending() if batch.get("signal_date") != run_date]
    store.save_pending(existing + pending_batches)
    if all_selected:
        non_empty_selected = [df for df in all_selected if not df.empty]
        if non_empty_selected:
            store.save_signals(pd.concat(non_empty_selected, ignore_index=True))
    if all_factor_tables:
        factor_snapshot = pd.concat(all_factor_tables, ignore_index=True)
        store.write_factor_snapshot(factor_snapshot, run_id or _fallback_run_id(config, run_date))
    if coverage_rows:
        store.append_factor_coverage(coverage_rows)
    return pending_batches


def _fallback_run_id(config: dict[str, Any], run_date: str) -> str:
    return f"{config.get('strategy_id', 'strategy')}-rebalance-{run_date}"


def _controlled_candidate_pool(
    scored: pd.DataFrame,
    account_state: dict[str, Any],
    config: dict[str, Any],
    top_n: int,
    run_date: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    controls = dict(config.get("portfolio_controls", {}) or {})
    warnings: list[str] = []
    expired_holdings: set[str] = set()
    max_holding_days = max(int(controls.get("max_holding_days") or 0), 0)
    if max_holding_days > 0 and run_date:
        current_date = parse_date(run_date)
        for raw_code, position in (
            account_state.get("positions", {}) or {}
        ).items():
            opened = position.get("last_buy_date") or position.get("hold_since")
            if not opened:
                continue
            try:
                holding_days = (current_date - parse_date(opened)).days
            except (TypeError, ValueError):
                continue
            if holding_days >= max_holding_days:
                code = str(raw_code).zfill(6)
                expired_holdings.add(code)
                warnings.append(f"optimizer_max_holding_exit:{code}")
    multiple = max(int(controls.get("candidate_pool_multiple", 3)), 3)
    required_size = max(int(top_n), 1) * multiple
    ranked = scored.copy()
    if "code" not in ranked.columns:
        ranked["code"] = pd.Series(dtype=str)
    if "score" not in ranked.columns:
        ranked["score"] = pd.Series(dtype=float)
    ranked = ranked.sort_values("score", ascending=False)
    ranked["_allocation_code"] = ranked["code"].map(lambda code: str(code).zfill(6))
    if expired_holdings:
        ranked = ranked.loc[
            ~ranked["_allocation_code"].isin(expired_holdings)
        ].copy()
    pool = ranked.head(required_size)
    prediction_applied = ranked.get(
        "prediction_applied",
        pd.Series(False, index=ranked.index),
    ).fillna(False).astype(bool)
    if bool(prediction_applied.any()) and "base_score" in ranked.columns:
        core_ranked = ranked.sort_values(
            ["base_score", "_allocation_code"],
            ascending=[False, True],
            kind="stable",
        )
        pool = pd.concat(
            [pool, core_ranked.head(required_size)],
            ignore_index=False,
        )
    held_codes = {
        str(code).zfill(6)
        for code in (account_state.get("positions", {}) or {})
    }.difference(expired_holdings)
    if held_codes:
        held_rows = ranked[ranked["_allocation_code"].isin(held_codes)]
        pool = pd.concat([pool, held_rows], ignore_index=False)
    existing_codes = set(pool["_allocation_code"])
    synthetic_rows: list[dict[str, Any]] = []
    minimum_score = pd.to_numeric(ranked["score"], errors="coerce").min()
    if pd.isna(minimum_score):
        minimum_score = 0.0
    for offset, (raw_code, position) in enumerate(
        (account_state.get("positions", {}) or {}).items(),
        start=1,
    ):
        code = str(raw_code).zfill(6)
        if code in expired_holdings:
            continue
        if code in existing_codes:
            continue
        synthetic_rows.append(
            {
                "code": code,
                "name": position.get("name", ""),
                "latest_price": (
                    safe_float(position.get("last_price"))
                    or safe_float(position.get("avg_cost"))
                ),
                "score": float(minimum_score) - offset * 1e-6,
                "score_detail": "held_position_outside_signal_universe",
                "industry": position.get("industry") or UNCLASSIFIED,
                "avg_amount_20": safe_float(position.get("avg_daily_amount")),
                "low_volatility_60": (
                    safe_float(position.get("expected_volatility")) or 0.20
                ),
                "_allocation_code": code,
                "synthetic_holding": True,
            }
        )
    if synthetic_rows:
        pool = pd.DataFrame(
            [*pool.to_dict(orient="records"), *synthetic_rows]
        )
    pool = (
        pool.drop_duplicates("_allocation_code", keep="first")
        .drop(columns=["_allocation_code"])
        .reset_index(drop=True)
    )
    if len(ranked) < required_size:
        warnings.append(f"optimizer_candidate_pool_short:{len(ranked)}/{required_size}")
    warnings.extend(
        f"optimizer_synthetic_holding:{row['code']}"
        for row in synthetic_rows
    )
    return pool, warnings


def _coverage_rows(scored: pd.DataFrame, factors: dict[str, Any], account_id: str, signal_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = max(len(scored), 1)
    for factor in factors:
        if factor not in scored.columns:
            continue
        column = pd.to_numeric(scored[factor], errors="coerce")
        valid = column.dropna()
        rows.append(
            {
                "signal_date": signal_date,
                "account_id": account_id,
                "factor": factor,
                "coverage_pct": round(len(valid) / total, 4),
                "missing_count": int(total - len(valid)),
                "mean": float(valid.mean()) if not valid.empty else None,
                "p5": float(valid.quantile(0.05)) if not valid.empty else None,
                "p50": float(valid.quantile(0.50)) if not valid.empty else None,
                "p95": float(valid.quantile(0.95)) if not valid.empty else None,
                "std": float(valid.std(ddof=0)) if not valid.empty else None,
            }
        )
    return rows


def build_target_orders(
    config: dict[str, Any],
    account_state: dict[str, Any],
    selected: pd.DataFrame,
    *,
    fallback_pool: pd.DataFrame | None = None,
    max_positions: int | None = None,
    return_history: pd.DataFrame | None = None,
    gross_exposure: float = 1.0,
    benchmark_weights: dict[str, float] | None = None,
    optimizer_diagnostics: dict[str, object] | None = None,
    target_weights_override: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build pending buy/sell orders for the next trading day.

    Formal callers pass a controlled candidate pool plus ``max_positions``;
    legacy callers may omit it when ``selected`` is already the approved
    allocation. ``fallback_pool`` is retained only for API compatibility and
    is deliberately ignored: order materialization cannot select a security
    that the allocation stage did not approve. Sizing applies in this order:

      1. Inverse-volatility base weights, tilted only by active prediction
         evidence and blended with current holdings to penalize turnover.
         Every target remains capped by ``max_single_weight``.
      2. ``target_shares = int(target_value // (price × lot_size)) × lot_size``.
      3. **Tier 1 — 1-lot fallback**: if ``target_shares == 0`` but 1 lot
         still fits under the 5% cap (``price × lot_size ≤ cap``), bump to
         ``lot_size``. This rescues stocks priced ¥100-250 that the strict
         equal-weight formula would otherwise drop.
    Stocks priced > ``max_single_value / lot_size`` (¥250 under default
    baseline) are still structurally excluded — buying any of them would
    breach the 5% single-stock cap.
    """
    explicit_max_positions = max_positions is not None
    top_n = max(int(max_positions or len(selected)), 1)
    total_value = account_total_value(account_state)
    max_single_weight = safe_float(config.get("trading", {}).get("max_single_weight"))
    if max_single_weight is not None and max_single_weight > 0:
        max_single_value = total_value * max_single_weight
    else:
        max_single_value = None
        max_single_weight = 1.0
    lot_size = int(config.get("trading", {}).get("lot_size", 100))
    current_positions = account_state.get("positions", {})
    current_weights: dict[str, float] = {}
    if total_value > 0:
        for code, position in current_positions.items():
            market_value = safe_float(position.get("market_value"))
            if market_value is None:
                shares = int(position.get("shares", 0))
                price = safe_float(position.get("last_price")) or safe_float(position.get("avg_cost")) or 0.0
                market_value = shares * price
            current_weights[str(code).zfill(6)] = max(float(market_value), 0.0) / total_value

    controls = config.get("portfolio_controls", {}) or {}
    defensive = str(config.get("agent_id") or "").lower() == "claude"
    turnover_penalty = float(controls.get("turnover_penalty", 0.45 if defensive else 0.20))
    min_trade_weight = float(controls.get("min_trade_weight", 0.003 if defensive else 0.001))
    max_turnover = min(
        max(float(controls.get("max_turnover", 1.0)), 0.0),
        1.0,
    )
    max_industry_weight = float(controls.get("max_industry_weight", 1.0))
    candidates = _attach_liquidity_caps(
        selected,
        total_value,
        float(max_single_weight),
        controls,
    )
    group_constraints = (
        {"industry": max_industry_weight}
        if max_industry_weight < 1.0 and "industry" in candidates.columns
        else None
    )
    solution_diagnostics: dict[str, object] = {}
    if target_weights_override is not None:
        target_weights = {
            str(code).zfill(6): min(
                max(float(weight), 0.0),
                float(max_single_weight),
            )
            for code, weight in target_weights_override.items()
            if float(weight) > 1e-10
        }
        solution_diagnostics["fallback_reason"] = "preapproved_cost_aware_target"
    elif explicit_max_positions:
        target_weights = risk_adjusted_target_weights(
            candidates,
            top_n=top_n,
            max_single_weight=float(max_single_weight),
            current_weights=current_weights,
            turnover_penalty=turnover_penalty,
            min_trade_weight=min_trade_weight,
            return_history=return_history,
            gross_exposure=gross_exposure,
            group_constraints=group_constraints,
            risk_aversion=float(
                controls.get("risk_aversion", 1.35 if defensive else 0.90)
            ),
            cost_aversion=float(controls.get("cost_aversion", 1.0)),
            max_turnover=max_turnover,
            benchmark_weights=benchmark_weights,
            diagnostics=solution_diagnostics,
        )
    else:
        target_weights = _preallocated_target_weights(
            candidates,
            max_single_weight=float(max_single_weight),
            gross_exposure=gross_exposure,
        )
        solution_diagnostics["fallback_reason"] = "legacy_preallocated_input"
    selected_codes = sorted(
        code for code, weight in target_weights.items() if float(weight) > 0.0
    )
    allocation_target_weights = {
        str(code): float(weight)
        for code, weight in target_weights.items()
    }
    for code in current_positions:
        allocation_target_weights.setdefault(str(code).zfill(6), 0.0)
    solution_diagnostics.update(
        {
            "candidate_pool_size": int(len(candidates)),
            "max_positions": top_n,
            "max_turnover_limit": max_turnover,
            "selected_codes": selected_codes,
            "target_weights": allocation_target_weights,
            "liquidity_caps": {
                str(row["code"]).zfill(6): float(row["liquidity_cap"])
                for _, row in candidates.iterrows()
            },
        }
    )
    if optimizer_diagnostics is not None:
        optimizer_diagnostics.clear()
        optimizer_diagnostics.update(solution_diagnostics)

    def _compute_target_shares(price: float, target_weight: float) -> int:
        target_value = total_value * min(max(target_weight, 0.0), float(max_single_weight))
        raw = int(target_value // (price * lot_size)) * lot_size
        if raw == 0 and max_single_value is not None and price * lot_size <= max_single_value:
            # Tier 1: 1 lot fits under cap — buy 1 lot rather than leave slot empty.
            return lot_size
        return raw

    def _make_target(code: str, row, price: float, target_shares: int,
                       *, fallback: bool = False) -> dict[str, Any]:
        base_reason = row.get("score_detail", "") if hasattr(row, "get") else ""
        if fallback:
            base_reason = f"{base_reason};fallback_fill" if base_reason else "fallback_fill"
        return {
            "code": code,
            "name": row.get("name", ""),
            "industry": row.get("industry") or UNCLASSIFIED,
            "target_shares": target_shares,
            "target_value": round(target_shares * price, 2),
            "target_weight": round((target_shares * price / total_value), 6) if total_value else None,
            "reference_price": price,
            "score": row.get("score"),
            "reason": base_reason,
            "avg_daily_amount": safe_float(row.get("avg_amount_20")),
            "expected_volatility": safe_float(
                row.get("expected_volatility", row.get("low_volatility_60"))
            ),
        }

    targets: dict[str, dict[str, Any]] = {}
    for _, row in candidates.iterrows():
        price = safe_float(row.get("latest_price"))
        if price is None or price <= 0:
            continue
        code = str(row["code"]).zfill(6)
        if code not in target_weights or float(target_weights[code]) <= 0.0:
            continue
        target_shares = _compute_target_shares(
            price,
            target_weights[code],
        )
        current_shares = int(current_positions.get(code, {}).get("shares", 0))
        if total_value > 0 and abs(target_shares - current_shares) * price / total_value < min_trade_weight:
            target_shares = current_shares
        targets[code] = _make_target(code, row, price, target_shares)

    for code, position in current_positions.items():
        targets.setdefault(
            code,
            {
                "code": code,
                "name": position.get("name", ""),
                "industry": position.get("industry") or UNCLASSIFIED,
                "target_shares": 0,
                "target_value": 0,
                "target_weight": 0,
                "reference_price": position.get("last_price"),
                "score": None,
                "reason": "not_selected",
            },
        )

    orders = []
    for code, target in sorted(targets.items()):
        current_shares = int(current_positions.get(code, {}).get("shares", 0))
        target_shares = int(target["target_shares"])
        if target_shares == current_shares:
            continue
        side = "buy" if target_shares > current_shares else "sell"
        reference_price = safe_float(target.get("reference_price")) or 0.0
        baseline_bps = float(config.get("trading", {}).get("slippage_rate", 0.0)) * 10_000.0
        estimated_impact_bps = estimate_market_impact_bps(
            order_value=abs(target_shares - current_shares) * reference_price,
            avg_daily_amount=target.get("avg_daily_amount"),
            volatility=target.get("expected_volatility"),
            baseline_bps=baseline_bps,
        )
        orders.append(
            {
                "code": code,
                "name": target.get("name", ""),
                "industry": target.get("industry") or UNCLASSIFIED,
                "side": side,
                "current_shares": current_shares,
                "target_shares": target_shares,
                "delta_shares": target_shares - current_shares,
                "target_value": target.get("target_value"),
                "target_weight": target.get("target_weight"),
                "reference_price": target.get("reference_price"),
                "score": target.get("score"),
                "reason": target.get("reason"),
                "estimated_impact_bps": round(estimated_impact_bps, 4),
                "status": "pending",
            }
        )
    return orders


def _attach_liquidity_caps(
    candidates: pd.DataFrame,
    account_value: float,
    max_single_weight: float,
    controls: dict[str, Any],
) -> pd.DataFrame:
    frame = candidates.copy()
    participation = min(
        max(float(controls.get("max_liquidity_participation", 0.05)), 0.0),
        1.0,
    )
    amount_column = (
        "avg_amount_20"
        if "avg_amount_20" in frame.columns
        else "avg_daily_amount"
        if "avg_daily_amount" in frame.columns
        else None
    )
    frame["liquidity_cap"] = float(max_single_weight)
    if amount_column is not None and account_value > 0 and participation > 0:
        amounts = pd.to_numeric(frame[amount_column], errors="coerce")
        observed_caps = amounts * participation / float(account_value)
        valid = observed_caps.notna() & observed_caps.gt(0.0)
        frame.loc[valid, "liquidity_cap"] = observed_caps.loc[valid].clip(
            upper=float(max_single_weight)
        )
    return frame


def _preallocated_target_weights(
    candidates: pd.DataFrame,
    *,
    max_single_weight: float,
    gross_exposure: float,
) -> dict[str, float]:
    if candidates.empty or "code" not in candidates.columns:
        return {}
    frame = candidates.copy()
    frame["_code"] = frame["code"].map(lambda code: str(code).zfill(6))
    volatility = pd.to_numeric(
        frame.get(
            "expected_volatility",
            frame.get(
                "low_volatility_60",
                pd.Series(0.20, index=frame.index),
            ),
        ),
        errors="coerce",
    ).abs()
    valid = volatility[volatility.gt(0.0) & volatility.notna()]
    fallback_volatility = float(valid.median()) if not valid.empty else 0.20
    volatility = (
        volatility.where(volatility.gt(0.0), fallback_volatility)
        .fillna(fallback_volatility)
        .clip(lower=0.03, upper=1.50)
    )
    utility = 1.0 / volatility
    expected = pd.to_numeric(
        frame.get(
            "expected_excess_return",
            pd.Series(0.0, index=frame.index),
        ),
        errors="coerce",
    ).fillna(0.0)
    confidence = pd.to_numeric(
        frame.get(
            "prediction_confidence",
            pd.Series(0.0, index=frame.index),
        ),
        errors="coerce",
    ).fillna(0.0)
    applied = frame.get(
        "prediction_applied",
        pd.Series(False, index=frame.index),
    ).fillna(False).astype(bool)
    if applied.any():
        alpha_tilt = (expected * confidence / volatility).clip(
            lower=-1.0,
            upper=1.0,
        )
        utility.loc[applied] *= (1.0 + alpha_tilt.loc[applied]).clip(
            lower=0.25,
            upper=2.0,
        )
    cap = min(max(float(max_single_weight), 0.0), 1.0)
    budget = min(
        max(float(gross_exposure), 0.0),
        1.0,
        cap * len(frame),
    )
    weights = pd.Series(0.0, index=frame.index)
    active = pd.Series(True, index=frame.index)
    while active.any() and budget - float(weights.sum()) > 1e-10:
        remaining = budget - float(weights.sum())
        active_utility = utility.where(active, 0.0).clip(lower=0.0)
        if float(active_utility.sum()) <= 0.0:
            active_utility = active.astype(float)
        proposal = active_utility / float(active_utility.sum()) * remaining
        headroom = (cap - weights).clip(lower=0.0)
        increment = pd.concat([proposal, headroom], axis=1).min(axis=1)
        weights += increment
        active &= weights.lt(cap - 1e-10)
        if float(increment.sum()) <= 1e-12:
            break
    return {
        code: float(weight)
        for code, weight in zip(frame["_code"], weights)
        if float(weight) > 1e-10
    }


def execute_due_orders(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: DataProvider,
    as_of: str | date | None = None,
    *,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
) -> list[dict[str, Any]]:
    store = _override_store(store, data_root)
    _override_provider_cache(provider, market_data_root)
    as_of = _resolve_as_of(as_of)
    state = store.initialize(config)
    pending = store.load_pending()
    run_date = as_of or date.today().isoformat()
    remaining: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    for batch in pending:
        if str(batch.get("execute_after", "")) > run_date:
            remaining.append(batch)
            continue
        account = state["accounts"][batch["account_id"]]
        refresh_sellable_positions(account, run_date)
        sells = [order for order in batch.get("orders", []) if order.get("side") == "sell"]
        buys = [order for order in batch.get("orders", []) if order.get("side") == "buy"]
        retained_orders: list[dict[str, Any]] = []
        for order in sells + buys:
            trade = execute_order(config, account, order, provider, batch.get("execute_after"), batch.get("account_id"), run_date)
            if trade:
                trade_rows.append(trade)
            if order.get("status") != "filled":
                retained_orders.append(order)
        if retained_orders:
            updated_batch = dict(batch)
            updated_batch["orders"] = retained_orders
            updated_batch["last_attempt_at"] = now_iso()
            remaining.append(updated_batch)

    store.save_pending(remaining)
    store.save_state(state)
    store.append_trades(trade_rows)
    store.write_positions(state)
    return trade_rows


def execute_order(
    config: dict[str, Any],
    account: dict[str, Any],
    order: dict[str, Any],
    provider: DataProvider,
    execute_after: str,
    account_id: str,
    run_date: str | None = None,
) -> dict[str, Any] | None:
    code = str(order["code"]).zfill(6)
    side = order["side"]
    order["attempts"] = int(order.get("attempts") or 0) + 1
    order["last_attempt_at"] = now_iso()
    target_shares = int(order["target_shares"])
    current = account.get("positions", {}).get(code, {})
    current_shares = int(current.get("shares", 0))
    raw_delta = target_shares - current_shares
    if raw_delta == 0:
        mark_order_filled(order, current_shares)
        return None

    quote = execution_quote(provider, code, execute_after, side, run_date or execute_after)
    if quote.reason:
        mark_order_unfilled(order, quote.reason, current_shares, target_shares)
        return None
    price = quote.price
    trade_date = quote.trade_date or execute_after
    if price is None or price <= 0:
        mark_order_unfilled(order, "execution_price_missing", current_shares, target_shares)
        return None

    trading = config.get("trading", {})
    lot_size = int(trading.get("lot_size", 100))
    slippage_rate = max(
        float(trading.get("slippage_rate", 0)),
        float(order.get("estimated_impact_bps", 0.0) or 0.0) / 10_000.0,
    )
    commission_rate = float(trading.get("commission_rate", 0))
    min_commission = float(trading.get("min_commission", 0))
    stamp_tax_rate = float(trading.get("stamp_tax_rate", 0))
    side_multiplier = 1 + slippage_rate if side == "buy" else 1 - slippage_rate
    execution_price = round(price * side_multiplier, 4)

    if side == "sell":
        available_shares = int(current.get("available_shares", current_shares))
        shares = min(abs(raw_delta), current_shares, available_shares)
        if shares <= 0:
            reason = "no_position" if current_shares <= 0 else "no_sellable_shares"
            mark_order_unfilled(order, reason, current_shares, target_shares)
            return None
    else:
        desired = max(raw_delta, 0)
        shares = (desired // lot_size) * lot_size
        estimated_cost = shares * execution_price
        estimated_fee = max(estimated_cost * commission_rate, min_commission) if shares else 0
        while shares > 0 and estimated_cost + estimated_fee > float(account.get("cash", 0)):
            shares -= lot_size
            estimated_cost = shares * execution_price
            estimated_fee = max(estimated_cost * commission_rate, min_commission) if shares else 0

    if shares <= 0:
        reason = "insufficient_cash" if side == "buy" else "no_sellable_shares"
        mark_order_unfilled(order, reason, current_shares, target_shares)
        return None

    fill = calculate_execution_fill(
        reference_price=price,
        shares=shares,
        side=side,
        trading=trading,
        impact_bps=slippage_rate * 10_000.0,
    )
    execution_price = fill.execution_price
    gross = fill.gross_amount
    commission = fill.commission
    stamp_tax = fill.stamp_tax
    slippage = fill.slippage

    if side == "sell":
        net = gross - commission - stamp_tax
        account["cash"] = float(account.get("cash", 0)) + fill.cash_delta
        new_shares = current_shares - shares
        if new_shares <= 0:
            account.get("positions", {}).pop(code, None)
        else:
            current["shares"] = new_shares
            current["available_shares"] = max(int(current.get("available_shares", new_shares)) - shares, 0)
            current["last_price"] = execution_price
            current["market_value"] = new_shares * execution_price
            account["positions"][code] = current
    else:
        net = gross + commission
        account["cash"] = float(account.get("cash", 0)) + fill.cash_delta
        new_shares = current_shares + shares
        old_cost = float(current.get("avg_cost", execution_price)) * current_shares
        avg_cost = (old_cost + gross + commission) / new_shares
        available_shares = int(current.get("available_shares", current_shares))
        preserved_industry = current.get("industry") or order.get("industry") or UNCLASSIFIED
        preserved_hold_since = current.get("hold_since") or trade_date
        account.setdefault("positions", {})[code] = {
            "name": order.get("name", code),
            "industry": preserved_industry,
            "shares": new_shares,
            "available_shares": min(available_shares, new_shares),
            "avg_cost": round(avg_cost, 4),
            "last_buy_date": trade_date,
            "hold_since": preserved_hold_since,
            "last_price": execution_price,
            "market_value": round(new_shares * execution_price, 2),
            "unrealized_pnl": round((execution_price - avg_cost) * new_shares, 2),
            "score": order.get("score"),
            "reason": order.get("reason", ""),
            "updated_at": now_iso(),
        }

    update_order_progress(order, account, code, target_shares, side, shares)
    return {
        "trade_date": trade_date,
        "account_id": account_id,
        "code": code,
        "name": order.get("name", code),
        "side": side,
        "shares": shares,
        "price": execution_price,
        "gross_amount": round(gross, 2),
        "commission": round(commission, 2),
        "stamp_tax": round(stamp_tax, 2),
        "slippage": round(slippage, 2),
        "net_amount": round(net, 2),
        "cash_after": round(float(account.get("cash", 0)), 2),
        "reason": order.get("reason", ""),
    }


def update_nav(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: DataProvider,
    as_of: str | date | None = None,
    notes: str = "",
    *,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
) -> list[dict[str, Any]]:
    store = _override_store(store, data_root)
    _override_provider_cache(provider, market_data_root)
    as_of = _resolve_as_of(as_of)
    state = store.initialize(config)
    run_date = as_of or date.today().isoformat()
    rows: list[dict[str, Any]] = []
    for account_id, account in state.get("accounts", {}).items():
        market_value = 0.0
        for code, position in account.get("positions", {}).items():
            snapshot = provider.price_snapshot(code, as_of=as_of)
            price = snapshot.close or safe_float(position.get("last_price")) or safe_float(position.get("avg_cost")) or 0
            shares = int(position.get("shares", 0))
            position["last_price"] = price
            position["market_value"] = round(shares * price, 2)
            position["unrealized_pnl"] = round((price - float(position.get("avg_cost", price))) * shares, 2)
            position["updated_at"] = now_iso()
            market_value += shares * price
        benchmark_close, benchmark_date = provider.benchmark_close(account.get("benchmark"), as_of=as_of)
        rows.append(
            {
                "date": run_date,
                "account_id": account_id,
                "cash": round(float(account.get("cash", 0)), 2),
                "market_value": round(market_value, 2),
                "total_value": round(float(account.get("cash", 0)) + market_value, 2),
                "benchmark_code": account.get("benchmark"),
                "benchmark_close": benchmark_close,
                "benchmark_date": benchmark_date,
                "notes": notes,
            }
        )
    store.save_state(state)
    store.append_nav(rows)
    store.write_positions(state)
    return rows


def account_total_value(account_state: dict[str, Any]) -> float:
    cash = float(account_state.get("cash", 0))
    market_value = sum(float(position.get("market_value", 0)) for position in account_state.get("positions", {}).values())
    return cash + market_value


def execution_quote(provider: DataProvider, code: str, execute_after: str, side: str, run_date: str) -> ExecutionQuote:
    if hasattr(provider, "execution_quote"):
        return provider.execution_quote(code, execute_after, side, as_of=run_date)
    price, trade_date = provider.execution_price(code, execute_after, side)
    reason = "" if price else "execution_quote_missing"
    return ExecutionQuote(code=code, trade_date=trade_date, price=price, reason=reason)


def refresh_sellable_positions(account: dict[str, Any], run_date: str) -> None:
    for position in account.get("positions", {}).values():
        shares = int(position.get("shares", 0))
        last_buy_date = str(position.get("last_buy_date") or "")
        if not last_buy_date or last_buy_date < run_date:
            position["available_shares"] = shares
        else:
            position["available_shares"] = min(int(position.get("available_shares", 0)), shares)


def mark_order_unfilled(order: dict[str, Any], reason: str, current_shares: int, target_shares: int) -> None:
    order["status"] = "pending"
    order["unfilled_reason"] = reason
    order["current_shares"] = current_shares
    order["target_shares"] = target_shares
    order["delta_shares"] = target_shares - current_shares


def mark_order_filled(order: dict[str, Any], current_shares: int) -> None:
    order["status"] = "filled"
    order["unfilled_reason"] = ""
    order["current_shares"] = current_shares
    order["delta_shares"] = 0


def update_order_progress(
    order: dict[str, Any],
    account: dict[str, Any],
    code: str,
    target_shares: int,
    side: str,
    filled_shares: int,
) -> None:
    current_shares = int(account.get("positions", {}).get(code, {}).get("shares", 0))
    delta = target_shares - current_shares
    order["current_shares"] = current_shares
    order["delta_shares"] = delta
    order["last_filled_shares"] = filled_shares
    if delta == 0:
        mark_order_filled(order, current_shares)
        return
    order["status"] = "partial"
    if side == "buy" and delta > 0:
        order["unfilled_reason"] = "insufficient_cash"
    elif side == "sell" and delta < 0:
        order["unfilled_reason"] = "no_sellable_shares"
    else:
        order["unfilled_reason"] = "partial_fill"
