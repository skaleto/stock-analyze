"""Source-dated underlying-index look-through for cross-border ETFs."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping


PROFILE_FILE = Path(__file__).with_name("index_profiles.json")


class IndexProfileInvalid(ValueError):
    """Bundled or injected index profile data is malformed."""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validate_profile(key: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise IndexProfileInvalid(f"index_profile_invalid:{key}")
    profile = dict(raw)
    if str(profile.get("index_key") or "") != key:
        raise IndexProfileInvalid(f"index_profile_key_mismatch:{key}")
    try:
        date.fromisoformat(str(profile["as_of"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise IndexProfileInvalid(f"index_profile_date_invalid:{key}") from exc
    if not str(profile.get("source_url") or "").startswith("https://"):
        raise IndexProfileInvalid(f"index_profile_source_invalid:{key}")
    constituents = profile.get("constituents")
    if not isinstance(constituents, list) or not constituents:
        raise IndexProfileInvalid(f"index_profile_constituents_missing:{key}")
    return profile


def load_index_profiles(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    profile_path = Path(path) if path else PROFILE_FILE
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    raw_profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(raw_profiles, dict):
        raise IndexProfileInvalid("index_profiles_missing")
    return {
        str(key): _validate_profile(str(key), value)
        for key, value in raw_profiles.items()
    }


def profile_for_index(
    index_key: str | None,
    *,
    profiles: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not index_key:
        return None
    source = profiles or load_index_profiles()
    profile = source.get(str(index_key))
    return dict(profile) if profile else None


def _row_value(row: Mapping[str, Any]) -> float:
    for key in ("market_value", "target_value", "target_weight", "gross_amount"):
        value = _number(row.get(key))
        if value is not None and value > 0:
            return value
    shares = _number(row.get("shares"))
    price = _number(row.get("last_price") or row.get("price"))
    if shares is not None and price is not None and shares * price > 0:
        return shares * price
    return 1.0


def _weighted_rows(values: Mapping[str, float], *, key_name: str = "label") -> list[dict[str, Any]]:
    return [
        {key_name: key, "weight": value}
        for key, value in sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_portfolio_lookthrough(
    rows: Iterable[Mapping[str, Any]],
    *,
    profiles: Mapping[str, dict[str, Any]] | None = None,
    source: str,
) -> dict[str, Any]:
    """Aggregate ETF rows into measured index, country, sector, and company exposure."""

    profile_map = dict(profiles or load_index_profiles())
    holdings = [
        dict(row)
        for row in rows
        if str(row.get("side") or "buy").lower() != "sell" and row.get("code")
    ]
    total = sum(_row_value(row) for row in holdings)
    if total <= 0 or not holdings:
        return {
            "status": "unavailable",
            "source": source,
            "profile_coverage": 0.0,
            "company_weight_coverage": 0.0,
            "indexes": [],
            "countries": [],
            "sectors": [],
            "companies": [],
            "company_symbols": [],
            "sources": [],
            "unsupported_indexes": [],
        }

    index_values: defaultdict[str, float] = defaultdict(float)
    index_labels: dict[str, str] = {}
    country_values: defaultdict[str, float] = defaultdict(float)
    sector_values: defaultdict[str, float] = defaultdict(float)
    company_values: defaultdict[str, float] = defaultdict(float)
    company_meta: dict[str, dict[str, str]] = {}
    company_symbols: set[str] = set()
    sources: dict[str, dict[str, Any]] = {}
    unsupported: set[str] = set()
    profile_coverage = 0.0
    company_weight_coverage = 0.0

    for row in holdings:
        allocation = _row_value(row) / total
        index_key = str(row.get("index_key") or "unknown")
        profile = profile_map.get(index_key)
        index_values[index_key] += allocation
        index_labels[index_key] = str(
            (profile or {}).get("name") or row.get("theme") or index_key
        )
        country = str(row.get("country") or (profile or {}).get("country") or "未知")
        country_values[country] += allocation
        if profile is None:
            unsupported.add(index_key)
            continue
        profile_coverage += allocation
        sources[index_key] = {
            "index_key": index_key,
            "name": profile.get("name"),
            "as_of": profile.get("as_of"),
            "source_url": profile.get("source_url"),
            "source_label": profile.get("source_label"),
        }
        sector_rows = profile.get("sector_weights") or []
        for sector in sector_rows:
            weight = _number(sector.get("weight"))
            if weight is not None and weight >= 0:
                sector_values[str(sector.get("label") or "未分类")] += allocation * weight
        for constituent in profile.get("constituents") or []:
            symbol = str(constituent.get("symbol") or "")
            if not symbol:
                continue
            company_symbols.add(symbol)
            company_meta[symbol] = {
                "name": str(constituent.get("name") or symbol),
                "sector": str(constituent.get("sector") or "未分类"),
            }
            weight = _number(constituent.get("weight"))
            if weight is None or weight < 0:
                continue
            contribution = allocation * weight
            company_values[symbol] += contribution
            company_weight_coverage += contribution

    indexes = [
        {
            "index_key": key,
            "label": index_labels[key],
            "weight": value,
            "profile_available": key in profile_map,
        }
        for key, value in sorted(index_values.items(), key=lambda item: (-item[1], item[0]))
    ]
    companies = [
        {
            "symbol": symbol,
            "name": company_meta[symbol]["name"],
            "sector": company_meta[symbol]["sector"],
            "weight": value,
        }
        for symbol, value in sorted(company_values.items(), key=lambda item: (-item[1], item[0]))
    ]
    status = (
        "complete"
        if profile_coverage >= 0.999 and company_weight_coverage >= 0.95
        else "partial"
        if profile_coverage > 0
        else "unavailable"
    )
    return {
        "status": status,
        "source": source,
        "profile_coverage": profile_coverage,
        "company_weight_coverage": company_weight_coverage,
        "indexes": indexes,
        "countries": _weighted_rows(country_values),
        "sectors": _weighted_rows(sector_values),
        "companies": companies,
        "company_symbols": sorted(company_symbols),
        "sources": sorted(sources.values(), key=lambda item: str(item.get("index_key"))),
        "unsupported_indexes": sorted(unsupported),
    }


__all__ = [
    "IndexProfileInvalid",
    "build_portfolio_lookthrough",
    "load_index_profiles",
    "profile_for_index",
]
