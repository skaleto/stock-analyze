#!/usr/bin/env python3
"""
Beginner A-share value-quality screener.

This is a research helper, not trading advice. It builds an observation list
from an index universe using simple value, quality, and safety rules.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


INDEX_CODES = {
    "hs300": "000300",
    "zz500": "000905",
    "zz1000": "000852",
    "cyb": "399006",
    "kcb": "000688",
}


@dataclass
class StockScore:
    code: str
    name: str
    industry: str | None
    latest_price: float | None
    pe: float | None
    pb: float | None
    market_cap_yi: float | None
    roe: float | None
    debt_ratio: float | None
    gross_margin: float | None
    net_profit_growth: float | None
    score: float
    reasons: str
    warnings: str


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "nan", "None"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def first_existing(row: pd.Series | dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, "", "--"):
            return row[name]
    return None


def fetch_universe(scope: str) -> pd.DataFrame:
    print(f"Loading universe: {scope}")
    try:
        all_spot = ak.stock_zh_a_spot_em()
    except Exception:
        all_spot = pd.DataFrame()

    if all_spot is None or all_spot.empty:
        if scope.startswith("custom:"):
            return fetch_custom_basic(scope.replace("custom:", "").split(","))
        raise RuntimeError("stock_zh_a_spot_em returned no data")

    if scope == "all":
        return all_spot

    if scope.startswith("custom:"):
        codes = [x.strip() for x in scope.replace("custom:", "").split(",") if x.strip()]
        return all_spot[all_spot["代码"].isin(codes)].copy()

    index_code = INDEX_CODES.get(scope)
    if not index_code:
        raise ValueError(f"Unknown scope: {scope}. Use one of {', '.join(INDEX_CODES)} or all/custom:...")

    constituents = ak.index_stock_cons(symbol=index_code)
    codes = set(constituents["品种代码"].astype(str).tolist())
    return all_spot[all_spot["代码"].astype(str).isin(codes)].copy()


def fetch_custom_basic(codes: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for raw_code in codes:
        code = raw_code.strip()
        if not code:
            continue
        try:
            info_df = ak.stock_individual_info_em(symbol=code)
            info = {row["item"]: row["value"] for _, row in info_df.iterrows()}
            rows.append(
                {
                    "代码": code,
                    "名称": info.get("股票简称", ""),
                    "最新价": None,
                    "市盈率-动态": info.get("市盈率(动态)"),
                    "市净率": info.get("市净率"),
                    "总市值": info.get("总市值"),
                }
            )
        except Exception as exc:
            print(f"Warning: failed to fetch basic info for {code}: {exc}")
    return pd.DataFrame(rows)


def normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "代码": ["code", "股票代码"],
        "名称": ["name", "股票名称"],
        "最新价": ["latest_price", "price", "当前价格"],
        "市盈率-动态": ["pe", "PE", "PE_TTM", "市盈率"],
        "市净率": ["pb", "PB"],
        "总市值": ["market_cap", "总市值"],
    }
    for canonical, candidates in aliases.items():
        if canonical not in df.columns:
            for candidate in candidates:
                if candidate in df.columns:
                    df[canonical] = df[candidate]
                    break

    required_columns = ["代码", "名称", "最新价", "市盈率-动态", "市净率", "总市值"]
    if df.empty:
        return pd.DataFrame(
            columns=[
                "code",
                "name",
                "latest_price",
                "pe",
                "pb",
                "market_cap_yi",
            ]
        )
    for column in required_columns:
        if column not in df.columns:
            df[column] = None

    df = df.copy()
    df["code"] = df["代码"].astype(str)
    df["name"] = df["名称"].astype(str)
    df["latest_price"] = pd.to_numeric(df.get("最新价", pd.Series([None] * len(df))).map(safe_float), errors="coerce")
    df["pe"] = pd.to_numeric(df.get("市盈率-动态", pd.Series([None] * len(df))).map(safe_float), errors="coerce")
    df["pb"] = pd.to_numeric(df.get("市净率", pd.Series([None] * len(df))).map(safe_float), errors="coerce")
    if "market_cap_yi" in df.columns:
        df["market_cap_yi"] = pd.to_numeric(df["market_cap_yi"].map(safe_float), errors="coerce")
    else:
        market_cap = pd.to_numeric(df.get("总市值", pd.Series([None] * len(df))).map(safe_float), errors="coerce")
        df["market_cap_yi"] = market_cap / 100_000_000
    return df


def keep_null_or_between(series: pd.Series, min_value: float, max_value: float) -> pd.Series:
    return series.isna() | series.between(min_value, max_value, inclusive="both")


def latest_financial_metrics(code: str) -> dict[str, float | None]:
    try:
        indicators = ak.stock_financial_abstract(symbol=code)
        if indicators is None or indicators.empty:
            indicators = ak.stock_financial_analysis_indicator(symbol=code)
        if indicators is None or indicators.empty:
            return {}
        latest = indicators.iloc[0]
        return {
            "roe": safe_float(first_existing(latest, ["净资产收益率", "加权净资产收益率", "ROE"])),
            "debt_ratio": safe_float(first_existing(latest, ["资产负债率"])),
            "gross_margin": safe_float(first_existing(latest, ["销售毛利率", "毛利率"])),
            "net_profit_growth": safe_float(first_existing(latest, ["净利润增长率"])),
        }
    except Exception as exc:
        return {"fetch_error": str(exc)}


def score_stock(row: pd.Series, metrics: dict[str, Any]) -> StockScore:
    pe = safe_float(row.get("pe"))
    pb = safe_float(row.get("pb"))
    market_cap_yi = safe_float(row.get("market_cap_yi"))
    roe = safe_float(metrics.get("roe"))
    debt_ratio = safe_float(metrics.get("debt_ratio"))
    gross_margin = safe_float(metrics.get("gross_margin"))
    net_profit_growth = safe_float(metrics.get("net_profit_growth"))

    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    if pe is not None and 0 < pe <= 25:
        score += 20
        reasons.append("PE合理")
    elif pe is not None and pe > 40:
        score -= 10
        warnings.append("PE偏高")

    if pb is not None and 0 < pb <= 4:
        score += 15
        reasons.append("PB可接受")
    elif pb is not None and pb > 6:
        score -= 8
        warnings.append("PB偏高")

    if market_cap_yi is not None and market_cap_yi >= 500:
        score += 10
        reasons.append("市值较大")

    if roe is not None:
        if roe >= 20:
            score += 25
            reasons.append("ROE优秀")
        elif roe >= 12:
            score += 15
            reasons.append("ROE达标")
        elif roe < 8:
            score -= 10
            warnings.append("ROE偏弱")
    else:
        warnings.append("ROE缺失")

    if debt_ratio is not None:
        if debt_ratio <= 60:
            score += 15
            reasons.append("负债率可控")
        elif debt_ratio > 75:
            score -= 10
            warnings.append("负债率偏高")
    else:
        warnings.append("资产负债率缺失")

    if gross_margin is not None and gross_margin >= 25:
        score += 8
        reasons.append("毛利率较好")

    if net_profit_growth is not None:
        if net_profit_growth > 0:
            score += 7
            reasons.append("利润正增长")
        else:
            warnings.append("利润负增长")

    if metrics.get("fetch_error"):
        warnings.append("财务指标抓取失败")

    return StockScore(
        code=str(row.get("code", "")),
        name=str(row.get("name", "")),
        industry=None,
        latest_price=safe_float(row.get("latest_price")),
        pe=pe,
        pb=pb,
        market_cap_yi=market_cap_yi,
        roe=roe,
        debt_ratio=debt_ratio,
        gross_margin=gross_margin,
        net_profit_growth=net_profit_growth,
        score=round(max(score, 0), 2),
        reasons="; ".join(reasons),
        warnings="; ".join(warnings),
    )


def screen(args: argparse.Namespace) -> list[StockScore]:
    if args.input_csv:
        raw_df = pd.read_csv(args.input_csv, dtype={"code": str, "代码": str, "股票代码": str})
        print(f"Loading local CSV: {args.input_csv}")
    else:
        raw_df = fetch_universe(args.scope)

    df = normalize_spot(raw_df)
    if df.empty:
        raise RuntimeError("No universe data was available from akshare. Retry later or use a local data file.")

    df = df[~df["name"].str.contains("ST", na=False)].copy()
    df = df[keep_null_or_between(df["pe"], args.pe_min, args.pe_max)]
    df = df[keep_null_or_between(df["pb"], args.pb_min, args.pb_max)]
    df = df[df["market_cap_yi"].isna() | (df["market_cap_yi"] >= args.market_cap_min)]

    if args.preselect:
        df = df.sort_values(["pe", "pb", "market_cap_yi"], ascending=[True, True, False]).head(args.preselect)

    results: list[StockScore] = []
    total = len(df)
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        code = str(row["code"])
        print(f"[{idx}/{total}] Scoring {code} {row['name']}")
        metrics = metrics_from_row(row)
        if not metrics and not args.input_csv:
            metrics = latest_financial_metrics(code)
        result = score_stock(row, metrics)
        if (result.roe is None or result.roe >= args.roe_min) and (
            result.debt_ratio is None or result.debt_ratio <= args.debt_ratio_max
        ):
            results.append(result)
        time.sleep(args.delay)

    return sorted(results, key=lambda x: x.score, reverse=True)[: args.top]


def metrics_from_row(row: pd.Series) -> dict[str, float | None]:
    metrics = {
        "roe": safe_float(first_existing(row, ["roe", "ROE", "净资产收益率", "加权净资产收益率"])),
        "debt_ratio": safe_float(first_existing(row, ["debt_ratio", "资产负债率"])),
        "gross_margin": safe_float(first_existing(row, ["gross_margin", "销售毛利率", "毛利率"])),
        "net_profit_growth": safe_float(first_existing(row, ["net_profit_growth", "净利润增长率"])),
    }
    return {key: value for key, value in metrics.items() if value is not None}


def write_outputs(results: list[StockScore], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = [asdict(item) for item in results]

    csv_path = output_dir / f"value_quality_watchlist_{timestamp}.csv"
    json_path = output_dir / f"value_quality_watchlist_{timestamp}.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nSaved CSV: {csv_path}")
    print(f"Saved JSON: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="A-share beginner value-quality screener")
    parser.add_argument("--scope", default="hs300", help="hs300/zz500/zz1000/cyb/kcb/all/custom:600519,000858")
    parser.add_argument("--input-csv", help="Optional local CSV with A-share data")
    parser.add_argument("--pe-min", type=float, default=0.0)
    parser.add_argument("--pe-max", type=float, default=25.0)
    parser.add_argument("--pb-min", type=float, default=0.0)
    parser.add_argument("--pb-max", type=float, default=4.0)
    parser.add_argument("--market-cap-min", type=float, default=500.0, help="Unit: 100 million CNY")
    parser.add_argument("--roe-min", type=float, default=12.0)
    parser.add_argument("--debt-ratio-max", type=float, default=60.0)
    parser.add_argument("--preselect", type=int, default=30, help="Fetch financial data for only this many candidates")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between financial API calls")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    try:
        results = screen(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not results:
        print("No candidates found. Try a smaller universe or looser filters.")
        return 0

    write_outputs(results, Path(args.output_dir))
    print("\nTop candidates:")
    for item in results[:10]:
        print(
            f"{item.code} {item.name} score={item.score} "
            f"PE={item.pe} PB={item.pb} ROE={item.roe} debt={item.debt_ratio} "
            f"warnings={item.warnings}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
