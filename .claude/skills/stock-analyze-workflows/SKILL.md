---
name: stock-analyze-workflows
description: Use when operating stock-analyze daily, weekly, monthly, tri-market, ECS/local sync, market sentiment, dashboard, or strategy-evolution workflows in /Users/yaoyibin/Documents/stock/stock-analyze.
---

# Stock Analyze Workflows

Use this skill for the project at `/Users/yaoyibin/Documents/stock/stock-analyze`.

## Purpose

This is the single skill for the stock-analyze operating loop: ECS/local daily runs, weekly tri-market tasks, market-sentiment recording, dashboard refresh, monthly review, and strategy evolution.

## Claude Code Compatibility

This skill is intentionally cross-agent. Claude Code, Codex, and future agents
should treat it as the same operator workflow whenever the user asks to run the
stock-analyze workflow, tri-market workflow, market-sentiment workflow,
Dashboard refresh, ECS sync, weekly loop, or monthly loop.

For Claude Code specifically:

- Load this skill from `.claude/skills/stock-analyze-workflows/SKILL.md` in
  this repo or `~/.claude/skills/stock-analyze-workflows/SKILL.md`.
- If the user says "Claude" as the competition agent, only write Claude-owned
  artifacts: `configs/agents/claude_<market>.yaml`,
  `data/<market>/claude/...`, and `reports/<market>/claude/...`.
- If the user asks as the human operator to run the whole workflow, inspect all
  six `(market, agent)` pairs while preserving each agent's private files.
- If model selection is available, use GPT-5.5 for judgement tasks. If the
  current environment does not expose model selection, continue with the active
  model and state that limitation.

## Non-Negotiables

- Use GPT-5.5 for agent/subagent LLM work whenever model selection is available.
- Deterministic jobs are not LLM jobs: daily execution, weekly signal generation, backtests, dashboard rendering, checks, syncs, and notifications are Python/shell workflows.
- LLM jobs are judgement jobs: find/read news, classify drivers, score market/sector sentiment, explain weekly performance, and propose monthly strategy changes.
- Preserve agent isolation. An agent may read/write its own `data/<market>/<agent>/...` and `reports/<market>/<agent>/...`; do not let it read another agent's private notes or alt-factor CSVs.
- Sentiment ingestion is market-aware for `a_share`, `hk`, and `us`. Write rows under `data/<market>/<agent>/alt_factors/` and let `run-weekly` consume only that market's rows.
- Long-term tri-market closed loops sync back to ECS by default after local dashboard refresh. Skip ECS sync only when the operator explicitly says 不同步, 只本地, or 不要更新远端.
- This skill is time-aware and idempotent. Every run must check the current time, determine which daily/weekly/monthly tasks are due, inspect existing local and ECS artifacts, and skip tasks already completed for the same `as_of`, `week_end`, or month.

## What The System Already Does

ECS owns A-share deterministic runtime:

- `stock-analyze-market-data.timer`: fetches Tushare data and starts both agents' `run-daily --offline`.
- `stock-analyze-weekly-trigger.timer`: starts both agents' `run-weekly --offline` using Friday cache.
- `stock-analyze-monthly-review.timer`: writes monthly comparison artifacts and refreshes the competition dashboard.
- `stock-analyze-dashboard.service`: serves the dashboard on ECS.

The operator machine owns LLM and overseas workflows:

- News search, trend judgement, sentiment scoring, weekly review notes, and monthly strategy evolution.
- HK/US yfinance runs through `scripts/run-overseas.sh`, because they require the local Hong Kong residential proxy.
- Local/ECS synchronization with `scripts/sync-from-ecs.sh` and `scripts/sync-to-ecs.sh`.

## LLM Task Map

| Task | Market | Frequency | LLM role | System feedback |
| --- | --- | --- | --- | --- |
| Market news and trend analysis | A-share/HK/US | Weekly or ad hoc | Search news, summarize drivers, classify risk-on/risk-off | Structured JSON plus market sentiment CSV |
| Market sentiment factor | A-share/HK/US | Weekly | Produce scalar score/confidence/drivers/sources | `data/<market>/<agent>/alt_factors/market_sentiment.csv` |
| Sector sentiment factor | A-share/HK/US | Weekly or manual | Score industries from news, policy, flows, earnings, and macro events | `data/<market>/<agent>/alt_factors/sector_sentiment.csv` |
| Weekly review | Agent scope | Weekly | Explain latest briefing, data quality, attribution, next plan | `data/<market>/<agent>/notes/*-weekly-review.md` where workflow supports it |
| Monthly strategy evolution | Agent scope | Monthly | Propose bounded overlay changes from monthly evidence | Overlay/evolution artifacts after validation |

## Source Plan

There is no in-repo automated news crawler for sentiment.

The LLM must search the web itself or use source links pasted by the operator. Always keep source URLs for audit.

| Market | System market data | LLM news/trend sources |
| --- | --- | --- |
| `a_share` | Tushare cache; `stock_basic.industry` for industry labels | 财联社, 新浪财经, 同花顺, 东方财富, 新华社, 央视新闻, 证券时报 |
| `hk` | yfinance through local HK residential proxy | HKEX, AAStocks, ETNet, HK01 Finance, issuer announcements, Reuters/Bloomberg/CNBC if accessible |
| `us` | yfinance through local HK residential proxy | SEC/issuer IR, Yahoo Finance, MarketWatch, CNBC, Reuters/AP/Bloomberg if accessible, Fed/FOMC releases |

## Standard LLM Output

For every market analysis, produce this shape in the final answer or intermediate file:

```json
{
  "market": "a_share",
  "week_end": "YYYY-MM-DD",
  "market_data_as_of": "YYYY-MM-DD",
  "market_score": 0.0,
  "confidence": 0.0,
  "key_drivers": ["driver1", "driver2", "driver3"],
  "risks": ["risk1", "risk2"],
  "sector_scores": [
    {"industry": "银行", "score": 0.15, "confidence": 0.60, "drivers": ["..."]}
  ],
  "sources": ["https://..."]
}
```

Rules:

- `market_score` and each sector `score` must be in `[-1.0, 1.0]`.
- `confidence` must be in `[0.0, 1.0]`.
- Use 3-5 concise drivers.
- Include source URLs, not just source names, when available.
- If the LLM cannot access enough current news, say so and cap confidence at `0.4`.

## One-Sentence Trigger

The operator can ask:

> 使用 `stock-analyze-workflows`，根据当前时间自动判断该跑日度、周度还是月度任务；用 GPT-5.5 补齐三大市场新闻、行情、行业趋势和市场情绪分析；写入 claude/codex 在 `a_share`/`hk`/`us` 的 market + sector sentiment，刷新三市场 dashboard，默认同步到 ECS，并说明本轮已完成、跳过和补跑的任务。

Treat that as the complete time-aware workflow below.

## Time-Aware Idempotent Runner

Always start with:

```bash
date '+%Y-%m-%d %H:%M:%S %Z %z'
```

Use Asia/Shanghai time unless the operator explicitly asks otherwise.

### Decide what is due

| Cadence | Due window | Target key | Owner |
| --- | --- | --- | --- |
| Daily | Trading weekdays after the market's data window has passed | `as_of=YYYY-MM-DD` | ECS for `a_share`; local operator machine for `hk`/`us` |
| Weekly | After the latest Friday close, normally Saturday/Sunday | `week_end=<latest Friday>` | ECS for `a_share` deterministic run; local operator machine for LLM, HK/US, and sync |
| Monthly | On/after the 1st day of a month for the previous month | `month=YYYY-MM` | ECS writes comparison review; local LLM performs strategy evolution if due |

If today is not in a due window, still verify the latest due window and report that no new work is required.

### Idempotency checks

Before running anything, build a status table for all `(market, agent)` pairs:

- `a_share/claude`, `a_share/codex`, `hk/claude`, `hk/codex`, `us/claude`, `us/codex`.
- Check local paths under `data/<market>/<agent>/...` and `reports/<market>/<agent>/...`.
- Check ECS paths with `ssh ai-baby-aliyun 'cd /opt/stock-analyze/app && ...'`.

Treat a task as complete only when the ledger row and expected artifact both exist:

- Daily complete: `runs.csv` has `command=run-daily`, `status=success`, matching `as_of`, and `daily_nav.csv` exists.
- Weekly complete: `runs.csv` has `command=run-weekly`, `status=success`, matching `as_of`/`week_end`, plus `pending_orders.json` and `reports/<market>/<agent>/weekly_report.md`.
- Market sentiment complete: `market_sentiment.csv` has `week_end_date=<week_end>`.
- Sector sentiment complete: `sector_sentiment.csv` has at least one row with `week_end=<week_end>`.
- Dashboard complete: `reports/competition/dashboard.html` exists and is newer than the latest changed local artifact.
- Monthly review complete: `data/competition/monthly_reviews/<month>.json` and `reports/competition/monthly_review_<month>.md` exist.
- Monthly strategy evolution complete: the relevant agent/market `config_evolution.csv`, `evolution_log/`, or `evolution_diff/` contains the target month and overlay validation has passed.

Skip complete tasks. Do not overwrite existing sentiment rows with `--force` unless the operator explicitly asks for a correction.

### Staleness rules

- If sentiment for `week_end` was recorded after a successful `run-weekly` for the same `week_end`, rerun that market/agent weekly once so the new sentiment can affect scoring.
- If HK/US weekly succeeded locally but ECS lacks `runs.csv`, `pending_orders.json`, or `weekly_report.md`, do not rerun HK/US; run `./scripts/sync-to-ecs.sh` and verify remote files.
- If A-share weekly is missing on ECS, run it on ECS or let the ECS timer state guide the fix; do not treat old local legacy paths like `data/claude/` as current evidence.
- If monthly review artifacts are missing after the monthly timer's due time, run `competition-monthly-review --month <month>` before monthly strategy evolution.

### Completion contract

At the end of every invocation, the agent must report:

- current time used for scheduling;
- which daily/weekly/monthly tasks were due;
- which tasks were already complete and skipped;
- which tasks were run or synced;
- any remaining blockers;
- remote access command and URL if ECS was updated.

## Tri-Market Closed Loop

Use these steps only for tasks that the idempotency checks marked incomplete or stale.

1. Refresh local state if needed, but do not overwrite newer local HK/US run outputs:

```bash
./scripts/sync-from-ecs.sh --exclude-cache
```

2. Refresh or verify market data only when due and incomplete:

```bash
python3 -m stock_analyze.cli prepare-market-data
./scripts/run-overseas.sh weekly hk
./scripts/run-overseas.sh weekly us
```

If a command is inappropriate for the current machine, explain why and use the freshest existing cache instead.

3. Ask GPT-5.5 to search/read each due market's news, market data context, and sector/industry trends. Use the A-share prompt templates as the base schema:

- `stock_analyze/markets/a_share/alt_factors/prompts/market_sentiment_v1.md`
- `stock_analyze/markets/a_share/alt_factors/prompts/sector_sentiment_v1.md`

Market source focus:

- `a_share`: policy, liquidity, index breadth, Tushare universe industries, 财联社/新浪/同花顺/东方财富/新华社/证券时报.
- `hk`: HKEX disclosures, HSI/HSCEI rotation, China ADR spillover, CN/HK policy, property/financials/tech, HKD liquidity.
- `us`: Fed, CPI/PCE/jobs, earnings revisions, mega-cap tech, sector ETF/rates/credit/VIX, SEC/issuer IR.

4. Record market sentiment only for missing `(market, agent, week_end)` rows:

```bash
python3 -m stock_analyze.cli record-sentiment \
  --market <a_share|hk|us> \
  --agent <claude|codex> \
  --week-end YYYY-MM-DD \
  --score 0.10 \
  --confidence 0.60 \
  --drivers "driver1,driver2,driver3" \
  --sources "https://source1|https://source2" \
  --llm-model gpt-5.5 \
  --prompt-version v1
```

5. Record sector sentiment only for missing `(market, agent, week_end)` rows:

```bash
python3 -m stock_analyze.cli record-sector-sentiment \
  --market <a_share|hk|us> \
  --agent <claude|codex> \
  --week-end YYYY-MM-DD \
  --json '{"llm_model":"gpt-5.5","sectors":[{"industry":"银行","score":0.15,"confidence":0.60}]}'
```

6. Inspect what was written:

```bash
python3 -m stock_analyze.cli sentiment-log --market <a_share|hk|us> --agent <claude|codex>
```

7. Run or refresh only the relevant stale deterministic loop:

```bash
python3 -m stock_analyze.cli --agent <claude|codex> --market a_share run-weekly --offline
python3 -m stock_analyze.cli --agent <claude|codex> --market hk run-weekly
python3 -m stock_analyze.cli --agent <claude|codex> --market us run-weekly
python3 -m stock_analyze.cli competition-dashboard --market all
```

For A-share, prefer the ECS-owned runtime when checking/rerunning scheduled daily/weekly work. For HK/US, use `scripts/run-overseas.sh` when running both agents on the local machine:

```bash
./scripts/run-overseas.sh daily both
./scripts/run-overseas.sh weekly both
```

8. Sync CSVs, overlays, HK/US local run artifacts, reports, and dashboard state back to ECS by default unless the operator explicitly says 不同步, 只本地, or 不要更新远端:

```bash
./scripts/sync-to-ecs.sh
```

`sync-to-ecs.sh` pushes `data/<market>/<agent>/alt_factors/`, `configs/agents/<agent>_<market>.yaml`, and HK/US local-owned run artifacts (`runs.csv`, `daily_nav.csv`, `pending_orders.json`, reports). It then refreshes the ECS competition dashboard by default. Use `SA_ECS_AFTER_SYNC=0` only when intentionally pushing files without refreshing the remote dashboard.

9. Report remote access after sync:

```bash
ssh -L 8765:127.0.0.1:8765 ai-baby-aliyun
```

Then open `http://127.0.0.1:8765/pro.html`.

## Strategy Feedback Loop

LLM analysis influences strategy only after it is converted into one of three strategy inputs:

1. **Weekly factor input**: structured sentiment rows that `run-weekly` reads during scoring.
2. **Risk-regime input**: market-level risk-on/risk-off state visible as a broadcast factor and dashboard trail.
3. **Monthly evolution evidence**: repeated weekly signals that justify bounded config changes after validation.

Do not let raw prose directly change trades. Always convert prose into structured score/confidence/drivers/sources first.

### Implemented closed-loop path

```text
LLM news/trend/market-data analysis
  -> record-sentiment --market <market>
  -> record-sector-sentiment --market <market>
  -> data/<market>/<agent>/alt_factors/*.csv
  -> run-weekly --market <market> reads latest eligible rows
  -> factor pipeline scores candidates if overlay enables the factor
  -> pending_orders.json
  -> next run-daily executes due orders
  -> competition-dashboard shows decisions, task status, and tri-market market-sentiment trail
  -> sync-to-ecs.sh publishes the updated closed loop to ECS by default
  -> ECS dashboard refresh exposes the same state remotely
```

Current factor behavior:

- `<agent>_market_sentiment_1w` is active in all six overlays at small weight. It is a broadcast factor, so it shifts composite scores uniformly and serves as risk-regime evidence rather than a stock picker.
- `<agent>_sector_sentiment` is active in all six overlays at small weight. It can change cross-sectional ranking because each stock inherits its industry's score.
- A-share industry labels come from Tushare. HK/US industry labels come from yfinance `industry`/`sector`; unknown industries map to missing values.
- If the CSV has no eligible row for `as_of`, the configured factor contributes zero or missing coverage rather than inventing a value.

### How to affect strategy safely

Use small, auditable changes first:

- Add or adjust `<agent>_sector_sentiment` weight only after a few weeks of source-backed records.
- Treat market-level sentiment as a risk regime, not a stock picker. Positive regime may allow normal exposure; negative regime should raise cash, reduce turnover, tighten filters, or tilt toward low-vol/quality/dividend factors.
- Require validation/backtest gates before monthly config changes become deployable.
- Keep a dashboard trail: latest score, confidence, drivers, sources, and whether the factor was active.

### Three-market target design

| Market | Weekly tactical impact | Monthly strategy impact |
| --- | --- | --- |
| `a_share` | Sector sentiment affects per-stock ranking through Tushare industry labels; market sentiment records risk regime. | Reweight value/quality/momentum/low-vol/dividend, adjust industry cap or filters after monthly validation. |
| `hk` | Sector sentiment affects per-stock ranking when yfinance industry labels are available; market sentiment records HK risk regime. | Tune HSI/HSCEI factor weights, concentration, and defensive posture from repeated HK evidence. |
| `us` | Sector sentiment affects per-stock ranking when yfinance sector/industry labels are available; market sentiment records US risk regime. | Tune momentum/quality/low-vol/dividend tilts from macro, earnings, and sector evidence. |

Remaining long-term hardening:

- Add market-specific prompt files for HK/US instead of reusing the A-share schema plus source guidance.
- Add an automated weekly sentiment runner if the operator wants less manual LLM orchestration.
- Turn risk-regime evidence into explicit cash/exposure controls only after validation gates exist.

## Natural-Language Trigger Patterns

When the operator asks any of the following, use this skill:

- "运行一下 `stock-analyze-workflows`。"
- "按当前时间检查并补齐三大市场今天/本周/本月该跑的任务。"
- "帮我跑本周三大市场的新闻和趋势分析，用 GPT-5.5，给出市场情绪分数和来源。"
- "用一句话跑完三大市场新闻、行情和情绪分析，默认同步到 ECS，并把结果反馈到 claude/codex 的后续策略。"
- "给 claude 和 codex 分别补录本周 A 股、港股、美股市场情绪和行业情绪。"
- "检查当前 sentiment 有没有进入策略打分，如果没有，告诉我缺哪条链路。"
- "把本周新闻、行业趋势、风险点整理成 monthly strategy 能用的输入。"

## Monthly Evolution

Monthly evolution is an LLM task because it rewrites strategy intent. Keep it bounded:

- Read monthly briefing and review artifacts first.
- Change only allowed overlay fields.
- Record reasoning in evolution artifacts.
- Run validation/backtest gates before treating changes as deployable.
- Do not sync to ECS as part of a note-only weekly review unless the operator requested the full tri-market closed loop. The full closed loop syncs to ECS by default.

## Common Mistakes

- Mistaking `run-weekly` for an LLM task. It only reads existing CSV/config and generates signals.
- Recording market sentiment and assuming it creates alpha. It is currently a uniform shift.
- Forgetting to pass `--market`; default is `a_share`.
- Saving sentiment under legacy `data/<agent>/alt_factors` while code reads `data/<market>/<agent>/alt_factors`.
- Pretending raw LLM prose affects strategy. Only CSV rows plus active overlay factors affect weekly scoring.
- Producing sentiment without source URLs for market sentiment; keep sources auditable.
- Treating ECS sync as optional in the long-term tri-market closed loop. It is default unless the operator explicitly says not to sync.
- Rerunning a task just because it is due by calendar. Calendar due only creates a candidate task; ledger rows plus artifacts decide whether it still needs work.
- Treating local HK/US success as remote completion before `sync-to-ecs.sh` has published `runs.csv`, `pending_orders.json`, and reports to ECS.
