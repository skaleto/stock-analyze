#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_LOCAL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

STAGE="parse"
LIMIT="10"
WORKERS="2"
REMOTE="${SA_LOCAL_INTELLIGENCE_REMOTE:-root@120.55.188.242}"
SSH_KEY="${SA_LOCAL_INTELLIGENCE_SSH_KEY:-$HOME/.ssh/ai_baby_aliyun}"
LOCAL_ROOT="$DEFAULT_LOCAL_ROOT"
REMOTE_ROOT="${SA_REMOTE_INTELLIGENCE_ROOT:-/opt/stock-analyze/app}"
REMOTE_PYTHON="${SA_REMOTE_INTELLIGENCE_PYTHON:-/opt/stock-analyze/venv/bin/python}"
REMOTE_FLOCK="${SA_REMOTE_INTELLIGENCE_FLOCK:-/usr/bin/flock}"
REMOTE_STAGE_LOCK="${SA_REMOTE_INTELLIGENCE_STAGE_LOCK:-/run/stock-analyze-intelligence-reconcile.lock}"
REMOTE_ENV_FILE="${SA_REMOTE_INTELLIGENCE_ENV_FILE:-/etc/stock-analyze/secrets.env}"
LEASE_SECONDS="${SA_LOCAL_INTELLIGENCE_LEASE_SECONDS:-14400}"
WORKER_ID="${SA_LOCAL_INTELLIGENCE_WORKER_ID:-local-$(hostname -s 2>/dev/null || hostname)}"
MAX_JOBS="${SA_LOCAL_INTELLIGENCE_MAX_JOBS:-10}"
MAX_RUNTIME_SECONDS="${SA_LOCAL_INTELLIGENCE_MAX_RUNTIME_SECONDS:-3600}"
JOB_TIMEOUT_SECONDS="${SA_LOCAL_INTELLIGENCE_JOB_TIMEOUT_SECONDS:-1800}"
INTER_JOB_DELAY_SECONDS="${SA_LOCAL_INTELLIGENCE_INTER_JOB_DELAY_SECONDS:-2}"
ONCE=0
ALLOW_BATTERY=0

usage() {
  cat <<'EOF'
Usage:
  run-local-intelligence-artifact-worker.sh [options]

Options:
  --stage parse|download  Artifact stage to claim (default: parse)
  --limit N               Maximum artifacts in one job (default: 10)
  --workers N             Local worker count (default: 2)
  --max-jobs N            Maximum jobs per invocation (default: 10)
  --max-runtime-seconds N Maximum invocation duration (default: 3600)
  --job-timeout-seconds N Maximum local job duration (default: 1800)
  --remote USER@HOST      ECS SSH target
  --ssh-key PATH          SSH private key path
  --local-root PATH       Local stock-analyze repository root
  --once                  Process at most one job
  --allow-battery         Explicitly allow work while on battery power
  -h, --help              Show this help
EOF
}

require_value() {
  local option="$1"
  local count="$2"
  if [[ "$count" -lt 2 ]]; then
    printf 'error: %s requires a value\n' "$option" >&2
    exit 2
  fi
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --stage)
      require_value "$1" "$#"
      STAGE="$2"
      shift 2
      ;;
    --limit)
      require_value "$1" "$#"
      LIMIT="$2"
      shift 2
      ;;
    --workers)
      require_value "$1" "$#"
      WORKERS="$2"
      shift 2
      ;;
    --max-jobs)
      require_value "$1" "$#"
      MAX_JOBS="$2"
      shift 2
      ;;
    --max-runtime-seconds)
      require_value "$1" "$#"
      MAX_RUNTIME_SECONDS="$2"
      shift 2
      ;;
    --job-timeout-seconds)
      require_value "$1" "$#"
      JOB_TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --remote)
      require_value "$1" "$#"
      REMOTE="$2"
      shift 2
      ;;
    --ssh-key)
      require_value "$1" "$#"
      SSH_KEY="$2"
      shift 2
      ;;
    --local-root)
      require_value "$1" "$#"
      LOCAL_ROOT="$2"
      shift 2
      ;;
    --once)
      ONCE=1
      shift
      ;;
    --allow-battery)
      ALLOW_BATTERY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$STAGE" in
  parse|download) ;;
  *)
    printf 'error: --stage must be parse or download\n' >&2
    exit 2
    ;;
esac

for numeric_value in \
  "$LIMIT" \
  "$WORKERS" \
  "$LEASE_SECONDS" \
  "$MAX_JOBS" \
  "$MAX_RUNTIME_SECONDS" \
  "$JOB_TIMEOUT_SECONDS"; do
  if [[ ! "$numeric_value" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' \
      'error: limits, workers, lease seconds, and runtime must be positive integers' \
      >&2
    exit 2
  fi
done
if [[ ! "$INTER_JOB_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
  printf 'error: inter-job delay must be a non-negative integer\n' >&2
  exit 2
fi

if [[ ! -d "$LOCAL_ROOT" ]]; then
  printf 'error: local root is not a directory: %s\n' "$LOCAL_ROOT" >&2
  exit 2
fi
if [[ -z "$REMOTE" ]]; then
  printf 'error: --remote must not be empty\n' >&2
  exit 2
fi
if [[ -z "$SSH_KEY" || ! -f "$SSH_KEY" ]]; then
  printf 'error: SSH key does not exist\n' >&2
  exit 2
fi

if [[ "$ALLOW_BATTERY" -eq 0 ]]; then
  if ! command -v pmset >/dev/null 2>&1; then
    printf 'error: pmset is required for the AC power check; use --allow-battery to override\n' >&2
    exit 2
  fi
  POWER_STATUS="$(pmset -g batt 2>/dev/null || true)"
  if [[ "$POWER_STATUS" != *"'AC Power'"* ]]; then
    printf 'Skipping local intelligence artifact worker while on battery power.\n'
    exit 0
  fi
fi

STATE_ROOT="$LOCAL_ROOT/.local-intelligence-artifact-worker"
JOBS_ROOT="$STATE_ROOT/jobs"
HISTORY_ROOT="$STATE_ROOT/history"
LOCK_DIR="$STATE_ROOT/worker.lock"
mkdir -p "$JOBS_ROOT" "$HISTORY_ROOT"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  owner_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  owner_host="$(cat "$LOCK_DIR/host" 2>/dev/null || true)"
  current_host="$(hostname -s 2>/dev/null || hostname)"
  if [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] \
    && [[ "$owner_host" == "$current_host" ]] \
    && kill -0 "$owner_pid" 2>/dev/null; then
    printf 'Local intelligence artifact worker is already running; skipping.\n'
    exit 0
  fi
  rm -rf -- "$LOCK_DIR"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    printf 'Local intelligence artifact worker is already running; skipping.\n'
    exit 0
  fi
fi
printf '%s\n' "$$" >"$LOCK_DIR/pid"
hostname -s >"$LOCK_DIR/host" 2>/dev/null || hostname >"$LOCK_DIR/host"
date -u '+%Y-%m-%dT%H:%M:%SZ' >"$LOCK_DIR/started_at"

LOCK_HELD=1
ACTIVE_JOB_DIR=""
cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$LOCK_HELD" -eq 1 ]]; then
    rm -rf -- "$LOCK_DIR"
  fi
  if [[ "$status" -ne 0 && -n "$ACTIVE_JOB_DIR" ]]; then
    printf 'Worker failed; task directory retained at %s\n' "$ACTIVE_JOB_DIR" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

SSH_ARGS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -i "$SSH_KEY"
)
printf -v SSH_KEY_QUOTED '%q' "$SSH_KEY"
RSYNC_RSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -i $SSH_KEY_QUOTED"
JSON_PYTHON="${SA_LOCAL_INTELLIGENCE_JSON_PYTHON:-/usr/bin/python3}"
if [[ ! -x "$JSON_PYTHON" ]]; then
  JSON_PYTHON="$(command -v python3)"
fi
if [[ -n "${SA_LOCAL_INTELLIGENCE_PYTHON:-}" ]]; then
  LOCAL_PYTHON="$SA_LOCAL_INTELLIGENCE_PYTHON"
elif [[ -x "$LOCAL_ROOT/.venv/bin/python" ]]; then
  LOCAL_PYTHON="$LOCAL_ROOT/.venv/bin/python"
else
  LOCAL_PYTHON="$(command -v python3)"
fi

if ! "$LOCAL_PYTHON" -c \
  'import fitz, pdfplumber, pypdf, pytesseract, yaml, httpx' \
  >/dev/null 2>&1; then
  printf '%s\n' \
    'error: local Python is missing PDF worker dependencies' \
    >&2
  exit 2
fi

if [[ "$STAGE" == "parse" ]]; then
  if ! command -v tesseract >/dev/null 2>&1; then
    printf '%s\n' \
      'error: tesseract is required for parse jobs' \
      >&2
    exit 2
  fi
  TESSERACT_LANGUAGES="$(tesseract --list-langs 2>/dev/null || true)"
  for required_language in chi_sim eng; do
    if ! printf '%s\n' "$TESSERACT_LANGUAGES" \
      | grep -Fxq "$required_language"; then
      printf 'error: tesseract language %s is required for parse jobs\n' \
        "$required_language" >&2
      exit 2
    fi
  done
fi

build_remote_command() {
  local argument
  local quoted
  REMOTE_COMMAND=""
  for argument in "$@"; do
    printf -v quoted '%q' "$argument"
    if [[ -n "$REMOTE_COMMAND" ]]; then
      REMOTE_COMMAND+=" "
    fi
    REMOTE_COMMAND+="$quoted"
  done
}

build_remote_python_command() {
  local remote_env_quoted
  local remote_root_quoted
  build_remote_command "$@"
  printf -v remote_root_quoted '%q' "$REMOTE_ROOT"
  printf -v remote_env_quoted '%q' "$REMOTE_ENV_FILE"
  REMOTE_COMMAND="$(
    printf 'cd %s && set -a && . %s && set +a && %s' \
      "$remote_root_quoted" \
      "$remote_env_quoted" \
      "$REMOTE_COMMAND"
  )"
}

run_remote_capture() {
  local command="$1"
  if REMOTE_OUTPUT="$(ssh "${SSH_ARGS[@]}" "$REMOTE" "$command" 2>/dev/null)"; then
    REMOTE_STATUS=0
  else
    REMOTE_STATUS=$?
  fi
}

process_one_job() {
  local export_json
  local job_id
  local remote_job_dir
  local expected_remote_job_dir
  local rsync_remote_job_dir
  local local_job_dir
  local run_log
  local import_json
  local import_status
  local import_job_id
  local receipt
  local receipt_temporary
  local status

  build_remote_python_command \
    "$REMOTE_FLOCK" \
    --nonblock \
    --conflict-exit-code 75 \
    "$REMOTE_STAGE_LOCK" \
    "$REMOTE_PYTHON" -m stock_analyze.cli \
    intelligence-artifact-job-export \
    --repo-root "$REMOTE_ROOT" \
    --stage "$STAGE" \
    --limit "$LIMIT" \
    --worker-id "$WORKER_ID" \
    --lease-seconds "$LEASE_SECONDS"
  run_remote_capture "$REMOTE_COMMAND"
  if [[ "$REMOTE_STATUS" -ne 0 ]]; then
    case "$REMOTE_STATUS" in
      3|75)
        printf 'No %s artifact job is currently available.\n' "$STAGE"
        return 10
        ;;
      *)
        printf 'error: remote artifact job export failed with status %s\n' \
          "$REMOTE_STATUS" >&2
        return "$REMOTE_STATUS"
        ;;
    esac
  fi

  export_json="$(printf '%s\n' "$REMOTE_OUTPUT" | awk 'NF { line=$0 } END { print line }')"
  if ! job_id="$("$JSON_PYTHON" -c '
import json
import sys
payload = json.load(sys.stdin)
value = payload.get("job_id")
print("" if value is None else value)
' <<<"$export_json" 2>/dev/null)"; then
    printf 'error: remote export did not end with valid job JSON\n' >&2
    return 1
  fi
  if ! remote_job_dir="$("$JSON_PYTHON" -c '
import json
import sys
payload = json.load(sys.stdin)
value = payload.get("job_dir")
print("" if value is None else value)
' <<<"$export_json" 2>/dev/null)"; then
    printf 'error: remote export did not end with valid job JSON\n' >&2
    return 1
  fi

  if [[ -z "$job_id" && -z "$remote_job_dir" ]]; then
    printf 'No %s artifact job is currently available.\n' "$STAGE"
    return 10
  fi
  if [[ -z "$job_id" || "$job_id" == "." || "$job_id" == ".." || "$job_id" == */* || "$job_id" == *$'\n'* ]]; then
    printf 'error: remote export returned an unsafe job_id\n' >&2
    return 1
  fi
  if [[ "$remote_job_dir" != /* || "$remote_job_dir" == *$'\n'* ]]; then
    printf 'error: remote export returned a non-absolute job_dir\n' >&2
    return 1
  fi
  expected_remote_job_dir="$REMOTE_ROOT/data/shared/intelligence/artifact_jobs/$job_id"
  if [[ "$remote_job_dir" != "$expected_remote_job_dir" ]]; then
    printf 'error: remote export returned a job_dir outside the control plane\n' >&2
    return 1
  fi

  local_job_dir="$JOBS_ROOT/$job_id"
  mkdir -p "$local_job_dir"
  ACTIVE_JOB_DIR="$local_job_dir"
  printf 'Claimed %s job %s.\n' "$STAGE" "$job_id"

  # macOS ships an rsync 2.6.9-compatible client without --protect-args.
  # Preserve remote spaces by passing shell escapes through to the remote rsync.
  printf -v rsync_remote_job_dir '%q' "$remote_job_dir/"
  rsync --archive --quiet --partial -e "$RSYNC_RSH" \
    "$REMOTE:$rsync_remote_job_dir" "$local_job_dir/" || {
      status=$?
      printf 'error: task package download failed with status %s\n' "$status" >&2
      return "$status"
    }

  run_log="$local_job_dir/local-worker.log"
  "$JSON_PYTHON" - \
    "$LOCAL_ROOT" \
    "$run_log" \
    "$JOB_TIMEOUT_SECONDS" \
    "$LOCAL_PYTHON" \
    -m stock_analyze.cli \
    intelligence-artifact-job-run \
    --repo-root "$LOCAL_ROOT" \
    --job-dir "$local_job_dir" \
    --workers "$WORKERS" <<'PY' || {
import subprocess
import sys

cwd, log_path, timeout, *command = sys.argv[1:]
with open(log_path, "wb") as log:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=int(timeout),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(124)
raise SystemExit(completed.returncode)
PY
      status=$?
      printf 'error: local artifact job failed with status %s\n' "$status" >&2
      return "$status"
    }

  (
    cd "$local_job_dir"
    rsync --archive --quiet --partial --relative -e "$RSYNC_RSH" \
      result.jsonl run_report.json outputs/ \
      "$REMOTE:$rsync_remote_job_dir"
  ) || {
      status=$?
      printf 'error: task result upload failed with status %s\n' "$status" >&2
      return "$status"
    }

  build_remote_python_command \
    "$REMOTE_FLOCK" \
    --wait 1800 \
    "$REMOTE_STAGE_LOCK" \
    "$REMOTE_PYTHON" -m stock_analyze.cli \
    intelligence-artifact-job-import \
    --repo-root "$REMOTE_ROOT" \
    --job-dir "$remote_job_dir"
  run_remote_capture "$REMOTE_COMMAND"
  if [[ "$REMOTE_STATUS" -ne 0 ]]; then
    printf 'error: remote artifact job import failed with status %s\n' \
      "$REMOTE_STATUS" >&2
    return "$REMOTE_STATUS"
  fi

  import_json="$(printf '%s\n' "$REMOTE_OUTPUT" | awk 'NF { line=$0 } END { print line }')"
  if ! import_status="$("$JSON_PYTHON" -c '
import json
import sys
payload = json.load(sys.stdin)
print(payload.get("status") or "")
' <<<"$import_json" 2>/dev/null)"; then
    printf 'error: remote import did not end with valid job JSON\n' >&2
    return 1
  fi
  if ! import_job_id="$("$JSON_PYTHON" -c '
import json
import sys
payload = json.load(sys.stdin)
print(payload.get("job_id") or "")
' <<<"$import_json" 2>/dev/null)"; then
    printf 'error: remote import did not end with valid job JSON\n' >&2
    return 1
  fi
  if [[ "$import_job_id" != "$job_id" ]]; then
    printf 'error: remote import returned a mismatched job_id\n' >&2
    return 1
  fi
  receipt="$HISTORY_ROOT/$job_id.import.json"
  receipt_temporary="$HISTORY_ROOT/.$job_id.import.json.$$"
  printf '%s\n' "$import_json" >"$receipt_temporary"
  mv "$receipt_temporary" "$receipt"
  if [[ "$import_status" == "partial" ]]; then
    printf 'Imported %s job %s partially; stopping before retry.\n' \
      "$STAGE" "$job_id" >&2
    return 11
  fi
  if [[ "$import_status" != "imported" ]]; then
    printf 'error: remote import returned unexpected status %s\n' \
      "$import_status" >&2
    return 1
  fi
  rm -rf -- "$local_job_dir"
  printf 'Imported %s job %s on ECS.\n' "$STAGE" "$job_id"
  ACTIVE_JOB_DIR=""
  return 0
}

STARTED_EPOCH="$(date +%s)"
COMPLETED_JOBS=0
while true; do
  current_epoch="$(date +%s)"
  if (( current_epoch - STARTED_EPOCH >= MAX_RUNTIME_SECONDS )); then
    printf 'Reached the %s second runtime limit after %s jobs.\n' \
      "$MAX_RUNTIME_SECONDS" "$COMPLETED_JOBS"
    break
  fi
  if (( COMPLETED_JOBS >= MAX_JOBS )); then
    printf 'Reached the %s job invocation limit.\n' "$MAX_JOBS"
    break
  fi
  if process_one_job; then
    COMPLETED_JOBS=$((COMPLETED_JOBS + 1))
    if [[ "$ONCE" -eq 1 ]]; then
      break
    fi
    if [[ "$INTER_JOB_DELAY_SECONDS" -gt 0 ]]; then
      sleep "$INTER_JOB_DELAY_SECONDS"
    fi
  else
    status=$?
    if [[ "$status" -eq 10 ]]; then
      break
    fi
    if [[ "$status" -eq 11 ]]; then
      exit 3
    fi
    exit "$status"
  fi
done
