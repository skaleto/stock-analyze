"""Fail-closed Tushare announcement history backfill."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from .sources.official import (
    TushareAnnouncementAdapter,
    announcement_rec_time,
)
from .store import (
    BackfillConfigurationConflict,
    BackfillDocumentWrite,
    BackfillGenerationConflict,
    BackfillLeaseBusy,
    BackfillUniverseMember,
    BACKFILL_COMPLETION_STRATEGY_VERSION,
    IntelligenceStore,
)
from .tushare_transport import (
    TushareRetryableError,
    TushareTerminalError,
)
from .types import SourceDocument, utc_iso


STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,list_date,delist_date,list_status"
)
STOCK_BASIC_STATUSES = ("L", "D", "P", "G")
STOCK_BASIC_MAX_ROWS = 6000
FUND_BASIC_FIELDS = "ts_code,list_date,delist_date,status"
FUND_BASIC_STATUSES = ("L", "D", "I")
FUND_BASIC_MAX_ROWS = 15000


@dataclass(frozen=True)
class HistoryPartition:
    start_date: date
    end_date: date
    depth: int = 0

    @property
    def start_key(self) -> str:
        return self.start_date.isoformat()

    @property
    def end_key(self) -> str:
        return self.end_date.isoformat()

    @property
    def api_start(self) -> str:
        return self.start_date.strftime("%Y%m%d")

    @property
    def api_end(self) -> str:
        return self.end_date.strftime("%Y%m%d")

    @property
    def is_day(self) -> bool:
        return self.start_date == self.end_date

    def split(self) -> tuple["HistoryPartition", "HistoryPartition"]:
        if self.is_day:
            raise ValueError(
                "intelligence_backfill_daily_partition_unsplittable"
            )
        midpoint = self.start_date + (
            (self.end_date - self.start_date) // 2
        )
        return (
            HistoryPartition(
                self.start_date,
                midpoint,
                self.depth + 1,
            ),
            HistoryPartition(
                midpoint + timedelta(days=1),
                self.end_date,
                self.depth + 1,
            ),
        )


class TushareTradingCalendarResolver:
    """Resolve date-only disclosures against a verified SSE calendar."""

    def __init__(
        self,
        open_dates: Iterable[date],
        *,
        coverage_start: date | None = None,
        coverage_end: date | None = None,
        max_next_open_gap_days: int = 45,
    ) -> None:
        normalized = tuple(sorted(set(open_dates)))
        if not normalized:
            raise ValueError("intelligence_tushare_trade_cal_empty")
        self._open_dates = normalized
        self.coverage_start = coverage_start or normalized[0]
        self.coverage_end = coverage_end or normalized[-1]
        self.max_next_open_gap_days = max(
            1,
            int(max_next_open_gap_days),
        )
        if self.coverage_start > self.coverage_end:
            raise ValueError(
                "intelligence_tushare_trade_cal_range_invalid"
            )
        if (
            normalized[0] < self.coverage_start
            or normalized[-1] > self.coverage_end
        ):
            raise ValueError(
                "intelligence_tushare_trade_cal_open_out_of_coverage"
            )
        open_boundaries = (
            (self.coverage_start, normalized[0]),
            *zip(normalized, normalized[1:]),
        )
        for previous_open, next_open in open_boundaries:
            if (
                next_open - previous_open
            ).days > self.max_next_open_gap_days:
                raise ValueError(
                    "intelligence_tushare_trade_cal_"
                    "next_open_gap_too_large:"
                    f"{previous_open.isoformat()}:{next_open.isoformat()}"
                )

    @classmethod
    def from_tushare(
        cls,
        client,
        *,
        start_date: date,
        end_date: date,
        cache_path: str | Path | None = None,
        max_next_open_gap_days: int = 45,
    ) -> "TushareTradingCalendarResolver":
        if start_date > end_date:
            raise ValueError(
                "intelligence_tushare_trade_cal_range_invalid"
            )
        cache = Path(cache_path) if cache_path is not None else None
        calendar: dict[date, int] = {}
        if cache is not None and cache.exists():
            try:
                cached_frame = pd.read_csv(
                    cache,
                    dtype={"cal_date": str, "is_open": str},
                )
            except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
                raise ValueError(
                    "intelligence_tushare_trade_cal_cache_invalid"
                ) from exc
            calendar.update(
                _validated_calendar_rows(
                    cached_frame,
                    source="cache",
                )
            )

        missing_ranges = _calendar_missing_ranges(
            calendar,
            start_date,
            end_date,
        )
        for chunk_start, chunk_end in missing_ranges:
            frame = client.trade_cal(
                exchange="SSE",
                start_date=chunk_start.strftime("%Y%m%d"),
                end_date=chunk_end.strftime("%Y%m%d"),
                fields="cal_date,is_open",
            )
            if not isinstance(frame, pd.DataFrame):
                raise TypeError(
                    "intelligence_tushare_trade_cal_invalid_response"
                )
            if frame.empty:
                raise ValueError(
                    "intelligence_tushare_trade_cal_chunk_empty:"
                    f"{chunk_start.year}"
                )
            calendar.update(
                _validated_calendar_rows(
                    frame,
                    source=f"provider:{chunk_start.year}",
                    expected_start=chunk_start,
                    expected_end=chunk_end,
                )
            )

        for expected in _date_range(start_date, end_date):
            if expected not in calendar:
                raise ValueError(
                    "intelligence_tushare_trade_cal_natural_day_gap:"
                    f"{expected.strftime('%Y%m%d')}"
                )
        if cache is not None and missing_ranges:
            _write_calendar_cache(cache, calendar)

        normalized_open_dates = tuple(
            calendar_date
            for calendar_date in _date_range(start_date, end_date)
            if calendar[calendar_date] == 1
        )
        return cls(
            normalized_open_dates,
            coverage_start=start_date,
            coverage_end=end_date,
            max_next_open_gap_days=max_next_open_gap_days,
        )

    def __call__(self, published_at: str) -> str:
        published = pd.Timestamp(published_at)
        published = (
            published.tz_localize("Asia/Shanghai")
            if published.tzinfo is None
            else published.tz_convert("Asia/Shanghai")
        )
        published_date = published.date()
        if not self.coverage_start <= published_date <= self.coverage_end:
            raise ValueError(
                "intelligence_tushare_trade_cal_published_out_of_coverage:"
                f"{published_date.isoformat()}"
            )
        next_open = next(
            (
                open_date
                for open_date in self._open_dates
                if open_date > published_date
            ),
            None,
        )
        if next_open is None:
            raise ValueError(
                "intelligence_tushare_trade_cal_next_open_missing:"
                f"{published_date.isoformat()}"
            )
        market_open = datetime.combine(
            next_open,
            time(hour=9, minute=30),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        return utc_iso(market_open)


def _date_range(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _calendar_missing_ranges(
    calendar: dict[date, int],
    start_date: date,
    end_date: date,
) -> tuple[tuple[date, date], ...]:
    ranges: list[tuple[date, date]] = []
    range_start: date | None = None
    previous: date | None = None
    for current in _date_range(start_date, end_date):
        is_missing = current not in calendar
        must_split = (
            range_start is not None
            and previous is not None
            and current.year != previous.year
        )
        if must_split:
            ranges.append((range_start, previous))
            range_start = None
        if is_missing and range_start is None:
            range_start = current
        elif not is_missing and range_start is not None:
            ranges.append((range_start, previous or range_start))
            range_start = None
        previous = current
    if range_start is not None:
        ranges.append((range_start, previous or range_start))
    return tuple(ranges)


def _validated_calendar_rows(
    frame: pd.DataFrame,
    *,
    source: str,
    expected_start: date | None = None,
    expected_end: date | None = None,
) -> dict[date, int]:
    if not {"cal_date", "is_open"}.issubset(frame.columns):
        raise ValueError(
            "intelligence_tushare_trade_cal_columns_invalid"
        )
    result: dict[date, int] = {}
    for row in frame.to_dict(orient="records"):
        value = str(row.get("cal_date") or "").strip()
        try:
            calendar_date = datetime.strptime(
                value,
                "%Y%m%d",
            ).date()
        except ValueError as exc:
            raise ValueError(
                "intelligence_tushare_trade_cal_date_invalid:"
                f"{source}"
            ) from exc
        if calendar_date in result:
            raise ValueError(
                "intelligence_tushare_trade_cal_duplicate_date:"
                f"{value}"
            )
        if (
            expected_start is not None
            and expected_end is not None
            and not expected_start <= calendar_date <= expected_end
        ):
            raise ValueError(
                "intelligence_tushare_trade_cal_out_of_range:"
                f"{value}"
            )
        status = str(row.get("is_open")).strip().casefold()
        if status in {"1", "1.0", "true"}:
            is_open = 1
        elif status in {"0", "0.0", "false"}:
            is_open = 0
        else:
            raise ValueError(
                "intelligence_tushare_trade_cal_is_open_invalid:"
                f"{value}"
            )
        result[calendar_date] = is_open
    if expected_start is not None and expected_end is not None:
        for expected in _date_range(expected_start, expected_end):
            if expected not in result:
                raise ValueError(
                    "intelligence_tushare_trade_cal_natural_day_gap:"
                    f"{expected.strftime('%Y%m%d')}"
                )
    return result


def _write_calendar_cache(
    cache_path: Path,
    calendar: dict[date, int],
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=cache_path.parent,
        prefix=f".{cache_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    handle.close()
    try:
        pd.DataFrame([
            {
                "cal_date": calendar_date.strftime("%Y%m%d"),
                "is_open": calendar[calendar_date],
            }
            for calendar_date in sorted(calendar)
        ]).to_csv(temporary_path, index=False)
        os.replace(temporary_path, cache_path)
    finally:
        temporary_path.unlink(missing_ok=True)


@dataclass
class _RunState:
    remaining: int
    resume: bool
    job_id: str
    job_generation: int
    fetched: int = 0
    inserted: int = 0
    b_share_filtered: int = 0
    touched: set[tuple[str, str]] = field(default_factory=set)
    supplier_circuit_open: bool = False


class AnnouncementBackfill:
    source = "tushare_announcement"

    def __init__(
        self,
        *,
        store: IntelligenceStore,
        adapter: TushareAnnouncementAdapter,
        sensitive_values: Iterable[str] = (),
        universe_page_size: int = STOCK_BASIC_MAX_ROWS,
        fund_universe_page_size: int = FUND_BASIC_MAX_ROWS,
        fund_market: str = "E",
        fund_statuses: Iterable[str] = FUND_BASIC_STATUSES,
        fund_include_unfiltered: bool = True,
        verification_rounds: int = 5,
        lease_seconds: int = 300,
    ) -> None:
        if adapter.source != self.source:
            raise ValueError(
                f"intelligence_backfill_source_unsupported:{adapter.source}"
            )
        self.store = store
        self.adapter = adapter
        self.universe_page_size = min(
            max(1, int(universe_page_size)),
            STOCK_BASIC_MAX_ROWS,
        )
        self.fund_universe_page_size = min(
            max(1, int(fund_universe_page_size)),
            FUND_BASIC_MAX_ROWS,
        )
        self.fund_market = str(fund_market).strip().upper()
        self.fund_statuses = tuple(
            str(status).strip().upper()
            for status in fund_statuses
            if str(status).strip()
        )
        if self.fund_market != "E":
            raise ValueError(
                "intelligence_fund_basic_market_invalid"
            )
        if self.fund_statuses != FUND_BASIC_STATUSES:
            raise ValueError(
                "intelligence_fund_basic_statuses_incomplete"
            )
        if fund_include_unfiltered is not True:
            raise ValueError(
                "intelligence_fund_basic_unfiltered_required"
            )
        self.fund_include_unfiltered = True
        self.verification_rounds = max(
            1,
            int(verification_rounds),
        )
        self.lease_seconds = max(1, int(lease_seconds))
        self._sensitive_values = tuple(
            value for value in map(str, sensitive_values) if value
        )

    def run(
        self,
        *,
        start_date: date,
        end_date: date,
        max_partitions: int,
        resume: bool = False,
    ) -> dict[str, object]:
        if start_date > end_date:
            raise ValueError("intelligence_backfill_date_range_invalid")
        if int(max_partitions) < 1:
            raise ValueError(
                "intelligence_backfill_max_partitions_invalid"
            )
        roots = tuple(_month_partitions(start_date, end_date))
        config_payload = {
            "page_size": self.adapter.page_size,
            "stock_basic_statuses": STOCK_BASIC_STATUSES,
            "stock_basic_page_size": self.universe_page_size,
            "fund_basic_market": self.fund_market,
            "fund_basic_statuses": self.fund_statuses,
            "fund_basic_include_unfiltered":
                self.fund_include_unfiltered,
            "fund_basic_page_size": self.fund_universe_page_size,
            "verification_rounds": self.verification_rounds,
            "completion_strategy_version":
                BACKFILL_COMPLETION_STRATEGY_VERSION,
        }
        config_json = json.dumps(
            config_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        config_hash = hashlib.sha256(
            config_json.encode("utf-8")
        ).hexdigest()
        compatibility_payload = dict(config_payload)
        compatibility_payload.pop("page_size")
        compatibility_hash = hashlib.sha256(
            json.dumps(
                compatibility_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        job = self.store.ensure_backfill_job(
            self.source,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            config_hash=config_hash,
            compatibility_hash=compatibility_hash,
            config_json=config_json,
            request_limit=self.adapter.page_size,
            verification_required=self.verification_rounds,
        )
        state = _RunState(
            remaining=int(max_partitions),
            resume=bool(resume),
            job_id=str(job["job_id"]),
            job_generation=int(job["generation"]),
        )
        roots_complete = True
        for partition in roots:
            outcome = self._process(partition, state)
            roots_complete = roots_complete and outcome is True
            if state.supplier_circuit_open:
                break
            if state.remaining <= 0 and outcome is not True:
                break

        statuses_by_partition = {
            (partition_start, partition_end): self.store.backfill_partition(
                self.source,
                partition_start,
                partition_end,
            )
            for partition_start, partition_end in state.touched
        }
        statuses = list(statuses_by_partition.values())
        complete_count = sum(
            row is not None and row["status"] == "complete"
            for row in statuses
        )
        result_status = "complete" if roots_complete else "partial"
        try:
            result_status = self.store.finish_backfill_job(
                state.job_id,
                generation=state.job_generation,
                status=result_status,
            )
        except BackfillGenerationConflict:
            result_status = "partial"
        progress = self.store.backfill_job_progress(
            state.job_id
        )
        untracked_roots = 0
        for partition in roots:
            row = statuses_by_partition.get(
                (partition.start_key, partition.end_key)
            )
            if row is None:
                untracked_roots += 1
        return {
            "status": result_status,
            "source": self.source,
            "partitions_complete": complete_count,
            "partitions_failed": progress["partitions_failed"],
            "partitions_needs_revalidation":
                progress["partitions_needs_revalidation"],
            "fetched": state.fetched,
            "inserted": state.inserted,
            "b_share_filtered": state.b_share_filtered,
            "live_cursor_unchanged": True,
            "coverage_basis":
                "catalog_items_plus_stable_offset0_reprobes",
            "partitions_remaining":
                progress["partitions_remaining"] + untracked_roots,
            "items_total": progress["items_total"],
            "items_complete": progress["items_complete"],
            "items_remaining": progress["items_remaining"],
            "items_failed": progress["items_failed"],
            "request_budget_remaining": state.remaining,
            "verification": progress["verification"],
        }

    def _process(
        self,
        partition: HistoryPartition,
        state: _RunState,
    ) -> bool | None:
        state.touched.add(
            (partition.start_key, partition.end_key)
        )
        existing = self.store.backfill_partition(
            self.source,
            partition.start_key,
            partition.end_key,
        )
        if existing is not None:
            try:
                existing = self.store.reference_backfill_partition(
                    self.source,
                    partition.start_key,
                    partition.end_key,
                    job_id=state.job_id,
                    job_generation=state.job_generation,
                )
            except (
                BackfillConfigurationConflict,
                BackfillGenerationConflict,
            ):
                return False
        if (
            existing is not None
            and existing["status"] == "complete"
            and int(existing.get("completion_strategy_version") or 0)
            == BACKFILL_COMPLETION_STRATEGY_VERSION
        ):
            return True
        if state.supplier_circuit_open:
            return False
        if (
            existing is not None
            and existing["status"] == "failed_overflow"
            and state.resume
            and (
                not partition.is_day
                or int(existing.get("probe_manifest_version") or 0)
                >= 1
            )
        ):
            if partition.is_day:
                return self._process_saturated_day(
                    partition,
                    state,
                )
            return self._process_split(partition, state)
        if state.remaining <= 0:
            return None

        try:
            claim = self.store.start_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                resume=state.resume,
                request_limit=self.adapter.page_size,
                lease_seconds=self.lease_seconds,
                job_id=state.job_id,
                job_generation=state.job_generation,
            )
        except (
            BackfillLeaseBusy,
            BackfillConfigurationConflict,
            BackfillGenerationConflict,
        ):
            return None
        if claim["status"] == "complete":
            return True
        generation = int(claim["generation"])
        state.remaining -= 1
        try:
            page = self.adapter.fetch_range_page(
                start_date=partition.api_start,
                end_date=partition.api_end,
                offset=0,
            )
            state.fetched += page.fetched
            state.b_share_filtered += page.b_share_filtered
            if page.fetched < self.adapter.page_size:
                inserted = self.store.commit_backfill_partition_leaf(
                    self.source,
                    partition.start_key,
                    partition.end_key,
                    generation=generation,
                    writes=self._writes(page.documents),
                    fetched=page.fetched,
                    b_share_filtered=page.b_share_filtered,
                )
                state.inserted += inserted
                return True

            if partition.is_day:
                inserted = (
                    self.store.commit_backfill_partition_probe(
                        self.source,
                        partition.start_key,
                        partition.end_key,
                        generation=generation,
                        writes=self._writes(page.documents),
                        probe_security_pairs=page.security_pairs,
                        fetched=page.fetched,
                        b_share_filtered=page.b_share_filtered,
                        job_id=state.job_id,
                    )
                )
                state.inserted += inserted
                return self._process_saturated_day(
                    partition,
                    state,
                )
            self.store.finish_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                generation=generation,
                status="failed_overflow",
                error="range_probe_saturated",
            )
            return self._process_split(partition, state)
        except TushareRetryableError as exc:
            self._finish_partition_failure(
                partition,
                generation,
                status="failed_retryable",
                exc=exc,
            )
            state.supplier_circuit_open = True
            return False
        except TushareTerminalError as exc:
            self._finish_partition_failure(
                partition,
                generation,
                status="failed_terminal",
                exc=exc,
            )
            state.supplier_circuit_open = True
            return False
        except BackfillGenerationConflict:
            return None
        except Exception as exc:  # noqa: BLE001 - persisted crash boundary
            self._finish_partition_failure(
                partition,
                generation,
                status="failed_retryable",
                exc=exc,
            )
            return False

    def _process_split(
        self,
        partition: HistoryPartition,
        state: _RunState,
    ) -> bool | None:
        left, right = partition.split()
        left_complete = self._process(left, state)
        if state.supplier_circuit_open:
            return False
        right_complete = self._process(right, state)
        if left_complete is True and right_complete is True:
            return self._complete_split_parent(partition, state)
        if left_complete is None or right_complete is None:
            return None
        return False

    def _complete_split_parent(
        self,
        partition: HistoryPartition,
        state: _RunState,
    ) -> bool | None:
        try:
            claim = self.store.start_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                resume=True,
                request_limit=self.adapter.page_size,
                lease_seconds=self.lease_seconds,
                job_id=state.job_id,
                job_generation=state.job_generation,
            )
            if claim["status"] == "complete":
                return True
            self.store.finish_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                generation=int(claim["generation"]),
                status="complete",
                error="split_complete",
            )
            return True
        except (BackfillLeaseBusy, BackfillGenerationConflict):
            return None

    def _process_saturated_day(
        self,
        partition: HistoryPartition,
        state: _RunState,
    ) -> bool | None:
        if not self.store.backfill_probe_manifest_exists(
            self.source,
            partition.start_key,
            partition.end_key,
        ):
            return False
        binding = self.store.backfill_universe_for_partition(
            self.source,
            partition.start_key,
            partition.end_key,
        )
        if binding is None:
            try:
                frozen = self.store.backfill_job_universe(
                    state.job_id
                )
                members = (
                    ()
                    if frozen is not None
                    else self._load_universe_members()
                )
                binding = self.store.bind_backfill_universe(
                    self.source,
                    partition.start_key,
                    partition.end_key,
                    security_members=members,
                    request_limit=self.adapter.page_size,
                    list_statuses=tuple(
                        f"stock:{status}"
                        for status in STOCK_BASIC_STATUSES
                    ) + ("fund:ALL",) + tuple(
                        f"fund:{status}"
                        for status in self.fund_statuses
                    ),
                    job_id=state.job_id,
                    job_generation=state.job_generation,
                )
            except TushareRetryableError as exc:
                self._replace_parent_failure(
                    partition,
                    status="failed_retryable",
                    exc=exc,
                    job_id=state.job_id,
                    job_generation=state.job_generation,
                )
                state.supplier_circuit_open = True
                return False
            except TushareTerminalError as exc:
                self._replace_parent_failure(
                    partition,
                    status="failed_terminal",
                    exc=exc,
                    job_id=state.job_id,
                    job_generation=state.job_generation,
                )
                state.supplier_circuit_open = True
                return False
        del binding

        while True:
            self.store.expand_backfill_job_from_catalog(
                state.job_id
            )
            any_failed = False
            for item in self.store.backfill_partition_items(
                self.source,
                partition.start_key,
                partition.end_key,
            ):
                if item["status"] == "complete":
                    continue
                if state.supplier_circuit_open:
                    return False
                same_overflow_configuration = (
                    item["status"] == "failed_overflow"
                    and int(item["request_limit"])
                    == self.adapter.page_size
                )
                if same_overflow_configuration and state.resume:
                    any_failed = True
                    continue
                if state.remaining <= 0:
                    return None
                outcome = self._process_security_item(
                    partition,
                    str(item["ts_code"]),
                    state,
                )
                if outcome is False:
                    any_failed = True
            if state.supplier_circuit_open:
                return False
            if any_failed:
                return False
            verification = (
                self.store.backfill_verification_state(
                    state.job_id,
                    self.source,
                    partition.start_key,
                    partition.end_key,
                )
            )
            if (
                int(verification["stable_rounds"])
                >= self.verification_rounds
            ):
                return self._complete_item_parent(
                    partition,
                    state,
                )
            if state.remaining <= 0:
                return None
            verified = self._run_verification_round(
                partition,
                state,
            )
            if verified is not True:
                return verified

    def _run_verification_round(
        self,
        partition: HistoryPartition,
        state: _RunState,
    ) -> bool | None:
        try:
            claim = self.store.start_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                resume=True,
                request_limit=self.adapter.page_size,
                lease_seconds=self.lease_seconds,
                job_id=state.job_id,
                job_generation=state.job_generation,
            )
        except (
            BackfillLeaseBusy,
            BackfillConfigurationConflict,
            BackfillGenerationConflict,
        ):
            return None
        if claim["status"] == "complete":
            return True
        generation = int(claim["generation"])
        state.remaining -= 1
        try:
            page = self.adapter.fetch_range_page(
                start_date=partition.api_start,
                end_date=partition.api_end,
                offset=0,
            )
            state.fetched += page.fetched
            state.b_share_filtered += page.b_share_filtered
            result = self.store.commit_backfill_verification_round(
                self.source,
                partition.start_key,
                partition.end_key,
                job_id=state.job_id,
                generation=generation,
                writes=self._writes(page.documents),
                probe_security_pairs=page.security_pairs,
                fetched=page.fetched,
                b_share_filtered=page.b_share_filtered,
            )
            state.inserted += int(result["inserted"])
            return True
        except TushareRetryableError as exc:
            self._finish_partition_failure(
                partition,
                generation,
                status="failed_retryable",
                exc=exc,
            )
            state.supplier_circuit_open = True
            return False
        except TushareTerminalError as exc:
            self._finish_partition_failure(
                partition,
                generation,
                status="failed_terminal",
                exc=exc,
            )
            state.supplier_circuit_open = True
            return False
        except BackfillGenerationConflict:
            return None
        except Exception as exc:  # noqa: BLE001
            self._finish_partition_failure(
                partition,
                generation,
                status="failed_retryable",
                exc=exc,
            )
            return False

    def _process_security_item(
        self,
        partition: HistoryPartition,
        ts_code: str,
        state: _RunState,
    ) -> bool | None:
        try:
            claim = self.store.start_backfill_item(
                self.source,
                partition.start_key,
                partition.end_key,
                ts_code,
                resume=state.resume,
                request_limit=self.adapter.page_size,
                lease_seconds=self.lease_seconds,
            )
        except BackfillLeaseBusy:
            return None
        if claim["status"] == "complete":
            return True
        generation = int(claim["generation"])
        state.remaining -= 1
        try:
            page = self.adapter.fetch_security_day_page(
                ann_date=partition.api_start,
                ts_code=ts_code,
                offset=0,
            )
            state.fetched += page.fetched
            state.b_share_filtered += page.b_share_filtered
            if page.fetched >= self.adapter.page_size:
                self.store.finish_backfill_item(
                    self.source,
                    partition.start_key,
                    partition.end_key,
                    ts_code,
                    generation=generation,
                    status="failed_overflow",
                    error="security_probe_saturated",
                )
                return False
            inserted = self.store.commit_backfill_item_leaf(
                self.source,
                partition.start_key,
                partition.end_key,
                ts_code,
                generation=generation,
                writes=self._writes(page.documents),
                fetched=page.fetched,
                b_share_filtered=page.b_share_filtered,
            )
            state.inserted += inserted
            return True
        except TushareRetryableError as exc:
            self._finish_item_failure(
                partition,
                ts_code,
                generation,
                status="failed_retryable",
                exc=exc,
            )
            state.supplier_circuit_open = True
            return False
        except TushareTerminalError as exc:
            self._finish_item_failure(
                partition,
                ts_code,
                generation,
                status="failed_terminal",
                exc=exc,
            )
            state.supplier_circuit_open = True
            return False
        except BackfillGenerationConflict:
            return None
        except Exception as exc:  # noqa: BLE001
            self._finish_item_failure(
                partition,
                ts_code,
                generation,
                status="failed_retryable",
                exc=exc,
            )
            return False

    def _complete_item_parent(
        self,
        partition: HistoryPartition,
        state: _RunState,
    ) -> bool | None:
        try:
            claim = self.store.start_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                resume=True,
                request_limit=self.adapter.page_size,
                lease_seconds=self.lease_seconds,
                job_id=state.job_id,
                job_generation=state.job_generation,
            )
            if claim["status"] == "complete":
                return True
            complete = self.store.complete_backfill_partition_from_items(
                self.source,
                partition.start_key,
                partition.end_key,
                generation=int(claim["generation"]),
                job_id=state.job_id,
            )
            if not complete:
                self.store.finish_backfill_partition(
                    self.source,
                    partition.start_key,
                    partition.end_key,
                    generation=int(claim["generation"]),
                    status="failed_overflow",
                    error="security_items_incomplete",
                )
            return complete
        except (BackfillLeaseBusy, BackfillGenerationConflict):
            return None

    def _load_universe_members(
        self,
    ) -> tuple[BackfillUniverseMember, ...]:
        members: dict[str, BackfillUniverseMember] = {}
        for status in STOCK_BASIC_STATUSES:
            frame = self.adapter.client.stock_basic(
                exchange="",
                list_status=status,
                limit=self.universe_page_size,
                offset=0,
                fields=STOCK_BASIC_FIELDS,
            )
            if not isinstance(frame, pd.DataFrame):
                raise TushareTerminalError(
                    "tushare_stock_basic_invalid_response"
                )
            if len(frame) >= self.universe_page_size:
                raise TushareTerminalError(
                    f"tushare_stock_basic_saturated:{status}"
                )
            if frame.empty:
                continue
            self._collect_universe_members(
                members,
                frame,
                security_type="stock",
                status_field="list_status",
                query_status=status,
                required_fields={
                    "ts_code",
                    "list_date",
                    "delist_date",
                    "list_status",
                },
            )
        fund_queries: tuple[str | None, ...] = (
            None,
            *self.fund_statuses,
        )
        for status in fund_queries:
            parameters: dict[str, object] = {
                "market": self.fund_market,
                "limit": self.fund_universe_page_size,
                "offset": 0,
                "fields": FUND_BASIC_FIELDS,
            }
            if status is not None:
                parameters["status"] = status
            frame = self.adapter.client.fund_basic(**parameters)
            if not isinstance(frame, pd.DataFrame):
                raise TushareTerminalError(
                    "tushare_fund_basic_invalid_response"
                )
            if len(frame) >= self.fund_universe_page_size:
                raise TushareTerminalError(
                    "tushare_fund_basic_saturated:"
                    f"{status or 'ALL'}"
                )
            if frame.empty:
                continue
            self._collect_universe_members(
                members,
                frame,
                security_type="fund",
                status_field="status",
                query_status=status or "",
                required_fields={
                    "ts_code",
                    "list_date",
                    "delist_date",
                    "status",
                },
            )
        if not members:
            raise TushareTerminalError(
                "tushare_announcement_universe_empty"
            )
        return tuple(members[code] for code in sorted(members))

    def _collect_universe_members(
        self,
        members: dict[str, BackfillUniverseMember],
        frame: pd.DataFrame,
        *,
        security_type: str,
        status_field: str,
        query_status: str,
        required_fields: set[str],
    ) -> None:
        if not required_fields.issubset(frame.columns):
            raise TushareTerminalError(
                f"tushare_{security_type}_basic_fields_missing"
            )
        for row in frame.to_dict(orient="records"):
            code = _provider_text(row.get("ts_code")).upper()
            digits = "".join(
                character for character in code
                if character.isdigit()
            )
            if not code or digits.startswith(("200", "900")):
                continue
            member = BackfillUniverseMember(
                ts_code=code,
                security_type=security_type,
                list_date=_provider_text(row.get("list_date")),
                delist_date=_provider_text(row.get("delist_date")),
                listing_status=(
                    _provider_text(row.get(status_field)).upper()
                    or query_status
                ),
            )
            existing = members.get(code)
            members[code] = (
                member
                if existing is None
                else _merge_universe_member(existing, member)
            )

    def _load_universe_codes(self) -> tuple[str, ...]:
        return tuple(
            member.ts_code
            for member in self._load_universe_members()
        )

    def _writes(
        self,
        documents: Iterable[SourceDocument],
    ) -> tuple[BackfillDocumentWrite, ...]:
        return tuple(
            self._write_for_document(document)
            for document in documents
        )

    def _write_for_document(
        self,
        document: SourceDocument,
    ) -> BackfillDocumentWrite:
        if utc_iso(document.published_at) > self.store.historical_cutoff:
            return BackfillDocumentWrite(document)
        precise_rec_time = announcement_rec_time(
            document.metadata.get("rec_time")
        )
        if precise_rec_time is not None:
            return BackfillDocumentWrite(
                document,
                availability_provenance="reconstructed_rec_time",
                source_recorded_at=precise_rec_time,
                research_available_at=precise_rec_time,
            )
        return BackfillDocumentWrite(
            document,
            availability_provenance="reconstructed_next_open",
        )

    def _finish_partition_failure(
        self,
        partition: HistoryPartition,
        generation: int,
        *,
        status: str,
        exc: Exception,
    ) -> None:
        try:
            self.store.finish_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                generation=generation,
                status=status,
                error=self._safe_error(exc),
            )
        except BackfillGenerationConflict:
            pass

    def _replace_parent_failure(
        self,
        partition: HistoryPartition,
        *,
        status: str,
        exc: Exception,
        job_id: str = "",
        job_generation: int | None = None,
    ) -> None:
        try:
            claim = self.store.start_backfill_partition(
                self.source,
                partition.start_key,
                partition.end_key,
                resume=True,
                request_limit=self.adapter.page_size,
                lease_seconds=self.lease_seconds,
                job_id=job_id,
                job_generation=job_generation,
            )
            if claim["status"] != "complete":
                self.store.finish_backfill_partition(
                    self.source,
                    partition.start_key,
                    partition.end_key,
                    generation=int(claim["generation"]),
                    status=status,
                    error=self._safe_error(exc),
                )
        except (BackfillLeaseBusy, BackfillGenerationConflict):
            pass

    def _finish_item_failure(
        self,
        partition: HistoryPartition,
        ts_code: str,
        generation: int,
        *,
        status: str,
        exc: Exception,
    ) -> None:
        try:
            self.store.finish_backfill_item(
                self.source,
                partition.start_key,
                partition.end_key,
                ts_code,
                generation=generation,
                status=status,
                error=self._safe_error(exc),
            )
        except BackfillGenerationConflict:
            pass

    def _safe_error(self, exc: Exception) -> str:
        message = f"{type(exc).__name__}:{exc}"
        for value in self._sensitive_values:
            message = message.replace(value, "[REDACTED]")
        return message


def _provider_text(value: object) -> str:
    if value is None or not pd.notna(value):
        return ""
    return str(value).strip()


def _merge_universe_member(
    existing: BackfillUniverseMember,
    incoming: BackfillUniverseMember,
) -> BackfillUniverseMember:
    if existing.security_type != incoming.security_type:
        raise TushareTerminalError(
            "tushare_universe_member_type_conflict:"
            f"{existing.ts_code}"
        )

    def merged_value(left: str, right: str) -> str:
        values = sorted({
            str(value).strip()
            for value in (left, right)
            if str(value).strip()
        })
        return values[0] if values else ""

    statuses = sorted({
        status
        for value in (
            existing.listing_status,
            incoming.listing_status,
        )
        for status in str(value).split(",")
        if status
    })
    return BackfillUniverseMember(
        ts_code=existing.ts_code,
        security_type=existing.security_type,
        list_date=merged_value(
            existing.list_date,
            incoming.list_date,
        ),
        delist_date=merged_value(
            existing.delist_date,
            incoming.delist_date,
        ),
        listing_status=",".join(statuses),
    )




def _month_partitions(
    start_date: date,
    end_date: date,
) -> Iterable[HistoryPartition]:
    current = start_date
    while current <= end_date:
        next_month = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
        yield HistoryPartition(
            current,
            min(end_date, next_month - timedelta(days=1)),
        )
        current = next_month
