from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
from datetime import date
from functools import partial
from pathlib import Path
from urllib.parse import parse_qs

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
from .dashboard_aggregator import generate_competition_dashboard
# Per-market run primitives (make_provider / initialize / generate_rebalance_orders
# / execute_due_orders / update_nav) are dispatched at call time via
# competition.get_market_module(market); see main(). compute_pending_forward_ic
# is an A-share diagnostic (forward IC) and stays A-share-only for now.
from .markets.a_share.diagnostics import compute_pending_forward_ic
from .monthly_review import compute_review, default_month_for, write_review
from .overlay_guard import (
    OverlayBaselineLocked,
    OverlayGuardError,
    validate as validate_overlay_guard,
)
from .reporting import generate_dashboard, generate_weekly_report
from .run_ledger import RunLedger
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
    parser.add_argument(
        "--market",
        choices=competition.MARKETS,
        default="a_share",
        help="Account range (a_share | cn_qdii_etf). Default: a_share.",
    )
    parser.add_argument("--as-of", help="Override run date in YYYY-MM-DD format")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize runtime state for the configured config")
    sub.add_parser("rebalance", help="Generate weekly signals and pending orders")
    sub.add_parser("execute", help="Execute pending orders whose date is due")
    sub.add_parser("update-nav", help="Update account NAV")
    sub.add_parser("report", help="Generate weekly report")
    sub.add_parser("dashboard", help="Generate dashboard HTML (page mode)")
    prepare = sub.add_parser(
        "prepare-market-data",
        help="Fetch shared market data once for the day; both agents subsequently run --offline",
    )
    prepare.add_argument("--scopes", nargs="*", help="Index scopes to fetch (default: union of baseline accounts)")
    prepare.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if today's snapshot already exists",
    )
    prepare.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="ThreadPoolExecutor size for per-candidate fetch (default: 5)",
    )
    daily = sub.add_parser("run-daily", help="Execute due orders, update NAV, refresh dashboard")
    daily.add_argument(
        "--offline",
        action="store_true",
        help="Forbid the provider from reaching the network — cache miss raises CacheMiss and fails the run.",
    )
    weekly = sub.add_parser("run-weekly", help="Generate signals, update NAV, report, and dashboard")
    weekly.add_argument(
        "--offline",
        action="store_true",
        help="Forbid the provider from reaching the network — cache miss raises CacheMiss and fails the run.",
    )
    sub.add_parser("competition-init", help="Initialize all competition agents and shared directories")
    review = sub.add_parser("competition-monthly-review", help="Compute and persist the monthly comparison review")
    review.add_argument("--month", help="Target month in YYYY-MM (default: previous calendar month)")
    review.add_argument("--agents", nargs="*", help="Subset of agent ids to review (default: all)")
    dashboard = sub.add_parser("competition-dashboard", help="Render the competition dashboard")
    dashboard.add_argument(
        "--market",
        dest="dashboard_market",
        choices=["all", *competition.MARKETS],
        default="all",
        help="Dashboard market scope (default: all).",
    )
    prep_weekly = sub.add_parser("agent-prepare-weekly", help="Write the weekly briefing markdown for an agent")
    prep_weekly.add_argument("--agent", required=True)
    prep_weekly.add_argument("--as-of", dest="briefing_as_of", help="Override briefing date (YYYY-MM-DD)")
    prep_monthly = sub.add_parser("agent-prepare-monthly", help="Write the monthly briefing markdown for an agent")
    prep_monthly.add_argument("--agent", required=True)
    prep_monthly.add_argument("--month", help="Target month YYYY-MM (default: previous calendar month)")
    validate = sub.add_parser(
        "validate-overlay",
        help="Run overlay_guard checks on configs/agents/<agent>.yaml (schema + lock fields only).",
    )
    validate.add_argument("--agent", required=True, help="Agent overlay to validate (claude|codex).")
    rollback = sub.add_parser("agent-rollback", help="Rollback an agent overlay to a historical config hash")
    rollback.add_argument("--agent", required=True)
    rollback.add_argument("--to", required=True, help="Config hash saved under configs/agents/_history/")
    serve = sub.add_parser("serve-dashboard", help="Serve reports directory on localhost")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    prep_bt = sub.add_parser(
        "prepare-backtest-data",
        help="One-time batch fetch of 5y A-share market data from Tushare into backtest_cache/",
    )
    prep_bt.add_argument("--start", type=_parse_iso_date, required=True,
                          help="Start date (YYYY-MM-DD).")
    prep_bt.add_argument("--end", type=_parse_iso_date, required=True,
                          help="End date (YYYY-MM-DD).")
    prep_bt.add_argument(
        "--cache-root",
        type=Path,
        default=Path("data/shared/backtest_cache"),
        help="Where the cache lives (default: data/shared/backtest_cache).",
    )
    prep_bt.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch even if already cached.",
    )

    bt = sub.add_parser(
        "backtest",
        help="Run a historical backtest of an overlay over an arbitrary window.",
    )
    bt.add_argument("--agent", required=True, choices=["claude", "codex"],
                     help="Agent the backtest belongs to (paths under data/<agent>/).")
    bt.add_argument("--start", type=_parse_iso_date, required=True,
                     help="Start date (YYYY-MM-DD).")
    bt.add_argument("--end", type=_parse_iso_date, required=True,
                     help="End date (YYYY-MM-DD).")
    bt.add_argument("--overlay", type=Path, required=True,
                     help="Path to overlay JSON/YAML (configs/agents/<agent>.yaml).")
    bt.add_argument("--output", type=Path, required=True,
                     help="Output directory for backtest products.")
    bt.add_argument("--in-memory", action="store_true",
                     help="Skip per-day disk writes; only emit final outputs.")
    bt.add_argument(
        "--universe",
        choices=["hs300", "zz500", "both"],
        default="both",
        help="Universe (default: both = hs300 + zz500).",
    )
    bt.add_argument(
        "--cache-root",
        type=Path,
        default=Path("data/shared/backtest_cache"),
        help="Where backtest_cache lives (default: data/shared/backtest_cache).",
    )
    bt.add_argument(
        "--compare-mvp",
        action="store_true",
        help="Also run the MVP low-PE proxy over the same window and append a "
             "full-pipeline-vs-MVP comparison panel to report.md.",
    )

    rec = sub.add_parser(
        "record-sentiment",
        help="Record one week of operator-curated market sentiment from LLM client.",
    )
    rec.add_argument(
        "--market",
        dest="sentiment_market",
        choices=competition.MARKETS,
        default=None,
        help="Market namespace for the sentiment row (default: global --market or a_share).",
    )
    rec.add_argument("--agent", required=True, choices=["claude", "codex"])
    rec.add_argument("--week-end", type=_parse_iso_date, required=True,
                      help="Friday-end of the analysed week (YYYY-MM-DD).")
    rec.add_argument("--score", type=float, required=True,
                      help="Sentiment score in [-1.0, 1.0].")
    rec.add_argument("--confidence", type=float, required=True,
                      help="LLM self-rated confidence in [0.0, 1.0].")
    rec.add_argument("--drivers", required=True,
                      help="Comma-separated key drivers (1..5).")
    rec.add_argument("--sources", default="",
                      help="Pipe-separated source URLs (optional).")
    rec.add_argument("--llm-model", required=True,
                      help="LLM model identifier, e.g. claude-sonnet-4.5.")
    rec.add_argument("--prompt-version", default="v1",
                      help="Prompt template version (default v1).")
    rec.add_argument("--force", action="store_true",
                      help="Overwrite an existing row for the same week_end.")

    # Phase 3: per-industry sentiment (a real per-stock factor, unlike the
    # broadcast market sentiment above). The operator's LLM scores each
    # industry; --json carries the batch.
    recsec = sub.add_parser(
        "record-sector-sentiment",
        help="Record one week of per-industry sentiment (Phase 3 per-stock factor).",
    )
    recsec.add_argument(
        "--market",
        dest="sentiment_market",
        choices=competition.MARKETS,
        default=None,
        help="Market namespace for the sector rows (default: global --market or a_share).",
    )
    recsec.add_argument("--agent", required=True, choices=["claude", "codex"])
    recsec.add_argument("--week-end", type=_parse_iso_date, required=True,
                         help="Friday-end of the analysed week (YYYY-MM-DD).")
    recsec.add_argument("--json", dest="sectors_json", default=None,
                         help='Inline JSON: {"sectors":[{"industry":"银行","score":0.3,'
                              '"confidence":0.8}, ...], "llm_model":"..."}.')
    recsec.add_argument("--json-file", type=Path, default=None,
                         help="Path to a JSON file with the same shape as --json.")
    recsec.add_argument("--llm-model", default=None,
                         help="LLM model id (overrides the JSON's llm_model if both set).")
    recsec.add_argument("--prompt-version", default="sector_v1")
    recsec.add_argument("--force", action="store_true",
                         help="Overwrite existing rows for the same week_end.")

    slog = sub.add_parser(
        "sentiment-log",
        help="Inspect / remove sentiment history rows.",
    )
    slog.add_argument(
        "--market",
        dest="sentiment_market",
        choices=competition.MARKETS,
        default=None,
        help="Market namespace to inspect/remove (default: global --market or a_share).",
    )
    slog.add_argument("--agent", required=True, choices=["claude", "codex"])
    slog.add_argument("--last", type=int, default=None,
                       help="Show only the last N rows.")
    slog.add_argument("--remove", action="store_true",
                       help="Remove the row whose week_end matches --week-end.")
    slog.add_argument("--week-end", type=_parse_iso_date, default=None,
                       help="Required with --remove.")
    slog.add_argument("--repo-root", type=Path, default=Path("."),
                       help="Override repo root for tests.")

    # Weekly anomaly detector — reads data/<agent>/ and prints findings.
    # Exit codes mirror validate-overlay: 0 info, 1 warn, 2 critical, so the
    # PIPELINE_FAILURES.log + Lark webhook notifier can wire to a single
    # `|| /opt/stock-analyze/app/scripts/notify-pipeline-failure.sh sanity`
    # without parsing stdout.
    sanity = sub.add_parser(
        "sanity-check",
        help="Run NAV / positions / IC anomaly checks on an agent's data dir.",
    )
    sanity.add_argument("--agent", required=True, choices=["claude", "codex"])
    sanity.add_argument("--repo-root", type=Path, default=None,
                          help="Override repo root (defaults to SA_REPO_ROOT or __file__ anchor).")

    # Daily summary push to the operator's Lark DM. Triggered nightly via
    # systemd ExecStartPost on stock-analyze-aggregate-dashboard.service.
    # Reads SA_LARK_APP_ID / SA_LARK_APP_SECRET / SA_LARK_USER_OPEN_ID;
    # falls back to stdout preview if any is missing.
    notify = sub.add_parser(
        "notify-daily-summary",
        help="Build daily ECS summary + send DM to operator via Lark Open API.",
    )
    notify.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override repo root (default: cwd; ECS will use /opt/stock-analyze/app).",
    )

    return parser


def _parse_iso_date(s: str) -> date:
    """Parse a YYYY-MM-DD string into a datetime.date."""
    return date.fromisoformat(s)


def _resolve_offline_as_of(cache_dir: Path) -> str | None:
    """Find the latest ``spot_<YYYYMMDD>.csv`` in ``cache_dir`` and return its date.

    Returns YYYY-MM-DD or None if no cache yet. Mirrors
    ``DataProvider._resolve_default_date`` but produces an ISO date the
    rest of the simulator wants (NAV ``date`` column, ``next_trading_day``).
    """

    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return None
    today = date.today().strftime("%Y%m%d")
    latest: str | None = None
    for path in cache_path.glob("spot_*.csv"):
        stem = path.stem  # spot_20260529
        parts = stem.split("_")
        if len(parts) != 2 or not parts[1].isdigit() or len(parts[1]) != 8:
            continue
        if parts[1] <= today and (latest is None or parts[1] > latest):
            latest = parts[1]
    if not latest:
        return None
    return f"{latest[:4]}-{latest[4:6]}-{latest[6:]}"


def _resolve_runtime(args: argparse.Namespace) -> tuple[dict | None, str, str, Path, str]:
    """Return (config, data_dir, reports_dir, cache_dir, market).

    Market is taken from ``--market`` (default ``a_share``). For the default
    a_share market this is byte-identical to the historical single-market
    behaviour (competition.load + resolve_agent_paths + data/shared/cache).
    For the cross-border ETF account, config, data/reports dirs, and a
    per-market shared cache are resolved via ``resolve_market_paths``.

    For competition agent mode, config is loaded via competition.load. For
    legacy single-agent mode, config falls back to configs/strategy_v1.yaml.

    Returns ``config=None`` when the command does not need a strategy config
    (handled by the early-return commands in ``main``).
    """

    market = getattr(args, "market", None) or "a_share"
    explicit_config = args.config is not None
    if args.agent:
        if explicit_config:
            raise CompetitionBaselineLocked(
                field="agent_config_override",
                baseline_value=f"configs/agents/{args.agent}_{market}.yaml",
                overlay_value=args.config,
            )
        cfg = competition.load(args.agent, market=market)
        if market == "a_share":
            # Unchanged a_share layout: data/a_share/<agent>, shared prefetch cache.
            paths = competition.resolve_agent_paths(args.agent)
            data_dir = args.data_dir or str(paths.data_dir)
            reports_dir = args.reports_dir or str(paths.reports_dir)
            cache_dir = paths.shared_cache_dir
        else:
            mp = competition.resolve_market_paths(market, args.agent)
            data_dir = args.data_dir or str(mp.data_dir)
            reports_dir = args.reports_dir or str(mp.reports_dir)
            # HK/US fetch yfinance online (no shared prefetch service); give the
            # provider a per-market shared cache to memoise within a run.
            cache_dir = mp.repo_root / "data" / market / "shared" / "cache"
    else:
        cfg_path = args.config or "configs/strategy_v1.yaml"
        cfg = load_config(cfg_path)
        data_dir = args.data_dir or "data"
        reports_dir = args.reports_dir or "reports"
        cache_dir = Path(data_dir) / "cache"
    return cfg, data_dir, reports_dir, cache_dir, market


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
        return _command_competition_dashboard(args)
    if args.command == "agent-prepare-weekly":
        ensure_dirs(args.logs_dir)
        return _command_agent_prepare_weekly(args)
    if args.command == "agent-prepare-monthly":
        ensure_dirs(args.logs_dir)
        return _command_agent_prepare_monthly(args)
    if args.command == "validate-overlay":
        ensure_dirs(args.logs_dir)
        return _command_validate_overlay(args)
    if args.command == "agent-rollback":
        ensure_dirs(args.logs_dir)
        return _command_agent_rollback(args)
    if args.command == "prepare-market-data":
        ensure_dirs(args.logs_dir)
        return _command_prepare_market_data(args)
    if args.command == "prepare-backtest-data":
        ensure_dirs(args.logs_dir)
        return _command_prepare_backtest_data(args)
    if args.command == "backtest":
        ensure_dirs(args.logs_dir)
        return _command_backtest(args)
    if args.command == "record-sentiment":
        ensure_dirs(args.logs_dir)
        return _command_record_sentiment(args)
    if args.command == "record-sector-sentiment":
        ensure_dirs(args.logs_dir)
        return _command_record_sector_sentiment(args)
    if args.command == "sentiment-log":
        ensure_dirs(args.logs_dir)
        return _command_sentiment_log(args)
    if args.command == "sanity-check":
        ensure_dirs(args.logs_dir)
        return _command_sanity_check(args)
    if args.command == "notify-daily-summary":
        ensure_dirs(args.logs_dir)
        return _command_notify_daily_summary(args)

    try:
        config, data_dir, reports_dir, cache_dir, market = _resolve_runtime(args)
    except CompetitionBaselineLocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    ensure_dirs(data_dir, reports_dir, args.logs_dir)

    # Dispatch the run primitives (provider + simulator) to the resolved
    # market. For a_share this is the same module the CLI used to import
    # directly, so behaviour is unchanged.
    market_module = competition.get_market_module(market)
    store = PortfolioStore(data_dir)
    offline = bool(getattr(args, "offline", False))
    # When offline and no explicit --as-of, resolve to the latest cache date
    # so Saturday weekly runs (no daily that day) naturally pick Friday's snapshot.
    if offline and not args.as_of:
        args.as_of = _resolve_offline_as_of(cache_dir)
    provider = market_module.make_provider(cache_dir=cache_dir, offline=offline, as_of=args.as_of)
    ledger = RunLedger(data_dir)
    migration_notes = (config or {}).get("_migration_notes") or []
    if migration_notes:
        print(f"config migration applied: {', '.join(migration_notes)}")

    try:
        with ledger.run(args.command, args.as_of, config) as context:
            run_id = context["run_id"]
            if args.command == "init":
                market_module.initialize(config, store)
                print(f"Initialized {data_dir}")
            elif args.command == "rebalance":
                batches = market_module.generate_rebalance_orders(config, store, provider, as_of=args.as_of, run_id=run_id)
                print(f"Generated {sum(len(batch.get('orders', [])) for batch in batches)} pending orders")
            elif args.command == "execute":
                trades = market_module.execute_due_orders(config, store, provider, as_of=args.as_of)
                print(f"Executed {len(trades)} trades")
            elif args.command == "update-nav":
                rows = market_module.update_nav(config, store, provider, as_of=args.as_of)
                print(f"Updated NAV for {len(rows)} accounts")
            elif args.command == "report":
                path = generate_weekly_report(config, store, reports_dir, run_id=run_id)
                print(f"Report written to {path}")
            elif args.command == "dashboard":
                page_path = generate_dashboard(config, store, reports_dir)
                fragment_path = generate_dashboard(config, store, reports_dir, mode="fragment")
                print(f"Dashboard written to {page_path}; fragment {fragment_path}")
            elif args.command == "run-daily":
                trades = market_module.execute_due_orders(config, store, provider, as_of=args.as_of)
                rows = market_module.update_nav(config, store, provider, as_of=args.as_of, notes=f"daily; trades={len(trades)}")
                # Forward-IC diagnostic is A-share-only (uses Tushare-specific
                # provider methods); skip for hk/us.
                if market == "a_share":
                    compute_pending_forward_ic(config, store, provider, as_of=args.as_of)
                provider.persist_health()
                page_path = generate_dashboard(config, store, reports_dir)
                generate_dashboard(config, store, reports_dir, mode="fragment")
                print(f"Daily run complete: trades={len(trades)}, nav_rows={len(rows)}, dashboard={page_path}")
            elif args.command == "run-weekly":
                batches = market_module.generate_rebalance_orders(config, store, provider, as_of=args.as_of, run_id=run_id)
                rows = market_module.update_nav(config, store, provider, as_of=args.as_of, notes="weekly signal")
                if market == "a_share":
                    compute_pending_forward_ic(config, store, provider, as_of=args.as_of)
                provider.persist_health()
                report = generate_weekly_report(config, store, reports_dir, run_id=run_id)
                dashboard = generate_dashboard(config, store, reports_dir)
                generate_dashboard(config, store, reports_dir, mode="fragment")
                # The weekly briefing is part of the A-share review workflow.
                briefing = _auto_write_weekly_briefing(args.agent, args.as_of) if market == "a_share" else None
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


def _command_competition_dashboard(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    scope = getattr(args, "dashboard_market", "all") or "all"
    markets = list(competition.MARKETS) if scope == "all" else [scope]
    agents: list[str] = []
    for market in markets:
        for agent in competition.list_agents_for_market(market, repo_root):
            if agent not in agents:
                agents.append(agent)
    if not agents:
        print(
            f"error: no agent overlays found for market={scope} under configs/agents/",
            file=sys.stderr,
        )
        return 2
    out_path = generate_competition_dashboard(agents=agents, repo_root=repo_root, markets=markets)
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


def _command_validate_overlay(args: argparse.Namespace) -> int:
    """Run overlay_guard on the on-disk overlay; exit code reflects outcome.

    - 0 = overlay passes all guard checks.
    - 1 = schema / factor / weight error (or unknown agent).
    - 2 = baseline-lock violation (cannot live with current competition_a_share.yaml).
    """

    import json

    repo_root = Path.cwd()
    agent_id = args.agent
    market = getattr(args, "market", None) or "a_share"
    try:
        if market == "a_share":
            paths = competition.resolve_agent_paths(agent_id, repo_root=repo_root)
        else:
            paths = competition.resolve_market_paths(market, agent_id, repo_root=repo_root)
    except competition.UnknownAgent as exc:
        print(f"错误：未知 agent: {exc}", file=sys.stderr)
        return 1
    if not paths.config_path.exists():
        print(
            f"错误：overlay 文件不存在 — {paths.config_path}",
            file=sys.stderr,
        )
        return 1
    try:
        overlay = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"错误：overlay JSON 解析失败 — {paths.config_path}: {exc.msg}",
            file=sys.stderr,
        )
        return 1
    try:
        validate_overlay_guard(agent_id, overlay, repo_root=repo_root, market=market)
    except OverlayBaselineLocked as exc:
        print(
            f"错误：overlay 改动了基线锁字段 `{exc.field}`（baseline={exc.baseline_value!r}, "
            f"overlay={exc.overlay_value!r}）。请回退该字段。",
            file=sys.stderr,
        )
        return 2
    except OverlayGuardError as exc:
        print(f"错误：overlay 守卫检查失败 — {exc}", file=sys.stderr)
        return 1
    print(
        f"OK: market={market} agent={agent_id} overlay 通过守卫检查 ({paths.config_path})"
    )
    return 0


def _command_agent_rollback(args: argparse.Namespace) -> int:
    from .agent_rollback import rollback

    try:
        result = rollback(args.agent, args.to, repo_root=Path.cwd())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        "Rollback result: "
        f"agent={result['agent_id']} status={result['status']} "
        f"from={result.get('from_hash', '-')} to={result.get('to_hash', '-')}"
    )
    return 0


def _command_prepare_market_data(args: argparse.Namespace) -> int:
    from .markets.a_share.market_data import prepare_market_data_via_ledger

    try:
        snapshot = prepare_market_data_via_ledger(
            scopes=args.scopes,
            as_of=args.as_of,
            repo_root=Path.cwd(),
            force=args.force,
            max_workers=args.max_workers,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: prepare-market-data failed: {exc}", file=sys.stderr)
        return 2
    print(
        "Prepare market-data: "
        f"as_of={snapshot.get('as_of')} status={snapshot.get('status')} "
        f"candidates={snapshot.get('candidates_fetched')} "
        f"errors={len(snapshot.get('errors') or [])} "
        f"duration_ms={snapshot.get('duration_ms')}"
    )
    return 0 if snapshot.get("status") != "failed" else 2


def _command_prepare_backtest_data(args: argparse.Namespace) -> int:
    """One-time batch fetch of historical market data from Tushare into backtest_cache/."""
    from .markets.a_share.backtest import data_prep

    try:
        data_prep.prepare_backtest_data(
            start=args.start,
            end=args.end,
            cache_root=args.cache_root,
            force=args.force,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: prepare-backtest-data failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Prepare backtest-data: "
        f"start={args.start.isoformat()} end={args.end.isoformat()} "
        f"cache_root={args.cache_root} done"
    )
    return 0


def _command_backtest(args: argparse.Namespace) -> int:
    """Run a historical backtest of an overlay and write outputs to args.output."""
    from .markets.a_share.backtest import engine

    # Load overlay
    try:
        overlay = competition.load(args.agent)
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to load overlay for agent {args.agent}: {exc}",
              file=sys.stderr)
        return 2

    universe_map = {
        "hs300": ["hs300"],
        "zz500": ["zz500"],
        "both": ["hs300", "zz500"],
    }
    universe = universe_map[args.universe]

    args.output.mkdir(parents=True, exist_ok=True)

    try:
        result = engine.run_backtest(
            overlay=overlay,
            start=args.start,
            end=args.end,
            universe=universe,
            market_data_root=args.cache_root,
            out_dir=args.output,
            in_memory=args.in_memory,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: backtest failed: {exc}", file=sys.stderr)
        return 2

    # Write markdown report
    from .markets.a_share.backtest.report import (
        render_compare_panel_markdown,
        write_report,
    )
    report_path = write_report(result)

    # --compare-mvp: run the same overlay/window under both scoring models
    # (full pipeline vs MVP PE-only) and append a comparison panel so the
    # operator can see whether the overlay's factor mix beats naive low-PE.
    if getattr(args, "compare_mvp", False):
        import copy

        def _variant(use_full: bool) -> Any:
            ov = copy.deepcopy(overlay)
            ov.setdefault("backtest", {})["use_full_pipeline"] = use_full
            sub_out = args.output / ("_full" if use_full else "_mvp")
            sub_out.mkdir(parents=True, exist_ok=True)
            return engine.run_backtest(
                overlay=ov, start=args.start, end=args.end, universe=universe,
                market_data_root=args.cache_root, out_dir=sub_out, in_memory=True,
            )

        try:
            panel = render_compare_panel_markdown(_variant(True), _variant(False))
            with report_path.open("a", encoding="utf-8") as fh:
                fh.write("\n" + panel)
            print("  compare: full-pipeline-vs-MVP panel appended to report.md")
        except Exception as exc:  # noqa: BLE001
            print(f"  warning: --compare-mvp panel failed: {exc}", file=sys.stderr)

    m = result.metrics
    print(
        f"✓ backtest complete · {args.start.isoformat()} → {args.end.isoformat()}"
        f" · cum={m.cum_return:+.1%} sharpe={m.sharpe:.2f}"
        f" max_dd={m.max_drawdown:+.1%}"
    )
    print(f"  outputs: {args.output}")
    print(f"  report:  {report_path}")
    return 0


def _sentiment_market(args: argparse.Namespace) -> str:
    return (
        getattr(args, "sentiment_market", None)
        or getattr(args, "market", None)
        or "a_share"
    )


def _command_record_sentiment(args: argparse.Namespace) -> int:
    """Record one operator-curated sentiment row from LLM-client chat."""
    from .markets.a_share.alt_factors import sentiment as alt_sent

    market = _sentiment_market(args)
    drivers = [d.strip() for d in args.drivers.split(",") if d.strip()]
    sources_raw = args.sources or ""
    sources = [s.strip() for s in sources_raw.split("|") if s.strip()] if sources_raw else []
    try:
        alt_sent.record_market_sentiment(
            agent_id=args.agent,
            week_end=args.week_end,
            score=args.score,
            confidence=args.confidence,
            drivers=drivers,
            sources=sources,
            llm_model=args.llm_model,
            prompt_version=args.prompt_version,
            repo_root=Path.cwd(),
            force=args.force,
            market=market,
        )
    except alt_sent.DuplicateSentimentEntry as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"✗ validation: {exc}", file=sys.stderr)
        return 1
    rows = alt_sent.load_sentiment_history(args.agent, Path.cwd(), market=market)
    print(
        f"✓ recorded {market}/{args.agent} {args.week_end.isoformat()} "
        f"score={args.score:+.2f} confidence={args.confidence:.2f}; "
        f"csv now has {len(rows)} weeks"
    )
    return 0


def _command_record_sector_sentiment(args: argparse.Namespace) -> int:
    """Record one week of per-industry sentiment (Phase 3 per-stock factor)."""
    import json as _json

    from .markets.a_share.alt_factors import sentiment as alt_sent

    market = _sentiment_market(args)
    if not args.sectors_json and not args.json_file:
        print("✗ provide --json or --json-file", file=sys.stderr)
        return 1
    try:
        raw = (args.json_file.read_text(encoding="utf-8")
               if args.json_file else args.sectors_json)
        payload = _json.loads(raw)
    except (OSError, ValueError) as exc:
        print(f"✗ failed to parse sector JSON: {exc}", file=sys.stderr)
        return 1

    sectors = payload.get("sectors") if isinstance(payload, dict) else payload
    if not isinstance(sectors, list):
        print("✗ JSON must contain a 'sectors' list (or be a list itself)", file=sys.stderr)
        return 1
    llm_model = args.llm_model or (payload.get("llm_model") if isinstance(payload, dict) else None) or "unknown"

    try:
        n = alt_sent.record_sector_sentiment(
            agent_id=args.agent,
            week_end=args.week_end,
            sectors=sectors,
            llm_model=llm_model,
            prompt_version=args.prompt_version,
            repo_root=Path.cwd(),
            force=args.force,
            market=market,
        )
    except alt_sent.DuplicateSentimentEntry as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError) as exc:
        print(f"✗ validation: {exc}", file=sys.stderr)
        return 1
    print(
        f"✓ recorded {market}/{args.agent} sector sentiment for {args.week_end.isoformat()}: "
        f"{n} industries"
    )
    return 0


def _command_sentiment_log(args: argparse.Namespace) -> int:
    """List or remove sentiment history rows."""
    from .markets.a_share.alt_factors import sentiment as alt_sent

    market = _sentiment_market(args)
    if args.remove:
        if args.week_end is None:
            print("✗ --remove requires --week-end", file=sys.stderr)
            return 1
        try:
            alt_sent.remove_sentiment(
                args.agent, args.week_end, args.repo_root, market=market,
            )
        except ValueError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        print(f"✓ removed {market}/{args.agent} {args.week_end.isoformat()}")
        return 0

    rows = alt_sent.load_sentiment_history(
        args.agent, args.repo_root, last_n=args.last, market=market,
    )
    if not rows:
        print(f"(no sentiment rows for {market}/{args.agent})")
        return 0
    for r in rows:
        print(
            f"{r.week_end.isoformat()}  score={r.score:+.2f}  "
            f"conf={r.confidence:.2f}  "
            f"drivers=\"{','.join(r.drivers)}\"  "
            f"({r.llm_model})"
        )
    return 0


def _command_sanity_check(args: argparse.Namespace) -> int:
    """Run sanity_check.check_agent and print findings.

    Exit code mirrors the worst severity so the same notification rule
    used for validate-overlay can fork on the result:
      - 0 = no anomalies (or only info-level cold-start notices).
      - 1 = at least one warn-level finding (probably worth a look).
      - 2 = at least one critical finding (data plumbing broken).
    """
    from .sanity_check import check_agent, format_report, max_severity

    findings = check_agent(args.agent, repo_root=args.repo_root)
    print(format_report(args.agent, findings))
    worst = max_severity(findings)
    return {"info": 0, "warn": 1, "critical": 2}[worst]


def _command_notify_daily_summary(args: argparse.Namespace) -> int:
    """Send the daily summary DM via Lark Open API.

    Delegated to ``stock_analyze.notifier.cli_send_daily_summary`` so the
    CLI layer stays thin and the heavy logic stays testable in isolation.
    """
    from .notifier import cli_send_daily_summary

    return cli_send_daily_summary(repo_root=args.repo_root)


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


# Logical-to-physical URL aliases for the beginner / pro / competition views.
# ``serve_dashboard`` interprets these when serving the ``reports/`` directory:
#
# - ``GET /``                       → reports/competition/simple.html  (beginner default)
# - ``GET /simple.html``            → reports/competition/simple.html
# - ``GET /simple/claude.html``     → reports/competition/simple/claude.html
# - ``GET /simple/codex.html``      → reports/competition/simple/codex.html
# - ``GET /pro.html``               → reports/competition/dashboard.html (alias)
# - ``GET /app.html``               → reports/app/index.html (React app)
# - ``GET /pro/<market>/<agent>.html`` → reports/<market>/<agent>/dashboard.html
# - ``GET /pro/claude.html``        → reports/a_share/claude/dashboard.html (compat alias)
# - ``GET /pro/codex.html``         → reports/a_share/codex/dashboard.html  (compat alias)
# - ``GET /competition/...``        → reports/competition/...          (unchanged)
DASHBOARD_ROUTES: dict[str, str] = {
    "/": "/competition/simple.html",
    "/index.html": "/competition/simple.html",
    "/simple.html": "/competition/simple.html",
    "/simple/claude.html": "/competition/simple/claude.html",
    "/simple/codex.html": "/competition/simple/codex.html",
    "/app.html": "/app/index.html",
    "/app/": "/app/index.html",
    "/pro.html": "/competition/dashboard.html",
    "/pro/claude.html": "/a_share/claude/dashboard.html",
    "/pro/codex.html": "/a_share/codex/dashboard.html",
    "/pro/a_share/claude.html": "/a_share/claude/dashboard.html",
    "/pro/a_share/codex.html": "/a_share/codex/dashboard.html",
    "/pro/cn_qdii_etf/claude.html": "/cn_qdii_etf/claude/dashboard.html",
    "/pro/cn_qdii_etf/codex.html": "/cn_qdii_etf/codex/dashboard.html",
}


def _resolve_dashboard_route(path: str, directory: Path) -> str | None:
    target = DASHBOARD_ROUTES.get(path)
    if target is None and path.startswith("/pro/") and path.endswith(".html"):
        parts = path.removesuffix(".html").split("/")
        if len(parts) == 4:
            _, pro, market, agent = parts
            if pro == "pro" and market in competition.MARKETS and agent:
                target = f"/{market}/{agent}/dashboard.html"
    if target is None:
        return None
    candidate = directory / target.lstrip("/")
    if candidate.exists():
        return target
    return target


def _is_dashboard_api_path(path: str) -> bool:
    return path in {
        "/api/dashboard/summary.json",
        "/api/dashboard.json",
        "/api/dashboard/detail.json",
    }


def _dashboard_api_error_response(exc: Exception) -> tuple[int, dict[str, str]]:
    if isinstance(exc, competition.UnknownMarket):
        return 400, {
            "error": "unknown_market",
            "message": f"Unknown market: {exc.market}",
        }
    if isinstance(exc, competition.UnknownAgent):
        return 404, {
            "error": "unknown_agent",
            "message": "Unknown agent for the selected market",
        }
    from .dashboard_aggregator import DashboardDataError

    if isinstance(exc, DashboardDataError):
        return 500, {
            "error": "dashboard_data_invalid",
            "message": f"Dashboard data source is unreadable: {exc.source}",
        }
    return 500, {
        "error": "dashboard_api_failed",
        "message": "Dashboard request failed",
    }


class _DashboardRequestHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler with logical-path aliases for the dashboard.

    Falls back to the parent's static-file behaviour for any path not in
    ``DASHBOARD_ROUTES``; this keeps direct links like
    ``/claude/dashboard.html`` and ``/competition/dashboard.html`` working
    unchanged.
    """

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        # Strip query / fragment for routing decisions; preserve them when
        # rewriting so deep links keep their parameters.
        path, _, suffix = self.path.partition("?")
        if _is_dashboard_api_path(path):
            self._serve_dashboard_api(path, suffix)
            return
        target = _resolve_dashboard_route(path, Path(self.directory))
        if target is not None:
            self.path = target + (("?" + suffix) if suffix else "")
        super().do_GET()

    def _serve_dashboard_api(self, path: str, query: str) -> None:
        repo_root = Path(self.directory).resolve().parent
        try:
            if path in {"/api/dashboard/summary.json", "/api/dashboard.json"}:
                from .dashboard_aggregator import build_dashboard_summary_data

                payload = build_dashboard_summary_data(repo_root=repo_root, markets=list(competition.MARKETS))
            else:
                from .dashboard_aggregator import build_dashboard_detail_data

                params = parse_qs(query, keep_blank_values=False)
                market = (params.get("market") or ["a_share"])[0]
                agent = (params.get("agent") or ["codex"])[0]
                payload = build_dashboard_detail_data(repo_root=repo_root, market=market, agent=agent)
            raw = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
            self.send_response(200)
        except Exception as exc:  # noqa: BLE001
            status, error_payload = _dashboard_api_error_response(exc)
            raw = json.dumps(
                error_payload,
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve_dashboard(reports_dir: str, host: str, port: int) -> int:
    directory = Path(reports_dir).resolve()
    handler = partial(_DashboardRequestHandler, directory=str(directory))

    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    with ReusableTCPServer((host, port), handler) as httpd:
        print(f"Serving {directory} at http://{host}:{port}")
        print("Routes: / → /competition/simple.html (beginner), /pro.html → /competition/dashboard.html")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
