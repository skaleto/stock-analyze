#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

readonly DASHBOARD_FILES=(
  "stock_analyze/cli.py"
  "stock_analyze/dashboard_api.py"
  "stock_analyze/dashboard_finance.py"
  "stock_analyze/dashboard_http.py"
  "stock_analyze/dashboard_workspace_api.py"
  "stock_analyze/dashboard_runtime.py"
  "tests/test_cli_dashboard_routes.py"
  "tests/test_dashboard_finance.py"
  "tests/test_dashboard_http.py"
  "tests/test_dashboard_resource_api.py"
  "tests/test_dashboard_runtime.py"
  "tests/test_dashboard_workspace_api.py"
)
readonly DASHBOARD_ASSET_TREE="reports/app"
readonly DASHBOARD_SERVICE="stock-analyze-dashboard.service"
readonly DASHBOARD_TEST_MODULES=(
  "tests.test_dashboard_finance"
  "tests.test_dashboard_http"
  "tests.test_dashboard_resource_api"
  "tests.test_dashboard_runtime"
  "tests.test_dashboard_workspace_api"
  "tests.test_cli_dashboard_routes"
)
readonly DASHBOARD_CANARY_ENDPOINTS=(
  "/api/dashboard/system-overview.json"
  "/api/dashboard/model-research.json?market=a_share"
  "/api/dashboard/data-intelligence.json?market=a_share"
  "/api/dashboard/operations-center.json?scope=all"
  "/app.html?view=system"
)

REMOTE_HOST=""
REMOTE_PATH=""
REMOTE_TARGET=""
REMOTE_RELEASE_ROOT=""
REMOTE_PYTHON=""
RELEASE_STAMP=""
BACKUP_DIR=""
PREIMAGE_MANIFEST=""
LOCAL_RELEASE_DIR=""
LOCAL_RELEASE_MANIFEST=""
MAX_BYTES=""
MAX_TTFB_SECONDS=""
CANARY_BASE_URL=""
BACKUP_READY=0
SYNC_STARTED=0
SSH_COMMAND=(ssh)

usage() {
  cat <<'EOF'
Usage:
  deploy-dashboard-workspaces-to-ecs.sh capture-preimage
  deploy-dashboard-workspaces-to-ecs.sh validate-manifest <file>
  deploy-dashboard-workspaces-to-ecs.sh deploy

Required for deploy:
  SA_ECS_REMOTE=user@host:/absolute/app/path
  SA_DASHBOARD_PREIMAGE_MANIFEST=/path/to/reviewed-preimage.manifest

Optional:
  SA_ECS_SSH_HOST=user@host
  SA_ECS_REMOTE_PATH=/absolute/app/path
  SA_ECS_SSH_OPTS='-i /path/to/key'
  RSYNC_RSH='ssh -i /path/to/key'
  SA_DASHBOARD_RELEASES_DIR=/absolute/releases/path
  SA_ECS_PYTHON=/absolute/path/to/python
  SA_DASHBOARD_RELEASE_STAMP=YYYYMMDD-HHMMSS
  SA_DASHBOARD_MAX_BYTES=250000
  SA_DASHBOARD_MAX_TTFB_SECONDS=0.500
  SA_DASHBOARD_CANARY_BASE_URL=http://127.0.0.1:8765

Preimage manifest format:
  FILE <64-char-sha256|MISSING> <relative-path>
  TREE <64-char-sha256|MISSING> reports/app

The manifest must contain every allowlisted file exactly once and one reports/app
tree entry. The deployment refuses to build or connect when the local contract
is incomplete.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

validate_manifest_file() {
  local manifest="$1"
  [[ -f "$manifest" ]] || die "preimage manifest not found: $manifest"

  python3 - "$manifest" "$DASHBOARD_ASSET_TREE" "${DASHBOARD_FILES[@]}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
tree_path = sys.argv[2]
file_paths = sys.argv[3:]
expected = {path: "FILE" for path in file_paths}
expected[tree_path] = "TREE"
entries: dict[str, tuple[str, str]] = {}

for line_number, raw_line in enumerate(
    manifest_path.read_text(encoding="utf-8").splitlines(),
    start=1,
):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split()
    if len(parts) != 3:
        raise SystemExit(
            f"manifest line {line_number}: expected KIND HASH PATH"
        )
    kind, digest, relative = parts
    if kind not in {"FILE", "TREE"}:
        raise SystemExit(f"manifest line {line_number}: invalid kind {kind}")
    if digest != "MISSING" and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(f"manifest line {line_number}: invalid sha256")
    if relative in entries:
        raise SystemExit(f"manifest line {line_number}: duplicate {relative}")
    entries[relative] = (kind, digest)

if set(entries) != set(expected):
    missing = sorted(set(expected) - set(entries))
    extra = sorted(set(entries) - set(expected))
    raise SystemExit(f"manifest allowlist mismatch: missing={missing} extra={extra}")

for relative, expected_kind in expected.items():
    actual_kind, _digest = entries[relative]
    if actual_kind != expected_kind:
        raise SystemExit(
            f"manifest kind mismatch for {relative}: "
            f"expected={expected_kind} actual={actual_kind}"
        )
PY
}

configure_remote() {
  local remote_no_slash="${SA_ECS_REMOTE%/}"
  [[ "$remote_no_slash" == *:* ]] \
    || die "SA_ECS_REMOTE must be user@host:/absolute/app/path"

  REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
  REMOTE_PATH="${SA_ECS_REMOTE_PATH:-${remote_no_slash#*:}}"
  [[ -n "$REMOTE_HOST" && "$REMOTE_HOST" != -* ]] \
    || die "invalid remote host"
  [[ "$REMOTE_HOST" != *[[:space:]]* ]] || die "invalid remote host"
  [[ "$REMOTE_PATH" == /* && "$REMOTE_PATH" != *[[:space:]]* ]] \
    || die "SA_ECS_REMOTE_PATH must be an absolute path without whitespace"
  [[ "/$REMOTE_PATH/" != *"/../"* && "/$REMOTE_PATH/" != *"/./"* ]] \
    || die "SA_ECS_REMOTE_PATH must not contain dot segments"

  REMOTE_TARGET="${REMOTE_HOST}:${REMOTE_PATH}"
  REMOTE_RELEASE_ROOT="${
    SA_DASHBOARD_RELEASES_DIR:-$(dirname "$REMOTE_PATH")/releases
  }"
  [[ "$REMOTE_RELEASE_ROOT" == /* ]] \
    || die "SA_DASHBOARD_RELEASES_DIR must be absolute"
  REMOTE_PYTHON="${
    SA_ECS_PYTHON:-$(dirname "$REMOTE_PATH")/venv/bin/python
  }"
  [[ "$REMOTE_PYTHON" == /* ]] || die "SA_ECS_PYTHON must be absolute"

  RELEASE_STAMP="${
    SA_DASHBOARD_RELEASE_STAMP:-$(date -u +%Y%m%d-%H%M%S)
  }"
  [[ "$RELEASE_STAMP" =~ ^[A-Za-z0-9._-]+$ ]] \
    || die "SA_DASHBOARD_RELEASE_STAMP contains unsupported characters"
  BACKUP_DIR="${REMOTE_RELEASE_ROOT}/${RELEASE_STAMP}-dashboard-workspaces"

  if [[ -n "${SA_ECS_SSH_OPTS:-}" ]]; then
    local parsed_options=()
    read -r -a parsed_options <<<"$SA_ECS_SSH_OPTS"
    SSH_COMMAND+=("${parsed_options[@]}")
    if [[ -z "${RSYNC_RSH:-}" ]]; then
      export RSYNC_RSH="ssh ${SA_ECS_SSH_OPTS}"
    fi
  fi
}

capture_remote_preimage() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- \
      "$REMOTE_PATH" \
      "${DASHBOARD_FILES[@]}" \
      -- \
      "$DASHBOARD_ASSET_TREE" <<'REMOTE'
set -euo pipefail

app_dir="$1"
shift
file_paths=()
while [[ "$#" -gt 0 && "$1" != "--" ]]; do
  file_paths+=("$1")
  shift
done
[[ "${1:-}" == "--" ]] || exit 2
shift
tree_path="$1"

cd "$app_dir"
for relative in "${file_paths[@]}"; do
  if [[ -f "$relative" ]]; then
    digest="$(sha256sum "$relative" | awk '{print $1}')"
  else
    digest="MISSING"
  fi
  printf 'FILE %s %s\n' "$digest" "$relative"
done

if [[ -d "$tree_path" ]]; then
  tree_digest="$(
    (
      cd "$tree_path"
      while IFS= read -r -d '' file; do
        digest="$(sha256sum "$file" | awk '{print $1}')"
        printf '%s  %s\n' "$digest" "${file#./}"
      done < <(find . -type f -print0 | LC_ALL=C sort -z)
    ) | sha256sum | awk '{print $1}'
  )"
else
  tree_digest="MISSING"
fi
printf 'TREE %s %s\n' "$tree_digest" "$tree_path"
REMOTE
}

validate_local_contract() {
  [[ -n "${SA_ECS_REMOTE:-}" ]] \
    || die "SA_ECS_REMOTE must be user@host:/absolute/app/path"
  [[ -n "${SA_DASHBOARD_PREIMAGE_MANIFEST:-}" ]] \
    || die "SA_DASHBOARD_PREIMAGE_MANIFEST is required"
  [[ -f "$SA_DASHBOARD_PREIMAGE_MANIFEST" ]] \
    || die "preimage manifest not found: $SA_DASHBOARD_PREIMAGE_MANIFEST"

  LOCAL_RELEASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dashboard-release.XXXXXX")"
  PREIMAGE_MANIFEST="$LOCAL_RELEASE_DIR/expected-preimage.manifest"
  cp -- "$SA_DASHBOARD_PREIMAGE_MANIFEST" "$PREIMAGE_MANIFEST"
  validate_manifest_file "$PREIMAGE_MANIFEST"
  configure_remote

  MAX_BYTES="${SA_DASHBOARD_MAX_BYTES:-250000}"
  MAX_TTFB_SECONDS="${SA_DASHBOARD_MAX_TTFB_SECONDS:-0.500}"
  CANARY_BASE_URL="${
    SA_DASHBOARD_CANARY_BASE_URL:-http://127.0.0.1:8765
  }"
  [[ "$MAX_BYTES" =~ ^[1-9][0-9]*$ ]] \
    || die "SA_DASHBOARD_MAX_BYTES must be a positive integer"
  [[ "$MAX_TTFB_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] \
    || die "SA_DASHBOARD_MAX_TTFB_SECONDS must be numeric"
  [[ "$CANARY_BASE_URL" =~ ^https?://[^[:space:]]+$ ]] \
    || die "SA_DASHBOARD_CANARY_BASE_URL must be an HTTP(S) URL"

  local relative
  for relative in "${DASHBOARD_FILES[@]}"; do
    [[ -f "$REPO_ROOT/$relative" ]] \
      || die "allowlisted release file is missing: $relative"
  done
}

write_local_release_manifest() {
  LOCAL_RELEASE_MANIFEST="$LOCAL_RELEASE_DIR/release-input.manifest"
  python3 - \
    "$REPO_ROOT" \
    "$LOCAL_RELEASE_MANIFEST" \
    "$DASHBOARD_ASSET_TREE" \
    "${DASHBOARD_FILES[@]}" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
output = Path(sys.argv[2])
tree_relative = sys.argv[3]
file_relatives = sys.argv[4:]


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(
        (candidate for candidate in path.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    ):
        relative = file_path.relative_to(path).as_posix()
        line = f"{file_digest(file_path)}  {relative}\n"
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


lines = [
    f"FILE {file_digest(root / relative)} {relative}"
    for relative in file_relatives
]
lines.append(f"TREE {tree_digest(root / tree_relative)} {tree_relative}")
output.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

build_dashboard_assets() {
  "$SCRIPT_DIR/build-dashboard-app.sh"
  [[ -f "$REPO_ROOT/$DASHBOARD_ASSET_TREE/index.html" ]] \
    || die "dashboard build did not create reports/app/index.html"

  write_local_release_manifest
}

prepare_remote_backup() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- "$BACKUP_DIR" <<'REMOTE'
set -euo pipefail

backup_dir="$1"
umask 077
if [[ -e "$backup_dir" ]]; then
  printf 'release backup already exists: %s\n' "$backup_dir" >&2
  exit 3
fi
mkdir -p "$backup_dir"
REMOTE

  rsync -az \
    "$PREIMAGE_MANIFEST" \
    "$REMOTE_HOST:$BACKUP_DIR/expected-preimage.manifest"
  rsync -az \
    "$LOCAL_RELEASE_MANIFEST" \
    "$REMOTE_HOST:$BACKUP_DIR/release-input.manifest"

  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- "$REMOTE_PATH" "$BACKUP_DIR" <<'REMOTE'
set -euo pipefail

app_dir="$1"
backup_dir="$2"
expected_manifest="$backup_dir/expected-preimage.manifest"
actual_manifest="$backup_dir/actual-preimage.manifest"
preexisting_manifest="$backup_dir/preexisting-files.txt"
missing_manifest="$backup_dir/missing-files.txt"

hash_file() {
  sha256sum "$1" | awk '{print $1}'
}

hash_tree() {
  local relative="$1"
  (
    cd "$app_dir/$relative"
    while IFS= read -r -d '' file; do
      digest="$(sha256sum "$file" | awk '{print $1}')"
      printf '%s  %s\n' "$digest" "${file#./}"
    done < <(find . -type f -print0 | LC_ALL=C sort -z)
  ) | sha256sum | awk '{print $1}'
}

cd "$app_dir"
: >"$actual_manifest"
: >"$preexisting_manifest"
: >"$missing_manifest"

while read -r kind expected relative extra; do
  [[ "${kind:-}" == \#* ]] && continue
  [[ -z "${extra:-}" ]] || {
    printf 'invalid preimage row for %s\n' "$relative" >&2
    exit 3
  }
  [[ -n "${kind:-}" ]] || continue
  [[ "$kind" == "FILE" || "$kind" == "TREE" ]] || {
    printf 'invalid preimage kind: %s\n' "$kind" >&2
    exit 3
  }
  [[ "$relative" != /* && "$relative" != *".."* ]] || {
    printf 'unsafe preimage path: %s\n' "$relative" >&2
    exit 3
  }

  if [[ ! -e "$relative" ]]; then
    actual="MISSING"
  elif [[ "$kind" == "FILE" && -f "$relative" ]]; then
    actual="$(hash_file "$relative")"
  elif [[ "$kind" == "TREE" && -d "$relative" ]]; then
    actual="$(hash_tree "$relative")"
  else
    actual="TYPE_MISMATCH"
  fi
  printf '%s %s %s\n' "$kind" "$actual" "$relative" >>"$actual_manifest"
  if [[ "$actual" != "$expected" ]]; then
    printf 'preimage mismatch: %s expected=%s actual=%s\n' \
      "$relative" "$expected" "$actual" >&2
    exit 3
  fi

  if [[ "$actual" == "MISSING" ]]; then
    printf '%s\n' "$relative" >>"$missing_manifest"
  else
    printf '%s\n' "$relative" >>"$preexisting_manifest"
    cp -a --parents "$relative" "$backup_dir/root/"
  fi
done <"$expected_manifest"

printf 'status=backup_ready\n' >"$backup_dir/backup-status.txt"
REMOTE

  BACKUP_READY=1
}

sync_dashboard_release() {
  SYNC_STARTED=1
  cd "$REPO_ROOT"
  rsync -az --relative \
    "${DASHBOARD_FILES[@]}" \
    "$REMOTE_TARGET/"
  rsync -az --delete \
    "$REPO_ROOT/$DASHBOARD_ASSET_TREE/" \
    "$REMOTE_TARGET/$DASHBOARD_ASSET_TREE/"
}

verify_remote_release() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- \
      "$REMOTE_PATH" \
      "$BACKUP_DIR" \
      "$REMOTE_PYTHON" \
      "$DASHBOARD_SERVICE" \
      "$MAX_BYTES" \
      "$MAX_TTFB_SECONDS" \
      "$CANARY_BASE_URL" \
      "${DASHBOARD_TEST_MODULES[@]}" \
      -- \
      "${DASHBOARD_CANARY_ENDPOINTS[@]}" <<'REMOTE'
set -euo pipefail

app_dir="$1"
backup_dir="$2"
python_bin="$3"
service_name="$4"
max_bytes="$5"
max_ttfb="$6"
base_url="$7"
shift 7

test_modules=()
while [[ "$#" -gt 0 && "$1" != "--" ]]; do
  test_modules+=("$1")
  shift
done
[[ "${1:-}" == "--" ]] || {
  printf 'missing canary separator\n' >&2
  exit 4
}
shift
canary_endpoints=("$@")

hash_tree() {
  local relative="$1"
  (
    cd "$app_dir/$relative"
    while IFS= read -r -d '' file; do
      digest="$(sha256sum "$file" | awk '{print $1}')"
      printf '%s  %s\n' "$digest" "${file#./}"
    done < <(find . -type f -print0 | LC_ALL=C sort -z)
  ) | sha256sum | awk '{print $1}'
}

cd "$app_dir"
while read -r kind expected relative extra; do
  [[ -z "${extra:-}" ]] || exit 4
  if [[ "$kind" == "FILE" ]]; then
    actual="$(sha256sum "$relative" | awk '{print $1}')"
  else
    actual="$(hash_tree "$relative")"
  fi
  if [[ "$actual" != "$expected" ]]; then
    printf 'release input mismatch: %s expected=%s actual=%s\n' \
      "$relative" "$expected" "$actual" >&2
    exit 4
  fi
done <"$backup_dir/release-input.manifest"

"$python_bin" -m unittest "${test_modules[@]}" -v
systemctl restart "$service_name"
systemctl is-active --quiet "$service_name"

canary_file="$backup_dir/canary-results.tsv"
printf 'endpoint\thttp_code\tbytes\twarm_ttfb_seconds\n' >"$canary_file"
for endpoint in "${canary_endpoints[@]}"; do
  url="${base_url%/}${endpoint}"
  curl --fail --silent --show-error --output /dev/null \
    --retry 20 --retry-connrefused --retry-delay 1 --max-time 120 \
    "$url"
  metrics="$(
    curl --silent --show-error --output /dev/null \
      --write-out '%{http_code}\t%{size_download}\t%{time_starttransfer}' \
      "$url"
  )"
  IFS=$'\t' read -r http_code size_download time_starttransfer <<<"$metrics"
  [[ "$http_code" == "200" ]] || {
    printf 'canary HTTP failure: %s status=%s\n' "$endpoint" "$http_code" >&2
    exit 5
  }
  [[ "$size_download" =~ ^[0-9]+$ && "$size_download" -le "$max_bytes" ]] || {
    printf 'canary payload too large: %s bytes=%s limit=%s\n' \
      "$endpoint" "$size_download" "$max_bytes" >&2
    exit 5
  }
  awk -v actual="$time_starttransfer" -v limit="$max_ttfb" \
    'BEGIN { exit !(actual <= limit) }' || {
      printf 'canary latency too high: %s ttfb=%s limit=%s\n' \
        "$endpoint" "$time_starttransfer" "$max_ttfb" >&2
      exit 5
    }
  printf '%s\t%s\t%s\t%s\n' \
    "$endpoint" "$http_code" "$size_download" "$time_starttransfer" \
    >>"$canary_file"
done
REMOTE
}

write_remote_manifest() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- \
      "$BACKUP_DIR" \
      "$REMOTE_PATH" \
      "$DASHBOARD_SERVICE" \
      "$RELEASE_STAMP" <<'REMOTE'
set -euo pipefail

backup_dir="$1"
app_dir="$2"
service_name="$3"
release_stamp="$4"
preimage_sha="$(sha256sum "$backup_dir/expected-preimage.manifest" | awk '{print $1}')"
release_sha="$(sha256sum "$backup_dir/release-input.manifest" | awk '{print $1}')"
canary_sha="$(sha256sum "$backup_dir/canary-results.tsv" | awk '{print $1}')"

cat >"$backup_dir/release-manifest.txt" <<EOF
format=dashboard-workspaces-release-v1
status=deployed
release_stamp=$release_stamp
app_dir=$app_dir
backup_dir=$backup_dir
service=$service_name
preimage_manifest_sha256=$preimage_sha
release_input_manifest_sha256=$release_sha
canary_results_sha256=$canary_sha
rollback_scope=dashboard-files-and-reports-app-only
EOF
REMOTE

  printf 'dashboard_release=%s\n' "$RELEASE_STAMP"
  printf 'rollback_backup=%s\n' "$BACKUP_DIR"
  printf 'release_manifest=%s/release-manifest.txt\n' "$BACKUP_DIR"
}

restore_remote_backup() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- \
      "$REMOTE_PATH" \
      "$BACKUP_DIR" \
      "$DASHBOARD_SERVICE" \
      "$RELEASE_STAMP" <<'REMOTE'
set -euo pipefail

app_dir="$1"
backup_dir="$2"
service_name="$3"
release_stamp="$4"
preexisting="$backup_dir/preexisting-files.txt"
missing="$backup_dir/missing-files.txt"

while IFS= read -r relative; do
  [[ -n "$relative" ]] || continue
  rm -rf "$app_dir/$relative"
  mkdir -p "$(dirname "$app_dir/$relative")"
  cp -a "$backup_dir/root/$relative" "$app_dir/$relative"
done <"$preexisting"

while IFS= read -r relative; do
  [[ -n "$relative" ]] || continue
  rm -rf "$app_dir/$relative"
done <"$missing"

systemctl restart "$service_name"
systemctl is-active --quiet "$service_name"
cat >"$backup_dir/release-manifest.txt" <<EOF
format=dashboard-workspaces-release-v1
status=rolled_back
release_stamp=$release_stamp
app_dir=$app_dir
backup_dir=$backup_dir
service=$service_name
rollback_scope=dashboard-files-and-reports-app-only
EOF
REMOTE
}

handle_exit() {
  local exit_code=$?
  trap - EXIT

  if [[ "$exit_code" -ne 0 && "$SYNC_STARTED" -eq 1 && "$BACKUP_READY" -eq 1 ]]; then
    printf 'deployment failed; restoring Dashboard-only backup %s\n' \
      "$BACKUP_DIR" >&2
    if ! restore_remote_backup; then
      printf 'automatic rollback failed; inspect %s manually\n' \
        "$BACKUP_DIR" >&2
    fi
  fi
  if [[ -n "$LOCAL_RELEASE_DIR" && -d "$LOCAL_RELEASE_DIR" ]]; then
    rm -rf "$LOCAL_RELEASE_DIR"
  fi
  exit "$exit_code"
}

main() {
  local command="${1:-deploy}"
  case "$command" in
    -h|--help|help)
      usage
      ;;
    validate-manifest)
      [[ "$#" -eq 2 ]] || die "validate-manifest requires one file"
      validate_manifest_file "$2"
      printf 'manifest_valid=%s\n' "$2"
      ;;
    capture-preimage)
      [[ "$#" -eq 1 ]] || die "capture-preimage does not accept arguments"
      [[ -n "${SA_ECS_REMOTE:-}" ]] \
        || die "SA_ECS_REMOTE must be user@host:/absolute/app/path"
      configure_remote
      capture_remote_preimage
      ;;
    deploy)
      [[ "$#" -eq 1 ]] || die "deploy does not accept positional arguments"
      trap handle_exit EXIT
      validate_local_contract
      build_dashboard_assets
      prepare_remote_backup
      sync_dashboard_release
      verify_remote_release
      write_remote_manifest
      ;;
    *)
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
