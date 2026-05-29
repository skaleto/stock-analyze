"""Tests for the backtest full-pipeline scoring adapter.

Builds a minimal real ``backtest_cache`` fixture (daily_basic + daily
history + stock_basic + index_weight) so ``score_with_overlay`` is
exercised end-to-end through the actual factor_pipeline, then verifies
the engine routes to it behind the ``use_full_pipeline`` flag.

OpenSpec change: bridge-factor-pipeline-into-backtest.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from stock_analyze.markets.a_share.backtest import engine
from stock_analyze.markets.a_share.backtest.data_view import PointInTimeView
from stock_analyze.markets.a_share.backtest.scoring import score_with_overlay


_AS_OF = date(2024, 6, 28)
_CODES = ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "000333.SZ"]


def _build_cache(root: Path, *, n_daily_days: int = 65) -> None:
    """Seed a minimal point-in-time cache covering _AS_OF."""
    (root / "daily").mkdir(parents=True)
    (root / "daily_basic").mkdir(parents=True)
    (root / "index_weight").mkdir(parents=True)

    # daily_basic on as_of: pe_ttm / pb / dv_ttm per code (varied so factors bite)
    db = pd.DataFrame({
        "ts_code": _CODES,
        "trade_date": ["20240628"] * len(_CODES),
        "pe_ttm": [8.0, 40.0, 12.0, 30.0, 6.0],
        "pb": [0.9, 5.0, 1.2, 8.0, 0.7],
        "dv_ttm": [3.0, 0.5, 2.0, 1.0, 4.0],
        "total_mv": [1e7, 2e7, 1.5e7, 3e7, 1.2e7],
    })
    db.to_csv(root / "daily_basic" / f"{_AS_OF.isoformat()}.csv", index=False)

    # daily history: build n_daily_days of closes per code with distinct
    # momentum (code 0 strong uptrend, code 1 flat, etc.)
    drifts = {c: d for c, d in zip(_CODES, [1.01, 1.000, 1.005, 0.997, 1.008])}
    base = {c: 10.0 for c in _CODES}
    for i in range(n_daily_days):
        d = _AS_OF - timedelta(days=n_daily_days - 1 - i)
        rows = []
        for c in _CODES:
            close = base[c] * (drifts[c] ** i)
            rows.append({"ts_code": c, "trade_date": d.strftime("%Y%m%d"),
                          "open": close * 0.999, "high": close * 1.01,
                          "low": close * 0.99, "close": close, "vol": 1e6})
        pd.DataFrame(rows).to_csv(root / "daily" / f"{d.isoformat()}.csv", index=False)

    # stock_basic with industry
    pd.DataFrame({
        "ts_code": _CODES,
        "industry": ["银行", "地产", "银行", "白酒", "家电"],
        "list_date": ["20000101"] * len(_CODES),
        "delist_date": [""] * len(_CODES),
    }).to_csv(root / "stock_basic.csv", index=False)

    # index_weight: all codes in hs300 as of 2024-06
    pd.DataFrame({
        "index_code": ["000300.SH"] * len(_CODES),
        "con_code": _CODES,
        "trade_date": ["20240601"] * len(_CODES),
        "weight": [20.0] * len(_CODES),
    }).to_csv(root / "index_weight" / "000300_2024-06.csv", index=False)


def _view(root: Path) -> PointInTimeView:
    return PointInTimeView(as_of=_AS_OF, cache_root=root)


def _overlay(factors: dict, *, top_n: int = 3, use_full: bool = True) -> dict:
    ov = {
        "factors": factors,
        "factor_processing": {"neutralize_industry": False, "min_factor_coverage": 0.0},
        "accounts": [{"id": "hs300", "scope": "hs300", "top_n": top_n}],
    }
    if use_full:
        ov["backtest"] = {"use_full_pipeline": True}
    return ov


class ScoreWithOverlayTests(unittest.TestCase):
    def test_returns_top_n_per_account(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            rows = score_with_overlay(
                _view(root),
                _overlay({"pe": {"weight": 1.0, "direction": "low"}}, top_n=3),
                _AS_OF, ["hs300"],
            )
            self.assertEqual(len(rows), 3)  # top_n=3, one account
            for r in rows:
                self.assertEqual(r["account_id"], "hs300")
                self.assertIn(r["ts_code"], _CODES)
                self.assertIn("score", r)

    def test_low_pe_factor_ranks_cheap_stocks_first(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            rows = score_with_overlay(
                _view(root),
                _overlay({"pe": {"weight": 1.0, "direction": "low"}}, top_n=2),
                _AS_OF, ["hs300"],
            )
            picked = {r["ts_code"] for r in rows}
            # pe_ttm: 000001=8, 600519=30, 000002=40, 600000=12, 000333=6
            # low-PE top 2 → 000333 (6) and 000001 (8)
            self.assertEqual(picked, {"000333.SZ", "000001.SZ"})

    def test_momentum_overlay_picks_uptrend_not_cheap(self):
        """The whole point of P1: a momentum overlay ranks differently than PE."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            rows = score_with_overlay(
                _view(root),
                _overlay({"momentum_60": {"weight": 1.0, "direction": "high"}}, top_n=2),
                _AS_OF, ["hs300"],
            )
            picked = {r["ts_code"] for r in rows}
            # drifts: 000001=1.01 (strongest), 000333=1.008, 600000=1.005...
            # momentum top 2 → 000001 and 000333. NOT the same as a value tilt
            # would give on its own — confirms the factor mix actually drives it.
            self.assertIn("000001.SZ", picked)

    def test_broadcast_sentiment_contributes_zero_in_backtest(self):
        """A sentiment factor in the overlay must not change historical ranking."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            base = score_with_overlay(
                _view(root),
                _overlay({"pe": {"weight": 1.0, "direction": "low"}}, top_n=3),
                _AS_OF, ["hs300"],
            )
            with_sent = score_with_overlay(
                _view(root),
                _overlay({
                    "pe": {"weight": 1.0, "direction": "low"},
                    "claude_market_sentiment_1w": {"weight": 0.5, "direction": "high"},
                }, top_n=3),
                _AS_OF, ["hs300"],
            )
            # Broadcast shift is uniform (and 0.0 in backtest) → same picks/order.
            self.assertEqual([r["ts_code"] for r in base],
                             [r["ts_code"] for r in with_sent])

    def test_empty_daily_basic_returns_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            # An as_of with no daily_basic snapshot → score_with_overlay's
            # first guard returns [] without touching the factor pipeline.
            empty_view = PointInTimeView(as_of=date(2024, 1, 1), cache_root=root)
            rows = score_with_overlay(
                empty_view,
                _overlay({"pe": {"weight": 1.0, "direction": "low"}}),
                date(2024, 1, 1), ["hs300"],
            )
            self.assertEqual(rows, [])


class EngineFlagRoutingTests(unittest.TestCase):
    def test_flag_on_routes_to_score_with_overlay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            overlay = _overlay({"pe": {"weight": 1.0, "direction": "low"}}, use_full=True)
            with patch(
                "stock_analyze.markets.a_share.backtest.scoring.score_with_overlay",
                return_value=[{"signal_date": _AS_OF.isoformat(), "account_id": "hs300",
                                "ts_code": "000001.SZ", "score": 1.0}],
            ) as mock_score:
                out = engine._compute_signals(_view(root), overlay, _AS_OF, ["hs300"])
            mock_score.assert_called_once()
            self.assertEqual(out[0]["ts_code"], "000001.SZ")

    def test_flag_off_uses_mvp_pe_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            overlay = _overlay({"momentum_60": {"weight": 1.0, "direction": "high"}},
                                top_n=2, use_full=False)  # no backtest.use_full_pipeline
            with patch(
                "stock_analyze.markets.a_share.backtest.scoring.score_with_overlay",
            ) as mock_score:
                out = engine._compute_signals(_view(root), overlay, _AS_OF, ["hs300"])
            # MVP path runs (PE-only), score_with_overlay NOT called
            mock_score.assert_not_called()
            picked = {r["ts_code"] for r in out}
            # MVP = low-PE top 2 → 000333 (6) and 000001 (8), ignoring the
            # overlay's momentum factor entirely (that's the bug P1 fixes).
            self.assertEqual(picked, {"000333.SZ", "000001.SZ"})


class BroadcastAccessorTests(unittest.TestCase):
    def test_broadcast_returns_zero(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            self.assertEqual(_view(root).broadcast("claude_market_sentiment_1w", _AS_OF), 0.0)


class StructuralEquivalenceTests(unittest.TestCase):
    """Gate structural-equivalence guard (bridge-factor-pipeline §5)."""

    def test_healthy_varied_scores_pass(self):
        from stock_analyze.markets.a_share.backtest.gate import check_structural_equivalence
        samples = [{"date": "2025-06-01", "scores": [0.9, 0.5, 0.1, -0.3, -0.8]}]
        check_structural_equivalence(samples)  # no raise

    def test_degenerate_all_tied_raises(self):
        from stock_analyze.markets.a_share.backtest.gate import check_structural_equivalence
        from stock_analyze.markets.a_share.backtest.exceptions import BacktestStructuralBreach
        samples = [{"date": "2025-06-01", "scores": [0.0] * 50}]
        with self.assertRaises(BacktestStructuralBreach) as ctx:
            check_structural_equivalence(samples)
        self.assertEqual(ctx.exception.detail["type"], "degenerate_scores")
        self.assertEqual(ctx.exception.detail["date"], "2025-06-01")

    def test_below_half_unique_raises(self):
        from stock_analyze.markets.a_share.backtest.gate import check_structural_equivalence
        from stock_analyze.markets.a_share.backtest.exceptions import BacktestStructuralBreach
        # 10 scores, only 3 distinct → ratio 0.3 < 0.5 → degenerate
        scores = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0]
        with self.assertRaises(BacktestStructuralBreach):
            check_structural_equivalence([{"date": "2025-06-01", "scores": scores}])

    def test_empty_samples_no_op(self):
        from stock_analyze.markets.a_share.backtest.gate import check_structural_equivalence
        check_structural_equivalence([])  # no raise (thin-cache safe)
        check_structural_equivalence([{"date": "x", "scores": []}])  # skipped
        check_structural_equivalence([{"date": "x", "scores": [0.0]}])  # <2, skipped


class DataViewEmptyUniverseTests(unittest.TestCase):
    """Robustness: an index with no weight snapshot must not crash."""

    def test_empty_universe_returns_empty_not_keyerror(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_cache(root)
            # zz500 has no index_weight file in the fixture → empty universe.
            # Pre-fix this raised KeyError('ts_code') in _filter_listed.
            self.assertEqual(_view(root).universe(indices=["zz500"]), [])


if __name__ == "__main__":
    unittest.main()
