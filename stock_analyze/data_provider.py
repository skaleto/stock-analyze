from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import akshare as ak
import pandas as pd
import requests

from .utils import ak_date, pct_change, previous_calendar_date, safe_float, write_json


INDEX_CODES = {
    "hs300": "000300",
    "zz500": "000905",
    "zz1000": "000852",
    "cyb": "399006",
    "kcb": "000688",
}

EASTMONEY_BENCHMARK_SYMBOLS = {
    "000300": "csi000300",
    "000905": "csi000905",
}

FALLBACK_BENCHMARK_SYMBOLS = {
    "000300": "sh000300",
    "000905": "sh000905",
}

RETRY_DELAYS = [2.0, 5.0, 10.0]
EASTMONEY_COOKIE_ENV = "EASTMONEY_COOKIE"
EASTMONEY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0 Safari/537.36"
)
EASTMONEY_DEFAULT_REFERER = "https://quote.eastmoney.com/center/gridlist.html"


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
    source: str = ""
    warning: str = ""


class AkshareProvider:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._spot: pd.DataFrame | None = None
        self._history_cache: dict[str, pd.DataFrame] = {}
        self.health: list[dict[str, Any]] = []
        self._baostock_logged_in = False

    def record_health(self, source: str, status: str, message: str = "", rows: int | None = None) -> None:
        self.health.append(
            {
                "time": pd.Timestamp.now().isoformat(timespec="seconds"),
                "source": source,
                "status": status,
                "message": message[:300],
                "rows": rows,
            }
        )

    def persist_health(self) -> None:
        if self.cache_dir and self.health:
            write_json(self.cache_dir.parent / "data_health.json", self.health)

    def retry(self, label: str, func: Callable[[], Any], delays: list[float] | None = None) -> Any:
        last_error: Exception | None = None
        retry_delays = RETRY_DELAYS if delays is None else delays
        for attempt, delay in enumerate([0.0] + retry_delays, start=1):
            if delay:
                time.sleep(delay)
            try:
                result = func()
                rows = len(result) if hasattr(result, "__len__") else None
                self.record_health(label, "ok", f"attempt={attempt}", rows=rows)
                return result
            except Exception as exc:  # noqa: BLE001 - external data APIs fail in many shapes
                last_error = exc
                self.record_health(label, "retry" if attempt <= len(retry_delays) else "failed", f"attempt={attempt}: {exc}")
        raise last_error or RuntimeError(f"{label} failed")

    def fallback_retry(self, label: str, func: Callable[[], Any]) -> Any:
        return self.retry(label, func, delays=[0.5])

    def eastmoney_retry(self, label: str, func: Callable[[], Any], referer: str = EASTMONEY_DEFAULT_REFERER) -> Any:
        with eastmoney_request_headers(referer):
            return self.fallback_retry(label, func)

    def cache_path(self, name: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        return self.cache_dir / f"{safe_name}.csv"

    def save_cache(self, name: str, df: pd.DataFrame) -> None:
        path = self.cache_path(name)
        if path and df is not None and not df.empty:
            df.to_csv(path, index=False, encoding="utf-8-sig")

    def load_cache(self, name: str) -> pd.DataFrame:
        path = self.cache_path(name)
        if path and path.exists():
            self.record_health(name, "cache", f"using cache {path.name}")
            return pd.read_csv(path, dtype={"code": str, "代码": str, "成分券代码": str, "品种代码": str})
        return pd.DataFrame()

    def spot(self) -> pd.DataFrame:
        if self._spot is not None:
            return self._spot.copy()

        sources: list[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("spot_eastmoney", lambda: self.eastmoney_retry("spot_eastmoney", ak.stock_zh_a_spot_em)),
            ("spot_sina", ak.stock_zh_a_spot),
        ]
        for name, fetch in sources:
            try:
                df = fetch() if name == "spot_eastmoney" else self.retry(name, fetch)
                normalized = normalize_spot(df)
                if not normalized.empty:
                    self.save_cache("spot_latest", normalized)
                    self._spot = normalized
                    return normalized.copy()
            except Exception as exc:  # noqa: BLE001
                self.record_health(name, "failed", str(exc))

        cached = self.load_cache("spot_latest")
        if not cached.empty:
            self._spot = normalize_spot(cached)
            return self._spot.copy()

        self.record_health("spot", "failed", "all realtime spot sources failed")
        return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])

    def index_constituents(self, scope: str) -> pd.DataFrame:
        index_code = INDEX_CODES.get(scope, scope)
        cache_name = f"constituents_{index_code}"
        sources = [
            ("index_cons_csindex", lambda: ak.index_stock_cons_csindex(symbol=index_code)),
            ("index_cons_weight_csindex", lambda: ak.index_stock_cons_weight_csindex(symbol=index_code)),
            ("index_cons_default", lambda: ak.index_stock_cons(symbol=index_code)),
            ("index_cons_baostock", lambda: self.baostock_constituents(index_code)),
        ]
        for name, fetch in sources:
            try:
                df = self.retry(f"{name}_{index_code}", fetch)
                normalized = normalize_constituents(df)
                if not normalized.empty:
                    self.save_cache(cache_name, normalized)
                    return normalized
            except Exception as exc:  # noqa: BLE001
                self.record_health(f"{name}_{index_code}", "failed", str(exc))

        cached = self.load_cache(cache_name)
        if not cached.empty:
            return normalize_constituents(cached)
        return pd.DataFrame(columns=["code", "name"])

    def universe(self, scope: str) -> pd.DataFrame:
        if scope.startswith("custom:"):
            codes = {normalize_code(item) for item in scope.replace("custom:", "").split(",") if normalize_code(item)}
            return pd.DataFrame({"code": sorted(codes)}).assign(name="", latest_price=None, pe=None, pb=None, market_cap_yi=None)

        spot = self.spot()
        if scope == "all":
            return spot

        constituents = self.index_constituents(scope)
        if constituents.empty:
            self.record_health(f"universe_{scope}", "failed", "no constituents")
            return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
        if spot.empty:
            return constituents.assign(latest_price=None, pe=None, pb=None, market_cap_yi=None)

        merged = constituents.merge(spot, on="code", how="left", suffixes=("_index", ""))
        merged["name"] = merged["name"].fillna(merged.get("name_index"))
        return merged[["code", "name", "latest_price", "pe", "pb", "market_cap_yi"]].copy()

    def basic_info(self, code: str) -> dict[str, Any]:
        cache_name = f"basic_{code}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return cached.iloc[0].to_dict()
        try:
            df = self.eastmoney_retry(
                f"basic_{code}",
                lambda: ak.stock_individual_info_em(symbol=code),
                referer=f"https://quote.eastmoney.com/{market_symbol(code)}.html",
            )
            info = {row["item"]: row["value"] for _, row in df.iterrows()}
            result = {
                "code": normalize_code(info.get("股票代码", code)) or code,
                "name": info.get("股票简称"),
                "latest_price": safe_float(info.get("最新")),
                "market_cap_yi": (safe_float(info.get("总市值")) or 0) / 100_000_000 if safe_float(info.get("总市值")) else None,
                "industry": info.get("行业"),
                "listing_date": info.get("上市时间"),
            }
            self.save_cache(cache_name, pd.DataFrame([result]))
            return result
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"basic_{code}", "failed", str(exc))
            return {}

    def valuation_metrics(self, code: str) -> dict[str, float | None]:
        cache_name = f"valuation_{code}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            row = cached.iloc[0]
            return {"pe": safe_float(row.get("pe")), "pb": safe_float(row.get("pb"))}

        result: dict[str, float | None] = {"pe": None, "pb": None}
        indicator_map = {"pe": "市盈率(TTM)", "pb": "市净率"}
        for key, indicator in indicator_map.items():
            try:
                df = self.fallback_retry(
                    f"valuation_{indicator}_{code}",
                    lambda indicator=indicator: ak.stock_zh_valuation_baidu(symbol=code, indicator=indicator, period="近一年"),
                )
                if df is not None and not df.empty:
                    result[key] = safe_float(df.iloc[-1].get("value"))
            except Exception as exc:  # noqa: BLE001
                self.record_health(f"valuation_{indicator}_{code}", "failed", str(exc))
        if result["pe"] is None or result["pb"] is None:
            baostock_result = self.baostock_valuation_metrics(code)
            result["pe"] = result["pe"] if result["pe"] is not None else baostock_result.get("pe")
            result["pb"] = result["pb"] if result["pb"] is not None else baostock_result.get("pb")
        if result["pe"] is not None or result["pb"] is not None:
            self.save_cache(cache_name, pd.DataFrame([{"code": code, **result}]))
        return result

    def financial_metrics(self, code: str) -> dict[str, Any]:
        cache_name = f"financial_{code}"
        cached = self.load_cache(cache_name)
        if not cached.empty:
            return cached.iloc[0].to_dict()

        try:
            indicators = self.fallback_retry(f"financial_abstract_{code}", lambda: ak.stock_financial_abstract(symbol=code))
            result = parse_financial_metrics(indicators)
            if not result:
                indicators = self.fallback_retry(
                    f"financial_indicator_{code}",
                    lambda: ak.stock_financial_analysis_indicator(symbol=code),
                )
                result = parse_financial_metrics(indicators)
            missing = {"roe", "gross_margin", "debt_ratio", "net_profit_growth"} - set(result)
            if missing:
                baostock_result = self.baostock_financial_metrics(code)
                for key in missing:
                    if key in baostock_result:
                        result[key] = baostock_result[key]
            if result:
                self.save_cache(cache_name, pd.DataFrame([{"code": code, **result}]))
                return result
            return {}
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"financial_{code}", "failed", str(exc))
            result = self.baostock_financial_metrics(code)
            if result:
                self.save_cache(cache_name, pd.DataFrame([{"code": code, **result}]))
                return result
            return {"fetch_error": str(exc)}

    def price_history(self, code: str, as_of: str | None = None, days: int = 180) -> pd.DataFrame:
        cache_key = f"{code}:{as_of}:{days}"
        if cache_key in self._history_cache:
            return self._history_cache[cache_key].copy()

        cache_name = f"history_{code}_{ak_date(as_of)}_{days}"
        end_date = ak_date(as_of)
        start_date = previous_calendar_date(days, as_of)
        symbol = market_symbol(code)
        sources = [
            (
                f"history_eastmoney_{code}",
                lambda: self.eastmoney_retry(
                    f"history_eastmoney_{code}",
                    lambda: ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq"),
                    referer=f"https://quote.eastmoney.com/{market_symbol(code)}.html",
                ),
            ),
            (
                f"history_tencent_{code}",
                lambda: ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq"),
            ),
            (
                f"history_sina_{code}",
                lambda: ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq"),
            ),
            (
                f"history_baostock_{code}",
                lambda: self.baostock_history(code, start_date, end_date),
            ),
        ]
        for name, fetch in sources:
            try:
                df = fetch() if name.startswith("history_eastmoney_") else self.fallback_retry(name, fetch)
                normalized = normalize_history(df)
                if not normalized.empty:
                    self.save_cache(cache_name, normalized)
                    self._history_cache[cache_key] = normalized
                    return normalized.copy()
            except Exception as exc:  # noqa: BLE001
                self.record_health(name, "failed", str(exc))

        cached = self.load_cache(cache_name)
        normalized = normalize_history(cached)
        self._history_cache[cache_key] = normalized
        return normalized.copy()

    def price_snapshot(self, code: str, as_of: str | None = None, spot_row: dict[str, Any] | None = None) -> PriceSnapshot:
        df = self.price_history(code, as_of=as_of, days=220)
        if df.empty:
            spot_price = safe_float((spot_row or {}).get("latest_price"))
            if spot_price is not None:
                return PriceSnapshot(code, None, spot_price, None, None, None, None, None, None, None, source="spot", warning="history_missing")
            return PriceSnapshot(code, None, None, None, None, None, None, None, None, None, paused=True, warning="price_missing")

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
            paused=is_truthy(latest.get("停牌")),
            source=str(latest.get("source", "")),
        )

    def execution_price(self, code: str, execute_after: str, side: str) -> tuple[float | None, str | None]:
        df = self.price_history(code, as_of=None, days=30)
        if df.empty:
            return None, None
        df = df.copy()
        target = pd.to_datetime(execute_after).date()
        df = df[pd.to_datetime(df["日期"]).dt.date >= target]
        if df.empty:
            return None, None
        row = df.iloc[0]
        price = safe_float(row.get("开盘")) or safe_float(row.get("收盘"))
        return price, str(row.get("日期"))

    def benchmark_close(self, benchmark_code: str, as_of: str | None = None) -> tuple[float | None, str | None]:
        eastmoney_symbol = EASTMONEY_BENCHMARK_SYMBOLS.get(benchmark_code, f"sh{benchmark_code}")
        fallback_symbol = FALLBACK_BENCHMARK_SYMBOLS.get(benchmark_code, f"sh{benchmark_code}")
        sources = [
            (
                "benchmark_eastmoney",
                lambda: self.eastmoney_retry(
                    f"benchmark_eastmoney_{benchmark_code}",
                    lambda: ak.stock_zh_index_daily_em(symbol=eastmoney_symbol),
                    referer="https://quote.eastmoney.com/center/hszs.html",
                ),
            ),
            ("benchmark_tencent", lambda: ak.stock_zh_index_daily_tx(symbol=fallback_symbol)),
            ("benchmark_sina", lambda: ak.stock_zh_index_daily(symbol=fallback_symbol)),
        ]
        for name, fetch in sources:
            try:
                df = fetch() if name == "benchmark_eastmoney" else self.fallback_retry(f"{name}_{benchmark_code}", fetch)
                normalized = normalize_index_history(df)
                if normalized.empty:
                    continue
                if as_of:
                    target = pd.to_datetime(as_of).date()
                    normalized = normalized[pd.to_datetime(normalized["日期"]).dt.date <= target]
                if normalized.empty:
                    continue
                row = normalized.iloc[-1]
                return safe_float(row.get("收盘")), str(row.get("日期"))
            except Exception as exc:  # noqa: BLE001
                self.record_health(f"{name}_{benchmark_code}", "failed", str(exc))
        return None, None

    def baostock_login(self) -> Any:
        try:
            import baostock as bs  # type: ignore
        except ImportError as exc:
            raise RuntimeError("baostock is not installed") from exc
        if not self._baostock_logged_in:
            result = bs.login()
            if getattr(result, "error_code", "0") != "0":
                raise RuntimeError(getattr(result, "error_msg", "baostock login failed"))
            self._baostock_logged_in = True
            self.record_health("baostock_login", "ok")
        return bs

    def baostock_dataframe(self, result: Any) -> pd.DataFrame:
        if getattr(result, "error_code", "0") != "0":
            raise RuntimeError(getattr(result, "error_msg", "baostock query failed"))
        rows = []
        while result.next():
            rows.append(result.get_row_data())
        return pd.DataFrame(rows, columns=result.fields)

    def baostock_constituents(self, index_code: str) -> pd.DataFrame:
        bs = self.baostock_login()
        query_date = date.today().isoformat()
        if index_code == "000300":
            result = bs.query_hs300_stocks(date=query_date)
        elif index_code == "000905":
            result = bs.query_zz500_stocks(date=query_date)
        else:
            raise RuntimeError(f"baostock does not support index constituents for {index_code}")
        df = self.baostock_dataframe(result)
        if df.empty:
            return pd.DataFrame(columns=["code", "name"])
        return pd.DataFrame(
            {
                "code": df["code"].map(lambda value: normalize_code(str(value).replace(".", ""))),
                "name": df.get("code_name", ""),
            }
        )

    def baostock_history(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        bs = self.baostock_login()
        result = bs.query_history_k_data_plus(
            baostock_code(code),
            "date,code,open,high,low,close,volume,amount,tradestatus,peTTM,pbMRQ,isST",
            start_date=f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}",
            end_date=f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}",
            frequency="d",
            adjustflag="2",
        )
        return self.baostock_dataframe(result)

    def baostock_valuation_metrics(self, code: str) -> dict[str, float | None]:
        try:
            end_date = ak_date()
            start_date = previous_calendar_date(45)
            history = self.baostock_history(code, start_date, end_date)
            if history.empty:
                return {"pe": None, "pb": None}
            row = history.iloc[-1]
            result = {"pe": safe_float(row.get("peTTM")), "pb": safe_float(row.get("pbMRQ"))}
            self.record_health(f"valuation_baostock_{code}", "ok", rows=1)
            return result
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"valuation_baostock_{code}", "failed", str(exc))
            return {"pe": None, "pb": None}

    def baostock_financial_metrics(self, code: str) -> dict[str, Any]:
        try:
            bs = self.baostock_login()
            current_year = date.today().year
            quarters = [(current_year, 1), (current_year - 1, 4), (current_year - 1, 3)]
            metrics: dict[str, Any] = {}
            for year, quarter in quarters:
                if "roe" not in metrics:
                    profit = self.baostock_dataframe(bs.query_profit_data(code=baostock_code(code), year=year, quarter=quarter))
                    if not profit.empty:
                        row = profit.iloc[0]
                        metrics["roe"] = ratio_to_percent(safe_float(row.get("roeAvg")))
                        metrics["gross_margin"] = ratio_to_percent(safe_float(row.get("gpMargin")))
                if "debt_ratio" not in metrics:
                    balance = self.baostock_dataframe(bs.query_balance_data(code=baostock_code(code), year=year, quarter=quarter))
                    if not balance.empty:
                        asset_to_equity = safe_float(balance.iloc[0].get("assetToEquity"))
                        if asset_to_equity and asset_to_equity > 1:
                            metrics["debt_ratio"] = (1 - 1 / asset_to_equity) * 100
                if "net_profit_growth" not in metrics:
                    growth = self.baostock_dataframe(bs.query_growth_data(code=baostock_code(code), year=year, quarter=quarter))
                    if not growth.empty:
                        row = growth.iloc[0]
                        growth_value = safe_float(row.get("YOYPNI"))
                        if growth_value is None:
                            growth_value = safe_float(row.get("YOYNI"))
                        metrics["net_profit_growth"] = ratio_to_percent(growth_value)
                metrics = {key: value for key, value in metrics.items() if value is not None}
                if len(metrics) >= 3:
                    break
            if metrics:
                self.record_health(f"financial_baostock_{code}", "ok", rows=1)
            return metrics
        except Exception as exc:  # noqa: BLE001
            self.record_health(f"financial_baostock_{code}", "failed", str(exc))
            return {}


@contextmanager
def eastmoney_request_headers(referer: str):
    original_request = requests.sessions.Session.request
    cookie = os.environ.get(EASTMONEY_COOKIE_ENV, "").strip()

    def patched_request(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
        if "eastmoney.com" in str(url).lower():
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", EASTMONEY_USER_AGENT)
            headers.setdefault("Referer", referer)
            if cookie:
                headers.setdefault("Cookie", cookie)
            kwargs["headers"] = headers
        return original_request(session, method, url, **kwargs)

    requests.sessions.Session.request = patched_request
    try:
        yield
    finally:
        requests.sessions.Session.request = original_request


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


def market_symbol(code: str) -> str:
    normalized = normalize_code(code)
    if normalized.startswith(("5", "6", "9")):
        return f"sh{normalized}"
    if normalized.startswith(("8", "4")):
        return f"bj{normalized}"
    return f"sz{normalized}"


def baostock_code(code: str) -> str:
    normalized = normalize_code(code)
    if normalized.startswith(("5", "6", "9")):
        return f"sh.{normalized}"
    if normalized.startswith(("8", "4")):
        return f"bj.{normalized}"
    return f"sz.{normalized}"


def ratio_to_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 100 if abs(value) <= 1 else value


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])
    out = df.copy()
    code_col = first_available_column(out, ["代码", "code", "股票代码"])
    name_col = first_available_column(out, ["名称", "name", "股票简称"])
    price_col = first_available_column(out, ["最新价", "最新", "price", "当前价格"])
    pe_col = first_available_column(out, ["市盈率-动态", "市盈率", "pe"])
    pb_col = first_available_column(out, ["市净率", "pb"])
    market_cap_col = first_available_column(out, ["总市值", "market_cap"])

    normalized = pd.DataFrame()
    normalized["code"] = out[code_col].map(normalize_code) if code_col else ""
    normalized["name"] = out[name_col].astype(str) if name_col else ""
    normalized["latest_price"] = out[price_col].map(safe_float) if price_col else None
    normalized["pe"] = out[pe_col].map(safe_float) if pe_col else None
    normalized["pb"] = out[pb_col].map(safe_float) if pb_col else None
    if market_cap_col:
        normalized["market_cap_yi"] = pd.to_numeric(out[market_cap_col].map(safe_float), errors="coerce") / 100_000_000
    else:
        normalized["market_cap_yi"] = None
    normalized = normalized[normalized["code"] != ""]
    return normalized.drop_duplicates("code")


def normalize_constituents(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name"])
    code_col = first_available_column(df, ["成分券代码", "品种代码", "代码", "code"])
    name_col = first_available_column(df, ["成分券名称", "品种名称", "名称", "name"])
    if not code_col:
        return pd.DataFrame(columns=["code", "name"])
    out = pd.DataFrame()
    out["code"] = df[code_col].map(normalize_code)
    out["name"] = df[name_col].astype(str) if name_col else ""
    return out[out["code"] != ""].drop_duplicates("code")


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交额", "source"])
    out = df.copy()
    date_col = first_available_column(out, ["日期", "date"])
    open_col = first_available_column(out, ["开盘", "open"])
    close_col = first_available_column(out, ["收盘", "close"])
    high_col = first_available_column(out, ["最高", "high"])
    low_col = first_available_column(out, ["最低", "low"])
    amount_col = first_available_column(out, ["成交额", "amount"])
    volume_col = first_available_column(out, ["成交量", "volume"])
    if not date_col or not close_col:
        return pd.DataFrame(columns=["日期", "开盘", "收盘", "最高", "最低", "成交额", "source"])
    normalized = pd.DataFrame()
    normalized["日期"] = pd.to_datetime(out[date_col]).dt.date.astype(str)
    normalized["开盘"] = out[open_col].map(safe_float) if open_col else None
    normalized["收盘"] = out[close_col].map(safe_float)
    normalized["最高"] = out[high_col].map(safe_float) if high_col else None
    normalized["最低"] = out[low_col].map(safe_float) if low_col else None
    if amount_col:
        normalized["成交额"] = normalize_amount_series(out[amount_col].map(safe_float))
    elif volume_col:
        normalized["成交额"] = pd.to_numeric(out[volume_col].map(safe_float), errors="coerce") * pd.to_numeric(normalized["收盘"], errors="coerce")
    else:
        normalized["成交额"] = None
    normalized["停牌"] = out["tradestatus"].astype(str).ne("1") if "tradestatus" in out.columns else False
    normalized["is_st"] = out["isST"].astype(str).eq("1") if "isST" in out.columns else False
    normalized["pe"] = out["peTTM"].map(safe_float) if "peTTM" in out.columns else None
    normalized["pb"] = out["pbMRQ"].map(safe_float) if "pbMRQ" in out.columns else None
    normalized["source"] = "history"
    return normalized.sort_values("日期").dropna(subset=["收盘"])


def normalize_index_history(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["日期", "收盘"])
    date_col = first_available_column(df, ["date", "日期"])
    close_col = first_available_column(df, ["close", "收盘"])
    if not date_col or not close_col:
        return pd.DataFrame(columns=["日期", "收盘"])
    out = pd.DataFrame()
    out["日期"] = pd.to_datetime(df[date_col]).dt.date.astype(str)
    out["收盘"] = df[close_col].map(safe_float)
    return out.sort_values("日期").dropna(subset=["收盘"])


def normalize_amount_series(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.dropna().empty:
        return numeric
    # Tencent historical quotes report turnover in ten-thousand yuan while
    # Eastmoney, Sina, and Baostock usually report yuan. Normalize the small
    # but positive turnover scale so liquidity filters compare one unit.
    high_quantile = numeric[numeric > 0].quantile(0.95)
    if pd.notna(high_quantile) and 0 < high_quantile < 10_000_000:
        return numeric * 10_000
    return numeric


def parse_financial_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {}
    if "指标" in df.columns:
        period_cols = sorted([col for col in df.columns if re.fullmatch(r"\d{8}", str(col))], reverse=True)
        if not period_cols:
            return {}
        return drop_missing_metrics({
            "roe": extract_metric(df, ["净资产收益率"], period_cols),
            "gross_margin": extract_metric(df, ["毛利率"], period_cols),
            "debt_ratio": extract_metric(df, ["资产负债率"], period_cols),
            "net_profit_growth": extract_metric(df, ["归属母公司净利润增长率", "净利润增长率"], period_cols),
        })

    latest = df.iloc[0]
    return drop_missing_metrics({
        "roe": safe_float(first_existing(latest, ["净资产收益率", "加权净资产收益率", "ROE"])),
        "gross_margin": safe_float(first_existing(latest, ["销售毛利率", "毛利率"])),
        "debt_ratio": safe_float(first_existing(latest, ["资产负债率"])),
        "net_profit_growth": safe_float(first_existing(latest, ["净利润增长率", "归属母公司净利润增长率"])),
    })


def drop_missing_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if value is not None}


def extract_metric(df: pd.DataFrame, names: list[str], period_cols: list[str]) -> float | None:
    indicators = df["指标"].astype(str)
    mask = False
    for name in names:
        mask = mask | indicators.str.contains(name, na=False)
    matched = df[mask]
    if matched.empty:
        return None
    row = matched.iloc[0]
    for col in period_cols:
        value = safe_float(row.get(col))
        if value is not None:
            return value
    return None


def first_available_column(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def first_existing(row: pd.Series, names: list[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, "", "--"):
            return row[name]
    return None
