#!/usr/bin/env bash
# One-shot Phase 1 path migration for the A-share competition.
#
# Moves (per docs/superpowers/plans/2026-05-27-multi-market-phase-1-refactor.md):
#   data/{claude,codex}/                  → data/a_share/{claude,codex}/
#   reports/{claude,codex}/                → reports/a_share/{claude,codex}/
#   configs/competition.yaml               → configs/competition_a_share.yaml
#   configs/agents/{claude,codex}.yaml     → configs/agents/{claude,codex}_a_share.yaml
#
# Idempotent: re-running after a partial run is safe (each step checks for
# source existence + destination non-existence before acting).
#
# Usage:
#   bash scripts/migrate-phase-1-paths.sh [--repo-root PATH] [--dry-run]
#
# Designed to run both on the local Mac and on ECS at /opt/stock-analyze/app.
# Uses `git mv` when invoked inside a git repo (preserves history); falls
# back to plain `mv` when git is unavailable or the path isn't tracked
# (e.g. ECS state files that aren't in version control).
set -euo pipefail

REPO_ROOT="$(pwd)"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

is_git_repo() {
  git rev-parse --git-dir > /dev/null 2>&1
}

# Move with `git mv` if tracked, otherwise plain `mv`. The `git mv` path is
# preferred locally so commit history shows the rename; on ECS where files
# aren't always in a git repo, plain mv is fine.
do_move() {
  local src="$1"
  local dst="$2"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  DRY: mv $src → $dst"
    return 0
  fi
  if is_git_repo && git ls-files --error-unmatch "$src" > /dev/null 2>&1; then
    echo "  git mv $src → $dst"
    git mv "$src" "$dst"
  else
    echo "  mv $src → $dst"
    mv "$src" "$dst"
  fi
}

ensure_parent() {
  local dst="$1"
  local parent
  parent="$(dirname "$dst")"
  if [[ ! -d "$parent" ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  DRY: mkdir -p $parent"
    else
      mkdir -p "$parent"
    fi
  fi
}

echo "→ data/<agent>/ → data/a_share/<agent>/"
for agent in claude codex; do
  src="data/$agent"
  dst="data/a_share/$agent"
  if [[ -d "$src" && ! -d "$dst" ]]; then
    ensure_parent "$dst"
    do_move "$src" "$dst"
  else
    echo "  skip (src missing or dst exists): $src → $dst"
  fi
done

echo "→ reports/<agent>/ → reports/a_share/<agent>/"
for agent in claude codex; do
  src="reports/$agent"
  dst="reports/a_share/$agent"
  if [[ -d "$src" && ! -d "$dst" ]]; then
    ensure_parent "$dst"
    do_move "$src" "$dst"
  else
    echo "  skip (src missing or dst exists): $src → $dst"
  fi
done

echo "→ configs/competition.yaml → configs/competition_a_share.yaml"
if [[ -f "configs/competition.yaml" && ! -f "configs/competition_a_share.yaml" ]]; then
  do_move "configs/competition.yaml" "configs/competition_a_share.yaml"
else
  echo "  skip"
fi

echo "→ configs/agents/<agent>.yaml → configs/agents/<agent>_a_share.yaml"
for agent in claude codex; do
  src="configs/agents/$agent.yaml"
  dst="configs/agents/${agent}_a_share.yaml"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    do_move "$src" "$dst"
  else
    echo "  skip: $src → $dst"
  fi
done

echo "Done."
