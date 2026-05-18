from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from .utils import ak_date, pct_change, previous_calendar_date, safe_float


INDEX_CODES = {
    "hs300": "000300",
    "zz500": "000905",
    "zz1000": "000852",
    "cyb": "399006",
    "kcb": "000688",
}

BENCHMARK_SYMBOLS = {
    "000300": "sh000300",
    "000905": "sh000905",
}


@dataclass
class PriceSnapshot:
    code: str
    trade_date: str | None
    close: float | None
    open: float | None
    high: float | None
    low: float | None
    amount: float | None
    momentum_20: float | None
    momentum_60: float | None
    avg_amount_20: float | None
    paused: bool = False
    limit_up: bool = False
    limit_down: bool = False


class AkshareProvider:
    def __init__(self) -> None:
        self._spot: pd.DataFrame | None = None
        self._history_cache: dict[str, pd.DataFrame] = {}

    def spot(self) -> pd.DataFrame:
        if self._spot is None:
            self._spot = ak.stock_zh_a_spot_em()
        return self._spot.copy()

    def index_constituents(self, scope: str) -> list[str]:
        index_code = INDEX_CODES.get(scope, scope)
        df = ak.index_stock_cons(symbol=index_code)
        if df is None or df.empty:
            return []
        return df["品种代码"].astype(str).str.zfill(6).tolist()

    def universe(self, scope: str) -> pd.DataFrame:
        spot = normalize_spot(self.spot())
        if scope == "all":
            return spot
        if scope.startswith("custom:"):
            codes = {item.strip().zfill(6) for item in scope.replace("custom:", "").split(",") if item.strip()}
            return spot[spot["code"].isin(codes)].copy()
        codes = set(self.index_constituents(scope))
        return spot[spot["code"].isin(codes)].copy()

    def financial_metrics(self, code: str) -> dict[str, Any]:
        try:
            indicators = ak.stock_financial_abstract(symbol=code)
            if indicators is None or indicators.empty:
                indicators = ak.stock_financial_analysis_indicator(symbol=code)
            if indicators is None or indicators.empty:
                return {}
            latest = indicators.iloc[0]
            return {
                "roe": safe_float(first_existing(latest, ["净资产收益率", "加权净资产收益率", "ROE"])),
                "gross_margin": safe_float(first_existing(latest, ["销售毛利率", "毛利率"])),
                "debt_ratio": safe_float(first_existing(latest, ["资产负债率"])),
                "net_profit_growth": safe_float(first_existing(latest, ["净利润增长率"])),
            }
        except Exception as exc:
            return {"fetch_error": str(exc)}

    def price_history(self, code: str, as_of: str | None = None, days: int = 180) -> pd.DataFrame:
        cache_key = f"{code}:{as_of}:{days}"
        if cache_key in self._history_cache:
            return self._history_cache[cache_key].copy()

        end_date = ak_date(as_of)
        start_date = previous_calendar_date(days, as_of)
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is None:
            df = pd.DataFrame()
        if not df.empty:
            df = df.copy()
            df["日期"] = pd.to_datetime(df["日期"]).dt.date
        self._history_cache[cache_key] = df
        return df.copy()

    def price_snapshot(self, code: str, as_of: str | None = None) -> PriceSnapshot:
        try:
            df = self.price_history(code, as_of=as_of, days=220)
            if df.empty:
                return PriceSnapshot(code, None, None, None, None, None, None, None, None, None, paused=True)
            latest = df.iloc[-1]
            close = safe_float(latest.get("收盘"))
            open_price = safe_float(latest.get("开盘"))
            high = safe_float(latest.get("最高"))
            low = safe_float(latest.get("最低"))
            amount = safe_float(latest.get("成交额"))
            closes = pd.to_numeric(df["收盘"], errors="coerce").dropna()
            amounts = pd.to_numeric(df.get("成交额", pd.Series(dtype=float)), errors="coerce").dropna()
            momentum_20 = pct_change(float(closes.iloc[-21]), float(closes.iloc[-1])) if len(closes) >= 21 else None
            momentum_60 = pct_change(float(closes.iloc[-61]), float(closes.iloc[-1])) if len(closes) >= 61 else None
            avg_amount_20 = float(amounts.tail(20).mean()) if len(amounts) else None
            return PriceSnapshot(
                code=code,
                trade_date=str(latest.get("日期")),
                close=close,
                open=open_price,
                high=high,
                low=low,
                amount=amount,
                momentum_20=momentum_20,
                momentum_60=momentum_60,
                avg_amount_20=avg_amount_20,
            )
        except Exception:
            return PriceSnapshot(code, None, None, None, None, None, None, None, None, None, paused=True)

    def execution_price(self, code: str, execute_after: str, side: str) -> tuple[float | None, str | None]:
        try:
            end = date.today().strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=execute_after.replace("-", ""),
                end_date=end,
                adjust="qfq",
            )
            if df is None or df.empty:
                return None, None
            df = df.copy()
            df["日期"] = pd.to_datetime(df["日期"]).dt.date
            target = pd.to_datetime(execute_after).date()
            df = df[df["日期"] >= target]
            if df.empty:
                return None, None
            row = df.iloc[0]
            price = safe_float(row.get("开盘")) or safe_float(row.get("收盘"))
            return price, str(row.get("日期"))
        except Exception:
            return None, None

    def benchmark_close(self, benchmark_code: str, as_of: str | None = None) -> tuple[float | None, str | None]:
        symbol = BENCHMARK_SYMBOLS.get(benchmark_code, f"sh{benchmark_code}")
        try:
            df = ak.stock_zh_index_daily_em(symbol=symbol)
            if df is None or df.empty:
                return None, None
            df = df.copy()
            date_column = "date" if "date" in df.columns else "日期"
            close_column = "close" if "close" in df.columns else "收盘"
            df[date_column] = pd.to_datetime(df[date_column]).dt.date
            if as_of:
                target = pd.to_datetime(as_of).date()
                df = df[df[date_column] <= target]
            if df.empty:
                return None, None
            row = df.iloc[-1]
            return safe_float(row.get(close_column)), str(row.get(date_column))
        except Exception:
            return None, None


def normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
    out = df.copy()
    out["code"] = out.get("代码", pd.Series([""] * len(out))).astype(str).str.zfill(6)
    out["name"] = out.get("名称", pd.Series([""] * len(out))).astype(str)
    out["latest_price"] = out.get("最新价", pd.Series([None] * len(out))).map(safe_float)
    out["pe"] = out.get("市盈率-动态", pd.Series([None] * len(out))).map(safe_float)
    out["pb"] = out.get("市净率", pd.Series([None] * len(out))).map(safe_float)
    market_cap = out.get("总市值", pd.Series([None] * len(out))).map(safe_float)
    out["market_cap_yi"] = pd.to_numeric(market_cap, errors="coerce") / 100_000_000
    return out[["code", "name", "latest_price", "pe", "pb", "market_cap_yi"]]


def first_existing(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, "", "--"):
            return row[name]
    return None
