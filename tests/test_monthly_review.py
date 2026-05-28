from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from stock_analyze.monthly_review import (
    compute_review,
    default_month_for,
    write_review,
)


BASELINE_CONFIG = {
    "competition_id": "test_competition",
    "start_date": "2026-05-26",
    "initial_cash": 1000000,
    "accounts": [
        {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500000, "top_n": 10},
        {"id": "zz500", "scope": "zz500", "benchmark": "000905", "cash": 500000, "top_n": 10},
    ],
    "schedule": {"rebalance": "weekly_after_close", "signal_day": "last_trading_day_of_week", "execution": "next_trading_day_open"},
    "trading": {"lot_size": 100, "commission_rate": 0.0003, "min_commission": 5, "stamp_tax_rate": 0.0005, "slippage_rate": 0.0005, "max_single_weight": 0.10},
    "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
}


def _seed_repo(tmp: Path) -> None:
    (tmp / "configs" / "agents").mkdir(parents=True, exist_ok=True)
    (tmp / "configs" / "competition_a_share.yaml").write_text(json.dumps(BASELINE_CONFIG), encoding="utf-8")
    for agent_id, factors in (
        ("claude", {"pe": {"weight": 0.5, "direction": "low"}, "roe": {"weight": 0.5, "direction": "high"}}),
        ("codex", {"roe": {"weight": 0.5, "direction": "high"}, "low_volatility_60": {"weight": 0.5, "direction": "low"}}),
    ):
        path = tmp / "configs" / "agents" / f"{agent_id}_a_share.yaml"
        payload = {"agent_id": agent_id, "strategy_id": f"{agent_id}_v1", "factors": factors}
        path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_agent(tmp: Path, agent: str, *, cumulative: float, ir: float, daily_returns: list[tuple[str, float]], positions: list[str], factor_ic: list[tuple[str, str, float]]) -> None:
    data_dir = tmp / "data" / "a_share" / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    (tmp / "reports" / "a_share" / agent).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "shared").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "competition").mkdir(parents=True, exist_ok=True)
    (tmp / "reports" / "competition").mkdir(parents=True, exist_ok=True)

    perf_summary = {
        "strategy_id": f"{agent}_v1",
        "generated_at": "2026-06-01",
        "config_hash": f"{agent}hash",
        "accounts": {
            "hs300": {
                "cumulative_return": cumulative,
                "annualized_return": cumulative * 12,
                "annualized_volatility": 0.18,
                "sharpe_ratio": 1.2,
                "sortino_ratio": 1.4,
                "max_drawdown": -0.05,
                "information_ratio": ir,
                "tracking_error": 0.06,
                "weekly_turnover_avg": 0.2,
                "cost_bps": 20.0,
                "round_trip_win_rate": 0.55,
                "nav_points": 30,
            }
        },
    }
    (data_dir / "performance_summary.json").write_text(json.dumps(perf_summary), encoding="utf-8")

    nav_rows = []
    cumulative_value = 1_000_000.0
    for date_str, daily in daily_returns:
        cumulative_value *= 1 + daily
        nav_rows.append({"date": date_str, "account_id": "hs300", "total_value": cumulative_value, "benchmark_close": 100.0, "benchmark_date": date_str, "cash": cumulative_value, "market_value": 0, "notes": ""})
    pd.DataFrame(nav_rows).to_csv(data_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")

    positions_rows = [{"account_id": "hs300", "code": code, "name": code, "industry": "金融", "shares": 100, "market_value": 1000.0, "avg_cost": 10.0, "last_buy_date": "2026-05-01", "hold_since": "2026-05-01", "last_price": 10.0, "unrealized_pnl": 0.0, "score": 1.0, "reason": "", "updated_at": "2026-05-31"} for code in positions]
    pd.DataFrame(positions_rows).to_csv(data_dir / "positions.csv", index=False, encoding="utf-8-sig")

    diag_dir = data_dir / "factor_diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    ic_rows = [{"signal_date": signal_date, "account_id": "hs300", "factor": factor, "ic": value, "sample_size": 50, "ic_status": "ok", "computed_at": "2026-06-01"} for signal_date, factor, value in factor_ic]
    pd.DataFrame(ic_rows).to_csv(diag_dir / "forward_ic.csv", index=False, encoding="utf-8-sig")


def _daily_series(month: str, base_return: float, length: int = 18) -> list[tuple[str, float]]:
    out = []
    start = pd.to_datetime(f"{month}-01")
    for i in range(length):
        out.append(((start + pd.Timedelta(days=i)).date().isoformat(), base_return + 0.0005 * i))
    return out


class MonthlyReviewTests(unittest.TestCase):
    def test_default_month_returns_previous_calendar_month(self) -> None:
        self.assertEqual(default_month_for(date(2026, 6, 3)), "2026-05")
        self.assertEqual(default_month_for(date(2026, 1, 5)), "2025-12")

    def test_review_block_has_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(
                tmp_path, "claude",
                cumulative=0.08,
                ir=0.9,
                daily_returns=_daily_series("2026-05", 0.001),
                positions=["000001", "000002", "000003", "000004"],
                factor_ic=[("2026-05-08", "pe", 0.12), ("2026-05-15", "roe", 0.10)],
            )
            _seed_agent(
                tmp_path, "codex",
                cumulative=0.05,
                ir=1.1,
                daily_returns=_daily_series("2026-05", 0.0008),
                positions=["000003", "000004", "000005", "000006"],
                factor_ic=[("2026-05-08", "roe", 0.09), ("2026-05-15", "low_volatility_60", 0.07)],
            )
            payload = compute_review("2026-05", ["claude", "codex"], repo_root=tmp_path)
            self.assertEqual(payload["review_period"], "2026-05")
            self.assertIn("claude", payload["agents"])
            self.assertIn("codex", payload["agents"])
            block = payload["agents"]["claude"]
            for key in [
                "cumulative_return",
                "annualized_return",
                "sharpe_ratio",
                "information_ratio",
                "tracking_error",
                "weekly_turnover_avg",
                "cost_bps",
                "round_trip_win_rate",
                "factor_ic_top3",
                "industry_exposure_top3",
                "active_factors",
                "config_hash",
            ]:
                self.assertIn(key, block, msg=f"missing {key}")

    def test_winner_and_spread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(tmp_path, "claude", cumulative=0.08, ir=0.9, daily_returns=_daily_series("2026-05", 0.001), positions=["000001", "000002"], factor_ic=[("2026-05-08", "pe", 0.12)])
            _seed_agent(tmp_path, "codex", cumulative=0.05, ir=1.1, daily_returns=_daily_series("2026-05", 0.0008), positions=["000002", "000003"], factor_ic=[("2026-05-08", "roe", 0.09)])
            payload = compute_review("2026-05", ["claude", "codex"], repo_root=tmp_path)
            comp = payload["comparison"]
            self.assertEqual(comp["winner_cumulative_return"], "claude")
            self.assertEqual(comp["winner_information_ratio"], "codex")
            self.assertAlmostEqual(comp["spread_cumulative_return"], 0.03, places=3)

    def test_overlap_is_jaccard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(tmp_path, "claude", cumulative=0.05, ir=0.5, daily_returns=_daily_series("2026-05", 0.001), positions=["000001", "000002", "000003", "000004"], factor_ic=[])
            _seed_agent(tmp_path, "codex", cumulative=0.05, ir=0.5, daily_returns=_daily_series("2026-05", 0.001), positions=["000003", "000004", "000005", "000006"], factor_ic=[])
            payload = compute_review("2026-05", ["claude", "codex"], repo_root=tmp_path)
            self.assertAlmostEqual(payload["comparison"]["position_overlap_ratio"], 2 / 6, places=4)

    def test_leaderboard_upsert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(tmp_path, "claude", cumulative=0.08, ir=0.9, daily_returns=_daily_series("2026-05", 0.001), positions=["000001"], factor_ic=[])
            _seed_agent(tmp_path, "codex", cumulative=0.05, ir=1.1, daily_returns=_daily_series("2026-05", 0.0008), positions=["000002"], factor_ic=[])
            payload = compute_review("2026-05", ["claude", "codex"], repo_root=tmp_path)
            json_path, md_path, leaderboard_path = write_review(payload, repo_root=tmp_path)
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertTrue(leaderboard_path.exists())

            # Re-write with updated numbers: leaderboard should still have one row for 2026-05.
            _seed_agent(tmp_path, "claude", cumulative=0.10, ir=1.0, daily_returns=_daily_series("2026-05", 0.001), positions=["000001"], factor_ic=[])
            payload2 = compute_review("2026-05", ["claude", "codex"], repo_root=tmp_path)
            write_review(payload2, repo_root=tmp_path)
            df = pd.read_csv(leaderboard_path)
            self.assertEqual(len(df), 1)
            self.assertAlmostEqual(float(df.iloc[0]["claude_return"]), 0.10)

    def test_markdown_contains_disclaimer_and_winner_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _seed_repo(tmp_path)
            _seed_agent(tmp_path, "claude", cumulative=0.08, ir=0.9, daily_returns=_daily_series("2026-05", 0.001), positions=["000001"], factor_ic=[])
            _seed_agent(tmp_path, "codex", cumulative=0.05, ir=1.1, daily_returns=_daily_series("2026-05", 0.0008), positions=["000002"], factor_ic=[])
            payload = compute_review("2026-05", ["claude", "codex"], repo_root=tmp_path)
            payload["competition_id"] = "test_competition"
            _, md_path, _ = write_review(payload, repo_root=tmp_path)
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("不构成投资建议", content)
            self.assertIn("累计收益", content)
            self.assertIn("claude", content)
            self.assertIn("codex", content)


if __name__ == "__main__":
    unittest.main()
