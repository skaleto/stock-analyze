"""Bounded, provider-free Dashboard resource for permanent portfolios."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .research.permanent_portfolio.contract import canonical_hash


ARTIFACT_RELATIVE = Path(
    "reports/research/permanent_portfolio/v1/dashboard.json"
)
ROW_LIMITS = {
    "series": 800,
    "trades": 500,
    "targets": 8,
    "positions": 8,
    "pending": 8,
}
DEFAULT_LIST_LIMIT = 100
RESPONSE_LIMIT_BYTES = 750_000
PORTFOLIO_IDS = {
    "fixed",
    "dynamic",
    "equity_buy_hold",
    "equal_weight_buy_hold",
    "cash_buy_hold",
}
METRIC_KEYS = {
    "cumulative_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe_vs_cash",
    "sortino_vs_cash",
    "max_drawdown",
    "max_drawdown_duration",
    "calmar",
    "positive_month_ratio",
    "annualized_turnover",
    "trade_count",
    "total_cost",
    "cost_bps",
}
SERIES_KEYS = {"date", "normalized_nav", "drawdown", "volatility_63d"}
TRADE_KEYS = {
    "signal_date",
    "trade_date",
    "strategy",
    "role",
    "code",
    "side",
    "shares",
    "price",
    "gross_amount",
    "commission",
    "stamp_tax",
    "slippage",
    "net_amount",
    "cash_after",
    "reason",
}
NAV_KEYS = {"date", "cash", "market_value", "total_value", "strategy"}
POSITION_KEYS = {
    "strategy",
    "role",
    "code",
    "shares",
    "last_price",
    "market_value",
}
TARGET_KEYS = {"strategy", "role", "signal_date", "target_weight"}
PENDING_KEYS = {
    "strategy",
    "role",
    "signal_date",
    "target_weight",
    "reason",
}
STAGE_BOUNDARY_KEYS = {"date", "before_label", "after_label"}
ASSETS = (
    {"role": "equity", "code": "510300.SH", "name": "沪深300ETF"},
    {"role": "bond", "code": "511260.SH", "name": "十年国债ETF"},
    {"role": "cash", "code": "511880.SH", "name": "银华日利ETF"},
    {"role": "gold", "code": "518880.SH", "name": "黄金ETF"},
)
STRATEGIES = (
    {"id": "fixed", "name": "固定永久组合"},
    {"id": "dynamic", "name": "动态永久组合"},
)
BENCHMARKS = (
    {"id": "equity_buy_hold", "name": "沪深300买入持有"},
    {"id": "equal_weight_buy_hold", "name": "四资产等权买入持有"},
    {"id": "cash_buy_hold", "name": "现金ETF买入持有"},
)


def _safe(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(item_key): _safe(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        limit = ROW_LIMITS.get(key or "", DEFAULT_LIST_LIMIT)
        return [_safe(item) for item in value[-limit:]]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (int, bool)):
        return value
    return str(value)[:500]


def _sample_rows(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    if len(value) <= limit:
        return [_safe(item) for item in value]
    if limit == 1:
        return [_safe(value[-1])]
    indexes = {
        round(index * (len(value) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [_safe(value[index]) for index in sorted(indexes)]


def _safe_records(value: Any, *, keys: set[str], limit: int) -> list[Any]:
    rows = _sample_rows(value, limit)
    return [
        {
            key: _safe(row[key], key=key)
            for key in keys
            if isinstance(row, dict) and key in row
        }
        for row in rows
        if isinstance(row, dict)
    ]


def _safe_recent_records(
    value: Any,
    *,
    keys: set[str],
    limit: int,
) -> list[Any]:
    if not isinstance(value, list):
        return []
    return _safe_records(value[-limit:], keys=keys, limit=limit)


def _safe_window(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: _safe(value[key], key=key)
        for key in ("status", "start_date", "end_date", "cost_multiplier")
        if key in value
    }
    portfolios = value.get("portfolios")
    result["stage_boundaries"] = _safe_recent_records(
        value.get("stage_boundaries"),
        keys=STAGE_BOUNDARY_KEYS,
        limit=4,
    )
    if not isinstance(portfolios, dict):
        return result
    public_portfolios: dict[str, Any] = {}
    for name, portfolio in portfolios.items():
        if str(name) not in PORTFOLIO_IDS or not isinstance(portfolio, dict):
            continue
        raw_metrics = portfolio.get("metrics")
        item: dict[str, Any] = {
            "metrics": {
                key: _safe(raw_metrics[key], key=key)
                for key in METRIC_KEYS
                if isinstance(raw_metrics, dict) and key in raw_metrics
            },
            "trades": _safe_recent_records(
                portfolio.get("trades"),
                keys=TRADE_KEYS,
                limit=ROW_LIMITS["trades"],
            ),
        }
        if str(name) in PORTFOLIO_IDS:
            item["series"] = _safe_records(
                portfolio.get("series"),
                keys=SERIES_KEYS,
                limit=ROW_LIMITS["series"],
            )
        if str(name) in {"fixed", "dynamic"}:
            item["nav"] = _safe_recent_records(
                portfolio.get("nav"),
                keys=NAV_KEYS,
                limit=1,
            )
            item["positions"] = _safe_recent_records(
                portfolio.get("positions"),
                keys=POSITION_KEYS,
                limit=ROW_LIMITS["positions"],
            )
            item["targets"] = _safe_recent_records(
                portfolio.get("targets"),
                keys=TARGET_KEYS,
                limit=ROW_LIMITS["targets"],
            )
            item["pending"] = _safe_recent_records(
                portfolio.get("pending"),
                keys=PENDING_KEYS,
                limit=ROW_LIMITS["pending"],
            )
        public_portfolios[str(name)] = item
    result["portfolios"] = public_portfolios
    return result


def _unavailable(error: str) -> dict[str, Any]:
    payload = {
        "schemaVersion": 1,
        "generatedAt": None,
        "status": "unavailable",
        "study": {
            "studyId": "permanent_portfolio_v1",
            "status": "unavailable",
            "initialCash": 200000.0,
        },
        "assets": list(ASSETS),
        "strategies": [],
        "benchmarks": list(BENCHMARKS),
        "windows": {
            "historical": {"status": "unavailable"},
            "forward": {"status": "unavailable"},
        },
        "errors": [error],
    }
    return payload


def build_dashboard_permanent_portfolio_data(
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root or ".").resolve()
    path = root / ARTIFACT_RELATIVE
    if not path.is_file():
        return _unavailable("artifact_missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _unavailable("artifact_unreadable")
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return _unavailable("artifact_schema")
    unsigned = dict(raw)
    recorded_dashboard_sha256 = unsigned.pop("dashboard_sha256", None)
    if (
        not isinstance(recorded_dashboard_sha256, str)
        or canonical_hash(unsigned) != recorded_dashboard_sha256
    ):
        return _unavailable("artifact_checksum")

    study = raw.get("study")
    if not isinstance(study, dict):
        return _unavailable("study_missing")
    unsigned_study = dict(study)
    recorded_state_sha256 = unsigned_study.pop("state_sha256", None)
    if (
        not isinstance(recorded_state_sha256, str)
        or canonical_hash(unsigned_study) != recorded_state_sha256
    ):
        return _unavailable("study_checksum")
    status = str(study.get("status") or "unavailable")
    historical = raw.get("historical")
    forward = raw.get("forward")
    windows: dict[str, Any] = {
        "historical": (
            {"status": "unavailable"}
            if not isinstance(historical, dict)
            else {"status": "complete", **_safe_window(historical)}
        ),
        "forward": (
            {"status": "unavailable"}
            if not isinstance(forward, dict)
            else _safe_window(forward)
        ),
    }
    public_study = {
        "studyId": "permanent_portfolio_v1",
        "status": status,
        "initialCash": 200000.0,
        "contractSha256": study.get("contract_sha256"),
        "dataSha256": study.get("market_bundle_sha256"),
        "developmentSha256": study.get("development_sha256"),
        "holdoutSha256": study.get("holdout_sha256"),
        "holdoutEnd": study.get("holdout_end"),
        "forwardAsOf": study.get("forward_as_of"),
    }
    payload = {
        "schemaVersion": 1,
        "generatedAt": raw.get("generated_at"),
        "status": "available",
        "study": _safe(public_study),
        "assets": list(ASSETS),
        "strategies": list(STRATEGIES),
        "benchmarks": list(BENCHMARKS),
        "windows": windows,
        "errors": [],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > RESPONSE_LIMIT_BYTES:
        return _unavailable("artifact_too_large")
    return payload
