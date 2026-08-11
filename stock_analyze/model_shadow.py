"""Isolated, version-pinned model iteration paper portfolio.

The account consumes point-in-time prediction artifacts but writes only under
``data/model_iterations/<market>/<horizon>/<version>``. It deliberately does
not participate in agent discovery, competition scoring, or strategy evolution.

The old ``model_shadow`` names remain as internal compatibility aliases.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from . import competition
from .model_iteration import (
    REQUIRED_SHADOW_CYCLES,
    ensure_iteration_candidate,
    iteration_portfolio_dir,
    iteration_prediction_dir,
    read_model_registry,
)
from .research.strategy_ensemble import (
    apply_cost_aware_transition,
    load_provider_return_history,
    risk_adjusted_target_weights,
)
from .run_ledger import RunLedger
from .store import PortfolioStore
from .utils import next_business_day, now_iso, read_json, safe_float, write_json


MODEL_SHADOW_CONFIG = Path("configs/model_shadow.json")
MODEL_SHADOW_AGENT = "model_shadow"
MODEL_ITERATION_LABEL = "模型迭代"
MODEL_ITERATION_PORTFOLIO_LABEL = "候选模型模拟组合"
MODEL_SHADOW_LABEL = MODEL_ITERATION_LABEL
TRUE_VALUES = frozenset({"1", "true", "yes", "y"})


def _decision_fingerprint(selected: pd.DataFrame) -> str:
    columns = [
        column
        for column in (
            "code",
            "account_id",
            "horizon",
            "model_version",
            "confidence",
            "p_up",
            "p_down",
            "expected_excess_return",
            "target_weight",
        )
        if column in selected.columns
    ]
    if not columns:
        return "empty"
    payload = (
        selected.loc[:, columns]
        .sort_values(
            [column for column in ("account_id", "code") if column in columns],
            kind="stable",
        )
        .to_json(orient="records", double_precision=15)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def shadow_data_dir(repo_root: str | Path, market: str) -> Path:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    return Path(repo_root) / "data" / MODEL_SHADOW_AGENT / market


def load_shadow_profile(repo_root: str | Path, market: str) -> dict[str, Any]:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    path = Path(repo_root) / MODEL_SHADOW_CONFIG
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("model_shadow_config") from exc
    market_profile = (payload.get("markets") or {}).get(market)
    if not isinstance(market_profile, dict):
        raise ValueError(f"model_shadow_market_config:{market}")
    profile = {
        "version": int(payload.get("version", 1)),
        "source_agent": str(payload.get("source_agent") or "codex"),
        "minimum_confidence": float(payload.get("minimum_confidence", 0.55)),
        "turnover_penalty": float(payload.get("turnover_penalty", 0.35)),
        "min_trade_weight": float(payload.get("min_trade_weight", 0.005)),
        **market_profile,
        "market": market,
    }
    accounts = [dict(account) for account in profile.get("accounts") or []]
    if not accounts and profile.get("account_id"):
        accounts = [{
            "id": str(profile["account_id"]),
            "name": str(profile.get("account_name") or MODEL_SHADOW_LABEL),
            "scope": str(profile.get("scope") or "model_long_only"),
            "benchmark": str(profile.get("benchmark") or ""),
            "cash": float(profile.get("initial_cash") or 0.0),
            "top_n": int(profile.get("top_n") or 1),
        }]
    profile["accounts"] = accounts
    required = {"horizon", "initial_cash", "max_single_weight", "accounts"}
    if required.difference(profile):
        raise ValueError(f"model_shadow_profile_schema:{market}")
    account_fields = {"id", "scope", "benchmark", "cash", "top_n"}
    if (
        not accounts
        or any(account_fields.difference(account) for account in accounts)
        or any(int(account["top_n"]) <= 0 or float(account["cash"]) <= 0 for account in accounts)
        or not 0 < float(profile["max_single_weight"]) <= 1
    ):
        raise ValueError(f"model_shadow_profile_limits:{market}")
    if abs(sum(float(account["cash"]) for account in accounts) - float(profile["initial_cash"])) > 0.01:
        raise ValueError(f"model_shadow_profile_cash:{market}")
    profile["account_id"] = str(accounts[0]["id"]) if len(accounts) == 1 else "multi_scope"
    profile["top_n"] = max(int(account["top_n"]) for account in accounts)
    return profile


def latest_prediction_path(
    repo_root: str | Path,
    market: str,
    as_of: str,
    *,
    source_agent: str = "codex",
) -> Path:
    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    cutoff = str(as_of).replace("-", "")[:8]
    directory = Path(repo_root) / "data" / market / source_agent / "predictions"
    paths = sorted(
        path
        for path in directory.glob("*.parquet")
        if len(path.stem) == 8 and path.stem.isdigit() and path.stem <= cutoff
    )
    if not paths:
        raise FileNotFoundError(
            f"model_shadow_prediction_missing:{market}:as_of={cutoff}"
        )
    return paths[-1]


def latest_iteration_prediction_path(
    repo_root: str | Path,
    market: str,
    horizon: int,
    model_version: str,
    as_of: str,
) -> Path:
    cutoff = str(as_of).replace("-", "")[:8]
    directory = iteration_prediction_dir(
        repo_root,
        market,
        horizon,
        model_version,
    )
    paths = sorted(
        path
        for path in directory.glob("*.parquet")
        if len(path.stem) == 8 and path.stem.isdigit() and path.stem <= cutoff
    )
    if not paths:
        raise FileNotFoundError(
            f"model_iteration_prediction_missing:{market}:{horizon}:{model_version}:as_of={cutoff}"
        )
    return paths[-1]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return str(value).strip().lower() in TRUE_VALUES


def _normalise_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    digits = raw.split(".", 1)[0]
    return digits.zfill(6) if digits.isdigit() else raw


def build_model_candidates(
    predictions: pd.DataFrame,
    profile: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Filter and rank long-only candidates from one prediction snapshot."""

    required = {
        "code",
        "horizon",
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
        "return_q10",
        "return_q90",
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"model_shadow_prediction_schema:{','.join(missing)}")
    frame = predictions.copy()
    source_rows = len(frame)
    for column in (
        "horizon",
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
        "return_q10",
        "return_q90",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    horizon = int(profile["horizon"])
    frame = frame.loc[frame["horizon"].eq(horizon)].copy()
    invalidated = (
        frame["invalidated"].map(_truthy)
        if "invalidated" in frame.columns
        else pd.Series(False, index=frame.index)
    )
    minimum_confidence = float(profile.get("minimum_confidence", 0.55))
    valid = ~invalidated
    confident = valid & frame["confidence"].ge(minimum_confidence)
    direction = confident & frame["p_up"].gt(frame["p_down"])
    eligible = direction & frame["expected_excess_return"].gt(0.0)
    selected = frame.loc[eligible].copy()
    if not selected.empty:
        predicted_span = (selected["return_q90"] - selected["return_q10"]).abs()
        selected["expected_volatility"] = (predicted_span / (2.0 * 1.281551565545)).clip(
            lower=0.005,
            upper=0.50,
        )
        spread = selected["p_up"] - selected["p_down"]
        risk_adjusted_alpha = (
            selected["expected_excess_return"] / selected["expected_volatility"]
        )
        selected["score"] = selected["confidence"] * (
            risk_adjusted_alpha + 0.25 * spread
        )
        selected["code"] = selected["code"].map(_normalise_code)
        selected["prediction_confidence"] = selected["confidence"]
        selected["prediction_applied"] = True
        selected["score_detail"] = selected.apply(
            lambda row: (
                f"模型{horizon}日：上涨{float(row['p_up']):.0%}，"
                f"下跌{float(row['p_down']):.0%}，"
                f"预期超额{float(row['expected_excess_return']):+.2%}，"
                f"可信度{float(row['confidence']):.0%}"
            ),
            axis=1,
        )
        selected["reason"] = selected["score_detail"]
        selected = (
            selected.sort_values(["score", "confidence"], ascending=False)
            .drop_duplicates("code", keep="first")
            .reset_index(drop=True)
        )
    near_misses: list[dict[str, Any]] = []
    rejected = frame.loc[valid & ~eligible].copy()
    if not rejected.empty:
        rejected["probability_spread"] = rejected["p_up"] - rejected["p_down"]
        rejected = rejected.sort_values(
            ["expected_excess_return", "probability_spread", "confidence"],
            ascending=False,
        )
        for raw in rejected.head(5).to_dict(orient="records"):
            confidence = float(raw["confidence"])
            p_up = float(raw["p_up"])
            p_down = float(raw["p_down"])
            expected = float(raw["expected_excess_return"])
            failed_rules: list[str] = []
            if confidence < minimum_confidence:
                failed_rules.append(f"置信度低于{minimum_confidence:.0%}")
            if p_up <= p_down:
                failed_rules.append("下跌概率不低于上涨概率")
            if expected <= 0.0:
                failed_rules.append("预期超额收益不高于0")
            near_misses.append(
                {
                    "code": _normalise_code(raw.get("code")),
                    "confidence": confidence,
                    "p_up": p_up,
                    "p_down": p_down,
                    "expected_excess_return": expected,
                    "failed_rules": failed_rules,
                }
            )
    regime = "unknown"
    if "regime" in frame.columns:
        regimes = frame["regime"].dropna().astype(str)
        if not regimes.empty:
            regime = str(regimes.value_counts().index[0])
    diagnostics = {
        "source_rows": int(source_rows),
        "horizon_rows": int(len(frame)),
        "invalidated_rows": int(invalidated.sum()),
        "eligible_rows": int(len(selected)),
        "minimum_confidence": minimum_confidence,
        "horizon": horizon,
        "regime": regime,
        "funnel": [
            {"key": "predictions", "label": "预测证券", "count": int(len(frame))},
            {"key": "valid", "label": "数据有效", "count": int(valid.sum())},
            {"key": "confidence", "label": "置信度达标", "count": int(confident.sum())},
            {"key": "direction", "label": "上涨概率占优", "count": int(direction.sum())},
            {"key": "positive_excess", "label": "预期超额为正", "count": int(eligible.sum())},
        ],
        "near_misses": near_misses,
    }
    return selected, diagnostics


def _decision_diagnostics_status(
    diagnostics: Mapping[str, Any],
    selected: pd.DataFrame,
    *,
    name_lookup: Mapping[str, str] | None,
) -> dict[str, Any]:
    funnel = [dict(stage) for stage in diagnostics.get("funnel") or []]
    counts = {str(stage.get("key")): int(stage.get("count") or 0) for stage in funnel}
    near_misses: list[dict[str, Any]] = []
    for raw in diagnostics.get("near_misses") or []:
        row = dict(raw)
        code = _normalise_code(row.get("code"))
        row["code"] = code
        row["name"] = str((name_lookup or {}).get(code) or code)
        near_misses.append(row)
    selected_count = int(len(selected))
    if selected_count:
        qualified_count = counts.get(
            "scope_eligible", counts.get("positive_excess", selected_count)
        )
        summary = (
            f"{qualified_count}只证券满足模型与可投资范围条件，"
            f"最终选择{selected_count}只进入候选模拟组合"
        )
    elif counts.get("positive_excess", 0) and not counts.get("scope_eligible", 0):
        summary = "模型条件通过的证券均不在配置可投资范围，候选模拟组合保持现金"
    elif counts.get("confidence", 0) and not counts.get("direction", 0):
        summary = (
            f"{counts['confidence']}只证券通过置信度门槛，但下跌概率均不低于上涨概率，"
            "候选模拟组合保持现金"
        )
    elif counts.get("direction", 0) and not counts.get("positive_excess", 0):
        summary = (
            f"{counts['direction']}只证券上涨概率占优，但预期超额收益均不高于0，"
            "候选模拟组合保持现金"
        )
    else:
        summary = "本期没有证券通过模型做多门槛，候选模拟组合保持现金"
    return {
        "outcome": "selected" if selected_count else "cash",
        "summary": summary,
        "regime": str(diagnostics.get("regime") or "unknown"),
        "funnel": funnel,
        "near_misses": near_misses,
    }


def synthetic_config(profile: Mapping[str, Any]) -> dict[str, Any]:
    market = str(profile["market"])
    trading = dict(profile.get("trading") or {})
    trading["max_single_weight"] = float(profile["max_single_weight"])
    return {
        "competition_id": f"model-iteration-{market}",
        "strategy_id": f"model_iteration_{market}_v{int(profile.get('version', 1))}",
        "agent_id": MODEL_SHADOW_AGENT,
        "name": MODEL_SHADOW_LABEL,
        "initial_cash": float(profile["initial_cash"]),
        "accounts": [dict(account) for account in profile.get("accounts") or []],
        "trading": trading,
        "portfolio_controls": {
            "turnover_penalty": float(profile.get("turnover_penalty", 0.35)),
            "min_trade_weight": float(profile.get("min_trade_weight", 0.005)),
        },
    }


def _pending_count(pending: list[dict[str, Any]]) -> int:
    return sum(
        len(item.get("orders") or [])
        if isinstance(item, dict) and isinstance(item.get("orders"), list)
        else int(isinstance(item, dict))
        for item in pending
    )


def _current_weights(
    state: dict[str, Any],
    account_id: str,
    provider: Any,
    as_of: str,
) -> dict[str, float]:
    account = state.get("accounts", {}).get(account_id, {})
    values: dict[str, float] = {}
    total = max(float(account.get("cash", 0.0)), 0.0)
    for code, position in account.get("positions", {}).items():
        quote = provider.price_snapshot(code, as_of=as_of)
        price = safe_float(getattr(quote, "close", None)) or safe_float(
            position.get("avg_cost")
        ) or 0.0
        value = max(int(position.get("shares", 0)) * price, 0.0)
        values[_normalise_code(code)] = value
        total += value
    if total <= 0:
        return {}
    return {code: value / total for code, value in values.items()}


def _default_round_trip_cost_bps(profile: Mapping[str, Any]) -> float:
    trading = dict(profile.get("trading") or {})
    baseline_bps = (
        float(trading.get("slippage_bps") or 0.0)
        if trading.get("slippage_bps") is not None
        else float(trading.get("slippage_rate") or 0.0) * 10_000.0
    )
    return (
        2.0 * baseline_bps
        + 2.0 * float(trading.get("commission_rate") or 0.0) * 10_000.0
        + float(trading.get("stamp_tax_rate") or 0.0) * 10_000.0
    )


def _materialize_transition_selection(
    pool: pd.DataFrame,
    *,
    weights: Mapping[str, float],
    account: Mapping[str, Any],
    state: Mapping[str, Any],
    provider: Any,
    as_of: str,
    market: str,
) -> pd.DataFrame:
    frame = pool.copy()
    if not frame.empty:
        frame["target_weight"] = frame["code"].map(
            lambda code: float(weights.get(_normalise_code(code), 0.0))
        )
    known_codes = set(frame.get("code", pd.Series(dtype=str)).map(_normalise_code))
    account_state = (state.get("accounts") or {}).get(str(account["id"]), {})
    additions: list[dict[str, Any]] = []
    for code, weight in weights.items():
        if code in known_codes or float(weight) <= 1e-10:
            continue
        position = (account_state.get("positions") or {}).get(code, {})
        quote = provider.price_snapshot(code, as_of=as_of)
        price = safe_float(getattr(quote, "close", None)) or safe_float(
            position.get("last_price")
        ) or safe_float(position.get("avg_cost"))
        if price is None or price <= 0.0:
            continue
        metadata: dict[str, Any] = {}
        if market == "cn_qdii_etf":
            from .markets.cn_qdii_etf.universe import metadata_for_code

            metadata = metadata_for_code(code)
        additions.append({
            **metadata,
            "code": code,
            "name": str(position.get("name") or getattr(quote, "name", "") or code),
            "industry": str(position.get("industry") or "模型组合"),
            "account_id": str(account["id"]),
            "research_scope": str(account.get("scope") or account["id"]),
            "latest_price": price,
            "score": None,
            "reason": "cost_aware_retained_holding",
            "target_weight": float(weight),
        })
    if additions:
        frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True, sort=False)
    if frame.empty:
        return frame
    return frame.loc[
        pd.to_numeric(frame["target_weight"], errors="coerce").fillna(0.0).gt(1e-10)
    ].copy()


def _enrich_candidates(
    candidates: pd.DataFrame,
    *,
    profile: Mapping[str, Any],
    provider: Any,
    as_of: str,
    name_lookup: Mapping[str, str] | None,
    account: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    account = dict(account or {})
    top_n = int(account.get("top_n") or profile["top_n"])
    rows: list[dict[str, Any]] = []
    for raw in candidates.head(max(top_n * 4, top_n)).to_dict(orient="records"):
        code = _normalise_code(raw.get("code"))
        quote = provider.price_snapshot(code, as_of=as_of)
        price = safe_float(getattr(quote, "close", None))
        if price is None or price <= 0:
            continue
        quote_name = str(getattr(quote, "name", "") or "").strip()
        name = quote_name or str((name_lookup or {}).get(code) or code)
        market_metadata: dict[str, Any] = {}
        if str(profile.get("market") or "") == "cn_qdii_etf":
            from .markets.cn_qdii_etf.universe import metadata_for_code

            market_metadata = metadata_for_code(code)
        rows.append(
            {
                **market_metadata,
                **raw,
                "code": code,
                "name": name,
                "industry": "模型组合",
                "account_id": str(account.get("id") or profile["account_id"]),
                "latest_price": price,
            }
        )
    return pd.DataFrame(rows)


def _dimension_value(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _route_candidates_to_accounts(
    candidates: pd.DataFrame,
    *,
    market: str,
    accounts: list[dict[str, Any]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Map candidates to configured accounts and reject scope leakage."""

    account_to_scope = {
        str(account["id"]): str(account.get("scope") or account["id"])
        for account in accounts
    }
    scope_to_account = {
        scope: account_id for account_id, scope in account_to_scope.items()
    }
    accepted: list[dict[str, Any]] = []
    rejected_scopes: dict[str, int] = {}
    rejected_codes: list[str] = []
    routing_sources: dict[str, int] = {}
    for raw in candidates.to_dict(orient="records"):
        row = dict(raw)
        code = _normalise_code(row.get("code"))
        explicit_account = _dimension_value(row.get("account_id"))
        explicit_scope = _dimension_value(row.get("research_scope"))
        account_id = ""
        scope = ""
        source = ""

        if explicit_scope in scope_to_account:
            mapped_account = scope_to_account[explicit_scope]
            if explicit_account in account_to_scope and explicit_account != mapped_account:
                source = "conflicting_account_scope"
            else:
                account_id = mapped_account
                scope = explicit_scope
                source = "research_scope"
        elif explicit_scope:
            source = "unsupported_scope"
        elif explicit_account in account_to_scope:
            account_id = explicit_account
            scope = account_to_scope[explicit_account]
            source = "account_id"
        elif explicit_account:
            source = "unsupported_account"
        elif market == "cn_qdii_etf":
            from .markets.cn_qdii_etf.universe import metadata_for_code

            inferred_scope = _dimension_value(metadata_for_code(code).get("scope"))
            if inferred_scope in scope_to_account:
                account_id = scope_to_account[inferred_scope]
                scope = inferred_scope
                source = "etf_metadata"
            else:
                explicit_scope = inferred_scope
                source = "metadata_scope_unavailable"
        else:
            source = "scope_missing"

        if not account_id:
            rejected_scope = explicit_scope or explicit_account or source
            rejected_scopes[rejected_scope] = rejected_scopes.get(rejected_scope, 0) + 1
            if len(rejected_codes) < 20:
                rejected_codes.append(code)
            continue
        row["code"] = code
        row["account_id"] = account_id
        row["research_scope"] = scope
        accepted.append(row)
        routing_sources[source] = routing_sources.get(source, 0) + 1

    routed = pd.DataFrame(accepted) if accepted else candidates.iloc[0:0].copy()
    for column in ("account_id", "research_scope"):
        if column not in routed.columns:
            routed[column] = pd.Series(dtype="string")
    return routed, {
        "source_rows": int(len(candidates)),
        "eligible_rows": int(len(routed)),
        "rejected_rows": int(len(candidates) - len(routed)),
        "rejected_scopes": dict(sorted(rejected_scopes.items())),
        "rejected_codes": rejected_codes,
        "routing_sources": dict(sorted(routing_sources.items())),
    }


def _selected_status_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    fields = (
        "account_id",
        "code",
        "name",
        "score",
        "target_weight",
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
        "gross_expected_edge_bps",
        "round_trip_cost_bps",
        "uncertainty_bps",
        "net_expected_edge_bps",
        "trade_allowed",
        "no_trade_reason",
        "partial_adjustment_rate",
        "model_version",
        "reason",
    )
    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        row: dict[str, Any] = {}
        for field in fields:
            value = raw.get(field)
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, float) and not math.isfinite(value):
                value = None
            row[field] = value
        rows.append(row)
    return rows


def run_shadow_cycle(
    *,
    market: str,
    profile: Mapping[str, Any],
    store: PortfolioStore,
    provider: Any,
    predictions: pd.DataFrame,
    as_of: str,
    prediction_as_of: str,
    run_id: str,
    name_lookup: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one idempotent mark-to-market and model decision cycle."""

    if market not in competition.MARKETS:
        raise competition.UnknownMarket(market)
    config = synthetic_config(profile)
    accounts = [dict(account) for account in profile.get("accounts") or []]
    account_id = "multi_scope" if len(accounts) > 1 else str(accounts[0]["id"])
    market_module = competition.get_market_module(market)
    if not store.state_path.exists():
        market_module.initialize(config, store)

    trades = market_module.execute_due_orders(
        config,
        store,
        provider,
        as_of=as_of,
    )
    nav_rows = market_module.update_nav(
        config,
        store,
        provider,
        as_of=as_of,
        notes=f"model shadow; trades={len(trades)}",
    )
    candidates, diagnostics = build_model_candidates(predictions, profile)
    candidates, scope_routing = _route_candidates_to_accounts(
        candidates,
        market=market,
        accounts=accounts,
    )
    diagnostics["funnel"] = [
        *(diagnostics.get("funnel") or []),
        {
            "key": "scope_eligible",
            "label": "可投资范围内",
            "count": scope_routing["eligible_rows"],
        },
    ]
    selected_parts: list[pd.DataFrame] = []
    account_optimizer_diagnostics: dict[str, dict[str, Any]] = {}
    cost_aware_decisions: list[dict[str, Any]] = []
    state_snapshot = store.load_state()
    for account in accounts:
        scoped_candidates = candidates.loc[
            candidates["account_id"].astype(str).eq(str(account["id"]))
        ]
        scoped_pool = _enrich_candidates(
            scoped_candidates,
            profile=profile,
            provider=provider,
            as_of=as_of,
            name_lookup=name_lookup,
            account=account,
        )
        if scoped_pool.empty:
            continue
        scoped_selected = scoped_pool.head(max(int(account["top_n"]) * 3, 3)).copy()
        company_exposure_constraints: dict[str, float] = {}
        company_exposure_metadata: dict[str, Any] = {}
        if market == "cn_qdii_etf":
            from .markets.cn_qdii_etf.run import (
                _attach_underlying_company_exposures,
                _underlying_company_diagnostics,
            )

            (
                scoped_selected,
                company_exposure_constraints,
                company_exposure_metadata,
            ) = _attach_underlying_company_exposures(scoped_selected, dict(profile))
        benchmark = str(account.get("benchmark") or "")
        history_codes = scoped_selected["code"].astype(str).tolist()
        if benchmark:
            history_codes.append(benchmark)
        return_history = load_provider_return_history(
            provider,
            history_codes,
            as_of=as_of,
        )
        group_constraints: dict[str, float] = {}
        if market == "cn_qdii_etf":
            for column, key, default in (
                ("index_key", "max_index_weight", 0.40),
                ("country", "max_country_weight", 0.60),
            ):
                cap = float(profile.get(key, default))
                if column in scoped_selected.columns and cap < 1.0:
                    group_constraints[column] = cap
        optimizer_diagnostics: dict[str, object] = {}
        current_weights = _current_weights(
            state_snapshot, str(account["id"]), provider, as_of
        )
        aim_weights = risk_adjusted_target_weights(
            scoped_selected,
            top_n=int(account["top_n"]),
            max_single_weight=float(profile["max_single_weight"]),
            current_weights=current_weights,
            return_history=return_history,
            benchmark_weights={benchmark: 1.0} if benchmark else None,
            group_constraints=group_constraints,
            exposure_constraints=company_exposure_constraints,
            turnover_penalty=float(profile.get("turnover_penalty", 0.35)),
            min_trade_weight=float(profile.get("min_trade_weight", 0.005)),
            risk_aversion=float(profile.get("risk_aversion", 1.0)),
            cost_aversion=float(profile.get("cost_aversion", 1.0)),
            max_turnover=float(profile.get("max_turnover", 1.0)),
            diagnostics=optimizer_diagnostics,
        )
        transition = apply_cost_aware_transition(
            scoped_selected,
            aim_weights=aim_weights,
            current_weights=current_weights,
            top_n=int(account["top_n"]),
            rank_buffer_pct=float(profile.get("rank_buffer_pct", 0.0)),
            minimum_target_change=float(profile.get("minimum_target_change", 0.0)),
            partial_adjustment_rate=float(profile.get("partial_adjustment_rate", 1.0)),
            max_daily_turnover=float(profile.get("max_daily_turnover", 1.0)),
            cost_safety_multiple=float(profile.get("cost_safety_multiple", 1.0)),
            alpha_persistence=float(profile.get("alpha_persistence", 1.0)),
            default_round_trip_cost_bps=_default_round_trip_cost_bps(profile),
            gross_exposure=1.0 - float(profile.get("cash_reserve_pct", 0.0)),
        )
        for raw in transition.decisions.to_dict(orient="records"):
            cost_aware_decisions.append({**raw, "account_id": str(account["id"])})
        scoped_selected = _materialize_transition_selection(
            scoped_selected,
            weights=transition.weights,
            account=account,
            state=state_snapshot,
            provider=provider,
            as_of=as_of,
            market=market,
        )
        if not scoped_selected.empty and not transition.decisions.empty:
            decision_columns = transition.decisions.drop(
                columns=["rank", "current_weight", "aim_weight", "target_weight"],
                errors="ignore",
            ).copy()
            scoped_selected = scoped_selected.merge(
                decision_columns,
                on="code",
                how="left",
                validate="many_to_one",
            )
        account_diagnostics: dict[str, Any] = {
            **optimizer_diagnostics,
            **company_exposure_metadata,
            "execution_policy_version": "cost-aware-aim-v1",
            "cost_aware_decision_count": int(len(transition.decisions)),
            "cost_aware_trade_count": int(
                transition.decisions.get(
                    "trade_allowed", pd.Series(dtype=bool)
                ).fillna(False).astype(bool).sum()
            ),
        }
        if market == "cn_qdii_etf":
            account_diagnostics.update(
                _underlying_company_diagnostics(
                    scoped_selected,
                    transition.weights,
                    optimizer_diagnostics,
                )
            )
        account_optimizer_diagnostics[str(account["id"])] = account_diagnostics
        selected_parts.append(scoped_selected)
    selected = (
        pd.concat(selected_parts, ignore_index=True, sort=False)
        if selected_parts else pd.DataFrame()
    )
    model_versions = sorted(
        {
            str(value)
            for value in predictions.loc[
                pd.to_numeric(predictions["horizon"], errors="coerce").eq(
                    int(profile["horizon"])
                ),
                "model_version",
            ].dropna().tolist()
        }
    ) if "model_version" in predictions.columns else []
    decision_key = "|".join(
        [
            market,
            str(prediction_as_of),
            str(profile["horizon"]),
            ",".join(model_versions),
            _decision_fingerprint(selected),
        ]
    )
    previous_status = read_json(store.data_dir / "shadow_status.json", {})
    decision_changed = previous_status.get("decision_key") != decision_key

    if decision_changed:
        previous_pending = store.load_pending()
        store.save_pending([])
        try:
            if market == "a_share":
                from .markets.a_share.simulator import build_target_orders

                state = store.load_state()
                execute_after = (
                    provider.next_trading_day(as_of)
                    if hasattr(provider, "next_trading_day")
                    else next_business_day(as_of)
                )
                pending_batches = []
                for account in accounts:
                    scoped = selected.loc[
                        selected.get("account_id", pd.Series(dtype=str)).astype(str).eq(str(account["id"]))
                    ]
                    benchmark = str(account.get("benchmark") or "")
                    history_codes = scoped.get(
                        "code", pd.Series(dtype=str)
                    ).astype(str).tolist()
                    if benchmark:
                        history_codes.append(benchmark)
                    return_history = load_provider_return_history(
                        provider,
                        history_codes,
                        as_of=as_of,
                    )
                    orders = build_target_orders(
                        config,
                        state["accounts"][str(account["id"])],
                        scoped,
                        max_positions=int(account["top_n"]),
                        return_history=return_history,
                        benchmark_weights=(
                            {benchmark: 1.0} if benchmark else None
                        ),
                        target_weights_override={
                            _normalise_code(row["code"]): float(row["target_weight"])
                            for row in scoped.to_dict(orient="records")
                            if safe_float(row.get("target_weight")) is not None
                        },
                    )
                    if orders:
                        pending_batches.append({
                            "run_id": run_id,
                            "signal_date": prediction_as_of,
                            "execute_after": execute_after,
                            "account_id": str(account["id"]),
                            "warnings": [],
                            "orders": orders,
                        })
                if pending_batches:
                    store.save_pending(pending_batches)
            else:
                from .markets.cn_qdii_etf import simulator as etf_simulator

                etf_simulator.generate_rebalance_orders(
                    store,
                    provider,
                    selected.to_dict(orient="records"),
                    as_of=date.fromisoformat(as_of),
                    top_n=int(profile["top_n"]),
                    max_single_weight=float(profile["max_single_weight"]),
                    top_n_by_account={
                        str(account["id"]): int(account["top_n"])
                        for account in accounts
                    },
                    cash_reserve_pct=float(profile.get("cash_reserve_pct", 0.0)),
                    min_trade_weight=float(profile.get("min_trade_weight", 0.005)),
                )
        except Exception:
            store.save_pending(previous_pending)
            raise

    store.save_signals(selected)
    pending_orders = _pending_count(store.load_pending())
    decision_diagnostics = _decision_diagnostics_status(
        diagnostics,
        selected,
        name_lookup=name_lookup,
    )
    latest_nav_by_account = {
        str(row.get("account_id") or ""): dict(row)
        for row in nav_rows
        if str(row.get("account_id") or "")
    }
    status = {
        "schema_version": 1,
        "market": market,
        "account_id": account_id,
        "label": MODEL_ITERATION_PORTFOLIO_LABEL,
        "isolation": "完全隔离，不计入双策略竞赛",
        "source_agent": str(profile.get("source_agent") or "codex"),
        "source_type": "point_in_time_prediction",
        "as_of": as_of,
        "prediction_as_of": prediction_as_of,
        "horizon": int(profile["horizon"]),
        "model_versions": model_versions,
        "decision_key": decision_key,
        "decision_changed": decision_changed,
        "candidate_rows": diagnostics["source_rows"],
        "model_eligible_rows": diagnostics["eligible_rows"],
        "eligible_rows": scope_routing["eligible_rows"],
        "scope_rejected_rows": scope_routing["rejected_rows"],
        "scope_routing": scope_routing,
        "selected_count": int(len(selected)),
        "invalidated_rows": diagnostics["invalidated_rows"],
        "minimum_confidence": diagnostics["minimum_confidence"],
        "cash_only": bool(selected.empty),
        "cash_reason": (
            "模型未发现满足条件的上行机会" if selected.empty else None
        ),
        "decision_diagnostics": decision_diagnostics,
        "execution_policy_version": "cost-aware-aim-v1",
        "cost_aware_decisions": cost_aware_decisions,
        "trades_executed": int(len(trades)),
        "pending_orders": pending_orders,
        "nav_rows": int(len(nav_rows)),
        "selected": _selected_status_rows(selected),
        "accounts": [
            {
                "account_id": str(account["id"]),
                "scope": str(account["scope"]),
                "benchmark": str(account["benchmark"]),
                "selected_count": int(
                    selected.get("account_id", pd.Series(dtype=str)).astype(str).eq(str(account["id"])).sum()
                ),
                "optimizer_diagnostics": account_optimizer_diagnostics.get(
                    str(account["id"]), {}
                ),
                **{
                    key: latest_nav_by_account.get(
                        str(account["id"]), {}
                    ).get(key)
                    for key in (
                        "date",
                        "cash",
                        "market_value",
                        "total_value",
                        "benchmark_close",
                    )
                },
            }
            for account in accounts
        ],
        "run_id": run_id,
        "updated_at": now_iso(),
    }
    write_json(store.data_dir / "shadow_status.json", status)
    return status


def _a_share_name_lookup(repo_root: Path, as_of: str) -> dict[str, str]:
    cutoff = as_of.replace("-", "")[:8]
    directory = repo_root / "data" / "shared" / "cache"
    paths = sorted(
        path
        for path in directory.glob("spot_*.csv")
        if path.stem.removeprefix("spot_") <= cutoff
    )
    if not paths:
        return {}
    try:
        frame = pd.read_csv(paths[-1], dtype={"code": str, "name": str})
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return {}
    if not {"code", "name"}.issubset(frame.columns):
        return {}
    return {
        _normalise_code(row["code"]): str(row["name"])
        for row in frame[["code", "name"]].dropna().to_dict(orient="records")
    }


def _current_status_path(root: Path, market: str, horizon: int) -> Path:
    return (
        root
        / "data"
        / "model_iterations"
        / market
        / str(int(horizon))
        / "current_status.json"
    )


def _write_current_status(
    root: Path,
    market: str,
    horizon: int,
    status: dict[str, Any],
) -> dict[str, Any]:
    write_json(_current_status_path(root, market, horizon), status)
    return status


def run_model_iteration(
    *,
    repo_root: str | Path,
    market: str,
    as_of: str,
    offline: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root)
    profile = load_shadow_profile(root, market)
    horizon = int(profile["horizon"])
    candidate = ensure_iteration_candidate(
        root,
        market,
        horizon,
        as_of=as_of,
    )
    if candidate is None:
        registry = read_model_registry(root, market, horizon)
        return _write_current_status(root, market, horizon, {
            "schema_version": 2,
            "status": "no_candidate",
            "market": market,
            "horizon": horizon,
            "label": MODEL_ITERATION_LABEL,
            "portfolio_label": MODEL_ITERATION_PORTFOLIO_LABEL,
            "champion_model_version": registry.get("champion_model_version"),
            "cash_only": True,
            "cash_reason": "当前没有待验证的候选模型",
            "updated_at": now_iso(),
        })
    model_version = str(candidate["model_version"])
    try:
        prediction_path = latest_iteration_prediction_path(
            root,
            market,
            horizon,
            model_version,
            as_of,
        )
    except FileNotFoundError:
        return _write_current_status(root, market, horizon, {
            "schema_version": 2,
            "status": "prediction_missing",
            "market": market,
            "horizon": horizon,
            "label": MODEL_ITERATION_LABEL,
            "portfolio_label": MODEL_ITERATION_PORTFOLIO_LABEL,
            "model_version": model_version,
            "display_version": candidate["display_version"],
            "lifecycle_status": candidate["status"],
            "lifecycle_status_label": candidate["status_label"],
            "champion_model_version": candidate.get("champion_model_version"),
            "cash_only": True,
            "cash_reason": "候选模型预测尚未生成，未使用正式模型替代",
            "updated_at": now_iso(),
        })
    predictions = pd.read_parquet(prediction_path)
    prediction_as_of = next(
        (
            str(value)
            for value in reversed(predictions.get("as_of", pd.Series(dtype=str)).tolist())
            if str(value).strip()
        ),
        f"{prediction_path.stem[:4]}-{prediction_path.stem[4:6]}-{prediction_path.stem[6:]}",
    )
    data_dir = iteration_portfolio_dir(root, market, horizon, model_version)
    store = PortfolioStore(data_dir)
    cache_dir = (
        root / "data" / "shared" / "cache"
        if market == "a_share"
        else root / "data" / market / "shared" / "cache"
    )
    market_module = competition.get_market_module(market)
    provider = market_module.make_provider(
        cache_dir=cache_dir,
        offline=offline,
        as_of=as_of,
    )
    config = synthetic_config(profile)
    ledger = RunLedger(data_dir)
    try:
        with ledger.run("run-model-iteration", as_of, config) as context:
            result = run_shadow_cycle(
                market=market,
                profile=profile,
                store=store,
                provider=provider,
                predictions=predictions,
                as_of=as_of,
                prediction_as_of=prediction_as_of,
                run_id=context["run_id"],
                name_lookup=(
                    _a_share_name_lookup(root, as_of) if market == "a_share" else None
                ),
            )
            status = {
                **result,
                "schema_version": 2,
                "status": "complete",
                "label": MODEL_ITERATION_LABEL,
                "portfolio_label": MODEL_ITERATION_PORTFOLIO_LABEL,
                "model_version": model_version,
                "display_version": candidate["display_version"],
                "lifecycle_status": candidate["status"],
                "lifecycle_status_label": candidate["status_label"],
                "champion_model_version": candidate.get("champion_model_version"),
                "shadow_cycles": candidate.get("shadow_cycles", 0),
                "shadow_cycles_remaining": candidate.get(
                    "shadow_cycles_remaining", REQUIRED_SHADOW_CYCLES
                ),
                "prediction_path": str(prediction_path),
                "portfolio_path": str(data_dir),
                "updated_at": now_iso(),
            }
            write_json(data_dir / "shadow_status.json", status)
            return _write_current_status(root, market, horizon, status)
    finally:
        persist_health = getattr(provider, "persist_health", None)
        if callable(persist_health):
            persist_health()


def run_model_shadow(
    *,
    repo_root: str | Path,
    market: str,
    as_of: str,
    offline: bool = True,
) -> dict[str, Any]:
    """Compatibility alias for operators and older systemd deployments."""

    return run_model_iteration(
        repo_root=repo_root,
        market=market,
        as_of=as_of,
        offline=offline,
    )


__all__ = [
    "MODEL_SHADOW_AGENT",
    "MODEL_SHADOW_LABEL",
    "MODEL_ITERATION_LABEL",
    "MODEL_ITERATION_PORTFOLIO_LABEL",
    "build_model_candidates",
    "latest_prediction_path",
    "latest_iteration_prediction_path",
    "load_shadow_profile",
    "run_model_iteration",
    "run_model_shadow",
    "run_shadow_cycle",
    "shadow_data_dir",
    "synthetic_config",
]
