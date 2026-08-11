"""Canonical point-in-time technical features backed by TA-Lib."""

from __future__ import annotations

import numpy as np
import pandas as pd
import talib


_REQUIRED_PRICE_COLUMNS = frozenset({"open", "high", "low", "close"})


def _as_float(group: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(group[column], errors="coerce").to_numpy(dtype=float)


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    def percentile(values: np.ndarray) -> float:
        current = values[-1]
        finite = values[np.isfinite(values)]
        if not np.isfinite(current) or len(finite) == 0:
            return np.nan
        return float(np.count_nonzero(finite <= current) / len(finite))

    return series.rolling(window, min_periods=max(5, window // 3)).apply(percentile, raw=True)


def _time_since_event(events: pd.Series) -> pd.Series:
    elapsed = np.nan
    values: list[float] = []
    for event in events.fillna(0.0):
        if event != 0:
            elapsed = 0.0
        elif np.isfinite(elapsed):
            elapsed += 1.0
        values.append(elapsed)
    return pd.Series(values, index=events.index, dtype=float)


def _compute_group(group: pd.DataFrame) -> pd.DataFrame:
    result = group.copy()
    close = _as_float(group, "close")
    high = _as_float(group, "high")
    low = _as_float(group, "low")
    open_ = _as_float(group, "open")

    for period in (5, 10, 20, 60, 120):
        average = talib.SMA(close, timeperiod=period)
        result[f"sma_{period}"] = average
        result[f"sma_distance_{period}"] = np.divide(
            close,
            average,
            out=np.full_like(close, np.nan),
            where=np.isfinite(average) & (average != 0),
        ) - 1.0
    for period in (12, 26):
        average = talib.EMA(close, timeperiod=period)
        result[f"ema_{period}"] = average
        result[f"ema_distance_{period}"] = np.divide(
            close,
            average,
            out=np.full_like(close, np.nan),
            where=np.isfinite(average) & (average != 0),
        ) - 1.0

    macd, signal, histogram = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    result["macd_dif"] = macd
    result["macd_dea"] = signal
    result["macd_hist"] = histogram
    result["macd_dif_pct"] = np.divide(
        macd, close, out=np.full_like(close, np.nan), where=np.isfinite(close) & (close != 0)
    )
    result["macd_dea_pct"] = np.divide(
        signal, close, out=np.full_like(close, np.nan), where=np.isfinite(close) & (close != 0)
    )
    result["macd_hist_pct"] = np.divide(
        histogram, close, out=np.full_like(close, np.nan), where=np.isfinite(close) & (close != 0)
    )
    previous_macd = pd.Series(macd, index=result.index).shift(1)
    previous_signal = pd.Series(signal, index=result.index).shift(1)
    result["macd_cross"] = np.select(
        [
            (previous_macd <= previous_signal) & (macd > signal),
            (previous_macd >= previous_signal) & (macd < signal),
        ],
        [1.0, -1.0],
        default=0.0,
    )
    result["macd_hist_slope"] = pd.Series(histogram, index=result.index).diff()
    result["macd_hist_acceleration"] = result["macd_hist_slope"].diff()
    result["macd_hist_slope_pct"] = result["macd_hist_slope"] / pd.Series(close, index=result.index).replace(0.0, np.nan)
    result["macd_hist_acceleration_pct"] = result["macd_hist_acceleration"] / pd.Series(close, index=result.index).replace(0.0, np.nan)
    result["macd_zero_state"] = np.sign(macd)
    result["macd_cross_age"] = _time_since_event(result["macd_cross"])

    result["rsi_14"] = talib.RSI(close, timeperiod=14)
    result["adx_14"] = talib.ADX(high, low, close, timeperiod=14)
    result["atr_14"] = talib.ATR(high, low, close, timeperiod=14)
    result["natr_14"] = talib.NATR(high, low, close, timeperiod=14)
    upper, middle, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
    result["bollinger_upper"] = upper
    result["bollinger_middle"] = middle
    result["bollinger_lower"] = lower
    width = upper - lower
    result["bollinger_position"] = np.divide(
        close - lower,
        width,
        out=np.full_like(close, np.nan),
        where=np.isfinite(width) & (width != 0),
    )
    result["bollinger_width"] = np.divide(
        width,
        middle,
        out=np.full_like(close, np.nan),
        where=np.isfinite(middle) & (middle != 0),
    )

    close_series = pd.Series(close, index=result.index)
    returns = close_series.pct_change(fill_method=None)
    result["return_1"] = returns
    result["momentum_5"] = close_series.pct_change(5, fill_method=None)
    result["momentum_10"] = close_series.pct_change(10, fill_method=None)
    result["momentum_20"] = close_series.pct_change(20, fill_method=None)
    result["momentum_60"] = close_series.pct_change(60, fill_method=None)
    result["momentum_120"] = close_series.pct_change(120, fill_method=None)
    result["reversal_5"] = -result["momentum_5"]
    result["realized_volatility_5"] = returns.rolling(5).std() * np.sqrt(252.0)
    result["realized_volatility_20"] = returns.rolling(20).std() * np.sqrt(252.0)
    result["realized_volatility_60"] = returns.rolling(60).std() * np.sqrt(252.0)
    downside = returns.where(returns < 0.0, 0.0)
    result["downside_volatility_20"] = downside.rolling(20).std() * np.sqrt(252.0)
    result["price_slope_5"] = close_series.pct_change(5, fill_method=None) / 5.0
    result["gap_return"] = pd.Series(open_, index=result.index) / close_series.shift(1) - 1.0
    day_range = pd.Series(high - low, index=result.index)
    result["intraday_range"] = day_range / close_series.replace(0.0, np.nan)
    result["close_location"] = (
        (2.0 * close_series - pd.Series(high + low, index=result.index))
        / day_range.replace(0.0, np.nan)
    )
    result["drawdown_60"] = close_series / close_series.rolling(60).max() - 1.0
    result["breakout_20"] = (
        close_series / close_series.shift(1).rolling(20).max() - 1.0
    )

    if "benchmark_close" in group.columns:
        benchmark = pd.to_numeric(group["benchmark_close"], errors="coerce")
        result["relative_strength_20"] = result["momentum_20"] - benchmark.pct_change(20, fill_method=None)
    else:
        result["relative_strength_20"] = np.nan

    has_volume = "volume" in group.columns and pd.to_numeric(group["volume"], errors="coerce").notna().any()
    if has_volume:
        volume = _as_float(group, "volume")
        volume_series = pd.Series(volume, index=result.index)
        volume_20 = volume_series.rolling(20).mean()
        result["volume_ratio_5_20"] = volume_series.rolling(5).mean() / volume_20
        result["volume_zscore_20"] = (volume_series - volume_20) / volume_series.rolling(20).std()
        result["obv"] = talib.OBV(close, volume)
        result["ad"] = talib.AD(high, low, close, volume)
        volume_base = volume_series.rolling(5).sum().replace(0.0, np.nan)
        result["obv_flow_5"] = pd.Series(result["obv"], index=result.index).diff(5) / volume_base
        result["ad_flow_5"] = pd.Series(result["ad"], index=result.index).diff(5) / volume_base
        result["mfi_14"] = talib.MFI(high, low, close, volume, timeperiod=14)
    else:
        for column in (
            "volume_ratio_5_20", "volume_zscore_20", "obv", "ad",
            "obv_flow_5", "ad_flow_5", "mfi_14",
        ):
            result[column] = np.nan

    if "amount" in group.columns:
        amount = pd.to_numeric(group["amount"], errors="coerce")
        amount_mean_20 = amount.rolling(20).mean()
        amount_std_20 = amount.rolling(20).std()
        result["avg_amount_20"] = amount_mean_20
        result["amount_ratio_5_20"] = amount.rolling(5).mean() / amount_mean_20
        result["amount_zscore_20"] = (
            (amount - amount_mean_20) / amount_std_20.replace(0.0, np.nan)
        )
        relative_amount = amount / amount.rolling(60, min_periods=20).median()
        result["amihud_illiquidity_20"] = (
            returns.abs() / relative_amount.replace(0.0, np.nan)
        ).rolling(20).mean()
        result["price_volume_confirmation_20"] = (
            result["momentum_20"] * (result["amount_ratio_5_20"] - 1.0)
        )
    else:
        result["avg_amount_20"] = np.nan
        result["amount_ratio_5_20"] = np.nan
        result["amount_zscore_20"] = np.nan
        result["amihud_illiquidity_20"] = np.nan
        result["price_volume_confirmation_20"] = np.nan

    if has_volume:
        volume_series = pd.Series(_as_float(group, "volume"), index=result.index)
        volume_change = np.log1p(volume_series).diff()
        result["volume_price_correlation_20"] = returns.rolling(20).corr(
            volume_change
        )
        up_volume = volume_series.where(returns > 0.0, 0.0).rolling(20).sum()
        result["up_volume_ratio_20"] = (
            up_volume / volume_series.rolling(20).sum().replace(0.0, np.nan)
        )
    else:
        result["volume_price_correlation_20"] = np.nan
        result["up_volume_ratio_20"] = np.nan

    if "turnover_rate" in group.columns:
        turnover = pd.to_numeric(group["turnover_rate"], errors="coerce")
        result["turnover_percentile_60"] = _rolling_percentile(turnover, 60)
        result["turnover_change_5"] = turnover / turnover.rolling(5).mean() - 1.0
        result["turnover_change_20"] = turnover / turnover.rolling(20).mean() - 1.0
    else:
        result["turnover_percentile_60"] = np.nan
        result["turnover_change_5"] = np.nan
        result["turnover_change_20"] = np.nan
    return result


def _compute_group_without_calendar_gaps(group: pd.DataFrame) -> pd.DataFrame:
    valid_prices = pd.DataFrame({
        column: pd.to_numeric(group[column], errors="coerce")
        for column in _REQUIRED_PRICE_COLUMNS
    }).notna().all(axis=1)
    if valid_prices.all() or not valid_prices.any():
        return _compute_group(group)
    computed = _compute_group(group.loc[valid_prices].copy())
    result = group.copy()
    derived_columns = [column for column in computed.columns if column not in result.columns]
    for column in derived_columns:
        result[column] = np.nan
        result.loc[computed.index, column] = computed[column]
    return result


def compute_technical_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Return one feature row per input row without filling unavailable values."""

    if "amount_unit" in ohlcv.columns:
        declared_units = set(
            ohlcv["amount_unit"].dropna().astype(str).str.strip()
        )
        if declared_units and declared_units != {"yuan"}:
            raise ValueError("research_amount_unit_mismatch")
    missing = _REQUIRED_PRICE_COLUMNS.difference(ohlcv.columns)
    if missing:
        raise ValueError(f"technical_feature_missing_columns:{','.join(sorted(missing))}")
    if ohlcv.empty:
        return ohlcv.copy()

    original = ohlcv.copy()
    original["__input_order"] = np.arange(len(original))
    sort_columns = [column for column in ("code", "trade_date") if column in original.columns]
    ordered = original.sort_values(sort_columns or ["__input_order"])
    if "code" in ordered.columns:
        parts = [
            _compute_group_without_calendar_gaps(group)
            for _, group in ordered.groupby("code", sort=False, dropna=False)
        ]
        featured = pd.concat(parts, axis=0)
    else:
        featured = _compute_group_without_calendar_gaps(ordered)
    return featured.sort_values("__input_order").drop(columns="__input_order").reset_index(drop=True)
