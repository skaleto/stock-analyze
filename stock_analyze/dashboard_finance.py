"""Finance-specific display metadata for the dynamic dashboard."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .strategy_registry import StrategyRegistryInvalid, strategy_display_name


ACCOUNT_LABELS = {
    "hs300": "沪深300账户",
    "zz500": "中证500账户",
    "us_exposure": "美国市场ETF账户",
    "hk_exposure": "香港市场ETF账户",
}

SIDE_LABELS = {
    "buy": "买入",
    "sell": "卖出",
}

FACTOR_METADATA: dict[str, dict[str, str]] = {
    "pe": {
        "label": "市盈率 PE",
        "explanation": "股价相对每股盈利的倍数，通常越低越便宜，但要结合行业和增长看。",
    },
    "pb": {
        "label": "市净率 PB",
        "explanation": "股价相对每股净资产的倍数，反映市场给公司资产的定价。",
    },
    "roe": {
        "label": "净资产收益率 ROE",
        "explanation": "公司用股东投入的净资产创造利润的效率，通常越高越好。",
    },
    "gross_margin": {
        "label": "毛利率",
        "explanation": "销售收入扣除直接成本后的利润比例，体现产品盈利空间。",
    },
    "debt_ratio": {
        "label": "资产负债率",
        "explanation": "负债占总资产的比例，过高可能意味着偿债压力更大。",
    },
    "net_profit_growth": {
        "label": "净利润增速",
        "explanation": "净利润相较上一期的增长速度，用来观察盈利是否在改善。",
    },
    "dividend_yield": {
        "label": "股息率",
        "explanation": "过去一年现金分红相对股价的比例，反映持有期间的现金回报。",
    },
    "momentum_20": {
        "label": "近20日动量",
        "explanation": "最近20个交易日的价格变化，正值表示近期走势偏强。",
    },
    "momentum_60": {
        "label": "近60日动量",
        "explanation": "最近60个交易日的价格变化，用来观察中期趋势。",
    },
    "low_volatility_60": {
        "label": "近60日波动率",
        "explanation": "最近60个交易日涨跌的离散程度，数值越低通常越稳定。",
    },
    "avg_amount_20": {
        "label": "20日平均成交额",
        "explanation": "最近20个交易日的平均成交金额，用来判断买卖是否活跃。",
    },
    "discount_premium": {
        "label": "折溢价率",
        "explanation": "ETF市场价格相对基金净值的偏离，正值为溢价、负值为折价。",
    },
    "codex_market_sentiment_1w": {
        "label": "趋势进攻市场情绪",
        "explanation": "趋势进攻策略槽对最近一周市场信息的结构化情绪评分。",
    },
    "claude_market_sentiment_1w": {
        "label": "稳健防守市场情绪",
        "explanation": "稳健防守策略槽对最近一周市场信息的结构化情绪评分。",
    },
    "codex_sector_sentiment": {
        "label": "趋势进攻行业情绪",
        "explanation": "趋势进攻策略槽对不同行业最近一周信息的结构化情绪评分。",
    },
    "claude_sector_sentiment": {
        "label": "稳健防守行业情绪",
        "explanation": "稳健防守策略槽对不同行业最近一周信息的结构化情绪评分。",
    },
}


class InvalidInstrumentCode(ValueError):
    """A dashboard instrument code does not match a domestic security code."""


class InstrumentDataError(RuntimeError):
    """An existing cached instrument artifact is malformed."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"instrument data source is unreadable: {source}")


def factor_metadata(key: str) -> dict[str, str]:
    return dict(
        FACTOR_METADATA.get(
            key,
            {"label": key, "explanation": "该指标暂未配置中文解释。"},
        )
    )


def instrument_metadata(
    market: str,
    code: str,
    name: str | None = None,
    *,
    industry: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    if market == "cn_qdii_etf":
        from .markets.cn_qdii_etf.universe import metadata_for_code

        classification = metadata_for_code(code, repo_root=repo_root)
    else:
        group = str(industry or "未分类")
        classification = {"exposure_group": group, "theme": group}
    return {
        "code": str(code),
        "name": name,
        **classification,
    }


def enrich_rows(
    market: str,
    rows: list[dict[str, Any]],
    *,
    repo_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        code = str(row.get("code") or "")
        if code:
            row.update(
                instrument_metadata(
                    market,
                    code,
                    str(row.get("name") or "") or None,
                    industry=str(row.get("industry") or "") or None,
                    repo_root=repo_root,
                )
            )
        account_id = str(row.get("account_id") or "")
        row["account_label"] = ACCOUNT_LABELS.get(account_id, account_id or "未分账户")
        side = str(row.get("side") or "").lower()
        row["side_label"] = SIDE_LABELS.get(side, side or "-")
        enriched.append(row)
    return enriched


def build_activity(
    trades: list[dict[str, Any]],
    orders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in trades:
        side_label = str(raw.get("side_label") or SIDE_LABELS.get(str(raw.get("side") or ""), ""))
        events.append(
            {
                **raw,
                "date": raw.get("trade_date"),
                "status": "completed",
                "status_label": f"已{side_label}",
                "event_type": "trade",
            }
        )
    for raw in orders:
        side_label = str(raw.get("side_label") or SIDE_LABELS.get(str(raw.get("side") or ""), ""))
        events.append(
            {
                **raw,
                "date": raw.get("execute_after") or raw.get("trade_date"),
                "status": "planned",
                "status_label": f"计划{side_label}",
                "event_type": "order",
            }
        )
    return sorted(
        events,
        key=lambda event: (
            str(event.get("date") or ""),
            event.get("status") == "planned",
            str(event.get("code") or ""),
        ),
        reverse=True,
    )


def build_strategy_profile(
    config_path: Path,
    *,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    agent_id = str(payload.get("agent_id") or "")
    base_agent = agent_id.split("_", 1)[0].lower()
    try:
        agent_label = strategy_display_name(base_agent, repo_root)
    except StrategyRegistryInvalid:
        agent_label = {
            "claude": "稳健防守",
            "codex": "趋势进攻",
        }.get(base_agent, payload.get("name") or f"{agent_id or '未知'} 策略")
    factors: list[dict[str, Any]] = []
    for key, raw in (payload.get("factors") or {}).items():
        config = raw if isinstance(raw, dict) else {"weight": raw}
        weight = float(config.get("weight") or 0.0)
        direction = str(config.get("direction") or "high")
        factors.append(
            {
                "key": key,
                **factor_metadata(key),
                "weight": weight,
                "direction": direction,
                "direction_label": "偏好高值" if direction == "high" else "偏好低值",
            }
        )
    factors.sort(key=lambda item: (-item["weight"], item["key"]))
    return {
        "agent": base_agent or agent_id,
        "agent_label": agent_label,
        "strategy_id": payload.get("strategy_id"),
        "name": payload.get("name") or agent_label,
        "factors": factors,
    }


_INSTRUMENT_CODE = re.compile(r"^[0-9]{6}(?:\.(?:SH|SZ))?$")


def normalize_instrument_code(market: str, code: str) -> str:
    raw = str(code or "").strip().upper()
    if not _INSTRUMENT_CODE.fullmatch(raw):
        raise InvalidInstrumentCode(raw)
    if "." in raw:
        return raw
    digits = raw[:6]
    if market == "cn_qdii_etf":
        exchange = "SH" if digits.startswith(("51", "58")) else "SZ"
    else:
        exchange = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
    return f"{digits}.{exchange}"


def _latest_cache_file(paths: list[Path], date_pattern: re.Pattern[str]) -> Path | None:
    dated: list[tuple[str, int, str, Path]] = []
    for path in paths:
        match = date_pattern.search(path.name)
        if match:
            days = int(match.groupdict().get("days") or 0)
            dated.append((match.group("date"), days, path.name, path))
    return max(dated, default=("", 0, "", None))[-1]


def read_instrument_history(
    repo_root: Path,
    market: str,
    code: str,
) -> tuple[str, list[dict[str, Any]], str | None]:
    normalized = normalize_instrument_code(market, code)
    digits, exchange = normalized.split(".", 1)
    if market == "cn_qdii_etf":
        cache = repo_root / "data" / market / "shared" / "cache"
        candidates = list(cache.glob(f"fund_daily_{digits}_{exchange}_*.csv"))
        path = _latest_cache_file(
            candidates,
            re.compile(r"_(?P<date>[0-9]{8})\.csv$"),
        )
        rename = {
            "trade_date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "vol": "volume",
            "amount": "amount",
        }
        date_column = "trade_date"
    else:
        cache = repo_root / "data" / "shared" / "cache"
        candidates = list(cache.glob(f"history_{digits}_*_*.csv"))
        path = _latest_cache_file(
            candidates,
            re.compile(rf"history_{digits}_(?P<date>[0-9]{{8}})_(?P<days>[0-9]+)\.csv$"),
        )
        rename = {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        date_column = "日期"
    if path is None:
        return normalized, [], "暂无可用的历史行情缓存"
    try:
        frame = pd.read_csv(path, dtype={date_column: str}, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001
        raise InstrumentDataError("instrument_history") from exc
    required = {date_column, *[key for key in rename if key not in {"成交量", "成交额", "vol", "amount"}]}
    if frame.empty or not required.issubset(frame.columns):
        raise InstrumentDataError("instrument_history")
    frame = frame.rename(columns=rename)
    if "volume" not in frame.columns:
        frame["volume"] = None
    if "amount" not in frame.columns:
        frame["amount"] = None
    for column in ("open", "high", "low", "close", "volume", "amount"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if market == "cn_qdii_etf":
        from .markets.cn_qdii_etf.data_provider import TUSHARE_AMOUNT_TO_YUAN

        frame["amount"] = frame["amount"] * TUSHARE_AMOUNT_TO_YUAN
    date_text = frame["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    frame["date"] = (
        date_text.str[:4] + "-" + date_text.str[4:6] + "-" + date_text.str[6:8]
    )
    frame = (
        frame.dropna(subset=["open", "high", "low", "close"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
    )
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    if not parsed_dates.dropna().empty:
        cutoff = parsed_dates.max() - pd.DateOffset(years=3)
        frame = frame.loc[parsed_dates >= cutoff]
    records = frame[["date", "open", "high", "low", "close", "volume", "amount"]].to_dict(
        orient="records"
    )
    return normalized, records, None


def build_history_metrics(
    candles: list[dict[str, Any]],
    factor_values: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    if not candles:
        return []
    frame = pd.DataFrame(candles)
    closes = pd.to_numeric(frame["close"], errors="coerce")
    amounts = pd.to_numeric(frame.get("amount"), errors="coerce")
    values: dict[str, float | None] = {
        "momentum_20": (closes.iloc[-1] / closes.iloc[-21] - 1.0) if len(closes) >= 21 and closes.iloc[-21] else None,
        "momentum_60": (closes.iloc[-1] / closes.iloc[-61] - 1.0) if len(closes) >= 61 and closes.iloc[-61] else None,
        "low_volatility_60": closes.pct_change().tail(60).std() if len(closes) >= 3 else None,
        "avg_amount_20": amounts.tail(20).mean() if amounts is not None and not amounts.dropna().empty else None,
    }
    values.update(factor_values or {})
    metrics: list[dict[str, Any]] = []
    for key, value in values.items():
        if value is None or pd.isna(value):
            continue
        meta = factor_metadata(key)
        metrics.append(
            {
                "key": key,
                **meta,
                "value": float(value),
                "format": "money" if key == "avg_amount_20" else "number" if key in {"pe", "pb"} else "percent",
            }
        )
    return metrics


def read_latest_factor_values(
    repo_root: Path,
    agent: str,
    code: str,
) -> dict[str, float]:
    factor_dir = repo_root / "data" / "a_share" / agent / "factor_runs"
    candidates = sorted(factor_dir.glob("*.csv"))
    if not candidates:
        return {}
    try:
        frame = pd.read_csv(
            candidates[-1],
            dtype={"code": str, "factor": str},
            keep_default_na=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise InstrumentDataError("factor_snapshot") from exc
    if not {"code", "factor", "raw"}.issubset(frame.columns):
        raise InstrumentDataError("factor_snapshot")
    digits = normalize_instrument_code("a_share", code).split(".", 1)[0]
    selected = frame[frame["code"].astype(str).str.zfill(6) == digits]
    values: dict[str, float] = {}
    for row in selected[["factor", "raw"]].to_dict(orient="records"):
        try:
            values[str(row["factor"])] = float(row["raw"])
        except (TypeError, ValueError):
            continue
    return values
