from __future__ import annotations

import argparse
import http.server
import socketserver
from functools import partial
from pathlib import Path

from .config import load_config
from .data_provider import AkshareProvider
from .reporting import generate_dashboard, generate_weekly_report
from .simulator import execute_due_orders, generate_rebalance_orders, initialize, update_nav
from .store import PortfolioStore
from .utils import ensure_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A-share forward simulation toolkit")
    parser.add_argument("--config", default="configs/strategy_v1.yaml")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--as-of", help="Override run date in YYYY-MM-DD format")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize runtime state")
    sub.add_parser("rebalance", help="Generate weekly signals and pending orders")
    sub.add_parser("execute", help="Execute pending orders whose date is due")
    sub.add_parser("update-nav", help="Update account NAV")
    sub.add_parser("report", help="Generate weekly report")
    sub.add_parser("dashboard", help="Generate dashboard HTML")
    sub.add_parser("run-daily", help="Execute due orders, update NAV, refresh dashboard")
    sub.add_parser("run-weekly", help="Generate signals, update NAV, report, and dashboard")
    serve = sub.add_parser("serve-dashboard", help="Serve reports directory on localhost")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ensure_dirs(args.data_dir, args.reports_dir, args.logs_dir)

    if args.command == "serve-dashboard":
        return serve_dashboard(args.reports_dir, args.host, args.port)

    config = load_config(args.config)
    store = PortfolioStore(args.data_dir)
    provider = AkshareProvider(cache_dir=Path(args.data_dir) / "cache")

    try:
        if args.command == "init":
            initialize(config, store)
            print(f"Initialized {args.data_dir}")
        elif args.command == "rebalance":
            batches = generate_rebalance_orders(config, store, provider, as_of=args.as_of)
            print(f"Generated {sum(len(batch.get('orders', [])) for batch in batches)} pending orders")
        elif args.command == "execute":
            trades = execute_due_orders(config, store, provider, as_of=args.as_of)
            print(f"Executed {len(trades)} trades")
        elif args.command == "update-nav":
            rows = update_nav(config, store, provider, as_of=args.as_of)
            print(f"Updated NAV for {len(rows)} accounts")
        elif args.command == "report":
            path = generate_weekly_report(config, store, args.reports_dir)
            print(f"Report written to {path}")
        elif args.command == "dashboard":
            path = generate_dashboard(config, store, args.reports_dir)
            print(f"Dashboard written to {path}")
        elif args.command == "run-daily":
            trades = execute_due_orders(config, store, provider, as_of=args.as_of)
            rows = update_nav(config, store, provider, as_of=args.as_of, notes=f"daily; trades={len(trades)}")
            provider.persist_health()
            path = generate_dashboard(config, store, args.reports_dir)
            print(f"Daily run complete: trades={len(trades)}, nav_rows={len(rows)}, dashboard={path}")
        elif args.command == "run-weekly":
            batches = generate_rebalance_orders(config, store, provider, as_of=args.as_of)
            rows = update_nav(config, store, provider, as_of=args.as_of, notes="weekly signal")
            provider.persist_health()
            report = generate_weekly_report(config, store, args.reports_dir)
            dashboard = generate_dashboard(config, store, args.reports_dir)
            print(f"Weekly run complete: batches={len(batches)}, nav_rows={len(rows)}, report={report}, dashboard={dashboard}")
        else:
            parser.error(f"Unknown command: {args.command}")
    finally:
        provider.persist_health()
    return 0


def serve_dashboard(reports_dir: str, host: str, port: int) -> int:
    directory = Path(reports_dir).resolve()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with socketserver.TCPServer((host, port), handler) as httpd:
        print(f"Serving {directory} at http://{host}:{port}")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
