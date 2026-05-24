"""Shared market-data preparation pipeline.

Runs once per weekday at 17:25 CST under
``stock-analyze-market-data.service`` and writes data/shared/cache/*.csv
plus data/shared/market_snapshot_<date>.json. Both agents subsequently
run with ``--offline`` and read this cache; on cache miss they fail-fast
rather than silently going to the network.

Public entry point:

    prepare_market_data(scopes, as_of, repo_root, force, max_workers)

Returns a dict with snapshot metadata that is also persisted to
``data/shared/market_snapshot_<as_of>.json``.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from . import competition
from .data_provider import DataProvider, INDEX_CODES, make_provider
from .run_ledger import RunLedger
from .strategy import preselect_universe
from .utils import ensure_dirs, parse_date


# Names that indicate a critical failure: if these come up empty the whole
# fetch is considered a failure and downstream agents must NOT run.
CRITICAL_STEPS = {"spot", "all_benchmarks"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _snapshot_path(repo_root: Path, as_of: str) -> Path:
    return repo_root / "data" / "shared" / f"market_snapshot_{as_of}.json"


def _merged_filters(agents: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Take the union of fetch-relevant filters across agents.

    prepare-market-data needs to fetch enough rows to satisfy whichever
    agent has the loosest filters. Concretely:

    - ``max_fetch_candidates``: take the max
    - ``min_pe``: take the min (most permissive)
    - ``min_avg_amount_20``: take the min
    - ``min_market_cap_yi``: take the min
    - ``max_market_cap_yi``: take the max (or unset)
    - ``min_listing_days``: take the min
    """

    def _gather(values: Iterable[Any]) -> list[float]:
        return [float(v) for v in values if v is not None]

    filters_list = [(a.get("filters") or {}) for a in agents]
    out: dict[str, Any] = {}
    max_cands = _gather(f.get("max_fetch_candidates") for f in filters_list)
    if max_cands:
        out["max_fetch_candidates"] = int(max(max_cands))
    min_pe_vals = _gather(f.get("min_pe") for f in filters_list)
    if min_pe_vals:
        out["min_pe"] = min(min_pe_vals)
    min_amount = _gather(f.get("min_avg_amount_20") for f in filters_list)
    if min_amount:
        out["min_avg_amount_20"] = min(min_amount)
    min_cap = _gather(f.get("min_market_cap_yi") for f in filters_list)
    if min_cap:
        out["min_market_cap_yi"] = min(min_cap)
    max_cap = _gather(f.get("max_market_cap_yi") for f in filters_list)
    if max_cap:
        out["max_market_cap_yi"] = max(max_cap)
    min_days = _gather(f.get("min_listing_days") for f in filters_list)
    if min_days:
        out["min_listing_days"] = min(min_days)
    return out


def _resolve_scopes_and_benchmarks(repo_root: Path, scopes: list[str] | None) -> tuple[list[str], list[str]]:
    """Return (scope list, benchmark code list) for prepare-market-data.

    Defaults to union of ``accounts.*.scope`` / ``accounts.*.benchmark``
    across the baseline. Callers can override scopes from the CLI.
    """

    baseline = competition.load_baseline(repo_root)
    accounts = baseline.get("accounts") or []
    default_scopes = sorted({str(a.get("scope")) for a in accounts if a.get("scope")})
    benchmarks = sorted({str(a.get("benchmark")) for a in accounts if a.get("benchmark")})
    return (scopes or default_scopes), benchmarks


def _fetch_one_candidate(
    provider: DataProvider,
    code: str,
    errors: list[dict[str, Any]],
) -> dict[str, int]:
    """Sequentially fetch the 5 per-stock endpoints for a single ``code``.

    Records per-method failures into ``errors`` (caller-shared list); does
    not raise on partial failure. Returns counters for the snapshot.
    """

    counts = {"basic": 0, "history": 0, "valuation": 0, "financial": 0, "dividend": 0}
    steps: tuple[tuple[str, str, Any], ...] = (
        ("basic", "basic_info", lambda: provider.basic_info(code)),
        ("history", "price_history", lambda: provider.price_history(code, days=220)),
        ("valuation", "valuation_metrics", lambda: provider.valuation_metrics(code)),
        ("financial", "financial_metrics", lambda: provider.financial_metrics(code)),
        ("dividend", "dividend_yield", lambda: provider.dividend_yield(code)),
    )
    for counter_key, method_name, call in steps:
        try:
            result = call()
            empty = (
                result is None
                or (hasattr(result, "empty") and getattr(result, "empty", False))
                or (isinstance(result, dict) and not result)
            )
            if empty:
                errors.append({"code": code, "method": method_name, "message": "empty_result"})
            else:
                counts[counter_key] = 1
        except Exception as exc:  # noqa: BLE001 — record + continue per-stock
            errors.append({"code": code, "method": method_name, "message": str(exc)[:200]})
    return counts


def _build_universe(provider: DataProvider, scopes: list[str], errors: list[dict[str, Any]]) -> pd.DataFrame:
    """Pull spot + constituents for each scope and return the merged universe."""

    spot = provider.spot()
    if spot.empty:
        errors.append({"code": "", "method": "spot", "message": "empty_spot"})
        return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])

    frames: list[pd.DataFrame] = []
    for scope in scopes:
        try:
            constituents = provider.index_constituents(scope)
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": scope, "method": "index_constituents", "message": str(exc)[:200]})
            continue
        if constituents.empty:
            errors.append({"code": scope, "method": "index_constituents", "message": "empty_constituents"})
            continue
        merged = constituents.merge(spot, on="code", how="left", suffixes=("_index", ""))
        merged["name"] = merged["name"].fillna(merged.get("name_index"))
        frames.append(merged[["code", "name", "latest_price", "pe", "pb", "market_cap_yi"]])

    if not frames:
        return pd.DataFrame(columns=["code", "name", "latest_price", "pe", "pb", "market_cap_yi"])

    universe = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["code"])
    return universe


def prepare_market_data(
    *,
    scopes: list[str] | None = None,
    as_of: str | None = None,
    repo_root: Path | None = None,
    force: bool = False,
    max_workers: int = 5,
) -> dict[str, Any]:
    """Pull all data the two agents will need today and write it to cache.

    Side effects:

    - Writes ``data/shared/cache/*.csv`` (one file per (method, code, date)).
    - Writes ``data/shared/market_snapshot_<as_of>.json`` with timing /
      error / row counters.
    - Appends one row to ``data/shared/runs.csv`` describing this run.

    The function is **online-only**: it constructs a provider via
    ``make_provider(offline=False)`` intentionally. Agents read what this
    writes; do not call this from an agent service.

    Returns the snapshot dict.
    """

    repo_root = (repo_root or Path.cwd()).resolve()
    as_of_str = as_of or pd.Timestamp.now().strftime("%Y-%m-%d")
    parse_date(as_of_str)  # validate format early

    snapshot_path = _snapshot_path(repo_root, as_of_str)
    if snapshot_path.exists() and not force:
        existing = json.loads(snapshot_path.read_text(encoding="utf-8"))
        # Only skip if the previous run actually produced usable data.
        # Failed snapshots SHOULD be retried — otherwise a one-time transient
        # failure (e.g. token not yet provisioned, rate limit hit) sticks
        # forever and the operator has to manually delete the file before
        # the next attempt. Treat ``partial`` as "good enough to skip" since
        # candidates are present.
        if existing.get("status") in ("success", "partial"):
            existing["skipped"] = "snapshot_exists"
            return existing

    cache_dir = repo_root / "data" / "shared" / "cache"
    ensure_dirs(cache_dir, repo_root / "data" / "shared")

    if force:
        # --force at the orchestration layer skipped the snapshot-existence check
        # above, but the per-provider methods (spot, stock_basic, index_constituents,
        # trading_calendar) all short-circuit on a non-empty load_cache(...) result —
        # so stale universe CSVs from a prior bad run were silently re-served. Drop
        # them here so the provider falls through to a real fetch.
        for pattern in ("spot_*", "stock_basic_*", "constituents_*", "trading_calendar"):
            for path in cache_dir.glob(f"{pattern}.csv"):
                path.unlink(missing_ok=True)

    scopes, benchmarks = _resolve_scopes_and_benchmarks(repo_root, scopes)
    agents = [competition.load(agent_id, repo_root=repo_root) for agent_id in competition.list_agents(repo_root)]
    filters = _merged_filters(agents)

    started_at = _now_iso()
    start_clock = time.time()
    errors: list[dict[str, Any]] = []
    counters: dict[str, int] = {"spot": 0, "trading_calendar": 0}

    provider = make_provider(cache_dir=cache_dir, offline=False, as_of=as_of_str)

    # 1. trading_calendar
    try:
        calendar_rows = provider.trading_calendar()
        counters["trading_calendar"] = len(calendar_rows)
        if not calendar_rows:
            errors.append({"code": "", "method": "trading_calendar", "message": "empty_calendar"})
    except Exception as exc:  # noqa: BLE001
        errors.append({"code": "", "method": "trading_calendar", "message": str(exc)[:200]})

    # 2. spot + 3. constituents → universe
    universe = _build_universe(provider, scopes, errors)
    counters["spot"] = int(len(provider.spot()))
    for scope in scopes:
        index_code = INDEX_CODES.get(scope, scope)
        counters[f"constituents_{index_code}"] = int(len(provider.index_constituents(scope)))

    if universe.empty:
        errors.append({"code": "", "method": "universe", "message": "no_candidates_after_merge"})

    # 4. preselect candidates (use merged filters so neither agent runs short)
    candidates = preselect_universe(universe, filters)
    candidate_codes: list[str] = candidates["code"].dropna().astype(str).tolist() if not candidates.empty else []
    counters["candidates_fetched"] = len(candidate_codes)

    # 5. per-candidate detail fetch (concurrent across stocks, sequential within)
    per_code_counts = {"basic": 0, "history": 0, "valuation": 0, "financial": 0, "dividend": 0}
    if candidate_codes:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one_candidate, provider, code, errors): code for code in candidate_codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    counts = future.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append({"code": code, "method": "candidate_worker", "message": str(exc)[:200]})
                    continue
                for key, value in counts.items():
                    per_code_counts[key] += value
    counters["basic_info"] = per_code_counts["basic"]
    counters["price_history"] = per_code_counts["history"]
    counters["valuation"] = per_code_counts["valuation"]
    counters["financial"] = per_code_counts["financial"]
    counters["dividend"] = per_code_counts["dividend"]

    # 6. benchmark closes
    benchmark_success = 0
    for code in benchmarks:
        try:
            close, trade_date = provider.benchmark_close(code, as_of=as_of_str)
            if close is not None and trade_date:
                counters[f"benchmark_{code}"] = 1
                benchmark_success += 1
            else:
                errors.append({"code": code, "method": "benchmark_close", "message": "no_close"})
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": code, "method": "benchmark_close", "message": str(exc)[:200]})

    # 7. determine overall status
    fatal_reasons: list[str] = []
    if counters.get("spot", 0) == 0:
        fatal_reasons.append("spot")
    if benchmarks and benchmark_success == 0:
        fatal_reasons.append("all_benchmarks")
    if not candidate_codes and scopes:
        fatal_reasons.append("no_candidates")

    if fatal_reasons:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "success"

    finished_at = _now_iso()
    duration_ms = int((time.time() - start_clock) * 1000)

    snapshot = {
        "as_of": as_of_str,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "scopes": scopes,
        "benchmarks": benchmarks,
        "candidates_fetched": len(candidate_codes),
        "rows": counters,
        "errors": errors,
        "fetch_summary": {
            "ok": sum(1 for _ in counters.values() if _),
            "errors": len(errors),
            "fatal": fatal_reasons,
        },
        "status": status,
    }

    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    provider.persist_health()

    return snapshot


def prepare_market_data_via_ledger(
    *,
    scopes: list[str] | None = None,
    as_of: str | None = None,
    repo_root: Path | None = None,
    force: bool = False,
    max_workers: int = 5,
) -> dict[str, Any]:
    """Like ``prepare_market_data`` but wraps the call in the shared RunLedger.

    Writes one row to ``data/shared/runs.csv`` with config_hash from the
    union of agent overlays — there is no single config to hash, so we
    hash an empty placeholder. Failures (fatal_reasons present) raise so
    systemd ExecStartPost won't fire downstream agents.
    """

    repo_root = (repo_root or Path.cwd()).resolve()
    shared_data = repo_root / "data" / "shared"
    ensure_dirs(shared_data)
    ledger = RunLedger(shared_data)

    with ledger.run("prepare-market-data", as_of, {}):
        snapshot = prepare_market_data(
            scopes=scopes,
            as_of=as_of,
            repo_root=repo_root,
            force=force,
            max_workers=max_workers,
        )
        if snapshot.get("status") == "failed":
            fatal = ",".join(snapshot.get("fetch_summary", {}).get("fatal") or [])
            raise RuntimeError(f"prepare_market_data fatal: {fatal}")
        return snapshot
