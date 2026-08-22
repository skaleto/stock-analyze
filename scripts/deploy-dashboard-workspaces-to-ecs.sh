#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

readonly DASHBOARD_FILES=(
  "configs/research/paper_candidate_gate_v1.yaml"
  "configs/research/production_paper_challengers_v1.yaml"
  "configs/research/qdii_global_context_v1.yaml"
  "configs/research/scenario_model_v1.yaml"
  "stock_analyze/cli.py"
  "stock_analyze/intelligence/factors.py"
  "stock_analyze/dashboard_aggregator.py"
  "stock_analyze/dashboard_api.py"
  "stock_analyze/dashboard_finance.py"
  "stock_analyze/dashboard_http.py"
  "stock_analyze/dashboard_multi_agent_research.py"
  "stock_analyze/dashboard_workspace_api.py"
  "stock_analyze/dashboard_runtime.py"
  "stock_analyze/model_iteration.py"
  "stock_analyze/model_shadow.py"
  "stock_analyze/markets/_settlement_simulator.py"
  "stock_analyze/markets/a_share/backtest/data_prep.py"
  "stock_analyze/markets/cn_qdii_etf/mechanics.py"
  "stock_analyze/markets/cn_qdii_etf/data_provider.py"
  "stock_analyze/research/activation.py"
  "stock_analyze/research/classical_specs.py"
  "stock_analyze/research/local_training.py"
  "stock_analyze/research/multi_agent_workflow.py"
  "stock_analyze/research/models.py"
  "stock_analyze/research/pipeline.py"
  "stock_analyze/research/portfolio_replay.py"
  "stock_analyze/research/paper_candidate_gate.py"
  "stock_analyze/research/paper_candidate_runtime.py"
  "stock_analyze/research/qdii_global_context.py"
  "stock_analyze/research/scenario_model.py"
  "stock_analyze/research/shadow_admission.py"
  "stock_analyze/research/storage.py"
  "stock_analyze/research/strategy_campaign.py"
  "stock_analyze/research/account_features.py"
  "stock_analyze/research/a_share_research_prices.py"
  "stock_analyze/research/technical_features.py"
  "stock_analyze/research/otc_fund_nav.py"
  "stock_analyze/research/universe_expansion.py"
  "tests/test_backtest_data_prep.py"
  "tests/test_cli_dashboard_routes.py"
  "tests/test_cli_research.py"
  "tests/test_dashboard_multi_agent_research.py"
  "tests/test_dashboard_model_shadow.py"
  "tests/test_dashboard_finance.py"
  "tests/test_dashboard_http.py"
  "tests/test_dashboard_resource_api.py"
  "tests/test_dashboard_runtime.py"
  "tests/test_dashboard_workspace_api.py"
  "tests/test_model_iteration.py"
  "tests/test_model_shadow.py"
  "tests/test_markets_cn_qdii_etf_simulator.py"
  "tests/test_research_activation.py"
  "tests/test_research_classical_specs.py"
  "tests/test_research_local_training.py"
  "tests/test_research_models.py"
  "tests/test_multi_agent_research.py"
  "tests/test_research_pipeline.py"
  "tests/test_research_portfolio_replay.py"
  "tests/test_research_paper_candidate_gate.py"
  "tests/test_research_paper_candidate_runtime.py"
  "tests/test_research_qdii_global_context.py"
  "tests/test_research_collectors.py"
  "tests/test_research_source_features.py"
  "tests/test_research_scenario_model.py"
  "tests/test_research_shadow_admission.py"
  "tests/test_research_storage.py"
  "tests/test_research_strategy_campaign.py"
  "tests/test_intelligence_factors.py"
  "tests/test_research_account_features.py"
  "tests/test_research_tabular_forward.py"
  "tests/test_research_a_share_prices.py"
  "tests/test_research_otc_fund_nav.py"
  "tests/test_research_universe_expansion.py"
  "scripts/system-audit.sh"
  "scripts/check-ecs-timers.sh"
  "docs/system-harness.md"
  "docs/system-overview.md"
)
readonly DASHBOARD_ASSET_TREE="reports/app"
readonly DEPLOY_VERSION_FILE="DEPLOY_VERSION"
readonly DASHBOARD_BUILD_INPUTS=(
  "frontend/dashboard"
  "scripts/deploy-dashboard-workspaces-to-ecs.sh"
  "scripts/build-dashboard-app.sh"
)
readonly DASHBOARD_SERVICE="stock-analyze-dashboard.service"
readonly RELEASE_INPUT_FORMAT="dashboard-workspaces-release-input-v1"
readonly DASHBOARD_TEST_MODULES=(
  "tests.test_backtest_data_prep"
  "tests.test_dashboard_finance"
  "tests.test_dashboard_http"
  "tests.test_dashboard_multi_agent_research"
  "tests.test_dashboard_resource_api"
  "tests.test_dashboard_runtime"
  "tests.test_dashboard_workspace_api"
  "tests.test_cli_dashboard_routes"
  "tests.test_cli_research"
  "tests.test_intelligence_factors"
  "tests.test_dashboard_model_shadow"
  "tests.test_model_iteration"
  "tests.test_model_shadow"
  "tests.test_markets_cn_qdii_etf_simulator"
  "tests.test_research_activation"
  "tests.test_research_classical_specs"
  "tests.test_research_local_training"
  "tests.test_research_models"
  "tests.test_multi_agent_research"
  "tests.test_research_pipeline"
  "tests.test_research_portfolio_replay"
  "tests.test_research_paper_candidate_gate"
  "tests.test_research_paper_candidate_runtime"
  "tests.test_research_qdii_global_context"
  "tests.test_research_collectors"
  "tests.test_research_source_features"
  "tests.test_research_scenario_model"
  "tests.test_research_shadow_admission"
  "tests.test_research_storage"
  "tests.test_research_strategy_campaign"
  "tests.test_research_account_features"
  "tests.test_research_tabular_forward"
  "tests.test_research_a_share_prices"
  "tests.test_research_otc_fund_nav"
  "tests.test_research_universe_expansion"
)
readonly DASHBOARD_CANARY_ENDPOINTS=(
  "/api/dashboard/system-overview.json"
  "/api/dashboard/model-research.json?market=a_share"
  "/api/dashboard/multi-agent-research.json"
  "/api/dashboard/data-intelligence.json?market=a_share"
  "/api/dashboard/operations-center.json?scope=all"
  "/api/dashboard/research-universe.json?kind=a_share&page=1&page_size=20"
  "/api/dashboard/research-universe-instrument.json?kind=a_share&code=000001.SZ"
  "/api/dashboard/research-universe-instrument.json?kind=otc_fund&code=006426.OF"
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
REVIEWED_RELEASE_MANIFEST=""
CURRENT_COMMIT=""
MAX_BYTES=""
MAX_TTFB_SECONDS=""
CANARY_BASE_URL=""
REMOTE_LOCK_DIR=""
BACKUP_READY=0
SYNC_STARTED=0
LOCK_HELD=0
SSH_COMMAND=(ssh)

usage() {
  cat <<'EOF'
Usage:
  deploy-dashboard-workspaces-to-ecs.sh capture-preimage
  deploy-dashboard-workspaces-to-ecs.sh capture-release-input
  deploy-dashboard-workspaces-to-ecs.sh validate-config
  deploy-dashboard-workspaces-to-ecs.sh validate-manifest <file>
  deploy-dashboard-workspaces-to-ecs.sh validate-release-input <file>
  deploy-dashboard-workspaces-to-ecs.sh deploy

Required for deploy:
  SA_ECS_REMOTE=user@host:/absolute/app/path
  SA_DASHBOARD_PREIMAGE_MANIFEST=/path/to/reviewed-preimage.manifest
  SA_DASHBOARD_RELEASE_INPUT_MANIFEST=/path/to/reviewed-release-input.manifest

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

Reviewed release-input manifest format:
  FORMAT dashboard-workspaces-release-input-v1
  COMMIT <40-char-git-commit>
  FILE <64-char-sha256> <relative-path>
  TREE <64-char-sha256> reports/app

Both manifests must contain every allowlisted file exactly once and one
reports/app tree entry. The release-input manifest must be reviewed outside the
deploy command. Tracked release files, frontend sources and build scripts must
match the current commit; the generated reports/app directory is bound by its
reviewed tree digest. The deployment refuses to connect when the local contract
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

  python3 - \
    "$manifest" \
    "$DASHBOARD_ASSET_TREE" \
    "$DEPLOY_VERSION_FILE" \
    "${DASHBOARD_FILES[@]}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
tree_path = sys.argv[2]
deploy_version_path = sys.argv[3]
file_paths = [deploy_version_path, *sys.argv[4:]]
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

validate_release_manifest_file() {
  local manifest="$1"
  [[ -f "$manifest" ]] || die "reviewed release-input manifest not found: $manifest"

  python3 - \
    "$manifest" \
    "$RELEASE_INPUT_FORMAT" \
    "$DASHBOARD_ASSET_TREE" \
    "${DASHBOARD_FILES[@]}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
expected_format = sys.argv[2]
tree_path = sys.argv[3]
file_paths = sys.argv[4:]
expected = {path: "FILE" for path in file_paths}
expected[tree_path] = "TREE"
entries: dict[str, tuple[str, str]] = {}
format_value: str | None = None
commit: str | None = None

for line_number, raw_line in enumerate(
    manifest_path.read_text(encoding="utf-8").splitlines(),
    start=1,
):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split()
    if parts[0] == "FORMAT":
        if len(parts) != 2 or format_value is not None:
            raise SystemExit(f"release manifest line {line_number}: invalid FORMAT")
        format_value = parts[1]
        continue
    if parts[0] == "COMMIT":
        if len(parts) != 2 or commit is not None:
            raise SystemExit(f"release manifest line {line_number}: invalid COMMIT")
        commit = parts[1]
        continue
    if len(parts) != 3:
        raise SystemExit(
            f"release manifest line {line_number}: expected KIND HASH PATH"
        )
    kind, digest, relative = parts
    if kind not in {"FILE", "TREE"}:
        raise SystemExit(
            f"release manifest line {line_number}: invalid kind {kind}"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(
            f"release manifest line {line_number}: invalid sha256"
        )
    if relative in entries:
        raise SystemExit(
            f"release manifest line {line_number}: duplicate {relative}"
        )
    entries[relative] = (kind, digest)

if format_value != expected_format:
    raise SystemExit(
        f"release manifest format mismatch: expected={expected_format} "
        f"actual={format_value}"
    )
if commit is None or not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("release manifest COMMIT must be a 40-char git commit")
if set(entries) != set(expected):
    missing = sorted(set(expected) - set(entries))
    extra = sorted(set(entries) - set(expected))
    raise SystemExit(
        f"release manifest allowlist mismatch: missing={missing} extra={extra}"
    )
for relative, expected_kind in expected.items():
    actual_kind, _digest = entries[relative]
    if actual_kind != expected_kind:
        raise SystemExit(
            f"release manifest kind mismatch for {relative}: "
            f"expected={expected_kind} actual={actual_kind}"
        )
PY
}

validate_safe_absolute_path() {
  local variable_name="$1"
  local value="$2"

  [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || die "$variable_name must be an absolute path using only safe characters"
  [[ "$value" != "/" && "$value" != *"//"* ]] \
    || die "$variable_name must be a non-root canonical path"
  [[ "/$value/" != *"/../"* && "/$value/" != *"/./"* ]] \
    || die "$variable_name must not contain dot segments"
}

paths_overlap() {
  local first="${1%/}"
  local second="${2%/}"
  [[ "$first" == "$second" || "$first" == "$second/"* || "$second" == "$first/"* ]]
}

configure_remote() {
  local remote_no_slash="${SA_ECS_REMOTE%/}"
  local parsed_host=""
  local parsed_path=""
  if [[ "$remote_no_slash" =~ ^([A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*):(/[A-Za-z0-9._/-]+)$ ]]; then
    parsed_host="${BASH_REMATCH[1]}"
    parsed_path="${BASH_REMATCH[2]}"
  else
    die "invalid remote host/path; SA_ECS_REMOTE must be user@host:/absolute/app/path"
  fi

  REMOTE_HOST="${SA_ECS_SSH_HOST:-$parsed_host}"
  REMOTE_PATH="${SA_ECS_REMOTE_PATH:-$parsed_path}"
  [[ "$REMOTE_HOST" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*$ ]] \
    || die "invalid remote host"
  [[ "$REMOTE_HOST" != *..* && "$REMOTE_HOST" != *. && "$REMOTE_HOST" != *- ]] \
    || die "invalid remote host"
  validate_safe_absolute_path "SA_ECS_REMOTE_PATH" "$REMOTE_PATH"
  REMOTE_PATH="${REMOTE_PATH%/}"

  REMOTE_RELEASE_ROOT="$(
    printf '%s' "${SA_DASHBOARD_RELEASES_DIR:-$(dirname "$REMOTE_PATH")/releases}"
  )"
  REMOTE_RELEASE_ROOT="${REMOTE_RELEASE_ROOT%/}"
  validate_safe_absolute_path \
    "SA_DASHBOARD_RELEASES_DIR" \
    "$REMOTE_RELEASE_ROOT"
  if [[ "$REMOTE_RELEASE_ROOT" == "$REMOTE_PATH" || "$REMOTE_RELEASE_ROOT" == "$REMOTE_PATH/"* ]]; then
    die "SA_DASHBOARD_RELEASES_DIR must be outside SA_ECS_REMOTE_PATH"
  fi
  if paths_overlap "$REMOTE_RELEASE_ROOT" "$REMOTE_PATH"; then
    die "SA_DASHBOARD_RELEASES_DIR must not overlap SA_ECS_REMOTE_PATH"
  fi

  REMOTE_PYTHON="$(
    printf '%s' "${SA_ECS_PYTHON:-$(dirname "$REMOTE_PATH")/venv/bin/python}"
  )"
  REMOTE_PYTHON="${REMOTE_PYTHON%/}"
  validate_safe_absolute_path "SA_ECS_PYTHON" "$REMOTE_PYTHON"

  RELEASE_STAMP="$(
    printf '%s' "${SA_DASHBOARD_RELEASE_STAMP:-$(date -u +%Y%m%d-%H%M%S)}"
  )"
  [[ "$RELEASE_STAMP" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "SA_DASHBOARD_RELEASE_STAMP contains unsupported characters"

  REMOTE_TARGET="${REMOTE_HOST}:${REMOTE_PATH}"
  BACKUP_DIR="${REMOTE_RELEASE_ROOT}/${RELEASE_STAMP}-dashboard-workspaces"
  REMOTE_LOCK_DIR="${REMOTE_RELEASE_ROOT}/.dashboard-workspaces-deploy.lock"

  if [[ -n "${SA_ECS_SSH_OPTS:-}" ]]; then
    local parsed_options=()
    local option
    read -r -a parsed_options <<<"$SA_ECS_SSH_OPTS"
    for option in "${parsed_options[@]}"; do
      [[ "$option" =~ ^[A-Za-z0-9_./:@=,+-]+$ ]] \
        || die "SA_ECS_SSH_OPTS contains unsupported characters"
    done
    SSH_COMMAND+=("${parsed_options[@]}")
    if [[ -z "${RSYNC_RSH:-}" ]]; then
      export RSYNC_RSH="ssh ${SA_ECS_SSH_OPTS}"
    fi
  fi
  if [[ -n "${RSYNC_RSH:-}" ]]; then
    [[ "$RSYNC_RSH" =~ ^ssh([[:space:]][A-Za-z0-9_./:@=,+-]+)*$ ]] \
      || die "RSYNC_RSH contains unsupported characters"
  fi
}

configure_limits() {
  MAX_BYTES="${SA_DASHBOARD_MAX_BYTES:-250000}"
  MAX_TTFB_SECONDS="${SA_DASHBOARD_MAX_TTFB_SECONDS:-0.500}"
  CANARY_BASE_URL="$(
    printf '%s' "${SA_DASHBOARD_CANARY_BASE_URL:-http://127.0.0.1:8765}"
  )"
  [[ "$MAX_BYTES" =~ ^[1-9][0-9]*$ ]] \
    || die "SA_DASHBOARD_MAX_BYTES must be a positive integer"
  [[ "$MAX_TTFB_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] \
    || die "SA_DASHBOARD_MAX_TTFB_SECONDS must be numeric"
  if [[ "$CANARY_BASE_URL" =~ ^https?://(127[.]0[.]0[.]1|localhost)(:([0-9]{1,5}))?/?$ ]]; then
    local port="${BASH_REMATCH[3]:-}"
    if [[ -n "$port" && ( "$port" -lt 1 || "$port" -gt 65535 ) ]]; then
      die "SA_DASHBOARD_CANARY_BASE_URL has an invalid port"
    fi
  else
    die "SA_DASHBOARD_CANARY_BASE_URL must be a loopback HTTP(S) origin"
  fi
  CANARY_BASE_URL="${CANARY_BASE_URL%/}"
}

capture_remote_preimage() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- \
      "$REMOTE_PATH" \
      "$DEPLOY_VERSION_FILE" \
      "${DASHBOARD_FILES[@]}" \
      -- \
      "$DASHBOARD_ASSET_TREE" <<'REMOTE'
set -euo pipefail

app_dir="$1"
shift
deploy_version_path="$1"
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
file_paths=("$deploy_version_path" "${file_paths[@]}")
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
  [[ -n "${SA_DASHBOARD_RELEASE_INPUT_MANIFEST:-}" ]] \
    || die "SA_DASHBOARD_RELEASE_INPUT_MANIFEST is required"
  [[ -f "$SA_DASHBOARD_RELEASE_INPUT_MANIFEST" ]] \
    || die "reviewed release-input manifest not found: $SA_DASHBOARD_RELEASE_INPUT_MANIFEST"

  LOCAL_RELEASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dashboard-release.XXXXXX")"
  PREIMAGE_MANIFEST="$LOCAL_RELEASE_DIR/expected-preimage.manifest"
  REVIEWED_RELEASE_MANIFEST="$LOCAL_RELEASE_DIR/reviewed-release-input.manifest"
  cp -- "$SA_DASHBOARD_PREIMAGE_MANIFEST" "$PREIMAGE_MANIFEST"
  cp -- "$SA_DASHBOARD_RELEASE_INPUT_MANIFEST" "$REVIEWED_RELEASE_MANIFEST"
  validate_manifest_file "$PREIMAGE_MANIFEST"
  validate_release_manifest_file "$REVIEWED_RELEASE_MANIFEST"
  configure_remote
  configure_limits
  CURRENT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  [[ "$CURRENT_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
    || die "unable to resolve current git commit"
  local reviewed_commit
  reviewed_commit="$(awk '$1 == "COMMIT" { print $2 }' "$REVIEWED_RELEASE_MANIFEST")"
  [[ "$reviewed_commit" == "$CURRENT_COMMIT" ]] \
    || die "reviewed release manifest commit does not match current HEAD"

  local relative
  for relative in "${DASHBOARD_FILES[@]}"; do
    [[ -f "$REPO_ROOT/$relative" ]] \
      || die "allowlisted release file is missing: $relative"
  done
}

ensure_release_inputs_match_head() {
  local relative
  local dirty
  for relative in \
    "${DASHBOARD_FILES[@]}" \
    "${DASHBOARD_BUILD_INPUTS[@]}"; do
    git -C "$REPO_ROOT" cat-file -e "HEAD:$relative" 2>/dev/null \
      || die "release input is not tracked by current HEAD: $relative"
  done
  dirty="$(
    git -C "$REPO_ROOT" status \
      --porcelain=v1 \
      --untracked-files=no \
      -- \
      "${DASHBOARD_FILES[@]}" \
      "${DASHBOARD_BUILD_INPUTS[@]}"
  )"
  [[ -z "$dirty" ]] \
    || die "release inputs must match reviewed commit $CURRENT_COMMIT"
}

write_local_release_manifest() {
  local output="$1"
  python3 - \
    "$REPO_ROOT" \
    "$output" \
    "$RELEASE_INPUT_FORMAT" \
    "$CURRENT_COMMIT" \
    "$DASHBOARD_ASSET_TREE" \
    "${DASHBOARD_FILES[@]}" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
output = Path(sys.argv[2])
release_format = sys.argv[3]
commit = sys.argv[4]
tree_relative = sys.argv[5]
file_relatives = sys.argv[6:]


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
    f"FORMAT {release_format}",
    f"COMMIT {commit}",
    *[
    f"FILE {file_digest(root / relative)} {relative}"
    for relative in file_relatives
    ],
]
lines.append(f"TREE {tree_digest(root / tree_relative)} {tree_relative}")
output.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

build_dashboard_assets() {
  "$SCRIPT_DIR/build-dashboard-app.sh"
  [[ -f "$REPO_ROOT/$DASHBOARD_ASSET_TREE/index.html" ]] \
    || die "dashboard build did not create reports/app/index.html"
}

verify_reviewed_release_input() {
  ensure_release_inputs_match_head
  LOCAL_RELEASE_MANIFEST="$LOCAL_RELEASE_DIR/actual-release-input.manifest"
  write_local_release_manifest "$LOCAL_RELEASE_MANIFEST"
  if ! cmp -s "$REVIEWED_RELEASE_MANIFEST" "$LOCAL_RELEASE_MANIFEST"; then
    die "release input mismatch: local allowlist does not match reviewed manifest"
  fi
  cp -- "$REVIEWED_RELEASE_MANIFEST" "$LOCAL_RELEASE_DIR/release-input.manifest"
  LOCAL_RELEASE_MANIFEST="$LOCAL_RELEASE_DIR/release-input.manifest"
}

capture_local_release_input() {
  LOCAL_RELEASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dashboard-release.XXXXXX")"
  CURRENT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  [[ "$CURRENT_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
    || die "unable to resolve current git commit"
  build_dashboard_assets >&2
  ensure_release_inputs_match_head
  LOCAL_RELEASE_MANIFEST="$LOCAL_RELEASE_DIR/release-input.manifest"
  write_local_release_manifest "$LOCAL_RELEASE_MANIFEST"
  cat "$LOCAL_RELEASE_MANIFEST"
}

acquire_remote_lock() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- \
      "$REMOTE_PATH" \
      "$REMOTE_RELEASE_ROOT" \
      "$REMOTE_LOCK_DIR" \
      "$RELEASE_STAMP" \
      "$REMOTE_PYTHON" <<'REMOTE'
set -euo pipefail

app_dir="$1"
release_root="$2"
lock_dir="$3"
release_stamp="$4"
python_bin="$5"
"$python_bin" - "$app_dir" "$release_root" <<'PY'
from pathlib import Path
import sys

app = Path(sys.argv[1]).resolve(strict=False)
release = Path(sys.argv[2]).resolve(strict=False)
if app == release or app in release.parents or release in app.parents:
    raise SystemExit(
        f"resolved release path overlaps app: app={app} release={release}"
    )
PY
umask 077
mkdir -p "$release_root"
if ! mkdir "$lock_dir"; then
  printf 'dashboard deployment lock is already held: %s\n' "$lock_dir" >&2
  exit 7
fi
printf '%s\n' "$release_stamp" >"$lock_dir/holder"
REMOTE
  LOCK_HELD=1
}

release_remote_lock() {
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- "$REMOTE_LOCK_DIR" "$RELEASE_STAMP" <<'REMOTE'
set -euo pipefail

lock_dir="$1"
release_stamp="$2"
[[ -d "$lock_dir" ]] || exit 0
[[ -f "$lock_dir/holder" ]] || {
  printf 'dashboard deployment lock has no holder record: %s\n' "$lock_dir" >&2
  exit 7
}
holder="$(cat "$lock_dir/holder")"
[[ "$holder" == "$release_stamp" ]] || {
  printf 'dashboard deployment lock holder mismatch: %s\n' "$lock_dir" >&2
  exit 7
}
rm -f "$lock_dir/holder"
rmdir "$lock_dir"
REMOTE
  LOCK_HELD=0
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
    mkdir -p "$(dirname "$backup_dir/root/$relative")"
    cp -a "$relative" "$backup_dir/root/$relative"
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
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" \
    bash -s -- "$REMOTE_PATH" "$DEPLOY_VERSION_FILE" "$CURRENT_COMMIT" <<'REMOTE'
set -euo pipefail

app_dir="$1"
deploy_version_path="$2"
commit="$3"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || exit 4
umask 022
temporary="$(mktemp "$app_dir/.DEPLOY_VERSION.XXXXXX")"
trap 'rm -f "$temporary"' EXIT
printf '%s\n' "$commit" >"$temporary"
mv -f "$temporary" "$app_dir/$deploy_version_path"
trap - EXIT
REMOTE
}

verify_remote_release() {
  local remote_command="bash -s --"
  local argument quoted_argument
  local -a remote_arguments=(
    "$REMOTE_PATH"
    "$BACKUP_DIR"
    "$REMOTE_PYTHON"
    "$DASHBOARD_SERVICE"
    "$MAX_BYTES"
    "$MAX_TTFB_SECONDS"
    "$CANARY_BASE_URL"
    "$DEPLOY_VERSION_FILE"
    "${DASHBOARD_TEST_MODULES[@]}"
    --
    "${DASHBOARD_CANARY_ENDPOINTS[@]}"
  )
  for argument in "${remote_arguments[@]}"; do
    printf -v quoted_argument '%q' "$argument"
    remote_command+=" $quoted_argument"
  done
  "${SSH_COMMAND[@]}" "$REMOTE_HOST" "$remote_command" <<'REMOTE'
set -euo pipefail

app_dir="$1"
backup_dir="$2"
python_bin="$3"
service_name="$4"
max_bytes="$5"
max_ttfb="$6"
base_url="$7"
deploy_version_path="$8"
shift 8

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
release_commit=""
while read -r kind expected relative extra; do
  [[ "${kind:-}" == \#* ]] && continue
  [[ -n "${kind:-}" ]] || continue
  if [[ "$kind" == "FORMAT" ]]; then
    [[ -n "${expected:-}" && -z "${relative:-}" && -z "${extra:-}" ]] || exit 4
    continue
  fi
  if [[ "$kind" == "COMMIT" ]]; then
    [[ -n "${expected:-}" && -z "${relative:-}" && -z "${extra:-}" ]] || exit 4
    [[ -z "$release_commit" && "$expected" =~ ^[0-9a-f]{40}$ ]] || exit 4
    release_commit="$expected"
    continue
  fi
  [[ -z "${extra:-}" ]] || exit 4
  if [[ "$kind" == "FILE" ]]; then
    actual="$(sha256sum "$relative" | awk '{print $1}')"
  elif [[ "$kind" == "TREE" ]]; then
    actual="$(hash_tree "$relative")"
  else
    printf 'invalid release input kind: %s\n' "$kind" >&2
    exit 4
  fi
  if [[ "$actual" != "$expected" ]]; then
    printf 'release input mismatch: %s expected=%s actual=%s\n' \
      "$relative" "$expected" "$actual" >&2
    exit 4
  fi
done <"$backup_dir/release-input.manifest"
[[ -n "$release_commit" ]] || exit 4
[[ -f "$deploy_version_path" ]] || {
  printf 'deployment version marker missing: %s\n' "$deploy_version_path" >&2
  exit 4
}
actual_deploy_version="$(tr -d '\r\n' <"$deploy_version_path")"
[[ "$actual_deploy_version" == "$release_commit" ]] || {
  printf 'deployment version mismatch: expected=%s actual=%s\n' \
    "$release_commit" "$actual_deploy_version" >&2
  exit 4
}

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
      "$RELEASE_STAMP" \
      "$CURRENT_COMMIT" \
      "$DEPLOY_VERSION_FILE" <<'REMOTE'
set -euo pipefail

backup_dir="$1"
app_dir="$2"
service_name="$3"
release_stamp="$4"
commit="$5"
deploy_version_path="$6"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || exit 6
actual_deploy_version="$(tr -d '\r\n' <"$app_dir/$deploy_version_path")"
[[ "$actual_deploy_version" == "$commit" ]] || exit 6
preimage_sha="$(sha256sum "$backup_dir/expected-preimage.manifest" | awk '{print $1}')"
release_sha="$(sha256sum "$backup_dir/release-input.manifest" | awk '{print $1}')"
canary_sha="$(sha256sum "$backup_dir/canary-results.tsv" | awk '{print $1}')"
deploy_version_sha="$(sha256sum "$app_dir/$deploy_version_path" | awk '{print $1}')"

cat >"$backup_dir/release-manifest.txt" <<EOF
format=dashboard-workspaces-release-v1
status=deployed
release_stamp=$release_stamp
app_dir=$app_dir
backup_dir=$backup_dir
service=$service_name
commit=$commit
deploy_version_file=$deploy_version_path
deploy_version_sha256=$deploy_version_sha
preimage_manifest_sha256=$preimage_sha
release_input_manifest_sha256=$release_sha
canary_results_sha256=$canary_sha
rollback_scope=allowlisted-app-files-and-reports-app-only
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
      "$RELEASE_STAMP" \
      "$CANARY_BASE_URL" <<'REMOTE'
set -euo pipefail

app_dir="$1"
backup_dir="$2"
service_name="$3"
release_stamp="$4"
base_url="$5"
preexisting="$backup_dir/preexisting-files.txt"
missing="$backup_dir/missing-files.txt"
expected_manifest="$backup_dir/expected-preimage.manifest"
actual_manifest="$backup_dir/rollback-actual-preimage.manifest"
rollback_result="$backup_dir/rollback-result.txt"
rollback_canary="$backup_dir/rollback-canary-results.tsv"
rollback_status="failed"
preimage_status="not_run"
service_status="not_run"
app_canary_status="not_run"
failure_step="restore_files"

write_rollback_result() {
  local exit_code=$?
  local release_status="rollback_failed"
  trap - EXIT
  set +e
  if [[ "$exit_code" -eq 0 && "$rollback_status" == "verified" ]]; then
    release_status="rolled_back"
  fi
  cat >"$rollback_result" <<EOF
rollback_status=$rollback_status
preimage_status=$preimage_status
service_status=$service_status
app_canary_status=$app_canary_status
failure_step=$failure_step
exit_code=$exit_code
EOF
  rollback_sha="$(sha256sum "$rollback_result" | awk '{print $1}')"
  cat >"$backup_dir/release-manifest.txt" <<EOF
format=dashboard-workspaces-release-v1
status=$release_status
release_stamp=$release_stamp
app_dir=$app_dir
backup_dir=$backup_dir
service=$service_name
rollback_result_sha256=$rollback_sha
rollback_scope=allowlisted-app-files-and-reports-app-only
EOF
  exit "$exit_code"
}
trap write_rollback_result EXIT

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

failure_step="verify_preimage"
: >"$actual_manifest"
cd "$app_dir"
while read -r kind expected relative extra; do
  [[ "${kind:-}" == \#* ]] && continue
  [[ -n "${kind:-}" ]] || continue
  [[ -z "${extra:-}" ]] || exit 8
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
    printf 'rollback preimage mismatch: %s expected=%s actual=%s\n' \
      "$relative" "$expected" "$actual" >&2
    exit 8
  fi
done <"$expected_manifest"
preimage_status="verified"

failure_step="restart_service"
systemctl restart "$service_name"
systemctl is-active --quiet "$service_name"
service_status="active"

failure_step="app_canary"
printf 'endpoint\tstatus\n' >"$rollback_canary"
for endpoint in "/api/dashboard/system-overview.json" "/app.html?view=system"; do
  curl --fail --silent --show-error --output /dev/null \
    --retry 20 --retry-connrefused --retry-delay 1 --max-time 120 \
    "${base_url%/}${endpoint}"
  printf '%s\tpassed\n' "$endpoint" >>"$rollback_canary"
done
app_canary_status="passed"
failure_step="complete"
rollback_status="verified"
REMOTE
}

handle_exit() {
  local exit_code=$?
  trap - EXIT

  if [[ "$exit_code" -ne 0 && "$SYNC_STARTED" -eq 1 && "$BACKUP_READY" -eq 1 ]]; then
    printf 'deployment failed; restoring allowlisted app backup %s\n' \
      "$BACKUP_DIR" >&2
    if restore_remote_backup; then
      printf 'automatic rollback verified: %s\n' "$BACKUP_DIR" >&2
    else
      printf 'automatic rollback failed; inspect rollback-result.txt in %s\n' \
        "$BACKUP_DIR" >&2
    fi
  fi
  if [[ "$LOCK_HELD" -eq 1 ]]; then
    if ! release_remote_lock; then
      printf 'failed to release dashboard deployment lock: %s\n' \
        "$REMOTE_LOCK_DIR" >&2
      if [[ "$exit_code" -eq 0 ]]; then
        exit_code=7
      fi
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
    validate-release-input)
      [[ "$#" -eq 2 ]] || die "validate-release-input requires one file"
      validate_release_manifest_file "$2"
      printf 'release_input_manifest_valid=%s\n' "$2"
      ;;
    validate-config)
      [[ "$#" -eq 1 ]] || die "validate-config does not accept arguments"
      [[ -n "${SA_ECS_REMOTE:-}" ]] \
        || die "SA_ECS_REMOTE must be user@host:/absolute/app/path"
      configure_remote
      configure_limits
      printf 'config_valid=1\n'
      ;;
    capture-preimage)
      [[ "$#" -eq 1 ]] || die "capture-preimage does not accept arguments"
      [[ -n "${SA_ECS_REMOTE:-}" ]] \
        || die "SA_ECS_REMOTE must be user@host:/absolute/app/path"
      configure_remote
      capture_remote_preimage
      ;;
    capture-release-input)
      [[ "$#" -eq 1 ]] || die "capture-release-input does not accept arguments"
      trap handle_exit EXIT
      capture_local_release_input
      ;;
    deploy)
      [[ "$#" -eq 1 ]] || die "deploy does not accept positional arguments"
      trap handle_exit EXIT
      validate_local_contract
      build_dashboard_assets
      verify_reviewed_release_input
      acquire_remote_lock
      prepare_remote_backup
      sync_dashboard_release
      verify_remote_release
      write_remote_manifest
      release_remote_lock
      ;;
    *)
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
