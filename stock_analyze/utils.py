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


def dashboard_fragment_path(reports_dir: str | Path) -> Path:
    """Where the per-agent dashboard fragment HTML should live.

    Fragments are an internal build artifact consumed by
    ``dashboard_aggregator.py`` when assembling
    ``reports/competition/dashboard.html``. They are NOT a user-facing
    page, so they must not pollute ``reports/`` (where the operator
    expects only viewable HTML).

    Convention (introduced 2026-05-24, §7.0 override):

    * Competition mode (``reports/<agent>/``) → ``data/_dashboard_build/<agent>/fragment.html``
    * Single-agent / legacy mode (``reports/``) → ``data/_dashboard_build/_default/fragment.html``

    Caller is responsible for creating the parent directory (use
    ``ensure_dirs(path.parent)``).
    """

    reports_path = Path(reports_dir)
    if reports_path.name == "reports":
        repo_root = reports_path.parent
        agent_dir = "_default"
    else:
        # Expected: <repo_root>/reports/<agent>
        repo_root = reports_path.parent.parent
        agent_dir = reports_path.name
    return repo_root / "data" / "_dashboard_build" / agent_dir / "fragment.html"


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

