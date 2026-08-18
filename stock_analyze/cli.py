from __future__ import annotations

import argparse
import http.server
import json
import re
import socketserver
import sys
import time
import uuid
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any
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
from .dashboard_aggregator import (
    PUBLIC_STRATEGY_KEYS,
    generate_competition_dashboard,
)
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
    sub.add_parser(
        "prepare-qdii-market-data",
        help="Warm daily mainland QDII fund history caches before offline research.",
    )
    daily = sub.add_parser("run-daily", help="Execute due orders, update NAV, and generate the next-session target")
    daily.add_argument(
        "--offline",
        action="store_true",
        help="Forbid the provider from reaching the network — cache miss raises CacheMiss and fails the run.",
    )
    weekly = sub.add_parser("run-weekly", help="Refresh diagnostics, weekly review, report, and dashboard without orders")
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
    sub.add_parser(
        "validate-strategy-pair",
        help="Validate that the two strategy slots remain materially different.",
    )
    release = sub.add_parser(
        "apply-strategy-release",
        help="Apply one audited multi-market strategy release manifest.",
    )
    release.add_argument("--manifest", type=Path, required=True)
    release.add_argument("--dry-run", action="store_true")
    rollback = sub.add_parser("agent-rollback", help="Rollback an agent overlay to a historical config hash")
    rollback.add_argument("--agent", required=True)
    rollback.add_argument("--to", required=True, help="Config hash saved under configs/agents/_history/")
    serve = sub.add_parser("serve-dashboard", help="Serve reports directory on localhost")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    prep_bt = sub.add_parser(
        "prepare-backtest-data",
        help="Incrementally fetch point-in-time A-share history from Tushare into backtest_cache/",
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
    prep_bt.add_argument(
        "--phases",
        help=(
            "Comma-separated phases: calendar,universe,daily,fundamentals,statements,"
            "adjustments,status,benchmark. Defaults to the legacy preparation set."
        ),
    )
    prep_bt.add_argument(
        "--code-scope",
        choices=["all", "historical-index-union"],
        default="all",
        help="Limit code-scoped endpoints to the historical HS300/ZZ500 union.",
    )
    prep_bt.add_argument(
        "--code-offset",
        type=int,
        default=0,
        help="Zero-based offset into the sorted code scope.",
    )
    prep_bt.add_argument(
        "--code-limit",
        type=int,
        help="Maximum number of sorted scope codes processed by this batch.",
    )
    prep_bt.add_argument(
        "--status-provider",
        choices=["auto", "tushare", "baostock"],
        default="auto",
        help="Historical ST provider; suspension data always comes from Tushare.",
    )

    materialize_a_share = sub.add_parser(
        "materialize-a-share-research-data",
        help="Build deterministic point-in-time research inputs from backtest_cache.",
    )
    materialize_a_share.add_argument("--start", type=_parse_iso_date, required=True)
    materialize_a_share.add_argument("--end", type=_parse_iso_date, required=True)
    materialize_a_share.add_argument("--as-of", required=True)
    materialize_a_share.add_argument(
        "--cache-root", type=Path, default=Path("data/shared/backtest_cache")
    )
    materialize_a_share.add_argument("--repo-root", type=Path, default=Path("."))

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

    qdii_capacity = sub.add_parser(
        "qdii-capacity-study",
        help="Run the offline three-year QDII top-N capacity study.",
    )
    qdii_capacity.add_argument("--start", type=_parse_iso_date, default=None)
    qdii_capacity.add_argument("--end", type=_parse_iso_date, default=None)
    qdii_capacity.add_argument(
        "--top-n",
        type=int,
        nargs="+",
        default=[4, 5, 6, 8, 10],
        help="Portfolio sizes to compare (default: 4 5 6 8 10).",
    )
    qdii_capacity.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/cn_qdii_etf/shared/cache"),
    )
    qdii_capacity.add_argument(
        "--universe",
        type=Path,
        default=Path("data/cn_qdii_etf/shared/universe_latest.json"),
    )
    qdii_capacity.add_argument("--output-root", type=Path, default=Path("."))
    qdii_capacity.add_argument("--min-signal-weeks", type=int, default=20)

    qdii_events = sub.add_parser(
        "refresh-qdii-events",
        help="Refresh source-dated fund announcements for the current QDII catalog.",
    )
    qdii_events.add_argument(
        "--universe",
        type=Path,
        default=Path("data/cn_qdii_etf/shared/universe_latest.json"),
    )
    qdii_events.add_argument(
        "--output",
        type=Path,
        default=Path("data/cn_qdii_etf/shared/fund_events.csv"),
    )

    qdii_shadow = sub.add_parser(
        "qdii-shadow-research",
        help="Build the research catalog and run global/commodity/bond shadow portfolios.",
    )
    qdii_shadow.add_argument("--start", type=_parse_iso_date, default=None)
    qdii_shadow.add_argument("--end", type=_parse_iso_date, default=None)
    qdii_shadow.add_argument(
        "--cache-dir", type=Path, default=Path("data/cn_qdii_etf/shared/cache")
    )
    qdii_shadow.add_argument(
        "--catalog", type=Path, default=Path("data/cn_qdii_etf/research/catalog_latest.json")
    )
    qdii_shadow.add_argument("--output-root", type=Path, default=Path("."))
    qdii_shadow.add_argument("--refresh-data", action="store_true")
    qdii_shadow.add_argument("--min-signal-weeks", type=int, default=12)
    qdii_shadow.add_argument(
        "--sentiment-file",
        type=Path,
        default=Path("data/cn_qdii_etf/research/theme_sentiment.csv"),
    )
    qdii_shadow.add_argument("--sentiment-agent", choices=["claude", "codex"], default="codex")

    theme_sentiment = sub.add_parser(
        "record-theme-sentiment",
        help="Record source-backed per-index sentiment for QDII shadow research.",
    )
    theme_sentiment.add_argument("--agent", required=True, choices=["claude", "codex"])
    theme_sentiment.add_argument("--week-end", required=True)
    theme_sentiment.add_argument("--index-key", required=True)
    theme_sentiment.add_argument("--score", type=float, required=True)
    theme_sentiment.add_argument("--confidence", type=float, required=True)
    theme_sentiment.add_argument("--drivers", required=True)
    theme_sentiment.add_argument("--sources", required=True)
    theme_sentiment.add_argument("--llm-model", required=True)
    theme_sentiment.add_argument("--prompt-version", default="theme_v1")
    theme_sentiment.add_argument(
        "--output", type=Path, default=Path("data/cn_qdii_etf/research/theme_sentiment.csv")
    )
    theme_sentiment.add_argument("--force", action="store_true")

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

    # Compatibility alias for the consolidated daily workflow summary.
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

    workflow_notify = sub.add_parser(
        "notify-workflow-summary",
        help="Send one idempotent daily, weekly, or monthly workflow summary.",
    )
    workflow_notify.add_argument(
        "--cadence",
        required=True,
        choices=["daily", "weekly", "monthly"],
    )
    workflow_notify.add_argument(
        "--target",
        help="YYYY-MM-DD for daily/weekly or YYYY-MM for monthly.",
    )
    workflow_notify.add_argument("--repo-root", type=Path, default=None)
    workflow_notify.add_argument(
        "--force",
        action="store_true",
        help="Send again even if this cadence/target was already delivered.",
    )
    workflow_notify.add_argument(
        "--preview",
        action="store_true",
        help="Print the summary without sending or marking it delivered.",
    )
    workflow_notify.add_argument(
        "--require-complete",
        action="store_true",
        help="Return 75 without sending until every formal task succeeded.",
    )
    workflow_notify.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        help="Maximum time to wait for required task completion.",
    )
    workflow_notify.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="Completion polling interval used with --require-complete.",
    )

    for command, help_text in (
        ("prepare-research-data", "Build immutable feature snapshots from market caches."),
        ("run-prediction-research", "Build labels, events, regimes, and event studies."),
        ("refresh-research-labels", "Refresh labels on the latest feature snapshot."),
        ("train-prediction-models", "Train and register calibrated challenger models."),
        ("predict", "Generate research or active prediction records."),
    ):
        research = sub.add_parser(command, help=help_text)
        research.add_argument("--offline", action="store_true", help="Use local market caches only.")
        research.add_argument("--repo-root", type=Path, default=Path("."))
        research.add_argument("--force", action="store_true", help="Rebuild an existing feature snapshot.")
        research.add_argument(
            "--max-full-history-instruments", type=int, default=500,
            help="A-share instruments retaining full history; all others keep the latest row.",
        )

    moneyflow_backfill = sub.add_parser(
        "backfill-a-share-moneyflow",
        help="Backfill resumable point-in-time Tushare money-flow history.",
    )
    moneyflow_backfill.add_argument("--repo-root", type=Path, default=Path("."))
    moneyflow_backfill.add_argument("--start-date", default="20180102")
    moneyflow_backfill.add_argument("--end-date", default=None)
    moneyflow_backfill.add_argument("--code", action="append", default=[])
    moneyflow_backfill.add_argument("--codes-file", type=Path, default=None)
    moneyflow_backfill.add_argument("--max-workers", type=int, default=4)
    moneyflow_backfill.add_argument("--retries", type=int, default=3)
    moneyflow_backfill.add_argument("--requests-per-minute", type=float, default=180)
    moneyflow_backfill.add_argument("--max-codes", type=int, default=None)
    moneyflow_backfill.add_argument("--force", action="store_true")

    earnings_backfill = sub.add_parser(
        "backfill-structured-earnings",
        help="Backfill resumable Tushare forecast and express partitions.",
    )
    earnings_backfill.add_argument("--repo-root", type=Path, default=Path("."))
    earnings_backfill.add_argument("--start-date", default="2018-01-01")
    earnings_backfill.add_argument("--end-date", default="2024-12-31")
    earnings_backfill.add_argument("--max-partitions", type=int, default=None)

    capital_actions_backfill = sub.add_parser(
        "backfill-structured-capital-actions",
        help="Backfill resumable Tushare repurchase and holder-trade partitions.",
    )
    capital_actions_backfill.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    capital_actions_backfill.add_argument("--start-date", default="2018-01-01")
    capital_actions_backfill.add_argument("--end-date", default="2024-12-31")
    capital_actions_backfill.add_argument(
        "--max-partitions", type=int, default=None
    )

    holder_concentration_backfill = sub.add_parser(
        "backfill-structured-holder-counts",
        help="Backfill resumable Tushare shareholder-count partitions.",
    )
    holder_concentration_backfill.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    holder_concentration_backfill.add_argument(
        "--start-date", default="2018-01-01"
    )
    holder_concentration_backfill.add_argument(
        "--end-date", default="2024-12-31"
    )
    holder_concentration_backfill.add_argument(
        "--max-partitions", type=int, default=None
    )

    share_unlock_backfill = sub.add_parser(
        "backfill-structured-share-unlocks",
        help="Backfill resumable Tushare restricted-share unlock partitions.",
    )
    share_unlock_backfill.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    share_unlock_backfill.add_argument("--start-date", default="2018-01-01")
    share_unlock_backfill.add_argument("--end-date", default="2024-12-31")
    share_unlock_backfill.add_argument("--max-partitions", type=int, default=None)

    dividend_growth_backfill = sub.add_parser(
        "backfill-structured-dividends",
        help="Backfill resumable implemented annual-dividend partitions.",
    )
    dividend_growth_backfill.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    dividend_growth_backfill.add_argument("--start-date", default="2018-01-01")
    dividend_growth_backfill.add_argument("--end-date", default="2024-12-31")
    dividend_growth_backfill.add_argument("--max-partitions", type=int, default=None)

    block_trade_backfill = sub.add_parser(
        "backfill-structured-block-trades",
        help="Backfill resumable Tushare block-trade partitions.",
    )
    block_trade_backfill.add_argument("--repo-root", type=Path, default=Path("."))
    block_trade_backfill.add_argument("--start-date", default="2018-01-01")
    block_trade_backfill.add_argument("--end-date", default="2024-12-31")
    block_trade_backfill.add_argument("--max-partitions", type=int, default=None)

    tournament = sub.add_parser(
        "run-classical-tournament",
        help="Run one sealed account-scoped classical model tournament.",
    )
    tournament.add_argument("--offline", action="store_true", help="Use local research snapshots only.")
    tournament.add_argument("--repo-root", type=Path, default=Path("."))
    tournament.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    tournament.add_argument("--max-full-history-instruments", type=int, default=500)
    tournament.add_argument("--account-scope", default=None)
    tournament.add_argument("--horizon", type=int, default=None)

    unified_arena = sub.add_parser(
        "run-unified-model-arena",
        help="Compare formal rules and candidate models on one sealed window.",
    )
    unified_arena.add_argument(
        "--offline",
        action="store_true",
        help="Use immutable local research snapshots only.",
    )
    unified_arena.add_argument("--repo-root", type=Path, default=Path("."))
    unified_arena.add_argument(
        "--force",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    unified_arena.add_argument(
        "--max-full-history-instruments",
        type=int,
        default=500,
    )
    unified_arena.add_argument("--horizon", type=int, default=None)

    cross_sectional_repair = sub.add_parser(
        "run-cross-sectional-alpha-repair",
        help="Run the frozen development-only H20 target ablation.",
    )
    cross_sectional_repair.add_argument(
        "--offline", action="store_true", help="Use local research snapshots only."
    )
    cross_sectional_repair.add_argument("--repo-root", type=Path, default=Path("."))
    cross_sectional_repair.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    cross_sectional_repair.add_argument("--max-full-history-instruments", type=int, default=500)
    cross_sectional_repair.add_argument("--account-scope", default=None)
    cross_sectional_repair.add_argument("--horizon", type=int, default=20)

    baseline_first = sub.add_parser(
        "run-baseline-first-research",
        help="Compare the transparent baseline and bounded residual on development folds.",
    )
    baseline_first.add_argument(
        "--offline", action="store_true", help="Use local research snapshots only."
    )
    baseline_first.add_argument("--repo-root", type=Path, default=Path("."))
    baseline_first.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    baseline_first.add_argument("--max-full-history-instruments", type=int, default=500)
    baseline_first.add_argument("--account-scope", default=None)
    baseline_first.add_argument("--horizon", type=int, default=None)
    baseline_first.add_argument(
        "--training-input-bundle",
        type=Path,
        default=None,
        help="Verified immutable ECS input bundle that fixes snapshot provenance.",
    )

    strategy_campaign = sub.add_parser(
        "run-strategy-campaign",
        help="Run one sealed, bounded transparent-strategy recovery campaign.",
    )
    strategy_campaign.add_argument(
        "--offline", action="store_true", help="Use immutable local bundles only."
    )
    strategy_campaign.add_argument("--repo-root", type=Path, default=Path("."))
    strategy_campaign.add_argument("--campaign", default=None)
    strategy_campaign.add_argument(
        "--input-bundle",
        type=Path,
        action="append",
        default=[],
        help="Path to a verified research-training input manifest; pass once per market.",
    )
    strategy_campaign.add_argument(
        "--stage",
        choices=["transparent", "incremental-ml"],
        default="transparent",
    )

    regime_tabular = sub.add_parser(
        "run-regime-tabular-alpha",
        help="Run the frozen ZZ500 regime-aware LightGBM development evaluation.",
    )
    regime_tabular.add_argument(
        "--offline", action="store_true", help="Use immutable local research snapshots only."
    )
    regime_tabular.add_argument("--repo-root", type=Path, default=Path("."))
    regime_tabular.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    regime_tabular.add_argument("--max-full-history-instruments", type=int, default=500)
    regime_tabular.add_argument(
        "--config",
        type=Path,
        default=Path("configs/research/classical_model.yaml"),
        help="Frozen research-only tabular model contract.",
    )

    tabular_forward_freeze = sub.add_parser(
        "freeze-regime-tabular-forward",
        help="Freeze the best ZZ500 tabular candidate for future-only observation.",
    )
    tabular_forward_freeze.add_argument("--offline", action="store_true")
    tabular_forward_freeze.add_argument("--repo-root", type=Path, default=Path("."))
    tabular_forward_freeze.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    tabular_forward_freeze.add_argument("--max-full-history-instruments", type=int, default=500)
    tabular_forward_freeze.add_argument(
        "--config",
        type=Path,
        default=Path("configs/research/classical_model.yaml"),
    )
    tabular_forward_freeze.add_argument(
        "--source-report",
        type=Path,
        required=True,
        help="Immutable development report that selected this candidate.",
    )
    tabular_forward_freeze.add_argument(
        "--observation-start",
        required=True,
        help="First future-only signal date in YYYYMMDD form.",
    )

    tabular_forward_run = sub.add_parser(
        "run-regime-tabular-forward",
        help="Score one new market day with the frozen research observer.",
    )
    tabular_forward_run.add_argument("--offline", action="store_true")
    tabular_forward_run.add_argument("--repo-root", type=Path, default=Path("."))
    tabular_forward_run.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    tabular_forward_run.add_argument("--max-full-history-instruments", type=int, default=500)
    tabular_forward_run.add_argument(
        "--config",
        type=Path,
        default=Path("configs/research/classical_model.yaml"),
    )

    rule_core = sub.add_parser(
        "run-rule-core-diagnostic",
        help="Falsify the two predeclared rule cores on the oldest 60%% of research data.",
    )
    rule_core.add_argument("--offline", action="store_true", help="Use immutable local snapshots only.")
    rule_core.add_argument(
        "--as-of",
        default=argparse.SUPPRESS,
        help="Snapshot cutoff in YYYYMMDD or YYYY-MM-DD form.",
    )
    rule_core.add_argument("--repo-root", type=Path, default=Path("."))
    rule_core.add_argument("--output-root", type=Path, default=None)

    earnings_drift = sub.add_parser(
        "run-earnings-drift-study",
        help="Run the preregistered model-free A-share earnings drift study.",
    )
    earnings_drift.add_argument("--repo-root", type=Path, default=Path("."))
    earnings_drift.add_argument("--snapshot-date", required=True)
    earnings_drift.add_argument(
        "--contract",
        type=Path,
        default=Path("configs/research/earnings_drift_study.yaml"),
    )
    earnings_drift.add_argument(
        "--output-root", type=Path, default=Path("reports/research")
    )

    capital_actions = sub.add_parser(
        "run-capital-actions-study",
        help="Run the preregistered model-free A-share capital-actions study.",
    )
    capital_actions.add_argument("--repo-root", type=Path, default=Path("."))
    capital_actions.add_argument("--snapshot-date", required=True)
    capital_actions.add_argument(
        "--contract",
        type=Path,
        default=Path("configs/research/capital_actions_study.yaml"),
    )
    capital_actions.add_argument(
        "--output-root", type=Path, default=Path("reports/research")
    )

    holder_concentration = sub.add_parser(
        "run-holder-concentration-study",
        help="Run the preregistered model-free shareholder concentration study.",
    )
    holder_concentration.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    holder_concentration.add_argument("--snapshot-date", required=True)
    holder_concentration.add_argument(
        "--contract",
        type=Path,
        default=Path("configs/research/holder_concentration_study.yaml"),
    )
    holder_concentration.add_argument(
        "--output-root", type=Path, default=Path("reports/research")
    )

    share_unlock = sub.add_parser(
        "run-share-unlock-avoidance-study",
        help="Run the preregistered restricted-share unlock avoidance study.",
    )
    share_unlock.add_argument("--repo-root", type=Path, default=Path("."))
    share_unlock.add_argument("--snapshot-date", required=True)
    share_unlock.add_argument(
        "--contract", type=Path,
        default=Path("configs/research/share_unlock_avoidance_study.yaml"),
    )
    share_unlock.add_argument(
        "--output-root", type=Path, default=Path("reports/research")
    )

    dividend_growth = sub.add_parser(
        "run-dividend-growth-study",
        help="Run the preregistered annual cash-dividend growth study.",
    )
    dividend_growth.add_argument("--repo-root", type=Path, default=Path("."))
    dividend_growth.add_argument("--snapshot-date", required=True)
    dividend_growth.add_argument(
        "--contract", type=Path,
        default=Path("configs/research/dividend_growth_study.yaml"),
    )
    dividend_growth.add_argument(
        "--output-root", type=Path, default=Path("reports/research")
    )

    block_trade = sub.add_parser(
        "run-block-trade-premium-study",
        help="Run the preregistered block-trade premium study.",
    )
    block_trade.add_argument("--repo-root", type=Path, default=Path("."))
    block_trade.add_argument("--snapshot-date", required=True)
    block_trade.add_argument(
        "--contract", type=Path,
        default=Path("configs/research/block_trade_premium_study.yaml"),
    )
    block_trade.add_argument("--output-root", type=Path, default=Path("reports/research"))

    training_bundle_export = sub.add_parser(
        "research-training-bundle-export",
        help="Export checksummed research snapshots for local CPU training.",
    )
    training_bundle_export.add_argument("--repo-root", type=Path, default=Path("."))
    training_bundle_export.add_argument("--output", type=Path, required=True)
    training_bundle_import = sub.add_parser(
        "research-training-bundle-import",
        help="Verify and install research snapshots on the local trainer.",
    )
    training_bundle_import.add_argument("--repo-root", type=Path, default=Path("."))
    training_bundle_import.add_argument("--bundle", type=Path, required=True)
    model_bundle_export = sub.add_parser(
        "research-model-bundle-export",
        help="Export a checksummed Shadow or Rejected tournament result.",
    )
    model_bundle_export.add_argument("--repo-root", type=Path, default=Path("."))
    model_bundle_export.add_argument("--report", type=Path, required=True)
    model_bundle_export.add_argument("--output", type=Path, required=True)
    model_bundle_import = sub.add_parser(
        "research-model-bundle-import",
        help="Merge a local research model result without changing champions.",
    )
    model_bundle_import.add_argument("--repo-root", type=Path, default=Path("."))
    model_bundle_import.add_argument("--bundle", type=Path, required=True)
    model_bundle_import.add_argument(
        "--training-input-bundle",
        type=Path,
        required=True,
    )
    result_bundle_export = sub.add_parser(
        "research-result-bundle-export",
        help="Export bounded baseline reports and frozen-window evidence.",
    )
    result_bundle_export.add_argument("--repo-root", type=Path, default=Path("."))
    result_bundle_export.add_argument("--result", type=Path, required=True)
    result_bundle_export.add_argument(
        "--training-input-bundle", type=Path, required=True
    )
    result_bundle_export.add_argument("--output", type=Path, required=True)
    result_bundle_import = sub.add_parser(
        "research-result-bundle-import",
        help="Install bounded baseline reports without changing model state.",
    )
    result_bundle_import.add_argument("--repo-root", type=Path, default=Path("."))
    result_bundle_import.add_argument("--bundle", type=Path, required=True)
    result_bundle_import.add_argument(
        "--training-input-bundle", type=Path, required=True
    )

    for command, help_text in (
        (
            "run-model-iteration",
            "Run the pinned Challenger model paper portfolio for one market.",
        ),
        (
            "run-model-shadow",
            "Compatibility alias for run-model-iteration.",
        ),
    ):
        model_iteration = sub.add_parser(command, help=help_text)
        model_iteration.add_argument(
            "--offline",
            action="store_true",
            help="Use only point-in-time market caches.",
        )
        model_iteration.add_argument("--repo-root", type=Path, default=Path("."))

    shadow_admission = sub.add_parser(
        "admit-personal-quant-shadow",
        help="Freeze and register one transparent-rule Shadow candidate per market.",
    )
    shadow_admission.add_argument("--repo-root", type=Path, default=Path("."))
    shadow_admission.add_argument(
        "--campaign-report",
        type=Path,
        required=True,
        help="Sealed transparent campaign report used as admission evidence.",
    )
    shadow_quality_audit = sub.add_parser(
        "audit-model-shadow-quality",
        help="Report or reject Shadow candidates without strict historical evidence.",
    )
    shadow_quality_audit.add_argument("--repo-root", type=Path, default=Path("."))
    shadow_quality_audit.add_argument(
        "--apply",
        action="store_true",
        help="Reject flagged legacy Shadow candidates after the read-only preview.",
    )
    full_history_audit = sub.add_parser(
        "audit-full-history-rebuild-data",
        help="Audit one full-history feature snapshot before model fitting.",
    )
    full_history_audit.add_argument("--repo-root", type=Path, default=Path("."))
    full_history_audit.add_argument("--market", choices=["a_share", "cn_qdii_etf"], required=True)
    full_history_audit.add_argument("--snapshot", type=Path, required=True)
    full_history_audit.add_argument("--required-start", required=True)
    full_history_audit.add_argument("--required-end", required=True)
    full_history_retire = sub.add_parser(
        "retire-full-history-legacy-shadows",
        help="Preview or retire transparent Shadow candidates superseded by the rebuild.",
    )
    full_history_retire.add_argument("--repo-root", type=Path, default=Path("."))
    full_history_retire.add_argument("--apply", action="store_true")
    full_history_run = sub.add_parser(
        "run-full-history-model-rebuild",
        help="Run the frozen full-history model rebuild campaign.",
    )
    full_history_run.add_argument("--repo-root", type=Path, default=Path("."))
    full_history_run.add_argument("--snapshot-date", required=True)
    full_history_run.add_argument(
        "--scopes",
        nargs="+",
        choices=["hs300", "zz500", "hk_exposure", "us_exposure"],
        default=None,
    )

    intelligence_ingest = sub.add_parser(
        "intelligence-ingest",
        help="Incrementally collect official announcements, policies, and licensed news.",
    )
    intelligence_ingest.add_argument("--repo-root", type=Path, default=Path("."))
    intelligence_ingest.add_argument("--config-path", type=Path, default=None)
    intelligence_ingest.add_argument("--since", default=None, help="Optional ISO cursor override for backfill.")
    intelligence_ingest.add_argument("--until", default=None, help="ISO end timestamp (default: now).")
    intelligence_ingest.add_argument("--sources", nargs="*", default=None)

    intelligence_backfill = sub.add_parser(
        "intelligence-backfill",
        help="Run isolated resumable full-history source backfill.",
    )
    intelligence_backfill.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    intelligence_backfill.add_argument("--config-path", type=Path, default=None)
    intelligence_backfill.add_argument(
        "--source",
        choices=["tushare_announcement"],
        required=True,
    )
    intelligence_backfill.add_argument("--start-date", required=True)
    intelligence_backfill.add_argument("--end-date", required=True)
    intelligence_backfill.add_argument(
        "--max-partitions",
        type=int,
        required=True,
    )
    intelligence_backfill.add_argument("--resume", action="store_true")

    intelligence_extract = sub.add_parser(
        "intelligence-extract",
        help="Extract auditable market events from newly collected documents.",
    )
    intelligence_extract.add_argument("--repo-root", type=Path, default=Path("."))
    intelligence_extract.add_argument("--config-path", type=Path, default=None)
    intelligence_extract.add_argument("--limit", type=int, default=500)

    intelligence_status = sub.add_parser(
        "intelligence-status",
        help="Write and print intelligence coverage and source-health diagnostics.",
    )
    intelligence_status.add_argument("--repo-root", type=Path, default=Path("."))

    intelligence_evaluate = sub.add_parser(
        "intelligence-evaluate",
        help="Evaluate event-factor coverage and forward rank IC on the latest research snapshot.",
    )
    intelligence_evaluate.add_argument("--repo-root", type=Path, default=Path("."))
    intelligence_evaluate.add_argument(
        "--market", choices=["a_share", "cn_qdii_etf"], required=True,
    )
    intelligence_evaluate.add_argument("--as-of", default=None)

    intelligence_model_effect = sub.add_parser(
        "intelligence-model-effect",
        help="Compare Base and Base+Event models without registry side effects.",
    )
    intelligence_model_effect.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    intelligence_model_effect.add_argument(
        "--market",
        choices=["a_share", "cn_qdii_etf"],
        required=True,
    )
    intelligence_model_effect.add_argument("--as-of", default=None)

    semantic_prepare = sub.add_parser(
        "intelligence-semantic-prepare",
        help="Prepare a bounded provider-neutral filesystem extraction job.",
    )
    semantic_prepare.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_prepare.add_argument(
        "--profile", default="a-share-announcement-mentions-v1"
    )
    semantic_prepare.add_argument("--limit", type=int, default=50)
    semantic_prepare.add_argument(
        "--max-input-characters", type=int, default=40_000
    )
    semantic_prepare.add_argument(
        "--executor-mode", choices=["api", "coding_plan"], default=None
    )
    semantic_prepare.add_argument("--provider", default=None)
    semantic_prepare.add_argument("--model", default=None)
    semantic_prepare.add_argument("--client-version", default=None)

    semantic_route_finalize = sub.add_parser(
        "intelligence-semantic-route-finalize",
        help="Finalize bounded deterministic routes without provider calls.",
    )
    semantic_route_finalize.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_route_finalize.add_argument(
        "--profile", default="a-share-announcement-mentions-v1"
    )
    semantic_route_finalize.add_argument("--limit", type=int, default=5_000)

    semantic_repair_prepare = sub.add_parser(
        "intelligence-semantic-repair-prepare",
        help="Prepare an explicit versioned semantic remediation job.",
    )
    semantic_repair_prepare.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_repair_prepare.add_argument(
        "--profile", default="a-share-announcement-remediation-v1"
    )
    semantic_repair_prepare.add_argument(
        "--document-id", type=int, action="append", required=True
    )
    semantic_repair_prepare.add_argument("--reason", required=True)
    semantic_repair_prepare.add_argument(
        "--max-input-characters", type=int, default=40_000
    )

    semantic_repair_rollback = sub.add_parser(
        "intelligence-semantic-repair-rollback",
        help="Roll back one semantic repair without deleting lineage.",
    )
    semantic_repair_rollback.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_repair_rollback.add_argument("--repair-id", required=True)

    semantic_run = sub.add_parser(
        "intelligence-semantic-run",
        help="Run missing job rows through one pluggable executor adapter.",
    )
    semantic_run.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_run.add_argument("--job", required=True)
    semantic_run.add_argument("--executor-config", required=True)

    semantic_coding_plan_collect = sub.add_parser(
        "intelligence-semantic-coding-plan-collect",
        help="Validate Coding Plan output without importing production data.",
    )
    semantic_coding_plan_collect.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_coding_plan_collect.add_argument("--job", required=True)

    semantic_import = sub.add_parser(
        "intelligence-semantic-import",
        help="Validate and persist one provider-neutral extraction job.",
    )
    semantic_import.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_import.add_argument("--job", required=True)

    semantic_job_status = sub.add_parser(
        "intelligence-semantic-job-status",
        help="Inspect one provider-neutral extraction job.",
    )
    semantic_job_status.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_job_status.add_argument("--job", required=True)

    semantic_daily = sub.add_parser(
        "intelligence-semantic-daily",
        help="Import returned artifacts, prepare a batch, and optionally run it.",
    )
    semantic_daily.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_daily.add_argument(
        "--profile", default="a-share-announcement-mentions-v1"
    )
    semantic_daily.add_argument("--limit", type=int, default=50)
    semantic_daily.add_argument(
        "--max-input-characters", type=int, default=40_000
    )
    semantic_daily.add_argument("--executor-config", default=None)

    semantic_quality = sub.add_parser(
        "intelligence-semantic-quality-evaluate",
        help="Evaluate frozen semantic predictions without production side effects.",
    )
    semantic_quality.add_argument("--reference", type=Path, required=True)
    semantic_quality.add_argument("--predictions", type=Path, required=True)
    semantic_quality.add_argument("--output", type=Path, required=True)

    semantic_frozen = sub.add_parser(
        "intelligence-semantic-frozen-run",
        help="Run a frozen semantic workbench without production imports.",
    )
    semantic_frozen.add_argument("--repo-root", type=Path, default=Path("."))
    semantic_frozen.add_argument("--workbench", type=Path, required=True)
    semantic_frozen.add_argument(
        "--profile", default="a-share-announcement-mentions-v24"
    )
    semantic_frozen.add_argument("--predictions", type=Path, required=True)
    semantic_frozen.add_argument("--report", type=Path, required=True)
    semantic_frozen.add_argument("--executor-config", type=Path, required=True)
    semantic_frozen.add_argument("--limit", type=int, default=None)
    semantic_frozen.add_argument("--document-id", type=int, action="append")

    semantic_frozen_prepare = sub.add_parser(
        "intelligence-semantic-frozen-prepare",
        help="Export a blind frozen semantic job for a Coding Plan.",
    )
    semantic_frozen_prepare.add_argument("--repo-root", type=Path, default=Path("."))
    semantic_frozen_prepare.add_argument("--workbench", type=Path, required=True)
    semantic_frozen_prepare.add_argument(
        "--profile", default="a-share-announcement-mentions-v27"
    )
    semantic_frozen_prepare.add_argument("--job", type=Path, required=True)
    semantic_frozen_prepare.add_argument("--provider", required=True)
    semantic_frozen_prepare.add_argument("--model", required=True)
    semantic_frozen_prepare.add_argument(
        "--client-version", default="coding-plan-v1"
    )
    semantic_frozen_prepare.add_argument("--limit", type=int, default=None)
    semantic_frozen_prepare.add_argument("--document-id", type=int, action="append")

    semantic_frozen_collect = sub.add_parser(
        "intelligence-semantic-frozen-collect",
        help="Validate and compile Coding Plan frozen output without imports.",
    )
    semantic_frozen_collect.add_argument("--repo-root", type=Path, default=Path("."))
    semantic_frozen_collect.add_argument("--workbench", type=Path, required=True)
    semantic_frozen_collect.add_argument("--job", type=Path, required=True)
    semantic_frozen_collect.add_argument("--predictions", type=Path, required=True)
    semantic_frozen_collect.add_argument("--report", type=Path, required=True)

    semantic_frozen_repair_prepare = sub.add_parser(
        "intelligence-semantic-frozen-repair-prepare",
        help="Export the single bounded repair round for a frozen Coding Plan job.",
    )
    semantic_frozen_repair_prepare.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_frozen_repair_prepare.add_argument(
        "--workbench", type=Path, required=True
    )
    semantic_frozen_repair_prepare.add_argument(
        "--source-job", type=Path, required=True
    )
    semantic_frozen_repair_prepare.add_argument(
        "--source-predictions", type=Path, required=True
    )
    semantic_frozen_repair_prepare.add_argument(
        "--repair-job", type=Path, required=True
    )
    semantic_frozen_repair_prepare.add_argument("--provider", required=True)
    semantic_frozen_repair_prepare.add_argument("--model", required=True)
    semantic_frozen_repair_prepare.add_argument("--client-version", required=True)

    semantic_frozen_repair_collect = sub.add_parser(
        "intelligence-semantic-frozen-repair-collect",
        help="Merge and validate the single frozen Coding Plan repair round.",
    )
    semantic_frozen_repair_collect.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    semantic_frozen_repair_collect.add_argument(
        "--workbench", type=Path, required=True
    )
    semantic_frozen_repair_collect.add_argument(
        "--source-job", type=Path, required=True
    )
    semantic_frozen_repair_collect.add_argument(
        "--source-predictions", type=Path, required=True
    )
    semantic_frozen_repair_collect.add_argument(
        "--repair-job", type=Path, required=True
    )
    semantic_frozen_repair_collect.add_argument(
        "--predictions", type=Path, required=True
    )
    semantic_frozen_repair_collect.add_argument("--report", type=Path, required=True)

    artifact_job_export = sub.add_parser(
        "intelligence-artifact-job-export",
        help="Lease and export a bounded historical artifact worker job.",
    )
    artifact_job_export.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    artifact_job_export.add_argument(
        "--stage",
        choices=["download", "parse"],
        default="parse",
    )
    artifact_job_export.add_argument("--limit", type=int, default=25)
    artifact_job_export.add_argument("--worker-id", required=True)
    artifact_job_export.add_argument(
        "--lease-seconds", type=int, default=14_400
    )

    artifact_job_run = sub.add_parser(
        "intelligence-artifact-job-run",
        help="Run one portable artifact job without production credentials.",
    )
    artifact_job_run.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    artifact_job_run.add_argument(
        "--job-dir", type=Path, required=True
    )
    artifact_job_run.add_argument("--workers", type=int, default=4)

    artifact_job_import = sub.add_parser(
        "intelligence-artifact-job-import",
        help="Verify and import a returned artifact worker job.",
    )
    artifact_job_import.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    artifact_job_import.add_argument(
        "--job-dir", type=Path, required=True
    )

    artifact_job_status = sub.add_parser(
        "intelligence-artifact-job-status",
        help="Inspect artifact worker leases and completed batches.",
    )
    artifact_job_status.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )

    intelligence_enrich = sub.add_parser(
        "intelligence-enrich",
        help="Run bounded announcement artifact download and parse stages.",
    )
    intelligence_enrich.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    intelligence_enrich.add_argument("--limit", type=int, default=500)
    intelligence_enrich.add_argument(
        "--stages",
        nargs="+",
        choices=[
            "enqueue",
            "download",
            "parse",
        ],
        default=["enqueue", "download", "parse"],
    )

    intelligence_reconcile = sub.add_parser(
        "intelligence-reconcile",
        help="Reconcile metadata and run the bounded enrichment pipeline.",
    )
    intelligence_reconcile.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    intelligence_reconcile.add_argument(
        "--lookback-days", type=int, default=2
    )
    intelligence_reconcile.add_argument("--limit", type=int, default=500)
    intelligence_reconcile.add_argument(
        "--stages",
        nargs="+",
        choices=[
            "metadata",
            "enqueue",
            "download",
            "parse",
        ],
        default=[
            "metadata",
            "enqueue",
            "download",
            "parse",
        ],
    )

    intelligence_semantic_status = sub.add_parser(
        "intelligence-semantic-status",
        help="Write and print the semantic pipeline operational snapshot.",
    )
    intelligence_semantic_status.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )

    intelligence_prune_raw = sub.add_parser(
        "intelligence-prune-raw",
        help="Delete raw source files that are no longer referenced.",
    )
    intelligence_prune_raw.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    intelligence_prune_raw.add_argument(
        "--source",
        choices=["tushare_announcement"],
        required=True,
    )

    source_audit = sub.add_parser(
        "intelligence-source-audit",
        help=(
            "Cross-check Tushare materializations with iFinD and "
            "optionally supplement primary-source gaps."
        ),
    )
    source_audit.add_argument(
        "--repo-root", type=Path, default=Path(".")
    )
    source_audit.add_argument(
        "--as-of",
        dest="audit_as_of",
        default=None,
        help="Audit date YYYY-MM-DD; defaults to latest fresh snapshot.",
    )
    source_audit.add_argument(
        "--datasets",
        nargs="+",
        choices=["market", "announcement"],
        default=["market", "announcement"],
    )
    source_audit.add_argument(
        "--announcement-scope",
        choices=["operational", "full-market"],
        default="operational",
    )
    source_audit.add_argument(
        "--codes",
        nargs="*",
        default=None,
        help="Explicit announcement codes for operational scope.",
    )
    source_audit.add_argument(
        "--supplement",
        action="store_true",
        help="Persist iFinD-only announcements and missing market rows.",
    )

    return parser


def _parse_iso_date(s: str) -> date:
    """Parse a YYYY-MM-DD string into a datetime.date."""
    return date.fromisoformat(s)


def _resolve_offline_as_of(cache_dir: Path) -> str | None:
    """Resolve the latest dated market snapshot in ``cache_dir``.

    Returns YYYY-MM-DD or None if no cache yet. Mirrors
    ``DataProvider._resolve_default_date`` but produces an ISO date the
    rest of the simulator wants (NAV ``date`` column, ``next_trading_day``).
    """

    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return None
    today = date.today().strftime("%Y%m%d")

    snapshot_pattern = re.compile(
        r"market_snapshot_(\d{4})-(\d{2})-(\d{2})\.json"
    )
    latest_snapshot: str | None = None
    for path in cache_path.parent.glob("market_snapshot_*.json"):
        match = snapshot_pattern.fullmatch(path.name)
        if not match:
            continue
        snapshot_date = "".join(match.groups())
        if snapshot_date <= today and (
            latest_snapshot is None or snapshot_date > latest_snapshot
        ):
            latest_snapshot = snapshot_date
    if latest_snapshot:
        return (
            f"{latest_snapshot[:4]}-{latest_snapshot[4:6]}-"
            f"{latest_snapshot[6:]}"
        )

    latest: str | None = None
    dated_patterns = (
        ("spot_*.csv", re.compile(r"spot_(\d{8})\.csv")),
        (
            "fund_daily_*.csv",
            re.compile(r"fund_daily_\d{6}_[A-Z]+_(\d{8})\.csv"),
        ),
    )
    for glob_pattern, filename_pattern in dated_patterns:
        for path in cache_path.glob(glob_pattern):
            match = filename_pattern.fullmatch(path.name)
            if not match:
                continue
            snapshot_date = match.group(1)
            if snapshot_date <= today and (
                latest is None or snapshot_date > latest
            ):
                latest = snapshot_date
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


def _count_generated_orders(rows: list[dict]) -> int:
    return sum(
        len(row["orders"])
        if isinstance(row.get("orders"), list)
        else 1
        for row in rows
    )


def _runtime_repo_root(store: PortfolioStore) -> Path:
    data_dir = Path(store.data_dir).resolve()
    for candidate in (data_dir, *data_dir.parents):
        if candidate.name == "data":
            return candidate.parent
    return Path.cwd()


def _run_daily_decision_cycle(
    config: dict,
    store: PortfolioStore,
    provider,
    market_module,
    *,
    as_of: str | None,
    run_id: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    trades = market_module.execute_due_orders(config, store, provider, as_of=as_of)
    nav_rows = market_module.update_nav(
        config,
        store,
        provider,
        as_of=as_of,
        notes=f"daily decision; trades={len(trades)}",
    )
    previous_targets = store.load_pending()
    store.save_pending([])
    try:
        batches = market_module.generate_rebalance_orders(
            config,
            store,
            provider,
            as_of=as_of,
            run_id=run_id,
        )
    except Exception:
        store.save_pending(previous_targets)
        raise
    return trades, nav_rows, batches


def _run_weekly_review_state(
    config: dict,
    store: PortfolioStore,
    provider,
    market_module,
    *,
    market: str,
    as_of: str | None,
) -> list[dict]:
    rows = market_module.update_nav(
        config,
        store,
        provider,
        as_of=as_of,
        notes="weekly review",
    )
    if market == "a_share":
        compute_pending_forward_ic(config, store, provider, as_of=as_of)
    return rows


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
    if args.command == "validate-strategy-pair":
        ensure_dirs(args.logs_dir)
        return _command_validate_strategy_pair(args)
    if args.command == "apply-strategy-release":
        ensure_dirs(args.logs_dir)
        return _command_apply_strategy_release(args)
    if args.command == "agent-rollback":
        ensure_dirs(args.logs_dir)
        return _command_agent_rollback(args)
    if args.command == "prepare-market-data":
        ensure_dirs(args.logs_dir)
        return _command_prepare_market_data(args)
    if args.command == "prepare-qdii-market-data":
        ensure_dirs(args.logs_dir)
        return _command_prepare_qdii_market_data(args)
    if args.command == "prepare-backtest-data":
        ensure_dirs(args.logs_dir)
        return _command_prepare_backtest_data(args)
    if args.command == "materialize-a-share-research-data":
        ensure_dirs(args.logs_dir)
        return _command_materialize_a_share_research_data(args)
    if args.command == "backfill-a-share-moneyflow":
        ensure_dirs(args.logs_dir)
        return _command_backfill_a_share_moneyflow(args)
    if args.command == "backfill-structured-earnings":
        ensure_dirs(args.logs_dir)
        return _command_backfill_structured_earnings(args)
    if args.command == "backfill-structured-capital-actions":
        ensure_dirs(args.logs_dir)
        return _command_backfill_structured_capital_actions(args)
    if args.command == "backfill-structured-holder-counts":
        ensure_dirs(args.logs_dir)
        return _command_backfill_structured_holder_counts(args)
    if args.command == "backfill-structured-share-unlocks":
        ensure_dirs(args.logs_dir)
        return _command_backfill_structured_share_unlocks(args)
    if args.command == "backfill-structured-dividends":
        ensure_dirs(args.logs_dir)
        return _command_backfill_structured_dividends(args)
    if args.command == "backfill-structured-block-trades":
        ensure_dirs(args.logs_dir)
        return _command_backfill_structured_block_trades(args)
    if args.command == "backtest":
        ensure_dirs(args.logs_dir)
        return _command_backtest(args)
    if args.command == "qdii-capacity-study":
        ensure_dirs(args.logs_dir)
        return _command_qdii_capacity_study(args)
    if args.command == "refresh-qdii-events":
        ensure_dirs(args.logs_dir)
        return _command_refresh_qdii_events(args)
    if args.command == "qdii-shadow-research":
        ensure_dirs(args.logs_dir)
        return _command_qdii_shadow_research(args)
    if args.command == "record-theme-sentiment":
        ensure_dirs(args.logs_dir)
        return _command_record_theme_sentiment(args)
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
    if args.command == "notify-workflow-summary":
        ensure_dirs(args.logs_dir)
        return _command_notify_workflow_summary(args)
    if args.command in {
        "prepare-research-data",
        "run-prediction-research",
        "refresh-research-labels",
        "train-prediction-models",
        "run-classical-tournament",
        "run-unified-model-arena",
        "run-baseline-first-research",
        "run-cross-sectional-alpha-repair",
        "run-regime-tabular-alpha",
        "freeze-regime-tabular-forward",
        "run-regime-tabular-forward",
        "predict",
    }:
        ensure_dirs(args.logs_dir)
        return _command_research_workflow(args)
    if args.command == "run-strategy-campaign":
        ensure_dirs(args.logs_dir)
        return _command_strategy_campaign(args)
    if args.command == "run-rule-core-diagnostic":
        ensure_dirs(args.logs_dir)
        return _command_rule_core_diagnostic(args)
    if args.command == "run-earnings-drift-study":
        ensure_dirs(args.logs_dir)
        return _command_earnings_drift_study(args)
    if args.command == "run-capital-actions-study":
        ensure_dirs(args.logs_dir)
        return _command_capital_actions_study(args)
    if args.command == "run-holder-concentration-study":
        ensure_dirs(args.logs_dir)
        return _command_holder_concentration_study(args)
    if args.command == "run-share-unlock-avoidance-study":
        ensure_dirs(args.logs_dir)
        return _command_share_unlock_avoidance_study(args)
    if args.command == "run-dividend-growth-study":
        ensure_dirs(args.logs_dir)
        return _command_dividend_growth_study(args)
    if args.command == "run-block-trade-premium-study":
        ensure_dirs(args.logs_dir)
        return _command_block_trade_premium_study(args)
    if args.command in {
        "research-training-bundle-export",
        "research-training-bundle-import",
        "research-model-bundle-export",
        "research-model-bundle-import",
        "research-result-bundle-export",
        "research-result-bundle-import",
    }:
        ensure_dirs(args.logs_dir)
        return _command_local_training_transfer(args)
    if args.command in {"run-model-iteration", "run-model-shadow"}:
        ensure_dirs(args.logs_dir)
        return _command_run_model_iteration(args)
    if args.command == "admit-personal-quant-shadow":
        ensure_dirs(args.logs_dir)
        return _command_admit_personal_quant_shadow(args)
    if args.command == "audit-model-shadow-quality":
        ensure_dirs(args.logs_dir)
        return _command_audit_model_shadow_quality(args)
    if args.command in {
        "audit-full-history-rebuild-data",
        "retire-full-history-legacy-shadows",
        "run-full-history-model-rebuild",
    }:
        ensure_dirs(args.logs_dir)
        return _command_full_history_rebuild_maintenance(args)
    if args.command in {
        "intelligence-ingest", "intelligence-backfill",
        "intelligence-extract", "intelligence-status", "intelligence-evaluate",
    }:
        ensure_dirs(args.logs_dir)
        return _command_intelligence(args)
    if args.command == "intelligence-model-effect":
        ensure_dirs(args.logs_dir)
        return _command_intelligence_model_effect(args)
    if args.command in {
        "intelligence-semantic-prepare",
        "intelligence-semantic-route-finalize",
        "intelligence-semantic-repair-prepare",
        "intelligence-semantic-repair-rollback",
        "intelligence-semantic-run",
        "intelligence-semantic-coding-plan-collect",
        "intelligence-semantic-import",
        "intelligence-semantic-job-status",
        "intelligence-semantic-daily",
    }:
        ensure_dirs(args.logs_dir)
        return _command_intelligence_exchange(args)
    if args.command == "intelligence-semantic-quality-evaluate":
        ensure_dirs(args.logs_dir)
        return _command_intelligence_semantic_quality(args)
    if args.command == "intelligence-semantic-frozen-run":
        ensure_dirs(args.logs_dir)
        return _command_intelligence_semantic_frozen(args)
    if args.command in {
        "intelligence-semantic-frozen-prepare",
        "intelligence-semantic-frozen-collect",
        "intelligence-semantic-frozen-repair-prepare",
        "intelligence-semantic-frozen-repair-collect",
    }:
        ensure_dirs(args.logs_dir)
        return _command_intelligence_semantic_frozen_exchange(args)
    if args.command in {
        "intelligence-artifact-job-export",
        "intelligence-artifact-job-run",
        "intelligence-artifact-job-import",
        "intelligence-artifact-job-status",
    }:
        ensure_dirs(args.logs_dir)
        return _command_intelligence_artifact_exchange(args)
    if args.command in {
        "intelligence-enrich",
        "intelligence-reconcile",
        "intelligence-semantic-status",
        "intelligence-prune-raw",
    }:
        ensure_dirs(args.logs_dir)
        return _command_intelligence_operations(args)
    if args.command == "intelligence-source-audit":
        ensure_dirs(args.logs_dir)
        return _command_intelligence_source_audit(args)

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
                from .research.formal_lineage import record_formal_decision

                record_formal_decision(
                    repo_root=_runtime_repo_root(store),
                    market=market,
                    config=config,
                    store=store,
                    run_id=run_id,
                    as_of=args.as_of or date.today().isoformat(),
                    generated=batches,
                )
                print(f"Generated {_count_generated_orders(batches)} pending orders")
            elif args.command == "execute":
                trades = market_module.execute_due_orders(config, store, provider, as_of=args.as_of)
                from .research.formal_lineage import record_formal_fills

                record_formal_fills(
                    repo_root=_runtime_repo_root(store),
                    market=market,
                    agent_id=str(config.get("agent_id") or args.agent or ""),
                    trades=trades,
                )
                print(f"Executed {len(trades)} trades")
            elif args.command == "update-nav":
                rows = market_module.update_nav(config, store, provider, as_of=args.as_of)
                from .research.formal_lineage import record_nav_attribution

                record_nav_attribution(
                    repo_root=_runtime_repo_root(store),
                    market=market,
                    agent_id=str(config.get("agent_id") or args.agent or ""),
                    store=store,
                    nav_rows=rows,
                    trades=[],
                )
                print(f"Updated NAV for {len(rows)} accounts")
            elif args.command == "report":
                path = generate_weekly_report(config, store, reports_dir, run_id=run_id)
                print(f"Report written to {path}")
            elif args.command == "dashboard":
                page_path = generate_dashboard(config, store, reports_dir)
                fragment_path = generate_dashboard(config, store, reports_dir, mode="fragment")
                print(f"Dashboard written to {page_path}; fragment {fragment_path}")
            elif args.command == "run-daily":
                trades, rows, batches = _run_daily_decision_cycle(
                    config,
                    store,
                    provider,
                    market_module,
                    as_of=args.as_of,
                    run_id=run_id,
                )
                from .research.formal_lineage import (
                    record_formal_decision,
                    record_formal_fills,
                    record_nav_attribution,
                )

                lineage_root = _runtime_repo_root(store)
                fill_projection = record_formal_fills(
                    repo_root=lineage_root,
                    market=market,
                    agent_id=str(config.get("agent_id") or args.agent or ""),
                    trades=trades,
                )
                decision_projection = record_formal_decision(
                    repo_root=lineage_root,
                    market=market,
                    config=config,
                    store=store,
                    run_id=run_id,
                    as_of=args.as_of or date.today().isoformat(),
                    generated=batches,
                )
                attribution_projection = record_nav_attribution(
                    repo_root=lineage_root,
                    market=market,
                    agent_id=str(config.get("agent_id") or args.agent or ""),
                    store=store,
                    nav_rows=rows,
                    trades=trades,
                    decision_ids=fill_projection.get("decision_ids") or {},
                )
                # Forward-IC diagnostic is A-share-only (uses Tushare-specific
                # provider methods); skip for hk/us.
                if market == "a_share":
                    compute_pending_forward_ic(config, store, provider, as_of=args.as_of)
                provider.persist_health()
                page_path = generate_dashboard(config, store, reports_dir)
                generate_dashboard(config, store, reports_dir, mode="fragment")
                order_count = _count_generated_orders(batches)
                print(
                    f"Daily decision complete: trades={len(trades)}, "
                    f"orders={order_count}, nav_rows={len(rows)}, dashboard={page_path}, "
                    f"lineage_decisions={decision_projection['inserted']['decision_runs']}, "
                    f"attributions={attribution_projection['inserted']}"
                )
            elif args.command == "run-weekly":
                rows = _run_weekly_review_state(
                    config,
                    store,
                    provider,
                    market_module,
                    market=market,
                    as_of=args.as_of,
                )
                provider.persist_health()
                report = generate_weekly_report(config, store, reports_dir, run_id=run_id)
                dashboard = generate_dashboard(config, store, reports_dir)
                generate_dashboard(config, store, reports_dir, mode="fragment")
                # The weekly briefing is part of the A-share review workflow.
                briefing = _auto_write_weekly_briefing(args.agent, args.as_of) if market == "a_share" else None
                briefing_note = f", briefing={briefing}" if briefing else ""
                print(
                    f"Weekly review complete: orders=0, nav_rows={len(rows)}, "
                    f"report={report}, dashboard={dashboard}{briefing_note}"
                )
            else:
                parser.error(f"Unknown command: {args.command}")
    finally:
        provider.persist_health()
    return 0


def _classical_tournament_cli_summary(result: dict) -> dict:
    items = result.get("results")
    reports = items if isinstance(items, list) else [result]
    summaries = []
    for report in reports:
        candidates = report.get("candidates") or []
        best = max(
            candidates,
            key=lambda item: float((item.get("metrics") or {}).get("rank_ic") or 0.0),
            default={},
        )
        best_metrics = best.get("metrics") or {}
        summaries.append({
            "account_scope": report.get("account_scope"),
            "status": report.get("status"),
            "report_path": report.get("report_path"),
            "candidate_count": len(candidates),
            "shadow_model_versions": report.get("shadow_model_versions") or [],
            "best_candidate": {
                "spec_id": best.get("spec_id"),
                "model_version": best.get("model_version"),
                "rank_ic": best_metrics.get("rank_ic"),
                "icir": best_metrics.get("icir"),
                "net_excess_return": best_metrics.get("net_excess_return"),
                "trade_count": best_metrics.get("trade_count"),
                "reasons": best.get("reasons") or [],
            },
        })
    return {
        "status": result.get("status"),
        "snapshot_date": result.get("snapshot_date"),
        "market": result.get("market"),
        "horizon": result.get("horizon"),
        "account_scopes": result.get("account_scopes") or [
            item.get("account_scope") for item in summaries
        ],
        "results": summaries,
    }


def _command_research_workflow(args: argparse.Namespace) -> int:
    from .research.pipeline import ResearchPipeline

    as_of = args.as_of
    if as_of is None and bool(args.offline):
        cache_dir = (
            Path(args.repo_root) / "data" / "shared" / "cache"
            if args.market == "a_share"
            else Path(args.repo_root) / "data" / args.market / "shared" / "cache"
        )
        as_of = _resolve_offline_as_of(cache_dir)
    pipeline = ResearchPipeline(
        args.repo_root,
        market=args.market,
        agent=args.agent or "codex",
        as_of=as_of,
        offline=bool(args.offline),
        max_full_history_instruments=args.max_full_history_instruments,
    )
    try:
        if args.command == "prepare-research-data":
            result = pipeline.prepare_data(force=bool(args.force))
        elif args.command == "run-prediction-research":
            result = pipeline.run_research()
        elif args.command == "refresh-research-labels":
            result = pipeline.refresh_labels()
        elif args.command == "train-prediction-models":
            result = pipeline.train_models()
        elif args.command == "run-classical-tournament":
            result = pipeline.run_classical_tournament(
                account_scope=args.account_scope,
                horizon=args.horizon,
                force=bool(args.force),
            )
        elif args.command == "run-unified-model-arena":
            result = pipeline.run_unified_model_arena(
                horizon=args.horizon,
                force=bool(args.force),
            )
        elif args.command == "run-cross-sectional-alpha-repair":
            result = pipeline.run_cross_sectional_alpha_repair(
                account_scope=args.account_scope,
                horizon=args.horizon,
            )
        elif args.command == "run-baseline-first-research":
            baseline_kwargs: dict[str, Any] = {
                "account_scope": args.account_scope,
                "horizon": args.horizon,
            }
            if args.training_input_bundle is not None:
                from .research.local_training import (
                    verify_installed_training_bundle,
                )

                training_manifest = verify_installed_training_bundle(
                    args.repo_root,
                    args.training_input_bundle,
                )
                if str(training_manifest.get("market") or "") != args.market:
                    raise ValueError("training_bundle_market_mismatch")
                baseline_kwargs["training_input"] = {
                    "market": args.market,
                    "snapshot_date": str(
                        training_manifest.get("snapshot_date") or ""
                    ),
                    "source_fingerprint": str(
                        training_manifest.get("source_fingerprint") or ""
                    ),
                    "files": [
                        str(item.get("path") or "")
                        for item in training_manifest.get("files") or []
                    ],
                }
            result = pipeline.run_baseline_first_research(
                **baseline_kwargs,
            )
        elif args.command == "run-regime-tabular-alpha":
            result = pipeline.run_regime_tabular_alpha(config_path=args.config)
        elif args.command == "freeze-regime-tabular-forward":
            result = pipeline.freeze_regime_tabular_forward(
                config_path=args.config,
                source_report=args.source_report,
                observation_start=args.observation_start,
            )
        elif args.command == "run-regime-tabular-forward":
            result = pipeline.run_regime_tabular_forward(config_path=args.config)
        else:
            result = pipeline.predict()
    except Exception as exc:  # noqa: BLE001
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    output = (
        _classical_tournament_cli_summary(result)
        if args.command == "run-classical-tournament"
        else result
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed", "fallback"} else 2


def _command_strategy_campaign(args: argparse.Namespace) -> int:
    from .research.strategy_campaign import run_strategy_campaign

    try:
        result = run_strategy_campaign(
            repo_root=args.repo_root,
            campaign_id=args.campaign,
            as_of=args.as_of,
            stage=str(args.stage).replace("-", "_"),
            input_manifests=tuple(args.input_bundle),
        )
    except Exception as exc:  # noqa: BLE001 - CLI reports sealed input failures
        print(f"error: run-strategy-campaign failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"failed", "fallback"} else 2


def _command_backfill_a_share_moneyflow(args: argparse.Namespace) -> int:
    import pandas as pd

    from .research.moneyflow import backfill_moneyflow_history

    try:
        codes = list(args.code or [])
        if args.codes_file is not None:
            raw = args.codes_file.read_text(encoding="utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = [line.strip() for line in raw.splitlines() if line.strip()]
            if isinstance(payload, dict):
                payload = payload.get("codes")
            if not isinstance(payload, list):
                raise ValueError("moneyflow_codes_file_invalid")
            codes.extend(str(value) for value in payload)
        end_date = str(
            args.end_date or args.as_of or date.today().isoformat()
        ).replace("-", "")[:8]
        if not codes:
            feature_root = args.repo_root / "data" / "research" / "features" / "a_share"
            snapshots = sorted(
                path for path in feature_root.glob("*.parquet")
                if path.stem.isdigit() and path.stem <= end_date
            )
            if not snapshots:
                raise FileNotFoundError("moneyflow_feature_snapshot_missing")
            codes = (
                pd.read_parquet(snapshots[-1], columns=["code"])["code"]
                .dropna()
                .astype("string")
                .drop_duplicates()
                .astype(str)
                .tolist()
            )
        codes = sorted(set(codes))
        if args.max_codes is not None:
            codes = codes[:max(1, int(args.max_codes))]
        result = backfill_moneyflow_history(
            args.repo_root,
            codes=codes,
            start_date=args.start_date,
            end_date=end_date,
            max_workers=args.max_workers,
            retries=args.retries,
            requests_per_minute=args.requests_per_minute,
            force=bool(args.force),
        )
    except Exception as exc:  # noqa: BLE001 - CLI reports bounded source failures
        print(f"error: backfill-a-share-moneyflow failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"complete", "cached"} else 2


def _command_local_training_transfer(args: argparse.Namespace) -> int:
    from .research.local_training import (
        export_model_bundle,
        export_research_result_bundle,
        export_training_bundle,
        import_model_bundle,
        import_research_result_bundle,
        install_training_bundle,
    )

    try:
        if args.command == "research-training-bundle-export":
            result = export_training_bundle(
                args.repo_root,
                market=args.market,
                as_of=args.as_of or date.today().isoformat(),
                destination=args.output,
            )
        elif args.command == "research-training-bundle-import":
            result = install_training_bundle(args.repo_root, args.bundle)
        elif args.command == "research-model-bundle-export":
            result = export_model_bundle(args.repo_root, args.report, args.output)
        elif args.command == "research-model-bundle-import":
            result = import_model_bundle(
                args.repo_root,
                args.bundle,
                training_input_bundle=args.training_input_bundle,
            )
        elif args.command == "research-result-bundle-export":
            result = export_research_result_bundle(
                args.repo_root,
                args.result,
                args.training_input_bundle,
                args.output,
            )
        else:
            result = import_research_result_bundle(
                args.repo_root,
                args.bundle,
                training_input_bundle=args.training_input_bundle,
            )
    except Exception as exc:  # noqa: BLE001 - transfer contracts fail closed
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _command_rule_core_diagnostic(args: argparse.Namespace) -> int:
    from .research.rule_core_diagnostic import run_rule_core_diagnostic

    result = run_rule_core_diagnostic(
        args.repo_root,
        as_of=args.as_of or date.today().isoformat(),
        offline=bool(args.offline),
        output_root=args.output_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_earnings_drift_study(args: argparse.Namespace) -> int:
    from .research.earnings_drift_study import run_earnings_drift_study

    try:
        result = run_earnings_drift_study(
            args.repo_root,
            snapshot_date=args.snapshot_date,
            contract_path=args.contract,
            output_root=args.output_root,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_backfill_structured_earnings(args: argparse.Namespace) -> int:
    from .intelligence.source_registry import build_adapters
    from .research.earnings_structured_backfill import (
        run_structured_earnings_backfill,
    )

    try:
        adapters = build_adapters(
            args.repo_root,
            Path(args.repo_root) / "configs" / "intelligence_sources.yaml",
        )
        adapter = next(
            item
            for item in adapters
            if getattr(item, "source", "") == "tushare_announcement"
        )
        if not hasattr(adapter, "client"):
            raise ValueError("structured_earnings_tushare_unavailable")
        result = run_structured_earnings_backfill(
            args.repo_root,
            adapter.client,
            start_date=args.start_date,
            end_date=args.end_date,
            max_partitions=args.max_partitions,
        )
    except Exception as exc:  # noqa: BLE001 - typed provider boundary
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_capital_actions_study(args: argparse.Namespace) -> int:
    from .research.capital_actions_study import run_capital_actions_study

    try:
        result = run_capital_actions_study(
            args.repo_root,
            snapshot_date=args.snapshot_date,
            contract_path=args.contract,
            output_root=args.output_root,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_backfill_structured_capital_actions(
    args: argparse.Namespace,
) -> int:
    from .intelligence.source_registry import build_adapters
    from .research.capital_actions_backfill import (
        run_capital_actions_backfill,
    )

    try:
        adapters = build_adapters(
            args.repo_root,
            Path(args.repo_root) / "configs" / "intelligence_sources.yaml",
        )
        adapter = next(
            item
            for item in adapters
            if getattr(item, "source", "") == "tushare_announcement"
        )
        if not hasattr(adapter, "client"):
            raise ValueError("capital_actions_tushare_unavailable")
        result = run_capital_actions_backfill(
            args.repo_root,
            adapter.client,
            start_date=args.start_date,
            end_date=args.end_date,
            max_partitions=args.max_partitions,
        )
    except Exception as exc:  # noqa: BLE001 - typed provider boundary
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_holder_concentration_study(args: argparse.Namespace) -> int:
    from .research.holder_concentration_study import (
        run_holder_concentration_study,
    )

    try:
        result = run_holder_concentration_study(
            args.repo_root,
            snapshot_date=args.snapshot_date,
            contract_path=args.contract,
            output_root=args.output_root,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_backfill_structured_holder_counts(
    args: argparse.Namespace,
) -> int:
    from .intelligence.source_registry import build_adapters
    from .research.holder_concentration_backfill import (
        run_holder_concentration_backfill,
    )

    try:
        adapters = build_adapters(
            args.repo_root,
            Path(args.repo_root) / "configs" / "intelligence_sources.yaml",
        )
        adapter = next(
            item
            for item in adapters
            if getattr(item, "source", "") == "tushare_announcement"
        )
        if not hasattr(adapter, "client"):
            raise ValueError("holder_concentration_tushare_unavailable")
        result = run_holder_concentration_backfill(
            args.repo_root,
            adapter.client,
            start_date=args.start_date,
            end_date=args.end_date,
            max_partitions=args.max_partitions,
        )
    except Exception as exc:  # noqa: BLE001 - typed provider boundary
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_share_unlock_avoidance_study(args: argparse.Namespace) -> int:
    from .research.share_unlock_study import run_share_unlock_study

    try:
        result = run_share_unlock_study(
            args.repo_root, snapshot_date=args.snapshot_date,
            contract_path=args.contract, output_root=args.output_root,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_backfill_structured_share_unlocks(
    args: argparse.Namespace,
) -> int:
    from .intelligence.source_registry import build_adapters
    from .research.share_unlock_backfill import run_share_unlock_backfill

    try:
        adapters = build_adapters(
            args.repo_root,
            Path(args.repo_root) / "configs" / "intelligence_sources.yaml",
        )
        adapter = next(
            item for item in adapters
            if getattr(item, "source", "") == "tushare_announcement"
        )
        if not hasattr(adapter, "client"):
            raise ValueError("share_unlock_tushare_unavailable")
        result = run_share_unlock_backfill(
            args.repo_root, adapter.client, start_date=args.start_date,
            end_date=args.end_date, max_partitions=args.max_partitions,
        )
    except Exception as exc:  # noqa: BLE001 - typed provider boundary
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_dividend_growth_study(args: argparse.Namespace) -> int:
    from .research.dividend_growth_study import run_dividend_growth_study

    try:
        result = run_dividend_growth_study(
            args.repo_root, snapshot_date=args.snapshot_date,
            contract_path=args.contract, output_root=args.output_root,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_backfill_structured_dividends(
    args: argparse.Namespace,
) -> int:
    from .intelligence.source_registry import build_adapters
    from .research.dividend_growth_backfill import run_dividend_growth_backfill

    try:
        adapters = build_adapters(
            args.repo_root,
            Path(args.repo_root) / "configs" / "intelligence_sources.yaml",
        )
        adapter = next(
            item for item in adapters
            if getattr(item, "source", "") == "tushare_announcement"
        )
        if not hasattr(adapter, "client"):
            raise ValueError("dividend_growth_tushare_unavailable")
        result = run_dividend_growth_backfill(
            args.repo_root, adapter.client, start_date=args.start_date,
            end_date=args.end_date, max_partitions=args.max_partitions,
        )
    except Exception as exc:  # noqa: BLE001 - typed provider boundary
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_block_trade_premium_study(args: argparse.Namespace) -> int:
    from .research.block_trade_study import run_block_trade_study

    try:
        result = run_block_trade_study(
            args.repo_root, snapshot_date=args.snapshot_date,
            contract_path=args.contract, output_root=args.output_root,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_backfill_structured_block_trades(
    args: argparse.Namespace,
) -> int:
    from .intelligence.source_registry import build_adapters
    from .research.block_trade_backfill import run_block_trade_backfill

    try:
        adapters = build_adapters(
            args.repo_root,
            Path(args.repo_root) / "configs" / "intelligence_sources.yaml",
        )
        adapter = next(
            item for item in adapters
            if getattr(item, "source", "") == "tushare_announcement"
        )
        if not hasattr(adapter, "client"):
            raise ValueError("block_trade_tushare_unavailable")
        result = run_block_trade_backfill(
            args.repo_root, adapter.client, start_date=args.start_date,
            end_date=args.end_date, max_partitions=args.max_partitions,
        )
    except Exception as exc:  # noqa: BLE001 - typed provider boundary
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _command_run_model_iteration(args: argparse.Namespace) -> int:
    from .model_shadow import run_model_iteration

    as_of = args.as_of
    if as_of is None and bool(args.offline) and args.market == "a_share":
        as_of = _resolve_offline_as_of(
            Path(args.repo_root) / "data" / "shared" / "cache"
        )
    as_of = as_of or date.today().isoformat()
    try:
        result = run_model_iteration(
            repo_root=args.repo_root,
            market=args.market,
            as_of=as_of,
            offline=bool(args.offline),
        )
    except Exception as exc:  # noqa: BLE001 - CLI reports a bounded failure
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "") in {"complete", "no_candidate"} else 2


def _command_admit_personal_quant_shadow(args: argparse.Namespace) -> int:
    from .research.shadow_admission import admit_campaign_shadows

    try:
        result = admit_campaign_shadows(args.repo_root, args.campaign_report)
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _command_audit_model_shadow_quality(args: argparse.Namespace) -> int:
    from .research.shadow_admission import audit_shadow_quality

    try:
        result = audit_shadow_quality(args.repo_root, apply=bool(args.apply))
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _command_full_history_rebuild_maintenance(args: argparse.Namespace) -> int:
    from .research.full_history_rebuild import (
        audit_full_history_dataset,
        retire_legacy_rebuild_shadows,
    )

    try:
        if args.command == "audit-full-history-rebuild-data":
            import pandas as pd

            frame = pd.read_parquet(args.snapshot)
            result = audit_full_history_dataset(
                frame,
                market=args.market,
                required_start=args.required_start,
                required_end=args.required_end,
            )
        elif args.command == "retire-full-history-legacy-shadows":
            result = retire_legacy_rebuild_shadows(
                args.repo_root,
                apply=bool(args.apply),
            )
        else:
            from .research.full_history_rebuild import run_full_history_rebuild

            result = run_full_history_rebuild(
                args.repo_root,
                snapshot_date=args.snapshot_date,
                scopes=args.scopes,
            )
    except (OSError, ValueError) as exc:
        print(f"error: {args.command} failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed", True) else 1



def _command_intelligence(args: argparse.Namespace) -> int:
    from .intelligence.diagnostics import build_quality_report, evaluate_event_factors
    from .intelligence.ingestion import IntelligencePipeline

    if args.command == "intelligence-status":
        result = build_quality_report(args.repo_root)
    elif args.command == "intelligence-evaluate":
        from .research.storage import ResearchStore

        root = Path(args.repo_root)
        store = ResearchStore(root / "data" / "research")
        snapshot_date = store.latest_common_snapshot_date(
            args.market,
            as_of=args.as_of or datetime.now(timezone.utc).strftime("%Y%m%d"),
        )
        result = evaluate_event_factors(
            store.read_feature_snapshot(args.market, snapshot_date),
            store.read_label_snapshot(args.market, snapshot_date),
        )
        result.update({"market": args.market, "snapshot_date": snapshot_date})
        report_dir = root / "reports" / "intelligence"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"factor_validation_{args.market}_{snapshot_date}.json"
        report_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        result["report_path"] = str(report_path)
    else:
        pipeline = IntelligencePipeline(args.repo_root, getattr(args, "config_path", None))
        if args.command == "intelligence-ingest":
            result = pipeline.ingest(
                since=args.since,
                until=args.until or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                sources=set(args.sources) if args.sources else None,
            )
        elif args.command == "intelligence-backfill":
            try:
                result = pipeline.backfill(
                    source=args.source,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    max_partitions=args.max_partitions,
                    resume=bool(args.resume),
                )
            except Exception as exc:  # noqa: BLE001 - secret-safe CLI boundary
                result = {
                    "status": "failed",
                    "source": args.source,
                    "partitions_complete": 0,
                    "partitions_failed": 0,
                    "fetched": 0,
                    "inserted": 0,
                    "b_share_filtered": 0,
                    "live_cursor_unchanged": True,
                    "error": type(exc).__name__,
                }
        else:
            result = pipeline.extract(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = any(item.get("status") == "failed" for item in result.get("sources", []))
    partial = args.command == "intelligence-backfill" and result.get("status") != "complete"
    if failed or result.get("status") == "failed":
        return 2
    return 3 if partial else 0


def _command_intelligence_model_effect(args: argparse.Namespace) -> int:
    from .research.intelligence_effect import (
        evaluate_latest_intelligence_effect,
    )

    try:
        result = evaluate_latest_intelligence_effect(
            args.repo_root,
            market=args.market,
            as_of=args.as_of,
        )
    except (FileNotFoundError, ValueError) as exc:
        result = {
            "status": "failed",
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "complete" else 3


def _command_intelligence_exchange(args: argparse.Namespace) -> int:
    from .intelligence.semantic.exchange import (
        SemanticExchangeError,
        collect_coding_plan_outputs,
        finalize_deterministic_routes,
        import_job,
        job_status,
        prepare_job,
        prepare_repair_job,
        rollback_repair,
        run_daily,
        run_job,
    )

    try:
        if args.command == "intelligence-semantic-prepare":
            prepare_kwargs = {
                "profile_id": args.profile,
                "limit": args.limit,
                "max_input_characters": args.max_input_characters,
            }
            if any(
                value is not None
                for value in (
                    args.executor_mode,
                    args.provider,
                    args.model,
                    args.client_version,
                )
            ):
                prepare_kwargs.update(
                    {
                        "executor_mode": args.executor_mode,
                        "executor_provider": args.provider,
                        "executor_model": args.model,
                        "executor_client_version": args.client_version,
                    }
                )
            result = prepare_job(args.repo_root, **prepare_kwargs)
        elif args.command == "intelligence-semantic-route-finalize":
            result = finalize_deterministic_routes(
                args.repo_root,
                profile_id=args.profile,
                limit=args.limit,
            )
        elif args.command == "intelligence-semantic-repair-prepare":
            result = prepare_repair_job(
                args.repo_root,
                document_ids=args.document_id,
                reason=args.reason,
                profile_id=args.profile,
                max_input_characters=args.max_input_characters,
            )
        elif args.command == "intelligence-semantic-repair-rollback":
            result = rollback_repair(args.repo_root, args.repair_id)
        elif args.command == "intelligence-semantic-run":
            result = run_job(
                args.repo_root,
                args.job,
                executor_config=args.executor_config,
            )
        elif args.command == "intelligence-semantic-coding-plan-collect":
            result = collect_coding_plan_outputs(args.repo_root, args.job)
        elif args.command == "intelligence-semantic-import":
            result = import_job(
                args.repo_root,
                args.job,
                refresh_features=True,
            )
        elif args.command == "intelligence-semantic-job-status":
            result = job_status(args.repo_root, args.job)
        else:
            result = run_daily(
                args.repo_root,
                profile_id=args.profile,
                limit=args.limit,
                max_input_characters=args.max_input_characters,
                executor_config=args.executor_config,
            )
    except SemanticExchangeError as exc:
        result = {
            "status": "failed",
            "error": exc.code,
            "detail": exc.detail,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    status = str(result.get("status") or "")
    if status in {"failed"}:
        return 2
    if (
        args.command == "intelligence-semantic-coding-plan-collect"
        and status == "ready_to_import"
    ):
        return 0
    if status in {"partial", "awaiting_executor", "ready_to_import"}:
        return 3
    return 0


def _command_intelligence_semantic_quality(args: argparse.Namespace) -> int:
    from .intelligence.semantic.quality import evaluate_files

    try:
        result = evaluate_files(
            args.reference,
            args.predictions,
            args.output,
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _command_intelligence_semantic_frozen(args: argparse.Namespace) -> int:
    from .intelligence.semantic.benchmark_runner import run_frozen_benchmark

    try:
        result = run_frozen_benchmark(
            args.repo_root,
            args.workbench,
            profile_id=args.profile,
            predictions_path=args.predictions,
            report_path=args.report,
            executor_config=args.executor_config,
            limit=args.limit,
            document_ids=args.document_id,
        )
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "complete" else 3


def _command_intelligence_semantic_frozen_exchange(args: argparse.Namespace) -> int:
    from .intelligence.semantic.benchmark_runner import (
        collect_frozen_coding_plan_job,
        collect_frozen_coding_plan_repair_job,
        prepare_frozen_coding_plan_job,
        prepare_frozen_coding_plan_repair_job,
    )

    try:
        if args.command == "intelligence-semantic-frozen-prepare":
            result = prepare_frozen_coding_plan_job(
                args.repo_root,
                args.workbench,
                profile_id=args.profile,
                job_dir=args.job,
                provider=args.provider,
                model=args.model,
                client_version=args.client_version,
                limit=args.limit,
                document_ids=args.document_id,
            )
        elif args.command == "intelligence-semantic-frozen-collect":
            result = collect_frozen_coding_plan_job(
                args.repo_root,
                args.workbench,
                job_dir=args.job,
                predictions_path=args.predictions,
                report_path=args.report,
            )
        elif args.command == "intelligence-semantic-frozen-repair-prepare":
            result = prepare_frozen_coding_plan_repair_job(
                args.repo_root,
                args.workbench,
                source_job_dir=args.source_job,
                source_predictions_path=args.source_predictions,
                repair_job_dir=args.repair_job,
                provider=args.provider,
                model=args.model,
                client_version=args.client_version,
            )
        else:
            result = collect_frozen_coding_plan_repair_job(
                args.repo_root,
                args.workbench,
                source_job_dir=args.source_job,
                source_predictions_path=args.source_predictions,
                repair_job_dir=args.repair_job,
                predictions_path=args.predictions,
                report_path=args.report,
            )
    except (OSError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {
        "prepared",
        "complete",
        "not_needed",
    } else 3


def _command_intelligence_artifact_exchange(
    args: argparse.Namespace,
) -> int:
    from .intelligence.artifact_exchange import (
        ArtifactExchangeError,
        artifact_worker_status,
        export_artifact_job,
        import_artifact_job,
        run_artifact_job,
    )

    try:
        if args.command == "intelligence-artifact-job-export":
            result = export_artifact_job(
                args.repo_root,
                stage=args.stage,
                limit=args.limit,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
            )
        elif args.command == "intelligence-artifact-job-run":
            result = run_artifact_job(
                args.repo_root,
                args.job_dir,
                workers=args.workers,
            )
        elif args.command == "intelligence-artifact-job-import":
            result = import_artifact_job(
                args.repo_root,
                args.job_dir,
            )
        else:
            result = artifact_worker_status(args.repo_root)
    except ArtifactExchangeError as exc:
        result = {
            "status": "failed",
            "error": exc.code,
            "detail": exc.detail,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    except (OSError, ValueError) as exc:
        result = {
            "status": "failed",
            "error": "artifact_job_configuration",
            "detail": type(exc).__name__,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _command_intelligence_operations(args: argparse.Namespace) -> int:
    from .intelligence.operations import (
        FatalOperationError,
        run_intelligence_enrich,
        run_intelligence_prune_raw,
        run_intelligence_reconcile,
        run_semantic_status,
    )

    try:
        if args.command == "intelligence-enrich":
            result = run_intelligence_enrich(
                args.repo_root,
                limit=args.limit,
                stages=tuple(args.stages),
            )
        elif args.command == "intelligence-reconcile":
            result = run_intelligence_reconcile(
                args.repo_root,
                lookback_days=args.lookback_days,
                limit=args.limit,
                stages=tuple(args.stages),
            )
        elif args.command == "intelligence-prune-raw":
            result = run_intelligence_prune_raw(
                args.repo_root,
                source=args.source,
            )
        else:
            result = run_semantic_status(args.repo_root)
    except FatalOperationError as exc:
        print(
            json.dumps(
                exc.report,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    except (ValueError, OSError):
        result = {
            "status": "failed",
            "error": "configuration",
            "counts": {},
            "retryable_failures": 0,
            "terminal_failures": 1,
            "next_queue_depth": 0,
        }
        print(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _command_intelligence_source_audit(
    args: argparse.Namespace,
) -> int:
    from .intelligence.cross_source import CrossSourceAuditor

    auditor = CrossSourceAuditor(args.repo_root)
    as_of = auditor.resolve_as_of(args.audit_as_of)
    full_market = args.announcement_scope == "full-market"
    if full_market or "announcement" not in args.datasets:
        announcement_codes: tuple[str, ...] = ()
    elif args.codes:
        announcement_codes = tuple(
            str(code).strip().upper()
            for code in args.codes
            if str(code).strip()
        )
    else:
        announcement_codes = auditor.operational_codes()
    if (
        "announcement" in args.datasets
        and not full_market
        and not announcement_codes
    ):
        result = {
            "status": "degraded",
            "as_of": as_of,
            "error": "ifind_operational_codes_empty",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    try:
        result = auditor.run(
            as_of=as_of,
            datasets=set(args.datasets),
            full_market_announcements=full_market,
            announcement_codes=announcement_codes,
            supplement=bool(args.supplement),
        )
    except Exception as exc:  # noqa: BLE001 - secret-safe CLI boundary
        result = {
            "status": "failed",
            "as_of": as_of,
            "error": type(exc).__name__,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
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


def _command_validate_strategy_pair(args: argparse.Namespace) -> int:
    from .strategy_registry import StrategyPairInvalid, validate_market_strategy_pair

    market = getattr(args, "market", None) or "a_share"
    try:
        result = validate_market_strategy_pair(market, repo_root=Path.cwd())
    except StrategyPairInvalid as exc:
        print(f"错误：双策略差异守卫失败 — {exc}", file=sys.stderr)
        return 1
    print(
        f"OK: market={market} factor_distance={result['factor_distance']:.4f} "
        f"floor={result['factor_distance_floor']:.4f}"
    )
    return 0


def _command_apply_strategy_release(args: argparse.Namespace) -> int:
    from .strategy_registry import StrategyPairInvalid
    from .strategy_release import StrategyReleaseInvalid, apply_strategy_release

    try:
        result = apply_strategy_release(
            args.manifest,
            repo_root=Path.cwd(),
            dry_run=bool(args.dry_run),
        )
    except (StrategyReleaseInvalid, StrategyPairInvalid, OverlayGuardError) as exc:
        print(f"错误：策略 release 失败 — {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
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


def _command_prepare_qdii_market_data(args: argparse.Namespace) -> int:
    from .markets.cn_qdii_etf.market_data import prepare_market_data

    try:
        snapshot = prepare_market_data(
            repo_root=Path.cwd(),
            as_of=args.as_of,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: prepare-qdii-market-data failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if snapshot.get("status") != "failed" else 2


def _command_prepare_backtest_data(args: argparse.Namespace) -> int:
    """Incrementally fetch historical Tushare data into backtest_cache/."""
    from .markets.a_share.backtest import data_prep

    try:
        phases = None
        if args.phases:
            phases = {
                phase.strip()
                for phase in args.phases.split(",")
                if phase.strip()
            }
        summary = data_prep.prepare_backtest_data(
            start=args.start,
            end=args.end,
            cache_root=args.cache_root,
            force=args.force,
            phases=phases,
            code_scope=args.code_scope,
            code_offset=args.code_offset,
            code_limit=args.code_limit,
            status_provider=args.status_provider,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: prepare-backtest-data failed: {exc}", file=sys.stderr)
        return 2
    if isinstance(summary, dict):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"Prepare backtest-data: start={args.start.isoformat()} "
            f"end={args.end.isoformat()} cache_root={args.cache_root} done"
        )
    return 0


def _command_materialize_a_share_research_data(args: argparse.Namespace) -> int:
    from .research.a_share_materializer import materialize_a_share_research_data

    try:
        result = materialize_a_share_research_data(
            repo_root=args.repo_root,
            cache_root=args.cache_root,
            start=args.start,
            end=args.end,
            as_of=args.as_of,
        )
    except Exception as exc:  # noqa: BLE001 - CLI contract fails closed
        print(f"error: materialize-a-share-research-data failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
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


def _command_qdii_capacity_study(args: argparse.Namespace) -> int:
    """Run a network-free QDII capacity study from the shared cache."""

    from .markets.cn_qdii_etf import capacity_study, research_panel

    try:
        if args.end is not None:
            end_date = args.end
        else:
            payload = json.loads(args.universe.read_text(encoding="utf-8"))
            end_date = date.fromisoformat(str(payload["as_of"])[:10])
        if args.start is not None:
            start_date = args.start
        else:
            try:
                start_date = end_date.replace(year=end_date.year - 3)
            except ValueError:
                start_date = end_date.replace(year=end_date.year - 3, day=28)
        if start_date > end_date:
            raise capacity_study.CapacityStudyError("invalid_date_range")

        root = Path.cwd()
        baseline = load_config(root / "configs" / "competition_cn_qdii_etf.yaml")
        overlays = {
            agent_id: load_config(
                root / "configs" / "agents" / f"{agent_id}_cn_qdii_etf.yaml"
            )
            for agent_id in ("claude", "codex")
        }
        panel = research_panel.build_research_panel(
            args.cache_dir,
            args.universe,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
        panel_start = date.fromisoformat(str(panel.metadata.get("start") or start_date))
        panel_end = date.fromisoformat(str(panel.metadata.get("end") or end_date))
        effective_start = max(start_date, panel_start)
        effective_end = min(end_date, panel_end)
        result = capacity_study.run_capacity_study(
            panel,
            overlays=overlays,
            baseline=baseline,
            top_ns=list(args.top_n),
            start=effective_start.isoformat(),
            end=effective_end.isoformat(),
            min_signal_weeks=max(int(args.min_signal_weeks), 1),
        )
        paths = capacity_study.write_capacity_artifacts(
            result,
            args.output_root,
            end_date=effective_end.isoformat(),
        )
    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        research_panel.ResearchPanelError,
        capacity_study.CapacityStudyError,
    ) as exc:
        print(f"error: QDII capacity study failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"QDII capacity study complete: run_id={result.run_id} "
        f"rows={len(result.metrics)}"
    )
    print(f"  report: {paths['report']}")
    for item in result.summary.get("recommendations", []):
        print(
            "  research: "
            f"{item.get('strategy')}/{item.get('scope')} "
            f"top_n={item.get('recommended_top_n')}"
        )
    return 0


def _command_refresh_qdii_events(args: argparse.Namespace) -> int:
    from .markets.cn_qdii_etf import fund_events

    try:
        payload = json.loads(args.universe.read_text(encoding="utf-8"))
        codes = sorted(
            {
                str(row["code"])
                for rows in (payload.get("scopes") or {}).values()
                for row in rows
                if row.get("code")
            }
        )
        if not codes:
            raise ValueError("empty_qdii_universe")
        events = fund_events.refresh_event_store(codes, args.output)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"error: QDII event refresh failed: {exc}", file=sys.stderr)
        return 2
    print(f"QDII events refreshed: codes={len(codes)} events={len(events)} output={args.output}")
    return 0


def _command_qdii_shadow_research(args: argparse.Namespace) -> int:
    from .markets.cn_qdii_etf import data_provider, research_catalog, research_panel, shadow_research, theme_sentiment

    end_date = args.end or date.today()
    if args.start is not None:
        start_date = args.start
    else:
        try:
            start_date = end_date.replace(year=end_date.year - 3)
        except ValueError:
            start_date = end_date.replace(year=end_date.year - 3, day=28)
    if start_date > end_date:
        print("error: QDII shadow research failed: invalid_date_range", file=sys.stderr)
        return 2
    end_key = end_date.strftime("%Y%m%d")
    try:
        provider = data_provider.make_provider(
            cache_dir=args.cache_dir,
            offline=not args.refresh_data,
            as_of=end_date.isoformat(),
            history_start=start_date.isoformat(),
        )
        basic = provider._fund_basic(refresh=bool(args.refresh_data), as_of_key=end_key)
        catalog = research_catalog.build_research_catalog(basic, as_of=end_date)
        if catalog.empty:
            raise ValueError("empty_research_catalog")
        available_codes: list[str] = []
        for code in catalog["code"].astype(str):
            try:
                daily = provider._fund_daily(code, end_key)
                if daily is None or daily.empty:
                    continue
                available_codes.append(code)
                if args.refresh_data:
                    for loader in (
                        lambda: provider.fund_adj(code, as_of=end_key),
                        lambda: provider._fund_nav(code, end_key),
                        lambda: provider._fund_share(code, end_key),
                    ):
                        try:
                            loader()
                        except Exception as exc:  # noqa: BLE001 - optional research field
                            provider.record_health("qdii_shadow_optional", "failed", f"{code}: {exc}")
            except Exception as exc:  # noqa: BLE001 - catalog coverage is reported below
                provider.record_health("qdii_shadow_daily", "failed", f"{code}: {exc}")
        catalog = catalog.loc[catalog["code"].astype(str).isin(available_codes)].copy()
        if catalog.empty:
            raise ValueError("research_history_unavailable")
        payload = research_catalog.catalog_payload(catalog, as_of=end_date.isoformat())
        args.catalog.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.catalog, payload)
        catalog.to_csv(args.catalog.with_suffix(".csv"), index=False)
        panel = research_panel.build_research_panel(
            args.cache_dir,
            args.catalog,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
        effective_end = date.fromisoformat(str(panel.metadata.get("end") or end_date))
        result = shadow_research.run_shadow_research(
            panel,
            catalog,
            start=start_date.isoformat(),
            end=effective_end.isoformat(),
            min_signal_weeks=max(int(args.min_signal_weeks), 1),
            theme_sentiment_records=theme_sentiment.load_theme_sentiment(args.sentiment_file),
            sentiment_agent=args.sentiment_agent,
        )
        paths = shadow_research.write_shadow_artifacts(
            result,
            args.output_root,
            end_date=effective_end.isoformat(),
        )
        provider.persist_health()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, research_panel.ResearchPanelError) as exc:
        print(f"error: QDII shadow research failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"QDII shadow research complete: run_id={result.run_id} "
        f"catalog={len(catalog)} metrics={len(result.metrics)}"
    )
    print(f"  report: {paths['report']}")
    return 0


def _command_record_theme_sentiment(args: argparse.Namespace) -> int:
    from .markets.cn_qdii_etf import theme_sentiment

    try:
        rows = theme_sentiment.record_theme_sentiment(
            args.output,
            agent=args.agent,
            week_end=args.week_end,
            index_key=args.index_key,
            score=args.score,
            confidence=args.confidence,
            drivers=args.drivers,
            sources=args.sources,
            llm_model=args.llm_model,
            prompt_version=args.prompt_version,
            force=args.force,
        )
    except (OSError, theme_sentiment.SentimentValidationError) as exc:
        print(f"error: theme sentiment record failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"QDII theme sentiment recorded: agent={args.agent} "
        f"index={args.index_key} rows={len(rows)}"
    )
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
    """Compatibility alias for ``notify-workflow-summary --cadence daily``."""
    from .workflow_notifications import cli_send_workflow_summary

    return cli_send_workflow_summary("daily", repo_root=args.repo_root)


def _command_notify_workflow_summary(args: argparse.Namespace) -> int:
    from .workflow_notifications import cli_send_workflow_summary

    return cli_send_workflow_summary(
        args.cadence,
        repo_root=args.repo_root,
        target=args.target,
        force=args.force,
        preview=args.preview,
        require_complete=args.require_complete,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )


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
        parts = path.split("/")
        if (
            len(parts) == 5
            and parts[1] == "strategy-reports"
            and parts[2] in competition.MARKETS
            and parts[4] == "weekly_report.md"
        ):
            market = parts[2]
            strategy_key = parts[3]
            agent = next(
                (
                    slot
                    for slot, public_key in PUBLIC_STRATEGY_KEYS.items()
                    if public_key == strategy_key
                ),
                None,
            )
            if agent is not None:
                target = f"/{market}/{agent}/weekly_report.md"
                if (
                    not (directory / target.lstrip("/")).exists()
                    and market == "a_share"
                ):
                    legacy_target = f"/{agent}/weekly_report.md"
                    if (directory / legacy_target.lstrip("/")).exists():
                        target = legacy_target
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
        "/api/dashboard/instrument.json",
        "/api/dashboard/system-overview.json",
        "/api/dashboard/model-research.json",
        "/api/dashboard/data-intelligence.json",
        "/api/dashboard/operations-center.json",
        "/api/dashboard/overview.json",
        "/api/dashboard/performance.json",
        "/api/dashboard/portfolio.json",
        "/api/dashboard/predictions.json",
        "/api/dashboard/research.json",
        "/api/dashboard/operations.json",
        "/api/dashboard/governance.json",
        "/api/dashboard/intelligence.json",
        "/api/dashboard/intelligence-event.json",
        "/api/dashboard/intelligence-document.json",
    }


def _dashboard_api_error_response(exc: Exception) -> tuple[int, dict[str, str]]:
    from .dashboard_http import (
        DashboardResourceNotFound,
        InvalidDashboardQuery,
    )

    if isinstance(exc, InvalidDashboardQuery):
        return 400, {
            "error": "invalid_query",
            "message": str(exc),
        }
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
    if isinstance(exc, DashboardResourceNotFound):
        return 404, {
            "error": "resource_not_found",
            "message": "Dashboard resource not found",
        }
    from .dashboard_aggregator import DashboardDataError
    from .dashboard_finance import InstrumentDataError, InvalidInstrumentCode

    if isinstance(exc, InvalidInstrumentCode):
        return 400, {
            "error": "invalid_instrument_code",
            "message": "Invalid instrument code",
        }

    if isinstance(exc, DashboardDataError):
        return 500, {
            "error": "dashboard_data_invalid",
            "message": f"Dashboard data source is unreadable: {exc.source}",
        }
    if isinstance(exc, InstrumentDataError):
        return 500, {
            "error": "instrument_data_invalid",
            "message": f"Instrument data source is unreadable: {exc.source}",
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

    from .dashboard_http import DashboardResponseCache

    protocol_version = "HTTP/1.1"
    _response_cache = DashboardResponseCache(
        ttl_seconds=15,
        stale_seconds=86_400,
    )

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
        from .dashboard_http import InvalidDashboardQuery, build_http_response

        repo_root = Path(self.directory).resolve().parent
        request_started = time.perf_counter()
        headers = getattr(self, "headers", {})
        request_id = headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        try:
            params = parse_qs(query, keep_blank_values=False)
            canonical_path = (
                "/api/dashboard/summary.json"
                if path == "/api/dashboard.json"
                else path
            )
            cache_key = json.dumps(
                {
                    "root": str(repo_root),
                    "path": canonical_path,
                    "query": sorted((key, tuple(values)) for key, values in params.items()),
                },
                sort_keys=True,
                separators=(",", ":"),
            )

            def build_payload() -> dict:
                if canonical_path == "/api/dashboard/summary.json":
                    from .dashboard_aggregator import build_dashboard_summary_data

                    return build_dashboard_summary_data(
                        repo_root=repo_root,
                        markets=list(competition.MARKETS),
                    )
                if canonical_path == "/api/dashboard/operations-center.json":
                    from .dashboard_workspace_api import (
                        build_dashboard_operations_center_data,
                    )

                    scope = (params.get("scope") or ["all"])[0]
                    return build_dashboard_operations_center_data(
                        repo_root=repo_root,
                        scope=scope,
                    )
                market = (params.get("market") or ["a_share"])[0]
                agent = (params.get("agent") or ["codex"])[0]
                if canonical_path == "/api/dashboard/model-research.json":
                    from .dashboard_workspace_api import (
                        build_dashboard_model_research_data,
                    )

                    return build_dashboard_model_research_data(
                        repo_root=repo_root,
                        market=market,
                    )
                if canonical_path == "/api/dashboard/data-intelligence.json":
                    from .dashboard_workspace_api import (
                        build_dashboard_data_intelligence_data,
                    )

                    return build_dashboard_data_intelligence_data(
                        repo_root=repo_root,
                        market=market,
                    )
                if canonical_path == "/api/dashboard/detail.json":
                    from .dashboard_aggregator import build_dashboard_detail_data

                    return build_dashboard_detail_data(
                        repo_root=repo_root,
                        market=market,
                        agent=agent,
                    )
                if canonical_path == "/api/dashboard/instrument.json":
                    from .dashboard_aggregator import build_dashboard_instrument_data

                    code = (params.get("code") or [""])[0]
                    return build_dashboard_instrument_data(
                        repo_root=repo_root,
                        market=market,
                        agent=agent,
                        code=code,
                    )
                from .dashboard_api import (
                    build_dashboard_intelligence_data,
                    build_dashboard_intelligence_document_data,
                    build_dashboard_intelligence_event_data,
                    build_dashboard_operations_data,
                    build_dashboard_governance_data,
                    build_dashboard_overview_data,
                    build_dashboard_performance_data,
                    build_dashboard_portfolio_data,
                    build_dashboard_predictions_data,
                    build_dashboard_research_data,
                    build_dashboard_system_overview_data,
                )

                if canonical_path == "/api/dashboard/system-overview.json":
                    return build_dashboard_system_overview_data(
                        repo_root=repo_root,
                    )
                builders = {
                    "/api/dashboard/overview.json": build_dashboard_overview_data,
                    "/api/dashboard/performance.json": build_dashboard_performance_data,
                    "/api/dashboard/portfolio.json": build_dashboard_portfolio_data,
                    "/api/dashboard/research.json": build_dashboard_research_data,
                    "/api/dashboard/operations.json": build_dashboard_operations_data,
                    "/api/dashboard/governance.json": build_dashboard_governance_data,
                }
                if canonical_path == "/api/dashboard/predictions.json":
                    raw_limit = (params.get("limit_per_horizon") or ["12"])[0]
                    try:
                        limit = int(raw_limit)
                    except ValueError as exc:
                        raise InvalidDashboardQuery(
                            "limit_per_horizon must be an integer"
                        ) from exc
                    return build_dashboard_predictions_data(
                        repo_root=repo_root,
                        market=market,
                        agent=agent,
                        limit_per_horizon=limit,
                    )
                if canonical_path == "/api/dashboard/intelligence.json":
                    return build_dashboard_intelligence_data(
                        repo_root=repo_root,
                        market=market,
                        agent=agent,
                    )
                if (
                    canonical_path
                    == "/api/dashboard/intelligence-event.json"
                ):
                    event_id = (params.get("event_id") or [""])[0]
                    return build_dashboard_intelligence_event_data(
                        repo_root=repo_root,
                        market=market,
                        agent=agent,
                        event_id=event_id,
                    )
                if (
                    canonical_path
                    == "/api/dashboard/intelligence-document.json"
                ):
                    document_id = (
                        params.get("document_id") or [""]
                    )[0]
                    return build_dashboard_intelligence_document_data(
                        repo_root=repo_root,
                        market=market,
                        agent=agent,
                        document_id=document_id,
                    )
                builder = builders[canonical_path]
                return builder(repo_root=repo_root, market=market, agent=agent)

            entry, cache_status = self._response_cache.get_or_build(
                cache_key,
                build_payload,
            )
            elapsed_ms = (time.perf_counter() - request_started) * 1000.0
            response = build_http_response(
                entry,
                accept_encoding=headers.get("Accept-Encoding", ""),
                if_none_match=headers.get("If-None-Match"),
                cache_status=cache_status,
                request_id=request_id,
                elapsed_ms=elapsed_ms,
            )
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)
            return
        except Exception as exc:  # noqa: BLE001
            status, error_payload = _dashboard_api_error_response(exc)
            raw = json.dumps(
                error_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Request-ID", request_id)
            self.send_header(
                "Server-Timing",
                f"app;dur={(time.perf_counter() - request_started) * 1000.0:.1f}",
            )
            self.end_headers()
            self.wfile.write(raw)


class _DashboardHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_dashboard(reports_dir: str, host: str, port: int) -> int:
    directory = Path(reports_dir).resolve()
    handler = partial(_DashboardRequestHandler, directory=str(directory))

    with _DashboardHTTPServer((host, port), handler) as httpd:
        print(f"Serving {directory} at http://{host}:{port}")
        print("Routes: / → /competition/simple.html (beginner), /pro.html → /competition/dashboard.html")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
