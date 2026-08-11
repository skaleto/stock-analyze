"""Tests for :mod:`stock_analyze.notifier`.

Network calls are mocked. Date-sensitive logic uses explicit ``today_d=``
overrides so tests don't drift on Saturday/Sunday/end-of-month.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze.notifier import (
    LarkAPIError,
    LarkCredentials,
    _months_first_day,
    _most_recent_friday_on_or_before,
    build_daily_summary,
    build_daily_summary_card,
    collect_pending_actions,
)


def _seed_agent_dir(repo_root: Path, agent: str) -> Path:
    config_dir = repo_root / "configs" / "agents"
    config_dir.mkdir(parents=True, exist_ok=True)
    registry_path = repo_root / "configs" / "strategy_competition.json"
    if not registry_path.exists():
        registry_path.write_text(
            json.dumps(
                {
                    "season_id": "s1",
                    "name": "双策略对抗",
                    "effective_date": "2026-07-11",
                    "factor_distance_floor": 0.45,
                    "slots": {
                        "claude": {"label": "稳健防守", "description": "", "color": "#d6a84b"},
                        "codex": {"label": "趋势进攻", "description": "", "color": "#22d3ee"},
                    },
                }
            ),
            encoding="utf-8",
        )
    overlay = config_dir / f"{agent}_a_share.yaml"
    if not overlay.exists():
        overlay.write_text(
            json.dumps(
                {
                    "agent_id": agent,
                    "strategy_id": f"{agent}_v1",
                    "name": "稳健防守" if agent == "claude" else "趋势进攻",
                    "factors": {"pe": {"weight": 1.0, "direction": "low"}},
                }
            ),
            encoding="utf-8",
        )
    data_dir = repo_root / "data" / "a_share" / agent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _enable_sentiment(repo_root: Path, agent: str) -> None:
    overlay = repo_root / "configs" / "agents" / f"{agent}_a_share.yaml"
    payload = json.loads(overlay.read_text(encoding="utf-8"))
    payload["factors"] = {
        f"{agent}_market_sentiment_1w": {"weight": 1.0, "direction": "high"}
    }
    overlay.write_text(json.dumps(payload), encoding="utf-8")


def _write_nav(data_dir: Path, rows: list[tuple[str, str, float]]) -> None:
    """rows: list of (date, account_id, total_value)."""
    lines = ["date,account_id,cash,positions_value,total_value,benchmark_code,benchmark_value,benchmark_date,source"]
    for d, acct, total in rows:
        lines.append(f"{d},{acct},{total * 0.3:.2f},{total * 0.7:.2f},{total:.2f},000300,4000.0,{d},daily")
    (data_dir / "daily_nav.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_positions(data_dir: Path, by_account: dict[str, int]) -> None:
    """Seed positions.csv with N rows per account_id."""
    rows = ["account_id,code,name,industry,shares,available_shares,avg_cost,last_buy_date,hold_since,last_price,market_value,unrealized_pnl,score,reason,updated_at"]
    for acct, n in by_account.items():
        for i in range(n):
            code = f"{600000 + i:06d}"
            rows.append(f"{acct},{code},X,Y,100,0,10.0,2026-05-25,2026-05-25,10.0,1000.0,0.0,0.5,r,2026-05-26T17:34")
    (data_dir / "positions.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


class LarkCredentialsTests(unittest.TestCase):
    def test_from_env_returns_none_when_any_missing(self):
        self.assertIsNone(LarkCredentials.from_env({}))
        self.assertIsNone(
            LarkCredentials.from_env({"SA_LARK_APP_ID": "x"})
        )
        self.assertIsNone(
            LarkCredentials.from_env(
                {"SA_LARK_APP_ID": "x", "SA_LARK_APP_SECRET": "y"}
            )
        )

    def test_from_env_strips_whitespace(self):
        creds = LarkCredentials.from_env(
            {
                "SA_LARK_APP_ID": "  app123  ",
                "SA_LARK_APP_SECRET": "secret456",
                "SA_LARK_USER_OPEN_ID": " ou_abc ",
            }
        )
        assert creds is not None
        self.assertEqual(creds.app_id, "app123")
        self.assertEqual(creds.user_open_id, "ou_abc")

    def test_from_env_returns_none_when_all_empty_strings(self):
        self.assertIsNone(
            LarkCredentials.from_env(
                {
                    "SA_LARK_APP_ID": "",
                    "SA_LARK_APP_SECRET": "",
                    "SA_LARK_USER_OPEN_ID": "",
                }
            )
        )


class DateHelperTests(unittest.TestCase):
    def test_most_recent_friday_on_friday_returns_self(self):
        # 2026-05-22 is a Friday
        self.assertEqual(
            _most_recent_friday_on_or_before(date(2026, 5, 22)),
            date(2026, 5, 22),
        )

    def test_most_recent_friday_on_monday(self):
        # 2026-05-25 is a Monday; previous Fri is 2026-05-22
        self.assertEqual(
            _most_recent_friday_on_or_before(date(2026, 5, 25)),
            date(2026, 5, 22),
        )

    def test_months_first_day_next_month(self):
        self.assertEqual(_months_first_day(date(2026, 5, 27), offset=1), date(2026, 6, 1))

    def test_months_first_day_crosses_year(self):
        self.assertEqual(_months_first_day(date(2026, 12, 15), offset=1), date(2027, 1, 1))


class PendingActionsTests(unittest.TestCase):
    def test_weekday_no_pending_action(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            # 2026-05-27 is Wednesday
            actions = collect_pending_actions(["claude"], root, date(2026, 5, 27))
            self.assertEqual(actions, [])

    def test_saturday_with_missing_sentiment_flags_action(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            _enable_sentiment(root, "claude")
            alt_dir = root / "data" / "a_share" / "claude" / "alt_factors"
            alt_dir.mkdir(parents=True)
            # Sentiment exists but doesn't cover this Friday (2026-05-22)
            (alt_dir / "market_sentiment.csv").write_text(
                "week_end,score,confidence\n2026-05-15,0.1,0.7\n",
                encoding="utf-8",
            )
            actions = collect_pending_actions(["claude"], root, date(2026, 5, 23))  # Saturday
            self.assertTrue(any("sentiment" in a for a in actions), msg=actions)
            self.assertTrue(any("2026-05-22" in a for a in actions), msg=actions)

    def test_saturday_with_recorded_sentiment_no_action(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            _enable_sentiment(root, "claude")
            alt_dir = root / "data" / "a_share" / "claude" / "alt_factors"
            alt_dir.mkdir(parents=True)
            (alt_dir / "market_sentiment.csv").write_text(
                "week_end,score,confidence\n2026-05-22,0.1,0.7\n",
                encoding="utf-8",
            )
            actions = collect_pending_actions(["claude"], root, date(2026, 5, 23))
            self.assertEqual(actions, [])

    def test_saturday_without_active_sentiment_factor_has_no_reminder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")

            actions = collect_pending_actions(["claude"], root, date(2026, 5, 23))

            self.assertEqual(actions, [])

    def test_three_days_before_month_start_flags_monthly_review(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            # 2026-05-29 is Fri; next-1st = 2026-06-01 (Mon) → 3 days away
            actions = collect_pending_actions(["claude"], root, date(2026, 5, 29))
            self.assertTrue(
                any("monthly-review" in a for a in actions), msg=actions
            )

    def test_far_from_month_start_no_review_reminder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            actions = collect_pending_actions(["claude"], root, date(2026, 5, 15))
            self.assertEqual(actions, [])


class BuildDailySummaryTests(unittest.TestCase):
    def test_empty_repo_produces_section_skeleton(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            _seed_agent_dir(root, "codex")
            text = build_daily_summary(
                ["claude", "codex"], repo_root=root, today_d=date(2026, 5, 27)
            )
            self.assertIn("Stock-Analyze 日报", text)
            self.assertIn("2026-05-27", text)
            self.assertIn("周三", text)
            self.assertIn("💰 NAV", text)
            self.assertIn("📈 持仓", text)
            self.assertIn("✅ Sanity-check", text)

    def test_seeded_data_renders_correctly(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cd = _seed_agent_dir(root, "claude")
            _write_nav(
                cd,
                [
                    ("2026-05-25", "hs300", 500_000),
                    ("2026-05-25", "zz500", 505_000),
                    ("2026-05-26", "hs300", 499_608),
                    ("2026-05-26", "zz500", 502_812),
                ],
            )
            _write_positions(cd, {"hs300": 46, "zz500": 47})
            text = build_daily_summary(
                ["claude"], repo_root=root, today_d=date(2026, 5, 27)
            )
            # NAV: 5/26 total = 499608 + 502812 = 1002420 → close to ¥1M
            self.assertIn("稳健防守", text)
            self.assertNotIn("claude", text)
            # Position breakdown
            self.assertIn("hs300=46", text)
            self.assertIn("zz500=47", text)
            # Total under 100 → warning emoji
            self.assertIn("(=93/100)", text)
            self.assertIn("⚠️", text)

    def test_full_holdings_show_check_emoji(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cd = _seed_agent_dir(root, "claude")
            _write_positions(cd, {"hs300": 50, "zz500": 50})
            text = build_daily_summary(
                ["claude"], repo_root=root, today_d=date(2026, 5, 27)
            )
            self.assertIn("(=100/100)", text)
            self.assertIn("✓", text)

    def test_pending_actions_section_omitted_on_quiet_weekdays(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            # Wednesday far from month-end → no actions → no section
            text_wed = build_daily_summary(
                ["claude"], repo_root=root, today_d=date(2026, 5, 13)
            )
            self.assertNotIn("⏰ 待办", text_wed)

    def test_pending_actions_section_shows_on_saturday_with_missing_sentiment(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            _enable_sentiment(root, "claude")
            # Saturday + no sentiment CSV at all → cold-start prompt fires
            text_sat = build_daily_summary(
                ["claude"], repo_root=root, today_d=date(2026, 5, 23)
            )
            self.assertIn("⏰ 待办", text_sat)
            self.assertIn("sentiment", text_sat)


class SendLarkDMTests(unittest.TestCase):
    """Mock-based tests for the network path. Real Lark calls are not made."""

    def test_send_lark_dm_raises_on_non_zero_token_code(self):
        from stock_analyze import notifier

        with patch.object(
            notifier,
            "_http_post_json",
            return_value={"code": 99991661, "msg": "app secret invalid"},
        ):
            creds = LarkCredentials(app_id="a", app_secret="b", user_open_id="c")
            with self.assertRaises(LarkAPIError) as ctx:
                notifier.send_lark_dm("hello", creds)
            self.assertIn("app secret invalid", str(ctx.exception))

    def test_send_lark_dm_raises_on_non_zero_message_code(self):
        from stock_analyze import notifier

        # First call returns token OK; second call returns send error
        responses = iter(
            [
                {"code": 0, "tenant_access_token": "t_xxx", "expire": 7200},
                {"code": 230006, "msg": "user open_id not exist"},
            ]
        )
        with patch.object(
            notifier, "_http_post_json", side_effect=lambda *a, **k: next(responses)
        ):
            creds = LarkCredentials(app_id="a", app_secret="b", user_open_id="c")
            with self.assertRaises(LarkAPIError) as ctx:
                notifier.send_lark_dm("hello", creds)
            self.assertIn("user open_id not exist", str(ctx.exception))

    def test_send_lark_dm_success(self):
        from stock_analyze import notifier

        responses = iter(
            [
                {"code": 0, "tenant_access_token": "t_xxx"},
                {"code": 0, "data": {"message_id": "om_abc"}},
            ]
        )
        with patch.object(
            notifier, "_http_post_json", side_effect=lambda *a, **k: next(responses)
        ):
            creds = LarkCredentials(app_id="a", app_secret="b", user_open_id="c")
            resp = notifier.send_lark_dm("hello", creds)
            self.assertEqual(resp.get("code"), 0)

    def test_send_lark_dm_payload_uses_open_id_receive_type(self):
        """Verify we POST to the correct URL with the right receive_id_type."""
        from stock_analyze import notifier

        captured: list[tuple[str, dict]] = []

        def fake_post(url, payload, **kwargs):
            captured.append((url, payload))
            if "auth/v3/tenant_access_token" in url:
                return {"code": 0, "tenant_access_token": "t_x"}
            return {"code": 0}

        with patch.object(notifier, "_http_post_json", side_effect=fake_post):
            creds = LarkCredentials(app_id="a", app_secret="b", user_open_id="ou_X")
            notifier.send_lark_dm("hi", creds)

        # Second call is the message send
        send_url, send_payload = captured[1]
        self.assertIn("im/v1/messages?receive_id_type=open_id", send_url)
        self.assertEqual(send_payload["receive_id"], "ou_X")
        self.assertEqual(send_payload["msg_type"], "text")
        # content is a JSON-encoded string per Lark API contract
        self.assertEqual(json.loads(send_payload["content"]), {"text": "hi"})


class SendLarkCardTests(unittest.TestCase):
    """Mock-based tests for the interactive-card send path."""

    def test_send_lark_card_payload_uses_interactive(self):
        from stock_analyze import notifier

        captured: list[tuple[str, dict]] = []

        def fake_post(url, payload, **kwargs):
            captured.append((url, payload))
            if "auth/v3/tenant_access_token" in url:
                return {"code": 0, "tenant_access_token": "t_x"}
            return {"code": 0}

        with patch.object(notifier, "_http_post_json", side_effect=fake_post):
            creds = LarkCredentials(app_id="a", app_secret="b", user_open_id="ou_X")
            card = {"header": {"template": "green"}, "elements": []}
            notifier.send_lark_card(card, creds)

        # Second call is the message send (first is the token fetch).
        send_url, send_payload = captured[1]
        self.assertIn("im/v1/messages?receive_id_type=open_id", send_url)
        self.assertEqual(send_payload["receive_id"], "ou_X")
        self.assertEqual(send_payload["msg_type"], "interactive")
        # content is the card JSON encoded as a string, per Lark API contract.
        self.assertEqual(json.loads(send_payload["content"]), card)

    def test_send_lark_card_raises_on_non_zero_code(self):
        from stock_analyze import notifier

        responses = iter(
            [
                {"code": 0, "tenant_access_token": "t_x"},
                {"code": 230006, "msg": "invalid card json"},
            ]
        )
        with patch.object(
            notifier, "_http_post_json", side_effect=lambda *a, **k: next(responses)
        ):
            creds = LarkCredentials(app_id="a", app_secret="b", user_open_id="c")
            with self.assertRaises(LarkAPIError) as ctx:
                notifier.send_lark_card({"elements": []}, creds)
            self.assertIn("invalid card json", str(ctx.exception))


class BuildDailySummaryCardTests(unittest.TestCase):
    def test_card_structure_and_header(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cd = _seed_agent_dir(root, "claude")
            _seed_agent_dir(root, "codex")
            _write_nav(
                cd,
                [
                    ("2026-05-25", "hs300", 500_000),
                    ("2026-05-26", "hs300", 500_500),
                ],
            )
            _write_positions(cd, {"hs300": 50, "zz500": 50})
            card = build_daily_summary_card(
                ["claude", "codex"], repo_root=root, today_d=date(2026, 5, 27)
            )
            # Top-level interactive-card shape.
            self.assertIn("header", card)
            self.assertIsInstance(card["elements"], list)
            self.assertTrue(card["elements"])
            # Colored header template + date in the title.
            self.assertIn(card["header"]["template"], {"green", "orange", "red"})
            self.assertIn("2026-05-27", card["header"]["title"]["content"])
            # NAV + 持仓 field labels appear in the serialized card.
            blob = json.dumps(card, ensure_ascii=False)
            self.assertIn("NAV", blob)
            self.assertIn("持仓", blob)

    def test_card_is_json_serializable(self):
        # send_lark_card json-encodes the card; a non-serializable card would
        # only blow up at send time, so guard it here.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_agent_dir(root, "claude")
            card = build_daily_summary_card(
                ["claude"], repo_root=root, today_d=date(2026, 5, 27)
            )
            json.dumps(card, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
