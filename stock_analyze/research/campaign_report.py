"""Decision reports for one sealed strategy-recovery campaign."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..utils import now_iso, write_text_atomic


EXPECTED_SCOPES = {
    ("a_share", "hs300"),
    ("a_share", "zz500"),
    ("cn_qdii_etf", "hk_exposure"),
    ("cn_qdii_etf", "us_exposure"),
}
TERMINAL_STATES = {
    "shadow_ready",
    "baseline_only",
    "falsified",
    "insufficient_data",
}


def _scope_identity(value: Mapping[str, Any]) -> tuple[str, str]:
    return str(value.get("market") or ""), str(value.get("account_scope") or "")


def _validate_scope_set(scopes: Sequence[Mapping[str, Any]]) -> None:
    identities = [_scope_identity(item) for item in scopes]
    if len(identities) != 4 or set(identities) != EXPECTED_SCOPES:
        raise ValueError("campaign_final_scope_count")
    if len(identities) != len(set(identities)):
        raise ValueError("campaign_final_scope_duplicate")


def _selected_trial(scope: Mapping[str, Any]) -> Mapping[str, Any]:
    selected = str(
        scope.get("selected_incremental_spec_id")
        or scope.get("selected_spec_id")
        or ""
    )
    for trial in [
        *(scope.get("incremental_trials") or []),
        *(scope.get("trials") or []),
    ]:
        if str(trial.get("spec_id") or "") == selected:
            return trial
    return {}


def _best_diagnostic_trial(scope: Mapping[str, Any]) -> Mapping[str, Any]:
    trials = [
        *list(scope.get("incremental_trials") or []),
        *list(scope.get("trials") or []),
    ]
    if not trials:
        return {}

    def sort_key(trial: Mapping[str, Any]) -> tuple[float, float, str]:
        metrics = trial.get("metrics") or {}
        return (
            float(metrics.get("net_excess_return") or 0.0),
            float(metrics.get("net_return") or 0.0),
            str(trial.get("spec_id") or ""),
        )

    return max(trials, key=sort_key)


def _display_trial(scope: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    selected = _selected_trial(scope)
    if selected:
        return selected, False
    diagnostic_id = str(scope.get("best_diagnostic_spec_id") or "")
    diagnostic = _best_diagnostic_trial(scope)
    if diagnostic_id:
        for trial in [
            *list(scope.get("incremental_trials") or []),
            *list(scope.get("trials") or []),
        ]:
            if str(trial.get("spec_id") or "") == diagnostic_id:
                return trial, True
    return diagnostic, bool(diagnostic)


def _normalize_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(scope)
    selected = _selected_trial(normalized)
    diagnostic = {} if selected else _best_diagnostic_trial(normalized)
    normalized["best_diagnostic_spec_id"] = (
        str(diagnostic.get("spec_id") or "") or None
    )
    normalized["diagnostic_only"] = bool(diagnostic)
    return normalized


def _percent(value: Any) -> str:
    try:
        return f"{float(value):.2%}"
    except (TypeError, ValueError):
        return "未提供"


def _number(value: Any, *, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "未提供"


def _gate_reasons(trial: Mapping[str, Any], key: str) -> str:
    reasons = [str(item) for item in (trial.get(key) or {}).get("reasons") or []]
    return ", ".join(reasons) if reasons else "无"


def _fold_summary(trial: Mapping[str, Any]) -> str:
    folds = sorted(
        list(trial.get("folds") or []),
        key=lambda item: int(item.get("fold") or 0),
    )
    return " / ".join(
        _percent(item.get("net_excess_return")) for item in folds
    ) or "未提供"


def _regime_summary(trial: Mapping[str, Any]) -> str:
    regimes = trial.get("regimes") or {}
    return " / ".join([
        f"牛市 {_percent((regimes.get('bull') or {}).get('cumulative_active_return'))}",
        f"震荡 {_percent((regimes.get('range') or {}).get('cumulative_active_return'))}",
        f"下行 {_percent((regimes.get('down') or {}).get('cumulative_active_return'))}",
    ])


def _attribution_summary(trial: Mapping[str, Any]) -> str:
    components = (trial.get("attribution") or {}).get("components") or {}
    labels = (
        ("selection", "选股"),
        ("timing", "择时"),
        ("beta", "Beta"),
        ("active_cash", "主动现金"),
        ("fees", "费用"),
        ("unfilled", "未成交"),
    )
    return " / ".join(
        f"{label} {_percent(components.get(key))}" for key, label in labels
    )


def _scope_explanation(scope: Mapping[str, Any]) -> str:
    status = str(scope.get("status") or "")
    reasons = [str(item) for item in scope.get("reasons") or []]
    if status == "shadow_ready":
        return "透明策略与固定 ML 增量均通过历史诊断，只进入 Shadow，不影响正式订单。"
    if status == "baseline_only":
        return "透明策略通过，但两个固定 ML 残差均未证明净增量，Shadow 保留规则版本。"
    if status == "insufficient_data":
        return "数据或回放可信门失败；只能补齐证据，不能继续调参。" + (
            f" 失败项：{', '.join(reasons)}。" if reasons else ""
        )
    return "固定预算内没有候选同时通过经济价值与稳健性门，停止该范围研究。" + (
        f" 失败项：{', '.join(reasons)}。" if reasons else ""
    )


def _markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        f"# 策略恢复 Campaign：{payload['campaign_id']}",
        "",
        f"- 状态：`{payload['status']}`",
        f"- Manifest：`{payload['manifest_hash']}`",
        f"- 正式策略已启用：`{str(payload['formal_strategy_activated']).lower()}`",
        "",
        "## 四个范围结论",
        "",
    ]
    for scope in payload["scopes"]:
        displayed, diagnostic_only = _display_trial(scope)
        metrics = displayed.get("metrics") or {}
        attribution = displayed.get("attribution") or {}
        gate_two = displayed.get("gate_two") or {}
        governance = gate_two.get("governance") or {}
        displayed_id = str(displayed.get("spec_id") or "")
        lines.extend([
            f"### {scope['market']} / {scope['account_scope']}",
            "",
            f"- 终态：`{scope['status']}`",
            (
                f"- 最佳诊断候选：`{displayed_id}`（仅用于解释失败，不代表选中）"
                if diagnostic_only
                else f"- 研究选中版本：`{displayed_id or '无'}`"
            ),
            f"- 基准收益：{_percent(metrics.get('benchmark_return'))}",
            f"- 净收益：{_percent(metrics.get('net_return'))}",
            f"- 净超额：{_percent(metrics.get('net_excess_return'))}",
            f"- Sharpe：{_number(metrics.get('portfolio_sharpe'))}",
            f"- 最大回撤：{_percent(metrics.get('max_drawdown'))}",
            f"- 2x 成本净超额：{_percent((displayed.get('cost_stress') or {}).get('net_excess_return'))}",
            f"- 目标成交率：{_percent(metrics.get('target_fill_ratio'))}；策略风险仓位：{_percent(metrics.get('strategic_risky_exposure'))}；年换手：{_number(metrics.get('annual_turnover'), digits=2)}x",
            f"- 三折净超额：{_fold_summary(displayed)}",
            f"- 市场状态净超额：{_regime_summary(displayed)}",
            f"- 稳健性：DSR {_number(governance.get('deflated_sharpe_probability'))} / PBO {_number(governance.get('probability_of_backtest_overfit'))} / bootstrap {_number(displayed.get('bootstrap_probability'))}",
            f"- Gate 1 失败：`{_gate_reasons(displayed, 'gate_one_pre_family')}`",
            f"- Gate 2 失败：`{_gate_reasons(displayed, 'gate_two')}`",
            f"- 收益归因：{_attribution_summary(displayed)}",
            f"- 归因状态：`{attribution.get('status') or metrics.get('attribution_status') or 'unavailable'}`",
            f"- 解释：{_scope_explanation(scope)}",
            "",
        ])
    lines.extend([
        "## 安全边界",
        "",
        "历史结果只决定是否进入 Shadow。正式纸面策略、持仓、订单与成交均未被本 Campaign 修改。",
        "",
    ])
    return "\n".join(lines)


def write_final_campaign_report(
    repo_root: Path,
    *,
    campaign_id: str,
    manifest_hash: str,
    scopes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _validate_scope_set(scopes)
    normalized = [_normalize_scope(item) for item in scopes]
    invalid = sorted({
        str(item.get("status") or "")
        for item in normalized
        if str(item.get("status") or "") not in TERMINAL_STATES
    })
    if invalid:
        raise ValueError(f"campaign_final_status_invalid:{','.join(invalid)}")
    payload = {
        "schema_version": 1,
        "status": "complete",
        "campaign_id": str(campaign_id),
        "manifest_hash": str(manifest_hash),
        "completed_at": now_iso(),
        "formal_strategy_activated": False,
        "champion_model_version": None,
        "scopes": sorted(normalized, key=_scope_identity),
    }
    reports = Path(repo_root) / "reports" / "research"
    json_path = reports / f"{campaign_id}-final.json"
    markdown_path = reports / f"{campaign_id}-final.md"
    write_text_atomic(
        json_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    )
    write_text_atomic(markdown_path, _markdown(payload))
    return {
        "status": "complete",
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "payload": payload,
    }


def write_transparent_campaign_report(
    repo_root: Path,
    *,
    campaign_id: str,
    manifest_hash: str,
    scopes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _validate_scope_set(scopes)
    payload = {
        "schema_version": 1,
        "status": "transparent_complete",
        "campaign_id": str(campaign_id),
        "manifest_hash": str(manifest_hash),
        "completed_at": now_iso(),
        "formal_strategy_activated": False,
        "scopes": sorted([_normalize_scope(item) for item in scopes], key=_scope_identity),
    }
    reports = Path(repo_root) / "reports" / "research"
    json_path = reports / f"{campaign_id}-transparent.json"
    markdown_path = reports / f"{campaign_id}-transparent.md"
    write_text_atomic(
        json_path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    )
    write_text_atomic(markdown_path, _markdown({**payload, "champion_model_version": None}))
    return {
        "status": "transparent_complete",
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "payload": payload,
    }


__all__ = [
    "EXPECTED_SCOPES",
    "TERMINAL_STATES",
    "write_final_campaign_report",
    "write_transparent_campaign_report",
]
