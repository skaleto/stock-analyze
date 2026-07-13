"""Prediction records, confidence scoring, and interpretable reasons."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .models import ModelBundle
from .schemas import PredictionRecord


_FEATURE_LABELS = {
    "sma_5": "5日均线", "sma_10": "10日均线", "sma_20": "20日均线", "sma_60": "60日均线",
    "ema_12": "12日指数均线", "ema_26": "26日指数均线",
    "macd_dif": "MACD快线", "macd_dea": "MACD慢线", "macd_hist": "MACD柱",
    "macd_cross": "MACD交叉", "macd_hist_slope": "MACD柱变化",
    "macd_hist_acceleration": "MACD柱加速度", "macd_zero_state": "MACD零轴位置",
    "macd_cross_age": "MACD交叉距今天数", "rsi_14": "RSI强弱", "adx_14": "ADX趋势强度",
    "atr_14": "真实波幅", "natr_14": "标准化波幅", "bollinger_position": "布林带位置",
    "bollinger_width": "布林带宽度", "return_1": "单日涨跌", "momentum_5": "5日动量",
    "momentum_20": "20日动量", "momentum_60": "60日动量",
    "realized_volatility_20": "20日波动率", "price_slope_5": "5日价格斜率",
    "gap_return": "跳空幅度", "relative_strength_20": "20日相对强弱",
    "volume_ratio_5_20": "短中期量比", "volume_zscore_20": "成交量异常度",
    "obv": "能量潮OBV", "ad": "累积派发指标", "mfi_14": "资金流量指标MFI",
    "amount_ratio_5_20": "成交额量比", "turnover_percentile_60": "换手率分位",
    "turnover_change_5": "换手率变化", "pe_ttm": "滚动市盈率", "pb": "市净率",
    "roe": "净资产收益率", "gross_margin": "毛利率", "roic": "投入资本回报率",
    "net_profit_margin": "净利率", "debt_ratio": "资产负债率", "revenue_growth": "收入增速",
    "profit_growth": "利润增速", "cash_conversion": "经营现金转化率", "accrual_ratio": "应计比率",
    "high_value_add_proxy": "高附加值代理", "declining_marginal_cost_proxy": "边际成本递减代理",
    "profit_pool_concentration": "业务集中度", "industry_relative_momentum_20": "行业相对动量",
    "industry_breadth": "行业上涨宽度", "industry_cycle_score": "行业周期得分",
    "discount_premium": "折溢价率", "premium_persistence_20": "20日平均折溢价",
    "tracking_difference_20": "20日跟踪差", "tracking_error_20": "20日跟踪误差",
    "fund_share_change_20": "20日基金份额变化", "global_index_momentum": "全球指数动量",
    "global_volatility": "全球市场波动", "rmb_depreciation": "人民币汇率变化",
    "pmi_change": "PMI变化", "m2_change": "M2增速变化", "cpi_change": "CPI变化",
    "ppi_change": "PPI变化", "yield_curve_slope": "收益率曲线斜率", "shibor_change": "Shibor变化",
    "us_yield_change": "美债收益率变化",
}


def compute_confidence(
    *,
    calibration_quality: float,
    sample_support: int,
    model_agreement: float,
    data_quality: float,
    regime_stability: float,
) -> float:
    support_score = min(1.0, max(0.0, sample_support / 500.0))
    confidence = (
        0.30 * calibration_quality
        + 0.20 * support_score
        + 0.20 * model_agreement
        + 0.15 * data_quality
        + 0.15 * regime_stability
    )
    if sample_support < 100:
        confidence = min(confidence, 0.49)
    return float(np.clip(confidence, 0.0, 1.0))


def _reason_text(contributions: list[tuple[str, float]]) -> tuple[str, ...]:
    reasons = []
    for name, value in contributions[:3]:
        direction = "正向" if value >= 0 else "负向"
        reasons.append(f"{_FEATURE_LABELS.get(name, name)} {direction}贡献 {abs(value):.3f}")
    return tuple(reasons)


def generate_predictions(
    bundle: ModelBundle,
    features: pd.DataFrame,
    *,
    as_of: str,
    horizon: int,
    regime: str,
    data_quality: float,
    regime_stability: float,
    feature_snapshot_id: str,
    active_status: str = "inactive",
) -> list[PredictionRecord]:
    if horizon != bundle.horizon:
        raise ValueError("prediction_model_horizon")
    probabilities = bundle.predict_proba(features)
    logistic, boosting = bundle.component_probabilities(features)
    records: list[PredictionRecord] = []
    for index, (_, row) in enumerate(features.iterrows()):
        probability_by_class = dict(zip(bundle.class_order, probabilities[index]))
        agreement = float(1.0 - np.abs(logistic[index] - boosting[index]).sum() / 2.0)
        row_quality = float(data_quality) * float(row.loc[list(bundle.feature_columns)].notna().mean())
        confidence = compute_confidence(
            calibration_quality=bundle.metrics.get("calibration_quality", 0.0),
            sample_support=bundle.sample_support,
            model_agreement=agreement,
            data_quality=row_quality,
            regime_stability=regime_stability,
        )
        expected = sum(probability_by_class[name] * bundle.return_stats[name]["mean"] for name in bundle.class_order)
        quantiles = {
            key: sum(probability_by_class[name] * bundle.return_stats[name][key] for name in bundle.class_order)
            for key in ("q10", "q50", "q90")
        }
        code = str(row.get("code", row.get("ts_code", ""))).split(".")[0]
        reasons = _reason_text(bundle.logistic_contributions(row, "up"))
        invalidation = (
            "数据完整度低于 70%" if row_quality < 0.70 else "模型状态或市场状态发生变化",
            "下行概率超过上行概率",
        )
        records.append(
            PredictionRecord(
                code=code,
                as_of=as_of,
                horizon=horizon,
                p_up=float(probability_by_class["up"]),
                p_flat=float(probability_by_class["flat"]),
                p_down=float(probability_by_class["down"]),
                confidence=confidence,
                expected_absolute_return=float(expected),
                expected_excess_return=float(expected),
                return_q10=float(quantiles["q10"]),
                return_q50=float(quantiles["q50"]),
                return_q90=float(quantiles["q90"]),
                regime=regime,
                reasons=reasons,
                invalidation=invalidation,
                model_version=bundle.model_version,
                feature_snapshot_id=feature_snapshot_id,
                active_status=active_status,
                metadata={"model_agreement": agreement, "data_quality": row_quality},
            )
        )
    return records
