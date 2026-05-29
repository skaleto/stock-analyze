# Pending operational switches — decide at the June 2026 monthly evolution

> Created 2026-05-29. Two capabilities are built, tested, and deployed but
> **dormant pending an operator decision**. This file is the durable record;
> a one-time scheduled reminder (fires ~2026-06-01) points here.

---

## Switch A — sector-level sentiment factor (NOT yet active)

**What it is:** per-industry LLM sentiment becomes a real per-stock factor
(each candidate inherits its industry's weekly score). Replaces the old
broadcast `market_sentiment_1w`, which had **zero** cross-sectional effect.

**Current state:** code + CLI + prompt deployed on ECS; **no overlay
references it**, so it has zero effect today. The 9 classic factors are
unchanged.

**To activate (two steps, both required):**

1. Add to `configs/agents/claude_a_share.yaml` `factors` (do this at the
   monthly evolution, with a matching evolution_log entry):
   ```json
   "claude_sector_sentiment": {"weight": 0.10, "direction": "high"}
   ```
2. Each weekend, record industry scores (replaces the old `record-sentiment`):
   - Use `stock_analyze/markets/a_share/alt_factors/prompts/sector_sentiment_v1.md`
   - `python3 -m stock_analyze record-sector-sentiment --agent claude
     --week-end <YYYY-MM-DD> --json '{"sectors":[...],"llm_model":"..."}'`

**If you don't activate it:** nothing breaks; the factor stays inert. The old
broadcast `record-sentiment` can be stopped now regardless (it fed nothing).

**Suggestion:** try it in a research backtest first
(`backtest --compare-mvp` with the factor added to a scratch overlay) before
committing it to the live overlay.

---

## Switch B — full-pipeline backtest gate (ACTIVE as of 2026-05-29)

**What it is:** the monthly evolution gate now backtests your **real factor
mix** (merged onto the baseline) instead of a low-PE approximation. Flag
`backtest.use_full_pipeline: true` in `configs/competition_a_share.yaml`.

**Decision for you:** keep it on (recommended) or set back to `false`. It
only affects the offline monthly gate, never live trading.

**Caveat — it only bites where a backtest cache exists:** that is **ECS**
(1.6 GB cache present), **not your local machine**. If you run
`/monthly-strategy` locally, the gate soft-skips (cache missing → swallowed).
To make it bite locally, either:
- `rsync` the cache from ECS:
  `rsync -avz -e "ssh -i ~/.ssh/ai_baby_aliyun" root@120.55.188.242:/opt/stock-analyze/app/data/shared/backtest_cache/ data/shared/backtest_cache/`
- or run `python3 -m stock_analyze prepare-backtest-data` locally (slow; uses Tushare quota).

**Why it matters:** before 2026-05-29 the gate was inert — it backtested a
7-key overlay with no `accounts`, produced a 0-trade flat NAV, and trivially
passed every floor. It never vetted anything. See the commit
"backtest: make the floor gate real (switch B) + fix 3 latent engine bugs".
