from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a config file and apply v1→v2 migration in place.

    The default config is JSON syntax stored in a .yaml file so the project has
    no YAML parser dependency. If users later write real YAML, PyYAML is used
    when installed.
    """

    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                f"{config_path} is not JSON-compatible YAML. Install PyYAML or keep JSON syntax."
            ) from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"{config_path} must contain a mapping at the top level")
    migrate_strategy_config(data)
    return data


def account_ids(config: dict[str, Any]) -> list[str]:
    return [str(account["id"]) for account in config.get("accounts", [])]


def migrate_strategy_config(config: dict[str, Any]) -> list[str]:
    """Mutate a strategy config to v2 shape and return applied migration tags.

    - Demote `market_cap_yi` from `factors` to `filters.min_market_cap_yi` if present.
    - Inject defaults for `factor_processing`, `portfolio_controls`, and `performance`.
    """

    applied: list[str] = []
    factors = config.setdefault("factors", {})
    if "market_cap_yi" in factors:
        factors.pop("market_cap_yi", None)
        filters = config.setdefault("filters", {})
        filters.setdefault("min_market_cap_yi", 30)
        applied.append("config_v1_market_cap_demoted")

    fp = config.setdefault("factor_processing", {})
    fp.setdefault("enabled", True)
    fp.setdefault("winsorize_lower", 0.01)
    fp.setdefault("winsorize_upper", 0.99)
    fp.setdefault("neutralize_industry", True)
    fp.setdefault("min_factor_coverage", 0.6)

    pc = config.setdefault("portfolio_controls", {})
    pc.setdefault("max_industry_weight", 0.30)
    pc.setdefault("hold_buffer_pct", 0.5)
    pc.setdefault("max_holding_days", 60)
    pc.setdefault("industry_unclassified_label", "未分类")

    perf = config.setdefault("performance", {})
    perf.setdefault("risk_free_rate", 0.02)
    perf.setdefault("trading_days_per_year", 252)
    perf.setdefault("forward_ic_horizon_days", 5)
    perf.setdefault("low_coverage_threshold", 0.5)

    config.setdefault("version", 2)
    if applied:
        config.setdefault("_migration_notes", []).extend(applied)
    return applied


def config_hash(config: dict[str, Any]) -> str:
    """Stable 12-char hash of the resolved config."""

    payload = canonical_json(config)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def canonical_json(config: dict[str, Any]) -> str:
    """JSON serialization with sorted keys, used for hashing snapshots."""

    return json.dumps(_strip_runtime_keys(config), ensure_ascii=False, sort_keys=True, default=str)


def _strip_runtime_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _strip_runtime_keys(value) for key, value in obj.items() if not str(key).startswith("_")}
    if isinstance(obj, list):
        return [_strip_runtime_keys(item) for item in obj]
    return obj
