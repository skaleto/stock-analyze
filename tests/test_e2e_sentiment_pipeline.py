"""End-to-end test: record-sentiment CLI → factor_pipeline broadcast shift.

Proves the full B pipeline works without needing a live LLM client.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze import factor_pipeline
from stock_analyze.markets.a_share.alt_factors import sentiment


PROJECT_ROOT = Path(__file__).parent.parent


def _three_candidates() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "000001", "name": "A", "industry": "银行",
         "pe": 5.0, "roe": 0.08},
        {"code": "000002", "name": "B", "industry": "地产",
         "pe": 10.0, "roe": 0.12},
        {"code": "000003", "name": "C", "industry": "银行",
         "pe": 7.5, "roe": 0.10},
    ])


class E2ESentimentToFactorPipelineTests(unittest.TestCase):
    """In-process e2e: record → load_broadcast_factor → process_factors."""

    def test_full_pipeline_shift_after_recording(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sentiment.record_market_sentiment(
                agent_id="claude", week_end=date(2026, 5, 22),
                score=0.50, confidence=0.78,
                drivers=["AI 算力链回暖", "央行 MLF 偏鸽"],
                sources=["https://www.cls.cn/x"],
                llm_model="claude-sonnet-4.5", prompt_version="v1",
                repo_root=repo,
            )

            value = factor_pipeline.load_broadcast_factor(
                "claude", "claude_market_sentiment_1w",
                date(2026, 5, 25), repo_root=repo,
            )
            # Confidence-weighted: 0.50 (score) × 0.78 (confidence) = 0.39
            self.assertAlmostEqual(value, 0.50 * 0.78)

            overlay_factors = {
                "pe": {"weight": 0.45, "direction": "low"},
                "roe": {"weight": 0.45, "direction": "high"},
                "claude_market_sentiment_1w": {"weight": 0.10, "direction": "high"},
            }
            fp_cfg = {
                "winsorize_lower": 0.01, "winsorize_upper": 0.99,
                "neutralize_industry": False, "min_factor_coverage": 0.1,
            }
            classic_only = {
                "pe": {"weight": 0.5, "direction": "low"},
                "roe": {"weight": 0.5, "direction": "high"},
            }
            scored_a, _ = factor_pipeline.process_factors(
                _three_candidates(), classic_only, fp_cfg,
            )
            scored_b, _ = factor_pipeline.process_factors(
                _three_candidates(), overlay_factors, fp_cfg,
                broadcast_values={"claude_market_sentiment_1w": value},
            )

            # Uniform shift = sign(+1) × weight(0.10) × value(0.50 × 0.78) = 0.039
            shifts = scored_b.set_index("code")["score"] - scored_a.set_index("code")["score"]
            for code in scored_a["code"]:
                self.assertAlmostEqual(shifts[code], 0.10 * 0.50 * 0.78, places=4)


class E2ESentimentCLISubprocessTests(unittest.TestCase):
    """E2E via subprocess: CLI record → CSV on disk → CLI sentiment-log → row visible."""

    def test_record_then_log_cycle(self):
        with TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable, "-m", "stock_analyze",
                    "record-sentiment",
                    "--agent", "claude",
                    "--week-end", "2026-05-22",
                    "--score", "0.32",
                    "--confidence", "0.78",
                    "--drivers", "AI 算力链回暖,央行 MLF 偏鸽",
                    "--llm-model", "claude-sonnet-4.5",
                ],
                cwd=workdir,
                capture_output=True, text=True,
                env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(
                result.returncode, 0,
                f"record failed: stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertIn("recorded", result.stdout.lower())

            csv = workdir / "data" / "a_share" / "claude" / "alt_factors" / "market_sentiment.csv"
            self.assertTrue(csv.exists())

            result = subprocess.run(
                [
                    sys.executable, "-m", "stock_analyze",
                    "sentiment-log",
                    "--agent", "claude",
                    "--repo-root", str(workdir),
                ],
                capture_output=True, text=True,
                env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("2026-05-22", result.stdout)
            self.assertIn("+0.32", result.stdout)

    def test_duplicate_record_exits_1(self):
        with TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            args = [
                sys.executable, "-m", "stock_analyze",
                "record-sentiment",
                "--agent", "claude",
                "--week-end", "2026-05-22",
                "--score", "0.32", "--confidence", "0.78",
                "--drivers", "x",
                "--llm-model", "m",
            ]
            env = {"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"}
            r1 = subprocess.run(args, cwd=workdir, capture_output=True,
                                 text=True, env=env)
            self.assertEqual(r1.returncode, 0)
            r2 = subprocess.run(args, cwd=workdir, capture_output=True,
                                 text=True, env=env)
            self.assertEqual(r2.returncode, 1)
            self.assertIn("already", r2.stderr.lower())


if __name__ == "__main__":
    unittest.main()
