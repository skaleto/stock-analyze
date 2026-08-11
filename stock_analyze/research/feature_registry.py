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
        "technical-v3-stationary",
        direction=direction,
        source="adjusted_ohlcv",
    )
    for name, lookback, direction in (
        ("sma_distance_5", 5, "contextual"), ("sma_distance_10", 10, "contextual"),
        ("sma_distance_20", 20, "contextual"), ("sma_distance_60", 60, "contextual"),
        ("ema_distance_12", 12, "contextual"), ("ema_distance_26", 26, "contextual"),
        ("macd_dif_pct", 35, "high"), ("macd_dea_pct", 35, "high"),
        ("macd_hist_pct", 35, "high"), ("macd_cross", 36, "high"),
        ("macd_hist_slope_pct", 36, "high"), ("macd_hist_acceleration_pct", 37, "high"),
        ("macd_zero_state", 35, "high"), ("macd_cross_age", 36, "contextual"),
        ("rsi_14", 14, "contextual"), ("adx_14", 28, "high"),
        ("natr_14", 14, "low"),
        ("bollinger_position", 20, "contextual"), ("bollinger_width", 20, "contextual"),
        ("return_1", 1, "high"), ("momentum_5", 5, "high"),
        ("momentum_20", 20, "high"), ("momentum_60", 60, "high"),
        ("realized_volatility_20", 20, "low"), ("relative_strength_20", 20, "high"),
        ("volume_ratio_5_20", 20, "contextual"), ("volume_zscore_20", 20, "contextual"),
        ("obv_flow_5", 5, "high"), ("ad_flow_5", 5, "high"), ("mfi_14", 14, "contextual"),
        ("amount_ratio_5_20", 20, "contextual"),
        ("turnover_percentile_60", 60, "contextual"),
        ("turnover_change_5", 5, "contextual"),
    )
)


ALPHA158_LITE_TECHNICAL_FEATURES = tuple(
    FeatureDefinition(
        name,
        "technical",
        lookback,
        0,
        "alpha158-lite-technical-v1",
        description=description,
        direction=direction,
        source="adjusted_ohlcv",
    )
    for name, lookback, direction, description in (
        ("momentum_10", 10, "high", "Ten-session price momentum"),
        ("momentum_120", 120, "high", "Long-horizon price momentum"),
        ("reversal_5", 5, "high", "Short-horizon reversal"),
        ("realized_volatility_5", 5, "low", "Short realized volatility"),
        ("realized_volatility_60", 60, "low", "Slow realized volatility"),
        ("downside_volatility_20", 20, "low", "Downside realized volatility"),
        ("intraday_range", 1, "low", "Scale-free intraday price range"),
        ("close_location", 1, "contextual", "Close location inside daily range"),
        ("drawdown_60", 60, "high", "Distance from trailing sixty-session high"),
        ("breakout_20", 20, "high", "Breakout above prior twenty-session high"),
        ("amount_zscore_20", 20, "contextual", "Abnormal traded amount"),
        ("amihud_illiquidity_20", 60, "low", "Relative Amihud-style illiquidity"),
        ("volume_price_correlation_20", 20, "high", "Price-volume confirmation"),
        ("up_volume_ratio_20", 20, "high", "Share of volume on positive sessions"),
        ("turnover_change_20", 20, "contextual", "Turnover versus trailing mean"),
        ("price_volume_confirmation_20", 20, "high", "Momentum times amount surprise"),
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


ACCOUNT_RELATIVE_FEATURES = tuple(
    FeatureDefinition(
        name,
        family,
        lookback,
        0,
        "account-relative-v1",
        description=description,
        direction=direction,
        source="scope_cross_section",
    )
    for name, family, lookback, direction, description in (
        (
            "account_residual_momentum_20",
            "residual_momentum",
            20,
            "high",
            "Momentum relative to the executable account cross-section.",
        ),
        (
            "account_residual_momentum_60",
            "residual_momentum",
            60,
            "high",
            "Slow momentum relative to the executable account cross-section.",
        ),
        (
            "industry_residual_momentum_20",
            "residual_momentum",
            20,
            "high",
            "Momentum relative to same-day industry peers.",
        ),
        (
            "account_low_volatility_percentile",
            "low_volatility",
            20,
            "high",
            "Inverse volatility percentile within the account scope.",
        ),
        (
            "account_liquidity_percentile",
            "liquidity",
            20,
            "high",
            "Liquidity percentile within the account scope.",
        ),
        (
            "account_quality_percentile",
            "quality",
            0,
            "high",
            "Composite point-in-time quality percentile within the account scope.",
        ),
    )
)


MONEYFLOW_FEATURES = tuple(
    FeatureDefinition(
        name,
        "fund_flow",
        lookback,
        0,
        "moneyflow-pit-v1",
        description=description,
        direction=direction,
        markets=("a_share",),
        source="tushare_moneyflow",
    )
    for name, lookback, direction, description in (
        (
            "moneyflow_net_ratio_1",
            1,
            "high",
            "Same-day active net inflow divided by traded amount.",
        ),
        (
            "moneyflow_net_ratio_5",
            5,
            "high",
            "Five-session active net inflow divided by traded amount.",
        ),
        (
            "moneyflow_net_ratio_20",
            20,
            "high",
            "Twenty-session active net inflow divided by traded amount.",
        ),
        (
            "moneyflow_positive_days_5",
            5,
            "high",
            "Share of observed sessions with positive active net inflow.",
        ),
        (
            "moneyflow_large_imbalance_5",
            5,
            "high",
            "Five-session large-order active buy-sell imbalance.",
        ),
        (
            "moneyflow_observed",
            1,
            "contextual",
            "Explicit exact-date money-flow source coverage flag.",
        ),
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


INTELLIGENCE_FEATURES = tuple(
    FeatureDefinition(
        name,
        "market_intelligence",
        lookback,
        0,
        "intelligence-v1",
        description=description,
        direction="contextual",
        source="point_in_time_event_store",
    )
    for name, lookback, description in (
        ("event_positive_decay_5d", 20, "Confidence-weighted positive event decay."),
        ("event_negative_decay_5d", 20, "Confidence-weighted negative event decay."),
        ("announcement_novelty_20d", 20, "Recent event novelty and strength."),
        ("policy_industry_exposure_20d", 60, "Industry and macro policy exposure."),
        ("news_volume_abnormal_20d", 20, "Log-scaled recent event volume."),
        ("event_source_confirmation", 20, "Independent-source confirmation ratio."),
        ("event_price_volume_confirmation", 20, "Event direction confirmed by price and volume."),
        ("event_data_coverage", 1, "Explicit event-source coverage flag."),
        ("event_relevance_20d", 20, "Decay-weighted semantic event relevance."),
        ("event_materiality_positive_20d", 20, "Positive event materiality."),
        ("event_materiality_negative_20d", 20, "Negative event materiality."),
        ("event_certainty_20d", 20, "Evidence-weighted event certainty."),
        ("event_revision_risk_20d", 20, "Revision, uncertainty, and cancellation risk."),
        ("earnings_event_score_20d", 20, "Earnings forecast and flash event score."),
        ("buyback_event_score_20d", 20, "Share buyback event score."),
        ("shareholder_flow_event_score_20d", 20, "Shareholder flow event score."),
        ("contract_event_score_60d", 60, "Major contract event score."),
        ("corporate_action_event_score_60d", 60, "Corporate action event score."),
        ("legal_risk_event_score_60d", 60, "Legal and enforcement risk event score."),
        ("delisting_risk_event_score_60d", 60, "Risk-warning and delisting event score."),
        ("capital_structure_event_score_60d", 60, "Capital structure event score."),
        ("event_net_strength_5d", 20, "Net positive-minus-negative event decay."),
        ("event_net_materiality_20d", 20, "Net positive-minus-negative semantic materiality."),
    )
)


DEFAULT_REGISTRY = (
    TECHNICAL_FEATURES
    + ALPHA158_LITE_TECHNICAL_FEATURES
    + FUNDAMENTAL_FEATURES
    + INDUSTRY_CHAIN_FEATURES
    + QDII_FEATURES
    + ACCOUNT_RELATIVE_FEATURES
    + MONEYFLOW_FEATURES
    + MACRO_FEATURES
    + INTELLIGENCE_FEATURES
)
DEFAULT_REGISTRY_HASH = registry_hash(DEFAULT_REGISTRY)
