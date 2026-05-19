from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
from datetime import date
from functools import partial
from pathlib import Path

from . import competition
from .agent_briefing import (
    build_monthly_briefing,
    build_weekly_briefing,
    monthly_briefing_path,
    weekly_briefing_path,
    write_briefing,
)
from .competition import CompetitionBaselineLocked
from .config import load_config
from .data_provider import AkshareProvider
from .dashboard_aggregator import generate_competition_dashboard
from .diagnostics import compute_pending_forward_ic
from .monthly_review import compute_review, default_month_for, write_review
from .reporting import generate_dashboard, generate_weekly_report
from .run_ledger import RunLedger
from .simulator import execute_due_orders, generate_rebalance_orders, initialize, update_nav
from .store import PortfolioStore
from .utils import ensure_dirs, write_json


COMPETITION_METADATA_FILE = "competition_metadata.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share forward simulation toolkit")
    parser.add_argument("--config", default=None, help="Path to strategy config (default: configs/strategy_v1.yaml or --agent overlay)")
    parser.add_argument("--data-dir", default=None, help="Data directory (default: data/ or data/<agent>)")
    parser.add_argument("--reports-dir", default=None, help="Reports directory (default: reports/ or reports/<agent>)")
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--agent", default=None, help="Competition agent id (claude|codex). Implies competition mode and routes paths.")
    parser.add_argument("--as-of", help="Override run date in YYYY-MM-DD format")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize runtime state for the configured config")
    sub.add_parser("rebalance", help="Generate weekly signals and pending orders")
    sub.add_parser("execute", help="Execute pending orders whose date is due")
    sub.add_parser("update-nav", help="Update account NAV")
    sub.add_parser("report", help="Generate weekly report")
    sub.add_parser("dashboard", help="Generate dashboard HTML (page mode)")
    sub.add_parser("run-daily", help="Execute due orders, update NAV, refresh dashboard")
    sub.add_parser("run-weekly", help="Generate signals, update NAV, report, and dashboard")
    sub.add_parser("competition-init", help="Initialize all competition agents and shared directories")
    review = sub.add_parser("competition-monthly-review", help="Compute and persist the monthly comparison review")
    review.add_argument("--month", help="Target month in YYYY-MM (default: previous calendar month)")
    review.add_argument("--agents", nargs="*", help="Subset of agent ids to review (default: all)")
    sub.add_parser("competition-dashboard", help="Render the three-tab competition dashboard")
    prep_weekly = sub.add_parser("agent-prepare-weekly", help="Write the weekly briefing markdown for an agent")
    prep_weekly.add_argument("--agent", required=True)
    prep_weekly.add_argument("--as-of", dest="briefing_as_of", help="Override briefing date (YYYY-MM-DD)")
    prep_monthly = sub.add_parser("agent-prepare-monthly", help="Write the monthly briefing markdown for an agent")
    prep_monthly.add_argument("--agent", required=True)
    prep_monthly.add_argument("--month", help="Target month YYYY-MM (default: previous calendar month)")
    serve = sub.add_parser("serve-dashboard", help="Serve reports directory on localhost")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def _resolve_runtime(args: argparse.Namespace) -> tuple[dict | None, str, str, Path]:
    """Return (config, data_dir, reports_dir, cache_dir).

    For competition agent mode, config is loaded via competition.load and the
    cache lives under data/shared/cache. For legacy single-agent mode, config
    falls back to configs/strategy_v1.yaml and the cache lives under
    <data-dir>/cache.

    Returns ``config=None`` when the command does not need a strategy config
    (e.g. serve-dashboard, competition-init, competition-monthly-review,
    competition-dashboard handle their own resolution).
    """

    explicit_config = args.config is not None
    if args.agent:
        if explicit_config:
            raise CompetitionBaselineLocked(
                field="agent_config_override",
                baseline_value=f"configs/agents/{args.agent}.yaml",
                overlay_value=args.config,
            )
        paths = competition.resolve_agent_paths(args.agent)
        cfg = competition.load(args.agent)
        data_dir = args.data_dir or str(paths.data_dir)
        reports_dir = args.reports_dir or str(paths.reports_dir)
        cache_dir = paths.shared_cache_dir
    else:
        cfg_path = args.config or "configs/strategy_v1.yaml"
        cfg = load_config(cfg_path)
        data_dir = args.data_dir or "data"
        reports_dir = args.reports_dir or "reports"
        cache_dir = Path(data_dir) / "cache"
    return cfg, data_dir, reports_dir, cache_dir


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve-dashboard":
        reports_dir = args.reports_dir or "reports"
        if args.agent and args.reports_dir is None:
            reports_dir = str(competition.resolve_agent_paths(args.agent).reports_dir)
        ensure_dirs(reports_dir, args.logs_dir)
        return serve_dashboard(reports_dir, args.host, args.port)
    if args.command == "competition-init":
        ensure_dirs(args.logs_dir)
        return _command_competition_init()
    if args.command == "competition-monthly-review":
        ensure_dirs(args.logs_dir)
        return _command_competition_monthly_review(args)
    if args.command == "competition-dashboard":
        ensure_dirs(args.logs_dir)
        return _command_competition_dashboard()
    if args.command == "agent-prepare-weekly":
        ensure_dirs(args.logs_dir)
        return _command_agent_prepare_weekly(args)
    if args.command == "agent-prepare-monthly":
        ensure_dirs(args.logs_dir)
        return _command_agent_prepare_monthly(args)

    try:
        config, data_dir, reports_dir, cache_dir = _resolve_runtime(args)
    except CompetitionBaselineLocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ensure_dirs(data_dir, reports_dir, args.logs_dir)

    store = PortfolioStore(data_dir)
    provider = AkshareProvider(cache_dir=cache_dir)
    ledger = RunLedger(data_dir)
    migration_notes = (config or {}).get("_migration_notes") or []
    if migration_notes:
        print(f"config migration applied: {', '.join(migration_notes)}")

    try:
        with ledger.run(args.command, args.as_of, config) as context:
            run_id = context["run_id"]
            if args.command == "init":
                initialize(config, store)
                print(f"Initialized {data_dir}")
            elif args.command == "rebalance":
                batches = generate_rebalance_orders(config, store, provider, as_of=args.as_of, run_id=run_id)
                print(f"Generated {sum(len(batch.get('orders', [])) for batch in batches)} pending orders")
            elif args.command == "execute":
                trades = execute_due_orders(config, store, provider, as_of=args.as_of)
                print(f"Executed {len(trades)} trades")
            elif args.command == "update-nav":
                rows = update_nav(config, store, provider, as_of=args.as_of)
                print(f"Updated NAV for {len(rows)} accounts")
            elif args.command == "report":
                path = generate_weekly_report(config, store, reports_dir, run_id=run_id)
                print(f"Report written to {path}")
            elif args.command == "dashboard":
                page_path = generate_dashboard(config, store, reports_dir)
                fragment_path = generate_dashboard(config, store, reports_dir, mode="fragment")
                print(f"Dashboard written to {page_path}; fragment {fragment_path}")
            elif args.command == "run-daily":
                trades = execute_due_orders(config, store, provider, as_of=args.as_of)
                rows = update_nav(config, store, provider, as_of=args.as_of, notes=f"daily; trades={len(trades)}")
                compute_pending_forward_ic(config, store, provider, as_of=args.as_of)
                provider.persist_health()
                page_path = generate_dashboard(config, store, reports_dir)
                generate_dashboard(config, store, reports_dir, mode="fragment")
                print(f"Daily run complete: trades={len(trades)}, nav_rows={len(rows)}, dashboard={page_path}")
            elif args.command == "run-weekly":
                batches = generate_rebalance_orders(config, store, provider, as_of=args.as_of, run_id=run_id)
                rows = update_nav(config, store, provider, as_of=args.as_of, notes="weekly signal")
                compute_pending_forward_ic(config, store, provider, as_of=args.as_of)
                provider.persist_health()
                report = generate_weekly_report(config, store, reports_dir, run_id=run_id)
                dashboard = generate_dashboard(config, store, reports_dir)
                generate_dashboard(config, store, reports_dir, mode="fragment")
                briefing = _auto_write_weekly_briefing(args.agent, args.as_of)
                briefing_note = f", briefing={briefing}" if briefing else ""
                print(f"Weekly run complete: batches={len(batches)}, nav_rows={len(rows)}, report={report}, dashboard={dashboard}{briefing_note}")
            else:
                parser.error(f"Unknown command: {args.command}")
    finally:
        provider.persist_health()
    return 0


def _command_competition_init() -> int:
    repo_root = Path.cwd()
    agents = competition.list_agents(repo_root)
    if not agents:
        print("error: no agent overlays found under configs/agents/", file=sys.stderr)
        return 2
    baseline = competition.load_baseline(repo_root)
    shared_cache = repo_root / "data" / "shared" / "cache"
    competition_data = repo_root / "data" / "competition"
    competition_reports = repo_root / "reports" / "competition"
    ensure_dirs(shared_cache, competition_data, competition_reports)

    for agent in agents:
        try:
            merged = competition.load(agent, repo_root=repo_root)
        except CompetitionBaselineLocked as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        paths = competition.resolve_agent_paths(agent, repo_root=repo_root)
        ensure_dirs(paths.data_dir, paths.reports_dir)
        store = PortfolioStore(paths.data_dir)
        store.initialize(merged)
        print(f"agent={agent}: initialized {paths.data_dir}")

    metadata = {
        "competition_id": baseline.get("competition_id"),
        "start_date": baseline.get("start_date"),
        "baseline_hash": competition.baseline_hash(baseline),
        "agents": agents,
        "initialized_at": date.today().isoformat(),
    }
    write_json(competition_data / COMPETITION_METADATA_FILE, metadata)
    print(f"Competition initialized: {metadata['competition_id']} start={metadata['start_date']} agents={agents}")
    return 0


def _command_competition_monthly_review(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    agents = args.agents or competition.list_agents(repo_root)
    if not agents:
        print("error: no agent overlays found under configs/agents/", file=sys.stderr)
        return 2
    month = args.month or default_month_for()
    payload = compute_review(month, agents, repo_root=repo_root)
    baseline = competition.load_baseline(repo_root)
    payload["competition_id"] = baseline.get("competition_id")
    json_path, md_path, leaderboard_path = write_review(payload, repo_root=repo_root)
    print(
        f"Monthly review written: month={month} json={json_path} md={md_path} leaderboard={leaderboard_path}"
    )
    for agent_id in agents:
        try:
            paths = competition.resolve_agent_paths(agent_id, repo_root=repo_root)
            briefing_text = build_monthly_briefing(agent_id, month, repo_root=repo_root)
            target = monthly_briefing_path(paths, month)
            write_briefing(briefing_text, target)
            print(f"agent={agent_id}: monthly briefing -> {target}")
        except Exception as exc:  # noqa: BLE001
            print(f"warning: failed to write monthly briefing for {agent_id}: {exc}", file=sys.stderr)
    return 0


def _command_competition_dashboard() -> int:
    repo_root = Path.cwd()
    agents = competition.list_agents(repo_root)
    if not agents:
        print("error: no agent overlays found under configs/agents/", file=sys.stderr)
        return 2
    out_path = generate_competition_dashboard(agents=agents, repo_root=repo_root)
    print(f"Competition dashboard written: {out_path}")
    return 0


def _command_agent_prepare_weekly(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    agent_id = args.agent
    as_of = getattr(args, "briefing_as_of", None)
    try:
        paths = competition.resolve_agent_paths(agent_id, repo_root=repo_root)
    except competition.UnknownAgent as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = build_weekly_briefing(agent_id, as_of=as_of, repo_root=repo_root)
    target = weekly_briefing_path(paths, as_of=as_of)
    write_briefing(text, target)
    print(f"Weekly briefing written: {target}")
    return 0


def _command_agent_prepare_monthly(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    agent_id = args.agent
    month = args.month or default_month_for()
    try:
        paths = competition.resolve_agent_paths(agent_id, repo_root=repo_root)
    except competition.UnknownAgent as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = build_monthly_briefing(agent_id, month, repo_root=repo_root)
    target = monthly_briefing_path(paths, month)
    write_briefing(text, target)
    print(f"Monthly briefing written: {target}")
    return 0


def _auto_write_weekly_briefing(agent_id: str | None, as_of: str | None) -> str | None:
    if not agent_id:
        return None
    repo_root = Path.cwd()
    try:
        paths = competition.resolve_agent_paths(agent_id, repo_root=repo_root)
    except competition.UnknownAgent:
        return None
    try:
        text = build_weekly_briefing(agent_id, as_of=as_of, repo_root=repo_root)
        target = weekly_briefing_path(paths, as_of=as_of)
        write_briefing(text, target)
        return str(target)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: failed to write weekly briefing for {agent_id}: {exc}", file=sys.stderr)
        return None


def serve_dashboard(reports_dir: str, host: str, port: int) -> int:
    directory = Path(reports_dir).resolve()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with socketserver.TCPServer((host, port), handler) as httpd:
        print(f"Serving {directory} at http://{host}:{port}")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
