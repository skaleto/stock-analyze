from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_analyze.research.rule_core_diagnostic import (
    DataAudit,
    RuleCoreSpec,
    _apply_filters,
    _portfolio_contract,
    _run_overlay,
    _score_rule_frame,
    attach_entry_execution_constraints,
    audit_rule_core_data,
    stage1_aggregate_status,
    development_hypothesis_status,
    select_development_dates,
    run_rule_core_diagnostic,
)


class RuleCoreDiagnosticTest(unittest.TestCase):
    def test_formal_replay_uses_live_default_execution_controls(self) -> None:
        baseline = {
            "accounts": [{"id": "hs300", "top_n": 50}],
            "trading": {"max_single_weight": 0.05},
        }

        trend = _portfolio_contract(
            baseline,
            {
                "agent_id": "codex",
                "portfolio_controls": {"hold_buffer_pct": 0.20},
            },
        )
        defensive = _portfolio_contract(
            baseline,
            {
                "agent_id": "claude",
                "portfolio_controls": {
                    "hold_buffer_pct": 0.80,
                    "min_trade_weight": 0.02,
                    "max_turnover": 0.35,
                },
            },
        )

        self.assertEqual(
            trend["rule_execution_policy"]["max_daily_turnover"],
            1.0,
        )
        self.assertEqual(
            trend["rule_execution_policy"]["minimum_target_change"],
            0.001,
        )
        self.assertEqual(
            defensive["rule_execution_policy"]["max_daily_turnover"],
            0.35,
        )
        self.assertEqual(
            defensive["rule_execution_policy"]["minimum_target_change"],
            0.02,
        )

    def test_development_window_uses_only_oldest_sixty_percent(self) -> None:
        dates = pd.date_range("2024-01-02", periods=10, freq="B")
        frame = pd.DataFrame({"trade_date": dates.strftime("%Y%m%d")})

        selected = select_development_dates(frame)

        self.assertEqual(selected, tuple(frame["trade_date"].iloc[:6]))
        self.assertNotIn(frame["trade_date"].iloc[6], selected)

    def test_filtered_securities_remain_as_ineligible_quote_rows(self) -> None:
        features = pd.DataFrame([
            {
                "trade_date": date, "account_id": "hs300", "code": code,
                "pe": pe, "avg_amount_20": amount, "industry": "测试",
            }
            for date in ("20260102", "20260105", "20260106")
            for code, pe, amount in (
                ("000001", 10.0, 1_000.0),
                ("000002", 20.0, 1.0),
            )
        ])
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=lambda frame: frame["trade_date"],
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {"pe": {"weight": 1.0, "direction": "low"}},
            "factor_processing": {"min_factor_coverage": 1.0},
            "filters": {"min_avg_amount_20": 100.0},
        }

        scored = _score_rule_frame(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=("20260102", "20260105", "20260106"),
            names_by_code={"000001": "可选", "000002": "仅行情"},
        )

        quote_only = scored.loc[scored["code"].eq("000002")]
        self.assertEqual(len(quote_only), 3)
        self.assertFalse(quote_only["_eligible_for_selection"].astype(bool).any())
        self.assertTrue(
            scored.loc[scored["code"].eq("000001"), "_eligible_for_selection"]
            .astype(bool)
            .all()
        )

    def test_missing_ohlcv_cannot_create_tradability_evidence(self) -> None:
        features = pd.DataFrame({
            "code": ["000001", "000001"],
            "trade_date": ["20260102", "20260105"],
            "open": [10.0, 10.1],
            "close": [10.0, 10.1],
        })
        labels = pd.DataFrame({
            "code": ["000001"],
            "trade_date": ["20260102"],
            "entry_date": ["20260105"],
            "entry_price": [10.1],
        })

        constrained = attach_entry_execution_constraints(features, labels)

        self.assertTrue(constrained["entry_buy_allowed"].isna().all())
        self.assertTrue(constrained["entry_sell_allowed"].isna().all())

    def test_st_filter_uses_point_in_time_flag_not_display_name(self) -> None:
        frame = pd.DataFrame({
            "code": ["000001", "000002"],
            "name": ["*ST未来名称", "普通名称"],
            "is_st": [False, True],
        })

        filtered = _apply_filters(
            frame,
            market="a_share",
            overlay={"filters": {"exclude_st": True}},
        )

        self.assertEqual(filtered["code"].tolist(), ["000001"])

    def test_st_filter_handles_nullable_arrow_integer_flags(self) -> None:
        frame = pd.DataFrame({
            "code": ["000001", "000002", "000003"],
            "is_st": pd.Series([0, 1, pd.NA], dtype="int32[pyarrow]"),
        })

        filtered = _apply_filters(
            frame,
            market="a_share",
            overlay={"filters": {"exclude_st": True}},
        )

        self.assertEqual(filtered["code"].tolist(), ["000001"])

    def test_entry_session_misalignment_blocks_data_audit(self) -> None:
        features = pd.DataFrame({
            "trade_date": ["20260102", "20260102"],
            "account_id": ["us_exposure", "us_exposure"],
            "code": ["513100", "513500"],
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "tushare_fund_basic_listing_interval",
            "momentum_60": 0.1,
            "unit_nav": 1.0,
            "discount_premium": 0.0,
            "avg_amount_20": 1_000_000.0,
            "tracking_error_20": 0.01,
            "index_key": "us_equity",
            "theme": "美国股票",
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=["20260105", "20260106"],
            entry_price=1.0,
            benchmark_entry_price=1.0,
        )

        audit = audit_rule_core_data(
            features,
            labels,
            market="cn_qdii_etf",
            overlay={"factors": {"momentum_60": {"weight": 1.0}}},
            development_dates=("20260102",),
            names_by_code={"513100": "纳指ETF", "513500": "标普ETF"},
        )

        self.assertIn("entry_session_alignment_coverage_below_floor", audit.reasons)

    def test_audit_blocks_a_share_when_point_in_time_valuation_is_missing(self) -> None:
        dates = pd.date_range("2024-01-02", periods=10, freq="B")
        features = pd.DataFrame(
            {
                "trade_date": dates.strftime("%Y%m%d"),
                "account_id": "hs300",
                "code": "000001",
                "unbiased_universe": True,
                "universe_quality": "available",
                "pe_ttm": [float("nan")] * 9 + [12.0],
                "pb": [float("nan")] * 9 + [1.2],
                "roe": 12.0,
                "return_1": 0.001,
            }
        )
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=dates.strftime("%Y%m%d"),
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {
                "pe": {"weight": 0.4, "direction": "low"},
                "pb": {"weight": 0.2, "direction": "low"},
                "roe": {"weight": 0.4, "direction": "high"},
            },
            "filters": {"require_fields": ["pe", "pb", "roe"]},
        }

        audit = audit_rule_core_data(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=tuple(features["trade_date"].iloc[:6]),
            names_by_code={"000001": "测试公司"},
            expected_account_sizes={"hs300": 1},
        )

        self.assertFalse(audit.passes)
        self.assertIn("factor_point_in_time_coverage_below_floor:pe", audit.reasons)
        self.assertIn("factor_point_in_time_coverage_below_floor:pb", audit.reasons)

    def test_a_share_audit_distinguishes_structural_nulls_from_missing_sources(self) -> None:
        dates = pd.bdate_range("2018-01-02", periods=80)
        trade_dates = dates.strftime("%Y%m%d")
        features = pd.DataFrame({
            "trade_date": trade_dates,
            "account_id": "hs300",
            "code": "000001",
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "materialized_index_weight",
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 1_000_000.0,
            "is_tradable": True,
            "pe_ttm": float("nan"),
            "pb": 1.2,
            "roe": 10.0,
            "low_volatility_60": [float("nan")] * 59 + [0.2] * 21,
            "daily_basic_trade_date": trade_dates,
            "fundamental_available_date": "20171231",
            "fundamental_restatement_policy": "latest_revision_visible_on_announcement_date",
            "security_status": "listed",
            "is_st": False,
            "is_suspended": False,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=trade_dates,
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {
                "pe": {"weight": 0.4},
                "pb": {"weight": 0.2},
                "roe": {"weight": 0.2},
                "low_volatility_60": {"weight": 0.2},
            },
            "factor_processing": {"min_factor_coverage": 0.0},
            "filters": {
                "min_pe": 0,
                "require_fields": ["pe", "pb", "roe", "low_volatility_60"],
            },
        }

        audit = audit_rule_core_data(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=tuple(trade_dates),
            names_by_code={"000001": "测试公司"},
            expected_account_sizes={"hs300": 1},
        )

        self.assertNotIn(
            "factor_point_in_time_coverage_below_floor:pe", audit.reasons
        )
        self.assertNotIn(
            "factor_point_in_time_coverage_below_floor:low_volatility_60",
            audit.reasons,
        )
        self.assertEqual(
            audit.checks["required_factor_data_coverage"]["pe"], 1.0
        )
        self.assertEqual(
            audit.checks["required_factor_data_coverage"]["low_volatility_60"],
            1.0,
        )

    def test_a_share_audit_rejects_non_exact_or_invalid_source_dates(self) -> None:
        dates = pd.bdate_range("2018-01-02", periods=80).strftime("%Y%m%d")
        features = pd.DataFrame({
            "trade_date": dates,
            "account_id": "hs300",
            "code": "000001",
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "materialized_index_weight",
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.0,
            "volume": 1_000_000.0,
            "is_tradable": True,
            "pe_ttm": 10.0,
            "roe": 10.0,
            "daily_basic_trade_date": "",
            "fundamental_available_date": "20171231",
            "fundamental_restatement_policy": "unverified_policy",
            "security_status": "listed",
            "is_st": False,
            "is_suspended": False,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=dates,
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {"pe": {"weight": 0.5}, "roe": {"weight": 0.5}},
            "factor_processing": {"min_factor_coverage": 0.0},
            "filters": {"require_fields": ["pe", "roe"]},
        }

        audit = audit_rule_core_data(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=tuple(dates),
            names_by_code={"000001": "测试公司"},
            expected_account_sizes={"hs300": 1},
        )

        self.assertIn("daily_basic_point_in_time_coverage_below_floor", audit.reasons)
        self.assertIn("financial_restatement_policy_coverage_below_floor", audit.reasons)
        self.assertEqual(audit.checks["daily_basic_point_in_time_coverage"], 0.0)
        self.assertEqual(audit.checks["financial_restatement_policy_coverage"], 0.0)

        features["daily_basic_trade_date"] = "20170101"
        stale = audit_rule_core_data(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=tuple(dates),
            names_by_code={"000001": "测试公司"},
            expected_account_sizes={"hs300": 1},
        )
        self.assertEqual(stale.checks["daily_basic_point_in_time_coverage"], 0.0)

    def test_status_is_driven_by_intended_core_not_control_result(self) -> None:
        passing_audit = DataAudit(
            passes=True,
            checks={"entry_price_coverage": 1.0},
            reasons=(),
        )
        intended = {
            "trade_count": 8,
            "net_return": 0.08,
            "annualized_excess_wealth": 0.03,
            "gross_profit_amount": 20_000.0,
            "total_execution_cost": 2_000.0,
        }
        losing_control = {
            "trade_count": 8,
            "net_return": -0.20,
            "annualized_excess_wealth": -0.12,
        }

        self.assertEqual(
            development_hypothesis_status(intended, passing_audit),
            "proceed",
        )
        self.assertEqual(
            development_hypothesis_status(losing_control, passing_audit),
            "negative_hypothesis",
        )
        self.assertEqual(
            development_hypothesis_status(
                intended,
                DataAudit(False, {}, ("membership_coverage_below_floor",)),
            ),
            "data_blocked",
        )

    def test_costs_consuming_gross_profit_is_a_negative_hypothesis(self) -> None:
        audit = DataAudit(True, {}, ())
        metrics = {
            "trade_count": 4,
            "net_return": 0.01,
            "annualized_excess_wealth": 0.01,
            "gross_profit_amount": 1_000.0,
            "total_execution_cost": 1_100.0,
        }

        self.assertEqual(
            development_hypothesis_status(metrics, audit),
            "negative_hypothesis",
        )

    def test_a_share_audit_requires_eight_years_and_point_in_time_provenance(self) -> None:
        dates = pd.Series(["20200102", "20260102"])
        features = pd.DataFrame({
            "trade_date": dates,
            "account_id": "hs300",
            "code": "000001",
            "name": "测试公司",
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "monthly_index_weight",
            "pe_ttm": 12.0,
            "pb": 1.2,
            "roe": 12.0,
            "low_volatility_60": 0.2,
            "daily_basic_trade_date": dates,
            "fundamental_available_date": ["20191231", "20251231"],
            "fundamental_period_end": ["20190930", "20250930"],
            "fundamental_restatement_policy": "latest_revision_visible_on_announcement_date",
            "security_status": "listed",
            "is_st": False,
            "is_suspended": False,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=dates,
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {
                "pe": {"weight": 0.3}, "pb": {"weight": 0.2},
                "roe": {"weight": 0.3}, "low_volatility_60": {"weight": 0.2},
            },
            "factor_processing": {"min_factor_coverage": 0.5},
            "filters": {"require_fields": ["pe", "pb", "roe", "low_volatility_60"]},
        }

        audit = audit_rule_core_data(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=tuple(dates),
            names_by_code={"000001": "测试公司"},
            expected_account_sizes={"hs300": 1},
        )

        self.assertFalse(audit.passes)
        self.assertIn("a_share_history_shorter_than_eight_years", audit.reasons)
        self.assertIn("trading_date_density_below_floor", audit.reasons)
        self.assertEqual(audit.checks["financial_publication_date_coverage"], 1.0)
        self.assertEqual(audit.checks["security_status_coverage"], 1.0)

    def test_complete_materialized_fixture_passes_a_share_audit(self) -> None:
        dates = pd.bdate_range("2018-01-02", "2026-08-07")
        trade_dates = dates.strftime("%Y%m%d")
        features = pd.DataFrame({
            "trade_date": trade_dates,
            "account_id": "hs300",
            "code": "000001",
            "name": "测试公司",
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "monthly_index_weight",
            "open": 10.0,
            "high": 10.2,
            "low": 9.8,
            "close": 10.1,
            "volume": 1_000_000.0,
            "pe_ttm": 12.0,
            "pb": 1.2,
            "roe": 12.0,
            "low_volatility_60": 0.2,
            "daily_basic_trade_date": trade_dates,
            "fundamental_available_date": "20171231",
            "fundamental_period_end": "20170930",
            "fundamental_restatement_policy": "latest_revision_visible_on_announcement_date",
            "security_status": "listed",
            "is_st": False,
            "is_suspended": False,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=trade_dates,
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {
                "pe": {"weight": 0.3},
                "pb": {"weight": 0.2},
                "roe": {"weight": 0.3},
                "low_volatility_60": {"weight": 0.2},
            },
            "factor_processing": {"min_factor_coverage": 0.95},
            "filters": {"require_fields": ["pe", "pb", "roe", "low_volatility_60"]},
        }
        development_dates = select_development_dates(features)

        audit = audit_rule_core_data(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            development_dates=development_dates,
            names_by_code={"000001": "测试公司"},
            expected_account_sizes={"hs300": 1},
        )

        self.assertTrue(audit.passes, audit.reasons)
        self.assertGreaterEqual(audit.checks["full_history_calendar_days"], 2922)
        self.assertEqual(audit.checks["security_status_coverage"], 1.0)

    def test_qdii_audit_reports_required_economic_data_coverage(self) -> None:
        dates = pd.Series(["20240102", "20240103", "20240104"])
        features = pd.DataFrame({
            "trade_date": dates,
            "account_id": "us_exposure",
            "code": "513100",
            "name": "纳指ETF",
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "tushare_fund_basic_listing_interval",
            "momentum_20": 0.1,
            "momentum_60": 0.2,
            "low_volatility_60": 0.15,
            "avg_amount_20": 100_000_000.0,
            "discount_premium": 0.01,
            "unit_nav": 1.0,
            "tracking_error_20": 0.02,
            "index_key": "nasdaq_100",
            "theme": "美国股票",
            "open": 1.0,
            "high": 1.01,
            "low": 0.99,
            "close": 1.0,
            "volume": 1_000_000.0,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=dates,
            entry_price=1.0,
            benchmark_entry_price=1.0,
        )
        overlay = {
            "factors": {
                "momentum_20": {"weight": 0.5},
                "momentum_60": {"weight": 0.5},
            },
            "factor_processing": {"min_factor_coverage": 0.6},
        }

        audit = audit_rule_core_data(
            features,
            labels,
            market="cn_qdii_etf",
            overlay=overlay,
            development_dates=tuple(dates),
            names_by_code={"513100": "纳指ETF"},
        )

        self.assertTrue(audit.passes, audit.reasons)
        self.assertEqual(audit.checks["qdii_nav_coverage"], 1.0)
        self.assertEqual(audit.checks["qdii_underlying_exposure_coverage"], 1.0)
        self.assertEqual(audit.checks["qdii_membership_coverage_by_year"]["2024"], 1.0)

    def test_qdii_coverage_excludes_declared_rolling_warmup_rows(self) -> None:
        dates = pd.Series(["20240102", "20240103", "20240104"])
        features = pd.DataFrame({
            "trade_date": dates,
            "account_id": "us_exposure",
            "code": "513100",
            "unbiased_universe": True,
            "universe_quality": "available",
            "membership_source": "tushare_fund_basic_listing_interval",
            "momentum_20": [float("nan"), 0.1, 0.2],
            "momentum_60": [float("nan"), 0.1, 0.2],
            "avg_amount_20": [float("nan"), 100_000_000.0, 100_000_000.0],
            "unit_nav": 1.0,
            "discount_premium": 0.0,
            "tracking_error_20": 0.01,
            "index_key": "nasdaq_100",
            "theme": "美国股票",
            "open": 1.0,
            "high": 1.01,
            "low": 0.99,
            "close": 1.0,
            "volume": 1_000_000.0,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=dates, entry_price=1.0, benchmark_entry_price=1.0,
        )
        overlay = {
            "factors": {
                "momentum_20": {"weight": 0.5},
                "momentum_60": {"weight": 0.5},
            },
            "factor_processing": {"min_factor_coverage": 0.6},
        }

        audit = audit_rule_core_data(
            features,
            labels,
            market="cn_qdii_etf",
            overlay=overlay,
            development_dates=tuple(dates),
            names_by_code={"513100": "纳指ETF"},
        )

        self.assertTrue(audit.passes, audit.reasons)
        self.assertEqual(audit.checks["qdii_liquidity_coverage"], 1.0)
        self.assertEqual(audit.checks["qdii_strategy_ready_rows"], 2)

    def test_any_data_blocked_market_stops_stage_two(self) -> None:
        self.assertEqual(
            stage1_aggregate_status({"a_share": "data_blocked", "qdii": "proceed"}),
            "data_repair_required",
        )
        self.assertEqual(
            stage1_aggregate_status({"a_share": "proceed", "qdii": "negative_hypothesis"}),
            "partial_proceed",
        )

    def test_rule_controls_share_the_same_benchmark_calendar(self) -> None:
        dates = pd.date_range("2024-01-02", periods=6, freq="B").strftime("%Y%m%d")
        features = pd.DataFrame({
            "trade_date": dates,
            "account_id": "scope",
            "code": "000001",
            "factor": [1.0, float("nan"), 1.0, float("nan"), 1.0, 1.0],
            "avg_amount_20": 100_000_000.0,
        })
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=pd.Series(dates).shift(-1).fillna(dates[-1]),
            entry_price=[10.0, 10.1, 10.2, 10.3, 10.4, 10.5],
            benchmark_entry_price=[100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        )
        baseline = {
            "accounts": [{"id": "scope", "cash": 100_000.0, "top_n": 1}],
            "trading": {"lot_size": 100, "max_single_weight": 1.0},
        }
        sparse_overlay = {
            "factors": {"factor": {"weight": 1.0, "direction": "high"}},
            "factor_processing": {"min_factor_coverage": 1.0},
            "filters": {"require_fields": ["factor"]},
        }
        dense_overlay = {
            "factors": {"avg_amount_20": {"weight": 1.0, "direction": "high"}},
            "factor_processing": {"min_factor_coverage": 1.0},
        }

        sparse, *_ = _run_overlay(
            features,
            labels,
            market="a_share",
            overlay=sparse_overlay,
            baseline=baseline,
            development_dates=tuple(dates),
            names_by_code={"000001": "测试"},
        )
        dense, *_ = _run_overlay(
            features,
            labels,
            market="a_share",
            overlay=dense_overlay,
            baseline=baseline,
            development_dates=tuple(dates),
            names_by_code={"000001": "测试"},
        )

        self.assertAlmostEqual(sparse["benchmark_cagr"], dense["benchmark_cagr"])

    def test_equal_weight_control_preserves_investable_account_top_n(self) -> None:
        dates = pd.date_range("2026-01-02", periods=4, freq="B").strftime("%Y%m%d")
        features = pd.DataFrame([
            {
                "trade_date": trade_date,
                "account_id": "scope",
                "code": f"{code:06d}",
                "avg_amount_20": 100_000_000.0,
                "industry": f"行业{code}",
            }
            for trade_date in dates
            for code in range(1, 7)
        ])
        labels = features[["trade_date", "account_id", "code"]].assign(
            entry_date=lambda frame: frame["trade_date"],
            entry_price=10.0,
            benchmark_entry_price=100.0,
        )
        overlay = {
            "factors": {"avg_amount_20": {"weight": 1.0, "direction": "high"}},
            "factor_processing": {"min_factor_coverage": 1.0},
            "portfolio_controls": {"max_industry_weight": 1.0},
        }
        baseline = {
            "accounts": [{"id": "scope", "cash": 1_000_000.0, "top_n": 2}],
            "trading": {"lot_size": 100, "max_single_weight": 0.50},
        }

        _, _, trades, _ = _run_overlay(
            features,
            labels,
            market="a_share",
            overlay=overlay,
            baseline=baseline,
            development_dates=tuple(dates),
            names_by_code={f"{code:06d}": f"证券{code}" for code in range(1, 7)},
            equal_weight_control=True,
        )

        first_buys = trades.loc[
            trades["signal_date"].eq(trades["signal_date"].min())
            & trades["side"].eq("buy")
        ]
        self.assertEqual(first_buys["code"].nunique(), 2)

    def test_end_to_end_fixture_writes_fixed_controls_and_attribution(self) -> None:
        dates = pd.date_range("2017-01-03", periods=10, freq="365D").strftime("%Y%m%d")

        def market_frames(market: str, account_id: str, horizon: int):
            rows = []
            labels = []
            for index, trade_date in enumerate(dates):
                for code, winner in (("000001", True), ("000002", False)):
                    price = 10.0 * ((1.05 if winner else 0.95) ** index)
                    row = {
                        "trade_date": trade_date,
                        "account_id": account_id,
                        "code": code,
                        "name": "赢家" if winner else "输家",
                        "open": price,
                        "high": price * 1.01,
                        "low": price * 0.99,
                        "close": price,
                        "volume": 1_000_000.0,
                        "avg_amount_20": 100_000_000.0,
                        "unbiased_universe": True,
                        "universe_quality": "available",
                        "industry": "行业A" if winner else "行业B",
                    }
                    if market == "a_share":
                        row.update({
                            "membership_source": "monthly_index_weight",
                            "pe": 10.0 if winner else 100.0,
                            "roe": 20.0 if winner else 1.0,
                            "daily_basic_trade_date": trade_date,
                            "fundamental_available_date": trade_date,
                            "fundamental_period_end": trade_date,
                            "fundamental_restatement_policy": "latest_revision_visible_on_announcement_date",
                            "security_status": "listed",
                            "is_st": False,
                            "is_suspended": False,
                        })
                    else:
                        row.update({
                            "membership_source": "tushare_fund_basic_listing_interval",
                            "momentum_20": 1.0 if winner else -1.0,
                            "momentum_60": 1.0 if winner else -1.0,
                            "low_volatility_60": 0.1,
                            "discount_premium": 0.0,
                            "unit_nav": price,
                            "tracking_error_20": 0.01,
                            "index_key": "winner_index" if winner else "loser_index",
                            "theme": "全球股票",
                        })
                    rows.append(row)
                    labels.append({
                        "trade_date": trade_date,
                        "account_id": account_id,
                        "code": code,
                        "horizon": horizon,
                        "entry_date": trade_date,
                        "entry_price": price,
                        "benchmark_entry_price": 100.0,
                    })
            return pd.DataFrame(rows), pd.DataFrame(labels)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs = (
                RuleCoreSpec(
                    market="a_share", horizon=3,
                    intended_overlay="configs/a_intended.json",
                    control_overlay="configs/a_control.json",
                    baseline="configs/a_baseline.json",
                    expected_account_sizes=(("a_scope", 2),),
                ),
                RuleCoreSpec(
                    market="cn_qdii_etf", horizon=10,
                    intended_overlay="configs/q_intended.json",
                    control_overlay="configs/q_control.json",
                    baseline="configs/q_baseline.json",
                ),
            )
            (root / "configs").mkdir(parents=True)
            a_intended = {
                "strategy_id": "a-intended", "name": "A intended",
                "factors": {
                    "pe": {"weight": 0.5, "direction": "low"},
                    "roe": {"weight": 0.5, "direction": "high"},
                },
                "factor_processing": {"min_factor_coverage": 1.0, "neutralize_industry": False},
                "portfolio_controls": {"max_industry_weight": 1.0},
                "filters": {"require_fields": ["pe", "roe"]},
            }
            a_control = {
                **a_intended, "strategy_id": "a-control", "name": "A control",
                "factors": {
                    "pe": {"weight": 0.5, "direction": "high"},
                    "missing_control_factor": {"weight": 0.5, "direction": "low"},
                },
                "filters": {"require_fields": ["pe", "missing_control_factor"]},
            }
            q_intended = {
                "strategy_id": "q-intended", "name": "Q intended",
                "factors": {"momentum_60": {"weight": 1.0, "direction": "high"}},
                "factor_processing": {"min_factor_coverage": 1.0, "neutralize_industry": False},
                "portfolio_controls": {"max_industry_weight": 1.0},
                "filters": {},
            }
            q_control = {
                **q_intended, "strategy_id": "q-control", "name": "Q control",
                "factors": {"momentum_60": {"weight": 1.0, "direction": "low"}},
            }
            baseline = lambda account: {
                "accounts": [{"id": account, "cash": 100_000.0, "top_n": 1}],
                "trading": {
                    "lot_size": 100, "max_single_weight": 1.0,
                    "commission_rate": 0.0001, "min_commission": 0.0,
                    "stamp_tax_rate": 0.0, "slippage_rate": 0.0001,
                },
                "performance": {"risk_free_rate": 0.02, "trading_days_per_year": 252},
            }
            payloads = {
                "a_intended.json": a_intended, "a_control.json": a_control,
                "q_intended.json": q_intended, "q_control.json": q_control,
                "a_baseline.json": baseline("a_scope"),
                "q_baseline.json": baseline("q_scope"),
            }
            for name, payload in payloads.items():
                (root / "configs" / name).write_text(json.dumps(payload), encoding="utf-8")
            for market, account, horizon in (
                ("a_share", "a_scope", 3), ("cn_qdii_etf", "q_scope", 10),
            ):
                features, labels = market_frames(market, account, horizon)
                feature_path = root / "data" / "research" / "features" / market / "20260807.parquet"
                label_path = root / "data" / "research" / "labels" / market / "20260807.parquet"
                feature_path.parent.mkdir(parents=True, exist_ok=True)
                label_path.parent.mkdir(parents=True, exist_ok=True)
                features.to_parquet(feature_path, index=False)
                labels.to_parquet(label_path, index=False)
            output = root / "artifacts"

            with patch(
                "stock_analyze.research.rule_core_diagnostic.RULE_CORE_SPECS",
                specs,
            ), patch(
                "stock_analyze.research.rule_core_diagnostic.TRADING_DATE_DENSITY_FLOOR",
                0.0,
            ):
                result = run_rule_core_diagnostic(
                    root, as_of="20260807", output_root=output,
                )

            self.assertEqual(result["status"], "ready_for_stage2")
            self.assertEqual(result["decision"]["a_share"], "proceed")
            self.assertEqual(result["decision"]["cn_qdii_etf"], "proceed")
            for name in (
                "data_audit.json", "a_share_intended.json", "a_share_controls.json",
                "qdii_intended.json", "qdii_controls.json", "nav.parquet",
                "trades.parquet", "attribution.parquet", "decision.json",
                "model_gate_diagnostics.json", "report.md",
            ):
                self.assertTrue((output / name).exists(), name)
            controls = json.loads((output / "qdii_controls.json").read_text())
            self.assertEqual(
                [item["control_id"] for item in controls["controls"]],
                ["alternate_overlay", "one_over_n", "benchmark"],
            )
            a_controls = json.loads((output / "a_share_controls.json").read_text())
            alternate = a_controls["controls"][0]
            self.assertEqual(alternate["status"], "not_run_data_blocked")
            self.assertEqual(alternate["metrics"], {})
            self.assertFalse(alternate["audit"]["passes"])
            self.assertIn(
                "factor_point_in_time_coverage_below_floor:missing_control_factor",
                alternate["audit"]["reasons"],
            )
            attribution = pd.read_parquet(output / "attribution.parquet")
            self.assertIn("gross_active_return", attribution.columns)
            self.assertLess(attribution["reconciliation_error"].abs().max(), 1e-10)
            self.assertFalse((output / "cn_qdii_etf_intended.json").exists())


if __name__ == "__main__":
    unittest.main()
