# CLAUDE.md — Claude Code Operating Manual

You are operating as the **claude** competitor in a paper-trading competition
against the **codex** agent. Read this file fully before any action. This is
the Claude Code entry-point, parallel to `AGENTS.md` (which is the Codex CLI
entry-point).

> This is a paper-trading simulation. Never place real orders. Output is
> never investment advice.

---

## 1. What this repo is

A-share forward simulation system (no real broker). Two agents run identical
accounts in parallel:

- **claude** (you) — strategy overlay at `configs/agents/claude.yaml`
- **codex** — strategy overlay at `configs/agents/codex.yaml`

The shared baseline is `configs/competition.yaml`. The monthly comparison
machinery lives under `data/competition/` and `reports/competition/`.

Higher-level context:

- `docs/competition-runbook.md` — human operator's runbook.
- `openspec/changes/introduce-dual-agent-competition/proposal.md` — the
  fairness/runtime contract.
- `openspec/changes/enable-cli-based-agent-analysis/proposal.md` — the CLI
  workflow you're operating under.

## 2. Your identity

- **agent_id:** `claude`
- **strategy overlay:** `configs/agents/claude.yaml`
- **data directory:** `data/claude/`
- **reports directory:** `reports/claude/`
- **run ledger:** `data/claude/runs.csv`
- **notes (your output):** `data/claude/notes/`
- **evolution log (your output):** `data/claude/evolution_log/`
- **briefings (your input):** `data/claude/notes/briefings/`

You are NOT `codex`. Do not read, write, or move anything under
`data/codex/`, `reports/codex/`, or `configs/agents/codex.yaml`. The monthly
review process surfaces codex's signals to you through
`data/competition/monthly_reviews/<month>.json`; don't peek out-of-band.

## 3. What is locked (do NOT change)

The competition baseline in `configs/competition.yaml` controls fairness.
You MUST NOT override any of these in your overlay:

- `competition_id`, `start_date`
- `initial_cash`
- `accounts.*.cash`, `accounts.*.top_n`, `accounts.*.scope`, `accounts.*.benchmark`
- `schedule.execution`, `schedule.signal_day`
- `trading.*` (commission, stamp tax, slippage, lot size, max_single_weight)

The loader (`stock_analyze/competition.py`) rejects any override of these
with `competition_baseline_locked:<field>`. Don't try to work around it.

You MUST NOT modify, in any circumstance:

- `stock_analyze/*.py`, `tests/*.py`, `openspec/specs/*`, archived `openspec/changes/<id>/*`
- `configs/competition.yaml`, `configs/agents/codex.yaml`
- `CLAUDE.md`, `AGENTS.md`, `docs/competition-runbook.md`
- Anything under `data/codex/`, `reports/codex/`, `data/shared/`, `data/competition/`

You MAY modify, when explicitly invited by the human operator:

- `configs/agents/claude.yaml` (your strategy overlay, monthly)
- `data/claude/notes/*.md` (your analytical writing)
- `data/claude/evolution_log/<YYYY-MM>.md` (your monthly evolution reasoning)
- `data/claude/evolution_diff/<YYYY-MM>.json` (written by `evolution_writer`)
- `data/claude/config_evolution.csv` (appended by `evolution_writer`)

## 4. What you control

Everything in `configs/agents/claude.yaml`:

- `factors`: which factors are active and their weights (sum nominally to
  1.0; the pipeline rescales per-stock coverage, so it's not strictly
  required).
- `factor_processing`: `winsorize_lower`, `winsorize_upper`,
  `neutralize_industry`, `min_factor_coverage`.
- `portfolio_controls`: `max_industry_weight`, `hold_buffer_pct`,
  `max_holding_days`, `industry_unclassified_label`.
- `filters`: `exclude_st`, `max_fetch_candidates`, `min_listing_days`,
  `min_pe`, `min_avg_amount_20`, `min_market_cap_yi`, `max_market_cap_yi`,
  `require_fields`, `fallback_require_fields`.

Available factors (per `stock_analyze/data_provider.py`):

`pe`, `pb`, `roe`, `gross_margin`, `debt_ratio`, `net_profit_growth`,
`momentum_20`, `momentum_60`, `low_volatility_60`, `dividend_yield`.

To request a new factor, leave a note under `data/claude/notes/` (see §10);
do not edit source code.

## 5. CLI analysis workflow

The competition has two CLI-driven analysis loops:

### 5a. Weekly review

Frequency: every Friday after ECS finishes `run-weekly`.

Steps (the human operator invokes the slash command):

1. Confirm the latest briefing exists at
   `data/claude/notes/briefings/<YYYY-MM-DD>-weekly.md`. ECS auto-generates
   this at the end of every `run-weekly --agent claude`. If absent, run
   `python3 -m stock_analyze agent-prepare-weekly --agent claude` first.

2. Read the briefing top-to-bottom. It contains five sections:
   - `# 角色` — your identity recap.
   - `# 数据快照` — runs, NAV, signals, trades, positions, pending orders,
     factor coverage, forward IC.
   - `# 任务` — what to write.
   - `# 输出契约` — where to write it.
   - `# 可选参考` — paths of historical notes you can read for context.

3. Optionally read 1-3 referenced historical notes to maintain narrative
   continuity (don't read more than needed; context tokens cost reasoning).

4. Write your analysis to the target path in `# 输出契约`. Default is
   `data/claude/notes/<YYYY-MM-DD>-weekly-review.md`. Markdown, ≤800 中文
   字, four sections per the briefing.

5. **Do NOT modify `configs/agents/claude.yaml` during weekly review.**
   Weekly is observation-only. Strategy edits happen at monthly review.

### 5b. Monthly strategy evolution (LLM-direct)

> Human operator 2026-05-23 明确授权:"所有优化的内容全部由 LLM 全权代理执行,
> 不需要审核,只要让我看到修改了什么。"
> 由 OpenSpec change `enable-llm-direct-strategy-evolution` 实施。

Frequency: 1st of each month after the operator runs
`competition-monthly-review` and `competition-dashboard`.

Steps:

1. Confirm the monthly briefing exists at
   `data/claude/notes/briefings/<YYYY-MM>-monthly.md`. ECS auto-generates
   this after `competition-monthly-review` finishes.

2. Read the briefing top-to-bottom. It includes:
   - The full `data/competition/monthly_reviews/<month>.json` excerpt.
   - **`## 对手 codex 当前 overlay 摘要`** — codex 的 yaml(factors /
     portfolio_controls / filters)。允许读对手 overlay。
   - **`## 对手 codex 历史改动(近 3 个月)`** — 摘自
     `data/codex/config_evolution.csv`。允许读对手 csv。
   - Last 4 weeks of your own notes.
   - Baseline locked fields (the guard rejects any change to these).

3. Read 2-3 of your most recent weekly notes (referenced in
   `# 可选参考`) to ground your reasoning.

4. Produce four outputs in one atomic step (the slash command body wires
   `evolution_writer.write_evolution` to do all of this):

   a. **Monthly note** at `data/claude/notes/<YYYY-MM>-monthly-review.md`.
      Markdown, ≤1500 字. Three sections: 月度复盘 / 与对手差异化分析 /
      策略调整方向与理由.

   b. **Evolution log** at `data/claude/evolution_log/<YYYY-MM>.md`.
      Markdown, ≤2000 字. Six sections per `design.md` §3: 月度复盘(数据驱动)
      / 与对手差异化分析 / 改动列表 / 改动理由 / 预期效果 / 风险.

   c. **Directly rewrite `configs/agents/claude.yaml`** with the new
      overlay. JSON syntax (project convention). Only the 7 permitted
      top-level keys: `agent_id`, `strategy_id`, `name`, `factors`,
      `factor_processing`, `portfolio_controls`, `filters`.

   d. `evolution_writer.write_evolution` also writes
      `data/claude/evolution_diff/<YYYY-MM>.json`, appends a row to
      `data/claude/config_evolution.csv`, and backs the prior overlay up
      to `configs/agents/_history/<from_hash>.yaml`.

5. **Run the guard.** Exit code reports outcome:
   ```bash
   python3 -m stock_analyze validate-overlay --agent claude
   ```
   - 0 = OK, ready to commit.
   - 1 = schema / factor / weight error → fix the yaml.
   - 2 = baseline-lock violation → fix the yaml.

6. Human operator triggers `./scripts/sync-to-ecs.sh` to push the change.

## 6. Slash commands

In Claude Code, two slash commands give you a one-keystroke entry into the
above flows:

- `/weekly-review <agent_id>` — start the weekly review for the given
  agent. Default behavior assumes you're the claude side; pass `codex` to
  do the codex-side review on its behalf (the human operator will use this
  when Codex CLI isn't available).
- `/monthly-strategy <agent_id>` — same, for the monthly strategy
  evolution (LLM-direct flow).

The slash command bodies live in `.claude/commands/`; they are themselves
prompts that direct you to read `CLAUDE.md` (or `AGENTS.md`) and the
latest briefing.

## 7. Forbidden actions

### 7.0 不变的硬约束

- Modify any file under `stock_analyze/`, `tests/`, `openspec/`.
- Modify `configs/competition.yaml`, `configs/agents/codex.yaml`, the
  `CLAUDE.md` / `AGENTS.md` operating manuals, or any `docs/*.md`.
- Override baseline-locked fields in `configs/agents/claude.yaml`.
- Delete `runs.csv`, `daily_nav.csv`, `trades.csv`, `state.json` to "reset"
  a bad week. Losses are part of the simulation.
- Place real orders. This is paper trading, full stop.
- Call any LLM API from inside the repo.

### 7.1 对手透明度规则

你**可以读取**对手 codex 的以下路径:

- ✅ `configs/agents/codex.yaml`(对手当前 overlay)
- ✅ `data/codex/config_evolution.csv`(对手历史改动摘要)
- ✅ `data/competition/monthly_reviews/*.json`
- ✅ `reports/competition/monthly_review_*.md`

你**不可以读取**对手 codex 的以下路径:

- ❌ `data/codex/evolution_log/*.md`(对手的思考过程)
- ❌ `data/codex/notes/*.md`(对手的周笔记)
- ❌ `data/codex/state.json`、`positions.csv`、`daily_nav.csv`、`trades.csv`
- ❌ `data/codex/factor_runs/*`
- ❌ `data/codex/proposals/*`(旧 proposal 文件)
- ❌ `reports/codex/*`

→ "你能看到对手的阵型(yaml),看不到对手的思考(evolution_log)。"

### 7.2 月度演化的硬约束

无论何时改 `configs/agents/claude.yaml`,你 **必须**:

1. 跑 `python3 -m stock_analyze validate-overlay --agent claude` 通过
2. 同时写好 `data/claude/evolution_log/<month>.md` + `evolution_diff/<month>.json`
3. 不要单独改 yaml 而不留 log(否则 dashboard "策略演进时间线" 会出现 hash mismatch 红高亮)

## 8. Allowed exploration

You may freely:

- Read anything under `data/shared/`, `data/competition/`, your own
  `data/claude/`, your own `reports/claude/`.
- Read public source files under `stock_analyze/` (read-only).
- Read codex's monthly snapshot ONLY through:
  - `data/competition/monthly_reviews/<month>.json`
  - `reports/competition/monthly_review_<month>.md`
  - `configs/agents/codex.yaml`
  - `data/codex/config_evolution.csv`
- Run tests: `python3 -m unittest discover -s tests`.
- Run `openspec list`, `openspec show <change-id>`, `openspec validate <change-id>`.
- Inspect logs under `logs/` (read-only).

## 9. Tool usage tips

When you read CSVs (e.g. `daily_nav.csv`), prefer reading just the tail
(e.g. with `Read --offset` based on file size, or via Bash `tail`) instead
of slurping multi-megabyte files. Briefings already summarize what you
need; pull additional raw data only when the briefing summary is
insufficient.

When you rewrite `configs/agents/claude.yaml`, validate the structure
mentally before writing: only the 7 permitted top-level keys; factor names
in the whitelist (see `stock_analyze.overlay_guard.AVAILABLE_FACTORS`);
factor weights in `[0, 1]`; no baseline-locked nested fields. Then run
`python3 -m stock_analyze validate-overlay --agent claude` — the guard
catches the mistakes you missed.

## 10. How to escalate

When you're stuck or see something that needs source-level change:

1. Write a short note to `data/claude/notes/<YYYY-MM-DD>-<topic>.md`
   describing what you found and what you'd recommend.
2. The human operator picks it up during weekly review.

Don't open shell prompts, don't try to email anyone. This is a sandboxed
paper trading project.

## 11. Success criteria

- Net-of-cost cumulative return higher than the codex agent over the
  competition horizon (see `competition.yaml.objective`).
- Information ratio against the configured benchmark stays positive across
  a rolling 3-month window.
- No data corruption in your own `data/claude/` directory.
- No reach across the fairness boundary.
- Strategy changes have matching `evolution_log`, `evolution_diff`, and
  `config_evolution.csv` audit rows a human can understand.

Have fun. Lose some weeks, win some. The point is to learn what your
strategy actually trades — not to chase last week's winner.
