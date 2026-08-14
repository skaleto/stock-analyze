"""Compact stationary feature views for one executable account scope."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AccountFeatureContract:
    market: str
    account_scope: str
    horizon: int
    allowed_features: tuple[str, ...]
    max_features: int = 12
    max_per_family: int = 3
    minimum_coverage: float = 0.70


_A_SHARE_FEATURES = (
    "account_residual_momentum_20",
    "account_residual_momentum_60",
    "industry_residual_momentum_20",
    "account_low_volatility_percentile",
    "account_liquidity_percentile",
    "account_quality_percentile",
    "momentum_20",
    "momentum_60",
    "natr_14",
    "realized_volatility_20",
    "amount_ratio_5_20",
    "turnover_percentile_60",
    "roe",
    "roic",
    "cash_conversion",
    "accrual_ratio",
    "free_cashflow_to_assets",
    "gross_profit_to_assets",
    "pe_ttm",
    "pb",
    "industry_breadth",
    "industry_relative_momentum_20",
    "industry_cycle_score",
    "sma_distance_20",
    "macd_hist_slope_pct",
)

_A_SHARE_H20_FEATURES = (
    "momentum_20",
    "momentum_60",
    "momentum_120",
    "sma_distance_200",
    "account_low_volatility_percentile",
    "account_liquidity_percentile",
    "account_quality_percentile",
    "realized_volatility_20",
    "natr_14",
    "roe",
    "roic",
    "cash_conversion",
    "accrual_ratio",
    "free_cashflow_to_assets",
    "gross_profit_to_assets",
    "pe_ttm",
    "pb",
)

_QDII_FEATURES = (
    "account_residual_momentum_20",
    "account_residual_momentum_60",
    "account_low_volatility_percentile",
    "account_liquidity_percentile",
    "discount_premium",
    "premium_persistence_20",
    "tracking_difference_20",
    "tracking_error_20",
    "nav_momentum_20",
    "global_index_momentum",
    "global_volatility",
    "rmb_depreciation",
    "sma_distance_20",
    "momentum_60",
    "momentum_120",
    "sma_distance_200",
    "natr_14",
)


_ALPHA158_LITE_STOCK_FEATURES = (
    "return_1",
    "momentum_5", "momentum_10", "momentum_20", "momentum_60", "momentum_120",
    "reversal_5",
    "sma_distance_5", "sma_distance_10", "sma_distance_20", "sma_distance_60",
    "sma_distance_120", "ema_distance_12", "ema_distance_26",
    "macd_dif_pct", "macd_dea_pct", "macd_hist_pct", "macd_hist_slope_pct",
    "rsi_14", "adx_14", "natr_14", "bollinger_position", "bollinger_width",
    "realized_volatility_5", "realized_volatility_20", "realized_volatility_60",
    "downside_volatility_20", "intraday_range", "close_location",
    "drawdown_60", "breakout_20", "relative_strength_20",
    "volume_ratio_5_20", "volume_zscore_20", "obv_flow_5", "ad_flow_5", "mfi_14",
    "amount_ratio_5_20", "amount_zscore_20", "amihud_illiquidity_20",
    "volume_price_correlation_20", "up_volume_ratio_20",
    "turnover_percentile_60", "turnover_change_5", "turnover_change_20",
    "price_volume_confirmation_20",
    "roe", "roic", "gross_margin", "net_profit_margin", "debt_ratio",
    "revenue_growth", "profit_growth", "cash_conversion", "accrual_ratio",
    "free_cashflow_to_assets", "gross_profit_to_assets", "pe_ttm", "pb",
    "industry_relative_momentum_20", "industry_breadth", "industry_cycle_score",
)

_ALPHA158_LITE_MONEYFLOW_FEATURES = (
    "moneyflow_net_ratio_1",
    "moneyflow_net_ratio_5",
    "moneyflow_net_ratio_20",
    "moneyflow_positive_days_5",
    "moneyflow_large_imbalance_5",
)

_ALPHA158_LITE_INDUSTRY_RANK_FEATURES = (
    "momentum_20", "momentum_60", "realized_volatility_20",
    "price_volume_confirmation_20", "roe", "cash_conversion",
    "gross_profit_to_assets", "pe_ttm", "pb",
)

_ALPHA158_LITE_MISSING_FEATURES = (
    "roe", "roic", "gross_margin", "net_profit_margin", "debt_ratio",
    "revenue_growth", "profit_growth", "cash_conversion", "accrual_ratio",
    "free_cashflow_to_assets", "gross_profit_to_assets", "pe_ttm", "pb",
)

_ALPHA158_LITE_MARKET_FEATURES = (
    "market_breadth_1",
    "market_median_momentum_20",
    "market_median_momentum_60",
    "market_cross_sectional_volatility_1",
    "market_median_realized_volatility_20",
    "market_drawdown_breadth_60",
)


def account_feature_contract(
    market: str,
    account_scope: str,
    horizon: int,
) -> AccountFeatureContract:
    if market == "a_share":
        allowed = _A_SHARE_H20_FEATURES if int(horizon) == 20 else _A_SHARE_FEATURES
    elif market == "cn_qdii_etf":
        allowed = _QDII_FEATURES
    else:
        raise ValueError(f"account_feature_market_unknown:{market}")
    return AccountFeatureContract(
        market=str(market),
        account_scope=str(account_scope),
        horizon=int(horizon),
        allowed_features=tuple(allowed),
    )


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _daily_percentile(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    return values.groupby(frame["trade_date"].astype(str)).rank(
        pct=True,
        method="average",
    )


def _centered_rank(values: pd.Series, groupers: list[pd.Series]) -> pd.Series:
    ranked = values.groupby(groupers, sort=False).rank(pct=True, method="average")
    return ranked - ranked.groupby(groupers, sort=False).transform("mean")


def date_balanced_sample_weights(frame: pd.DataFrame) -> pd.Series:
    """Give every trade date equal total estimator weight."""

    if "trade_date" not in frame.columns:
        raise ValueError("date_balanced_weights_missing_trade_date")
    dates = frame["trade_date"].astype(str)
    counts = dates.groupby(dates).transform("size").astype(float)
    if counts.empty or bool(counts.le(0).any()):
        raise ValueError("date_balanced_weights_empty")
    return pd.Series(1.0 / counts.to_numpy(), index=frame.index, dtype=float)


def build_account_feature_view(
    frame: pd.DataFrame,
    *,
    account_scope: str,
) -> pd.DataFrame:
    scope_column = (
        "research_scope"
        if "research_scope" in frame.columns
        else "account_id" if "account_id" in frame.columns else ""
    )
    if not scope_column:
        raise ValueError("account_feature_scope_missing")
    normalized_scope = str(account_scope).strip()
    scoped = frame.loc[
        frame[scope_column].astype(str).eq(normalized_scope)
    ].copy()
    if scoped.empty:
        raise ValueError("account_feature_scope_mismatch")
    scoped["trade_date"] = scoped["trade_date"].astype(str)
    scoped["code"] = scoped["code"].astype(str).str.zfill(6)
    scoped = scoped.sort_values(["code", "trade_date"], kind="stable").reset_index(
        drop=True
    )
    close = _numeric(scoped, "close")
    scoped["sma_200"] = close.groupby(scoped["code"], sort=False).transform(
        lambda values: values.rolling(200, min_periods=200).mean()
    )
    scoped["sma_distance_200"] = close / scoped["sma_200"] - 1.0
    daily = scoped.groupby("trade_date", sort=False)
    momentum_20 = _numeric(scoped, "momentum_20")
    momentum_60 = _numeric(scoped, "momentum_60")
    scoped["account_residual_momentum_20"] = (
        momentum_20 - momentum_20.groupby(scoped["trade_date"]).transform("mean")
    )
    scoped["account_residual_momentum_60"] = (
        momentum_60 - momentum_60.groupby(scoped["trade_date"]).transform("mean")
    )
    if "industry" in scoped.columns:
        industry_median = momentum_20.groupby(
            [scoped["trade_date"], scoped["industry"].fillna("unclassified").astype(str)]
        ).transform("median")
        scoped["industry_residual_momentum_20"] = momentum_20 - industry_median
    else:
        scoped["industry_residual_momentum_20"] = scoped[
            "account_residual_momentum_20"
        ]
    volatility = _numeric(scoped, "realized_volatility_20")
    scoped["account_low_volatility_percentile"] = 1.0 - _daily_percentile(
        scoped,
        volatility,
    )
    liquidity_source = "avg_amount_20" if "avg_amount_20" in scoped.columns else "amount"
    scoped["account_liquidity_percentile"] = _daily_percentile(
        scoped,
        _numeric(scoped, liquidity_source),
    )
    quality_columns = [
        column
        for column in ("roe", "roic", "cash_conversion", "gross_profit_to_assets")
        if column in scoped.columns
    ]
    if quality_columns:
        quality_ranks = pd.concat(
            [
                _daily_percentile(scoped, _numeric(scoped, column)).rename(column)
                for column in quality_columns
            ],
            axis=1,
        )
        scoped["account_quality_percentile"] = quality_ranks.mean(axis=1, skipna=True)
    else:
        scoped["account_quality_percentile"] = np.nan
    del daily
    return scoped.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)


def build_alpha158_lite_feature_view(
    frame: pd.DataFrame,
    *,
    account_scope: str,
) -> pd.DataFrame:
    """Build stationary stock, industry, and market-state inputs by date."""

    scoped = build_account_feature_view(frame, account_scope=account_scope)
    dates = scoped["trade_date"].astype(str)
    for column in (
        *_ALPHA158_LITE_STOCK_FEATURES,
        *_ALPHA158_LITE_MONEYFLOW_FEATURES,
    ):
        if column not in scoped.columns:
            continue
        scoped[f"{column}_cs_rank"] = _centered_rank(
            _numeric(scoped, column),
            [dates],
        )

    total_mv = _numeric(scoped, "total_mv").where(lambda values: values > 0.0)
    scoped["log_total_mv"] = np.log(total_mv)
    scoped["log_total_mv_cs_rank"] = _centered_rank(
        scoped["log_total_mv"],
        [dates],
    )

    industries = scoped.get(
        "industry",
        pd.Series("unclassified", index=scoped.index, dtype="string"),
    ).fillna("unclassified").astype(str)
    for column in _ALPHA158_LITE_INDUSTRY_RANK_FEATURES:
        if column not in scoped.columns:
            continue
        scoped[f"{column}_industry_rank"] = _centered_rank(
            _numeric(scoped, column),
            [dates, industries],
        )

    returns = _numeric(scoped, "return_1")
    valid_returns = returns.notna()
    positive = returns.gt(0.0).where(valid_returns)
    scoped["market_breadth_1"] = positive.groupby(dates).transform("mean")
    scoped["market_median_momentum_20"] = _numeric(
        scoped, "momentum_20"
    ).groupby(dates).transform("median")
    scoped["market_median_momentum_60"] = _numeric(
        scoped, "momentum_60"
    ).groupby(dates).transform("median")
    scoped["market_cross_sectional_volatility_1"] = returns.groupby(dates).transform(
        "std"
    )
    scoped["market_median_realized_volatility_20"] = _numeric(
        scoped, "realized_volatility_20"
    ).groupby(dates).transform("median")
    drawdown = _numeric(scoped, "drawdown_60")
    scoped["market_drawdown_breadth_60"] = drawdown.lt(-0.10).where(
        drawdown.notna()
    ).groupby(dates).transform("mean")

    for column in (
        *_ALPHA158_LITE_MISSING_FEATURES,
        "moneyflow_net_ratio_20",
    ):
        scoped[f"{column}_missing"] = _numeric(scoped, column).isna().astype(float)
    return scoped.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)


def alpha158_lite_feature_columns(
    frame: pd.DataFrame,
    *,
    feature_set: str = "alpha158_lite_v1",
) -> tuple[str, ...]:
    """Return the deterministic model input contract present in ``frame``."""

    if feature_set not in {"alpha158_lite_v1", "alpha158_lite_moneyflow_v2"}:
        raise ValueError(f"alpha158_lite_feature_set_unknown:{feature_set}")
    stock_features = list(_ALPHA158_LITE_STOCK_FEATURES)
    missing_features = list(_ALPHA158_LITE_MISSING_FEATURES)
    if feature_set == "alpha158_lite_moneyflow_v2":
        stock_features.extend(_ALPHA158_LITE_MONEYFLOW_FEATURES)
        missing_features.append("moneyflow_net_ratio_20")
    candidates = [
        *(f"{column}_cs_rank" for column in stock_features),
        "log_total_mv_cs_rank",
        *(f"{column}_industry_rank" for column in _ALPHA158_LITE_INDUSTRY_RANK_FEATURES),
        *_ALPHA158_LITE_MARKET_FEATURES,
        *(f"{column}_missing" for column in missing_features),
    ]
    return tuple(column for column in candidates if column in frame.columns)


__all__ = [
    "AccountFeatureContract",
    "account_feature_contract",
    "alpha158_lite_feature_columns",
    "build_alpha158_lite_feature_view",
    "build_account_feature_view",
    "date_balanced_sample_weights",
]
