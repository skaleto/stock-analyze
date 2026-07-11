#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT/frontend/dashboard"
npm ci --audit=false
npm test
npm run build
npm audit --omit=dev

if [[ ! -s "$REPO_ROOT/reports/app/index.html" ]]; then
  echo "error: frontend build did not create reports/app/index.html" >&2
  exit 1
fi

echo "Dashboard app built at reports/app/index.html"
