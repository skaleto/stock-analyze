#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT/frontend/dashboard"
npm ci --audit=false
npm test
npm run build
npm audit --omit=dev

cd "$REPO_ROOT"
if [[ -s "reports/research/permanent_portfolio/v1/dashboard.json" ]]; then
  python3 - <<'PY'
from pathlib import Path

from stock_analyze.dashboard_permanent_portfolio import (
    write_dashboard_permanent_portfolio_public_snapshot,
)

path = write_dashboard_permanent_portfolio_public_snapshot(
    repo_root=Path.cwd()
)
print(f"Permanent portfolio public snapshot built at {path}")
PY
else
  rm -f "reports/app/data/permanent-portfolio.json"
  echo "warning: permanent portfolio report is unavailable; public snapshot omitted" >&2
fi

if [[ ! -s "$REPO_ROOT/reports/app/index.html" ]]; then
  echo "error: frontend build did not create reports/app/index.html" >&2
  exit 1
fi

echo "Dashboard app built at reports/app/index.html"
