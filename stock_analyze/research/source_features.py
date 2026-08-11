"""Research-source normalization and point-in-time derived features."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SourceSpec:
    keys: tuple[str, ...]
    required: tuple[str, ...]


@dataclass
class SourceCollection:
    frames: dict[str, pd.DataFrame]
    health: pd.DataFrame


SOURCE_SPECS = {
    "daily_basic": SourceSpec(("trade_date", "ts_code"), ("trade_date", "ts_code")),
    "moneyflow": SourceSpec(("trade_date", "ts_code"), ("trade_date", "ts_code")),
    "fund_nav": SourceSpec(("ts_code", "nav_date", "ann_date"), ("ts_code", "nav_date")),
    "fund_share": SourceSpec(("ts_code", "trade_date"), ("ts_code", "trade_date", "fd_share")),
    "macro_releases": SourceSpec(("series", "source_date"), ("series", "source_date", "value")),
}


def _scrub_error(error: Exception) -> str:
    text = str(error)
    text = re.sub(r"(?i)(token|api_key|apikey)=([^&\s]+)", r"\1=<redacted>", text)
    return text[:240]


def normalize_source_frame(
    name: str,
    frame: pd.DataFrame | None,
    observed_at: str,
) -> pd.DataFrame:
    normalized = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    for column in ("ts_code", "code", "trade_date", "ann_date", "nav_date", "end_date"):
        if column in normalized.columns:
            normalized[column] = normalized[column].astype("string")
    date_column = next(
        (column for column in ("source_date", "trade_date", "ann_date", "nav_date", "end_date", "month", "date") if column in normalized.columns),
        None,
    )
    if date_column:
        normalized["source_date"] = normalized[date_column].astype("string")
    else:
        normalized["source_date"] = pd.Series([pd.NA] * len(normalized), dtype="string")
    normalized["source"] = f"tushare:{name}"
    normalized["observed_at"] = observed_at
    return normalized


def collect_source_calls(
    calls: Mapping[str, Iterable[Callable[[], pd.DataFrame]]],
    *,
    observed_at: str,
) -> SourceCollection:
    frames: dict[str, pd.DataFrame] = {}
    health_rows: list[dict[str, Any]] = []
    for name, endpoint_calls in calls.items():
        pieces: list[pd.DataFrame] = []
        failures: list[str] = []
        for endpoint_call in endpoint_calls:
            try:
                piece = endpoint_call()
                if isinstance(piece, pd.DataFrame) and not piece.empty:
                    pieces.append(piece)
            except Exception as exc:  # noqa: BLE001 - source failure is persisted
                failures.append(_scrub_error(exc))
        combined = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
        frames[name] = normalize_source_frame(name, combined, observed_at)
        health_rows.append(
            {
                "source": name,
                "observed_at": observed_at,
                "rows": len(combined),
                "failed": bool(failures),
                "error": " | ".join(failures),
            }
        )
    return SourceCollection(frames=frames, health=pd.DataFrame(health_rows))


def _latest(frame: pd.DataFrame, date_columns: tuple[str, ...] = ("trade_date", "ann_date", "nav_date")) -> pd.DataFrame:
    if frame.empty or "ts_code" not in frame.columns:
        return pd.DataFrame()
    date_column = next((column for column in date_columns if column in frame.columns), None)
    ordered = frame.sort_values(date_column) if date_column else frame
    return ordered.groupby("ts_code", as_index=False, dropna=False).tail(1)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def attach_daily_basic_point_in_time_features(
    prices: pd.DataFrame,
    daily_basic: pd.DataFrame,
) -> pd.DataFrame:
    """Attach valuation data only when its market date exactly matches.

    Tushare ``daily_basic`` is a daily market observation, not a slowly changing
    fundamental. Carrying a stale row across a missing date can hide source
    gaps and use the wrong valuation, so unmatched dates remain null.
    """

    result = prices.reset_index(drop=True).copy()
    value_columns = (
        "pe_ttm",
        "pb",
        "dv_ttm",
        "total_mv",
        "circ_mv",
        "turnover_rate",
    )
    for column in (*value_columns, "dividend_yield", "daily_basic_trade_date"):
        if column not in result.columns:
            result[column] = np.nan if column != "daily_basic_trade_date" else pd.NA
    if (
        result.empty
        or daily_basic.empty
        or "trade_date" not in result.columns
        or "trade_date" not in daily_basic.columns
        or not ({"code", "ts_code"}.intersection(daily_basic.columns))
    ):
        return result

    target = result
    target["_input_order"] = np.arange(len(target))
    target["_code"] = target["code"].astype("string").str.split(".").str[0].str.zfill(6)
    target["_date"] = pd.to_datetime(
        target["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8],
        format="%Y%m%d",
        errors="coerce",
    )

    source_code = "ts_code" if "ts_code" in daily_basic.columns else "code"
    source_keys = (
        daily_basic[source_code]
        .astype("string")
        .str.split(".")
        .str[0]
        .str.zfill(6)
    )
    wanted_codes = set(target["_code"].dropna().astype(str))
    selected = source_keys.isin(wanted_codes)
    source = daily_basic.loc[selected].copy()
    source["_code"] = source_keys.loc[selected].to_numpy()
    source["_date"] = pd.to_datetime(
        source["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8],
        format="%Y%m%d",
        errors="coerce",
    )
    available = [column for column in value_columns if column in source.columns]
    if not available:
        return result
    source = source[["_code", "_date", "trade_date", *available]].copy()
    source = source.rename(columns={"trade_date": "daily_basic_trade_date"})
    for column in available:
        source[column] = pd.to_numeric(source[column], errors="coerce")
    source = source.dropna(subset=["_date"]).drop_duplicates(
        ["_code", "_date"],
        keep="last",
    )

    existing = [
        column
        for column in (*available, "daily_basic_trade_date")
        if column in target.columns
    ]
    left = target.drop(columns=existing)
    right = source.sort_values(["_date", "_code"], kind="stable")
    merged = left.merge(
        right,
        on=["_code", "_date"],
        how="left",
        sort=False,
        validate="many_to_one",
    ).sort_values("_input_order", kind="stable")
    for column in value_columns:
        if column not in merged.columns:
            merged[column] = np.nan
    merged["dividend_yield"] = pd.to_numeric(merged["dv_ttm"], errors="coerce")
    return merged.drop(columns=["_input_order", "_code", "_date"]).reset_index(drop=True)


def _merge_asof_feature(
    target: pd.DataFrame,
    values: pd.DataFrame,
    *,
    value_column: str,
) -> pd.DataFrame:
    if values.empty or value_column not in values.columns:
        target[value_column] = np.nan
        return target
    prepared = values[["_date", value_column]].copy()
    prepared["_date"] = pd.to_datetime(prepared["_date"], errors="coerce")
    prepared[value_column] = pd.to_numeric(prepared[value_column], errors="coerce")
    prepared = prepared.dropna(subset=["_date"]).groupby("_date", as_index=False)[value_column].mean()
    return pd.merge_asof(
        target.sort_values("_date"),
        prepared.sort_values("_date"),
        on="_date",
        direction="backward",
        allow_exact_matches=True,
    )


def _monthly_change(
    frame: pd.DataFrame,
    *,
    month_column: str,
    value_column: str,
    output_column: str,
    release_lag_days: int,
) -> pd.DataFrame:
    if frame.empty or month_column not in frame.columns or value_column not in frame.columns:
        return pd.DataFrame(columns=["_date", output_column])
    values = frame[[month_column, value_column]].copy()
    values[month_column] = values[month_column].astype("string").str.replace("-", "", regex=False).str[:6]
    values[value_column] = pd.to_numeric(values[value_column], errors="coerce")
    values = values.dropna().drop_duplicates(month_column, keep="last").sort_values(month_column)
    periods = pd.PeriodIndex(values[month_column], freq="M")
    values["_date"] = periods.to_timestamp(how="end").normalize() + pd.to_timedelta(release_lag_days, unit="D")
    values[output_column] = values[value_column].diff()
    return values[["_date", output_column]]


def _daily_level_change(
    frame: pd.DataFrame,
    *,
    date_column: str,
    value_column: str,
    output_column: str,
    periods: int = 20,
    relative: bool = False,
) -> pd.DataFrame:
    if frame.empty or date_column not in frame.columns or value_column not in frame.columns:
        return pd.DataFrame(columns=["_date", output_column])
    values = frame[[date_column, value_column]].copy()
    values["_date"] = pd.to_datetime(values[date_column].astype("string").str[:8], format="%Y%m%d", errors="coerce")
    values[value_column] = pd.to_numeric(values[value_column], errors="coerce")
    values = values.dropna(subset=["_date", value_column]).groupby("_date", as_index=False)[value_column].mean().sort_values("_date")
    lag = periods if len(values) > periods else 1
    if relative:
        values[output_column] = values[value_column].pct_change(lag, fill_method=None)
    else:
        values[output_column] = values[value_column].diff(lag)
    return values[["_date", output_column]]


def build_regime_components(
    frames: Mapping[str, pd.DataFrame],
    trade_dates: pd.Series | Iterable[str],
) -> pd.DataFrame:
    """Build point-in-time macro and global components for market regimes.

    Monthly series use conservative publication lags. Daily market series are
    merged backward so no row can observe data published after its trade date.
    """

    target = pd.DataFrame({"trade_date": pd.Series(list(trade_dates), dtype="string")})
    target["trade_date"] = target["trade_date"].str.replace("-", "", regex=False).str[:8]
    target["_date"] = pd.to_datetime(target["trade_date"], format="%Y%m%d", errors="coerce")
    target = target.dropna(subset=["_date"]).drop_duplicates("trade_date").sort_values("_date")

    monthly_specs = (
        ("cn_pmi", "MONTH", "PMI010000", "pmi_change", 1),
        ("cn_m", "month", "m2_yoy", "m2_change", 15),
        ("cn_cpi", "month", "nt_yoy", "cpi_change", 10),
        ("cn_ppi", "month", "ppi_yoy", "ppi_change", 10),
    )
    for source, month_column, value_column, output_column, release_lag in monthly_specs:
        values = _monthly_change(
            frames.get(source, pd.DataFrame()),
            month_column=month_column,
            value_column=value_column,
            output_column=output_column,
            release_lag_days=release_lag,
        )
        target = _merge_asof_feature(target, values, value_column=output_column)

    shibor = _daily_level_change(
        frames.get("shibor", pd.DataFrame()),
        date_column="date",
        value_column="3m",
        output_column="shibor_change",
    )
    target = _merge_asof_feature(target, shibor, value_column="shibor_change")

    treasury = frames.get("us_tycr", pd.DataFrame()).copy()
    if not treasury.empty and {"date", "y2", "y10"}.issubset(treasury.columns):
        treasury["_date"] = pd.to_datetime(treasury["date"].astype("string").str[:8], format="%Y%m%d", errors="coerce")
        treasury["y2"] = pd.to_numeric(treasury["y2"], errors="coerce")
        treasury["y10"] = pd.to_numeric(treasury["y10"], errors="coerce")
        treasury = treasury.dropna(subset=["_date"]).sort_values("_date")
        treasury["yield_curve_slope"] = treasury["y10"] - treasury["y2"]
        lag = min(20, max(1, len(treasury) - 1))
        treasury["us_yield_change"] = treasury["y10"].diff(lag)
        for column in ("yield_curve_slope", "us_yield_change"):
            target = _merge_asof_feature(target, treasury[["_date", column]], value_column=column)
    else:
        target["yield_curve_slope"] = np.nan
        target["us_yield_change"] = np.nan

    global_index = frames.get("index_global", pd.DataFrame()).copy()
    if not global_index.empty and {"ts_code", "trade_date", "close"}.issubset(global_index.columns):
        global_index["_date"] = pd.to_datetime(global_index["trade_date"].astype("string").str[:8], format="%Y%m%d", errors="coerce")
        global_index["close"] = pd.to_numeric(global_index["close"], errors="coerce")
        global_index = global_index.dropna(subset=["_date", "close"]).sort_values(["ts_code", "_date"])
        parts: list[pd.DataFrame] = []
        for _, group in global_index.groupby("ts_code", sort=False):
            group = group.copy()
            lag = 20 if len(group) > 20 else 1
            returns = group["close"].pct_change(fill_method=None)
            group["global_index_momentum"] = group["close"].pct_change(lag, fill_method=None)
            group["global_volatility"] = returns.rolling(min(20, max(2, len(group))), min_periods=2).std() * np.sqrt(252.0)
            parts.append(group[["_date", "global_index_momentum", "global_volatility"]])
        global_daily = pd.concat(parts, ignore_index=True).groupby("_date", as_index=False).median(numeric_only=True)
        for column in ("global_index_momentum", "global_volatility"):
            target = _merge_asof_feature(target, global_daily[["_date", column]], value_column=column)
    else:
        target["global_index_momentum"] = np.nan
        target["global_volatility"] = np.nan

    fx = frames.get("fx_daily", pd.DataFrame()).copy()
    fx_close = "bid_close" if "bid_close" in fx.columns else "close"
    rmb = _daily_level_change(
        fx,
        date_column="trade_date",
        value_column=fx_close,
        output_column="rmb_depreciation",
        relative=True,
    )
    target = _merge_asof_feature(target, rmb, value_column="rmb_depreciation")
    return target.drop(columns="_date").sort_values("trade_date").reset_index(drop=True)


def _dedupe_reports(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "ts_code" not in frame.columns:
        return pd.DataFrame()
    reports = frame.copy()
    for column in ("ts_code", "ann_date", "end_date"):
        if column in reports.columns:
            reports[column] = reports[column].astype("string")
    if "ann_date" not in reports.columns:
        reports["ann_date"] = reports.get("end_date", pd.Series(pd.NA, index=reports.index)).astype("string")
    if "end_date" not in reports.columns:
        reports["end_date"] = reports["ann_date"]
    sort_columns = [column for column in ("ts_code", "end_date", "ann_date", "update_flag") if column in reports.columns]
    reports = reports.sort_values(sort_columns)
    return reports.drop_duplicates(["ts_code", "end_date", "ann_date"], keep="last")


def _merge_report_revisions_asof(
    history: pd.DataFrame,
    reports: pd.DataFrame,
    *,
    value_columns: list[str],
    availability_column: str,
) -> pd.DataFrame:
    """Attach only the statement revision visible at each indicator announcement."""

    if reports.empty:
        return history
    result = history.reset_index(drop=True).copy()
    result[availability_column] = pd.Series(pd.NA, index=result.index, dtype="string")
    for column in value_columns:
        result[column] = np.nan
    join_keys = ["ts_code", "end_date"]
    rows = result[[*join_keys, "available_date"]].assign(
        _target_index=result.index
    )
    rows["_indicator_available_at"] = pd.to_datetime(
        rows["available_date"].astype("string"),
        format="%Y%m%d",
        errors="coerce",
    )
    rows = rows.dropna(subset=[*join_keys, "_indicator_available_at"])
    source = reports[[*join_keys, "ann_date", *value_columns]].copy()
    source["_statement_available_at"] = pd.to_datetime(
        source["ann_date"].astype("string"),
        format="%Y%m%d",
        errors="coerce",
    )
    source[availability_column] = source["ann_date"].astype("string")
    source = source.dropna(subset=[*join_keys, "_statement_available_at"])
    source = source.drop_duplicates(
        [*join_keys, "_statement_available_at"],
        keep="last",
    )
    if rows.empty or source.empty:
        return result
    merged = pd.merge_asof(
        rows.sort_values(["_indicator_available_at", *join_keys]),
        source[
            [*join_keys, "_statement_available_at", availability_column, *value_columns]
        ].sort_values(["_statement_available_at", *join_keys]),
        by=join_keys,
        left_on="_indicator_available_at",
        right_on="_statement_available_at",
        direction="backward",
    )
    target_index = merged["_target_index"].astype(int).to_numpy()
    result.loc[target_index, availability_column] = merged[
        availability_column
    ].to_numpy()
    for column in value_columns:
        numeric = pd.to_numeric(merged[column], errors="coerce")
        result.loc[target_index, column] = numeric.to_numpy(
            dtype="float64",
            na_value=np.nan,
        )
    return result


def build_fundamental_history(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Create announcement-date financial features for point-in-time joins."""

    indicators = _dedupe_reports(frames.get("fina_indicator", pd.DataFrame()))
    if not indicators.empty:
        observable = pd.to_datetime(
            indicators["ann_date"].astype("string"),
            format="%Y%m%d",
            errors="coerce",
        ).notna()
        indicators = indicators.loc[observable].copy()
    if indicators.empty:
        return pd.DataFrame(columns=["code", "available_date", "end_date"])
    history = indicators[["ts_code", "ann_date", "end_date"]].copy()
    history = history.rename(columns={"ann_date": "available_date"})
    field_map = {
        "roe": "roe",
        "gross_margin": "grossprofit_margin",
        "roic": "roic",
        "net_profit_margin": "netprofit_margin",
        "debt_ratio": "debt_to_assets",
        "current_ratio": "current_ratio",
        "quick_ratio": "quick_ratio",
        "asset_turnover": "assets_turn",
        "revenue_growth": "q_sales_yoy",
        "profit_growth": "netprofit_yoy",
        "growth_acceleration": "q_op_qoq",
        "operating_cashflow_growth": "ocf_yoy",
    }
    for target, source in field_map.items():
        history[target] = pd.to_numeric(indicators.get(source), errors="coerce")

    income = _dedupe_reports(frames.get("income", pd.DataFrame()))
    if not income.empty:
        for column in ("revenue", "operate_profit", "n_income", "total_cogs", "rd_exp"):
            income[column] = pd.to_numeric(income.get(column), errors="coerce")
        income["operating_margin"] = _safe_ratio(income["operate_profit"], income["revenue"])
        income["rd_intensity"] = _safe_ratio(income["rd_exp"], income["revenue"])
        income["cost_growth"] = income.groupby("ts_code")["total_cogs"].pct_change(4, fill_method=None) * 100.0
        income["operating_profit_growth"] = income.groupby("ts_code")["operate_profit"].pct_change(4, fill_method=None) * 100.0
        income_values = [
            "revenue", "n_income", "operating_margin", "rd_intensity",
            "cost_growth", "operating_profit_growth",
        ]
        history = _merge_report_revisions_asof(
            history,
            income,
            value_columns=income_values,
            availability_column="income_available_date",
        )

    cashflow = _dedupe_reports(frames.get("cashflow", pd.DataFrame()))
    if not cashflow.empty:
        for column in ("n_cashflow_act", "free_cashflow"):
            cashflow[column] = pd.to_numeric(cashflow.get(column), errors="coerce")
        history = _merge_report_revisions_asof(
            history,
            cashflow,
            value_columns=["n_cashflow_act", "free_cashflow"],
            availability_column="cash_available_date",
        )

    balance = _dedupe_reports(frames.get("balancesheet", pd.DataFrame()))
    if not balance.empty:
        balance["total_assets"] = pd.to_numeric(balance.get("total_assets"), errors="coerce")
        history = _merge_report_revisions_asof(
            history,
            balance,
            value_columns=["total_assets"],
            availability_column="balance_available_date",
        )

    business = frames.get("fina_mainbz", pd.DataFrame()).copy()
    if not business.empty and {"ts_code", "end_date", "bz_sales"}.issubset(business.columns):
        business["bz_sales"] = pd.to_numeric(business["bz_sales"], errors="coerce")
        business["bz_profit"] = pd.to_numeric(business.get("bz_profit"), errors="coerce")
        business_rows: list[dict[str, Any]] = []
        for (ts_code, end_date), group in business.groupby(["ts_code", "end_date"], sort=False):
            sales = group["bz_sales"].clip(lower=0.0)
            total_sales = float(sales.sum())
            if total_sales <= 0:
                continue
            shares = sales / total_sales
            profit = pd.to_numeric(group["bz_profit"], errors="coerce")
            business_rows.append({
                "ts_code": ts_code,
                "end_date": str(end_date),
                "profit_pool_concentration": float((shares ** 2).sum()),
                "largest_business_share": float(shares.max()),
                "business_profit_margin": float(profit.sum() / total_sales) if profit.notna().any() else np.nan,
            })
        if business_rows:
            history = history.merge(pd.DataFrame(business_rows), on=["ts_code", "end_date"], how="left")

    availability = [column for column in ("available_date", "income_available_date", "cash_available_date", "balance_available_date") if column in history.columns]
    history["available_date"] = history[availability].astype("string").max(axis=1)
    history = history.drop(columns=[column for column in availability if column != "available_date"])
    for column in (
        "revenue", "n_income", "n_cashflow_act", "free_cashflow", "total_assets",
        "gross_margin", "roic", "operating_margin", "rd_intensity", "revenue_growth",
        "cost_growth", "operating_profit_growth", "profit_growth",
        "business_profit_margin",
    ):
        if column not in history.columns:
            history[column] = np.nan
    history["cash_conversion"] = _safe_ratio(
        pd.to_numeric(history.get("n_cashflow_act"), errors="coerce"),
        pd.to_numeric(history.get("revenue"), errors="coerce"),
    )
    history["accrual_ratio"] = _safe_ratio(
        pd.to_numeric(history.get("n_income"), errors="coerce") - pd.to_numeric(history.get("n_cashflow_act"), errors="coerce"),
        pd.to_numeric(history.get("total_assets"), errors="coerce"),
    )
    history["free_cashflow_to_assets"] = _safe_ratio(
        pd.to_numeric(history.get("free_cashflow"), errors="coerce"),
        pd.to_numeric(history.get("total_assets"), errors="coerce"),
    )
    history["gross_profit_to_assets"] = (
        pd.to_numeric(history.get("gross_margin"), errors="coerce") / 100.0
        * _safe_ratio(pd.to_numeric(history.get("revenue"), errors="coerce"), pd.to_numeric(history.get("total_assets"), errors="coerce"))
    )
    history["operating_leverage_proxy"] = pd.to_numeric(history.get("operating_profit_growth"), errors="coerce") - pd.to_numeric(history.get("revenue_growth"), errors="coerce")
    history["declining_marginal_cost_proxy"] = (
        pd.to_numeric(history.get("revenue_growth"), errors="coerce")
        - pd.to_numeric(history.get("cost_growth"), errors="coerce")
        + pd.to_numeric(history.get("operating_leverage_proxy"), errors="coerce")
    ) / 100.0
    quality_components = pd.DataFrame({
        "gross_margin": pd.to_numeric(history.get("gross_margin"), errors="coerce") / 100.0,
        "roic": pd.to_numeric(history.get("roic"), errors="coerce") / 100.0,
        "operating_margin": pd.to_numeric(history.get("operating_margin"), errors="coerce"),
        "cash_conversion": pd.to_numeric(history.get("cash_conversion"), errors="coerce"),
        "rd_intensity": pd.to_numeric(history.get("rd_intensity"), errors="coerce"),
        "business_margin": pd.to_numeric(history.get("business_profit_margin"), errors="coerce"),
    })
    history["high_value_add_proxy"] = quality_components.clip(-2.0, 2.0).mean(axis=1, skipna=True)
    history = history.sort_values(["ts_code", "available_date", "end_date"])
    history["earnings_stability"] = -history.groupby("ts_code")["profit_growth"].transform(
        lambda values: values.rolling(4, min_periods=2).std()
    )
    history["pricing_power_persistence"] = -history.groupby("ts_code")["gross_margin"].transform(
        lambda values: values.rolling(4, min_periods=2).std()
    )
    business_columns = [
        column for column in ("profit_pool_concentration", "largest_business_share", "business_profit_margin")
        if column in history.columns
    ]
    if business_columns:
        history[business_columns] = history.groupby("ts_code")[business_columns].ffill()
    history["code"] = history["ts_code"].astype("string").str.split(".").str[0]
    history["fundamental_available_date"] = history["available_date"].astype("string")
    history["fundamental_period_end"] = history["end_date"].astype("string")
    history["fundamental_restatement_policy"] = (
        "latest_revision_visible_on_announcement_date"
    )
    drop_columns = [column for column in ("ts_code", "revenue", "n_income", "n_cashflow_act", "free_cashflow", "total_assets") if column in history.columns]
    return history.drop(columns=drop_columns).reset_index(drop=True)


def attach_point_in_time_features(prices: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if prices.empty or history.empty:
        return prices.copy()
    source_columns = [column for column in history.columns if column not in {"code", "available_date", "end_date"}]
    left = prices.reset_index(drop=True).copy()
    left["_row_order"] = np.arange(len(left))
    left["code"] = left["code"].astype("string")
    left["_merge_date"] = pd.to_datetime(
        left["trade_date"].astype("string").str[:8],
        format="%Y%m%d",
        errors="coerce",
    )
    wanted_codes = set(left["code"].dropna().astype(str))
    history_codes = history["code"].astype("string")
    selected = history_codes.isin(wanted_codes)
    right = history.loc[
        selected, ["code", "available_date", *source_columns]
    ].copy()
    right["code"] = right["code"].astype("string")
    right["_merge_date"] = pd.to_datetime(
        right["available_date"].astype("string").str[:8],
        format="%Y%m%d",
        errors="coerce",
    )
    right = (
        right.dropna(subset=["_merge_date"])
        .drop_duplicates(["code", "_merge_date"], keep="last")
        .drop(columns="available_date")
        .sort_values(["_merge_date", "code"], kind="stable")
    )
    existing = [column for column in source_columns if column in left.columns]
    if existing:
        left = left.drop(columns=existing)
    merged = pd.merge_asof(
        left.sort_values(["_merge_date", "code"], kind="stable"),
        right,
        on="_merge_date",
        by="code",
        direction="backward",
    )
    return (
        merged.sort_values("_row_order", kind="stable")
        .drop(columns=["_row_order", "_merge_date"])
        .reset_index(drop=True)
    )


def attach_qdii_point_in_time_features(
    prices: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Attach observable NAV and share history, then derive ETF-specific risks."""

    result = prices.copy()
    nav = frames.get("fund_nav", pd.DataFrame()).copy()
    if not nav.empty and {"ts_code", "ann_date", "nav_date", "unit_nav"}.issubset(nav.columns):
        nav["unit_nav"] = pd.to_numeric(nav["unit_nav"], errors="coerce")
        nav = nav.dropna(subset=["unit_nav"]).sort_values(["ts_code", "nav_date", "ann_date"])
        nav = nav.drop_duplicates(["ts_code", "nav_date"], keep="last")
        nav["nav_return_1"] = nav.groupby("ts_code")["unit_nav"].pct_change(fill_method=None)
        nav["nav_momentum_20"] = nav.groupby("ts_code")["unit_nav"].pct_change(20, fill_method=None)
        nav_history = nav[["ts_code", "ann_date", "nav_date", "unit_nav", "nav_return_1", "nav_momentum_20"]].copy()
        nav_history["code"] = nav_history["ts_code"].astype("string").str.split(".").str[0]
        nav_history["available_date"] = nav_history["ann_date"].astype("string")
        nav_history["end_date"] = nav_history["nav_date"].astype("string")
        result = attach_point_in_time_features(
            result,
            nav_history[["code", "available_date", "end_date", "nav_date", "unit_nav", "nav_return_1", "nav_momentum_20"]],
        )

    share = frames.get("fund_share", pd.DataFrame()).copy()
    if not share.empty and {"ts_code", "trade_date", "fd_share"}.issubset(share.columns):
        share["fd_share"] = pd.to_numeric(share["fd_share"], errors="coerce")
        share = share.dropna(subset=["fd_share"]).sort_values(["ts_code", "trade_date"])
        share["fund_share_change_20"] = share.groupby("ts_code")["fd_share"].pct_change(20, fill_method=None)
        share_history = share[["ts_code", "trade_date", "fd_share", "fund_share_change_20"]].copy()
        share_history["code"] = share_history["ts_code"].astype("string").str.split(".").str[0]
        share_history["available_date"] = share_history["trade_date"].astype("string")
        share_history["end_date"] = share_history["trade_date"].astype("string")
        result = attach_point_in_time_features(
            result,
            share_history[["code", "available_date", "end_date", "fd_share", "fund_share_change_20"]],
        )

    for column in ("unit_nav", "nav_return_1", "nav_momentum_20", "fd_share", "fund_share_change_20"):
        if column not in result.columns:
            result[column] = np.nan
    trade_dates = pd.to_datetime(result["trade_date"].astype("string").str[:8], format="%Y%m%d", errors="coerce")
    nav_dates = pd.to_datetime(result.get("nav_date"), format="%Y%m%d", errors="coerce")
    nav_is_fresh = nav_dates.notna() & trade_dates.sub(nav_dates).dt.days.between(0, 7)
    result["discount_premium"] = (
        pd.to_numeric(result.get("close"), errors="coerce")
        / pd.to_numeric(result["unit_nav"], errors="coerce").replace(0.0, np.nan)
        - 1.0
    ).where(nav_is_fresh)
    result = result.sort_values(["code", "trade_date"])
    result["premium_persistence_20"] = result.groupby("code")["discount_premium"].transform(
        lambda values: values.rolling(20, min_periods=5).mean()
    )
    result["tracking_difference_20"] = (
        pd.to_numeric(result.get("momentum_20"), errors="coerce")
        - pd.to_numeric(result["nav_momentum_20"], errors="coerce")
    )
    result["_tracking_daily"] = (
        pd.to_numeric(result.get("return_1"), errors="coerce")
        - pd.to_numeric(result["nav_return_1"], errors="coerce")
    )
    result["tracking_error_20"] = result.groupby("code")["_tracking_daily"].transform(
        lambda values: values.rolling(20, min_periods=10).std() * np.sqrt(252.0)
    )
    gap = pd.to_numeric(result.get("gap_return", pd.Series(np.nan, index=result.index)), errors="coerce")
    result["overseas_close_gap_proxy"] = gap - pd.to_numeric(result["nav_return_1"], errors="coerce")
    return result.drop(columns="_tracking_daily").sort_values(["code", "trade_date"]).reset_index(drop=True)


def attach_industry_membership(prices: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return prices.copy()
    result = prices.assign(_row_order=np.arange(len(prices))).copy()
    if members.empty or "ts_code" not in members.columns:
        result["industry"] = "unclassified"
        result["industry_l2"] = "unclassified"
        return result.drop(columns="_row_order")
    membership = members.copy()
    membership["code"] = membership["ts_code"].astype("string").str.split(".").str[0]
    membership["_in_date"] = pd.to_datetime(membership.get("in_date"), format="%Y%m%d", errors="coerce").fillna(pd.Timestamp("1900-01-01"))
    membership["_out_date"] = pd.to_datetime(membership.get("out_date"), format="%Y%m%d", errors="coerce")
    output: list[pd.DataFrame] = []
    for code, rows in result.groupby("code", sort=False):
        left = rows.copy()
        left["_trade_date"] = pd.to_datetime(left["trade_date"].astype("string").str[:8], format="%Y%m%d", errors="coerce")
        right = membership.loc[membership["code"] == str(code)].sort_values("_in_date")
        if right.empty:
            left["industry"] = "unclassified"
            left["industry_l2"] = "unclassified"
        else:
            left = pd.merge_asof(
                left.sort_values("_trade_date"),
                right[["_in_date", "_out_date", "l1_name", "l2_name"]],
                left_on="_trade_date",
                right_on="_in_date",
                direction="backward",
            )
            valid = left["_out_date"].isna() | (left["_trade_date"] < left["_out_date"])
            left["industry"] = left["l1_name"].where(valid).fillna("unclassified")
            left["industry_l2"] = left["l2_name"].where(valid).fillna("unclassified")
            left = left.drop(columns=["_in_date", "_out_date", "l1_name", "l2_name"])
        output.append(left.drop(columns="_trade_date"))
    return pd.concat(output, ignore_index=True).sort_values("_row_order").drop(columns="_row_order").reset_index(drop=True)


def add_industry_features(features: pd.DataFrame) -> pd.DataFrame:
    if features.empty or "industry" not in features.columns:
        return features.copy()
    result = features.copy()
    result["industry"] = result["industry"].fillna("unclassified").astype("string")
    for column in ("momentum_20", "realized_volatility_20", "return_1", "roe", "profit_growth"):
        if column not in result.columns:
            result[column] = np.nan
    result["_positive_return"] = pd.to_numeric(result.get("return_1"), errors="coerce").gt(0).astype(float)
    result["_positive_profit"] = pd.to_numeric(result.get("profit_growth"), errors="coerce").gt(0).astype(float)
    industry_daily = result.groupby(["trade_date", "industry"], as_index=False).agg(
        industry_momentum_20=("momentum_20", "median"),
        industry_volatility_20=("realized_volatility_20", "median"),
        industry_breadth=("_positive_return", "mean"),
        industry_profitability=("roe", "median"),
        industry_earnings_diffusion=("_positive_profit", "mean"),
    )
    market_momentum = result.groupby("trade_date")["momentum_20"].median().rename("_market_momentum_20")
    industry_daily = industry_daily.merge(market_momentum, on="trade_date", how="left")
    industry_daily["industry_relative_momentum_20"] = industry_daily["industry_momentum_20"] - industry_daily["_market_momentum_20"]
    industry_daily["industry_cycle_score"] = (
        np.tanh(industry_daily["industry_relative_momentum_20"].fillna(0.0) * 8.0)
        + (industry_daily["industry_breadth"].fillna(0.5) - 0.5) * 2.0
        + (industry_daily["industry_earnings_diffusion"].fillna(0.5) - 0.5)
    ) / 3.0
    industry_daily["industry_cycle"] = np.select(
        [
            industry_daily["industry_cycle_score"] > 0.35,
            industry_daily["industry_cycle_score"] > 0.0,
            industry_daily["industry_cycle_score"] > -0.35,
        ],
        ["expansion", "recovery", "slowdown"],
        default="contraction",
    )
    result = result.drop(columns=["_positive_return", "_positive_profit"])
    return result.merge(
        industry_daily.drop(columns="_market_momentum_20"),
        on=["trade_date", "industry"],
        how="left",
    )


def build_source_features(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Collapse normalized raw sources into one latest feature row per instrument."""

    instrument_sources = ("daily_basic", "moneyflow", "margin_detail", "fina_indicator", "income", "balancesheet", "cashflow", "fina_mainbz", "fund_nav", "fund_share")
    codes: set[str] = set()
    for source in instrument_sources:
        frame = frames.get(source, pd.DataFrame())
        if not frame.empty and "ts_code" in frame.columns:
            codes.update(frame["ts_code"].dropna().astype(str))
    if not codes:
        return pd.DataFrame(columns=["code", "ts_code"])
    output = pd.DataFrame({"ts_code": sorted(codes)})
    output["code"] = output["ts_code"].str.split(".").str[0]

    daily = frames.get("daily_basic", pd.DataFrame()).copy()
    if not daily.empty:
        daily["turnover_rate"] = pd.to_numeric(daily.get("turnover_rate"), errors="coerce")
        turnover_change = daily.sort_values("trade_date").groupby("ts_code")["turnover_rate"].agg(
            lambda values: values.iloc[-1] / values.iloc[0] - 1.0 if len(values) > 1 and values.iloc[0] else np.nan
        )
        latest_daily = _latest(daily).copy()
        latest_daily["turnover_change"] = latest_daily["ts_code"].map(turnover_change)
        for column in ("pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate"):
            if column in latest_daily.columns:
                latest_daily[column] = pd.to_numeric(latest_daily[column], errors="coerce")
        for column in ("pe_ttm", "pb", "total_mv"):
            if column in latest_daily.columns:
                latest_daily[f"{column}_percentile"] = latest_daily[column].rank(pct=True)
        keep = [column for column in latest_daily.columns if column in {"ts_code", "pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate", "turnover_change", "pe_ttm_percentile", "pb_percentile", "total_mv_percentile"}]
        output = output.merge(latest_daily[keep], on="ts_code", how="left")

    flow = frames.get("moneyflow", pd.DataFrame()).copy()
    if not flow.empty:
        for column in ("buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"):
            flow[column] = pd.to_numeric(flow.get(column, 0.0), errors="coerce").fillna(0.0)
        flow["flow_net_large"] = flow["buy_lg_amount"] + flow["buy_elg_amount"] - flow["sell_lg_amount"] - flow["sell_elg_amount"]
        flow["flow_persistence_5"] = flow.sort_values("trade_date").groupby("ts_code")["flow_net_large"].transform(lambda values: values.tail(5).gt(0).mean())
        output = output.merge(_latest(flow)[["ts_code", "flow_net_large", "flow_persistence_5"]], on="ts_code", how="left")

    income = _latest(frames.get("income", pd.DataFrame()), ("ann_date", "end_date"))
    cashflow = _latest(frames.get("cashflow", pd.DataFrame()), ("ann_date", "end_date"))
    balance = _latest(frames.get("balancesheet", pd.DataFrame()), ("ann_date", "end_date"))
    if not income.empty:
        financial = income.copy()
        if not cashflow.empty:
            financial = financial.merge(cashflow, on="ts_code", how="left", suffixes=("", "_cash"))
        if not balance.empty:
            financial = financial.merge(balance, on="ts_code", how="left", suffixes=("", "_balance"))
        n_income = pd.to_numeric(financial.get("n_income"), errors="coerce")
        operating_cash = pd.to_numeric(financial.get("n_cashflow_act"), errors="coerce")
        financial["cash_flow_quality"] = _safe_ratio(operating_cash, n_income.abs())
        if "total_assets" in financial.columns and "revenue" in financial.columns:
            financial["asset_turnover"] = _safe_ratio(pd.to_numeric(financial["revenue"], errors="coerce"), pd.to_numeric(financial["total_assets"], errors="coerce"))
        keep = [column for column in ("ts_code", "cash_flow_quality", "asset_turnover") if column in financial.columns]
        output = output.merge(financial[keep], on="ts_code", how="left")

    fund_share = frames.get("fund_share", pd.DataFrame()).copy()
    if not fund_share.empty:
        fund_share["fd_share"] = pd.to_numeric(fund_share["fd_share"], errors="coerce")
        share_change = fund_share.sort_values("trade_date").groupby("ts_code")["fd_share"].agg(
            lambda values: values.iloc[-1] / values.iloc[0] - 1.0 if len(values) > 1 and values.iloc[0] else np.nan
        )
        output["fund_share_change"] = output["ts_code"].map(share_change)

    for source, target in (("index_global", "global_index_momentum"), ("fx_daily", "rmb_depreciation")):
        frame = frames.get(source, pd.DataFrame()).copy()
        if not frame.empty and "close" in frame.columns:
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            series = frame.sort_values("trade_date").groupby("ts_code")["close"].agg(
                lambda values: values.iloc[-1] / values.iloc[0] - 1.0 if len(values) > 1 and values.iloc[0] else np.nan
            )
            output[target] = float(series.mean()) if not series.empty else np.nan
    return output
