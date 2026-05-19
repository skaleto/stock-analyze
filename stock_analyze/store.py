from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .utils import append_csv, ensure_dirs, now_iso, read_json, write_json


STATE_FILE = "state.json"
DAILY_NAV_FILE = "daily_nav.csv"
TRADES_FILE = "trades.csv"
POSITIONS_FILE = "positions.csv"
PENDING_FILE = "pending_orders.json"
SIGNALS_FILE = "latest_signals.csv"
PERFORMANCE_FILE = "performance_summary.json"
FACTOR_RUNS_DIR = "factor_runs"
FACTOR_DIAGNOSTICS_DIR = "factor_diagnostics"
FACTOR_COVERAGE_FILE = "coverage.csv"
FACTOR_FORWARD_IC_FILE = "forward_ic.csv"

FACTOR_COVERAGE_COLUMNS = [
    "signal_date",
    "account_id",
    "factor",
    "coverage_pct",
    "missing_count",
    "mean",
    "p5",
    "p50",
    "p95",
    "std",
]

FORWARD_IC_COLUMNS = [
    "signal_date",
    "account_id",
    "factor",
    "ic",
    "sample_size",
    "ic_status",
    "computed_at",
]


class PortfolioStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        ensure_dirs(self.data_dir)

    @property
    def state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    @property
    def pending_path(self) -> Path:
        return self.data_dir / PENDING_FILE

    def initialize(self, config: dict[str, Any], force: bool = False) -> dict[str, Any]:
        if self.state_path.exists() and not force:
            return self.load_state()
        accounts: dict[str, Any] = {}
        for account in config.get("accounts", []):
            accounts[str(account["id"])] = {
                "name": account.get("name", account["id"]),
                "scope": account["scope"],
                "benchmark": account["benchmark"],
                "cash": float(account["cash"]),
                "positions": {},
            }
        state = {
            "strategy_id": config.get("strategy_id", "strategy"),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "accounts": accounts,
        }
        self.save_state(state)
        write_json(self.pending_path, [])
        self.write_positions(state)
        return state

    def load_state(self) -> dict[str, Any]:
        return read_json(self.state_path, {"accounts": {}})

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = now_iso()
        write_json(self.state_path, state)

    def load_pending(self) -> list[dict[str, Any]]:
        return read_json(self.pending_path, [])

    def save_pending(self, pending: list[dict[str, Any]]) -> None:
        write_json(self.pending_path, pending)

    def save_signals(self, df: pd.DataFrame) -> Path:
        path = self.data_dir / SIGNALS_FILE
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def append_trades(self, rows: list[dict[str, Any]]) -> None:
        append_csv(
            self.data_dir / TRADES_FILE,
            rows,
            [
                "trade_date",
                "account_id",
                "code",
                "name",
                "side",
                "shares",
                "price",
                "gross_amount",
                "commission",
                "stamp_tax",
                "slippage",
                "net_amount",
                "cash_after",
                "reason",
            ],
        )

    def append_nav(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = [
            "date",
            "account_id",
            "cash",
            "market_value",
            "total_value",
            "benchmark_code",
            "benchmark_close",
            "benchmark_date",
            "notes",
        ]
        path = self.data_dir / DAILY_NAV_FILE
        if path.exists():
            existing = pd.read_csv(path)
        else:
            existing = pd.DataFrame(columns=columns)
        new_rows = pd.DataFrame(rows, columns=columns)
        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(["date", "account_id"], keep="last")
        combined = combined.sort_values(["date", "account_id"])
        combined.to_csv(path, index=False, encoding="utf-8-sig")

    def write_positions(self, state: dict[str, Any]) -> None:
        rows: list[dict[str, Any]] = []
        for account_id, account in state.get("accounts", {}).items():
            for code, position in account.get("positions", {}).items():
                row = {"account_id": account_id, "code": code}
                row.update(position)
                rows.append(row)
        columns = [
            "account_id",
            "code",
            "name",
            "industry",
            "shares",
            "available_shares",
            "avg_cost",
            "last_buy_date",
            "hold_since",
            "last_price",
            "market_value",
            "unrealized_pnl",
            "score",
            "reason",
            "updated_at",
        ]
        path = self.data_dir / POSITIONS_FILE
        pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")

    def write_factor_snapshot(self, df: pd.DataFrame, run_id: str) -> Path:
        target_dir = self.data_dir / FACTOR_RUNS_DIR
        ensure_dirs(target_dir)
        path = target_dir / f"{run_id}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def append_factor_coverage(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        target_dir = self.data_dir / FACTOR_DIAGNOSTICS_DIR
        ensure_dirs(target_dir)
        append_csv(target_dir / FACTOR_COVERAGE_FILE, rows, FACTOR_COVERAGE_COLUMNS)

    def append_forward_ic(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        target_dir = self.data_dir / FACTOR_DIAGNOSTICS_DIR
        ensure_dirs(target_dir)
        append_csv(target_dir / FACTOR_FORWARD_IC_FILE, rows, FORWARD_IC_COLUMNS)

    def read_factor_coverage(self) -> pd.DataFrame:
        path = self.data_dir / FACTOR_DIAGNOSTICS_DIR / FACTOR_COVERAGE_FILE
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame(columns=FACTOR_COVERAGE_COLUMNS)
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=FACTOR_COVERAGE_COLUMNS)

    def read_forward_ic(self) -> pd.DataFrame:
        path = self.data_dir / FACTOR_DIAGNOSTICS_DIR / FACTOR_FORWARD_IC_FILE
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame(columns=FORWARD_IC_COLUMNS)
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame(columns=FORWARD_IC_COLUMNS)

    def list_factor_runs(self) -> list[Path]:
        target_dir = self.data_dir / FACTOR_RUNS_DIR
        if not target_dir.exists():
            return []
        return sorted(target_dir.glob("*.csv"))

    def read_factor_run(self, run_id: str) -> pd.DataFrame:
        path = self.data_dir / FACTOR_RUNS_DIR / f"{run_id}.csv"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, dtype={"code": str})

    def read_nav(self) -> pd.DataFrame:
        path = self.data_dir / DAILY_NAV_FILE
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path)

    def read_trades(self) -> pd.DataFrame:
        path = self.data_dir / TRADES_FILE
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, dtype={"code": str})

    def read_positions(self) -> pd.DataFrame:
        path = self.data_dir / POSITIONS_FILE
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, dtype={"code": str})
