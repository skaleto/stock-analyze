"""Versioned feature declarations used to reproduce research snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    family: str
    lookback: int
    availability_lag: int
    version: str
    description: str = ""
    direction: str = "contextual"
    markets: tuple[str, ...] = ("a_share", "cn_qdii_etf")
    source: str = "derived"


def registry_hash(definitions: Iterable[FeatureDefinition]) -> str:
    payload = [asdict(item) for item in sorted(definitions, key=lambda item: item.name)]
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


TECHNICAL_FEATURES = tuple(
    FeatureDefinition(
        name,
        "technical",
        lookback,
        0,
        "technical-v2",
        direction=direction,
        source="adjusted_ohlcv",
    )
    for name, lookback, direction in (
        ("sma_5", 5, "contextual"), ("sma_10", 10, "contextual"),
        ("sma_20", 20, "contextual"), ("sma_60", 60, "contextual"),
        ("ema_12", 12, "contextual"), ("ema_26", 26, "contextual"),
        ("macd_dif", 35, "high"), ("macd_dea", 35, "high"),
        ("macd_hist", 35, "high"), ("macd_cross", 36, "high"),
        ("macd_hist_slope", 36, "high"), ("macd_hist_acceleration", 37, "high"),
        ("macd_zero_state", 35, "high"), ("macd_cross_age", 36, "contextual"),
        ("rsi_14", 14, "contextual"), ("adx_14", 28, "high"),
        ("atr_14", 14, "low"), ("natr_14", 14, "low"),
        ("bollinger_position", 20, "contextual"), ("bollinger_width", 20, "contextual"),
        ("return_1", 1, "high"), ("momentum_5", 5, "high"),
        ("momentum_20", 20, "high"), ("momentum_60", 60, "high"),
        ("realized_volatility_20", 20, "low"), ("relative_strength_20", 20, "high"),
        ("volume_ratio_5_20", 20, "contextual"), ("volume_zscore_20", 20, "contextual"),
        ("obv", 1, "high"), ("ad", 1, "high"), ("mfi_14", 14, "contextual"),
        ("amount_ratio_5_20", 20, "contextual"),
        ("turnover_percentile_60", 60, "contextual"),
        ("turnover_change_5", 5, "contextual"),
    )
)


FUNDAMENTAL_FEATURES = tuple(
    FeatureDefinition(
        name,
        "fundamental",
        lookback,
        1,
        "fundamental-pit-v2",
        description=description,
        direction=direction,
        markets=("a_share",),
        source="tushare_financial_announced",
    )
    for name, lookback, direction, description in (
        ("pe_ttm", 0, "low", "Trailing earnings valuation"),
        ("pb", 0, "low", "Book value valuation"),
        ("roe", 0, "high", "Return on equity"),
        ("gross_margin", 0, "high", "Gross margin"),
        ("roic", 0, "high", "Return on invested capital"),
        ("net_profit_margin", 0, "high", "Net profit margin"),
        ("debt_ratio", 0, "low", "Balance-sheet leverage"),
        ("revenue_growth", 0, "high", "Revenue growth"),
        ("profit_growth", 0, "high", "Profit growth"),
        ("cash_conversion", 0, "high", "Operating cash conversion"),
        ("accrual_ratio", 0, "low", "Accrual intensity"),
        ("asset_turnover", 0, "high", "Asset turnover"),
        ("current_ratio", 0, "high", "Current liquidity ratio"),
        ("quick_ratio", 0, "high", "Quick liquidity ratio"),
        ("operating_margin", 0, "high", "Operating margin"),
        ("operating_profit_growth", 0, "high", "Operating profit growth"),
        ("operating_cashflow_growth", 0, "high", "Operating cash-flow growth"),
        ("operating_leverage_proxy", 0, "contextual", "Operating leverage proxy"),
        ("growth_acceleration", 0, "high", "Growth acceleration"),
        ("earnings_stability", 8, "high", "Rolling earnings stability"),
        ("rd_intensity", 0, "high", "Research and development intensity"),
        ("free_cashflow_to_assets", 0, "high", "Free cash flow to assets"),
        ("gross_profit_to_assets", 0, "high", "Gross profit to assets"),
    )
)


INDUSTRY_CHAIN_FEATURES = tuple(
    FeatureDefinition(
        name,
        "industry_chain",
        lookback,
        1,
        "industry-chain-v2",
        description=description,
        direction=direction,
        markets=("a_share",),
        source="announced_financials_and_industry_membership",
    )
    for name, lookback, direction, description in (
        ("profit_pool_concentration", 0, "contextual", "Concentration of segment profit pools"),
        ("largest_business_share", 0, "contextual", "Largest reported business share"),
        ("business_profit_margin", 0, "high", "Main business profit margin"),
        ("high_value_add_proxy", 0, "high", "Composite value-add and pricing-power proxy"),
        ("declining_marginal_cost_proxy", 0, "high", "Scale economics and marginal-cost decline proxy"),
        ("pricing_power_persistence", 8, "high", "Persistence of gross-margin pricing power"),
        ("industry_relative_momentum_20", 20, "high", "Momentum relative to industry peers"),
        ("industry_momentum_20", 20, "high", "Industry aggregate momentum"),
        ("industry_volatility_20", 20, "low", "Industry aggregate volatility"),
        ("industry_breadth", 1, "high", "Share of industry constituents rising"),
        ("industry_profitability", 0, "high", "Industry aggregate profitability"),
        ("industry_earnings_diffusion", 0, "high", "Breadth of improving industry earnings"),
        ("industry_cycle_score", 20, "high", "Continuous industry cycle score"),
    )
)


QDII_FEATURES = tuple(
    FeatureDefinition(
        name,
        "qdii",
        lookback,
        lag,
        "qdii-pit-v2",
        direction=direction,
        markets=("cn_qdii_etf",),
        source=source,
    )
    for name, lookback, lag, direction, source in (
        ("discount_premium", 1, 1, "low", "fund_nav"),
        ("premium_persistence_20", 20, 1, "low", "fund_nav"),
        ("tracking_difference_20", 20, 1, "low", "fund_nav"),
        ("tracking_error_20", 20, 1, "low", "fund_nav"),
        ("fund_share_change_20", 20, 0, "high", "fund_share"),
        ("nav_momentum_20", 20, 1, "high", "fund_nav"),
        ("global_index_momentum", 20, 1, "high", "global_index"),
        ("global_volatility", 20, 1, "low", "global_index"),
        ("rmb_depreciation", 20, 1, "contextual", "fx_daily"),
    )
)


MACRO_FEATURES = tuple(
    FeatureDefinition(
        name,
        "macro_regime",
        20,
        lag,
        "macro-pit-v2",
        direction="contextual",
        source=source,
    )
    for name, lag, source in (
        ("pmi_change", 1, "cn_pmi"), ("m2_change", 15, "cn_m"),
        ("cpi_change", 10, "cn_cpi"), ("ppi_change", 10, "cn_ppi"),
        ("yield_curve_slope", 1, "us_tycr"), ("shibor_change", 1, "shibor"),
        ("us_yield_change", 1, "us_tycr"),
    )
)


DEFAULT_REGISTRY = (
    TECHNICAL_FEATURES
    + FUNDAMENTAL_FEATURES
    + INDUSTRY_CHAIN_FEATURES
    + QDII_FEATURES
    + MACRO_FEATURES
)
DEFAULT_REGISTRY_HASH = registry_hash(DEFAULT_REGISTRY)
