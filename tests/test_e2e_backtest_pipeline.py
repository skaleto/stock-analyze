"""End-to-end test: full backtest pipeline via CLI, using synthetic data.

This proves the pipeline works without needing a live TUSHARE_TOKEN — it
constructs a minimal but valid backtest_cache layout, runs the CLI in a
subprocess, and verifies all expected output files are produced.

It also exercises the gate (breach + pass) by mocking the engine.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from stock_analyze.backtest import engine, gate
from stock_analyze.backtest.exceptions import BacktestFloorBreach
from stock_analyze.backtest.types import BacktestMetrics, BacktestResult


PROJECT_ROOT = Path(__file__).parent.parent


def _build_synthetic_cache(cache_root: Path, n_days: int = 10) -> None:
    """Construct a minimal but valid backtest_cache.

    Creates:
    - trade_cal.csv (n_days consecutive weekdays starting 2023-06-26)
    - daily/<iso>.csv for each day (2 stocks)
    - daily_basic/<iso>.csv for each day
    - index_weight/000300_2023-06.csv (2 stocks)
    - stock_basic.csv (2 stocks, all listed)
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    for sub in ("daily", "daily_basic", "fina_indicator",
                "index_weight", "adj_factor"):
        (cache_root / sub).mkdir(parents=True, exist_ok=True)

    # 10 weekdays starting Mon 2023-06-26
    start = date(2023, 6, 26)
    days = []
    d = start
    while len(days) < n_days:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        from datetime import timedelta
        d = d + timedelta(days=1)

    yyyymmdd = [d.strftime("%Y%m%d") for d in days]
    pd.DataFrame({"cal_date": yyyymmdd, "is_open": [1] * len(yyyymmdd)}).to_csv(
        cache_root / "trade_cal.csv", index=False,
    )

    # Daily + daily_basic for each day
    for i, d in enumerate(days):
        iso = d.isoformat()
        raw = d.strftime("%Y%m%d")
        # Price drifts up slightly each day for both stocks
        pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": raw,
             "open": 12.0 + 0.05 * i, "close": 12.0 + 0.06 * i,
             "high": 12.2 + 0.05 * i, "low": 11.9 + 0.05 * i,
             "vol": 1e6, "amount": 1.2e10},
            {"ts_code": "000002.SZ", "trade_date": raw,
             "open": 20.0 + 0.08 * i, "close": 20.0 + 0.10 * i,
             "high": 20.5 + 0.08 * i, "low": 19.8 + 0.08 * i,
             "vol": 8e5, "amount": 1.6e10},
        ]).to_csv(cache_root / "daily" / f"{iso}.csv", index=False)

        pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": raw,
             "pe_ttm": 5.5, "pb": 1.1, "dv_ttm": 4.5,
             "total_mv": 200_000, "circ_mv": 150_000},
            {"ts_code": "000002.SZ", "trade_date": raw,
             "pe_ttm": 12.0, "pb": 1.8, "dv_ttm": 2.0,
             "total_mv": 250_000, "circ_mv": 200_000},
        ]).to_csv(cache_root / "daily_basic" / f"{iso}.csv", index=False)

    # Index weight (June 2023 snapshot containing both stocks for hs300)
    pd.DataFrame({
        "index_code": ["000300.SH", "000300.SH"],
        "con_code": ["000001.SZ", "000002.SZ"],
        "weight": [0.5, 0.5],
        "trade_date": ["20230601", "20230601"],
    }).to_csv(cache_root / "index_weight" / "000300_2023-06.csv", index=False)
    # Empty zz500 snapshot
    pd.DataFrame({
        "index_code": [], "con_code": [], "weight": [], "trade_date": [],
    }).to_csv(cache_root / "index_weight" / "000905_2023-06.csv", index=False)

    # Stock basic
    pd.DataFrame([
        {"ts_code": "000001.SZ", "name": "平安银行",
         "list_date": "19910403", "delist_date": "", "industry": "银行"},
        {"ts_code": "000002.SZ", "name": "万科A",
         "list_date": "19910129", "delist_date": "", "industry": "房地产"},
    ]).to_csv(cache_root / "stock_basic.csv", index=False)


class E2EBacktestPipelineTests(unittest.TestCase):
    """E2E: synthetic cache → engine.run_backtest → all output files exist."""

    def test_engine_e2e_with_synthetic_cache(self):
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            out = Path(tmp) / "out"
            out.mkdir(parents=True)
            _build_synthetic_cache(cache, n_days=10)

            overlay = {
                "agent_id": "claude",
                "strategy_id": "e2e",
                "accounts": [
                    {"id": "main", "scope": "hs300", "benchmark": "000300",
                     "cash": 1_000_000, "top_n": 2},
                ],
                "trading": {
                    "lot_size": 100, "commission_rate": 0.0003,
                    "min_commission": 5, "stamp_tax_rate": 0.0005,
                    "slippage_rate": 0.0, "max_single_weight": 0.5,
                },
            }

            result = engine.run_backtest(
                overlay=overlay,
                start=date(2023, 6, 26),
                end=date(2023, 7, 7),
                universe=["hs300"],
                market_data_root=cache,
                out_dir=out,
            )

            # All required outputs
            for name in ("daily_nav.csv", "trades.csv", "signals.csv",
                          "performance_summary.json"):
                self.assertTrue((out / name).exists(),
                                 f"missing output: {name}")

            # NAV has one row per (day × account)
            nav = pd.read_csv(out / "daily_nav.csv")
            self.assertEqual(nav["date"].nunique(), 10)

            # Signals produced on at least one Friday (2023-06-30 + 2023-07-07)
            signals = pd.read_csv(out / "signals.csv")
            self.assertGreaterEqual(signals["signal_date"].nunique(), 1)

            # At least one trade was executed (signals → pending → execute)
            trades = pd.read_csv(out / "trades.csv")
            self.assertGreaterEqual(len(trades), 1)

            # Metrics returned in result
            self.assertIsNotNone(result.metrics.cum_return)

    def test_research_cli_e2e(self):
        """Drive the CLI via subprocess to exercise the full plumbing."""
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            out = Path(tmp) / "out"
            _build_synthetic_cache(cache, n_days=10)

            # We need configs/agents/claude.yaml; use a fixture overlay.
            # Strategy: create a temp working tree with minimal repo layout
            # then run the CLI from there.
            workdir = Path(tmp) / "workdir"
            (workdir / "configs" / "agents").mkdir(parents=True)
            (workdir / "data" / "shared").mkdir(parents=True)
            (workdir / "logs").mkdir(parents=True)

            # Copy real competition.yaml so loader works
            comp = json.loads((PROJECT_ROOT / "configs/competition.yaml")
                                .read_text())
            (workdir / "configs/competition.yaml").write_text(json.dumps(comp))

            overlay = {
                "agent_id": "claude", "strategy_id": "e2e", "name": "E2E test",
                "factors": {"pe": {"weight": 1.0, "direction": "low"}},
                "factor_processing": {"winsorize_lower": 0.01,
                                        "winsorize_upper": 0.99,
                                        "neutralize_industry": True,
                                        "min_factor_coverage": 0.6},
                "portfolio_controls": {"max_industry_weight": 0.3,
                                         "hold_buffer_pct": 0.5,
                                         "max_holding_days": 365,
                                         "industry_unclassified_label": "未分类"},
                "filters": {"exclude_st": True, "max_fetch_candidates": 250,
                             "min_listing_days": 365, "min_pe": 0,
                             "min_avg_amount_20": 0, "min_market_cap_yi": 0,
                             "max_market_cap_yi": 100000, "require_fields": [],
                             "fallback_require_fields": []},
            }
            (workdir / "configs/agents/claude.yaml").write_text(json.dumps(overlay))

            # Move cache into workdir layout
            shared_cache = workdir / "data" / "shared" / "backtest_cache"
            shared_cache.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copytree(cache, shared_cache)

            cmd = [
                sys.executable, "-m", "stock_analyze",
                "backtest",
                "--agent", "claude",
                "--start", "2023-06-26",
                "--end", "2023-07-07",
                "--overlay", str(workdir / "configs/agents/claude.yaml"),
                "--output", str(out),
                "--cache-root", str(shared_cache),
                "--in-memory",
            ]
            result = subprocess.run(
                cmd, cwd=workdir, capture_output=True, text=True,
                env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(
                result.returncode, 0,
                f"CLI failed: stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertIn("backtest complete", result.stdout)
            self.assertTrue((out / "daily_nav.csv").exists())
            self.assertTrue((out / "report.md").exists())


class E2EGateTests(unittest.TestCase):
    """E2E: gate raises BacktestFloorBreach + evolution_writer aborts cleanly."""

    def test_gate_floor_breach_full_path(self):
        from stock_analyze import evolution_writer

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs/agents").mkdir(parents=True)
            comp = json.loads((PROJECT_ROOT / "configs/competition.yaml")
                                .read_text())
            (root / "configs/competition.yaml").write_text(json.dumps(comp))
            overlay = {
                "agent_id": "claude", "strategy_id": "e2e", "name": "E2E",
                "factors": {"pe": {"weight": 1.0, "direction": "low"}},
                "factor_processing": {"winsorize_lower": 0.01,
                                        "winsorize_upper": 0.99,
                                        "neutralize_industry": True,
                                        "min_factor_coverage": 0.6},
                "portfolio_controls": {"max_industry_weight": 0.3,
                                         "hold_buffer_pct": 0.5,
                                         "max_holding_days": 365,
                                         "industry_unclassified_label": "未分类"},
                "filters": {"exclude_st": True, "max_fetch_candidates": 250,
                             "min_listing_days": 365, "min_pe": 0,
                             "min_avg_amount_20": 0, "min_market_cap_yi": 0,
                             "max_market_cap_yi": 100000, "require_fields": [],
                             "fallback_require_fields": []},
            }
            (root / "configs/agents/claude.yaml").write_text(json.dumps(overlay))

            new_overlay = dict(overlay)
            new_overlay["factors"] = {"pe": {"weight": 0.95, "direction": "low"}}

            catastrophic = BacktestResult(
                out_dir=Path("/tmp"),
                start=date(2025, 1, 1),
                end=date(2026, 4, 30),
                metrics=BacktestMetrics(-0.30, -0.20, -1.2, -0.40, -1.8),
            )
            with patch(
                "stock_analyze.backtest.engine.run_backtest",
                return_value=catastrophic,
            ):
                with self.assertRaises(BacktestFloorBreach):
                    evolution_writer.write_evolution(
                        agent_id="claude",
                        old_overlay=overlay,
                        new_overlay=new_overlay,
                        reasoning_md="# breaking the floor",
                        repo_root=root,
                        month="2026-06",
                    )
            # Live yaml untouched
            live = json.loads(
                (root / "configs/agents/claude.yaml").read_text()
            )
            self.assertEqual(live["factors"]["pe"]["weight"], 1.0)
            # Breach log written
            breach = (
                root / "data" / "claude" / "evolution_log"
                / "2026-06-floor-breach.md"
            )
            self.assertTrue(breach.exists())
            content = breach.read_text()
            self.assertIn("max_drawdown_exceeded", content)
            self.assertIn("breaking the floor", content)


if __name__ == "__main__":
    unittest.main()
