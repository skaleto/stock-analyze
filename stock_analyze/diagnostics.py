"""Factor diagnostics: per-run snapshots, coverage logging, forward IC."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .data_provider import AkshareProvider
from .store import PortfolioStore
from .utils import now_iso


def compute_pending_forward_ic(
    config: dict[str, Any],
    store: PortfolioStore,
    provider: AkshareProvider,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """Compute forward Spearman rank IC for past snapshots whose forward horizon
    has elapsed. Writes one row per (signal_date, account_id, factor) to
    ``forward_ic.csv``. Already-computed rows are not duplicated.

    Returns the list of new rows written. Insufficient-history snapshots get a
    placeholder row so the caller can show "等待 5 个交易日" in the dashboard.
    """

    snapshots = store.list_factor_runs()
    if not snapshots:
        return []
    horizon = int(config.get("performance", {}).get("forward_ic_horizon_days", 5))
    today = as_of or date.today().isoformat()

    existing = store.read_forward_ic()
    finalized_keys: set[tuple[str, str, str]] = set()
    if not existing.empty:
        for _, row in existing.iterrows():
            if str(row.get("ic_status")) == "ok":
                finalized_keys.add((str(row["signal_date"]), str(row["account_id"]), str(row["factor"])))

    new_rows: list[dict[str, Any]] = []
    for snapshot_path in snapshots:
        try:
            snapshot = pd.read_csv(snapshot_path, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        if snapshot.empty or "signal_date" not in snapshot.columns:
            continue
        for (signal_date, account_id), group in snapshot.groupby(["signal_date", "account_id"]):
            forward_ready = _has_horizon_elapsed(str(signal_date), today, horizon)
            for factor, factor_rows in group.groupby("factor"):
                key = (str(signal_date), str(account_id), str(factor))
                if key in finalized_keys:
                    continue
                if not forward_ready:
                    new_rows.append(_insufficient_row(signal_date, account_id, factor))
                    continue
                ic, sample = _compute_ic(factor_rows, provider, str(signal_date), horizon)
                if ic is None:
                    new_rows.append(_insufficient_row(signal_date, account_id, factor, reason="forward_return_missing"))
                    continue
                new_rows.append(
                    {
                        "signal_date": signal_date,
                        "account_id": account_id,
                        "factor": factor,
                        "ic": round(float(ic), 6),
                        "sample_size": int(sample),
                        "ic_status": "ok",
                        "computed_at": now_iso(),
                    }
                )

    if new_rows:
        store.append_forward_ic(new_rows)
    return new_rows


def _has_horizon_elapsed(signal_date: str, today: str, horizon: int) -> bool:
    try:
        delta = (pd.to_datetime(today) - pd.to_datetime(signal_date)).days
    except Exception:  # noqa: BLE001
        return False
    return delta >= horizon * 7 / 5  # rough calendar-day padding for trading-day horizon


def _insufficient_row(signal_date: Any, account_id: Any, factor: Any, reason: str = "insufficient_history") -> dict[str, Any]:
    return {
        "signal_date": str(signal_date),
        "account_id": str(account_id),
        "factor": str(factor),
        "ic": None,
        "sample_size": 0,
        "ic_status": reason,
        "computed_at": now_iso(),
    }


def _compute_ic(
    factor_rows: pd.DataFrame,
    provider: AkshareProvider,
    signal_date: str,
    horizon: int,
) -> tuple[float | None, int]:
    pairs: list[tuple[float, float]] = []
    for _, row in factor_rows.iterrows():
        zscore = row.get("zscore")
        if pd.isna(zscore):
            continue
        forward_return = _forward_return_for_code(str(row["code"]).zfill(6), provider, signal_date, horizon)
        if forward_return is None:
            continue
        pairs.append((float(zscore), float(forward_return)))
    if len(pairs) < 3:
        return None, len(pairs)
    z_values = pd.Series([p[0] for p in pairs])
    r_values = pd.Series([p[1] for p in pairs])
    ic = _spearman_corr(z_values, r_values)
    if ic is None or pd.isna(ic):
        return None, len(pairs)
    return float(ic), len(pairs)


def _spearman_corr(a: pd.Series, b: pd.Series) -> float | None:
    """Spearman rank correlation without a scipy dependency."""

    if len(a) != len(b) or len(a) < 2:
        return None
    ranks_a = a.rank(method="average")
    ranks_b = b.rank(method="average")
    if ranks_a.std(ddof=0) == 0 or ranks_b.std(ddof=0) == 0:
        return None
    return float(ranks_a.corr(ranks_b))  # Pearson on ranks == Spearman


def _forward_return_for_code(code: str, provider: AkshareProvider, signal_date: str, horizon: int) -> float | None:
    history = provider.price_history(code, as_of=None, days=260)
    if history.empty:
        return None
    history = history.copy()
    history["_date"] = pd.to_datetime(history["日期"]).dt.date
    signal_d = pd.to_datetime(signal_date).date()
    on_or_after = history[history["_date"] >= signal_d]
    if on_or_after.empty:
        return None
    start_price = _safe_close(on_or_after.iloc[0])
    if start_price is None or start_price <= 0:
        return None
    if len(on_or_after) <= horizon:
        return None
    end_price = _safe_close(on_or_after.iloc[horizon])
    if end_price is None or end_price <= 0:
        return None
    return float(end_price) / float(start_price) - 1.0


def _safe_close(row: pd.Series) -> float | None:
    value = row.get("收盘")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
