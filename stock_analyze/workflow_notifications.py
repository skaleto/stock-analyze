"""Consolidated daily, weekly, and monthly operator notifications."""

from __future__ import annotations

import json
import hashlib
import sys
import time
import urllib.error
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from . import competition
from .notifier import (
    LarkAPIError,
    LarkCredentials,
    MARKET_INITIAL_CASH,
    MARKET_LABELS,
    send_lark_card,
    send_lark_dm,
)
from .model_iteration import lifecycle_label
from .strategy_registry import StrategyRegistryInvalid, strategy_display_name
from .utils import today as _today


Cadence = Literal["daily", "weekly", "monthly"]
AGENT_IDS = ("claude", "codex")
LEDGER_RELATIVE_PATH = Path("data") / "notifications" / "workflow_sent.json"
MAX_DAILY_PREDICTION_LINES = 3
MAX_SEMANTIC_STATUS_BYTES = 1_048_576
WORKFLOW_DEFERRED_EXIT = 75

_RUN_DTYPE = {
    "run_id": str,
    "command": str,
    "as_of": str,
    "started_at": str,
    "finished_at": str,
    "status": str,
    "error_summary": str,
    "config_hash": str,
    "code_version": str,
}
_NAV_DTYPE = {
    "date": str,
    "account_id": str,
    "benchmark_code": str,
    "benchmark_date": str,
}


@dataclass(frozen=True)
class WorkflowTaskResult:
    market: str
    agent_id: str
    status: str
    finished_at: str = ""
    error_summary: str = ""


def _display_name(repo_root: Path, agent_id: str) -> str:
    try:
        return strategy_display_name(agent_id, repo_root)
    except StrategyRegistryInvalid:
        return agent_id


def _previous_month(d: date) -> str:
    first = d.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _recent_friday(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 4) % 7)


def default_target(cadence: Cadence, today_d: date | None = None) -> str:
    today_d = today_d or _today()
    if cadence == "daily":
        return today_d.isoformat()
    if cadence == "weekly":
        return _recent_friday(today_d).isoformat()
    if cadence == "monthly":
        return _previous_month(today_d)
    raise ValueError(f"unsupported cadence: {cadence}")


def _validate_target(cadence: Cadence, target: str) -> None:
    if cadence in ("daily", "weekly"):
        date.fromisoformat(target)
    elif cadence == "monthly":
        datetime.strptime(target, "%Y-%m")
    else:
        raise ValueError(f"unsupported cadence: {cadence}")


def _run_dates(cadence: Cadence, target: str) -> set[str]:
    target_date = date.fromisoformat(target)
    if cadence == "weekly":
        return {
            (target_date + timedelta(days=offset)).isoformat()
            for offset in range(3)
        }
    return {target_date.isoformat()}


def _read_csv(path: Path, dtype: dict[str, type]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=dtype, keep_default_na=False)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return pd.DataFrame()


def _latest_task_result(
    repo_root: Path,
    market: str,
    agent_id: str,
    cadence: Literal["daily", "weekly"],
    target: str,
) -> WorkflowTaskResult:
    path = repo_root / "data" / market / agent_id / "runs.csv"
    df = _read_csv(path, _RUN_DTYPE)
    command = f"run-{cadence}"
    if df.empty or "command" not in df.columns:
        return WorkflowTaskResult(market, agent_id, "missing")

    subset = df.loc[df["command"].astype(str) == command].copy()
    if subset.empty:
        return WorkflowTaskResult(market, agent_id, "missing")

    allowed_dates = _run_dates(cadence, target)
    started_dates = subset.get("started_at", pd.Series(dtype=str)).astype(str).str[:10]
    as_of = subset.get("as_of", pd.Series(dtype=str)).astype(str)
    subset = subset.loc[as_of.eq(target) | started_dates.isin(allowed_dates)]
    if subset.empty:
        return WorkflowTaskResult(market, agent_id, "missing")

    if "run_id" in subset.columns:
        subset = subset.groupby("run_id", sort=False, dropna=False).tail(1)
    latest = subset.sort_values("started_at", kind="stable").iloc[-1]
    return WorkflowTaskResult(
        market=market,
        agent_id=agent_id,
        status=str(latest.get("status") or "unknown"),
        finished_at=str(latest.get("finished_at") or latest.get("started_at") or ""),
        error_summary=str(latest.get("error_summary") or ""),
    )


def collect_task_results(
    cadence: Literal["daily", "weekly"],
    repo_root: Path,
    target: str,
) -> list[WorkflowTaskResult]:
    return [
        _latest_task_result(repo_root, market, agent_id, cadence, target)
        for market in competition.MARKETS
        for agent_id in AGENT_IDS
    ]


def _trade_count(repo_root: Path, market: str, agent_id: str, target: str) -> int:
    path = repo_root / "data" / market / agent_id / "trades.csv"
    df = _read_csv(path, {"trade_date": str, "account_id": str, "code": str})
    if df.empty or "trade_date" not in df.columns:
        return 0
    return int(df["trade_date"].astype(str).eq(target).sum())


def _pending_count(repo_root: Path, market: str, agent_id: str) -> int:
    path = repo_root / "data" / market / agent_id / "pending_orders.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, list):
        return 0

    count = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        orders = item.get("orders")
        candidates = orders if isinstance(orders, list) else [item]
        for order in candidates:
            if not isinstance(order, dict):
                continue
            status = str(order.get("status") or "pending").lower()
            if status not in {"filled", "cancelled", "canceled", "expired"}:
                count += 1
    return count


def _nav_series(repo_root: Path, market: str, agent_id: str) -> pd.Series:
    path = repo_root / "data" / market / agent_id / "daily_nav.csv"
    df = _read_csv(path, _NAV_DTYPE)
    if df.empty or not {"date", "total_value"}.issubset(df.columns):
        return pd.Series(dtype=float, index=pd.Index([], dtype=str))
    values = pd.to_numeric(df["total_value"], errors="coerce")
    work = pd.DataFrame({"date": df["date"].astype(str), "total_value": values})
    work = work.dropna(subset=["total_value"])
    if work.empty:
        return pd.Series(dtype=float, index=pd.Index([], dtype=str))
    return work.groupby("date")["total_value"].sum().sort_index()


def _strategy_nav_summary(
    repo_root: Path,
    agent_id: str,
    *,
    on_or_before: str | None = None,
) -> tuple[float | None, float | None]:
    latest_total = 0.0
    previous_total = 0.0
    markets_found = 0
    previous_found = 0
    for market in competition.MARKETS:
        series = _nav_series(repo_root, market, agent_id)
        if on_or_before:
            series = series.loc[series.index.astype(str) <= on_or_before]
        if series.empty:
            continue
        latest_total += float(series.iloc[-1])
        markets_found += 1
        if len(series) >= 2:
            previous_total += float(series.iloc[-2])
            previous_found += 1
    if markets_found != len(competition.MARKETS):
        return None, None
    latest = latest_total
    previous = previous_total if previous_found == markets_found else None
    return latest, previous


def _monthly_strategy_return(
    repo_root: Path, agent_id: str, month: str
) -> float | None:
    start_total = 0.0
    end_total = 0.0
    found = 0
    for market in competition.MARKETS:
        series = _nav_series(repo_root, market, agent_id)
        series = series.loc[series.index.astype(str).str.startswith(month)]
        if series.empty:
            continue
        start_total += float(series.iloc[0])
        end_total += float(series.iloc[-1])
        found += 1
    if found != len(competition.MARKETS) or start_total <= 0:
        return None
    return end_total / start_total - 1.0


def _status_counts(results: list[WorkflowTaskResult]) -> tuple[int, int, int]:
    success = sum(result.status == "success" for result in results)
    failed = sum(result.status in {"failed", "error"} for result in results)
    missing = len(results) - success - failed
    return success, failed, missing


def _task_lines(
    results: list[WorkflowTaskResult], repo_root: Path
) -> list[str]:
    symbols = {"success": "成功", "failed": "失败", "error": "失败"}
    lines: list[str] = []
    for result in results:
        market = MARKET_LABELS.get(result.market, result.market)
        strategy = _display_name(repo_root, result.agent_id)
        status = symbols.get(result.status, "未完成")
        finished = result.finished_at[11:16] if len(result.finished_at) >= 16 else ""
        suffix = f" {finished}" if finished else ""
        lines.append(f"{market} / {strategy}: {status}{suffix}")
    return lines


def _strategy_lines(
    repo_root: Path, *, on_or_before: str | None = None
) -> list[str]:
    baseline = sum(MARKET_INITIAL_CASH.get(market, 0.0) for market in competition.MARKETS)
    lines: list[str] = []
    for agent_id in AGENT_IDS:
        label = _display_name(repo_root, agent_id)
        latest, previous = _strategy_nav_summary(
            repo_root, agent_id, on_or_before=on_or_before
        )
        if latest is None:
            lines.append(f"{label}: 净值数据积累中")
            continue
        season = (latest / baseline - 1.0) if baseline > 0 else 0.0
        daily = ""
        if previous and previous > 0:
            daily = f"，较前次 {latest / previous - 1.0:+.2%}"
        lines.append(f"{label}: ¥{latest:,.0f}，相对初始 {season:+.2%}{daily}")
    return lines


def _model_iteration_pending_count(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, list):
        return 0
    total = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        orders = item.get("orders")
        candidates = orders if isinstance(orders, list) else [item]
        total += sum(
            isinstance(order, dict)
            and str(order.get("status") or "pending").lower()
            not in {"filled", "cancelled", "canceled", "expired"}
            for order in candidates
        )
    return int(total)


def _model_iteration_lines(repo_root: Path, *, on_or_before: str) -> list[str]:
    lines: list[str] = []
    for market in competition.MARKETS:
        market_root = repo_root / "data" / "model_iterations" / market
        status_paths = sorted(market_root.glob("*/current_status.json"))
        status_path = status_paths[-1] if status_paths else None
        if status_path is None:
            legacy_dir = repo_root / "data" / "model_shadow" / market
            legacy_status = legacy_dir / "shadow_status.json"
            if not legacy_status.exists():
                continue
            status_path = legacy_status
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            horizon = int(status.get("horizon") or status_path.parent.name or 0)
        except (TypeError, ValueError):
            horizon = 0
        model_version = str(status.get("model_version") or "")
        if model_version and status_path.name == "current_status.json":
            data_dir = status_path.parent / model_version
        else:
            data_dir = status_path.parent
        nav_path = data_dir / "daily_nav.csv"
        nav = _read_csv(nav_path, _NAV_DTYPE)
        latest: float | None = None
        if not nav.empty and {"date", "total_value"}.issubset(nav.columns):
            values = pd.to_numeric(nav["total_value"], errors="coerce")
            work = pd.DataFrame({"date": nav["date"].astype(str), "value": values})
            work = work.loc[work["date"].le(on_or_before)].dropna(subset=["value"])
            if not work.empty:
                latest = float(work.groupby("date")["value"].sum().sort_index().iloc[-1])
        positions = _read_csv(
            data_dir / "positions.csv",
            {"account_id": str, "code": str},
        )
        holding_count = int(len(positions)) if not positions.empty else 0
        pending_count = _model_iteration_pending_count(data_dir / "pending_orders.json")
        prediction_date = status.get("prediction_as_of") or "待生成"
        posture = "，保持现金" if status.get("cash_only") else ""
        display_version = status.get("display_version") or model_version or "待选择"
        lifecycle = status.get("lifecycle_status_label") or {
            "research": "研究候选",
            "shadow": "模拟验证",
            "active": "正式使用",
        }.get(str(status.get("lifecycle_status") or ""), "待验证")
        cycles = int(status.get("shadow_cycles") or 0)
        required = cycles + int(status.get("shadow_cycles_remaining") or 0)
        progress = f" {cycles}/{required}" if required else ""
        nav_text = f"¥{latest:,.0f}" if latest is not None else "净值待生成"
        lines.append(
            f"{MARKET_LABELS.get(market, market)}: {display_version}（{lifecycle}{progress}），"
            f"{nav_text}，"
            f"持仓 {holding_count}，待执行 {pending_count}，"
            f"{horizon}日模型 {prediction_date}{posture}"
        )
    return lines


def _qdii_research_alerts(repo_root: Path, today_d: date) -> list[str]:
    from .markets.cn_qdii_etf.fund_events import active_event_state, load_event_store

    alerts: list[str] = []
    event_path = repo_root / "data" / "cn_qdii_etf" / "shared" / "fund_events.csv"
    events = load_event_store(event_path)
    if not events.empty:
        cutoff = f"{today_d.isoformat()}T23:59:59"
        blocked = sum(
            bool(active_event_state(events, code, cutoff).get("hard_block"))
            for code in events["code"].astype(str).drop_duplicates()
        )
        if blocked:
            alerts.append(f"{blocked} 只基金存在公告硬阻断")

    shadow_root = repo_root / "data" / "cn_qdii_etf" / "research" / "shadow"
    summaries = sorted(shadow_root.glob("*/summary.json")) if shadow_root.exists() else []
    if not summaries:
        alerts.append("影子研究尚未生成")
        return alerts
    try:
        summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
        end_date = date.fromisoformat(str(summary.get("end") or "")[:10])
    except (OSError, ValueError, json.JSONDecodeError):
        alerts.append("影子研究摘要不可读")
        return alerts
    age = (today_d - end_date).days
    if age > 8:
        alerts.append(f"影子研究已 {age} 天未更新")
    skipped = summary.get("skipped_scopes") or []
    failed = [item for item in skipped if "history_unavailable" not in str(item.get("reason"))]
    if failed:
        alerts.append(f"{len(failed)} 个研究范围运行失败")
    return alerts


def collect_prediction_notifications(
    repo_root: Path,
    target: str,
    *,
    seen_alert_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return stable IDs and concise lines for new material prediction changes."""

    seen = seen_alert_ids or set()
    alert_ids: list[str] = []
    ranked_lines: list[tuple[float, str]] = []
    target_key = target.replace("-", "")
    for market in competition.MARKETS:
        for agent_id in AGENT_IDS:
            directory = repo_root / "data" / market / agent_id / "predictions"
            files = [path for path in sorted(directory.glob("*.parquet")) if path.stem <= target_key] if directory.exists() else []
            if not files:
                continue
            try:
                frame = pd.read_parquet(files[-1])
            except Exception:  # noqa: BLE001 - failures are reported by pipeline health
                continue
            for row in frame.to_dict(orient="records"):
                if row.get("invalidated") in (True, 1, "true", "True"):
                    continue
                confidence = float(row.get("confidence") or 0.0)
                p_up = float(row.get("p_up") or 0.0)
                p_down = float(row.get("p_down") or 0.0)
                if confidence < 0.70 or max(p_up, p_down) < 0.60:
                    continue
                direction = "上行" if p_up >= p_down else "下行"
                probability = max(p_up, p_down)
                bucket = round(probability / 0.05) * 0.05
                raw_id = "|".join(
                    [
                        market,
                        agent_id,
                        str(row.get("code") or ""),
                        str(row.get("horizon") or ""),
                        direction,
                        str(row.get("model_version") or ""),
                        f"{bucket:.2f}",
                    ]
                )
                alert_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:20]
                if alert_id in seen or alert_id in alert_ids:
                    continue
                alert_ids.append(alert_id)
                active = " · 已激活" if str(row.get("active_status") or "") == "active" else " · 研究中"
                ranked_lines.append(
                    (
                        confidence * probability,
                        f"{MARKET_LABELS.get(market, market)} / {_display_name(repo_root, agent_id)} / "
                        f"{row.get('code')}：{row.get('horizon')}日{direction} {probability:.0%}，"
                        f"可信度 {confidence:.0%}{active}",
                    )
                )
    lines = [line for _, line in sorted(ranked_lines, key=lambda item: (-item[0], item[1]))]
    return alert_ids, lines


def _model_research_lines(repo_root: Path) -> list[str]:
    from .dashboard_aggregator import DashboardDataError, _read_model_health

    lines: list[str] = []
    for market in competition.MARKETS:
        try:
            health = _read_model_health(repo_root, market)
        except DashboardDataError:
            lines.append(f"{MARKET_LABELS.get(market, market)}：模型元数据不可读")
            continue
        models = health.get("models") or []
        if not models:
            continue
        payload = next(
            (model for model in models if int(model.get("horizon") or 0) == 5),
            models[0],
        )
        metrics = payload.get("metrics") or {}
        status_label = lifecycle_label(str(payload.get("status") or "research"))
        lines.append(
            f"{MARKET_LABELS.get(market, market)}：{payload.get('horizon', '-')}日模型，"
            f"Brier {float(metrics.get('brier_score', 0.0)):.3f}，"
            f"LogLoss {float(metrics.get('log_loss', 0.0)):.3f}，状态 {status_label}"
        )
    return lines


def _intelligence_line(repo_root: Path, target: str) -> str | None:
    del target
    report = (
        repo_root
        / "reports"
        / "intelligence"
        / "semantic_status_latest.json"
    )
    if not report.exists():
        return "公告：状态尚未生成"
    try:
        with report.open("rb") as stream:
            raw_payload = stream.read(MAX_SEMANTIC_STATUS_BYTES + 1)
        if len(raw_payload) > MAX_SEMANTIC_STATUS_BYTES:
            return "公告：状态暂不可用"
        payload = json.loads(raw_payload.decode("utf-8"))
        if not isinstance(payload, dict):
            return "公告：状态暂不可用"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "公告：状态暂不可用"

    metadata = payload.get("metadata")
    artifacts = payload.get("artifacts")
    semantic = payload.get("semantic")
    versions = payload.get("versions")
    if not all(
        isinstance(section, dict)
        for section in (metadata, artifacts, semantic, versions)
    ):
        return "公告：状态暂不可用"

    def count(section: dict[str, Any], key: str) -> int:
        value = section.get(key, 0)
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    documents = count(metadata, "documents")
    new_documents = count(metadata, "new_documents") or documents
    pdf_ready = count(artifacts, "pdf_available") or count(
        artifacts, "downloaded"
    )
    events = count(semantic, "canonical") or count(semantic, "succeeded")
    quarantined = count(semantic, "quarantined")
    queue_depth = count(payload, "queue_depth")
    if not queue_depth:
        queue_depth = count(artifacts, "queued") + count(semantic, "queued")

    profile_label = " ".join(
        str(versions.get("profile") or "未配置").split()
    )[:80]
    failed = count(semantic, "failed")
    return (
        f"公告：新增 {new_documents}｜PDF {pdf_ready}/{documents}｜"
        f"事件 {events}｜隔离 {quarantined}｜失败 {failed}｜"
        f"待处理 {queue_depth}｜契约 {profile_label}"
    )


def build_workflow_summary(
    cadence: Cadence,
    repo_root: Path | None = None,
    today_d: date | None = None,
    *,
    target: str | None = None,
) -> str:
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    today_d = today_d or _today()
    target = target or default_target(cadence, today_d)
    _validate_target(cadence, target)

    if cadence in ("daily", "weekly"):
        results = collect_task_results(cadence, repo_root, target)
        success, failed, missing = _status_counts(results)
        title = "任务日报" if cadence == "daily" else "周度复盘"
        lines = [f"{title} {target}", f"整体: {success}/{len(results)} 成功"]
        if failed or missing:
            lines[-1] += f"，失败 {failed}，未完成 {missing}"
        if cadence == "daily":
            trades = sum(
                _trade_count(repo_root, market, agent_id, target)
                for market in competition.MARKETS
                for agent_id in AGENT_IDS
            )
            lines[-1] += f"，成交 {trades}"
            pending = sum(
                _pending_count(repo_root, market, agent_id)
                for market in competition.MARKETS
                for agent_id in AGENT_IDS
            )
            lines[-1] += f"，待执行订单 {pending}"
            lines.extend(["", "策略总览:", *_strategy_lines(repo_root, on_or_before=target)])
            model_iteration_lines = _model_iteration_lines(
                repo_root,
                on_or_before=target,
            )
            if model_iteration_lines:
                lines.extend(["", "模型迭代:", *model_iteration_lines])
            intelligence = _intelligence_line(repo_root, target)
            if intelligence:
                lines.extend(["", intelligence])
            ledger = _read_delivery_ledger(repo_root)
            seen_alert_ids = set(ledger.get("alert_ids") or [])
            _, prediction_lines = collect_prediction_notifications(
                repo_root,
                target,
                seen_alert_ids=seen_alert_ids,
            )
            if prediction_lines:
                visible = prediction_lines[:MAX_DAILY_PREDICTION_LINES]
                heading = f"新增预测提醒: {len(prediction_lines)} 条"
                if len(prediction_lines) > len(visible):
                    heading += f"，仅展示置信度最高的 {len(visible)} 条"
                lines.extend(["", heading, *visible])
        lines.extend(["", "任务运行:", *_task_lines(results, repo_root)])
        if cadence == "weekly":
            research_alerts = _qdii_research_alerts(repo_root, today_d)
            if research_alerts:
                lines.extend(["", "研究异常:", *research_alerts])
            model_lines = _model_research_lines(repo_root)
            if model_lines:
                lines.extend(["", "模型研究:", *model_lines])
            lines.extend(
                [
                    "",
                    "需要你做 1 件事:",
                    f"打开 Codex，发送：运行 {target} 周度复盘",
                    "复盘只做归因和异常检查，不会手动成交。",
                ]
            )
        return "\n".join(lines)

    review_path = (
        repo_root / "data" / "competition" / "monthly_reviews" / f"{target}.json"
    )
    review_status = "A股月报已生成" if review_path.exists() else "A股月报尚未生成"
    lines = [f"月任务 {target}", f"整体: {review_status}，QDII 月度数据已纳入策略总览"]
    lines.extend(["", "策略月度表现:"])
    for agent_id in AGENT_IDS:
        label = _display_name(repo_root, agent_id)
        monthly_return = _monthly_strategy_return(repo_root, agent_id, target)
        value = "数据积累中" if monthly_return is None else f"{monthly_return:+.2%}"
        lines.append(f"{label}: {value}")
    lines.extend(
        [
            "",
            "需要你做 1 件事:",
            f"打开 Codex，发送：运行 {target} 月度策略演化",
            "Codex 会完成四份策略的分析、门禁、版本发布和 ECS 验证。",
        ]
    )
    return "\n".join(lines)


def _notification_health(
    cadence: Cadence, repo_root: Path, target: str
) -> tuple[str, str]:
    if cadence == "monthly":
        review = repo_root / "data" / "competition" / "monthly_reviews" / f"{target}.json"
        return ("blue", "待演化") if review.exists() else ("orange", "月报未完成")
    results = collect_task_results(cadence, repo_root, target)
    success, failed, missing = _status_counts(results)
    if failed:
        return "red", f"{success}/{len(results)} 成功"
    if missing:
        return "orange", f"{success}/{len(results)} 成功"
    return "green", f"{success}/{len(results)} 成功"


def build_workflow_summary_card(
    cadence: Cadence,
    repo_root: Path | None = None,
    today_d: date | None = None,
    *,
    target: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    today_d = today_d or _today()
    target = target or default_target(cadence, today_d)
    summary = build_workflow_summary(
        cadence, repo_root, today_d=today_d, target=target
    )
    color, badge = _notification_health(cadence, repo_root, target)
    title_by_cadence = {
        "daily": "任务日报",
        "weekly": "周度复盘提醒",
        "monthly": "月任务与策略演化提醒",
    }
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {
                "tag": "plain_text",
                "content": f"{title_by_cadence[cadence]} {target} | {badge}",
            },
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
            {"tag": "note", "elements": [{
                "tag": "plain_text",
                "content": "Stock-Analyze 模拟盘，不连接真实券商。",
            }]},
        ],
    }


def _read_delivery_ledger(repo_root: Path) -> dict[str, Any]:
    path = repo_root / LEDGER_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "sent": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("sent"), dict):
        return {"version": 1, "sent": {}}
    return payload


def _mark_sent(repo_root: Path, key: str, *, alert_ids: list[str] | None = None) -> None:
    path = repo_root / LEDGER_RELATIVE_PATH
    payload = _read_delivery_ledger(repo_root)
    payload["version"] = 1
    payload.setdefault("sent", {})[key] = {
        "sent_at": datetime.now().astimezone().isoformat(timespec="seconds")
    }
    if alert_ids:
        existing = list(payload.get("alert_ids") or [])
        payload["alert_ids"] = list(dict.fromkeys([*existing, *alert_ids]))[-1000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def cli_send_workflow_summary(
    cadence: Cadence,
    repo_root: Path | None = None,
    *,
    target: str | None = None,
    force: bool = False,
    preview: bool = False,
    require_complete: bool = False,
    wait_seconds: int = 0,
    poll_seconds: float = 5.0,
) -> int:
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    target = target or default_target(cadence)
    try:
        _validate_target(cadence, target)
    except ValueError as exc:
        print(f"invalid workflow target: {exc}", file=sys.stderr)
        return 2

    key = f"{cadence}:{target}"
    if require_complete and cadence in ("daily", "weekly"):
        deadline = time.monotonic() + max(0, int(wait_seconds))
        while True:
            results = collect_task_results(cadence, repo_root, target)
            success, failed, missing = _status_counts(results)
            if success == len(results):
                break
            if failed or time.monotonic() >= deadline:
                print(
                    "workflow notification deferred: "
                    f"{key} has {success}/{len(results)} success, "
                    f"{failed} failed, {missing} incomplete",
                    file=sys.stderr,
                )
                return WORKFLOW_DEFERRED_EXIT
            remaining = max(0.0, deadline - time.monotonic())
            time.sleep(min(max(0.1, float(poll_seconds)), remaining))

    ledger = _read_delivery_ledger(repo_root)
    if key in ledger.get("sent", {}) and not force:
        print(f"workflow notification already sent: {key}")
        return 0

    summary = build_workflow_summary(cadence, repo_root, target=target)
    card = build_workflow_summary_card(cadence, repo_root, target=target)
    creds = None if preview else LarkCredentials.from_env()
    if creds is None:
        print(summary)
        if not preview:
            print("Lark credentials missing; preview only, not marked as sent.", file=sys.stderr)
        return 0

    try:
        send_lark_card(card, creds)
    except Exception as card_exc:  # noqa: BLE001
        print(f"Lark card push failed ({card_exc}); falling back to text DM", file=sys.stderr)
        try:
            send_lark_dm(summary, creds)
        except (urllib.error.URLError, LarkAPIError) as text_exc:
            print(f"Lark workflow push failed: {text_exc}", file=sys.stderr)
            return 1
    alert_ids = collect_prediction_notifications(repo_root, target)[0] if cadence == "daily" else []
    _mark_sent(repo_root, key, alert_ids=alert_ids)
    print(f"workflow notification sent: {key}")
    return 0


__all__ = [
    "WorkflowTaskResult",
    "build_workflow_summary",
    "build_workflow_summary_card",
    "cli_send_workflow_summary",
    "collect_task_results",
    "collect_prediction_notifications",
    "default_target",
    "WORKFLOW_DEFERRED_EXIT",
]
