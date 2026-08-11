"""Normalization and comparison primitives for Tushare and iFinD."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from ..utils import write_dataframe_csv_atomic, write_text_atomic
from .ifind_transport import IfindSdkTransport, result_by_id
from .store import IntelligenceStore
from .types import SourceDocument, utc_iso


TITLE_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
TOKEN_MARKERS = ("token", "userid", "password", "account")
MARKET_FIELDS = ("open", "high", "low", "close", "volume_shares", "amount_yuan")
MAINLAND_SECURITY_CODE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")


def normalize_announcement_title(title: object, name: object = "") -> str:
    value = unicodedata.normalize("NFKC", str(title or "")).strip()
    issuer = unicodedata.normalize("NFKC", str(name or "")).strip()
    if issuer:
        value = re.sub(
            rf"^{re.escape(issuer)}\s*[:：]\s*",
            "",
            value,
            count=1,
        )
    return TITLE_PATTERN.sub("", value.casefold())


def announcement_comparison_key(
    code: object,
    title: object,
    name: object = "",
) -> str:
    normalized_code = str(code or "").strip().upper()
    normalized_title = normalize_announcement_title(title, name)
    return f"{normalized_code}|{normalized_title}"


def _dedupe_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    secondary: bool,
) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = announcement_comparison_key(
            row.get("thscode" if secondary else "ts_code"),
            row.get("reportTitle" if secondary else "title"),
            row.get("secName" if secondary else "name"),
        )
        if key.endswith("|"):
            continue
        current = out.get(key)
        identity = str(
            row.get("seq" if secondary else "source_id") or ""
        )
        current_identity = str(
            (
                current.get("seq" if secondary else "source_id")
                if current
                else ""
            )
            or ""
        )
        if current is None or identity < current_identity:
            out[key] = row
    return out


def compare_announcement_rows(
    primary_rows: Iterable[Mapping[str, Any]],
    secondary_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    primary = _dedupe_rows(primary_rows, secondary=False)
    secondary = _dedupe_rows(secondary_rows, secondary=True)
    items: list[dict[str, Any]] = []
    counts = {
        "matched": 0,
        "primary_only": 0,
        "secondary_only": 0,
    }
    for key in sorted(set(primary) | set(secondary)):
        if key in primary and key in secondary:
            status = "matched"
        elif key in primary:
            status = "primary_only"
        else:
            status = "secondary_only"
        counts[status] += 1
        primary_row = primary.get(key) or {}
        secondary_row = secondary.get(key) or {}
        items.append(
            {
                "dataset": "announcement",
                "item_key": key,
                "comparison_status": status,
                "primary_id": str(primary_row.get("source_id") or ""),
                "secondary_id": str(secondary_row.get("seq") or ""),
                "detail": {
                    "code": (
                        primary_row.get("ts_code")
                        or secondary_row.get("thscode")
                    ),
                    "title": (
                        primary_row.get("title")
                        or secondary_row.get("reportTitle")
                    ),
                },
            }
        )
    return {"counts": counts, "items": items}


def _ifind_tables(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tables = payload.get("tables") or []
    return [
        item for item in tables if isinstance(item, Mapping)
    ] if isinstance(tables, list) else []


def normalize_ifind_hq(payload: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in _ifind_tables(payload):
        code = str(item.get("thscode") or "").strip().upper()
        times = item.get("time") or []
        table = item.get("table") or {}
        if not isinstance(times, list) or not isinstance(table, Mapping):
            continue
        for index, raw_date in enumerate(times):
            row: dict[str, Any] = {
                "ts_code": code,
                "trade_date": str(raw_date).replace("-", "")[:8],
                "source": "ifind_hq",
            }
            for field in ("open", "high", "low", "close"):
                values = table.get(field)
                row[field] = (
                    pd.to_numeric(values[index], errors="coerce")
                    if isinstance(values, list) and index < len(values)
                    else None
                )
            volume = table.get("volume")
            amount = table.get("amount")
            row["volume_shares"] = (
                pd.to_numeric(volume[index], errors="coerce")
                if isinstance(volume, list) and index < len(volume)
                else None
            )
            row["amount_yuan"] = (
                pd.to_numeric(amount[index], errors="coerce")
                if isinstance(amount, list) and index < len(amount)
                else None
            )
            rows.append(row)
    return pd.DataFrame(
        rows,
        columns=("ts_code", "trade_date", *MARKET_FIELDS, "source"),
    )


def compare_market_frames(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    *,
    price_tolerance: float = 1e-8,
    volume_tolerance: float = 1e-4,
    amount_tolerance: float = 1.0,
) -> dict[str, Any]:
    def indexed(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for row in frame.to_dict(orient="records"):
            key = (
                f"{str(row.get('ts_code') or '').upper()}|"
                f"{str(row.get('trade_date') or '').replace('-', '')[:8]}"
            )
            records[key] = row
        return records

    left = indexed(primary)
    right = indexed(secondary)
    counts = {
        "matched": 0,
        "mismatch": 0,
        "primary_only": 0,
        "secondary_only": 0,
    }
    items: list[dict[str, Any]] = []
    tolerances = {
        "open": price_tolerance,
        "high": price_tolerance,
        "low": price_tolerance,
        "close": price_tolerance,
        "volume_shares": volume_tolerance,
        "amount_yuan": amount_tolerance,
    }
    for key in sorted(set(left) | set(right)):
        differences: dict[str, float] = {}
        if key not in left:
            status = "secondary_only"
        elif key not in right:
            status = "primary_only"
        else:
            for field, tolerance in tolerances.items():
                primary_value = pd.to_numeric(
                    left[key].get(field),
                    errors="coerce",
                )
                secondary_value = pd.to_numeric(
                    right[key].get(field),
                    errors="coerce",
                )
                if pd.isna(primary_value) and pd.isna(secondary_value):
                    continue
                if pd.isna(primary_value) or pd.isna(secondary_value):
                    differences[field] = None
                    continue
                delta = abs(float(primary_value) - float(secondary_value))
                if delta > tolerance:
                    differences[field] = delta
            status = "mismatch" if differences else "matched"
        counts[status] += 1
        items.append(
            {
                "dataset": "market",
                "item_key": key,
                "comparison_status": status,
                "primary_id": key if key in left else "",
                "secondary_id": key if key in right else "",
                "detail": {"differences": differences},
            }
        )
    return {"counts": counts, "items": items}


def _published_at(row: Mapping[str, Any], seen_at: str) -> str:
    value = str(row.get("ctime") or "").strip()
    if not value:
        value = f"{row.get('reportDate')} 00:00:00"
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return utc_iso(seen_at)
    if pd.isna(parsed):
        return utc_iso(seen_at)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize(ZoneInfo("Asia/Shanghai"))
    return utc_iso(parsed.to_pydatetime())


def ifind_announcement_document(
    row: Mapping[str, Any],
    *,
    seen_at: str,
) -> SourceDocument:
    source_id = str(row.get("seq") or "").strip()
    if not source_id:
        identity = (
            f"{row.get('thscode')}|{row.get('reportDate')}|"
            f"{row.get('reportTitle')}"
        )
        source_id = hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()[:24]
    code = str(row.get("thscode") or "").strip().upper()
    name = str(row.get("secName") or "").strip()
    title = " ".join(str(row.get("reportTitle") or "").split())
    published_at = _published_at(row, seen_at)
    pdf_url = str(row.get("pdfURL") or "")
    metadata = {
        "provider": "ifind",
        "content_scope": "title_metadata",
        "ingestion_mode": "live",
        "report_date": str(row.get("reportDate") or ""),
        "rec_time": str(row.get("ctime") or ""),
        "ts_code": code,
        "name": name,
        "security_codes": [code] if code else [],
        "security_links": (
            [
                {
                    "ts_code": code,
                    "name": name,
                    "provenance": "ifind_report_query",
                }
            ]
            if code
            else []
        ),
        "pdf_available": bool(pdf_url),
    }
    if any(marker in str(metadata).casefold() for marker in TOKEN_MARKERS):
        raise ValueError("ifind_announcement_sensitive_metadata")
    return SourceDocument(
        source="ifind_announcement",
        source_id=source_id,
        title=title,
        published_at=published_at,
        first_seen_at=utc_iso(seen_at),
        effective_at=published_at,
        source_url="",
        content=f"ifind_announcement|{source_id}".encode("utf-8"),
        mime_type="text/plain",
        metadata=metadata,
    )


def ifind_report_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _ifind_tables(payload):
        table = item.get("table") or {}
        if not isinstance(table, Mapping):
            continue
        fields = list(table)
        lengths = [
            len(value)
            for value in table.values()
            if isinstance(value, list)
        ]
        for index in range(max(lengths, default=0)):
            row = {
                field: (
                    table[field][index]
                    if isinstance(table[field], list)
                    and index < len(table[field])
                    else None
                )
                for field in fields
            }
            code = str(row.get("thscode") or "").strip().upper()
            if (
                not MAINLAND_SECURITY_CODE.fullmatch(code)
                or code.startswith(("200", "900"))
            ):
                continue
            rows.append(row)
    return rows


def _normalize_stock_code(value: object) -> str:
    raw = str(value or "").strip().upper()
    if "." in raw:
        return raw
    code = raw.zfill(6)
    suffix = (
        "SH"
        if code.startswith(("6", "9"))
        else ("BJ" if code.startswith(("4", "8")) else "SZ")
    )
    return f"{code}.{suffix}"


def _action_data(
    results: Mapping[str, Mapping[str, Any]],
    action_id: str,
) -> Mapping[str, Any]:
    result = results.get(action_id)
    if result is None:
        raise RuntimeError(f"ifind_action_missing:{action_id}")
    if int(result.get("errorcode") or 0) != 0:
        raise RuntimeError(
            f"ifind_action_failed:{action_id}:"
            f"{result.get('errorcode')}"
        )
    data = result.get("data") or {}
    if not isinstance(data, Mapping):
        raise RuntimeError(f"ifind_action_data_invalid:{action_id}")
    return data


class CrossSourceAuditor:
    """Compare current Tushare materializations with iFinD and repair gaps."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        transport: IfindSdkTransport | None = None,
    ) -> None:
        self.root = Path(repo_root)
        self.transport = transport or IfindSdkTransport(
            repo_root=self.root
        )
        self.store = IntelligenceStore(
            self.root / "data" / "shared" / "intelligence"
        )

    def resolve_as_of(self, explicit: str | None) -> str:
        if explicit:
            datetime.strptime(explicit, "%Y-%m-%d")
            return explicit

        a_share_dates: set[str] = set()
        for path in (
            self.root / "data" / "shared"
        ).glob("market_snapshot_*.json"):
            try:
                snapshot = json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if snapshot.get("status") in {"success", "partial"}:
                a_share_dates.add(
                    str(snapshot.get("as_of") or "")
                )

        qdii_dates: set[str] = set()
        for path in (
            self.root / "data" / "cn_qdii_etf" / "shared"
        ).glob("market_snapshot_*.json"):
            try:
                snapshot = json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                continue
            if (
                snapshot.get("status") in {"success", "partial"}
                and int(snapshot.get("fresh_codes") or 0) > 0
            ):
                qdii_dates.add(str(snapshot.get("as_of") or ""))

        common = {
            value
            for value in a_share_dates.intersection(qdii_dates)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        }
        if common:
            return max(common)
        available = {
            value
            for value in a_share_dates.union(qdii_dates)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        }
        if available:
            return max(available)

        current = pd.Timestamp.now(tz="Asia/Shanghai").normalize()
        while current.dayofweek >= 5:
            current -= pd.Timedelta(days=1)
        return current.strftime("%Y-%m-%d")

    def operational_codes(self) -> tuple[str, ...]:
        roots = (
            self.root / "data" / "a_share",
            self.root / "data" / "cn_qdii_etf",
            self.root / "data" / "model_iterations",
        )
        codes: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, Mapping):
                raw_code = value.get("code")
                if raw_code and re.match(r"^\d{6}", str(raw_code)):
                    codes.add(_normalize_stock_code(raw_code))
                positions = value.get("positions")
                if isinstance(positions, Mapping):
                    for code in positions:
                        if re.match(r"^\d{6}", str(code)):
                            codes.add(_normalize_stock_code(code))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for root in roots:
            if not root.exists():
                continue
            for pattern in ("**/state.json", "**/pending_orders.json"):
                for path in root.glob(pattern):
                    try:
                        visit(json.loads(path.read_text(encoding="utf-8")))
                    except (OSError, json.JSONDecodeError):
                        continue
        return tuple(sorted(codes))

    def _a_share_snapshot(self, as_of: str) -> dict[str, Any]:
        path = (
            self.root
            / "data"
            / "shared"
            / f"market_snapshot_{as_of}.json"
        )
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _a_share_codes(self, as_of: str) -> list[str]:
        snapshot = self._a_share_snapshot(as_of)
        raw_codes = snapshot.get("target_codes") or []
        if raw_codes:
            return sorted({
                _normalize_stock_code(code)
                for code in raw_codes
                if str(code).strip()
            })
        stamp = as_of.replace("-", "")
        cache = self.root / "data" / "shared" / "cache"
        codes = []
        for path in cache.glob(f"history_*_{stamp}_*.csv"):
            match = re.fullmatch(
                rf"history_(\d{{6}})_{stamp}_\d+\.csv",
                path.name,
            )
            if match:
                codes.append(_normalize_stock_code(match.group(1)))
        return sorted(set(codes))

    def _a_share_cache_path(
        self,
        code: str,
        as_of: str,
    ) -> Path | None:
        base_code = code.split(".")[0]
        stamp = as_of.replace("-", "")
        candidates = sorted(
            (
                self.root / "data" / "shared" / "cache"
            ).glob(f"history_{base_code}_{stamp}_*.csv")
        )
        return candidates[-1] if candidates else None

    def _qdii_snapshot(self, as_of: str) -> dict[str, Any]:
        path = (
            self.root
            / "data"
            / "cn_qdii_etf"
            / "shared"
            / f"market_snapshot_{as_of}.json"
        )
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _qdii_codes(self, as_of: str) -> list[str]:
        snapshot = self._qdii_snapshot(as_of)
        latest_dates = snapshot.get("latest_trade_dates") or {}
        if not isinstance(latest_dates, Mapping):
            return []
        return sorted({
            _normalize_stock_code(code)
            for code in latest_dates
            if str(code).strip()
        })

    def _qdii_cache_path(
        self,
        code: str,
        as_of: str,
    ) -> Path:
        return (
            self.root
            / "data"
            / "cn_qdii_etf"
            / "shared"
            / "cache"
            / (
                f"fund_daily_{code.replace('.', '_')}_"
                f"{as_of.replace('-', '')}.csv"
            )
        )

    def _a_share_primary(
        self,
        codes: Iterable[str],
        as_of: str,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        stamp = as_of.replace("-", "")
        for code in codes:
            path = self._a_share_cache_path(code, as_of)
            if path is None:
                continue
            frame = pd.read_csv(path, dtype={"日期": str})
            selected = frame[
                frame["日期"].astype(str).str.replace(
                    "-", "", regex=False
                ).str[:8]
                == stamp
            ]
            for row in selected.to_dict(orient="records"):
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": stamp,
                        "open": row.get("开盘"),
                        "high": row.get("最高"),
                        "low": row.get("最低"),
                        "close": row.get("收盘"),
                        "volume_shares": (
                            float(row["成交量"]) * 100.0
                            if pd.notna(row.get("成交量"))
                            else None
                        ),
                        "amount_yuan": row.get("成交额"),
                    }
                )
        return pd.DataFrame(rows)

    def _qdii_primary(
        self,
        codes: Iterable[str],
        as_of: str,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        stamp = as_of.replace("-", "")
        for code in codes:
            path = self._qdii_cache_path(code, as_of)
            if not path.exists():
                continue
            frame = pd.read_csv(
                path,
                dtype={"ts_code": str, "trade_date": str},
            )
            selected = frame[
                frame["trade_date"].astype(str).str.replace(
                    "-", "", regex=False
                ).str[:8]
                == stamp
            ]
            for row in selected.to_dict(orient="records"):
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": stamp,
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "volume_shares": (
                            float(row["vol"]) * 100.0
                            if pd.notna(row.get("vol"))
                            else None
                        ),
                        "amount_yuan": (
                            float(row["amount"]) * 1000.0
                            if pd.notna(row.get("amount"))
                            else None
                        ),
                    }
                )
        return pd.DataFrame(rows)

    def _repair_a_share_market(
        self,
        *,
        comparison: dict[str, Any],
        secondary: pd.DataFrame,
        as_of: str,
    ) -> int:
        indexed = {
            (
                f"{str(row['ts_code']).upper()}|"
                f"{str(row['trade_date'])}"
            ): row
            for row in secondary.to_dict(orient="records")
        }
        repaired = 0
        for item in comparison["items"]:
            if item["comparison_status"] != "secondary_only":
                continue
            row = indexed.get(item["item_key"])
            if row is None:
                continue
            path = self._a_share_cache_path(
                str(row["ts_code"]),
                as_of,
            )
            if path is None:
                item["detail"]["supplement_error"] = (
                    "history_cache_missing"
                )
                continue
            frame = pd.read_csv(path, dtype={"日期": str})
            stamp = as_of.replace("-", "")
            present = (
                frame["日期"].astype(str).str.replace(
                    "-", "", regex=False
                ).str[:8]
                == stamp
            ).any()
            if present:
                continue
            appended = pd.DataFrame(
                [
                    {
                        "日期": as_of,
                        "开盘": row.get("open"),
                        "收盘": row.get("close"),
                        "最高": row.get("high"),
                        "最低": row.get("low"),
                        "成交量": (
                            float(row["volume_shares"]) / 100.0
                            if pd.notna(row.get("volume_shares"))
                            else None
                        ),
                        "成交额": row.get("amount_yuan"),
                        "停牌": False,
                        "is_st": False,
                        "pe": None,
                        "pb": None,
                        "source": "ifind_hq_fallback",
                    }
                ]
            )
            combined = pd.concat(
                [frame, appended],
                ignore_index=True,
            )
            combined = combined.sort_values("日期").drop_duplicates(
                subset=["日期"],
                keep="last",
            )
            write_dataframe_csv_atomic(
                combined,
                path,
                index=False,
            )
            item["comparison_status"] = "supplemented"
            item["detail"]["supplement_source"] = "ifind_hq"
            repaired += 1
        return repaired

    def _repair_qdii_market(
        self,
        *,
        comparison: dict[str, Any],
        secondary: pd.DataFrame,
        as_of: str,
    ) -> int:
        indexed = {
            (
                f"{str(row['ts_code']).upper()}|"
                f"{str(row['trade_date'])}"
            ): row
            for row in secondary.to_dict(orient="records")
        }
        repaired = 0
        for item in comparison["items"]:
            if item["comparison_status"] != "secondary_only":
                continue
            row = indexed.get(item["item_key"])
            if row is None:
                continue
            path = self._qdii_cache_path(
                str(row["ts_code"]),
                as_of,
            )
            if not path.exists():
                item["detail"]["supplement_error"] = (
                    "fund_daily_cache_missing"
                )
                continue
            frame = pd.read_csv(
                path,
                dtype={"ts_code": str, "trade_date": str},
            )
            stamp = as_of.replace("-", "")
            present = (
                frame["trade_date"].astype(str).str.replace(
                    "-", "", regex=False
                ).str[:8]
                == stamp
            ).any()
            if present:
                continue
            appended = pd.DataFrame(
                [
                    {
                        "ts_code": str(row["ts_code"]),
                        "trade_date": stamp,
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "vol": (
                            float(row["volume_shares"]) / 100.0
                            if pd.notna(row.get("volume_shares"))
                            else None
                        ),
                        "amount": (
                            float(row["amount_yuan"]) / 1000.0
                            if pd.notna(row.get("amount_yuan"))
                            else None
                        ),
                        "source": "ifind_hq_fallback",
                    }
                ]
            )
            combined = pd.concat(
                [frame, appended],
                ignore_index=True,
            )
            combined = combined.sort_values(
                "trade_date",
                ascending=False,
            ).drop_duplicates(
                subset=["trade_date"],
                keep="last",
            )
            write_dataframe_csv_atomic(
                combined,
                path,
                index=False,
            )
            item["comparison_status"] = "supplemented"
            item["detail"]["supplement_source"] = "ifind_hq"
            repaired += 1
        return repaired

    def _primary_announcements(
        self,
        as_of: str,
    ) -> list[dict[str, Any]]:
        ann_date = as_of.replace("-", "")
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    d.source_id,
                    d.title,
                    l.ts_code,
                    l.name
                FROM documents d
                JOIN document_security_links l
                  ON l.document_id=d.id
                WHERE d.source='tushare_announcement'
                  AND json_extract(
                    d.metadata_json, '$.ann_date'
                  )=?
                GROUP BY d.source_id, d.title, l.ts_code, l.name
                ORDER BY d.source_id, l.ts_code
                """,
                (ann_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _quota(
        results: Mapping[str, Mapping[str, Any]],
        action_id: str,
    ) -> dict[str, Any]:
        try:
            data = _action_data(results, action_id)
        except RuntimeError:
            return {}
        tables = data.get("tables") or {}
        return dict(tables) if isinstance(tables, Mapping) else {}

    def run(
        self,
        *,
        as_of: str,
        datasets: set[str],
        full_market_announcements: bool = False,
        announcement_codes: Iterable[str] = (),
        supplement: bool = False,
    ) -> dict[str, Any]:
        unknown = set(datasets).difference({"market", "announcement"})
        if unknown or not datasets:
            raise ValueError("source_audit_datasets_invalid")
        datetime.strptime(as_of, "%Y-%m-%d")
        run_id = uuid.uuid4().hex
        started_at = utc_iso()
        actions: list[dict[str, Any]] = [
            {"id": "statistics_before", "op": "statistics"}
        ]
        a_share_codes: list[str] = []
        qdii_codes: list[str] = []
        if "market" in datasets:
            a_share_codes = self._a_share_codes(as_of)
            if a_share_codes:
                actions.append(
                    self.transport.history_action(
                        action_id="a_share_hq",
                        codes=a_share_codes,
                        start_date=as_of,
                        end_date=as_of,
                    )
                )
            qdii_codes = self._qdii_codes(as_of)
            if qdii_codes:
                actions.append(
                    self.transport.history_action(
                        action_id="qdii_hq",
                        codes=qdii_codes,
                        start_date=as_of,
                        end_date=as_of,
                    )
                )
        if "announcement" in datasets:
            actions.append(
                self.transport.announcement_action(
                    action_id="announcements",
                    start_date=as_of,
                    end_date=as_of,
                    codes=announcement_codes,
                    full_market=full_market_announcements,
                )
            )
        actions.append(
            {"id": "statistics_after", "op": "statistics"}
        )
        response = self.transport.execute(actions)
        indexed_results = result_by_id(response)
        dataset_results: dict[str, Any] = {}
        audit_items: list[dict[str, Any]] = []

        if "market" in datasets and a_share_codes:
            secondary_market = normalize_ifind_hq(
                _action_data(indexed_results, "a_share_hq")
            )
            primary_market = self._a_share_primary(
                a_share_codes,
                as_of,
            )
            comparison = compare_market_frames(
                primary_market,
                secondary_market,
            )
            for item in comparison["items"]:
                item["dataset"] = "a_share_market"
            supplemented = (
                self._repair_a_share_market(
                    comparison=comparison,
                    secondary=secondary_market,
                    as_of=as_of,
                )
                if supplement
                else 0
            )
            comparison["counts"]["supplemented"] = supplemented
            dataset_results["a_share_market"] = {
                "codes": len(a_share_codes),
                "counts": comparison["counts"],
            }
            audit_items.extend(comparison["items"])

        if "market" in datasets and qdii_codes:
            secondary_market = normalize_ifind_hq(
                _action_data(indexed_results, "qdii_hq")
            )
            primary_market = self._qdii_primary(
                qdii_codes,
                as_of,
            )
            comparison = compare_market_frames(
                primary_market,
                secondary_market,
            )
            for item in comparison["items"]:
                item["dataset"] = "qdii_market"
            supplemented = (
                self._repair_qdii_market(
                    comparison=comparison,
                    secondary=secondary_market,
                    as_of=as_of,
                )
                if supplement
                else 0
            )
            comparison["counts"]["supplemented"] = supplemented
            dataset_results["qdii_market"] = {
                "codes": len(qdii_codes),
                "counts": comparison["counts"],
            }
            audit_items.extend(comparison["items"])

        if "announcement" in datasets:
            secondary_rows = ifind_report_rows(
                _action_data(indexed_results, "announcements")
            )
            comparison = compare_announcement_rows(
                self._primary_announcements(as_of),
                secondary_rows,
            )
            supplemented = 0
            if supplement:
                secondary_by_key = _dedupe_rows(
                    secondary_rows,
                    secondary=True,
                )
                for item in comparison["items"]:
                    if item["comparison_status"] != "secondary_only":
                        continue
                    row = secondary_by_key[item["item_key"]]
                    document = ifind_announcement_document(
                        row,
                        seen_at=started_at,
                    )
                    document_id, _ = self.store.insert_document(
                        document
                    )
                    item["comparison_status"] = "supplemented"
                    item["secondary_id"] = document.source_id
                    item["detail"]["supplement_document_id"] = (
                        document_id
                    )
                    supplemented += 1
            comparison["counts"]["supplemented"] = supplemented
            dataset_results["announcement"] = {
                "counts": comparison["counts"],
            }
            audit_items.extend(comparison["items"])

        mismatch_count = sum(
            int(result["counts"].get("mismatch", 0))
            for result in dataset_results.values()
        )
        unresolved_market_gaps = sum(
            int(result["counts"].get("secondary_only", 0))
            - int(result["counts"].get("supplemented", 0))
            for name, result in dataset_results.items()
            if name.endswith("_market")
        )
        status = (
            "degraded"
            if mismatch_count or unresolved_market_gaps
            else "success"
        )
        finished_at = utc_iso()
        metrics = {
            "datasets": dataset_results,
            "quota_before": self._quota(
                indexed_results,
                "statistics_before",
            ),
            "quota_after": self._quota(
                indexed_results,
                "statistics_after",
            ),
        }
        self.store.record_source_audit(
            run_id=run_id,
            as_of_date=as_of,
            dataset_scope=",".join(sorted(datasets)),
            primary_source="tushare",
            secondary_source="ifind",
            status=status,
            supplement_enabled=supplement,
            metrics=metrics,
            items=audit_items,
            started_at=started_at,
            finished_at=finished_at,
        )
        report = {
            "run_id": run_id,
            "status": status,
            "as_of": as_of,
            "supplement_enabled": supplement,
            "datasets": dataset_results,
            "quota_before": metrics["quota_before"],
            "quota_after": metrics["quota_after"],
            "started_at": started_at,
            "finished_at": finished_at,
        }
        report_dir = self.root / "reports" / "intelligence"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / (
            f"source_audit_{as_of.replace('-', '')}_{run_id[:8]}.json"
        )
        payload = (
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )
        write_text_atomic(report_path, payload, encoding="utf-8")
        write_text_atomic(
            report_dir / "source_audit_latest.json",
            payload,
            encoding="utf-8",
        )
        report["report_path"] = str(report_path)
        return report


__all__ = [
    "announcement_comparison_key",
    "CrossSourceAuditor",
    "compare_announcement_rows",
    "compare_market_frames",
    "ifind_announcement_document",
    "ifind_report_rows",
    "normalize_announcement_title",
    "normalize_ifind_hq",
]
