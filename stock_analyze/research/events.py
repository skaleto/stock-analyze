"""Deterministic technical, volume-price, flow, and breadth event detection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pandas as pd


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _event_id(market: str, code: str, trade_date: str, event: str) -> str:
    raw = f"{market}|{code}|{trade_date}|{event}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _volume_price_stage(price_return: pd.Series, volume_ratio: pd.Series) -> pd.Series:
    price_up = price_return > 0.005
    price_down = price_return < -0.005
    volume_up = volume_ratio > 1.05
    volume_down = volume_ratio < 0.95
    return pd.Series(
        np.select(
            [
                price_up & volume_up,
                price_up & volume_down,
                price_down & volume_up,
                price_down & volume_down,
                (~price_up & ~price_down) & volume_up,
                (~price_up & ~price_down) & volume_down,
            ],
            [
                "volume_price_rise_confirmed",
                "volume_price_rise_divergent",
                "volume_price_fall_confirmed",
                "volume_price_fall_exhausting",
                "volume_expansion_flat_price",
                "volume_contraction_flat_price",
            ],
            default="volume_price_neutral",
        ),
        index=price_return.index,
    )


def _detect_group(group: pd.DataFrame, market: str) -> list[dict[str, Any]]:
    group = group.sort_values("trade_date").copy()
    close = _series(group, "close")
    macd = _series(group, "macd_dif")
    signal = _series(group, "macd_dea")
    histogram = _series(group, "macd_hist")
    hist_slope = _series(group, "macd_hist_slope")
    ma5 = _series(group, "sma_5")
    ma20 = _series(group, "sma_20")
    rsi = _series(group, "rsi_14")
    adx = _series(group, "adx_14")
    upper = _series(group, "bollinger_upper")
    lower = _series(group, "bollinger_lower")
    width = _series(group, "bollinger_width")
    volume_ratio = _series(group, "volume_ratio_5_20")
    flow = _series(group, "flow_net_large")
    breadth = _series(group, "industry_breadth")
    price_return = close.pct_change(fill_method=None)

    masks: dict[str, pd.Series] = {}
    explicit_cross = _series(group, "macd_cross")
    masks["macd_golden_cross"] = (explicit_cross == 1) | ((macd.shift(1) <= signal.shift(1)) & (macd > signal))
    masks["macd_death_cross"] = (explicit_cross == -1) | ((macd.shift(1) >= signal.shift(1)) & (macd < signal))
    masks["macd_zero_cross_up"] = (macd.shift(1) <= 0) & (macd > 0)
    masks["macd_zero_cross_down"] = (macd.shift(1) >= 0) & (macd < 0)
    masks["macd_hist_reversal_up"] = (hist_slope.shift(1) <= 0) & (hist_slope > 0)
    masks["macd_hist_reversal_down"] = (hist_slope.shift(1) >= 0) & (hist_slope < 0)
    masks["macd_hist_sign_up"] = (histogram.shift(1) <= 0) & (histogram > 0)
    masks["macd_hist_sign_down"] = (histogram.shift(1) >= 0) & (histogram < 0)
    masks["ma_golden_cross_5_20"] = (ma5.shift(1) <= ma20.shift(1)) & (ma5 > ma20)
    masks["ma_death_cross_5_20"] = (ma5.shift(1) >= ma20.shift(1)) & (ma5 < ma20)
    masks["price_breakout_20"] = close > close.shift(1).rolling(20, min_periods=3).max()
    masks["price_breakdown_20"] = close < close.shift(1).rolling(20, min_periods=3).min()
    masks["rsi_oversold_exit"] = (rsi.shift(1) < 30) & (rsi >= 30)
    masks["rsi_overbought_exit"] = (rsi.shift(1) > 70) & (rsi <= 70)
    masks["adx_trend_strengthening"] = (adx.shift(1) < 25) & (adx >= 25)
    masks["adx_trend_weakening"] = (adx.shift(1) >= 25) & (adx < 25)
    masks["bollinger_breakout_up"] = close > upper
    masks["bollinger_breakout_down"] = close < lower
    squeeze_threshold = width.rolling(60, min_periods=3).quantile(0.2)
    masks["bollinger_squeeze"] = (width <= squeeze_threshold) & (width.shift(1) > squeeze_threshold.shift(1))
    masks["macd_bearish_divergence"] = (close.pct_change(5, fill_method=None) > 0.03) & (macd.diff(5) < 0)
    masks["macd_bullish_divergence"] = (close.pct_change(5, fill_method=None) < -0.03) & (macd.diff(5) > 0)
    masks["flow_price_confirmation_up"] = (price_return > 0) & (flow > 0)
    masks["flow_price_confirmation_down"] = (price_return < 0) & (flow < 0)
    masks["flow_price_divergence_bearish"] = (price_return > 0) & (flow < 0)
    masks["flow_price_divergence_bullish"] = (price_return < 0) & (flow > 0)
    masks["industry_breadth_reversal_up"] = (breadth.shift(1) < 0.4) & (breadth >= 0.5)
    masks["industry_breadth_reversal_down"] = (breadth.shift(1) > 0.6) & (breadth <= 0.5)

    stages = _volume_price_stage(price_return, volume_ratio)
    for stage in sorted(set(stages) - {"volume_price_neutral"}):
        masks[stage] = (stages == stage) & (stages.shift(1) != stage)

    rows: list[dict[str, Any]] = []
    for event, mask in masks.items():
        for index in group.index[mask.fillna(False)]:
            source = group.loc[index]
            code = str(source.get("code", source.get("ts_code", ""))).split(".")[0]
            trade_date = str(source.get("trade_date", ""))
            direction = "up" if any(token in event for token in ("up", "golden", "bullish", "rise", "strengthening")) else "down" if any(token in event for token in ("down", "death", "bearish", "fall", "weakening", "breakdown")) else "neutral"
            context = {
                key: float(source[key])
                for key in ("close", "macd_dif", "macd_dea", "macd_hist", "rsi_14", "adx_14", "volume_ratio_5_20", "flow_net_large", "industry_breadth")
                if key in source and pd.notna(source[key])
            }
            rows.append(
                {
                    "event_id": _event_id(market, code, trade_date, event),
                    "event": event,
                    "market": market,
                    "code": code,
                    "trade_date": trade_date,
                    "direction": direction,
                    "regime": str(source.get("regime", "unknown")),
                    "industry": str(source.get("industry", "unclassified")),
                    "context": json.dumps(context, ensure_ascii=False, sort_keys=True),
                }
            )
    return rows


def detect_events(features: pd.DataFrame, *, market: str) -> pd.DataFrame:
    required = {"trade_date"}
    if required.difference(features.columns):
        raise ValueError("event_missing_trade_date")
    if features.empty:
        return pd.DataFrame(columns=["event_id", "event", "market", "code", "trade_date", "direction", "regime", "industry", "context"])
    working = features.copy()
    group_column = "code" if "code" in working.columns else "ts_code" if "ts_code" in working.columns else None
    groups = working.groupby(group_column, sort=False, dropna=False) if group_column else [("market", working)]
    rows: list[dict[str, Any]] = []
    for _, group in groups:
        rows.extend(_detect_group(group, market))
    return pd.DataFrame(rows).sort_values(["trade_date", "event", "code"]).reset_index(drop=True) if rows else pd.DataFrame(columns=["event_id", "event", "market", "code", "trade_date", "direction", "regime", "industry", "context"])
