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
        selected = _selected_trial(scope)
        metrics = selected.get("metrics") or {}
        attribution = selected.get("attribution") or {}
        lines.extend([
            f"### {scope['market']} / {scope['account_scope']}",
            "",
            f"- 终态：`{scope['status']}`",
            f"- 选中版本：`{scope.get('selected_incremental_spec_id') or scope.get('selected_spec_id') or '无'}`",
            f"- 净收益：{float(metrics.get('net_return') or 0.0):.2%}",
            f"- 净超额：{float(metrics.get('net_excess_return') or 0.0):.2%}",
            f"- Sharpe：{float(metrics.get('portfolio_sharpe') or 0.0):.3f}",
            f"- 最大回撤：{float(metrics.get('max_drawdown') or 0.0):.2%}",
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
    normalized = [dict(item) for item in scopes]
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
        "scopes": sorted([dict(item) for item in scopes], key=_scope_identity),
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
