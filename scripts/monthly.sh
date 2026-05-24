#!/usr/bin/env bash
# One-shot monthly cycle: sync-from-ecs → Claude monthly strategy → Codex monthly strategy → sync-to-ecs.
#
# Usage:
#   ./scripts/monthly.sh
#
# This is the heavyweight monthly run that actually MODIFIES strategy YAML.
# Both agents will:
#   - read the monthly briefing
#   - rewrite configs/agents/<agent>.yaml
#   - write evolution_log/<month>.md + evolution_diff/<month>.json
#   - append a row to config_evolution.csv
#   - back the prior overlay up to configs/agents/_history/<from_hash>.yaml
#   - run `validate-overlay` to confirm schema + baseline guard
#
# After this, the next Saturday's weekly-trigger on ECS will score with the
# new YAML. Review the diffs before sync-to-ecs if you want to vet:
#   git diff configs/agents/

set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"

TS=$(date +%Y%m%d-%H%M%S)
LOG_DIR="$REPO/logs/monthly-$TS"
mkdir -p "$LOG_DIR"

: "${SA_ECS_REMOTE:=ai-baby-aliyun:/opt/stock-analyze/app}"
export SA_ECS_REMOTE

CLAUDE_PROMPT='请按 CLAUDE.md §5b 描述的 6 步,以 claude agent 身份完成本月策略演化。读 CLAUDE.md, 找到 data/claude/notes/briefings/ 下最新 monthly briefing 文件,基于月度数据 + 我自己近 4 周 weekly notes + 对手 codex 当前 overlay 摘要(允许看),决定如何调整 claude.yaml,然后:1) 写 data/claude/notes/<月>-monthly-review.md(≤1500 字 3 段);2) 直接重写 configs/agents/claude.yaml(JSON 语法,7 个允许的 top-level keys);3) 调用 evolution_writer.write_evolution 原子写 evolution_log + evolution_diff + 备份旧 yaml + append config_evolution.csv;4) 跑 python3 -m stock_analyze validate-overlay --agent claude 守门员检查,exit 0 才算完成。完成后简单告诉我:改了哪些权重 + validate-overlay 结果。'

CODEX_PROMPT='请按 AGENTS.md §6 描述的步骤,以 codex agent 身份完成本月策略演化。读 AGENTS.md, 找到 data/codex/notes/briefings/ 下最新 monthly briefing 文件,基于月度数据 + codex 近 4 周 weekly notes + 对手 claude 当前 overlay 摘要,决定如何调整 codex.yaml,然后:1) 写 data/codex/notes/<月>-monthly-review.md;2) 直接重写 configs/agents/codex.yaml;3) 调用 evolution_writer.write_evolution 写 evolution_log + diff + 备份 + csv;4) 跑 validate-overlay --agent codex 守门员检查。完成后告诉我改了什么。'

banner() {
  echo
  echo "================================================================"
  echo "[$(date +%H:%M:%S)] $*"
  echo "================================================================"
}

banner "[1/4] sync-from-ecs (拉月报 briefing + state)"
bash ./scripts/sync-from-ecs.sh --exclude-cache 2>&1 | tee "$LOG_DIR/01-sync-from.log" | tail -5

banner "[2/4] Claude 跑 monthly strategy evolution"
claude -p "$CLAUDE_PROMPT" \
  --add-dir "$REPO" \
  --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
  > "$LOG_DIR/02-claude-monthly.log" 2>&1
echo "Claude 完成. 看完整输出:cat $LOG_DIR/02-claude-monthly.log"

banner "[3/4] Codex 跑 monthly strategy evolution"
codex exec \
  --cd "$REPO" \
  --sandbox workspace-write \
  --dangerously-bypass-approvals-and-sandbox \
  "$CODEX_PROMPT" \
  -o "$LOG_DIR/03-codex-monthly-last.txt" \
  > "$LOG_DIR/03-codex-monthly.log" 2>&1
echo "Codex 完成. 看完整输出:cat $LOG_DIR/03-codex-monthly.log"

banner "[4/4] yaml diff 预览 + sync-to-ecs"
echo
echo "===== claude.yaml diff ====="
git --no-pager diff configs/agents/claude.yaml || true
echo
echo "===== codex.yaml diff ====="
git --no-pager diff configs/agents/codex.yaml || true
echo
echo "上面是两个 agent 本月对 yaml 的改动。回车继续 sync 到 ECS,或 Ctrl-C 终止人工审阅..."
read -r

bash ./scripts/sync-to-ecs.sh 2>&1 | tee "$LOG_DIR/04-sync-to.log" | tail -10

banner "ALL DONE ✓"
echo
echo "本月演化产物:"
ls -la data/claude/notes/*-monthly-review.md data/codex/notes/*-monthly-review.md \
       data/claude/evolution_log/*.md data/codex/evolution_log/*.md 2>/dev/null | tail -4
echo
echo "新 yaml 已推到 ECS。下个周六 (weekly-trigger.timer 10:00) 会用新 yaml 算分。"
echo "本次跑的所有 log: $LOG_DIR/"
