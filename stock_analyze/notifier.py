"""Operator notification layer.

Pushes a daily summary of ECS automation + per-agent NAV/positions/sanity
state + pending operator actions to the operator's Lark DM via the Lark
Open API. Designed to run via systemd ``ExecStartPost=`` on
``stock-analyze-aggregate-dashboard.service`` so it fires once per day
right after the daily simulation pipeline closes out.

This sits *next to* (not in place of) the existing PIPELINE_FAILURES.log
+ ``SA_LARK_WEBHOOK`` failure-only path:

  - Failure path (already in place): systemd OnFailure= → group webhook
  - Success/status path (this module): aggregate-dashboard.ExecStartPost=
    → user open_id DM

The two channels are independent. Failure of one does not affect the
other.

Required environment variables (typically loaded from
``/etc/stock-analyze/secrets.env``):

    SA_LARK_APP_ID         App ID of the operator-owned Lark custom app
                           (e.g. ``cli_a8xxxxxxxx``).
    SA_LARK_APP_SECRET     App secret. NEVER log or persist this value
                           outside the secrets.env file.
    SA_LARK_USER_OPEN_ID   Operator's Lark ``open_id`` — the DM target.

If any of these is missing, :func:`cli_send_daily_summary` still builds
the summary and prints it to stdout — the script exits 0 so it doesn't
trigger the OnFailure hook just because credentials weren't set up yet.
This lets the operator preview the message format locally before
configuring credentials on ECS.

See ``docs/operator-alerting-setup.md`` for the Lark custom-app
creation steps + how to discover ``open_id`` from an email address.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .sanity_check import Anomaly, check_agent, max_severity
from .utils import today as _today


LARK_BASE_URL = "https://open.feishu.cn/open-apis"

#: Baseline starting cash per the locked competition configuration. Kept
#: here as a constant (rather than re-loading competition_a_share.yaml) so the
#: notifier stays import-cheap. If the baseline ever changes, this is
#: the only line that needs updating in this module.
COMPETITION_INITIAL_CASH = 1_000_000.0

#: Per-agent target holdings (top_n × n_accounts). For the competition
#: baseline this is 50 + 50 = 100. Used purely for the "X / 100" display
#: so the operator can spot the Tier-1+2 sizing gap at a glance.
TARGET_HOLDINGS_PER_AGENT = 100


@dataclass
class LarkCredentials:
    """Lark Open API credentials, loaded from env vars.

    Kept as a small dataclass (rather than passing three positional args
    everywhere) so future credential rotations or alternative auth flows
    have one obvious touchpoint.
    """

    app_id: str
    app_secret: str
    user_open_id: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LarkCredentials | None":
        """Return credentials from env vars, or ``None`` if any are missing.

        Pass ``env`` to override ``os.environ`` (used by tests).
        """
        e = env if env is not None else os.environ
        app_id = (e.get("SA_LARK_APP_ID") or "").strip()
        app_secret = (e.get("SA_LARK_APP_SECRET") or "").strip()
        user_open_id = (e.get("SA_LARK_USER_OPEN_ID") or "").strip()
        if not (app_id and app_secret and user_open_id):
            return None
        return cls(app_id=app_id, app_secret=app_secret, user_open_id=user_open_id)


class LarkAPIError(RuntimeError):
    """Raised when the Lark Open API returns a non-zero ``code``.

    Carries the API's error message text so the systemd journal has a
    useful entry without leaking the bearer token.
    """


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 8,
) -> dict[str, Any]:
    """POST JSON, return parsed JSON response.

    Kept as a private helper so the two Lark API call sites share one
    timeout/encoding policy. Doesn't catch URLError — callers decide.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    base_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=base_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def get_tenant_access_token(app_id: str, app_secret: str, timeout: int = 8) -> str:
    """Fetch a tenant_access_token from Lark, raise on error.

    Per Lark Open API docs, ``tenant_access_token`` lasts ~2 hours and
    is the right one for a custom self-built app DMing its publisher.
    We don't cache it — daily summaries fire once per day, so a fresh
    token per call is the simplest correct behaviour.
    """
    url = f"{LARK_BASE_URL}/auth/v3/tenant_access_token/internal"
    resp = _http_post_json(
        url,
        {"app_id": app_id, "app_secret": app_secret},
        timeout=timeout,
    )
    if resp.get("code") != 0:
        # Strip the token field if it ever appears in error envelopes
        # before raising — defence in depth against accidental log leaks.
        msg = resp.get("msg", "unknown")
        raise LarkAPIError(f"tenant_access_token: code={resp.get('code')} msg={msg}")
    token = resp.get("tenant_access_token")
    if not token:
        raise LarkAPIError("tenant_access_token: response missing token field")
    return token


def send_lark_dm(message_text: str, creds: LarkCredentials, timeout: int = 8) -> dict[str, Any]:
    """Send a text DM to ``creds.user_open_id``.

    Returns the Lark API response body so the caller can detect partial
    failures (e.g. bot not authorised to message that user). Raises
    :class:`LarkAPIError` on non-zero API code, :class:`urllib.error.URLError`
    on network issues.
    """
    token = get_tenant_access_token(creds.app_id, creds.app_secret, timeout=timeout)
    url = f"{LARK_BASE_URL}/im/v1/messages?receive_id_type=open_id"
    payload = {
        "receive_id": creds.user_open_id,
        "msg_type": "text",
        # The ``content`` field is itself a JSON-encoded string per the
        # Lark API contract — not a nested object.
        "content": json.dumps({"text": message_text}, ensure_ascii=False),
    }
    resp = _http_post_json(
        url,
        payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    if resp.get("code") != 0:
        raise LarkAPIError(f"send DM: code={resp.get('code')} msg={resp.get('msg')}")
    return resp


# ---------------------------------------------------------------------------
# Daily summary body
# ---------------------------------------------------------------------------


_NAV_DTYPE = {
    "date": str,
    "account_id": str,
    "benchmark_code": str,
    "benchmark_date": str,
}


# Per-market display config. Phase 2/3 added 'hk' + 'us'; A-share's
# entry preserves the prior behavior (¥ currency, ¥1M starting cash).
MARKET_LABELS: dict[str, str] = {
    "a_share": "A股",
    "hk": "港股",
    "us": "美股",
}
MARKET_CURRENCY: dict[str, str] = {
    "a_share": "¥",
    "hk": "HK$",
    "us": "$",
}
MARKET_INITIAL_CASH: dict[str, float] = {
    "a_share": 1_000_000.0,
    "hk": 1_000_000.0,   # HK$1M
    "us": 150_000.0,
}


def _format_nav_section(
    agent_ids: list[str],
    repo_root: Path,
    market: str = "a_share",
) -> list[str]:
    """Per-agent NAV + 1-day delta for a single market."""
    label = MARKET_LABELS.get(market, market)
    currency = MARKET_CURRENCY.get(market, "")
    baseline_cash = MARKET_INITIAL_CASH.get(market, 1_000_000.0)
    baseline_disp = f"{currency}{baseline_cash / 1000:.0f}K" if baseline_cash < 500_000 else f"{currency}{baseline_cash / 1000_000:.0f}M"

    lines = [f"💰 {label} NAV:"]
    for agent in agent_ids:
        nav_path = repo_root / "data" / market / agent / "daily_nav.csv"
        if not nav_path.exists():
            lines.append(f"  {agent:<7} (尚未初始化)")
            continue
        try:
            df = pd.read_csv(nav_path, dtype=_NAV_DTYPE)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {agent:<7} (读取失败: {exc.__class__.__name__})")
            continue
        if df.empty or "total_value" not in df.columns:
            lines.append(f"  {agent:<7} (NAV 数据为空)")
            continue
        per_day = df.groupby("date")["total_value"].sum().sort_index()
        latest = per_day.iloc[-1]
        pct_vs_baseline = (latest / baseline_cash - 1.0) * 100
        delta_line = ""
        if len(per_day) >= 2:
            prev = per_day.iloc[-2]
            if prev > 0:
                pct_1d = (latest / prev - 1.0) * 100
                delta_line = f"  Δ {pct_1d:+.2f}%"
        lines.append(
            f"  {agent:<7} {currency}{latest:>11,.0f}  ({pct_vs_baseline:+.2f}% vs {baseline_disp}){delta_line}"
        )
    return lines


def _format_positions_section(
    agent_ids: list[str],
    repo_root: Path,
    market: str = "a_share",
) -> list[str]:
    """Per-account holdings breakdown for a single market."""
    label = MARKET_LABELS.get(market, market)
    lines = [f"📈 {label} 持仓:"]
    for agent in agent_ids:
        pos_path = repo_root / "data" / market / agent / "positions.csv"
        if not pos_path.exists():
            lines.append(f"  {agent:<7} (尚未初始化)")
            continue
        try:
            df = pd.read_csv(pos_path, dtype={"code": str})
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {agent:<7} (读取失败: {exc.__class__.__name__})")
            continue
        n = len(df)
        if n == 0:
            lines.append(f"  {agent:<7} (0 持仓)")
            continue
        if "account_id" in df.columns:
            by_acct = df.groupby("account_id").size().sort_index()
            breakdown = "  ".join(f"{k}={v}" for k, v in by_acct.items())
        else:
            breakdown = f"total={n}"
        tag = " ✓" if n >= TARGET_HOLDINGS_PER_AGENT else " ⚠️"
        lines.append(
            f"  {agent:<7} {breakdown}  (={n}/{TARGET_HOLDINGS_PER_AGENT}){tag}"
        )
    return lines


def _format_sanity_section(
    agent_ids: list[str],
    repo_root: Path,
    market: str = "a_share",
) -> list[str]:
    """Run sanity_check per agent, format worst severity + highlights."""
    label = MARKET_LABELS.get(market, market)
    lines = [f"✅ {label} Sanity-check:"]
    for agent in agent_ids:
        try:
            # sanity_check.check_agent gained a `market` kwarg in Phase 1 T8
            # but the body currently still reads a_share paths. We pass
            # market explicitly so future-proof; today's behavior is
            # equivalent for a_share, and for hk/us the check gracefully
            # treats missing data as info-level.
            findings = check_agent(agent, repo_root=repo_root, market=market)
        except TypeError:
            # Fallback for older sanity_check that didn't accept `market`
            findings = check_agent(agent, repo_root=repo_root)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {agent:<7} (sanity-check 抛错: {exc.__class__.__name__})")
            continue
        if not findings:
            lines.append(f"  {agent:<7} ✓ no anomalies")
            continue
        critical_count = sum(1 for f in findings if f.severity == "critical")
        warn_count = sum(1 for f in findings if f.severity == "warn")
        info_count = sum(1 for f in findings if f.severity == "info")
        bucket_strs = []
        if critical_count:
            bucket_strs.append(f"{critical_count} critical")
        if warn_count:
            bucket_strs.append(f"{warn_count} warn")
        if info_count:
            bucket_strs.append(f"{info_count} info")
        lines.append(f"  {agent:<7} {', '.join(bucket_strs)}")
        for f in findings:
            if f.severity in ("warn", "critical"):
                msg = f.message
                if len(msg) > 80:
                    msg = msg[:77] + "..."
                lines.append(f"    [{f.severity.upper()}] {f.check_name}: {msg}")
    return lines


def _months_first_day(d: date, *, offset: int = 1) -> date:
    """Return the 1st of the month ``offset`` months ahead of ``d``."""
    year = d.year
    month = d.month + offset
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)


def _most_recent_friday_on_or_before(d: date) -> date:
    """Return the most recent Friday on/before ``d`` (Mon=0..Sun=6 → Fri=4)."""
    days_since_fri = (d.weekday() - 4) % 7
    return d - timedelta(days=days_since_fri)


def collect_pending_actions(
    agent_ids: list[str], repo_root: Path, today_d: date
) -> list[str]:
    """Return human-readable pending action strings.

    Checks (each gated by date so noise stays low):

    - **Saturday/Sunday**: if this week's Friday sentiment row is missing
      for any agent, flag it. The operator runs ``weekly.sh`` on
      Saturday which records sentiment; this is the safety-net reminder.
    - **3 days before month start**: heads-up that ``monthly-review``
      timer fires on the 1st (so LLM evolution can prep notes).
    - **Tier-1+2 fix awaiting effect**: if all agents are at <100
      holdings AND the next rebalance is within 2 days, note that the
      simulator fix should auto-fill the gap.

    Returns ``[]`` on weekdays with no pending items — silence is fine.
    """
    actions: list[str] = []

    # 1. Weekend sentiment record reminder
    if today_d.weekday() >= 5:  # Saturday=5, Sunday=6
        this_friday = _most_recent_friday_on_or_before(today_d)
        for agent in agent_ids:
            sentiment_csv = (
                repo_root / "data" / "a_share" / agent / "alt_factors" / "market_sentiment.csv"
            )
            if not sentiment_csv.exists():
                actions.append(
                    f"sentiment 未记录过 ({agent}) — 周末跑 weekly.sh 把 {this_friday.isoformat()} 那周补上"
                )
                continue
            try:
                df = pd.read_csv(sentiment_csv, dtype={"week_end": str})
                recorded_weeks = set(df.get("week_end", pd.Series([], dtype=str)).astype(str))
            except Exception:
                continue
            if this_friday.isoformat() not in recorded_weeks:
                actions.append(
                    f"sentiment 缺 week_end={this_friday.isoformat()} ({agent}) — 跑 weekly.sh 或 record-sentiment"
                )

    # 2. Monthly-review countdown
    next_first = _months_first_day(today_d, offset=1)
    days_to_next_first = (next_first - today_d).days
    if 0 <= days_to_next_first <= 3:
        weekday_cn = "一二三四五六日"[next_first.weekday()]
        actions.append(
            f"{next_first.isoformat()} (周{weekday_cn}) monthly-review timer 触发"
            f"，提前看看 sentiment 历史 + 对手 overlay"
        )

    return actions


def _collect_ecs_timeline(today_d: date) -> list[str]:
    """Best-effort: parse today's stock-analyze-* journal events.

    Calls ``journalctl --since 'today 00:00' -u 'stock-analyze-*'`` via
    subprocess. Silently omits the section if journalctl isn't on PATH
    (e.g. local Mac dev) or returns nothing useful.
    """
    try:
        result = subprocess.run(
            [
                "journalctl",
                "--since",
                f"{today_d.isoformat()} 00:00",
                "-u",
                "stock-analyze-*",
                "--no-pager",
                "-o",
                "short-iso",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    # Pick out "Starting" + "Finished" + "Failed" events to build a clean timeline
    interesting: list[str] = []
    for line in result.stdout.splitlines():
        # Match e.g. "2026-05-26T17:25:06+0800 host systemd[1]: Starting ..."
        # Keep it short — just hh:mm + event + unit name.
        for keyword in (" Started ", " Starting ", " Finished ", " Failed "):
            if keyword in line:
                # Crude extract: split on the systemd[1]: prefix.
                if ": " in line:
                    rest = line.split(": ", 1)[1]
                    # Trim trailing dot + unit description tail
                    rest = rest.split(" - ", 1)[0]
                    # Time field at column 11..15 in short-iso ("HH:MM")
                    parts = line.split("T", 1)
                    hhmm = parts[1][:5] if len(parts) > 1 else "??:??"
                    tag = "✓" if "Finished" in keyword or "Started" in keyword else (
                        "→" if "Starting" in keyword else "✗"
                    )
                    # Don't duplicate Starting + Finished — keep only Started/Finished/Failed
                    if "Starting" in keyword:
                        continue
                    interesting.append(f"  {tag} {hhmm}  {rest}")
                break
    # De-duplicate consecutive identical lines (systemd sometimes logs twice)
    deduped: list[str] = []
    for item in interesting:
        if not deduped or deduped[-1] != item:
            deduped.append(item)
    if not deduped:
        return []
    return ["🕐 ECS 自动化（今天）:"] + deduped[:12]  # cap at 12 lines


def _collect_recent_failures(today_d: date, lookback_days: int = 2) -> list[str]:
    """Scan PIPELINE_FAILURES.log for entries dated in the recent window."""
    log_path = Path("/opt/stock-analyze/logs/PIPELINE_FAILURES.log")
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    window_dates = {
        (today_d - timedelta(days=i)).isoformat() for i in range(lookback_days + 1)
    }
    recent: list[str] = []
    for line in text.splitlines():
        if "\tFAILED\t" not in line:
            continue
        # Format: "<iso-timestamp>\tFAILED\t<unit>"
        ts_field = line.split("\t", 1)[0]
        date_part = ts_field[:10]
        if date_part in window_dates:
            recent.append(f"  {line}")
    if not recent:
        return []
    return [f"🚨 近 {lookback_days + 1} 天 PIPELINE_FAILURES:"] + recent[-5:]


def build_daily_summary(
    agent_ids: list[str],
    repo_root: Path | None = None,
    today_d: date | None = None,
    *,
    markets: list[str] | None = None,
) -> str:
    """Assemble the full daily summary text body.

    Pure function (no I/O beyond reading repo files + an optional
    journalctl subprocess) so it's straightforward to test against
    a hand-crafted ``repo_root`` directory.

    ``markets`` defaults to ``["a_share"]`` for backward compatibility.
    Phase 2/3 callers pass ``["a_share", "hk", "us"]`` for the cross-market
    summary; each market gets its own NAV / 持仓 / Sanity blocks with
    market-label prefixes (``💰 A股 NAV``, ``💰 港股 NAV``, ``💰 美股 NAV``).
    """
    repo_root = Path(repo_root) if repo_root else Path.cwd()
    today_d = today_d or _today()
    weekday_cn = "一二三四五六日"[today_d.weekday()]
    markets = markets if markets is not None else ["a_share"]

    sections: list[str] = []
    sections.append(f"📊 Stock-Analyze 日报 {today_d.isoformat()} (周{weekday_cn})")
    sections.append("─" * 30)

    timeline = _collect_ecs_timeline(today_d)
    if timeline:
        sections.append("")
        sections.extend(timeline)

    # Single-market path emits the legacy header format (no 市场 prefix) so
    # existing tests + single-market deployments stay byte-equivalent.
    # Multi-market path always shows the market label.
    if len(markets) == 1:
        market = markets[0]
        sections.append("")
        nav_lines = _format_nav_section(agent_ids, repo_root, market)
        if market == "a_share":
            # Back-compat: original header was "💰 NAV" without market label.
            nav_lines[0] = "💰 NAV:"
        sections.extend(nav_lines)

        sections.append("")
        pos_lines = _format_positions_section(agent_ids, repo_root, market)
        if market == "a_share":
            pos_lines[0] = "📈 持仓:"
        sections.extend(pos_lines)

        sections.append("")
        san_lines = _format_sanity_section(agent_ids, repo_root, market)
        if market == "a_share":
            san_lines[0] = "✅ Sanity-check:"
        sections.extend(san_lines)
    else:
        for market in markets:
            sections.append("")
            sections.extend(_format_nav_section(agent_ids, repo_root, market))
            sections.append("")
            sections.extend(_format_positions_section(agent_ids, repo_root, market))
            sections.append("")
            sections.extend(_format_sanity_section(agent_ids, repo_root, market))

    pending = collect_pending_actions(agent_ids, repo_root, today_d)
    if pending:
        sections.append("")
        sections.append("⏰ 待办:")
        for item in pending:
            sections.append(f"  • {item}")

    failures = _collect_recent_failures(today_d)
    if failures:
        sections.append("")
        sections.extend(failures)

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def cli_send_daily_summary(repo_root: Path | None = None) -> int:
    """CLI entry: build summary, optionally send via Lark DM.

    Behaviour matrix:

    - Credentials present + Lark reachable: send DM, print "✓ sent", exit 0.
    - Credentials present + Lark error: print error to stderr, exit 1
      (systemd will mark the unit failed → group webhook fires).
    - Credentials absent: print the summary to stdout, exit 0 (preview mode).
      This lets the operator install the script before configuring auth.
    """
    # Phase 2/3 multi-market: include all configured markets in the daily DM.
    # Each gets its own NAV / 持仓 / Sanity block with the market label
    # prefix. If a market has no data on disk yet (cold-start) it shows
    # "尚未初始化" and doesn't block the other markets.
    from . import competition as _competition
    markets_to_include = list(_competition.MARKETS)
    summary = build_daily_summary(
        ["claude", "codex"],
        repo_root=repo_root,
        markets=markets_to_include,
    )

    creds = LarkCredentials.from_env()
    if creds is None:
        print(summary)
        print(file=sys.stderr)
        print(
            "(SA_LARK_APP_ID / SA_LARK_APP_SECRET / SA_LARK_USER_OPEN_ID not all set; "
            "skipping Lark push — preview only.)",
            file=sys.stderr,
        )
        return 0

    try:
        send_lark_dm(summary, creds)
    except (urllib.error.URLError, LarkAPIError) as exc:
        # systemd journal captures stderr; never log the secret itself.
        print(f"Lark push failed: {exc}", file=sys.stderr)
        # Still print the message body so an operator inspecting
        # journalctl sees what would have been sent.
        print(file=sys.stderr)
        print(summary, file=sys.stderr)
        return 1

    print("✓ daily summary sent via Lark")
    return 0


__all__ = [
    "COMPETITION_INITIAL_CASH",
    "TARGET_HOLDINGS_PER_AGENT",
    "LarkAPIError",
    "LarkCredentials",
    "build_daily_summary",
    "cli_send_daily_summary",
    "collect_pending_actions",
    "get_tenant_access_token",
    "send_lark_dm",
]
