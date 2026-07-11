"""Finance-specific display metadata for the dynamic dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
        "label": "Codex 市场情绪",
        "explanation": "Codex 对最近一周市场信息的结构化情绪评分。",
    },
    "claude_market_sentiment_1w": {
        "label": "Claude 市场情绪",
        "explanation": "Claude 对最近一周市场信息的结构化情绪评分。",
    },
    "codex_sector_sentiment": {
        "label": "Codex 行业情绪",
        "explanation": "Codex 对不同行业最近一周信息的结构化情绪评分。",
    },
    "claude_sector_sentiment": {
        "label": "Claude 行业情绪",
        "explanation": "Claude 对不同行业最近一周信息的结构化情绪评分。",
    },
}


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
) -> dict[str, Any]:
    if market == "cn_qdii_etf":
        from .markets.cn_qdii_etf.universe import metadata_for_code

        classification = metadata_for_code(code)
    else:
        group = str(industry or "未分类")
        classification = {"exposure_group": group, "theme": group}
    return {
        "code": str(code),
        "name": name,
        **classification,
    }


def enrich_rows(market: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def build_strategy_profile(config_path: Path) -> dict[str, Any]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    agent_id = str(payload.get("agent_id") or "")
    base_agent = agent_id.split("_", 1)[0].lower()
    agent_label = {
        "codex": "Codex 策略",
        "claude": "Claude 策略",
    }.get(base_agent, f"{agent_id or '未知'} 策略")
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
