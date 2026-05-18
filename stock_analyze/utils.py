from __future__ import annotations

import csv
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


def ensure_dirs(*paths: str | Path) -> None:
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def today_str() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "nan", "None", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    number = safe_float(value)
    if number is None:
        return None
    return int(number)


def read_json(path: str | Path, default: Any) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    file_path = Path(path)
    ensure_dirs(file_path.parent)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_csv(path: str | Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        return
    file_path = Path(path)
    ensure_dirs(file_path.parent)
    exists = file_path.exists()
    with file_path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def read_csv(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame()
    return pd.read_csv(file_path, dtype={"code": str})


def parse_date(value: str | date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def next_business_day(value: str | date | datetime | None) -> str:
    day = parse_date(value) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def previous_calendar_date(days: int, value: str | date | datetime | None = None) -> str:
    return (parse_date(value) - timedelta(days=days)).strftime("%Y%m%d")


def ak_date(value: str | date | datetime | None = None) -> str:
    return parse_date(value).strftime("%Y%m%d")


def pct_change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return (end / start) - 1


def format_pct(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number * 100:.2f}%"


def format_money(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:,.2f}"


def unique_rows(rows: Iterable[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(row.get(item) for item in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result

