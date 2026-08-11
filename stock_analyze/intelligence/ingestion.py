"""Resumable ingestion and extraction orchestration."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .backfill import (
    FUND_BASIC_STATUSES,
    AnnouncementBackfill,
    TushareTradingCalendarResolver,
)
from .entities import load_entity_resolver
from .extraction import RuleEventExtractor
from .source_registry import build_adapters, load_source_config
from .sources.official import TushareAnnouncementAdapter
from .store import IntelligenceStore
from .tushare_transport import TushareProTransport
from .types import utc_iso


def _batch_status(*, complete: bool, warnings: tuple[str, ...]) -> str:
    normalized_warnings = tuple(str(warning).lower() for warning in warnings)
    if any(
        warning.startswith("source_unavailable:") or "entitlement_disabled" in warning
        for warning in normalized_warnings
    ):
        return "unavailable"
    if not complete or warnings:
        return "degraded"
    return "success"


def _saturated_days(
    warnings: tuple[str, ...],
) -> tuple[date, ...]:
    days: set[date] = set()
    for warning in warnings:
        value = str(warning)
        if not value.startswith("day_saturated:"):
            continue
        raw_day = value.split(":", 1)[1]
        try:
            days.add(datetime.strptime(raw_day, "%Y%m%d").date())
        except ValueError:
            continue
    return tuple(sorted(days))


def _shanghai_day(value: str) -> date:
    parsed = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(
        ZoneInfo("Asia/Shanghai")
    ).date()


class IntelligencePipeline:
    def __init__(self, repo_root: str | Path, config_path: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = Path(config_path or self.repo_root / "configs" / "intelligence_sources.yaml")
        self.store = IntelligenceStore(self.repo_root / "data" / "shared" / "intelligence")

    def ingest(
        self,
        *,
        until: str,
        sources: set[str] | None = None,
        since: str | None = None,
    ) -> dict:
        adapters = build_adapters(self.repo_root, self.config_path)
        results: list[dict] = []
        for adapter in adapters:
            if sources and adapter.source not in sources:
                continue
            run_id = uuid.uuid4().hex
            provisional_retry_day = None
            if adapter.source == "tushare_announcement":
                provisional_retry_day = (
                    _shanghai_day(until)
                    - timedelta(
                        days=max(
                            1,
                            int(
                                getattr(
                                    adapter,
                                    "initial_lookback_days",
                                    1,
                                )
                            ),
                        )
                        - 1
                    )
                ).isoformat()
            claim = self.store.start_run(
                run_id,
                adapter.source,
                since,
                owner=run_id,
                provisional_retry_day=provisional_retry_day,
            )
            cursor = str(claim["cursor"])
            claimed_retry_day = str(
                claim.get("retry_unresolved_day") or ""
            )
            try:
                batch = adapter.fetch_since(cursor, until)
                inserted = sum(self.store.insert_document(item)[1] for item in batch.documents)
                status = _batch_status(complete=batch.complete, warnings=batch.warnings)
                saturated_days = _saturated_days(batch.warnings)
                retry_day = (
                    saturated_days[0].isoformat()
                    if saturated_days
                    else None
                )
                retry_window_scanned = bool(
                    claimed_retry_day
                    and _shanghai_day(until)
                    >= date.fromisoformat(claimed_retry_day)
                )
                self.store.finish_run(
                    run_id, status=status,
                    cursor=batch.cursor, fetched=len(batch.documents), inserted=inserted,
                    error=" | ".join(batch.warnings),
                    retry_unresolved_day=retry_day,
                    retry_reason=" | ".join(
                        warning
                        for warning in batch.warnings
                        if str(warning).startswith("day_saturated:")
                    ),
                    retry_window_scanned=retry_window_scanned,
                    retry_covered_floor=(
                        claimed_retry_day
                        if retry_window_scanned
                        else None
                    ),
                    generation=int(claim["generation"]),
                    owner=run_id,
                )
                results.append({
                    "source": adapter.source, "status": status,
                    "fetched": len(batch.documents), "inserted": inserted,
                    "warnings": list(batch.warnings),
                })
            except Exception as exc:  # noqa: BLE001
                self.store.finish_run(
                    run_id,
                    status="failed",
                    error=f"{type(exc).__name__}:{exc}",
                    generation=int(claim["generation"]),
                    owner=run_id,
                )
                results.append({"source": adapter.source, "status": "failed", "error": str(exc)[:200]})
        return {"status": "complete", "until": utc_iso(until), "sources": results}

    def extract(self, *, limit: int = 500) -> dict:
        resolver = load_entity_resolver(self.repo_root)
        known_fingerprints = self.store.known_fingerprints()
        documents = self.store.pending_documents(limit)
        inserted = 0
        no_event = 0
        failed = 0
        for row in documents:
            try:
                extractor = RuleEventExtractor(
                    resolver,
                    prior_fingerprints=known_fingerprints,
                )
                document_row = dict(row)
                try:
                    metadata = json.loads(
                        str(document_row.get("metadata_json") or "{}")
                    )
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                links = self.store.document_security_links(
                    int(row["id"])
                )
                if links:
                    metadata["security_codes"] = sorted({
                        str(link["ts_code"])
                        for link in links
                    })
                    metadata["security_links"] = [
                        {
                            "ts_code": str(link["ts_code"]),
                            "name": str(link["name"]),
                            "provenance": str(link["provenance"]),
                        }
                        for link in links
                    ]
                    document_row["metadata_json"] = json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                events = extractor.extract(
                    int(row["id"]),
                    document_row,
                    self.store.document_content(row),
                )
                inserted += sum(self.store.insert_event(event) for event in events)
                for event in events:
                    known_fingerprints.update(
                        value
                        for value in (
                            event.document_fingerprint,
                            event.event_fingerprint,
                        )
                        if value
                    )
                self.store.mark_document(int(row["id"]), "processed" if events else "no_event")
                no_event += int(not events)
            except Exception:  # noqa: BLE001
                self.store.mark_document(int(row["id"]), "parse_failed")
                failed += 1
        return {
            "status": "complete", "documents": len(documents),
            "events_inserted": inserted, "no_event": no_event, "failed": failed,
        }

    def extract_semantic(
        self,
        processor,
        *,
        document_ids: tuple[int, ...] | list[int] | None = None,
        limit: int = 500,
    ) -> dict[str, object]:
        """Run the independent semantic lane over parsed artifacts."""

        selected_ids = (
            [int(document_id) for document_id in document_ids]
            if document_ids is not None
            else self.store.semantic_ready_document_ids(limit=limit)
        )
        results = [
            processor.process_document(document_id)
            for document_id in selected_ids
        ]
        status_counts: dict[str, int] = {}
        reused = 0
        for result in results:
            status = str(getattr(result, "status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1
            reused += int(bool(getattr(result, "reused", False)))
        return {
            "status": "complete",
            "documents": len(selected_ids),
            "reused": reused,
            "statuses": status_counts,
        }

    def backfill(
        self,
        *,
        source: str,
        start_date: str,
        end_date: str,
        max_partitions: int,
        resume: bool,
    ) -> dict[str, object]:
        if source != "tushare_announcement":
            raise ValueError(
                f"intelligence_backfill_source_unsupported:{source}"
            )
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        config = load_source_config(self.config_path)
        spec = (config.get("sources") or {}).get(source) or {}
        if not spec.get("enabled") or not spec.get("entitled"):
            raise RuntimeError(
                "intelligence_tushare_unavailable:entitlement_disabled"
            )
        earliest = date.fromisoformat(
            str(spec.get("full_history_start") or "1990-12-19")
        )
        if start < earliest:
            raise ValueError(
                "intelligence_backfill_before_supported_history:"
                f"{start.isoformat()}<{earliest.isoformat()}"
            )
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("intelligence_tushare_token_missing")

        client = TushareProTransport(
            token,
            endpoint=str(
                spec.get("endpoint")
                or "https://api.tushare.pro"
            ),
        )
        if (
            str(spec.get("trade_calendar_mode") or "")
            != "full_natural_days"
        ):
            raise ValueError(
                "intelligence_trade_calendar_mode_invalid"
            )
        calendar_cache = Path(
            str(
                spec.get("trade_calendar_cache")
                or "reference/tushare_sse_trade_calendar.csv"
            )
        )
        if (
            calendar_cache.is_absolute()
            or ".." in calendar_cache.parts
        ):
            raise ValueError(
                "intelligence_trade_calendar_cache_path_invalid"
            )
        intelligence_root = (
            self.repo_root
            / "data"
            / "shared"
            / "intelligence"
        )
        calendar = TushareTradingCalendarResolver.from_tushare(
            client,
            start_date=start,
            end_date=end + timedelta(
                days=int(
                    spec.get(
                        "trade_calendar_boundary_buffer_days"
                    )
                    or 45
                )
            ),
            cache_path=intelligence_root / calendar_cache,
            max_next_open_gap_days=int(
                spec.get(
                    "trade_calendar_max_next_open_gap_days"
                )
                or 45
            ),
        )
        self.store = IntelligenceStore(
            intelligence_root,
            next_market_open_resolver=calendar,
        )
        adapter = TushareAnnouncementAdapter(
            client,
            enabled=True,
            initial_lookback_days=int(
                spec.get("initial_lookback_days") or 7
            ),
            page_size=int(spec.get("page_size") or 2000),
            max_pages_per_day=int(
                spec.get("max_pages_per_day") or 20
            ),
        )
        fund_market = str(
            spec.get("fund_basic_market") or "E"
        ).strip().upper()
        configured_fund_statuses = spec.get(
            "fund_basic_statuses",
            list(FUND_BASIC_STATUSES),
        )
        if not isinstance(configured_fund_statuses, list):
            raise ValueError(
                "intelligence_fund_basic_statuses_invalid"
            )
        fund_statuses = tuple(
            str(status).strip().upper()
            for status in configured_fund_statuses
        )
        fund_include_unfiltered = spec.get(
            "fund_basic_include_unfiltered",
            True,
        )
        if fund_include_unfiltered is not True:
            raise ValueError(
                "intelligence_fund_basic_unfiltered_required"
            )
        coordinator = AnnouncementBackfill(
            store=self.store,
            adapter=adapter,
            sensitive_values=(token,),
            universe_page_size=int(
                spec.get("stock_basic_page_size") or 6000
            ),
            fund_universe_page_size=int(
                spec.get("fund_basic_page_size") or 15000
            ),
            fund_market=fund_market,
            fund_statuses=fund_statuses,
            fund_include_unfiltered=fund_include_unfiltered,
            verification_rounds=max(
                5,
                int(
                    spec.get(
                        "backfill_verification_rounds"
                    )
                    or 5
                ),
            ),
            lease_seconds=int(
                spec.get("backfill_lease_seconds") or 300
            ),
        )
        return coordinator.run(
            start_date=start,
            end_date=end,
            max_partitions=max_partitions,
            resume=resume,
        )
