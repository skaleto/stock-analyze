#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="${PYTHON_BIN}"
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif [[ -x "/opt/homebrew/bin/python3" ]]; then
  PYTHON="/opt/homebrew/bin/python3"
else
  PYTHON="$(command -v python3)"
fi

"${PYTHON}" -c "import jsonschema" >/dev/null 2>&1 || {
  printf 'semantic_campaign_python_dependency_missing: jsonschema (%s)\n' "${PYTHON}" >&2
  exit 2
}

exec "${PYTHON}" -m stock_analyze.intelligence.semantic.local_campaign \
  --repo-root "${ROOT}" \
  "$@"
