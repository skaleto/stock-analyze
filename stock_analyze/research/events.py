"""Deterministic technical, volume-price, flow, and breadth event detection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


_EVENT_COLUMNS = (
    "event_id",
    "event",
    "market",
    "code",
    "trade_date",
    "direction",
    "regime",
    "industry",
    "context",
)
_CONTEXT_COLUMNS = (
    "close",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "rsi_14",
    "adx_14",
    "volume_ratio_5_20",
    "flow_net_large",
    "industry_breadth",
)


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _event_id(market: str, code: str, trade_date: str, event: str) -> str:
    raw = f"{market}|{code}|{trade_date}|{event}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            column: pd.Series(dtype="string[pyarrow]")
            for column in _EVENT_COLUMNS
        }
    )


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


def _detect_group(group: pd.DataFrame, market: str) -> pd.DataFrame:
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

    context_columns = [column for column in _CONTEXT_COLUMNS if column in group]
    context_values = [_series(group, column).to_numpy() for column in context_columns]
    contexts = [
        json.dumps(
            {
                key: float(value)
                for key, value in zip(context_columns, values)
                if pd.notna(value)
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for values in zip(*context_values)
    ] if context_values else ["{}"] * len(group)
    code_column = "code" if "code" in group else "ts_code" if "ts_code" in group else None
    codes = (
        group[code_column].astype(str).str.split(".").str[0]
        if code_column
        else pd.Series("", index=group.index)
    )
    base = pd.DataFrame(
        {
            "code": codes.to_numpy(),
            "trade_date": group["trade_date"].astype(str).to_numpy(),
            "regime": (
                group["regime"].astype(str).to_numpy()
                if "regime" in group
                else np.full(len(group), "unknown", dtype=object)
            ),
            "industry": (
                group["industry"].astype(str).to_numpy()
                if "industry" in group
                else np.full(len(group), "unclassified", dtype=object)
            ),
            "context": contexts,
        }
    )
    event_frames: list[pd.DataFrame] = []
    for event, mask in masks.items():
        positions = np.flatnonzero(mask.fillna(False).to_numpy(dtype=bool))
        if len(positions) == 0:
            continue
        selected = base.iloc[positions].copy()
        selected["event"] = event
        selected["market"] = market
        selected["direction"] = (
            "up"
            if any(token in event for token in ("up", "golden", "bullish", "rise", "strengthening"))
            else "down"
            if any(token in event for token in ("down", "death", "bearish", "fall", "weakening", "breakdown"))
            else "neutral"
        )
        selected["event_id"] = [
            _event_id(market, code, trade_date, event)
            for code, trade_date in zip(selected["code"], selected["trade_date"])
        ]
        event_frames.append(selected.loc[:, _EVENT_COLUMNS])
    if not event_frames:
        return _empty_events()
    detected = pd.concat(event_frames, ignore_index=True)
    for column in _EVENT_COLUMNS:
        detected[column] = detected[column].astype("string[pyarrow]")
    return detected


def detect_events(features: pd.DataFrame, *, market: str) -> pd.DataFrame:
    required = {"trade_date"}
    if required.difference(features.columns):
        raise ValueError("event_missing_trade_date")
    if features.empty:
        return _empty_events()
    working = features
    group_column = "code" if "code" in working.columns else "ts_code" if "ts_code" in working.columns else None
    groups = working.groupby(group_column, sort=False, dropna=False) if group_column else [("market", working)]
    detected_groups: list[pd.DataFrame] = []
    for _, group in groups:
        detected = _detect_group(group, market)
        if not detected.empty:
            detected_groups.append(detected)
    if not detected_groups:
        return _empty_events()
    return pd.concat(detected_groups, ignore_index=True).sort_values(
        ["trade_date", "event", "code"]
    ).reset_index(drop=True)


def write_events_incremental(
    features: pd.DataFrame,
    *,
    market: str,
    destination: str | Path,
    regime_by_date: dict[str, str] | None = None,
    groups_per_batch: int = 16,
) -> int:
    """Detect and atomically persist events without retaining all groups."""

    if "trade_date" not in features.columns:
        raise ValueError("event_missing_trade_date")
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".parquet",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink(missing_ok=True)
    writer: pq.ParquetWriter | None = None
    rows = 0
    pending: list[pd.DataFrame] = []
    batch_size = max(1, int(groups_per_batch))

    def flush() -> None:
        nonlocal writer, rows
        if not pending:
            return
        batch = pd.concat(pending, ignore_index=True)
        pending.clear()
        if regime_by_date:
            detected_regime = batch["trade_date"].astype(str).map(regime_by_date)
            batch["regime"] = detected_regime.fillna(batch["regime"])
        for column in _EVENT_COLUMNS:
            batch[column] = batch[column].astype("string[pyarrow]")
        table = pa.Table.from_pandas(batch.loc[:, _EVENT_COLUMNS], preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(temporary, table.schema, compression="snappy")
        writer.write_table(table)
        rows += len(batch)

    try:
        if features.empty:
            _empty_events().to_parquet(temporary, index=False)
        else:
            group_column = (
                "code"
                if "code" in features.columns
                else "ts_code"
                if "ts_code" in features.columns
                else None
            )
            groups = (
                features.groupby(group_column, sort=False, dropna=False)
                if group_column
                else [("market", features)]
            )
            for _, group in groups:
                detected = _detect_group(group, market)
                if not detected.empty:
                    pending.append(detected)
                if len(pending) >= batch_size:
                    flush()
            flush()
            if writer is None:
                _empty_events().to_parquet(temporary, index=False)
        if writer is not None:
            writer.close()
            writer = None
        os.replace(temporary, path)
        return rows
    finally:
        if writer is not None:
            writer.close()
        temporary.unlink(missing_ok=True)
