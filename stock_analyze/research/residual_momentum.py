"""Ex-ante market and industry residual-momentum features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ResidualMomentumConfig:
    regression_window: int = 252
    minimum_history: int = 126
    ridge_alpha: float = 1e-8
    windows: tuple[tuple[int, int], ...] = ((20, 5), (60, 5), (120, 20))
    winsorize_lower: float = 0.01
    winsorize_upper: float = 0.99
    residual_scale_floor: float = 1e-6
    minimum_industry_constituents: int = 3


def _validate(frame: pd.DataFrame, benchmark: pd.DataFrame) -> None:
    required = {"code", "trade_date", "return_1", "industry", "total_mv"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"residual_momentum_columns:{','.join(missing)}")
    benchmark_required = {"trade_date", "benchmark_return_1"}
    missing_benchmark = sorted(benchmark_required.difference(benchmark.columns))
    if missing_benchmark:
        raise ValueError(
            f"residual_momentum_benchmark_columns:{','.join(missing_benchmark)}"
        )
    if frame.duplicated(["code", "trade_date"]).any():
        raise ValueError("residual_momentum_duplicate_rows")


def _rolling_coefficients(
    group: pd.DataFrame,
    *,
    window: int,
    minimum_history: int,
    ridge_alpha: float,
) -> np.ndarray:
    y = pd.to_numeric(group["return_1"], errors="coerce")
    market = pd.to_numeric(group["benchmark_return_1"], errors="coerce")
    industry = pd.to_numeric(group["industry_return_1"], errors="coerce")
    valid = y.notna() & market.notna() & industry.notna()
    terms = pd.DataFrame({
        "n": valid.astype(float),
        "y": y.where(valid, 0.0),
        "m": market.where(valid, 0.0),
        "g": industry.where(valid, 0.0),
        "m2": (market * market).where(valid, 0.0),
        "g2": (industry * industry).where(valid, 0.0),
        "mg": (market * industry).where(valid, 0.0),
        "my": (market * y).where(valid, 0.0),
        "gy": (industry * y).where(valid, 0.0),
    })
    sums = terms.rolling(window, min_periods=minimum_history).sum().shift(1)
    matrices = np.zeros((len(group), 3, 3), dtype=float)
    vectors = np.zeros((len(group), 3), dtype=float)
    matrices[:, 0, 0] = sums["n"]
    matrices[:, 0, 1] = matrices[:, 1, 0] = sums["m"]
    matrices[:, 0, 2] = matrices[:, 2, 0] = sums["g"]
    matrices[:, 1, 1] = sums["m2"] + float(ridge_alpha)
    matrices[:, 1, 2] = matrices[:, 2, 1] = sums["mg"]
    matrices[:, 2, 2] = sums["g2"] + float(ridge_alpha)
    vectors[:, 0] = sums["y"]
    vectors[:, 1] = sums["my"]
    vectors[:, 2] = sums["gy"]
    coefficients = np.full((len(group), 3), np.nan, dtype=float)
    eligible = (
        sums["n"].ge(minimum_history).to_numpy()
        & np.isfinite(matrices).all(axis=(1, 2))
        & np.isfinite(vectors).all(axis=1)
    )
    if eligible.any():
        try:
            coefficients[eligible] = np.linalg.solve(
                matrices[eligible], vectors[eligible]
            )
        except np.linalg.LinAlgError:
            for index in np.flatnonzero(eligible):
                coefficients[index] = np.linalg.pinv(matrices[index]) @ vectors[index]
    return coefficients


def _neutralize_by_date(
    frame: pd.DataFrame,
    column: str,
    *,
    lower: float,
    upper: float,
) -> pd.Series:
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    for _, indices in frame.groupby("trade_date", sort=True).groups.items():
        part = frame.loc[indices]
        values = pd.to_numeric(part[column], errors="coerce")
        valid = values.notna()
        if int(valid.sum()) < 2:
            continue
        clipped = values.clip(
            lower=values.loc[valid].quantile(lower),
            upper=values.loc[valid].quantile(upper),
        )
        size = np.log(
            pd.to_numeric(part["total_mv"], errors="coerce").where(lambda x: x > 0)
        )
        fit = valid & size.notna()
        residual = clipped.copy()
        if int(fit.sum()) >= 3 and float(size.loc[fit].std()) > 1e-12:
            design = np.column_stack([
                np.ones(int(fit.sum()), dtype=float),
                size.loc[fit].to_numpy(dtype=float),
            ])
            beta = np.linalg.lstsq(
                design, clipped.loc[fit].to_numpy(dtype=float), rcond=None
            )[0]
            residual.loc[fit] = (
                clipped.loc[fit]
                - design @ beta
            )
        industries = part["industry"].fillna("unclassified").astype(str)
        ranked = residual.groupby(industries).rank(pct=True, method="average")
        result.loc[indices] = (ranked - 0.5) * 2.0
    return result


def build_exante_residual_momentum(
    frame: pd.DataFrame,
    benchmark_returns: pd.DataFrame,
    *,
    config: ResidualMomentumConfig | None = None,
) -> pd.DataFrame:
    """Return an ordered frame with ex-ante residual-momentum columns."""

    resolved = config or ResidualMomentumConfig()
    _validate(frame, benchmark_returns)
    result = frame.copy()
    result["code"] = result["code"].astype(str).str.zfill(6)
    result["trade_date"] = result["trade_date"].astype(str)
    result["industry"] = result["industry"].fillna("unclassified").astype(str)
    benchmark = benchmark_returns.loc[
        :, ["trade_date", "benchmark_return_1"]
    ].copy()
    benchmark["trade_date"] = benchmark["trade_date"].astype(str)
    benchmark = benchmark.drop_duplicates("trade_date", keep="last")
    result = result.merge(benchmark, on="trade_date", how="left", validate="many_to_one")
    result = result.sort_values(["code", "trade_date"], kind="stable").reset_index(drop=True)
    industry_groups = [result["trade_date"], result["industry"]]
    stock_return = pd.to_numeric(result["return_1"], errors="coerce")
    industry_return = stock_return.groupby(industry_groups).transform("mean")
    industry_count = stock_return.groupby(industry_groups).transform("count")
    result["industry_return_1"] = industry_return.where(
        industry_count.ge(resolved.minimum_industry_constituents), 0.0
    )
    residual = pd.Series(np.nan, index=result.index, dtype=float)
    for _, indices in result.groupby("code", sort=False).groups.items():
        part = result.loc[indices]
        coefficients = _rolling_coefficients(
            part,
            window=resolved.regression_window,
            minimum_history=resolved.minimum_history,
            ridge_alpha=resolved.ridge_alpha,
        )
        current = np.column_stack([
            np.ones(len(part), dtype=float),
            pd.to_numeric(part["benchmark_return_1"], errors="coerce"),
            pd.to_numeric(part["industry_return_1"], errors="coerce"),
        ])
        predicted = np.einsum("ij,ij->i", current, coefficients)
        residual.loc[indices] = (
            pd.to_numeric(part["return_1"], errors="coerce").to_numpy(dtype=float)
            - predicted
        )
    result["exante_residual_return_1"] = residual
    for window, skip in resolved.windows:
        if window <= skip or skip < 1:
            raise ValueError(f"residual_momentum_window_invalid:{window}:{skip}")
        name = f"exante_residual_momentum_{window}_{skip}"
        raw_name = f"{name}_raw"
        length = window - skip + 1
        shifted = result.groupby("code", sort=False)["exante_residual_return_1"].shift(skip)
        grouped = shifted.groupby(result["code"], sort=False)
        minimum_periods = max(2, int(length * 0.8))
        rolling_sum = grouped.rolling(length, min_periods=minimum_periods).sum().reset_index(level=0, drop=True)
        rolling_std = grouped.rolling(length, min_periods=minimum_periods).std().reset_index(level=0, drop=True)
        rolling_count = grouped.rolling(length, min_periods=1).count().reset_index(level=0, drop=True)
        sufficient = rolling_count.ge(minimum_periods)
        stable_scale = rolling_std.gt(resolved.residual_scale_floor)
        raw = (
            rolling_sum / rolling_std.where(stable_scale) * np.sqrt(length)
        )
        raw = raw.where(stable_scale, 0.0).where(sufficient)
        result[raw_name] = raw
        result[name] = _neutralize_by_date(
            result,
            raw_name,
            lower=resolved.winsorize_lower,
            upper=resolved.winsorize_upper,
        )
    return result.sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)


__all__ = [
    "ResidualMomentumConfig",
    "build_exante_residual_momentum",
]
