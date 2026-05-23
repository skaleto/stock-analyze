"""Beginner-friendly Chinese formatting helpers for the simplified dashboard.

These helpers convert numeric values, dates, and ratios into the spelling
and unit conventions a non-quant reader expects:

- 现金:`1,234元` / `1.23万元` / `1.23亿元`
- 百分比:`+1.32%` / `-0.84%` / `0.00%`
- 日期:`5月22日`、`昨天`、`上周二`、`上月15日`、`2025年12月18日`

All public functions tolerate ``None``/NaN inputs and never raise — the
beginner view should never crash on missing data.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any


__all__ = ["cny", "pct", "cn_date", "cn_relative_date"]


# Order matters: largest unit first so 12,345,678,901 → 亿元, not 万元.
_CNY_SCALES: tuple[tuple[float, str], ...] = (
    (1e8, "亿元"),
    (1e4, "万元"),
)

_WEEKDAY_CN: tuple[str, ...] = ("一", "二", "三", "四", "五", "六", "日")


def _coerce_number(value: Any) -> float | None:
    """Return ``value`` as a float, or ``None`` for NaN/empty/non-numeric."""

    if value is None:
        return None
    if isinstance(value, bool):
        # bool is an int subclass; refuse silently.
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "nan", "None", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _coerce_date(value: Any) -> date | None:
    """Return ``value`` as a ``datetime.date`` or ``None`` on failure."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Accept 2026-05-22, 2026/05/22, 20260522.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def cny(value: Any) -> str:
    """Format ``value`` as Chinese yuan with auto-selected unit.

    Examples:

    - ``cny(1234)`` → ``"1,234元"``
    - ``cny(12345)`` → ``"1.23万元"``
    - ``cny(123456789)`` → ``"1.23亿元"``
    - ``cny(-9876)`` → ``"-9,876元"``
    - ``cny(None)`` → ``"-"``
    """

    number = _coerce_number(value)
    if number is None:
        return "-"
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    for threshold, suffix in _CNY_SCALES:
        if magnitude >= threshold:
            scaled = magnitude / threshold
            return f"{sign}{scaled:.2f}{suffix}"
    # Below 1万: integer 元, thousand-separated.
    return f"{sign}{magnitude:,.0f}元"


def pct(value: Any, signed: bool = True, color: bool = False) -> str:
    """Format ``value`` (a ratio like 0.0132) as ``"+1.32%"``.

    - ``signed=True`` (default): always include a leading ``+`` or ``-``.
    - ``signed=False``: drop the ``+`` on positives, keep ``-`` on negatives.
    - ``color=True``: wrap output in a ``<span class="pos|neg|zero">`` so the
      CSS rules in ``beginner_dashboard.py`` colour positives red (中国习惯).

    Examples:

    - ``pct(0.0132)`` → ``"+1.32%"``
    - ``pct(-0.0084)`` → ``"-0.84%"``
    - ``pct(0)`` → ``"0.00%"``
    - ``pct(None)`` → ``"-"``
    """

    number = _coerce_number(value)
    if number is None:
        return "-"
    scaled = number * 100
    if signed and scaled > 0:
        text = f"+{scaled:.2f}%"
    else:
        text = f"{scaled:.2f}%"
    if not color:
        return text
    if scaled > 0:
        cls = "pos"
    elif scaled < 0:
        cls = "neg"
    else:
        cls = "zero"
    return f'<span class="{cls}">{text}</span>'


def cn_date(value: Any, today: Any = None) -> str:
    """Format a date as ``"5月22日"`` (drop the year if it matches ``today``).

    Examples (with today=2026-05-23):

    - ``cn_date("2026-05-22")`` → ``"5月22日"``
    - ``cn_date("2025-12-18")`` → ``"2025年12月18日"``
    - ``cn_date(None)`` → ``"-"``
    """

    day = _coerce_date(value)
    if day is None:
        return "-"
    reference = _coerce_date(today) or date.today()
    if day.year == reference.year:
        return f"{day.month}月{day.day}日"
    return f"{day.year}年{day.month}月{day.day}日"


def cn_relative_date(value: Any, today: Any = None) -> str:
    """Format a date relative to ``today`` in beginner-friendly Chinese.

    Examples (today=2026-05-23, a Saturday):

    - ``cn_relative_date("2026-05-23")`` → ``"今天"``
    - ``cn_relative_date("2026-05-22")`` → ``"昨天"``
    - ``cn_relative_date("2026-05-19")`` → ``"上周二"``  (within prior 7 days)
    - ``cn_relative_date("2026-04-15")`` → ``"上月15日"``  (in the prior month)
    - ``cn_relative_date("2025-12-18")`` → ``"2025年12月18日"``  (older)
    """

    day = _coerce_date(value)
    if day is None:
        return "-"
    reference = _coerce_date(today) or date.today()
    delta_days = (reference - day).days

    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "昨天"
    if delta_days == 2:
        return "前天"
    if 3 <= delta_days <= 7:
        return f"上周{_WEEKDAY_CN[day.weekday()]}"
    # Calendar-month-aware: same year, previous month.
    if day.year == reference.year and day.month == reference.month - 1:
        return f"上月{day.day}日"
    # Same year, more than a week ago: month/day.
    if day.year == reference.year:
        return f"{day.month}月{day.day}日"
    # Different year: full year/month/day.
    return f"{day.year}年{day.month}月{day.day}日"
