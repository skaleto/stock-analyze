"""Source-dated fund announcement ingestion and active risk-state evaluation."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from ...utils import write_dataframe_csv_atomic


PARSER_VERSION = "qdii-fund-events-v2"
EVENT_COLUMNS = [
    "event_id",
    "report_id",
    "code",
    "name",
    "category",
    "title",
    "published_at",
    "observed_at",
    "effective_at",
    "expires_at",
    "event_type",
    "severity",
    "hard_block",
    "clears_temporary_blocks",
    "restriction_scope",
    "confirmation_status",
    "source_kind",
    "valid_to",
    "resume_of",
    "source_url",
    "raw_content_hash",
    "parser_version",
]


@dataclass(frozen=True)
class EventClassification:
    event_type: str
    severity: str
    hard_block: bool = False
    clears_temporary_blocks: bool = False
    expires_days: int | None = 30
    restriction_scope: str = ""


def _restriction_scope(text: str) -> str:
    has_purchase = bool(re.search(r"申购|定期定额|定投", text))
    has_redemption = "赎回" in text
    has_trading = bool(re.search(r"上市交易|暂停交易|恢复交易|停牌|复牌", text))
    if has_trading:
        return "trading"
    if has_purchase and has_redemption:
        return "purchase_redemption"
    if has_purchase:
        return "purchase"
    if has_redemption:
        return "redemption"
    return ""


def classify_title(title: str) -> EventClassification:
    text = re.sub(r"\s+", "", str(title or ""))
    if re.search(r"恢复.*(?:申购|赎回|交易)|恢复交易.*(?:申购|赎回)", text):
        return EventClassification(
            "resume",
            "info",
            clears_temporary_blocks=True,
            expires_days=7,
            restriction_scope=_restriction_scope(text),
        )
    if re.search(r"终止|清盘|清算|基金合同失效", text):
        return EventClassification(
            "termination",
            "hard",
            hard_block=True,
            expires_days=None,
            restriction_scope="fund",
        )
    if re.search(r"限制.*(?:申购|赎回)|暂停大额|申购限额|额度限制", text):
        return EventClassification(
            "purchase_restriction",
            "hard",
            hard_block=True,
            expires_days=None,
            restriction_scope=_restriction_scope(text) or "purchase",
        )
    if re.search(r"暂停.*(?:申购|赎回|交易)|暂停上市", text):
        return EventClassification(
            "suspension",
            "hard",
            hard_block=True,
            expires_days=None,
            restriction_scope=_restriction_scope(text) or "trading",
        )
    if re.search(r"溢价.*风险|交易价格.*风险提示", text):
        return EventClassification("premium_warning", "warning", expires_days=10)
    if re.search(r"份额.*(?:拆分|合并|折算)|基金份额拆分", text):
        return EventClassification("share_action", "info", expires_days=14)
    if re.search(r"分红|收益分配", text):
        return EventClassification("dividend", "info", expires_days=14)
    if re.search(r"基金经理.*变更|增聘|解聘", text):
        return EventClassification("manager_change", "warning", expires_days=45)
    if re.search(r"标的指数|业绩比较基准|指数.*变更", text):
        return EventClassification("index_change", "warning", expires_days=45)
    return EventClassification("other", "info", expires_days=14)


def _ts(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid event timestamp: {value}")
    return parsed.to_pydatetime().replace(tzinfo=None).isoformat(timespec="seconds")


def _event_row(raw: dict[str, Any], code: str, observed_at: datetime) -> dict[str, Any]:
    title = str(raw.get("TITLE") or raw.get("title") or "").strip()
    if not title:
        raise ValueError("fund event title is required")
    published_at = _ts(raw.get("PUBLISHDATE") or raw.get("published_at"))
    classification = classify_title(title)
    raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    report_id = str(raw.get("ID") or raw.get("report_id") or "").strip()
    event_id = report_id or raw_hash[:24]
    expires_at = str(raw.get("expires_at") or "").strip()
    if classification.expires_days is not None:
        expires_at = (
            datetime.fromisoformat(published_at) + timedelta(days=classification.expires_days)
        ).isoformat(timespec="seconds")
    valid_to = str(raw.get("valid_to") or expires_at).strip()
    symbol = str(raw.get("FUNDCODE") or code.split(".")[0]).strip()
    normalized = code if "." in code else symbol
    source_kind = str(raw.get("source_kind") or "aggregator").strip().lower()
    confirmation_status = str(raw.get("confirmation_status") or "discovered").strip().lower()
    source_url = str(raw.get("source_url") or "").strip()
    if not source_url:
        source_url = f"https://fund.eastmoney.com/gonggao/{symbol},{event_id}.html"
    return {
        "event_id": event_id,
        "report_id": report_id,
        "code": normalized,
        "name": str(raw.get("ShortTitle") or raw.get("name") or "").strip(),
        "category": str(raw.get("NEWCATEGORY") or raw.get("category") or "1"),
        "title": title,
        "published_at": published_at,
        "observed_at": observed_at.replace(tzinfo=None).isoformat(timespec="seconds"),
        "effective_at": published_at,
        "expires_at": expires_at,
        "event_type": classification.event_type,
        "severity": classification.severity,
        "hard_block": classification.hard_block,
        "clears_temporary_blocks": classification.clears_temporary_blocks,
        "restriction_scope": str(
            raw.get("restriction_scope") or classification.restriction_scope
        ).strip(),
        "confirmation_status": confirmation_status,
        "source_kind": source_kind,
        "valid_to": valid_to,
        "resume_of": str(raw.get("resume_of") or "").strip(),
        "source_url": source_url,
        "raw_content_hash": raw_hash,
        "parser_version": PARSER_VERSION,
    }


def fetch_eastmoney_announcements(code: str, *, timeout: float = 20.0) -> list[dict[str, Any]]:
    """Fetch all public announcement categories for one mainland fund code."""

    symbol = str(code).split(".")[0]
    output: list[dict[str, Any]] = []
    for category in ("1", "2", "3", "4"):
        query = urllib.parse.urlencode(
            {
                "fundcode": symbol,
                "pageIndex": "1",
                "pageSize": "1000",
                "type": category,
                "_": int(time.time() * 1000),
            }
        )
        request = urllib.request.Request(
            f"https://api.fund.eastmoney.com/f10/JJGG?{query}",
            headers={
                "User-Agent": "Mozilla/5.0 stock-analyze/1.0",
                "Referer": f"https://fundf10.eastmoney.com/jjgg_{symbol}_{category}.html",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.load(response)
        if int(payload.get("ErrCode") or 0) != 0:
            raise RuntimeError(f"eastmoney_fund_event_error:{payload.get('ErrMsg')}")
        output.extend(dict(item) for item in (payload.get("Data") or []))
    return output


def load_event_store(path: str | Path) -> pd.DataFrame:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    frame = pd.read_csv(
        target,
        dtype={
            "event_id": str,
            "report_id": str,
            "code": str,
            "category": str,
            "published_at": str,
            "observed_at": str,
            "effective_at": str,
            "expires_at": str,
            "restriction_scope": str,
            "confirmation_status": str,
            "source_kind": str,
            "valid_to": str,
            "resume_of": str,
            "raw_content_hash": str,
            "parser_version": str,
        },
    )
    for column in EVENT_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    for column in ("hard_block", "clears_temporary_blocks"):
        frame[column] = frame[column].astype(str).str.lower().isin({"true", "1", "yes"})
    return frame[EVENT_COLUMNS]


def refresh_event_store(
    codes: Iterable[str],
    path: str | Path,
    *,
    fetcher: Callable[[str], list[dict[str, Any]]] = fetch_eastmoney_announcements,
    observed_at: datetime | None = None,
) -> pd.DataFrame:
    observed = observed_at or datetime.now()
    target = Path(path)
    existing = load_event_store(target)
    rows: list[dict[str, Any]] = []
    for code in sorted({str(value) for value in codes if str(value)}):
        for raw in fetcher(code):
            rows.append(_event_row(raw, code, observed))
    fresh = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    combined = pd.concat([existing, fresh], ignore_index=True)
    if not combined.empty:
        combined = combined.drop_duplicates(["code", "event_id"], keep="first")
        combined = combined.sort_values(["published_at", "code", "event_id"]).reset_index(drop=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_dataframe_csv_atomic(combined, target, index=False, encoding="utf-8")
    return combined


def active_event_state(events: pd.DataFrame, code: str, as_of: str | datetime) -> dict[str, Any]:
    cutoff = pd.Timestamp(as_of)
    empty = {
        "hard_block": False,
        "hard_events": [],
        "unconfirmed_hard_events": [],
        "warnings": [],
        "recent_events": [],
    }
    if events is None or events.empty:
        return empty
    rows = events.loc[events["code"].astype(str).eq(str(code))].copy()
    if rows.empty:
        return empty
    for column in ("expires_at", "valid_to"):
        if column not in rows.columns:
            rows[column] = ""
    for column in ("published_at", "observed_at", "effective_at", "expires_at", "valid_to"):
        rows[f"_{column}"] = pd.to_datetime(rows[column], errors="coerce")
    if "event_id" not in rows.columns:
        rows["event_id"] = rows.index.astype(str)
    rows = rows.loc[
        rows["_published_at"].le(cutoff)
        & rows["_observed_at"].le(cutoff)
        & rows["_effective_at"].le(cutoff)
    ].sort_values(["_effective_at", "_observed_at", "event_id"])
    temporary: dict[str, dict[str, dict[str, Any]]] = {}
    unconfirmed: dict[str, dict[str, dict[str, Any]]] = {}
    permanent: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []

    def truthy(value: Any) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes"}

    def scopes(value: Any) -> set[str]:
        scope = str(value or "").strip().lower()
        if scope == "purchase_redemption":
            return {"purchase", "redemption"}
        return {scope} if scope else {"unspecified"}

    def add_scoped(
        container: dict[str, dict[str, dict[str, Any]]],
        row_scopes: set[str],
        public: dict[str, Any],
    ) -> None:
        event_id = str(public.get("event_id") or "")
        for scope in row_scopes:
            container.setdefault(scope, {})[event_id] = public

    def clear_scoped(
        container: dict[str, dict[str, dict[str, Any]]],
        row_scopes: set[str],
        resume_of: str,
    ) -> None:
        if resume_of:
            for active in container.values():
                active.pop(resume_of, None)
        for scope in row_scopes:
            container.pop(scope, None)

    def flatten(container: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        deduplicated: dict[str, dict[str, Any]] = {}
        for active in container.values():
            deduplicated.update(active)
        return list(deduplicated.values())

    for row in rows.to_dict(orient="records"):
        restriction_scope = str(row.get("restriction_scope") or "").strip()
        if not restriction_scope:
            restriction_scope = classify_title(str(row.get("title") or "")).restriction_scope
        confirmation_status = str(row.get("confirmation_status") or "discovered").strip().lower()
        public = {
            key: row.get(key)
            for key in (
                "event_id",
                "event_type",
                "severity",
                "title",
                "published_at",
                "source_url",
            )
        }
        public["restriction_scope"] = restriction_scope
        public["confirmation_status"] = confirmation_status
        visible.append(public)
        row_scopes = scopes(restriction_scope)
        if truthy(row.get("clears_temporary_blocks")):
            resume_of = str(row.get("resume_of") or "").strip()
            clear_scoped(temporary, row_scopes, resume_of)
            clear_scoped(unconfirmed, row_scopes, resume_of)
        if truthy(row.get("hard_block")):
            valid_to = row.get("_valid_to")
            if pd.isna(valid_to):
                valid_to = row.get("_expires_at")
            active = pd.isna(valid_to) or valid_to >= cutoff
            if not active:
                continue
            confirmed = confirmation_status == "confirmed"
            if str(row.get("event_type")) == "termination":
                if confirmed:
                    permanent.append(public)
                else:
                    add_scoped(unconfirmed, row_scopes, public)
            elif confirmed:
                add_scoped(temporary, row_scopes, public)
            else:
                add_scoped(unconfirmed, row_scopes, public)
        elif str(row.get("severity")) == "warning":
            valid_to = row.get("_valid_to")
            if pd.isna(valid_to):
                valid_to = row.get("_expires_at")
            if pd.isna(valid_to) or valid_to >= cutoff:
                warnings.append(public)
    hard_events = permanent + flatten(temporary)
    result = {
        "hard_block": bool(hard_events),
        "hard_events": hard_events[-5:],
        "unconfirmed_hard_events": flatten(unconfirmed)[-5:],
        "warnings": warnings[-5:],
        "recent_events": visible[-10:][::-1],
    }
    if visible:
        result["latest_event_type"] = visible[-1]["event_type"]
        result["latest_published_at"] = visible[-1]["published_at"]
    return result


__all__ = [
    "EVENT_COLUMNS",
    "EventClassification",
    "active_event_state",
    "classify_title",
    "fetch_eastmoney_announcements",
    "load_event_store",
    "refresh_event_store",
]
