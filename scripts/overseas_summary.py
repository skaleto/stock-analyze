#!/usr/bin/env python3
"""overseas_summary.py — 组装港美股本机运行的飞书【交互卡片】日报并发送。

由 run-overseas.sh 跑完后调用:
    overseas_summary.py <daily|weekly> <results_tsv> <market...>

results_tsv 每行(制表符分隔,run-overseas.sh 写):
    market \t agent \t cmd \t rc \t batches \t trades \t failed

发送飞书 interactive 卡片(彩色标题 + 分市场字段网格 + 结论),结构化、有层次。
复用 stock_analyze.notifier 的凭据/token/HTTP 与展示常量;不修改 notifier 源码。
卡片发送失败时退回纯文本。缺凭据则只打印(供 launchd 日志留存)。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path("/Users/yaoyibin/Documents/stock/stock-analyze")
sys.path.insert(0, str(REPO))  # 路径式调用时把仓库根加进 sys.path

try:
    import pandas as pd
    from stock_analyze.notifier import (  # type: ignore
        LARK_BASE_URL,
        LarkAPIError,
        LarkCredentials,
        MARKET_CURRENCY,
        MARKET_INITIAL_CASH,
        MARKET_LABELS,
        _http_post_json,
        get_tenant_access_token,
        send_lark_dm,
    )
except Exception as exc:  # noqa: BLE001
    print(f"overseas_summary: import failed: {exc}", file=sys.stderr)
    sys.exit(0)

MODE = sys.argv[1] if len(sys.argv) > 1 else "daily"
RESULTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None
MARKETS = sys.argv[3:] or ["hk", "us"]
AGENTS = ["claude", "codex"]
FLAG = {"hk": "🇭🇰", "us": "🇺🇸", "a_share": "🇨🇳"}


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------
def load_results() -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    if not RESULTS or not RESULTS.exists():
        return out
    for raw in RESULTS.read_text(encoding="utf-8").splitlines():
        p = raw.split("\t")
        if len(p) < 7:
            continue
        mkt, ag, cmd, rc, batches, trades, failed = p[:7]
        out[(mkt, ag)] = {
            "cmd": cmd,
            "rc": int(rc) if rc.lstrip("-").isdigit() else 1,
            "batches": int(batches) if batches.isdigit() else None,
            "trades": int(trades) if trades.lstrip("-").isdigit() else None,
            "failed": int(failed) if failed.isdigit() else 0,
        }
    return out


def orders_count(mkt: str, ag: str) -> int | None:
    p = REPO / "data" / mkt / ag / "pending_orders.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return len(d) if isinstance(d, list) else len(d.get("orders", d)) if isinstance(d, dict) else None


def data_date(mkt: str, ag: str) -> str | None:
    p = REPO / "data" / mkt / ag / "performance_summary.json"
    if not p.exists():
        return None
    try:
        accts = json.loads(p.read_text(encoding="utf-8")).get("accounts", {})
        for a in accts.values():
            if a.get("latest_date"):
                return a["latest_date"]
    except Exception:  # noqa: BLE001
        return None
    return None


def _fmt_money(v: float, cur: str) -> str:
    if abs(v) >= 1_000_000:
        return f"{cur}{v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{cur}{v / 1_000:.0f}K"
    return f"{cur}{v:.0f}"


def nav_str(mkt: str, ag: str) -> str | None:
    p = REPO / "data" / mkt / ag / "daily_nav.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"date": str, "account_id": str})
        if df.empty or "total_value" not in df.columns:
            return None
        per_day = df.groupby("date")["total_value"].sum().sort_index()
        latest = float(per_day.iloc[-1])
        cur = MARKET_CURRENCY.get(mkt, "")
        base = MARKET_INITIAL_CASH.get(mkt, 1_000_000.0)
        pct = (latest / base - 1.0) * 100
        s = f"{_fmt_money(latest, cur)} ({pct:+.2f}%)"
        if len(per_day) >= 2 and float(per_day.iloc[-2]) > 0:
            s += f" Δ{(latest / float(per_day.iloc[-2]) - 1.0) * 100:+.2f}%"
        return s
    except Exception:  # noqa: BLE001
        return None


def positions_str(mkt: str, ag: str) -> str:
    p = REPO / "data" / mkt / ag / "positions.csv"
    if not p.exists():
        return "待成交" if orders_count(mkt, ag) else "—"
    try:
        n = len(pd.read_csv(p, dtype={"code": str}))
        return f"{n} 只" if n else "待成交"
    except Exception:  # noqa: BLE001
        return "—"


# --------------------------------------------------------------------------
# 卡片组装
# --------------------------------------------------------------------------
def build(res: dict[tuple[str, str], dict]):
    now = datetime.now().strftime("%m-%d %H:%M")  # noqa: DTZ005
    mode_cn = "周度调仓 (run-weekly)" if MODE == "weekly" else "日度执行 (run-daily)"
    did = (
        "拉 yfinance(港+美) → 因子打分 → 选股 → 生成下周目标订单"
        if MODE == "weekly"
        else "拉 yfinance → 按模拟价成交到期订单 → 标记当日 NAV"
    )

    any_fail = any(r["rc"] != 0 for r in res.values())
    cover_warn = any((r["batches"] or 0) < 50 for r in res.values() if r["rc"] == 0)
    color = "red" if any_fail else ("orange" if cover_warn else "green")
    badge = "❌ 有失败" if any_fail else ("⚠️ 覆盖偏低" if cover_warn else "✅ 正常")

    elements: list[dict] = [
        {"tag": "div", "text": {"tag": "lark_md",
         "content": f"**🔧 本次任务**：{mode_cn}\n{did}"}},
    ]

    text_lines = [f"📊 港美股本机运行报告 · {mode_cn} · {now} [{badge}]", f"🔧 {did}"]

    for mkt in MARKETS:
        agents = [a for a in AGENTS if (mkt, a) in res] or AGENTS
        label = MARKET_LABELS.get(mkt, mkt)
        flag = FLAG.get(mkt, "")

        def per_agent(fn):  # 把各 agent 的值拼成 "claude … / codex …"
            return "\n".join(f"{a}: {fn(a)}" for a in agents)

        def data_v(a):
            r = res.get((mkt, a))
            if not r:
                return "—"
            if r["rc"] != 0:
                return f"❌ 失败(rc={r['rc']})"
            s = f"打分 {r['batches']} 只" if r["batches"] is not None else "完成"
            if r["failed"]:
                s += f"，丢 {r['failed']}"
            return s

        def action_v(a):
            r = res.get((mkt, a))
            if not r or r["rc"] != 0:
                return "—"
            if MODE == "weekly":
                n = orders_count(mkt, a)
                return f"目标 {n if n is not None else '?'} 单"
            return f"成交 {r['trades'] if r['trades'] is not None else '?'} 单"

        elements.append({"tag": "hr"})
        dd = next((data_date(mkt, a) for a in agents if data_date(mkt, a)), None)
        elements.append({"tag": "div", "text": {"tag": "lark_md",
            "content": f"**{flag} {label} {mkt.upper()}**" + (f"   `数据日 {dd}`" if dd else "")}})
        elements.append({"tag": "div", "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**📥 数据**\n{per_agent(data_v)}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**📝 动作**\n{per_agent(action_v)}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**💰 NAV**\n{per_agent(lambda a: nav_str(mkt, a) or '—')}"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**📈 持仓**\n{per_agent(lambda a: positions_str(mkt, a))}"}},
        ]})

        text_lines.append(f"\n【{flag} {label}】" + (f" 数据日 {dd}" if dd else ""))
        for a in agents:
            text_lines.append(f"  {a}: {data_v(a)} | {action_v(a)} | NAV {nav_str(mkt, a) or '—'} | 持仓 {positions_str(mkt, a)}")

    verdict = []
    if any_fail:
        verdict.append("有运行失败,多半是代理掉线→yfinance 429,需手动重跑")
    if cover_warn:
        verdict.append("覆盖率偏低(yfinance 偶发 TLS 丢票),已自动重跑")
    if not verdict:
        verdict.append("覆盖率健康、无失败")
    if MODE == "weekly":
        verdict.append("下一交易日 run-daily 按模拟价成交")
    vtext = "；".join(verdict) + "。"

    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**📌 结论**\n{vtext}"}})
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"数据源 yfinance · 香港住宅代理 · {now}"}]})

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": f"📊 港美股本机日报 · {badge}"},
        },
        "elements": elements,
    }
    text_lines.append(f"📌 结论：{vtext}")
    return card, "\n".join(text_lines)


def send_card(card: dict, creds: LarkCredentials, timeout: int = 10) -> None:
    token = get_tenant_access_token(creds.app_id, creds.app_secret, timeout=timeout)
    url = f"{LARK_BASE_URL}/im/v1/messages?receive_id_type=open_id"
    payload = {
        "receive_id": creds.user_open_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    resp = _http_post_json(url, payload, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    if resp.get("code") != 0:
        raise LarkAPIError(f"send card: code={resp.get('code')} msg={resp.get('msg')}")


def main() -> None:
    res = load_results()
    card, text = build(res)
    print(text)  # 进 launchd 日志

    creds = LarkCredentials.from_env()
    if not creds:
        print("overseas_summary: no Lark creds — 仅打印未发送", file=sys.stderr)
        return
    try:
        send_card(card, creds)
    except Exception as exc:  # noqa: BLE001  卡片失败 → 退回纯文本
        print(f"overseas_summary: card send failed ({exc}); fallback to text", file=sys.stderr)
        try:
            send_lark_dm(text, creds)
        except Exception as exc2:  # noqa: BLE001
            print(f"overseas_summary: text send also failed: {exc2}", file=sys.stderr)


if __name__ == "__main__":
    main()
