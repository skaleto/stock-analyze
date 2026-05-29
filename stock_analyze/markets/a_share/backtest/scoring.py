"""Full-pipeline scoring adapter for the backtest engine.

Bridges the live ``stock_analyze.factor_pipeline.process_factors`` into
the backtest's ``PointInTimeView`` so the gate scores an overlay with the
SAME factor model live trading uses — not the MVP low-PE proxy. Implements
OpenSpec change ``bridge-factor-pipeline-into-backtest``.

``score_with_overlay`` assembles a per-stock factor frame from the view:

  pe                  ← daily_basic.pe_ttm
  pb                  ← daily_basic.pb
  dividend_yield      ← daily_basic.dv_ttm
  roe                 ← fina_indicator.roe            (ann_date-filtered)
  gross_margin        ← fina_indicator.grossprofit_margin
  debt_ratio          ← fina_indicator.debt_to_assets
  net_profit_growth   ← fina_indicator.netprofit_yoy
  momentum_20/60      ← trailing close return from daily/*.csv
  low_volatility_60   ← trailing daily-return std
  industry            ← stock_basic.industry          (for neutralization)

then delegates to ``factor_pipeline.process_factors`` (same winsorize /
z-score / industry-neutralize / weight-combine as live) and returns the
top-N-eligible rows in the same shape the engine's ``_compute_signals``
emits.

Only the factors the overlay actually weights are assembled — assembling
momentum (the expensive part: a trailing price-panel scan) is skipped
when the overlay has no momentum/volatility factors.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from ....factor_pipeline import is_broadcast_factor, process_factors


logger = logging.getLogger(__name__)

# Map an overlay factor name → (source, column). "db" = daily_basic,
# "fina" = fina_indicator. Momentum/volatility are computed, not mapped.
_DAILY_BASIC_FACTORS = {
    "pe": "pe_ttm",
    "pb": "pb",
    "dividend_yield": "dv_ttm",
}
_FINA_FACTORS = {
    "roe": "roe",
    "gross_margin": "grossprofit_margin",
    "debt_ratio": "debt_to_assets",
    "net_profit_growth": "netprofit_yoy",
}
_MOMENTUM_FACTORS = {"momentum_20", "momentum_60", "low_volatility_60"}


def score_with_overlay(
    view: Any,
    overlay: dict[str, Any],
    as_of: date,
    universe: list[str],
    *,
    broadcast_values: dict[str, float | None] | None = None,
) -> list[dict[str, Any]]:
    """Score the universe with the overlay's full factor pipeline.

    Returns one row per (account, code) — ``{signal_date, account_id,
    ts_code, score}`` — matching ``engine._compute_signals``'s output, so
    the engine can delegate to this verbatim.
    """
    factors = overlay.get("factors", {}) or {}
    if not factors:
        return []

    daily_basic = view.daily_basic(as_of=as_of)
    if daily_basic.empty:
        return []

    available = set(view.universe(as_of=as_of, indices=universe))
    db = daily_basic[daily_basic["ts_code"].isin(available)].copy()
    if db.empty:
        return []

    frame = _assemble_factor_frame(view, db, factors, as_of)
    if frame.empty:
        return []

    # Resolve broadcast factors (e.g. sentiment) via the view's point-in-time
    # accessor. In the training/validation windows this returns 0.0 (no
    # historical sentiment), so broadcast factors contribute nothing — the
    # gate checks factor structure, not sentiment-conditioned alpha.
    if broadcast_values is None:
        broadcast_values = {
            name: view.broadcast(name, as_of)
            for name in factors
            if is_broadcast_factor(name)
        } or None

    factor_processing = overlay.get("factor_processing", {})
    scored, _factor_table = process_factors(
        frame,
        factors=factors,
        factor_processing=factor_processing,
        broadcast_values=broadcast_values,
    )

    # Rank by score desc and take each account's top_n — matching the MVP
    # _compute_signals contract (it returns exactly top_n rows per account,
    # which _build_pending_batch then treats as the full target set).
    ranked = scored.sort_values("score", ascending=False)
    rows: list[dict[str, Any]] = []
    for account in overlay.get("accounts", []):
        acc_id = account["id"]
        top_n = int(account.get("top_n", 50))
        for _, r in ranked.head(top_n).iterrows():
            rows.append({
                "signal_date": as_of.isoformat(),
                "account_id": acc_id,
                "ts_code": r["code"],
                "score": float(r["score"]),
            })
    return rows


def _assemble_factor_frame(
    view: Any,
    db: pd.DataFrame,
    factors: dict[str, Any],
    as_of: date,
) -> pd.DataFrame:
    """Build the candidates DataFrame process_factors consumes.

    Columns: ``code`` + each non-broadcast factor the overlay weights +
    ``industry``. Broadcast factors (``<agent>_market_sentiment_1w``) are
    not per-stock columns — they enter via ``broadcast_values`` instead.
    """
    frame = pd.DataFrame({"code": db["ts_code"].astype(str).tolist()})

    # 1. daily_basic-sourced factors (direct column copy)
    for name, col in _DAILY_BASIC_FACTORS.items():
        if name in factors and col in db.columns:
            frame[name] = pd.to_numeric(db[col].values, errors="coerce")

    # 2. fina_indicator-sourced factors (per-code latest visible row)
    fina_needed = [n for n in _FINA_FACTORS if n in factors]
    if fina_needed:
        fina_cols: dict[str, list[float | None]] = {n: [] for n in fina_needed}
        for code in frame["code"]:
            latest = _latest_fina_row(view, code, as_of)
            for name in fina_needed:
                src = _FINA_FACTORS[name]
                val = latest.get(src) if latest is not None else None
                fina_cols[name].append(_num_or_none(val))
        for name in fina_needed:
            frame[name] = fina_cols[name]

    # 3. momentum / volatility (trailing price panel; only if overlay uses them)
    mom_needed = [n for n in _MOMENTUM_FACTORS if n in factors]
    if mom_needed:
        panel = _trailing_close_panel(view, as_of, lookback=61)
        codes = frame["code"].tolist()
        if "momentum_20" in mom_needed:
            frame["momentum_20"] = [_panel_return(panel, c, 20) for c in codes]
        if "momentum_60" in mom_needed:
            frame["momentum_60"] = [_panel_return(panel, c, 60) for c in codes]
        if "low_volatility_60" in mom_needed:
            frame["low_volatility_60"] = [_panel_volatility(panel, c, 60) for c in codes]

    # 4. industry (for neutralization) — from stock_basic
    frame["industry"] = _industry_map(view, frame["code"].tolist())

    return frame


def _latest_fina_row(view: Any, code: str, as_of: date) -> dict[str, Any] | None:
    fina = view.fina_for_code(code, as_of=as_of)
    if fina is None or fina.empty:
        return None
    # fina_for_code already ann_date-filtered to <= as_of; take the most
    # recently-announced row (last by end_date if present, else last row).
    sort_col = "end_date" if "end_date" in fina.columns else None
    ordered = fina.sort_values(sort_col) if sort_col else fina
    return ordered.iloc[-1].to_dict()


def _trailing_close_panel(view: Any, as_of: date, lookback: int) -> dict[str, list[float]]:
    """Build {code: [close, …]} over the last ``lookback`` trading days <= as_of.

    Reads the trailing daily/*.csv files once each (lookback files), not
    once-per-stock, so the cost is O(lookback) file reads per signal day.
    """
    daily_dir = view.cache_root / "daily"
    if not daily_dir.exists():
        return {}
    dates = sorted(
        p.stem for p in daily_dir.glob("*.csv")
        if p.stem <= as_of.isoformat()
    )
    recent = dates[-lookback:]
    panel: dict[str, list[float]] = {}
    for d in recent:
        path = daily_dir / f"{d}.csv"
        try:
            df = pd.read_csv(path, dtype={"ts_code": str, "trade_date": str})
        except Exception:  # noqa: BLE001
            continue
        if df.empty or "close" not in df.columns:
            continue
        for code, close in zip(df["ts_code"].astype(str), pd.to_numeric(df["close"], errors="coerce")):
            if pd.notna(close):
                panel.setdefault(code, []).append(float(close))
    return panel


def _panel_return(panel: dict[str, list[float]], code: str, lookback: int) -> float | None:
    closes = panel.get(code, [])
    if len(closes) < lookback + 1:
        return None
    prior = closes[-lookback - 1]
    last = closes[-1]
    if prior <= 0:
        return None
    return last / prior - 1.0


def _panel_volatility(panel: dict[str, list[float]], code: str, lookback: int) -> float | None:
    closes = panel.get(code, [])
    if len(closes) < lookback + 1:
        return None
    series = pd.Series(closes[-(lookback + 1):])
    rets = series.pct_change().dropna()
    if rets.empty:
        return None
    return float(rets.std())


def _industry_map(view: Any, codes: list[str]) -> list[str]:
    sb_path = view.cache_root / "stock_basic.csv"
    if not sb_path.exists():
        return ["未分类"] * len(codes)
    try:
        sb = pd.read_csv(sb_path, dtype={"ts_code": str})
    except Exception:  # noqa: BLE001
        return ["未分类"] * len(codes)
    if sb.empty or "industry" not in sb.columns:
        return ["未分类"] * len(codes)
    lookup = dict(zip(sb["ts_code"].astype(str), sb["industry"].astype(str)))
    return [lookup.get(c) or "未分类" for c in codes]


def _num_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


__all__ = ["score_with_overlay"]
