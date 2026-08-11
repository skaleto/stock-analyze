"""Isolated, version-pinned model iteration paper portfolio.

The account consumes point-in-time prediction artifacts but writes only under
``data/model_iterations/<market>/<horizon>/<version>``. It deliberately does
not participate in agent discovery, competition scoring, or strategy evolution.

The old ``model_shadow`` names remain as internal compatibility aliases.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from . import competition
from .model_iteration import (
    ensure_iteration_candidate,
    iteration_portfolio_dir,
    iteration_prediction_dir,
    read_model_registry,
)
from .research.strategy_ensemble import risk_adjusted_target_weights
from .run_ledger import RunLedger
from .store import PortfolioStore
from .utils import next_business_day, now_iso, read_json, safe_float, write_json


MODEL_SHADOW_CONFIG = Path("configs/model_shadow.json")
MODEL_SHADOW_AGENT = "model_shadow"
MODEL_ITERATION_LABEL = "模型迭代"
MODEL_ITERATION_PORTFOLIO_LABEL = "候选模型模拟组合"
MODEL_SHADOW_LABEL = MODEL_ITERATION_LABEL
TRUE_VALUES = frozenset({"1", "true", "yes", "y"})


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
    required = {
        "horizon",
        "initial_cash",
        "account_id",
        "benchmark",
        "top_n",
        "max_single_weight",
    }
    if required.difference(profile):
        raise ValueError(f"model_shadow_profile_schema:{market}")
    if int(profile["top_n"]) <= 0 or not 0 < float(profile["max_single_weight"]) <= 1:
        raise ValueError(f"model_shadow_profile_limits:{market}")
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
    eligible = (
        ~invalidated
        & frame["confidence"].ge(minimum_confidence)
        & frame["p_up"].gt(frame["p_down"])
        & frame["expected_excess_return"].gt(0.0)
    )
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
    diagnostics = {
        "source_rows": int(source_rows),
        "horizon_rows": int(len(frame)),
        "invalidated_rows": int(invalidated.sum()),
        "eligible_rows": int(len(selected)),
        "minimum_confidence": minimum_confidence,
        "horizon": horizon,
    }
    return selected, diagnostics


def synthetic_config(profile: Mapping[str, Any]) -> dict[str, Any]:
    market = str(profile["market"])
    account_id = str(profile["account_id"])
    trading = dict(profile.get("trading") or {})
    trading["max_single_weight"] = float(profile["max_single_weight"])
    return {
        "competition_id": f"model-iteration-{market}",
        "strategy_id": f"model_iteration_{market}_v{int(profile.get('version', 1))}",
        "agent_id": MODEL_SHADOW_AGENT,
        "name": MODEL_SHADOW_LABEL,
        "initial_cash": float(profile["initial_cash"]),
        "accounts": [
            {
                "id": account_id,
                "name": str(profile.get("account_name") or MODEL_SHADOW_LABEL),
                "scope": "model_long_only",
                "benchmark": str(profile["benchmark"]),
                "cash": float(profile["initial_cash"]),
                "top_n": int(profile["top_n"]),
            }
        ],
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


def _enrich_candidates(
    candidates: pd.DataFrame,
    *,
    profile: Mapping[str, Any],
    provider: Any,
    as_of: str,
    name_lookup: Mapping[str, str] | None,
) -> pd.DataFrame:
    top_n = int(profile["top_n"])
    rows: list[dict[str, Any]] = []
    for raw in candidates.head(max(top_n * 4, top_n)).to_dict(orient="records"):
        code = _normalise_code(raw.get("code"))
        quote = provider.price_snapshot(code, as_of=as_of)
        price = safe_float(getattr(quote, "close", None))
        if price is None or price <= 0:
            continue
        quote_name = str(getattr(quote, "name", "") or "").strip()
        name = quote_name or str((name_lookup or {}).get(code) or code)
        rows.append(
            {
                **raw,
                "code": code,
                "name": name,
                "industry": "模型组合",
                "account_id": str(profile["account_id"]),
                "latest_price": price,
            }
        )
    return pd.DataFrame(rows)


def _selected_status_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    fields = (
        "code",
        "name",
        "score",
        "target_weight",
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
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
    account_id = str(profile["account_id"])
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
    selected = _enrich_candidates(
        candidates,
        profile=profile,
        provider=provider,
        as_of=as_of,
        name_lookup=name_lookup,
    ).head(int(profile["top_n"]))
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
        ]
    )
    previous_status = read_json(store.data_dir / "shadow_status.json", {})
    decision_changed = previous_status.get("decision_key") != decision_key

    if market == "cn_qdii_etf":
        weights = risk_adjusted_target_weights(
            selected,
            top_n=int(profile["top_n"]),
            max_single_weight=float(profile["max_single_weight"]),
            current_weights=_current_weights(
                store.load_state(), account_id, provider, as_of
            ),
            turnover_penalty=float(profile.get("turnover_penalty", 0.35)),
            min_trade_weight=float(profile.get("min_trade_weight", 0.005)),
        )
        if not selected.empty:
            selected = selected.copy()
            selected["target_weight"] = selected["code"].map(
                lambda code: weights.get(_normalise_code(code), 0.0)
            )

    if decision_changed:
        previous_pending = store.load_pending()
        store.save_pending([])
        try:
            if market == "a_share":
                from .markets.a_share.simulator import build_target_orders

                state = store.load_state()
                orders = build_target_orders(
                    config,
                    state["accounts"][account_id],
                    selected,
                )
                if orders:
                    execute_after = (
                        provider.next_trading_day(as_of)
                        if hasattr(provider, "next_trading_day")
                        else next_business_day(as_of)
                    )
                    store.save_pending(
                        [
                            {
                                "run_id": run_id,
                                "signal_date": prediction_as_of,
                                "execute_after": execute_after,
                                "account_id": account_id,
                                "warnings": [],
                                "orders": orders,
                            }
                        ]
                    )
            else:
                from .markets.cn_qdii_etf import simulator as etf_simulator

                etf_simulator.generate_rebalance_orders(
                    store,
                    provider,
                    selected.to_dict(orient="records"),
                    as_of=date.fromisoformat(as_of),
                    top_n=int(profile["top_n"]),
                    max_single_weight=float(profile["max_single_weight"]),
                    top_n_by_account={account_id: int(profile["top_n"])},
                    cash_reserve_pct=float(profile.get("cash_reserve_pct", 0.0)),
                    min_trade_weight=float(profile.get("min_trade_weight", 0.005)),
                )
        except Exception:
            store.save_pending(previous_pending)
            raise

    store.save_signals(selected)
    pending_orders = _pending_count(store.load_pending())
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
        "eligible_rows": diagnostics["eligible_rows"],
        "selected_count": int(len(selected)),
        "invalidated_rows": diagnostics["invalidated_rows"],
        "minimum_confidence": diagnostics["minimum_confidence"],
        "cash_only": bool(selected.empty),
        "cash_reason": (
            "模型未发现满足条件的上行机会" if selected.empty else None
        ),
        "trades_executed": int(len(trades)),
        "pending_orders": pending_orders,
        "nav_rows": int(len(nav_rows)),
        "selected": _selected_status_rows(selected),
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
                "shadow_cycles_remaining": candidate.get("shadow_cycles_remaining", 4),
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
