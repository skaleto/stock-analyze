"""Localization labels and small formatting helpers for the reporting layer.

Extracted from ``reporting/__init__.py`` as Stage 3 of the I1 split
(2026-05-26 audit). The constants and functions here are pure (no
shared mutable state, no I/O) so moving them has zero risk of altering
production behavior.

All public symbols are re-exported from ``stock_analyze.reporting`` so any
caller doing ``from stock_analyze.reporting import localize_reason`` keeps
working unchanged.
"""

from __future__ import annotations

from typing import Any

from ..utils import safe_float


REASON_LABELS = {
    "not_selected": "本期未入选，调出组合",
    "history_missing": "历史行情缺失",
    "price_missing": "价格缺失",
    "financial_fetch_failed": "财务数据获取失败",
    "pe_missing": "PE 缺失",
    "pb_missing": "PB 缺失",
    "execution_quote_missing": "缺少模拟成交行情",
    "execution_quote_not_visible": "运行日尚无可见成交行情",
    "execution_price_missing": "模拟成交价缺失",
    "limit_up_buy_blocked": "涨停买入阻塞",
    "limit_down_sell_blocked": "跌停卖出阻塞",
    "paused": "停牌阻塞",
    "no_position": "无可卖持仓",
    "no_sellable_shares": "T+1 或可卖股数不足",
    "insufficient_cash": "现金不足",
    "partial_fill": "部分成交",
}

FACTOR_LABELS = {
    "pe": "PE",
    "pb": "PB",
    "roe": "ROE",
    "gross_margin": "毛利率",
    "debt_ratio": "资产负债率",
    "market_cap_yi": "总市值",
    "momentum_20": "20日动量",
    "momentum_60": "60日动量",
}

SOURCE_LABELS = {
    # Current sources (Tushare Pro primary + Baostock fallback)
    "spot_daily_basic": "Tushare daily_basic",
    "spot_daily": "Tushare daily",
    "spot_stock_basic": "Tushare stock_basic",
    "stock_basic": "Tushare 股票基础信息",
    "trade_cal": "Tushare 交易日历",
    "baostock_login": "Baostock 登录",
    "spot": "实时行情",
    "universe": "股票池",
    "basic": "个股基础信息",
    # Legacy labels kept so old data_health.json files still render
    "spot_eastmoney": "东方财富实时行情",
    "spot_sina": "新浪实时行情",
    "index_cons_csindex": "中证指数成分",
    "index_cons_weight_csindex": "中证权重成分",
    "index_cons_default": "AkShare 默认成分",
    "index_cons_baostock": "Baostock 指数成分",
    "history_eastmoney": "东方财富历史行情",
    "history_tencent": "腾讯历史行情",
    "history_sina": "新浪历史行情",
    "history_baostock": "Baostock 历史行情",
    "benchmark_eastmoney": "东方财富指数行情",
    "benchmark_tencent": "腾讯指数行情",
    "benchmark_sina": "新浪指数行情",
    "financial_abstract": "AkShare 财务摘要",
    "financial_indicator": "AkShare 财务指标",
    "financial_baostock": "Baostock 财务指标",
    "valuation_baostock": "Baostock 估值",
    "valuation_市盈率(TTM)": "百度估值 PE",
    "valuation_市净率": "百度估值 PB",
}


MAX_PANEL_CONTENT_BYTES = 16 * 1024


def localize_reason(value: Any) -> str:
    text = str(value or "")
    if text in REASON_LABELS:
        return REASON_LABELS[text]
    parts = []
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        if item in REASON_LABELS:
            parts.append(REASON_LABELS[item])
            continue
        if ":" in item:
            key, score = item.split(":", 1)
            parts.append(f"{FACTOR_LABELS.get(key, key)} 加分 {score}")
            continue
        parts.append(item)
    return "；".join(parts)


def localize_source(value: Any) -> str:
    text = str(value or "")
    for prefix, label in sorted(SOURCE_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        if text.startswith(prefix):
            suffix = text[len(prefix) :].strip("_")
            return f"{label} {suffix}".strip()
    return text


def localize_message(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("attempt="):
        attempt, _, detail = text.partition(":")
        prefix = attempt.replace("attempt=", "第 ", 1) + " 次尝试"
        return f"{prefix}：{simplify_error(detail.strip())}" if detail else prefix
    if text.startswith("using cache"):
        return text.replace("using cache", "使用本地缓存", 1)
    if text == "all realtime spot sources failed":
        return "全部实时行情源失败，已尝试缓存或降级数据"
    if text == "no constituents":
        return "股票池成分为空"
    simplified = simplify_error(text)
    if simplified != text:
        return simplified
    return text


def simplify_error(text: str) -> str:
    if not text:
        return ""
    if "RemoteDisconnected" in text:
        return "远端主动断开连接，已触发重试或降级"
    if "ProxyError" in text or "HTTPSConnectionPool" in text or "Max retries exceeded" in text:
        return "网络连接失败，已触发重试或降级"
    if "JSONDecodeError" in text:
        return "数据源返回内容无法解析，已触发重试或降级"
    return text


def format_rows(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return str(int(number))


def format_ratio(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.2f}"


def format_bps(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.1f}"


def format_duration_ms(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    if number < 1000:
        return f"{int(number)} ms"
    return f"{number / 1000:.1f} s"


def format_days(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{int(number)} d"


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _truncate(text: str, limit: int = MAX_PANEL_CONTENT_BYTES) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    return encoded.decode("utf-8", errors="ignore") + "\n…(truncated)"
