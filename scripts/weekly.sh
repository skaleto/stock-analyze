#!/usr/bin/env bash
# One-shot weekly cycle: sync-from-ecs → reviews (both agents) → sentiment
# records (both agents) → sync-to-ecs.
#
# Usage:
#   ./scripts/weekly.sh
#
# Prerequisites (one-time setup on this laptop):
#   - claude (Claude Code CLI) on PATH, logged in (with WebSearch + WebFetch)
#   - codex (Codex CLI) on PATH, logged in
#   - SSH alias `ai-baby-aliyun` configured (~/.ssh/config)
#
# What this does (~5-8 minutes):
#   [1/6] rsync ECS → local (briefings, positions, NAV, dashboard, alt_factors)
#   [2/6] claude -p  → writes data/claude/notes/<date>-weekly-review.md
#   [3/6] claude -p  → reads market_sentiment_v1 prompt + WebSearch, runs
#                      `record-sentiment --agent claude` for NEXT Friday's week_end
#   [4/6] codex exec → writes data/codex/notes/<date>-weekly-review.md
#   [5/6] codex exec → reads same prompt + curl/internal knowledge, runs
#                      `record-sentiment --agent codex` for NEXT Friday's week_end
#   [6/6] rsync local → ECS, refresh dashboard
#
# Why "NEXT Friday" for sentiment? Sentiment feeds the run-weekly broadcast
# factor consumed at ECS Sat 10:00 weekly-trigger. If operator runs weekly.sh
# on Sunday, this Saturday is already past — so the recorded sentiment is for
# the upcoming week (next Friday's signal date). Idempotent: if sentiment for
# that week_end already exists, `record-sentiment` returns exit 1 (no overwrite
# without --force); the cycle still completes via sync-to-ecs.
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

# Compute NEXT Friday (sentiment week_end). If today is Friday, use next Friday.
NEXT_FRIDAY=$(python3 -c "
import datetime
today = datetime.date.today()
days = (4 - today.weekday()) % 7  # Mon=0 .. Fri=4 .. Sun=6
if days == 0:
    days = 7
print((today + datetime.timedelta(days=days)).isoformat())
")

CLAUDE_REVIEW_PROMPT='请按 .claude/commands/weekly-review.md 描述的 6 步骤,以 claude agent 身份完成本周 weekly review。读 CLAUDE.md,找到 data/claude/notes/briefings/ 下最新 weekly briefing 文件,阅读后写一份 ≤800 中文字的笔记到 data/claude/notes/<对应日期>-weekly-review.md(覆盖已存在文件)。四段:数据合理性检查 / 本周表现归因 / 观察点 / 下一步计划草稿。本周不要修改 configs/。完成后只回复一行:目标文件路径。'

CODEX_REVIEW_PROMPT='请按 .claude/commands/weekly-review.md 描述的步骤,以 codex agent 身份完成本周 weekly review。读 AGENTS.md(codex 的操作手册),找到 data/codex/notes/briefings/ 下最新 weekly briefing 文件,阅读后写一份 ≤800 中文字的笔记到 data/codex/notes/<对应日期>-weekly-review.md(覆盖已存在文件)。四段:数据合理性检查 / 本周表现归因 / 观察点 / 下一步计划草稿。本周不要修改 configs/。完成后只回复一行:目标文件路径。'

CLAUDE_SENTIMENT_PROMPT="你正在为 claude agent 录入下周 A 股市场情绪因子。下周五 week_end = ${NEXT_FRIDAY}。

步骤:
1. Read stock_analyze/alt_factors/prompts/market_sentiment_v1.md 完整看打分规则
2. 用 WebSearch 工具搜本周(过去 7 天)A 股市场关键新闻。关键词:
   - 'A股 本周走势' / '上证指数 涨跌' / '央行 货币政策' / '北向资金'
   - '板块表现' / '政策预期'
   - 优先权威源:财联社 cls.cn / 新浪财经 finance.sina.com.cn / 同花顺 10jqka.com.cn / 东方财富
3. 综合分析,产出:
   - sentiment_score ∈ [-1, +1](-1 极空 / 0 中性 / +1 极多)
   - confidence ∈ [0, 1]
   - 3-5 个 drivers(简短描述,逗号分隔)
   - 对应 source URLs(| 分隔)
4. 调用 Bash 跑录入命令(参数都要填实际值,不要 \\ 续行):
   python3 -m stock_analyze.cli record-sentiment --agent claude --week-end ${NEXT_FRIDAY} --score <SCORE> --confidence <CONFIDENCE> --drivers '<drivers>' --sources '<urls>' --llm-model claude-opus-4.7 --prompt-version v1
5. 完成后只回一行:'已录入 claude_sentiment: score=X.XX confidence=X.XX'

约束:
- agent 必须是 claude,不要为 codex 录
- week_end 必须是 ${NEXT_FRIDAY}
- 不要加 --force(如果该 week_end 已存在,接受失败)
- 如果 WebSearch 完全失败,基于你的市场知识估算,但 confidence 不要超过 0.5"

CODEX_SENTIMENT_PROMPT="你正在为 codex agent 录入下周 A 股市场情绪因子。下周五 week_end = ${NEXT_FRIDAY}。

步骤:
1. Read stock_analyze/alt_factors/prompts/market_sentiment_v1.md 看打分规则
2. 综合本周市场认知 + 你能访问的信息(可以 curl 公开 RSS 比如 https://www.cls.cn/sitemap.xml 拿标题,也可以基于你的训练知识估算最近趋势)
3. 产出:sentiment_score ∈ [-1,+1]、confidence ∈ [0,1]、3-5 drivers、source notes
4. 调用 shell 跑录入(参数填实际值):
   python3 -m stock_analyze.cli record-sentiment --agent codex --week-end ${NEXT_FRIDAY} --score <SCORE> --confidence <CONFIDENCE> --drivers '<drivers>' --sources '<urls or descriptive note>' --llm-model gpt-5-codex --prompt-version v1
5. 完成后只回一行:'已录入 codex_sentiment: score=X.XX confidence=X.XX'

约束:
- agent 必须是 codex
- week_end 必须是 ${NEXT_FRIDAY}
- 不要加 --force
- 如果信息匮乏,confidence 不要超过 0.4"

banner() {
  echo
  echo "================================================================"
  echo "[$(date +%H:%M:%S)] $*"
  echo "================================================================"
}

banner "[1/6] sync-from-ecs (拉 briefings + state + alt_factors)"
bash ./scripts/sync-from-ecs.sh --exclude-cache 2>&1 | tee "$LOG_DIR/01-sync-from.log" | tail -5

banner "[2/6] Claude 写 weekly review (claude -p)"
claude -p "$CLAUDE_REVIEW_PROMPT" \
  --add-dir "$REPO" \
  --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
  > "$LOG_DIR/02-claude-review.log" 2>&1
echo "Claude review 完成. log: $LOG_DIR/02-claude-review.log"
ls -la data/claude/notes/*-weekly-review.md 2>/dev/null | tail -1 || true

banner "[3/6] Claude 录入 sentiment (week_end=${NEXT_FRIDAY}, with WebSearch)"
claude -p "$CLAUDE_SENTIMENT_PROMPT" \
  --add-dir "$REPO" \
  --allowedTools "Read,Bash,WebSearch,WebFetch" \
  > "$LOG_DIR/03-claude-sentiment.log" 2>&1 || echo "(claude sentiment exit non-zero — see log)"
echo "Claude sentiment 完成. log: $LOG_DIR/03-claude-sentiment.log"
tail -3 "$LOG_DIR/03-claude-sentiment.log" || true

banner "[4/6] Codex 写 weekly review (codex exec)"
codex exec \
  --cd "$REPO" \
  --sandbox workspace-write \
  --dangerously-bypass-approvals-and-sandbox \
  "$CODEX_REVIEW_PROMPT" \
  -o "$LOG_DIR/04-codex-review-last.txt" \
  > "$LOG_DIR/04-codex-review.log" 2>&1
echo "Codex review 完成. log: $LOG_DIR/04-codex-review.log"
ls -la data/codex/notes/*-weekly-review.md 2>/dev/null | tail -1 || true

banner "[5/6] Codex 录入 sentiment (week_end=${NEXT_FRIDAY})"
codex exec \
  --cd "$REPO" \
  --sandbox workspace-write \
  --dangerously-bypass-approvals-and-sandbox \
  "$CODEX_SENTIMENT_PROMPT" \
  -o "$LOG_DIR/05-codex-sentiment-last.txt" \
  > "$LOG_DIR/05-codex-sentiment.log" 2>&1 || echo "(codex sentiment exit non-zero — see log)"
echo "Codex sentiment 完成. log: $LOG_DIR/05-codex-sentiment.log"
tail -3 "$LOG_DIR/05-codex-sentiment.log" || true

banner "[6/6] sync-to-ecs (推 reviews + sentiment + 刷新 dashboard)"
bash ./scripts/sync-to-ecs.sh 2>&1 | tee "$LOG_DIR/06-sync-to.log" | tail -10

banner "ALL DONE ✓"
echo
echo "本地新写的 review:"
ls -la data/claude/notes/*-weekly-review.md data/codex/notes/*-weekly-review.md 2>/dev/null | tail -2
echo
echo "本次 sentiment 录入(应该是 ${NEXT_FRIDAY}):"
python3 -m stock_analyze.cli sentiment-log --agent claude --last 1 2>&1 | tail -3 || true
python3 -m stock_analyze.cli sentiment-log --agent codex --last 1 2>&1 | tail -3 || true
echo
echo "看 dashboard(隧道已起的话):"
echo "  http://localhost:8765/pro.html → '对比' tab → 滚到底部 '本周双方观察对照'"
echo "  http://localhost:8765/pro.html → '洞察' tab → 'market sentiment' panel"
echo
echo "本次跑的所有 log: $LOG_DIR/"
