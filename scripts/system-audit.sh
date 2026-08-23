#!/usr/bin/env bash
# One-command structural and runtime audit for Stock Analyze.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="${1:-local}"
PYTHON_BIN="${SA_PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  elif [[ -x /opt/stock-analyze/venv/bin/python ]]; then
    PYTHON_BIN=/opt/stock-analyze/venv/bin/python
  else
    PYTHON_BIN=python3
  fi
fi

cd "$ROOT"

run_data_foundation_audit() {
  "$PYTHON_BIN" - "$ROOT" "${SA_SYSTEM_AUDIT_PRODUCTION:-0}" <<'PY'
from __future__ import annotations

import csv
import json
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml


root = Path(sys.argv[1]).resolve()
production = sys.argv[2] == "1"
results: list[str] = []
_A_SHARE_STOCK_CODE = re.compile(
    r"(?:(?:600|601|603|605|688|689)[0-9]{3}\.SH|"
    r"(?:000|001|002|003|300|301)[0-9]{3}\.SZ|"
    r"(?:43|83|87)[0-9]{4}\.BJ|920[0-9]{3}\.BJ)"
)


def emit(status: str, name: str, detail: str) -> None:
    results.append(status)
    print(f"{status} {name} {detail}")


def absent_status() -> str:
    return "FAIL" if production else "WARN"


def is_a_share_stock_code(value: object) -> bool:
    return _A_SHARE_STOCK_CODE.fullmatch(str(value)) is not None


def reason_code(exc: Exception, prefix: str) -> str:
    raw = str(exc).split(":", 1)[0]
    if re.fullmatch(r"[a-z0-9_]+", raw) and raw.startswith(prefix):
        return raw
    return f"{prefix}_invalid"


def verified_source_manifest() -> None:
    name = "a_share_all_cap_source_manifest"
    marker = root / "data/research/a_share_all_cap/v1/sources/latest.json"
    if not marker.exists():
        emit(absent_status(), name, "checksum=missing datasets=0 rows=0 partition_years=0")
        return
    try:
        from stock_analyze.research.a_share_all_cap_sources import (
            load_verified_all_cap_sources,
        )

        verified = load_verified_all_cap_sources(root)
        row_counts = verified.metadata.get("row_counts")
        if not isinstance(row_counts, Mapping):
            raise ValueError("all_cap_source_manifest_incomplete")
        rows = sum(int(value) for value in row_counts.values())
        years = len(verified.stk_limit)
        emit(
            "PASS",
            name,
            f"checksum=verified datasets={len(row_counts)} rows={rows} "
            f"partition_years={years}",
        )
    except Exception as exc:  # noqa: BLE001
        emit(
            "FAIL",
            name,
            f"checksum=invalid reason={reason_code(exc, 'all_cap_source')}",
        )


def verified_universe_manifest() -> None:
    name = "a_share_all_cap_universe_manifest"
    marker = root / "data/research/a_share_all_cap/v1/universe/latest.json"
    if not marker.exists():
        emit(
            absent_status(),
            name,
            "checksum=missing membership_years=0 membership_rows=0 "
            "status_years=0 status_rows=0",
        )
        return
    try:
        from stock_analyze.research.a_share_all_cap_universe import (
            load_verified_all_cap_universe,
        )

        verified = load_verified_all_cap_universe(root)
        row_counts = verified.metadata.get("row_counts")
        if not isinstance(row_counts, Mapping):
            raise ValueError("all_cap_universe_manifest_incomplete")
        emit(
            "PASS",
            name,
            "checksum=verified "
            f"membership_years={len(verified.membership)} "
            f"membership_rows={int(row_counts['membership'])} "
            f"status_years={len(verified.daily_hard_status)} "
            f"status_rows={int(row_counts['daily_hard_status'])}",
        )
    except Exception as exc:  # noqa: BLE001
        emit(
            "FAIL",
            name,
            f"checksum=invalid reason={reason_code(exc, 'all_cap_universe')}",
        )


def load_contract_window() -> tuple[str, str, float]:
    contract = yaml.safe_load(
        (root / "configs/research/a_share_all_cap_v2.yaml").read_text(
            encoding="utf-8"
        )
    )
    windows = contract["windows"]
    storage = contract["storage"]
    start = str(windows["development_start"])
    end = str(windows["holdout_end"])
    minimum = max(
        0.15,
        float(storage["minimum_filesystem_free_fraction_after_publish"]),
    )
    if (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) is None
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) is None
        or start > end
    ):
        raise ValueError("all_cap_audit_contract")
    return start, end, minimum


def csv_has_columns(path: Path, required: set[str]) -> bool:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            fields = csv.DictReader(handle).fieldnames
    except (OSError, UnicodeError, csv.Error):
        return False
    return fields is not None and required.issubset(fields)


def load_backtest_state() -> tuple[dict[str, object], list[dict[str, str]]]:
    cache = root / "data/shared/backtest_cache"
    meta_path = cache / "_meta.json"
    master_path = cache / "stock_basic.csv"
    if not meta_path.exists() or not master_path.exists():
        raise FileNotFoundError
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError("backtest_audit_meta")
    with master_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(
        not row.get("ts_code") or row.get("list_status") not in {"L", "D", "P"}
        for row in rows
    ):
        raise ValueError("backtest_audit_stock_master")
    return meta, rows


def backtest_checks(start: str, end: str) -> None:
    try:
        meta, rows = load_backtest_state()
    except FileNotFoundError:
        level = absent_status()
        emit(
            level,
            "a_share_stock_master_counts",
            "active=0 delisted=0 paused=0 total=0 collection_complete=0",
        )
        emit(
            level,
            "backtest_statement_code_coverage",
            "completed_codes=0 total_codes=0 completed_ranges=0",
        )
        emit(
            level,
            "backtest_status_code_coverage",
            "provider=baostock completed_codes=0 total_codes=0 completed_ranges=0",
        )
        return
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        for name, detail in (
            (
                "a_share_stock_master_counts",
                "active=0 delisted=0 paused=0 total=0 collection_complete=0",
            ),
            (
                "backtest_statement_code_coverage",
                "completed_codes=0 total_codes=0 completed_ranges=0",
            ),
            (
                "backtest_status_code_coverage",
                "provider=baostock completed_codes=0 total_codes=0 completed_ranges=0",
            ),
        ):
            emit("FAIL", name, f"{detail} reason=invalid_cache_metadata")
        return

    stock_rows = [row for row in rows if is_a_share_stock_code(row["ts_code"])]
    counts = {
        status: sum(row["list_status"] == status for row in stock_rows)
        for status in ("L", "D", "P")
    }
    statuses_done = {
        str(value) for value in meta.get("stock_basic_statuses_done", [])
    }
    master_complete = bool(meta.get("stock_basic_done")) and statuses_done == {
        "L",
        "D",
        "P",
    }
    master_level = "PASS" if master_complete else absent_status()
    emit(
        master_level,
        "a_share_stock_master_counts",
        f"active={counts['L']} delisted={counts['D']} paused={counts['P']} "
        f"total={len(stock_rows)} collection_complete={int(master_complete)}",
    )

    codes = {str(row["ts_code"]) for row in stock_rows}
    cache = root / "data/shared/backtest_cache"
    statement_complete: set[str] = set(codes)
    statement_ranges = 0
    for endpoint in ("income", "balancesheet", "cashflow"):
        ranges = {
            str(value)
            for value in meta.get(f"{endpoint}_code_ranges_done", [])
        }
        completed: set[str] = set()
        for code in codes:
            key = f"{code}:{start}:{end}"
            if key not in ranges:
                continue
            if not csv_has_columns(
                cache / endpoint / f"{code}.csv",
                {"ts_code", "ann_date", "end_date"},
            ):
                continue
            completed.add(code)
        statement_complete &= completed
        statement_ranges += len(completed)
    statement_level = (
        "PASS"
        if codes and statement_complete == codes
        else absent_status()
    )
    emit(
        statement_level,
        "backtest_statement_code_coverage",
        f"completed_codes={len(statement_complete)} total_codes={len(codes)} "
        f"completed_ranges={statement_ranges} range_start={start} range_end={end}",
    )

    status_ranges = {
        str(value)
        for value in meta.get("baostock_status_code_ranges_done", [])
    }
    status_complete = {
        code
        for code in codes
        if f"{code}:{start}:{end}" in status_ranges
        and csv_has_columns(
            cache / "baostock_status" / f"{code}.csv",
            {"ts_code", "trade_date", "tradestatus", "is_st", "st_source"},
        )
    }
    status_level = (
        "PASS" if codes and status_complete == codes else absent_status()
    )
    emit(
        status_level,
        "backtest_status_code_coverage",
        f"provider=baostock completed_codes={len(status_complete)} "
        f"total_codes={len(codes)} completed_ranges={len(status_complete)} "
        f"range_start={start} range_end={end}",
    )


def filesystem_check(minimum: float) -> None:
    usage = shutil.disk_usage(root)
    fraction = usage.free / usage.total if usage.total else 0.0
    level = "PASS" if fraction >= minimum else "FAIL"
    emit(
        level,
        "filesystem_free_fraction",
        f"free_bytes={usage.free} total_bytes={usage.total} "
        f"free_fraction={fraction:.6f} minimum={minimum:.6f}",
    )


verified_source_manifest()
verified_universe_manifest()
try:
    window_start, window_end, minimum_fraction = load_contract_window()
except (OSError, UnicodeError, KeyError, TypeError, ValueError, yaml.YAMLError):
    emit(
        "FAIL",
        "a_share_stock_master_counts",
        "active=0 delisted=0 paused=0 total=0 collection_complete=0 "
        "reason=invalid_contract",
    )
    emit(
        "FAIL",
        "backtest_statement_code_coverage",
        "completed_codes=0 total_codes=0 completed_ranges=0 "
        "reason=invalid_contract",
    )
    emit(
        "FAIL",
        "backtest_status_code_coverage",
        "provider=baostock completed_codes=0 total_codes=0 completed_ranges=0 "
        "reason=invalid_contract",
    )
    minimum_fraction = 0.15
else:
    backtest_checks(window_start, window_end)
filesystem_check(minimum_fraction)

overall = "FAIL" if "FAIL" in results else "WARN" if "WARN" in results else "PASS"
print(f"RESULT all_cap_data_foundation {overall}")
raise SystemExit(1 if overall == "FAIL" else 0)
PY
}

run_local() {
  # Remote connection settings can break tests that intentionally replace ssh
  # with a local fake. Keep the local gate hermetic when running --remote.
  env -u SA_ECS_REMOTE -u SA_ECS_SSH_HOST -u SA_ECS_SSH_OPTS \
    "$PYTHON_BIN" -m unittest \
    tests.test_system_structure \
    tests.test_archived_markets \
    tests.test_qdii_systemd_units \
    tests.test_deploy_app_script \
    tests.test_deploy_dashboard_workspaces_script \
    tests.test_dashboard_http \
    tests.test_dashboard_resource_api \
    tests.test_dashboard_workspace_api \
    tests.test_dashboard_runtime \
    tests.test_operator_workflow_docs

  "$PYTHON_BIN" -m stock_analyze --help >/dev/null
  bash -n scripts/*.sh
  echo "OK: local structure, harness, dashboard, and shell checks passed."
}

run_remote() {
  : "${SA_ECS_REMOTE:?set SA_ECS_REMOTE=user@host:/opt/stock-analyze/app}"
  local remote_no_slash="${SA_ECS_REMOTE%/}"
  local remote_host="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"

  "$SCRIPT_DIR/check-ecs-timers.sh"
  ssh ${SA_ECS_SSH_OPTS:-} "$remote_host" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/stock-analyze/app

SA_SYSTEM_AUDIT_DATA_ONLY=1 SA_SYSTEM_AUDIT_PRODUCTION=1 \
  ./scripts/system-audit.sh

failed=$(systemctl list-units --failed --plain --no-legend 'stock-analyze-*' 2>/dev/null || true)
if [[ -n "$failed" ]]; then
  echo "$failed" >&2
  echo "ERROR: failed stock-analyze systemd units found." >&2
  exit 1
fi

curl --fail --silent --show-error \
  http://127.0.0.1:8765/api/dashboard/summary.json >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/operations.json?market=a_share&agent=claude' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/model-research.json?market=a_share' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/data-intelligence.json?market=a_share' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/operations-center.json?scope=all' >/dev/null
/opt/stock-analyze/venv/bin/python -m stock_analyze intelligence-status >/dev/null
echo "OK: ECS services, dashboard APIs, and intelligence store are healthy."
REMOTE
}

data_status=0
if run_data_foundation_audit; then
  :
else
  data_status=$?
fi

if [[ "${SA_SYSTEM_AUDIT_DATA_ONLY:-0}" == "1" ]]; then
  exit "$data_status"
fi

case "$MODE" in
  local)
    run_local
    ;;
  --remote|remote)
    run_local
    run_remote
    ;;
  *)
    echo "usage: $0 [--remote]" >&2
    exit 2
    ;;
esac

exit "$data_status"
