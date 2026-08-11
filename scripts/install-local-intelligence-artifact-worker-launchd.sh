#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$REPO_ROOT/deploy/launchd/com.stock-analyze.local-intelligence-artifact-worker.plist"
WORKER="$SCRIPT_DIR/run-local-intelligence-artifact-worker.sh"
LABEL="com.stock-analyze.local-intelligence-artifact-worker"

LOCAL_ROOT="$REPO_ROOT"
REMOTE="${SA_LOCAL_INTELLIGENCE_REMOTE:-root@120.55.188.242}"
SSH_KEY="${SA_LOCAL_INTELLIGENCE_SSH_KEY:-$HOME/.ssh/<ssh-key-file>}"
STAGE="parse"
LIMIT="10"
WORKERS="2"
ALLOW_BATTERY=0

usage() {
  cat <<'EOF'
Usage:
  install-local-intelligence-artifact-worker-launchd.sh [options]

Options:
  --stage parse|download
  --limit N
  --workers N
  --remote USER@HOST
  --ssh-key PATH
  --local-root PATH
  --allow-battery
  -h, --help
EOF
}

require_value() {
  if [[ "$2" -lt 2 ]]; then
    printf 'error: %s requires a value\n' "$1" >&2
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
for numeric_value in "$LIMIT" "$WORKERS"; do
  if [[ ! "$numeric_value" =~ ^[1-9][0-9]*$ ]]; then
    printf 'error: limit and workers must be positive integers\n' >&2
    exit 2
  fi
done
if [[ ! -d "$LOCAL_ROOT" ]]; then
  printf 'error: local root is not a directory: %s\n' "$LOCAL_ROOT" >&2
  exit 2
fi
if [[ ! -f "$SSH_KEY" ]]; then
  printf 'error: SSH key does not exist\n' >&2
  exit 2
fi
if [[ ! -f "$TEMPLATE" || ! -x "$WORKER" ]]; then
  printf 'error: worker or launchd template is missing\n' >&2
  exit 2
fi
if [[ -n "${SA_LOCAL_INTELLIGENCE_PYTHON:-}" ]]; then
  LOCAL_PYTHON="$SA_LOCAL_INTELLIGENCE_PYTHON"
elif [[ -x "$LOCAL_ROOT/.venv/bin/python" ]]; then
  LOCAL_PYTHON="$LOCAL_ROOT/.venv/bin/python"
else
  LOCAL_PYTHON="$(command -v python3)"
fi
if [[ ! -x "$LOCAL_PYTHON" ]]; then
  printf 'error: local Python does not exist\n' >&2
  exit 2
fi
TESSERACT_BIN="$(command -v tesseract || true)"
TOOL_PATH="$(dirname "$LOCAL_PYTHON")"
if [[ -n "$TESSERACT_BIN" ]]; then
  TOOL_PATH="$TOOL_PATH:$(dirname "$TESSERACT_BIN")"
fi
TOOL_PATH="$TOOL_PATH:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET="$TARGET_DIR/$LABEL.plist"
LOG_DIR="$LOCAL_ROOT/.local-intelligence-artifact-worker/logs"
mkdir -p "$TARGET_DIR" "$LOG_DIR"

python3 - \
  "$TEMPLATE" \
  "$TARGET" \
  "$WORKER" \
  "$LOCAL_ROOT" \
  "$REMOTE" \
  "$SSH_KEY" \
  "$STAGE" \
  "$LIMIT" \
  "$WORKERS" \
  "$ALLOW_BATTERY" \
  "$LOG_DIR" \
  "$LOCAL_PYTHON" \
  "$TOOL_PATH" <<'PY'
import plistlib
import sys
from pathlib import Path

(
    template_path,
    target_path,
    worker,
    local_root,
    remote,
    ssh_key,
    stage,
    limit,
    workers,
    allow_battery,
    log_dir,
    local_python,
    tool_path,
) = sys.argv[1:]

with Path(template_path).open("rb") as handle:
    config = plistlib.load(handle)

arguments = [
    worker,
    "--local-root",
    local_root,
    "--remote",
    remote,
    "--ssh-key",
    ssh_key,
    "--stage",
    stage,
    "--limit",
    limit,
    "--workers",
    workers,
    "--once",
]
if allow_battery == "1":
    arguments.append("--allow-battery")

config["ProgramArguments"] = arguments
config["WorkingDirectory"] = local_root
config["EnvironmentVariables"] = {
    "PATH": tool_path,
    "SA_LOCAL_INTELLIGENCE_PYTHON": local_python,
}
config["StandardOutPath"] = str(Path(log_dir) / "launchd.stdout.log")
config["StandardErrorPath"] = str(Path(log_dir) / "launchd.stderr.log")
with Path(target_path).open("wb") as handle:
    plistlib.dump(config, handle, sort_keys=False)
PY

chmod 0644 "$TARGET"
launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$TARGET"

printf 'Installed %s. It will attempt one %s job every 30 minutes.\n' \
  "$LABEL" "$STAGE"
printf 'LaunchAgent: %s\n' "$TARGET"
