#!/usr/bin/env bash
# One-shot weekly cycle: sync-from-ecs → Claude weekly review → Codex weekly review → sync-to-ecs.
#
# Usage:
#   ./scripts/weekly.sh
#
# Prerequisites (one-time setup on your laptop):
#   - claude (Claude Code CLI) on PATH, logged in
#   - codex (Codex CLI) on PATH, logged in
#   - SSH alias `ai-baby-aliyun` configured (~/.ssh/config)
#
# What this does (all in one go, ~2-5 minutes):
#   1. rsync ECS → local (briefings, positions, NAV, dashboard)
#   2. claude -p   → writes data/claude/notes/<date>-weekly-review.md
#   3. codex exec  → writes data/codex/notes/<date>-weekly-review.md
#   4. rsync local → ECS, then refresh dashboard
#
# Detailed logs land in logs/weekly-<timestamp>/.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"

TS=$(date +%Y%m%d-%H%M%S)
LOG_DIR="$REPO/logs/weekly-$TS"
mkdir -p "$LOG_DIR"

: "${SA_ECS_REMOTE:=ai-baby-aliyun:/opt/stock-analyze/app}"
export SA_ECS_REMOTE

CLAUDE_PROMPT='请按 .claude/commands/weekly-review.md 描述的 6 步骤,以 claude agent 身份完成本周 weekly review。读 CLAUDE.md,找到 data/claude/notes/briefings/ 下最新 weekly briefing 文件,阅读后写一份 ≤800 中文字的笔记到 data/claude/notes/<对应日期>-weekly-review.md(覆盖已存在文件)。四段:数据合理性检查 / 本周表现归因 / 观察点 / 下一步计划草稿。本周不要修改 configs/。完成后只回复一行:目标文件路径。'

CODEX_PROMPT='请按 .claude/commands/weekly-review.md 描述的步骤,以 codex agent 身份完成本周 weekly review。读 AGENTS.md(codex 的操作手册),找到 data/codex/notes/briefings/ 下最新 weekly briefing 文件,阅读后写一份 ≤800 中文字的笔记到 data/codex/notes/<对应日期>-weekly-review.md(覆盖已存在文件)。四段:数据合理性检查 / 本周表现归因 / 观察点 / 下一步计划草稿。本周不要修改 configs/。完成后只回复一行:目标文件路径。'

banner() {
  echo
  echo "================================================================"
  echo "[$(date +%H:%M:%S)] $*"
  echo "================================================================"
}

banner "[1/4] sync-from-ecs (拉 briefings + state)"
bash ./scripts/sync-from-ecs.sh --exclude-cache 2>&1 | tee "$LOG_DIR/01-sync-from.log" | tail -5

banner "[2/4] Claude 写 weekly review (claude -p, 非交互)"
claude -p "$CLAUDE_PROMPT" \
  --add-dir "$REPO" \
  --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
  > "$LOG_DIR/02-claude-weekly.log" 2>&1
echo "Claude 完成. 看完整输出:cat $LOG_DIR/02-claude-weekly.log"
ls -la data/claude/notes/*-weekly-review.md 2>/dev/null | tail -1 || true

banner "[3/4] Codex 写 weekly review (codex exec, 非交互)"
codex exec \
  --cd "$REPO" \
  --sandbox workspace-write \
  --dangerously-bypass-approvals-and-sandbox \
  "$CODEX_PROMPT" \
  -o "$LOG_DIR/03-codex-weekly-last.txt" \
  > "$LOG_DIR/03-codex-weekly.log" 2>&1
echo "Codex 完成. 看完整输出:cat $LOG_DIR/03-codex-weekly.log"
ls -la data/codex/notes/*-weekly-review.md 2>/dev/null | tail -1 || true

banner "[4/4] sync-to-ecs (推回 + 刷新 dashboard)"
bash ./scripts/sync-to-ecs.sh 2>&1 | tee "$LOG_DIR/04-sync-to.log" | tail -10

banner "ALL DONE ✓"
echo
echo "本地新写的 review:"
ls -la data/claude/notes/*-weekly-review.md data/codex/notes/*-weekly-review.md 2>/dev/null | tail -2
echo
echo "看 dashboard(隧道已起的话):"
echo "  http://localhost:8765/pro.html → '对比' tab → 滚到底部 '本周双方观察对照'"
echo
echo "本次跑的所有 log: $LOG_DIR/"
