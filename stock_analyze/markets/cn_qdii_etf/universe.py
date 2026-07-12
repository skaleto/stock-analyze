"""Dynamic universe rules for mainland-listed overseas-exposure ETFs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


US_EXPOSURE = [
    "513100.SH",
    "159941.SZ",
    "513500.SH",
    "159655.SZ",
    "513300.SH",
    "159632.SZ",
    "513850.SH",
]

HK_EXPOSURE = [
    "513130.SH",
    "159920.SZ",
    "513180.SH",
    "513330.SH",
    "513060.SH",
    "159726.SZ",
    "513690.SH",
]

UNIVERSES = {
    "us_exposure": US_EXPOSURE,
    "hk_exposure": HK_EXPOSURE,
}


def _rule(
    scope: str,
    index_key: str,
    theme: str,
    pattern: str,
    *,
    sector: str,
) -> dict[str, str]:
    return {
        "scope": scope,
        "index_key": index_key,
        "theme": theme,
        "pattern": pattern,
        "sector": sector,
        "country": "美国" if scope == "us_exposure" else "香港",
        "exposure_group": "美国市场" if scope == "us_exposure" else "香港市场",
    }


# Specific themes precede broad indexes so a biotechnology or consumer ETF is
# not collapsed into its parent S&P/Nasdaq family.
INDEX_RULES: tuple[dict[str, str], ...] = (
    _rule("us_exposure", "sp_oil_gas", "美国油气上游", r"标普.*石油天然气.*勘探.*生产", sector="能源"),
    _rule("us_exposure", "sp_biotech", "美国生物科技", r"标普.*生物科技", sector="医疗保健"),
    _rule("us_exposure", "nasdaq_biotech", "纳斯达克生物科技", r"纳斯达克.*生物科技", sector="医疗保健"),
    _rule("us_exposure", "sp_500_consumer", "美国消费精选", r"标普(?:500)?消费精选|美国品质消费", sector="可选消费"),
    _rule("us_exposure", "nasdaq_technology", "纳斯达克科技", r"纳斯达克科技市值加权|纳指科技", sector="信息技术"),
    _rule("us_exposure", "msci_us_50", "MSCI美国50", r"MSCI美国50", sector="美国大盘"),
    _rule("us_exposure", "dow_jones_industrial", "道琼斯工业平均", r"道琼斯工业平均", sector="美国大盘"),
    _rule("us_exposure", "us_reit", "美国REIT", r"道琼斯美国精选REIT", sector="房地产"),
    _rule("us_exposure", "sp_500", "标普500", r"标普500", sector="美国大盘"),
    _rule("us_exposure", "nasdaq_100", "纳斯达克100", r"纳斯达克100|纳指100", sector="科技成长"),
    _rule("hk_exposure", "hang_seng_internet", "恒生互联网", r"恒生互联网科技业", sector="互联网"),
    _rule("hk_exposure", "csi_hk_connect_internet", "港股通互联网", r"中证港股通互联网", sector="互联网"),
    _rule("hk_exposure", "hang_seng_connect_tech", "恒生港股通科技", r"恒生港股通科技主题", sector="信息技术"),
    _rule("hk_exposure", "csi_hk_connect_information_tech", "港股通信息技术", r"中证港股通信息技术", sector="信息技术"),
    _rule("hk_exposure", "csi_hk_connect_tech", "中证港股通科技", r"中证港股通科技", sector="信息技术"),
    _rule("hk_exposure", "guozheng_hk_connect_tech", "国证港股通科技", r"国证港股通科技", sector="信息技术"),
    _rule("hk_exposure", "hang_seng_tech", "恒生科技", r"恒生科技指数|恒生科技ETF", sector="科技成长"),
    _rule("hk_exposure", "hang_seng_healthcare", "恒生医疗保健", r"恒生医疗保健", sector="医疗保健"),
    _rule("hk_exposure", "csi_hk_connect_healthcare", "港股通医疗", r"中证港股通医疗", sector="医疗保健"),
    _rule("hk_exposure", "hang_seng_biotech", "恒生生物科技", r"恒生生物科技", sector="生物科技"),
    _rule("hk_exposure", "hk_innovative_drug", "港股创新药", r"(?:恒生|中证|国证).*港股通.*创新药|恒生创新药", sector="创新药"),
    _rule("hk_exposure", "hang_seng_consumer", "恒生消费", r"恒生消费", sector="消费"),
    _rule("hk_exposure", "hk_connect_consumer", "港股通消费", r"(?:中证|国证)港股通消费", sector="消费"),
    _rule("hk_exposure", "hang_seng_connect_high_dividend_low_vol", "港股高股息低波", r"恒生港股通高股息低波", sector="红利"),
    _rule("hk_exposure", "hang_seng_connect_high_dividend", "恒生港股通高股息", r"恒生港股通.*高股息", sector="红利"),
    _rule("hk_exposure", "csi_hk_connect_high_dividend", "港股通高股息", r"中证.*港股通.*高股息|中证港股通央企红利", sector="红利"),
    _rule("hk_exposure", "hk_state_owned", "香港央国企", r"香港内地国有企业|港股通.*央企", sector="央国企"),
    _rule("hk_exposure", "hk_connect_financials", "港股通金融", r"港股通.*(?:金融|内地金融)", sector="金融"),
    _rule("hk_exposure", "hk_connect_auto", "港股通汽车", r"港股通.*汽车", sector="汽车"),
    _rule("hk_exposure", "csi_hk_connect_50", "港股通50", r"中证港股通50", sector="香港大盘"),
    _rule("hk_exposure", "hang_seng_china_enterprises", "恒生中国企业", r"恒生中国企业", sector="中国企业"),
    _rule("hk_exposure", "hang_seng", "恒生指数", r"香港恒生指数|恒生ETF", sector="香港大盘"),
)


def _metadata_from_rule(rule: Mapping[str, str]) -> dict[str, str]:
    return {
        key: str(rule[key])
        for key in (
            "scope",
            "index_key",
            "theme",
            "sector",
            "country",
            "exposure_group",
        )
    }


_RULES_BY_INDEX = {rule["index_key"]: rule for rule in INDEX_RULES}

_STATIC_INDEX_KEYS = {
    "513100.SH": "nasdaq_100",
    "159941.SZ": "nasdaq_100",
    "513500.SH": "sp_500",
    "159655.SZ": "sp_500",
    "513300.SH": "nasdaq_100",
    "159632.SZ": "nasdaq_100",
    "513850.SH": "msci_us_50",
    "513130.SH": "hang_seng_tech",
    "159920.SZ": "hang_seng",
    "513180.SH": "hang_seng_tech",
    "513330.SH": "hang_seng_internet",
    "513060.SH": "hang_seng_healthcare",
    "159726.SZ": "hang_seng_connect_high_dividend",
    "513690.SH": "hang_seng_connect_high_dividend",
}

ETF_METADATA: dict[str, dict[str, str]] = {
    code: _metadata_from_rule(_RULES_BY_INDEX[index_key])
    for code, index_key in _STATIC_INDEX_KEYS.items()
}


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _date_key(value: Any) -> str:
    return _text(value).replace("-", "")[:8]


def _as_of_key(value: str | date | None) -> str:
    if value is None:
        return date.today().strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return _date_key(value)


def _matching_rule(name: str, benchmark: str) -> dict[str, str] | None:
    haystack = f"{name} {benchmark}"
    for rule in INDEX_RULES:
        if re.search(rule["pattern"], haystack, flags=re.IGNORECASE):
            return rule
    return None


def build_catalog_candidates(
    fund_basic: pd.DataFrame,
    *,
    as_of: str | date | None = None,
) -> list[dict[str, Any]]:
    """Classify active mainland-listed overseas ETFs from ``fund_basic``.

    ``fund_basic`` is available to the production token while the richer
    ``etf_basic`` endpoint is not. The benchmark text is therefore retained in
    every candidate as provenance for the deterministic classification.
    """

    if fund_basic is None or fund_basic.empty:
        return []
    as_of_key = _as_of_key(as_of)
    candidates: list[dict[str, Any]] = []
    for row in fund_basic.to_dict(orient="records"):
        code = _text(row.get("ts_code")).upper()
        name = _text(row.get("name"))
        benchmark = _text(row.get("benchmark"))
        status = _text(row.get("status")).upper()
        list_date = _date_key(row.get("list_date"))
        delist_date = _date_key(row.get("delist_date"))
        if not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ)", code):
            continue
        if "ETF" not in name.upper() or "联接" in name or "LOF" in name.upper():
            continue
        if status in {"D", "P"} or (delist_date and delist_date <= as_of_key):
            continue
        if not list_date or list_date > as_of_key:
            continue
        rule = _matching_rule(name, benchmark)
        if rule is None:
            continue
        # US-listed exposure must use an outbound QDII vehicle. Hong Kong
        # exposure may also use mainland Stock Connect ETFs.
        if rule["scope"] == "us_exposure" and "QDII" not in f"{name}{benchmark}".upper():
            continue
        fee = pd.to_numeric(pd.Series([row.get("m_fee")]), errors="coerce").iloc[0]
        candidates.append(
            {
                "code": code,
                "name": name,
                **_metadata_from_rule(rule),
                "benchmark": benchmark,
                "list_date": f"{list_date[:4]}-{list_date[4:6]}-{list_date[6:8]}",
                "management_fee": float(fee) if pd.notna(fee) else None,
                "source": "tushare_fund_basic",
            }
        )
    return sorted(candidates, key=lambda item: (item["scope"], item["index_key"], item["code"]))


def _number(value: Any, *, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def select_liquid_representatives(
    rows: Iterable[dict[str, Any]],
    *,
    max_per_index: int = 2,
    max_per_scope: int = 24,
) -> dict[str, list[dict[str, Any]]]:
    """Keep liquid index representatives while preserving theme diversity."""

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for raw in rows:
        row = dict(raw)
        scope = str(row.get("scope") or "")
        index_key = str(row.get("index_key") or "")
        if scope not in UNIVERSES or not index_key:
            continue
        grouped[scope][index_key].append(row)

    output: dict[str, list[dict[str, Any]]] = {scope: [] for scope in UNIVERSES}
    for scope in UNIVERSES:
        groups = grouped.get(scope, {})
        for candidates in groups.values():
            candidates.sort(
                key=lambda item: (
                    -_number(item.get("avg_amount_20")),
                    str(item.get("list_date") or "9999-99-99"),
                    str(item.get("code") or ""),
                )
            )
        keys = sorted(
            groups,
            key=lambda key: (
                -_number(groups[key][0].get("avg_amount_20")) if groups[key] else 0.0,
                key,
            ),
        )
        for rank in range(max(int(max_per_index), 0)):
            for key in keys:
                if len(output[scope]) >= max(int(max_per_scope), 0):
                    break
                if rank < len(groups[key]):
                    output[scope].append(groups[key][rank])
    return output


def catalog_content_hash(scopes: Mapping[str, Iterable[Mapping[str, Any]]]) -> str:
    """Hash membership/classification only, independent of row order."""

    canonical: list[dict[str, str]] = []
    for scope, rows in scopes.items():
        for row in rows:
            canonical.append(
                {
                    "scope": str(scope),
                    "code": str(row.get("code") or ""),
                    "index_key": str(row.get("index_key") or ""),
                    "theme": str(row.get("theme") or ""),
                }
            )
    canonical.sort(key=lambda item: (item["scope"], item["index_key"], item["code"]))
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def resolve_universe(scope: str) -> list[str]:
    """Return the static fallback seed for a configured account scope."""
    try:
        return list(UNIVERSES[scope])
    except KeyError as exc:
        raise ValueError(f"unknown cn_qdii_etf universe scope: {scope}") from exc


def classify_scope(code: str) -> str:
    metadata = ETF_METADATA.get(str(code).upper())
    return str(metadata.get("scope")) if metadata else "cn_qdii_etf"


def _latest_snapshot_metadata(repo_root: Path, code: str) -> dict[str, Any] | None:
    latest = repo_root / "data" / "cn_qdii_etf" / "shared" / "universe_latest.json"
    if not latest.exists():
        return None
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    for rows in (payload.get("scopes") or {}).values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("code") or "").upper() == code:
                return row
    return None


def metadata_for_code(
    code: str,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return display and underlying-index metadata for an ETF code."""

    normalized = str(code).upper()
    dynamic = _latest_snapshot_metadata(Path(repo_root), normalized) if repo_root else None
    metadata = dynamic or ETF_METADATA.get(normalized)
    if metadata is None:
        return {
            "exposure_group": "全球市场",
            "theme": "跨境ETF",
            "index_key": "unknown",
            "country": "未知",
            "sector": "未分类",
        }
    return {
        key: value
        for key, value in metadata.items()
        if key in {
            "exposure_group",
            "theme",
            "index_key",
            "country",
            "sector",
            "benchmark",
            "universe_hash",
        }
    }


__all__ = [
    "ETF_METADATA",
    "HK_EXPOSURE",
    "INDEX_RULES",
    "UNIVERSES",
    "US_EXPOSURE",
    "build_catalog_candidates",
    "catalog_content_hash",
    "classify_scope",
    "metadata_for_code",
    "resolve_universe",
    "select_liquid_representatives",
]
