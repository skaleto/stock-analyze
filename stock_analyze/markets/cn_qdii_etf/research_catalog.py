"""Research-only classification for mainland-listed overseas fund products."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

import pandas as pd


_RULES: tuple[dict[str, str], ...] = (
    {"scope": "japan_exposure", "asset_class": "global_equity", "index_key": "nikkei_225", "theme": "日本股票", "country": "日本", "pattern": r"日经|NIKKEI|东证|TOPIX"},
    {"scope": "europe_exposure", "asset_class": "global_equity", "index_key": "germany_dax", "theme": "德国股票", "country": "德国", "pattern": r"德国|DAX"},
    {"scope": "europe_exposure", "asset_class": "global_equity", "index_key": "france_cac40", "theme": "法国股票", "country": "法国", "pattern": r"法国|CAC\s*40"},
    {"scope": "saudi_exposure", "asset_class": "global_equity", "index_key": "saudi_equity", "theme": "沙特股票", "country": "沙特阿拉伯", "pattern": r"沙特"},
    {"scope": "commodity_oil", "asset_class": "commodity", "index_key": "overseas_oil", "theme": "海外原油", "country": "全球", "pattern": r"原油|全球石油|油气能源|石油天然气上游"},
    {"scope": "commodity_precious_metals", "asset_class": "commodity", "index_key": "precious_metals", "theme": "海外贵金属", "country": "全球", "pattern": r"黄金|贵金属"},
    {"scope": "commodity_broad", "asset_class": "commodity", "index_key": "broad_commodity", "theme": "全球商品", "country": "全球", "pattern": r"大宗商品|全球商品|抗通胀"},
    {"scope": "bond_overseas", "asset_class": "bond", "index_key": "overseas_bond", "theme": "海外债券", "country": "全球", "pattern": r"美元债|亚洲债|海外债|全球债"},
    {"scope": "other_global_exposure", "asset_class": "global_equity", "index_key": "brazil_equity", "theme": "巴西股票", "country": "巴西", "pattern": r"巴西|IBOVESPA"},
    {"scope": "other_global_exposure", "asset_class": "global_equity", "index_key": "korea_equity", "theme": "韩国股票", "country": "韩国", "pattern": r"韩国|韩交所"},
    {"scope": "other_global_exposure", "asset_class": "global_equity", "index_key": "asia_equity", "theme": "亚洲股票", "country": "亚洲", "pattern": r"亚洲|东南亚|亚太"},
)


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _date_key(value: Any) -> str:
    return _text(value).replace("-", "")[:8]


def _as_of_key(value: str | date | None) -> str:
    if value is None:
        return date.today().strftime("%Y%m%d")
    return value.strftime("%Y%m%d") if isinstance(value, date) else _date_key(value)


def _product_type(name: str) -> str | None:
    upper = name.upper()
    if "LOF" in upper:
        return "qdii_lof"
    if "ETF" in upper and "联接" not in name:
        return "etf"
    return None


def build_research_catalog(
    fund_basic: pd.DataFrame,
    *,
    as_of: str | date | None = None,
) -> pd.DataFrame:
    """Classify active exchange-listed QDII products without promoting accounts."""

    as_of_key = _as_of_key(as_of)
    rows: list[dict[str, Any]] = []
    for raw in (fund_basic.to_dict(orient="records") if fund_basic is not None else []):
        code = _text(raw.get("ts_code")).upper()
        name = _text(raw.get("name"))
        benchmark = _text(raw.get("benchmark"))
        fund_type = _text(raw.get("fund_type"))
        status = _text(raw.get("status")).upper()
        list_date = _date_key(raw.get("list_date"))
        delist_date = _date_key(raw.get("delist_date"))
        product_type = _product_type(name)
        if not re.fullmatch(r"\d{6}\.(?:SH|SZ)", code) or product_type is None:
            continue
        if status in {"D", "P"} or (delist_date and delist_date <= as_of_key):
            continue
        if not list_date or list_date > as_of_key:
            continue
        haystack = f"{name} {benchmark}"
        if "QDII" not in haystack.upper():
            continue
        rule = next((item for item in _RULES if re.search(item["pattern"], haystack, re.I)), None)
        if rule is None:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "fund_type": fund_type,
                "product_type": product_type,
                "asset_class": rule["asset_class"],
                "research_scope": rule["scope"],
                "scope": rule["scope"],
                "index_key": rule["index_key"],
                "theme": rule["theme"],
                "country": rule["country"],
                "exposure_group": rule["theme"],
                "benchmark": benchmark,
                "list_date": f"{list_date[:4]}-{list_date[4:6]}-{list_date[6:8]}",
                "management_fee": pd.to_numeric(raw.get("m_fee"), errors="coerce"),
                "mode": "research_only",
                "source": "tushare_fund_basic",
                "catalog_as_of": f"{as_of_key[:4]}-{as_of_key[4:6]}-{as_of_key[6:8]}",
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "code", "name", "product_type", "asset_class", "research_scope",
                "scope", "index_key", "theme", "country", "promotion_status", "mode",
            ]
        )
    breadth = frame.groupby("research_scope")["code"].transform("nunique")
    frame["scope_breadth"] = breadth.astype(int)
    frame["factor_model"] = frame["asset_class"].map(
        {"global_equity": "global_equity_v1", "commodity": "commodity_v1", "bond": "bond_v1"}
    )
    frame["promotion_status"] = "research_only"
    equity_ready = (
        frame["asset_class"].eq("global_equity")
        & frame["product_type"].eq("etf")
        & breadth.ge(2)
    )
    frame.loc[equity_ready, "promotion_status"] = "shadow_ready"
    frame.loc[frame["asset_class"].eq("bond") & breadth.lt(3), "promotion_status"] = "insufficient_breadth"
    return frame.sort_values(["asset_class", "research_scope", "index_key", "code"]).reset_index(drop=True)


def catalog_payload(frame: pd.DataFrame, *, as_of: str) -> dict[str, Any]:
    records = frame.to_dict(orient="records")
    canonical = [
        {key: row.get(key) for key in ("code", "research_scope", "asset_class", "product_type", "index_key")}
        for row in records
    ]
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    scopes: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        scopes.setdefault(str(row["research_scope"]), []).append(row)
    return {
        "schema_version": 1,
        "as_of": str(as_of)[:10],
        "mode": "research_only",
        "universe_hash": digest,
        "source_status": "tushare_fund_basic_research",
        "scopes": scopes,
    }


__all__ = ["build_research_catalog", "catalog_payload"]
