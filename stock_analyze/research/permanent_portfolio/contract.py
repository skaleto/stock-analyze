"""Frozen configuration and lifecycle rules for permanent-portfolio research."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ...config import load_config


STUDY_ID = "permanent_portfolio_v1"
SUPPORTED_STUDY_IDS = frozenset({STUDY_ID, "permanent_portfolio_v2"})
ASSET_ROLES = frozenset({"equity", "bond", "cash", "gold"})
STATE_ORDER = (
    "draft",
    "development_sealed",
    "development_complete",
    "holdout_opened",
    "holdout_complete",
    "forward_ready",
)


def _date_key(value: Any) -> str:
    key = str(value).replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"permanent_portfolio_date:{value}")
    return key


@dataclass(frozen=True)
class AssetSpec:
    role: str
    code: str
    name: str


@dataclass(frozen=True)
class PermanentPortfolioContract:
    study_id: str
    accounting_version: str
    evidence_class: str
    source_start: str
    development_start: str
    development_end: str
    holdout_start: str
    initial_cash: float
    assets: tuple[AssetSpec, ...]
    lower_band: float
    upper_band: float
    fixed_target_weight: float
    dynamic_rank_weights: tuple[float, ...]
    tie_break_order: tuple[str, ...]
    lot_size: int
    commission_rate: float
    minimum_commission: float
    slippage_rate: float
    stamp_tax_rate: float
    raw: Mapping[str, Any]

    def assert_development_date(self, value: str) -> None:
        key = _date_key(value)
        if not self.development_start <= key <= self.development_end:
            raise ValueError(f"permanent_portfolio_development_window:{key}")


def canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_contract(path: str | Path) -> PermanentPortfolioContract:
    raw = load_config(path, apply_migrations=False)
    assets = tuple(
        AssetSpec(
            role=str(item["role"]),
            code=str(item["code"]),
            name=str(item["name"]),
        )
        for item in raw["assets"]
    )
    fixed = raw["fixed"]
    dynamic = raw["dynamic"]
    trading = raw["trading"]
    contract = PermanentPortfolioContract(
        study_id=str(raw["study_id"]),
        accounting_version=str(
            raw.get("accounting_version") or "adjusted_valuation_v1"
        ),
        evidence_class=str(raw.get("evidence_class") or "original_v1"),
        source_start=_date_key(raw["source_start"]),
        development_start=_date_key(raw["development_start"]),
        development_end=_date_key(raw["development_end"]),
        holdout_start=_date_key(raw["holdout_start"]),
        initial_cash=float(raw["initial_cash"]),
        assets=assets,
        lower_band=float(fixed["lower_band"]),
        upper_band=float(fixed["upper_band"]),
        fixed_target_weight=float(fixed["target_weight"]),
        dynamic_rank_weights=tuple(
            float(value) for value in dynamic["rank_weights"]
        ),
        tie_break_order=tuple(
            str(value) for value in dynamic["tie_break_order"]
        ),
        lot_size=int(trading["lot_size"]),
        commission_rate=float(trading["commission_rate"]),
        minimum_commission=float(trading["minimum_commission"]),
        slippage_rate=float(trading["slippage_rate"]),
        stamp_tax_rate=float(trading["stamp_tax_rate"]),
        raw=raw,
    )
    if contract.study_id not in SUPPORTED_STUDY_IDS:
        raise ValueError("permanent_portfolio_study_id")
    expected_accounting = (
        "cash_distributions_v2"
        if contract.study_id == "permanent_portfolio_v2"
        else "adjusted_valuation_v1"
    )
    if contract.accounting_version != expected_accounting:
        raise ValueError("permanent_portfolio_accounting_version")
    if len(assets) != 4 or {asset.role for asset in assets} != ASSET_ROLES:
        raise ValueError("permanent_portfolio_assets")
    if len({asset.code for asset in assets}) != len(assets):
        raise ValueError("permanent_portfolio_asset_codes")
    if not 0 < contract.lower_band < contract.upper_band < 1:
        raise ValueError("permanent_portfolio_fixed_bands")
    if abs(contract.fixed_target_weight * len(assets) - 1.0) > 1e-12:
        raise ValueError("permanent_portfolio_fixed_weights")
    if (
        len(contract.dynamic_rank_weights) != len(assets)
        or abs(sum(contract.dynamic_rank_weights) - 1.0) > 1e-12
        or min(contract.dynamic_rank_weights) < 0.10
        or max(contract.dynamic_rank_weights) > 0.40
    ):
        raise ValueError("permanent_portfolio_dynamic_weights")
    if set(contract.tie_break_order) != ASSET_ROLES:
        raise ValueError("permanent_portfolio_tie_break")
    if contract.development_end >= contract.holdout_start:
        raise ValueError("permanent_portfolio_window_overlap")
    return contract


def transition_state(
    state: Mapping[str, Any],
    target: str,
    *,
    expected_from: str,
) -> dict[str, Any]:
    current = str(state.get("status") or "draft")
    if (
        current != expected_from
        or target not in STATE_ORDER
        or STATE_ORDER.index(target) != STATE_ORDER.index(current) + 1
    ):
        raise ValueError(f"permanent_portfolio_state:{current}:{target}")
    return {**state, "status": target}
