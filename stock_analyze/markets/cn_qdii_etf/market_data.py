"""Daily shared market-data preparation for mainland-listed QDII products."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ...utils import read_json, write_json
from .data_provider import make_provider, normalize_ts_code
from .research_catalog import build_research_catalog


def _date_parts(as_of: str | None) -> tuple[str, str]:
    resolved = date.fromisoformat(as_of) if as_of else date.today()
    return resolved.isoformat(), resolved.strftime("%Y%m%d")


def _latest_trade_date(frame: pd.DataFrame) -> str | None:
    if frame is None or frame.empty or "trade_date" not in frame.columns:
        return None
    dates = frame["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
    valid = dates[dates.str.fullmatch(r"\d{8}")]
    return str(valid.max()) if not valid.empty else None


def _latest_feature_codes(root: Path, as_of_key: str) -> set[str]:
    feature_dir = root / "data" / "research" / "features" / "cn_qdii_etf"
    candidates = [
        path
        for path in feature_dir.glob("*.parquet")
        if len(path.stem) == 8 and path.stem.isdigit() and path.stem <= as_of_key
    ]
    if not candidates:
        return set()
    frame = pd.read_parquet(max(candidates, key=lambda path: path.stem), columns=["code"])
    return {
        normalize_ts_code(code)
        for code in frame["code"].dropna().astype(str)
        if str(code).strip()
    }


def _collect_operational_codes(root: Path) -> set[str]:
    search_roots = [
        root / "data" / "cn_qdii_etf",
        root / "data" / "model_iterations" / "cn_qdii_etf",
    ]
    paths: set[Path] = set()
    for search_root in search_roots:
        paths.update(search_root.glob("**/state.json"))
        paths.update(search_root.glob("**/pending_orders.json"))

    codes: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            code = value.get("code")
            if code:
                codes.add(normalize_ts_code(str(code)))
            positions = value.get("positions")
            if isinstance(positions, dict):
                codes.update(normalize_ts_code(str(code)) for code in positions)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in sorted(paths):
        visit(read_json(path, {}))
    return codes


def _universe_codes(snapshot: dict[str, Any]) -> set[str]:
    scopes = snapshot.get("scopes", {})
    if not isinstance(scopes, dict):
        return set()
    return {
        normalize_ts_code(str(row["code"]))
        for rows in scopes.values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict) and row.get("code")
    }


def prepare_market_data(
    *,
    repo_root: str | Path,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Warm one point-in-time ``fund_daily`` cache for every research product."""

    root = Path(repo_root)
    as_of_iso, as_of_key = _date_parts(as_of)
    shared_dir = root / "data" / "cn_qdii_etf" / "shared"
    cache_dir = shared_dir / "cache"
    provider = make_provider(
        cache_dir=cache_dir,
        offline=False,
        as_of=as_of_iso,
    )
    errors: list[dict[str, str]] = []
    latest_dates: dict[str, str | None] = {}
    try:
        basic = provider._fund_basic(refresh=True, as_of_key=as_of_key)
        catalog = build_research_catalog(basic, as_of=as_of_iso)
        universe_codes = _universe_codes(provider.universe_snapshot(as_of_iso))
        catalog_codes = {
            normalize_ts_code(code)
            for code in catalog.get("code", pd.Series(dtype=str)).dropna().astype(str)
        }
        feature_codes = _latest_feature_codes(root, as_of_key)
        operational_codes = _collect_operational_codes(root)
        codes = sorted(
            catalog_codes | universe_codes | feature_codes | operational_codes
        )
        if not codes:
            raise ValueError("empty_qdii_research_catalog")
        for code in codes:
            try:
                latest_dates[code] = _latest_trade_date(
                    provider._fund_daily(code, as_of_key)
                )
            except Exception as exc:  # noqa: BLE001 - bounded in snapshot
                errors.append({"code": code, "message": str(exc)[:300]})
        fresh_codes = sum(
            1 for trade_date in latest_dates.values() if trade_date == as_of_key
        )
        available_codes = sum(1 for trade_date in latest_dates.values() if trade_date)
        status = (
            "success"
            if fresh_codes == available_codes and available_codes
            else ("stale" if available_codes else "failed")
        )
        snapshot = {
            "schema_version": 1,
            "market": "cn_qdii_etf",
            "as_of": as_of_iso,
            "status": status,
            "catalog_codes": len(catalog_codes),
            "universe_codes": len(universe_codes),
            "feature_codes": len(feature_codes),
            "operational_codes": len(operational_codes),
            "target_codes": len(codes),
            "target_code_list": codes,
            "available_codes": available_codes,
            "fresh_codes": fresh_codes,
            "stale_codes": max(available_codes - fresh_codes, 0),
            "errors": errors,
            "latest_trade_dates": latest_dates,
        }
        write_json(shared_dir / f"market_snapshot_{as_of_iso}.json", snapshot)
        return snapshot
    finally:
        provider.persist_health()


__all__ = ["prepare_market_data"]
