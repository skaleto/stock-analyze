from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze.notifier import LarkCredentials
from stock_analyze.workflow_notifications import (
    build_workflow_summary,
    build_workflow_summary_card,
    cli_send_workflow_summary,
)


AGENTS = ("claude", "codex")
MARKETS = ("a_share", "cn_qdii_etf")


def _seed_registry(root: Path) -> None:
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "strategy_competition.json").write_text(
        json.dumps(
            {
                "season_id": "s1",
                "name": "dual strategy",
                "effective_date": "2026-07-11",
                "factor_distance_floor": 0.45,
                "slots": {
                    "claude": {
                        "label": "稳健防守",
                        "description": "",
                        "color": "#d6a84b",
                    },
                    "codex": {
                        "label": "趋势进攻",
                        "description": "",
                        "color": "#22d3ee",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _seed_run(
    root: Path,
    market: str,
    agent: str,
    command: str,
    *,
    as_of: str,
    started_at: str,
    status: str = "success",
) -> None:
    data_dir = root / "data" / market / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{command}-{market}-{agent}"
    (data_dir / "runs.csv").write_text(
        "run_id,command,as_of,started_at,finished_at,duration_ms,status,error_summary,config_hash,code_version\n"
        f"{run_id},{command},{as_of},{started_at},{started_at},1000,{status},,hash,version\n",
        encoding="utf-8",
    )


def _seed_nav(root: Path, market: str, agent: str, value: float) -> None:
    data_dir = root / "data" / market / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "daily_nav.csv").write_text(
        "date,account_id,total_value,benchmark_code,benchmark_date\n"
        f"2026-07-09,main,{value - 1000:.2f},000300,2026-07-09\n"
        f"2026-07-10,main,{value:.2f},000300,2026-07-10\n",
        encoding="utf-8",
    )


def _seed_trades(root: Path, market: str, agent: str, count: int) -> None:
    data_dir = root / "data" / market / agent
    rows = ["trade_date,account_id,code,side,shares,price"]
    rows.extend(
        f"2026-07-13,main,{i:06d},buy,100,10" for i in range(count)
    )
    (data_dir / "trades.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _seed_pending(root: Path, market: str, agent: str, count: int) -> None:
    data_dir = root / "data" / market / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    if market == "a_share":
        payload = [{"account_id": "main", "orders": [{"status": "pending"}] * count}]
    else:
        payload = [{"account_id": "main", "side": "buy"} for _ in range(count)]
    (data_dir / "pending_orders.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class WorkflowSummaryTests(unittest.TestCase):
    def test_daily_summary_is_compact_and_reports_four_pipeline_results(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            for market in MARKETS:
                for agent in AGENTS:
                    _seed_run(
                        root,
                        market,
                        agent,
                        "run-daily",
                        as_of="2026-07-13",
                        started_at="2026-07-13T18:50:00",
                    )
                    _seed_nav(root, market, agent, 1_001_000.0)
                    _seed_trades(root, market, agent, 2)

            text = build_workflow_summary(
                "daily", root, today_d=date(2026, 7, 13), target="2026-07-13"
            )

            self.assertIn("4/4", text)
            self.assertIn("稳健防守", text)
            self.assertIn("趋势进攻", text)
            self.assertIn("成交 8", text)
            self.assertNotIn("持仓明细", text)
            self.assertNotIn("Sanity-check", text)

    def test_weekly_summary_matches_friday_as_of_and_weekend_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            for agent in AGENTS:
                _seed_run(
                    root,
                    "a_share",
                    agent,
                    "run-weekly",
                    as_of="2026-07-10",
                    started_at="2026-07-11T10:00:00",
                )
                _seed_run(
                    root,
                    "cn_qdii_etf",
                    agent,
                    "run-weekly",
                    as_of="",
                    started_at="2026-07-11T10:15:00",
                )
                _seed_pending(root, "a_share", agent, 3)
                _seed_pending(root, "cn_qdii_etf", agent, 2)

            text = build_workflow_summary(
                "weekly", root, today_d=date(2026, 7, 11), target="2026-07-10"
            )

            self.assertIn("4/4", text)
            self.assertIn("待执行订单 10", text)
            self.assertIn("运行 2026-07-10 周度复盘", text)

    def test_weekly_summary_only_adds_material_qdii_research_alerts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            events = root / "data" / "cn_qdii_etf" / "shared" / "fund_events.csv"
            events.parent.mkdir(parents=True)
            events.write_text(
                "event_id,report_id,code,name,category,title,published_at,observed_at,effective_at,expires_at,event_type,severity,hard_block,clears_temporary_blocks,source_url,raw_content_hash,parser_version\n"
                "AN1,AN1,513100.SH,纳指ETF,1,暂停申购,2026-07-10T00:00:00,2026-07-10T08:00:00,2026-07-10T00:00:00,2026-08-09T00:00:00,suspension,hard,True,False,https://example.test/a,hash,v1\n",
                encoding="utf-8",
            )

            text = build_workflow_summary(
                "weekly", root, today_d=date(2026, 7, 11), target="2026-07-10"
            )

        self.assertIn("研究异常", text)
        self.assertIn("1 只基金存在公告硬阻断", text)
        self.assertIn("影子研究尚未生成", text)

    def test_daily_strategy_total_is_not_computed_from_one_market_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            _seed_nav(root, "a_share", "claude", 1_001_000.0)

            text = build_workflow_summary(
                "daily", root, today_d=date(2026, 7, 13), target="2026-07-13"
            )

            self.assertIn("稳健防守: 净值数据积累中", text)
            self.assertNotIn("稳健防守: ¥1,001,000", text)

    def test_monthly_summary_points_to_previous_month_evolution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            review = root / "data" / "competition" / "monthly_reviews" / "2026-06.json"
            review.parent.mkdir(parents=True, exist_ok=True)
            review.write_text("{}", encoding="utf-8")

            text = build_workflow_summary(
                "monthly", root, today_d=date(2026, 7, 1), target="2026-06"
            )
            card = build_workflow_summary_card(
                "monthly", root, today_d=date(2026, 7, 1), target="2026-06"
            )

            self.assertIn("A股月报已生成", text)
            self.assertIn("运行 2026-06 月度策略演化", text)
            self.assertIn("2026-06", card["header"]["title"]["content"])


class WorkflowNotificationDeliveryTests(unittest.TestCase):
    def test_successful_send_is_deduplicated_by_cadence_and_target(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            creds = LarkCredentials("app", "secret", "user")
            with (
                patch(
                    "stock_analyze.workflow_notifications.LarkCredentials.from_env",
                    return_value=creds,
                ),
                patch(
                    "stock_analyze.workflow_notifications.send_lark_card",
                    return_value={"code": 0},
                ) as send_card,
            ):
                first = cli_send_workflow_summary(
                    "daily", root, target="2026-07-13"
                )
                second = cli_send_workflow_summary(
                    "daily", root, target="2026-07-13"
                )

            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            send_card.assert_called_once()
            ledger = json.loads(
                (root / "data" / "notifications" / "workflow_sent.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("daily:2026-07-13", ledger["sent"])

    def test_preview_without_credentials_does_not_mark_as_sent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_registry(root)
            with patch(
                "stock_analyze.workflow_notifications.LarkCredentials.from_env",
                return_value=None,
            ):
                result = cli_send_workflow_summary(
                    "weekly", root, target="2026-07-10"
                )

            self.assertEqual(result, 0)
            self.assertFalse(
                (root / "data" / "notifications" / "workflow_sent.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
