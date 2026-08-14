#!/usr/bin/env bash
set -euo pipefail

repo_root="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
campaign_id="${CAMPAIGN_ID:-strategy-recovery-20260814-v1}"
as_of="${AS_OF:-2026-08-14}"
stage="${1:-transparent}"

cd "$repo_root"

case "$stage" in
  transparent)
    python3 -m stock_analyze \
      --as-of "$as_of" \
      run-strategy-campaign \
      --offline \
      --repo-root "$repo_root" \
      --campaign "$campaign_id" \
      --input-bundle ".artifacts/local-model-training/a_share-20260814-080903-62501/input/manifest.json" \
      --input-bundle ".artifacts/local-model-training/cn_qdii_etf-20260814-083057-66211/input/manifest.json" \
      --stage transparent
    ;;
  incremental-ml|incremental_ml)
    python3 -m stock_analyze \
      --as-of "$as_of" \
      run-strategy-campaign \
      --offline \
      --repo-root "$repo_root" \
      --campaign "$campaign_id" \
      --stage incremental-ml
    ;;
  *)
    printf 'unknown stage: %s\n' "$stage" >&2
    exit 2
    ;;
esac
