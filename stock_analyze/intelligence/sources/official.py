"""Official disclosure/policy adapters and licensed Tushare announcements."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import (
    parse_qs,
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
)
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup

from ..http import IntelligenceHttpClient
from ..types import SourceDocument, utc_iso
from .base import FetchBatch


_DATE_PATTERNS = (
    re.compile(
        r"(?P<year>(?:19|20)\d{2})[-/.年]"
        r"(?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})"
    ),
    re.compile(
        r"(?P<year>(?:19|20)\d{2})(?P<month>\d{2})(?P<day>\d{2})"
    ),
)
TUSHARE_ANNOUNCEMENT_FIELDS = "ann_date,ts_code,name,title,url,rec_time"


def _provider_text(value: object) -> str:
    if value is None or not pd.notna(value):
        return ""
    return " ".join(str(value).split())


def _published_at(text: str, fallback: str) -> str:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            parsed = datetime(
                int(match.group("year")), int(match.group("month")), int(match.group("day")),
                0, 0, tzinfo=timezone.utc,
            )
            return utc_iso(parsed)
    return utc_iso(fallback)


def _detail_published_at(soup: BeautifulSoup, url: str, listing_context: str) -> str | None:
    for name in (
        "firstpublishedtime", "publishdate", "pubdate", "article:published_time",
        "date", "createdate",
    ):
        tag = soup.find("meta", attrs={"name": re.compile(f"^{re.escape(name)}$", re.I)})
        if tag is None:
            tag = soup.find("meta", attrs={"property": re.compile(f"^{re.escape(name)}$", re.I)})
        content = str(tag.get("content") or "") if tag else ""
        if any(pattern.search(content) for pattern in _DATE_PATTERNS):
            return _published_at(content, content)
    candidates = [listing_context]
    candidates.extend(
        node.get_text(" ", strip=True)
        for node in soup.select(
            "time, .pages-date, [class*='publish'], [class*='date'], [class*='time'], "
            "[id*='publish'], [id*='date'], [id*='time']"
        )[:20]
    )
    for candidate in candidates:
        labelled = re.search(
            r"(?:发布时间|发布日期|成文日期|发文日期|时间)?\s*[:：]?\s*"
            r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2})",
            candidate,
        )
        if labelled:
            return _published_at(labelled.group(1), labelled.group(1))
    path_date = re.search(r"/(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})(?:/|_|\.)", url)
    if path_date:
        return utc_iso(f"{path_date.group(1)}-{path_date.group(2)}-{path_date.group(3)}T00:00:00Z")
    return None


def _article_content(soup: BeautifulSoup) -> bytes:
    selectors = (
        "article", "#UCAP-CONTENT", ".TRS_Editor", ".article-content",
        ".pages_content", ".content", "main",
    )
    selected = next((soup.select_one(selector) for selector in selectors if soup.select_one(selector)), None)
    return str(selected or soup.body or soup).encode("utf-8")


class OfficialHtmlAdapter:
    def __init__(
        self,
        source: str,
        listing_urls: tuple[str, ...],
        client: IntelligenceHttpClient,
        *,
        include_path: str = r".",
        min_title_length: int = 8,
    ) -> None:
        self.source = source
        self.listing_urls = listing_urls
        self.client = client
        self.include_path = re.compile(include_path)
        self.min_title_length = min_title_length

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        since = cursor or "1970-01-01T00:00:00+00:00"
        seen_at = utc_iso()
        documents: list[SourceDocument] = []
        warnings: list[str] = []
        seen_urls: set[str] = set()
        for listing_url in self.listing_urls:
            try:
                response = self.client.get(listing_url)
                soup = BeautifulSoup(response.content, "html.parser")
            except Exception as exc:  # noqa: BLE001 - one listing must not erase others
                warnings.append(f"{listing_url}:{type(exc).__name__}")
                continue
            for anchor in soup.find_all("a", href=True):
                title = " ".join(anchor.get_text(" ", strip=True).split())
                url = urljoin(listing_url, str(anchor.get("href")))
                if len(title) < self.min_title_length or not self.include_path.search(url):
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                try:
                    detail = self.client.get(url)
                    requested_path = urlparse(url).path.rstrip("/")
                    final_path = urlparse(str(getattr(detail, "url", url))).path.rstrip("/")
                    if requested_path and final_path in {"", "/csrc"}:
                        warnings.append(f"detail_redirected_to_home:{url}")
                        continue
                    detail_soup = BeautifulSoup(detail.content, "html.parser")
                    context = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
                    published = _detail_published_at(detail_soup, url, context)
                    if published is None:
                        warnings.append(f"publish_time_missing:{url}")
                        continue
                    content = _article_content(detail_soup)
                    mime_type = detail.headers.get("Content-Type", "text/html").split(";", 1)[0]
                except Exception:
                    warnings.append(f"detail_failed:{url}")
                    continue
                if published <= utc_iso(since) or published > utc_iso(until):
                    continue
                documents.append(
                    SourceDocument(
                        source=self.source,
                        source_id=hashlib.sha256(url.encode("utf-8")).hexdigest()[:24],
                        title=title,
                        published_at=published,
                        first_seen_at=seen_at,
                        effective_at=published,
                        source_url=url,
                        content=content,
                        mime_type=mime_type,
                        metadata={"listing_url": listing_url},
                    )
                )
        return FetchBatch(tuple(documents), utc_iso(until), warnings=tuple(warnings))


class NdrcApiAdapter:
    """NDRC official search API, used for deterministic historical policy backfills."""

    source = "ndrc_policy"

    def __init__(
        self,
        client: IntelligenceHttpClient,
        *,
        endpoint: str,
        site_code: str,
        api_key: str,
        page_size: int = 50,
        max_pages: int = 100,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.site_code = site_code
        self.api_key = api_key
        self.page_size = max(1, min(int(page_size), 100))
        self.max_pages = max(1, int(max_pages))

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        start = pd.Timestamp(cursor or "1970-01-01")
        end = pd.Timestamp(until)
        seen_at = utc_iso()
        documents: list[SourceDocument] = []
        warnings: list[str] = []
        total_hits: int | None = None
        for page in range(1, self.max_pages + 1):
            query = urlencode({
                "qt": "", "tab": "all", "page": page, "pageSize": self.page_size,
                "siteCode": self.site_code, "key": self.api_key,
                "startDateStr": start.strftime("%Y-%m-%d"),
                "endDateStr": end.strftime("%Y-%m-%d"),
                "timeOption": 2, "sort": "dateDesc",
            })
            response = self.client.get(f"{self.endpoint}?{query}")
            payload = response.json()
            if int(payload.get("code") or 0) != 200:
                raise RuntimeError(f"ndrc_api_error:{payload.get('code')}:{payload.get('msg', '')}")
            data = payload.get("data") or {}
            rows = data.get("resultList") or []
            total_hits = int(data.get("totalHits") or len(rows))
            if not rows:
                break
            for row in rows:
                published = _published_at(str(row.get("docDate") or ""), seen_at)
                if published <= utc_iso(cursor or "1970-01-01T00:00:00Z") or published > utc_iso(until):
                    continue
                url = str(row.get("url") or "")
                values = row.get("myValues") or {}
                content = str(
                    values.get("QUICKDESCRIPTION") or row.get("summary") or row.get("title") or ""
                )
                source_id = str(row.get("reference") or "") or hashlib.sha256(
                    f"{url}|{published}".encode("utf-8")
                ).hexdigest()[:24]
                documents.append(SourceDocument(
                    source=self.source,
                    source_id=source_id,
                    title=" ".join(str(row.get("title") or "").split()),
                    published_at=published,
                    first_seen_at=seen_at,
                    effective_at=published,
                    source_url=url,
                    content=content.encode("utf-8"),
                    mime_type="text/plain",
                    metadata={
                        "reference": row.get("reference"),
                        "doc_type": row.get("docType"),
                        "source_name": row.get("source"),
                    },
                ))
            if page * self.page_size >= total_hits:
                break
        else:
            warnings.append(f"pagination_truncated:{total_hits or 'unknown'}")
        return FetchBatch(tuple(documents), utc_iso(until), warnings=tuple(warnings))


class TushareAnnouncementAdapter:
    source = "tushare_announcement"

    def __init__(
        self,
        client,
        *,
        enabled: bool,
        initial_lookback_days: int = 7,
        page_size: int = 2000,
        max_pages_per_day: int = 20,
    ) -> None:
        self.client = client
        self.enabled = enabled
        self.initial_lookback_days = max(1, int(initial_lookback_days))
        self.page_size = max(1, min(int(page_size), 2000))
        self.max_pages_per_day = max(1, int(max_pages_per_day))

    def fetch_since(self, cursor: str, until: str) -> FetchBatch:
        if not self.enabled:
            return FetchBatch((), cursor, warnings=("source_unavailable:entitlement_disabled",))
        end = _china_timestamp(until)
        start = (
            _china_timestamp(cursor)
            if cursor
            else end.normalize() - pd.Timedelta(days=self.initial_lookback_days - 1)
        )
        if start > end:
            return FetchBatch((), utc_iso(until))

        seen_at = utc_iso()
        warnings: list[str] = []
        frames: list[pd.DataFrame] = []
        for day in pd.date_range(start.normalize(), end.normalize(), freq="D"):
            date_key = day.strftime("%Y%m%d")
            frame = self.client.anns_d(
                ann_date=date_key,
                limit=self.page_size,
                offset=0,
                fields=TUSHARE_ANNOUNCEMENT_FIELDS,
            )
            if not isinstance(frame, pd.DataFrame):
                raise TypeError("intelligence_tushare_invalid_response")
            if frame.empty:
                continue
            frames.append(frame)
            if len(frame) >= self.page_size:
                warnings.append(f"day_saturated:{date_key}")

        if frames:
            page_result = self._documents_from_frame(
                pd.concat(frames, ignore_index=True),
                seen_at=seen_at,
                fallback_date=end.strftime("%Y%m%d"),
                ingestion_mode="live",
            )
            documents = page_result.documents
            fetched_rows = page_result.fetched
            b_share_filtered = page_result.b_share_filtered
        else:
            documents = ()
            fetched_rows = 0
            b_share_filtered = 0
        complete = not warnings
        return FetchBatch(
            tuple(documents),
            utc_iso(until) if complete else cursor,
            complete=complete,
            warnings=tuple(warnings),
            metrics={
                "fetched_rows": fetched_rows,
                "b_share_filtered": b_share_filtered,
            },
        )

    def fetch_range_page(
        self,
        *,
        start_date: str,
        end_date: str,
        offset: int,
        seen_at: str | None = None,
    ) -> "TushareAnnouncementPage":
        if not self.enabled:
            raise RuntimeError(
                "intelligence_tushare_unavailable:entitlement_disabled"
            )
        frame = self.client.anns_d(
            start_date=start_date,
            end_date=end_date,
            limit=self.page_size,
            offset=max(0, int(offset)),
            fields=TUSHARE_ANNOUNCEMENT_FIELDS,
        )
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("intelligence_tushare_invalid_response")
        return self._documents_from_frame(
            frame,
            seen_at=seen_at or utc_iso(),
            fallback_date=start_date,
            ingestion_mode="history",
        )

    def fetch_security_day_page(
        self,
        *,
        ann_date: str,
        ts_code: str,
        offset: int,
        seen_at: str | None = None,
    ) -> "TushareAnnouncementPage":
        if not self.enabled:
            raise RuntimeError(
                "intelligence_tushare_unavailable:entitlement_disabled"
            )
        frame = self.client.anns_d(
            ann_date=ann_date,
            ts_code=ts_code,
            limit=self.page_size,
            offset=max(0, int(offset)),
            fields=TUSHARE_ANNOUNCEMENT_FIELDS,
        )
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("intelligence_tushare_invalid_response")
        return self._documents_from_frame(
            frame,
            seen_at=seen_at or utc_iso(),
            fallback_date=ann_date,
            ingestion_mode="history",
        )

    def _documents_from_frame(
        self,
        frame: pd.DataFrame,
        *,
        seen_at: str,
        fallback_date: str,
        seen_source_ids: set[str] | None = None,
        ingestion_mode: str = "live",
    ) -> "TushareAnnouncementPage":
        documents: list[SourceDocument] = []
        security_pairs: list[tuple[str, str]] = []
        b_share_filtered = 0
        seen_ids = seen_source_ids if seen_source_ids is not None else set()
        groups: dict[str, list[dict]] = {}
        for row in frame.to_dict(orient="records"):
            if _is_b_share_code(row.get("ts_code")):
                b_share_filtered += 1
                continue
            url = (
                _provider_text(row.get("url"))
                or _provider_text(row.get("pdf_url"))
            )
            source_id = _announcement_source_id(row, url)
            groups.setdefault(source_id, []).append(row)

        for source_id in sorted(groups):
            rows = groups[source_id]
            link_names: dict[str, set[str]] = {}
            for row in rows:
                code = _provider_text(
                    row.get("ts_code")
                ).upper()
                if not code:
                    continue
                name = _provider_text(row.get("name"))
                link_names.setdefault(code, set()).add(name)
                security_pairs.append((source_id, code))
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            security_codes = sorted(link_names)
            security_links = [
                {
                    "ts_code": code,
                    "name": sorted(
                        name
                        for name in link_names[code]
                        if name
                    )[0] if any(link_names[code]) else "",
                    "provenance": "tushare_anns_d",
                }
                for code in security_codes
            ]
            titles = sorted({
                _provider_text(row.get("title"))
                for row in rows
                if _provider_text(row.get("title"))
            })
            urls = sorted({
                _normalized_announcement_url(
                    _provider_text(row.get("url"))
                    or _provider_text(row.get("pdf_url"))
                )
                for row in rows
                if (
                    _provider_text(row.get("url"))
                    or _provider_text(row.get("pdf_url"))
                )
            })
            ann_dates = sorted({
                re.sub(
                    r"\D",
                    "",
                    _provider_text(row.get("ann_date"))
                    or fallback_date,
                )
                for row in rows
                if re.sub(
                    r"\D",
                    "",
                    _provider_text(row.get("ann_date"))
                    or fallback_date,
                )
            })
            rec_times = sorted({
                _provider_text(row.get("rec_time"))
                for row in rows
                if _provider_text(row.get("rec_time"))
            })
            title = titles[0] if titles else ""
            url = urls[0] if urls else ""
            ann_date = ann_dates[0] if ann_dates else fallback_date
            rec_time = rec_times[0] if rec_times else ""
            canonical_row = {
                "ts_code": security_codes[0] if security_codes else "",
                "name": (
                    security_links[0]["name"]
                    if security_links
                    else ""
                ),
                "title": title,
                "url": url,
                "ann_date": ann_date,
                "rec_time": rec_time,
            }
            published = _announcement_row_published_at(
                canonical_row,
                fallback_date=fallback_date,
                fallback_seen_at=seen_at,
            )
            metadata = {
                **canonical_row,
                "announcement_id": source_id,
                "content_scope": "title_metadata",
                "provider": "tushare",
                "ingestion_mode": ingestion_mode,
                "security_codes": security_codes,
                "security_links": security_links,
            }
            documents.append(
                SourceDocument(
                    source=self.source,
                    source_id=source_id,
                    title=title,
                    published_at=published,
                    first_seen_at=seen_at,
                    effective_at=published,
                    source_url=url,
                    content=(
                        f"tushare_announcement|{source_id}|{url}"
                    ).encode("utf-8"),
                    mime_type="text/plain",
                    metadata=metadata,
                )
            )
        return TushareAnnouncementPage(
            documents=tuple(documents),
            fetched=len(frame),
            b_share_filtered=b_share_filtered,
            security_pairs=tuple(sorted(set(security_pairs))),
        )


@dataclass(frozen=True)
class TushareAnnouncementPage:
    documents: tuple[SourceDocument, ...]
    fetched: int
    b_share_filtered: int
    security_pairs: tuple[tuple[str, str], ...]


def _china_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("Asia/Shanghai")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("Asia/Shanghai")
    )


def _announcement_published_at(text: str, fallback: str) -> str:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            parsed = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
            return utc_iso(parsed)
    return utc_iso(fallback)


def announcement_rec_time(value) -> str | None:
    if value is None or not pd.notna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    has_clock = bool(re.search(r"\d{1,2}:\d{2}", text))
    compact_timestamp = bool(re.fullmatch(r"\d{14}", text))
    if not has_clock and not compact_timestamp:
        return None
    try:
        timestamp = (
            pd.Timestamp(datetime.strptime(text, "%Y%m%d%H%M%S"))
            if compact_timestamp
            else pd.Timestamp(text)
        )
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    timestamp = (
        timestamp.tz_localize("Asia/Shanghai")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("Asia/Shanghai")
    )
    return utc_iso(timestamp.to_pydatetime())


def _announcement_row_published_at(
    row: dict,
    *,
    fallback_date: str,
    fallback_seen_at: str,
) -> str:
    precise = announcement_rec_time(row.get("rec_time"))
    if precise:
        return precise
    return _announcement_published_at(
        str(row.get("ann_date") or fallback_date),
        fallback_seen_at,
    )


def _announcement_source_id(row: dict, url: str) -> str:
    for key in ("ann_id", "id"):
        value = row.get(key)
        if value is not None and pd.notna(value) and str(value):
            return str(value)
    announcement_id = (parse_qs(urlparse(url).query).get("announcementId") or [""])[0]
    if announcement_id:
        return str(announcement_id)
    normalized_url = _normalized_announcement_url(url)
    if normalized_url:
        identity = f"url|{normalized_url}"
    else:
        normalized_title = " ".join(
            str(row.get("title") or "").split()
        ).casefold()
        ann_date = re.sub(
            r"\D",
            "",
            str(row.get("ann_date") or ""),
        )
        identity = (
            f"metadata|{str(row.get('ts_code') or '').upper()}|"
            f"{normalized_title}|{ann_date}"
        )
    return hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:24]


def _normalized_announcement_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    if not parsed.scheme or not parsed.hostname:
        return text
    hostname = parsed.hostname.casefold()
    port = parsed.port
    default_port = (
        parsed.scheme.casefold() == "https" and port == 443
    ) or (
        parsed.scheme.casefold() == "http" and port == 80
    )
    authority = hostname
    if port is not None and not default_port:
        authority = f"{authority}:{port}"
    normalized_query = urlencode(
        sorted(parse_qsl(parsed.query, keep_blank_values=True)),
        doseq=True,
    )
    return urlunsplit((
        parsed.scheme.casefold(),
        authority,
        parsed.path or "/",
        normalized_query,
        "",
    ))


def _announcement_metadata(row: dict) -> dict:
    metadata = {}
    for key, value in row.items():
        if value is None or not pd.notna(value):
            continue
        if isinstance(value, pd.Timestamp):
            value = value.isoformat()
        elif hasattr(value, "item"):
            value = value.item()
        metadata[str(key)] = value
    return metadata


def _is_b_share_code(value) -> bool:
    code = re.sub(r"\D", "", str(value or ""))
    return code.startswith(("200", "900"))
