#!/usr/bin/env bash

set -euo pipefail

APP_DIR="${SA_REPO_ROOT:-/opt/stock-analyze/app}"
PYTHON_BIN="${SA_PYTHON_BIN:-/opt/stock-analyze/venv/bin/python}"
TARGET_DATE="${SA_QDII_RESEARCH_DATE:-$(date -d 'yesterday' +%F)}"

cd "$APP_DIR"

"$PYTHON_BIN" -m stock_analyze.cli refresh-qdii-events
"$PYTHON_BIN" -m stock_analyze.cli qdii-shadow-research \
  --end "$TARGET_DATE" \
  --refresh-data
"$PYTHON_BIN" -m stock_analyze.cli refresh-qdii-events \
  --universe data/cn_qdii_etf/research/catalog_latest.json

