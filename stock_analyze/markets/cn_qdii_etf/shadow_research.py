"""Research-only shadow portfolios for global equity, commodity, and bond QDII."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .capacity_study import CapacityStudyError, run_capacity_study
from .research_panel import ResearchPanelResult


FACTOR_MODELS: dict[str, dict[str, dict[str, Any]]] = {
    "global_equity_v1": {
        "momentum_20": {"weight": 0.30, "direction": "high"},
        "momentum_60": {"weight": 0.30, "direction": "high"},
        "low_volatility_60": {"weight": 0.20, "direction": "low"},
        "avg_amount_20": {"weight": 0.15, "direction": "high"},
        "discount_premium": {"weight": 0.05, "direction": "low"},
    },
    "commodity_v1": {
        "momentum_20": {"weight": 0.25, "direction": "high"},
        "momentum_60": {"weight": 0.25, "direction": "high"},
        "low_volatility_60": {"weight": 0.20, "direction": "low"},
        "avg_amount_20": {"weight": 0.15, "direction": "high"},
        "premium_persistence_20": {"weight": 0.15, "direction": "low"},
    },
    "bond_v1": {
        "momentum_60": {"weight": 0.20, "direction": "high"},
        "nav_momentum_20": {"weight": 0.20, "direction": "high"},
        "low_volatility_60": {"weight": 0.30, "direction": "low"},
        "avg_amount_20": {"weight": 0.15, "direction": "high"},
        "premium_persistence_20": {"weight": 0.15, "direction": "low"},
    },
}


@dataclass(frozen=True)
class ShadowResearchResult:
    run_id: str
    catalog: pd.DataFrame
    metrics: pd.DataFrame
    selections: pd.DataFrame
    trades: pd.DataFrame
    nav: pd.DataFrame
    summary: dict[str, Any]


def _overlay(model: str) -> dict[str, Any]:
    return {
        "agent_id": "research",
        "strategy_id": model,
        "name": model,
        "factors": FACTOR_MODELS[model],
        "factor_processing": {
            "winsorize_lower": 0.02,
            "winsorize_upper": 0.98,
            "neutralize_industry": False,
            "min_factor_coverage": 0.5,
        },
        "portfolio_controls": {"max_etfs_per_index": 1, "hold_buffer_pct": 0.25, "max_holding_days": 60},
        "filters": {
            "max_fetch_candidates": 30,
            "min_listing_days": 60,
            "min_avg_amount_20_yuan": 500_000,
            "max_abs_premium": 0.12,
            "min_fund_size_yuan": 0,
            "max_management_fee_pct": 2.0,
        },
    }


def _baseline(scope: str, benchmark: str, top_n: int) -> dict[str, Any]:
    return {
        "initial_cash": 500_000,
        "accounts": [{"id": scope, "scope": scope, "cash": 500_000, "top_n": top_n, "benchmark": benchmark}],
        "trading": {"commission_rate": 0.0003, "slippage_bps": 5, "max_single_weight": min(0.98 / max(top_n, 1), 0.50), "lot_size_default": 100},
    }


def run_shadow_research(
    panel: ResearchPanelResult,
    catalog: pd.DataFrame,
    *,
    start: str,
    end: str,
    min_signal_weeks: int = 12,
) -> ShadowResearchResult:
    metric_frames: list[pd.DataFrame] = []
    selections: list[pd.DataFrame] = []
    trades: list[pd.DataFrame] = []
    nav: list[pd.DataFrame] = []
    skipped: list[dict[str, str]] = []
    for scope, rows in catalog.groupby("research_scope", sort=True):
        asset_class = str(rows.iloc[0]["asset_class"])
        model = {"global_equity": "global_equity_v1", "commodity": "commodity_v1", "bond": "bond_v1"}[asset_class]
        scope_panel = panel.frame.loc[panel.frame["scope"].astype(str).eq(str(scope))].copy()
        if scope_panel.empty:
            skipped.append({"scope": str(scope), "reason": "history_unavailable"})
            continue
        ranked_catalog = rows.sort_values(["list_date", "code"], na_position="last")
        benchmark = str(ranked_catalog.iloc[0]["code"])
        top_n = min(3, max(int(rows["code"].nunique()), 1))
        try:
            result = run_capacity_study(
                ResearchPanelResult(scope_panel, panel.metadata),
                overlays={model: _overlay(model)},
                baseline=_baseline(str(scope), benchmark, top_n),
                top_ns=[top_n],
                start=start,
                end=end,
                min_signal_weeks=min_signal_weeks,
            )
        except CapacityStudyError as exc:
            skipped.append({"scope": str(scope), "reason": str(exc)})
            continue
        metrics = result.metrics.assign(
            asset_class=asset_class,
            factor_model=model,
            mode="research_only",
            promotion_status=str(rows.iloc[0].get("promotion_status") or "research_only"),
        )
        metric_frames.append(metrics)
        selections.append(result.selections.assign(asset_class=asset_class, factor_model=model))
        trades.append(result.trades.assign(asset_class=asset_class, factor_model=model))
        nav.append(result.nav.assign(asset_class=asset_class, factor_model=model))
    metrics_frame = pd.concat(metric_frames, ignore_index=True) if metric_frames else pd.DataFrame()
    payload = {
        "universe_hash": panel.metadata.get("universe_hash"),
        "start": start,
        "end": end,
        "models": sorted(metrics_frame.get("factor_model", pd.Series(dtype=str)).unique().tolist()),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    run_id = f"{end}-{digest}"
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "research_only",
        "start": start,
        "end": end,
        "factor_models": {key: list(spec) for key, spec in FACTOR_MODELS.items()},
        "limitations": {
            "survivorship_bias": bool(panel.metadata.get("survivorship_bias", True)),
            "live_account_mutation": False,
            "bond_model_requires_product_breadth": True,
            "commodity_curve_data_available": False,
        },
        "skipped_scopes": skipped,
        "metrics": metrics_frame.to_dict(orient="records"),
    }
    return ShadowResearchResult(
        run_id,
        catalog.copy(),
        metrics_frame,
        pd.concat(selections, ignore_index=True) if selections else pd.DataFrame(),
        pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(),
        pd.concat(nav, ignore_index=True) if nav else pd.DataFrame(),
        summary,
    )


def _report(result: ShadowResearchResult) -> str:
    lines = [
        f"# QDII 全球与多资产影子研究 · {result.summary['end']}",
        "",
        "> 研究模式：不会创建真实或模拟竞赛订单，不修改活动账户、持仓或策略基线。",
        "",
        "| 资产 | 研究范围 | 模型 | 数量 | 累计收益 | Sharpe | 最大回撤 | 状态 |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in result.metrics.to_dict(orient="records"):
        lines.append(
            f"| {row['asset_class']} | {row['scope']} | {row['factor_model']} | {int(row['top_n'])} | "
            f"{float(row['cumulative_return']):+.2%} | {float(row['sharpe_ratio']):.2f} | "
            f"{float(row['max_drawdown']):+.2%} | {row['promotion_status']} |"
        )
    if result.summary["skipped_scopes"]:
        lines += ["", "## 未运行范围", ""]
        lines.extend(f"- {item['scope']}: {item['reason']}" for item in result.summary["skipped_scopes"])
    lines += [
        "",
        "## 数据边界",
        "",
        "- 当前目录历史回放存在幸存者偏差。",
        "- 商品曲线和滚动收益数据尚不可测，模型明确降级为价格、NAV、溢价与流动性组合。",
        "- 债券范围不足三只独立产品时只展示数据，不允许晋级。",
        "",
    ]
    return "\n".join(lines)


def write_shadow_artifacts(
    result: ShadowResearchResult,
    repo_root: str | Path,
    *,
    end_date: str,
) -> dict[str, Path]:
    root = Path(repo_root)
    data_dir = root / "data" / "cn_qdii_etf" / "research" / "shadow" / result.run_id
    report_dir = root / "reports" / "competition" / "research"
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": data_dir / "summary.json",
        "catalog": data_dir / "catalog.csv",
        "metrics": data_dir / "metrics.csv",
        "selections": data_dir / "selections.csv",
        "trades": data_dir / "trades.csv",
        "nav": data_dir / "nav.csv",
        "report": report_dir / f"qdii_shadow_{end_date}.md",
    }
    paths["summary"].write_text(json.dumps(result.summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    result.catalog.to_csv(paths["catalog"], index=False)
    result.metrics.to_csv(paths["metrics"], index=False)
    result.selections.to_csv(paths["selections"], index=False)
    result.trades.to_csv(paths["trades"], index=False)
    result.nav.to_csv(paths["nav"], index=False)
    paths["report"].write_text(_report(result), encoding="utf-8")
    return paths


__all__ = ["FACTOR_MODELS", "ShadowResearchResult", "run_shadow_research", "write_shadow_artifacts"]
