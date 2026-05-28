"""Tests for :mod:`stock_analyze.sanity_check`.

Each check function is unit-tested with hand-crafted DataFrames so the
test suite stays fast (no I/O, no fixture files). The end-to-end
:func:`check_agent` is also exercised against a freshly seeded
``PortfolioStore`` in a temp dir so we cover the disk-read path too.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.sanity_check import (
    Anomaly,
    check_agent,
    check_benchmark_code_dtype,
    check_forward_ic_coverage,
    check_nav_jump,
    check_position_count_drop,
    check_trades_freshness,
    format_report,
    max_severity,
)


class NavJumpTests(unittest.TestCase):
    def test_no_anomaly_for_small_moves(self):
        df = pd.DataFrame(
            {
                "date": ["2026-05-20", "2026-05-21", "2026-05-22"],
                "total_value": [100_000.0, 100_500.0, 100_800.0],
            }
        )
        self.assertEqual(check_nav_jump(df), [])

    def test_warns_at_5_to_10_percent(self):
        df = pd.DataFrame(
            {
                "date": ["2026-05-20", "2026-05-21"],
                "total_value": [100_000.0, 106_000.0],
            }
        )
        out = check_nav_jump(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "warn")
        self.assertEqual(out[0].check_name, "nav_jump")

    def test_critical_above_10_percent(self):
        df = pd.DataFrame(
            {
                "date": ["2026-05-20", "2026-05-21"],
                "total_value": [100_000.0, 115_000.0],
            }
        )
        out = check_nav_jump(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "critical")

    def test_aggregates_across_accounts(self):
        # 2 accounts each move modestly but in the same direction; aggregated
        # change is still small.
        df = pd.DataFrame(
            {
                "date": ["2026-05-20", "2026-05-20", "2026-05-21", "2026-05-21"],
                "account_id": ["a1", "a2", "a1", "a2"],
                "total_value": [50_000.0, 50_000.0, 51_000.0, 50_500.0],
            }
        )
        # Aggregated: 100k → 101.5k = +1.5% → no warn.
        self.assertEqual(check_nav_jump(df), [])

    def test_empty_input_no_crash(self):
        self.assertEqual(check_nav_jump(pd.DataFrame()), [])

    def test_missing_columns_no_crash(self):
        self.assertEqual(
            check_nav_jump(pd.DataFrame({"foo": [1, 2]})), []
        )


class PositionCountTests(unittest.TestCase):
    def test_healthy_count_no_anomaly(self):
        df = pd.DataFrame({"code": [f"{i:06d}" for i in range(60)]})
        self.assertEqual(check_position_count_drop(df), [])

    def test_warns_when_below_expected(self):
        df = pd.DataFrame({"code": [f"{i:06d}" for i in range(30)]})
        out = check_position_count_drop(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "warn")

    def test_critical_when_far_below(self):
        df = pd.DataFrame({"code": [f"{i:06d}" for i in range(10)]})
        out = check_position_count_drop(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "critical")

    def test_empty_is_info(self):
        out = check_position_count_drop(pd.DataFrame())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "info")


class ForwardICCoverageTests(unittest.TestCase):
    def test_healthy_ic_no_anomaly(self):
        df = pd.DataFrame(
            {
                "signal_date": ["2026-05-08"] * 5 + ["2026-05-15"] * 5,
                "factor": ["pe", "pb", "roe", "momentum_20", "momentum_60"] * 2,
                "ic": [0.05, 0.03, 0.04, 0.02, 0.06] * 2,
                "ic_status": ["ok"] * 10,
            }
        )
        self.assertEqual(check_forward_ic_coverage(df), [])

    def test_warns_when_too_many_nans(self):
        df = pd.DataFrame(
            {
                "signal_date": ["2026-05-15"] * 10,
                "factor": ["f1", "f2", "f3", "f4", "f5"] * 2,
                "ic": [float("nan")] * 5 + [0.02] * 5,
                "ic_status": ["nan_too_few_obs"] * 5 + ["ok"] * 5,
            }
        )
        out = check_forward_ic_coverage(df, nan_ratio_threshold=0.30)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "warn")
        self.assertIn("50%", out[0].message)

    def test_falls_back_to_value_when_status_column_missing(self):
        df = pd.DataFrame(
            {
                "signal_date": ["2026-05-15"] * 4,
                "factor": ["a", "b", "c", "d"],
                "ic": [float("nan"), float("nan"), 0.03, 0.05],
            }
        )
        out = check_forward_ic_coverage(df, nan_ratio_threshold=0.40)
        self.assertEqual(len(out), 1)


class BenchmarkCodeDtypeTests(unittest.TestCase):
    def test_canonical_codes_pass(self):
        df = pd.DataFrame(
            {
                "benchmark_code": ["000300", "000905", "000300"],
            }
        )
        self.assertEqual(check_benchmark_code_dtype(df), [])

    def test_six_digit_leading_zero_passes(self):
        df = pd.DataFrame({"benchmark_code": ["000001"]})  # SH composite, not in INDEX_CODES but valid form
        self.assertEqual(check_benchmark_code_dtype(df), [])

    def test_int_coerced_three_digit_flagged_critical(self):
        # The bug we fixed: pandas read '000300' as int 300, str(300) = '300'.
        df = pd.DataFrame({"benchmark_code": ["300", "905"]})
        out = check_benchmark_code_dtype(df)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "critical")
        self.assertEqual(out[0].check_name, "benchmark_code_dtype")


class TradesFreshnessTests(unittest.TestCase):
    def test_recent_trades_no_anomaly(self):
        today = pd.Timestamp.today().normalize()
        df = pd.DataFrame(
            {"trade_date": [(today - pd.Timedelta(days=2)).date().isoformat()]}
        )
        self.assertEqual(check_trades_freshness(df), [])

    def test_stale_trades_info(self):
        df = pd.DataFrame({"trade_date": ["2020-01-01"]})
        out = check_trades_freshness(df, stale_days=14)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "info")

    def test_empty_is_info(self):
        out = check_trades_freshness(pd.DataFrame())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "info")


class CheckAgentEndToEndTests(unittest.TestCase):
    def test_cold_start_agent_only_info_findings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "a_share" / "claude").mkdir(parents=True)
            findings = check_agent("claude", repo_root=root)
            # Cold-start: no nav, no positions, no trades, no IC.
            # Only the "info"-level "cold start" notices should fire.
            for f in findings:
                self.assertEqual(f.severity, "info", msg=f)
            self.assertEqual(max_severity(findings), "info")

    def test_critical_benchmark_dtype_propagates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data" / "a_share" / "claude"
            data_dir.mkdir(parents=True)
            # Seed a daily_nav.csv with the int-coerced benchmark_code bug.
            (data_dir / "daily_nav.csv").write_text(
                "date,account_id,benchmark_code,total_value\n"
                "2026-05-20,a1,300,100000\n"
                "2026-05-21,a1,300,100500\n",
                encoding="utf-8",
            )
            # Add positions so the count check passes
            codes = "\n".join(f"00000{i}" for i in range(60))
            (data_dir / "positions.csv").write_text(
                "code\n" + codes + "\n", encoding="utf-8"
            )
            findings = check_agent("claude", repo_root=root)
            self.assertEqual(max_severity(findings), "critical")
            names = {f.check_name for f in findings}
            self.assertIn("benchmark_code_dtype", names)


class FormatReportTests(unittest.TestCase):
    def test_empty_findings_clean_message(self):
        self.assertEqual(
            format_report("claude", []), "agent=claude: ✓ no anomalies"
        )

    def test_findings_listed_with_severity_tags(self):
        findings = [
            Anomaly(
                severity="critical",
                check_name="nav_jump",
                message="big move",
                detail={},
            ),
            Anomaly(
                severity="warn",
                check_name="position_count",
                message="low",
                detail={},
            ),
        ]
        out = format_report("claude", findings)
        self.assertIn("[CRITICAL]", out)
        self.assertIn("[WARN]", out)
        self.assertIn("nav_jump", out)


class MaxSeverityTests(unittest.TestCase):
    def test_empty_is_info(self):
        self.assertEqual(max_severity([]), "info")

    def test_picks_worst(self):
        findings = [
            Anomaly("info", "x", "x"),
            Anomaly("warn", "y", "y"),
            Anomaly("critical", "z", "z"),
            Anomaly("warn", "w", "w"),
        ]
        self.assertEqual(max_severity(findings), "critical")


if __name__ == "__main__":
    unittest.main()
