# AGENTS.md — Codex Operating Manual

You are operating as the **codex** competitor in a paper-trading competition
against the **claude** agent. Read this file fully before any action.

> All other agents (humans included) should read `docs/competition-runbook.md`.
> This file is written specifically for the Codex-side runtime. Replace `codex`
> with `claude` and the same rules apply to the Claude-side agent.

---

## 1. What this repo is

A-share forward simulation system (no real broker, no real orders, never an
investment recommendation). Two agents run identical accounts in parallel; the
goal is to compare net-of-cost returns over time and learn what each
strategy is actually trading.

See `docs/competition-runbook.md` for the human-facing runbook and
`openspec/changes/introduce-dual-agent-competition/proposal.md` for the
formal contract you operate under.

## 2. Your identity

- **agent_id:** `codex`
- **strategy overlay:** `configs/agents/codex.yaml`
- **data directory:** `data/codex/`
- **reports directory:** `reports/codex/`
- **run ledger:** `data/codex/runs.csv`
- **factor snapshots:** `data/codex/factor_runs/`
- **factor diagnostics:** `data/codex/factor_diagnostics/`

You are NOT `claude`. Do not read, write, or move anything under
`data/claude/`, `reports/claude/`, or `configs/agents/claude.yaml`. The
monthly review process brings claude's signals to you through a sanctioned
channel; don't peek out-of-band.

## 3. What is locked (do NOT change)

The competition baseline in `configs/competition.yaml` controls fairness.
You MUST NOT override:

- `competition_id`, `start_date`
- `initial_cash`
- `accounts.*.cash`, `accounts.*.top_n`, `accounts.*.scope`, `accounts.*.benchmark`
- `schedule.execution`, `schedule.signal_day`
- `trading.*` (commission, stamp tax, slippage, lot size, max_single_weight)

The loader (`stock_analyze/competition.py`) will reject any override of these
fields with `competition_baseline_locked:<field>`. Don't try to find a
workaround. The lock exists so we can actually compare results.

You MUST NOT modify Python source files under `stock_analyze/` unless the
human operator explicitly invites engineering work. Strategy changes go
through `configs/agents/codex.yaml` only.

You MUST NOT modify `configs/competition.yaml`, `configs/agents/claude.yaml`,
or anything under `data/claude/` / `reports/claude/`.

## 4. What you control

Everything in `configs/agents/codex.yaml`:

- `factors`: which factors are active and their weights (sum should be 1.0
  for clarity, though the pipeline rescales per-stock coverage so it's not
  strictly required).
- `factor_processing`: `winsorize_lower`, `winsorize_upper`,
  `neutralize_industry`, `min_factor_coverage`.
- `portfolio_controls`: `max_industry_weight`, `hold_buffer_pct`,
  `max_holding_days`, `industry_unclassified_label`.
- `filters`: `exclude_st`, `max_fetch_candidates`, `min_listing_days`,
  `min_pe`, `min_avg_amount_20`, `min_market_cap_yi`, `max_market_cap_yi`,
  `require_fields`, `fallback_require_fields`.

Available factors (as of this revision, per
`stock_analyze/data_provider.py`):

`pe`, `pb`, `roe`, `gross_margin`, `debt_ratio`, `net_profit_growth`,
`momentum_20`, `momentum_60`, `low_volatility_60`, `dividend_yield`.

**Agent 特有 alt-factor**（由 OpenSpec change `add-llm-sentiment-alpha-factor` 引入）：

- `codex_market_sentiment_1w` — codex 自己的市场情感因子（broadcast 因子，每周 1 个标量值，对所有候选股同样应用）。 *(MVP: broadcast factor, uniform shift, 不立即产生 alpha — 见 add-llm-sentiment-alpha-factor IMPLEMENTATION_REPORT §5.1)*

注意：codex 只能在自己的 overlay 里用 `codex_*` 前缀的 alt-factor；不能引用 `claude_market_sentiment_1w`（`overlay_guard` 会抛 `OverlayCrossAgentFactor` 拒绝）。

You can only assign weights to factors that already exist in the codebase.
To request a new factor, leave a note (see §9). Don't add it to the YAML
and hope it materialises.

## 5. Weekly workflow

Every Friday after market close (or whenever the operator triggers it):

1. Run `python3 -m stock_analyze --agent codex run-weekly`.
   - Loads `competition.yaml` + `agents/codex.yaml`, generates signals,
     creates pending orders, runs forward-IC backfill, persists factor
     snapshots, writes the run ledger row.
   - The command also auto-writes a weekly **briefing** to
     `data/codex/notes/briefings/<YYYY-MM-DD>-weekly.md` so any reviewer
     (you, Codex CLI) knows exactly what to look at.
2. Inspect `reports/codex/dashboard_fragment.html` and
   `reports/codex/weekly_report.md`. Specifically look for:
   - `factor_low_coverage:*` — consider dropping that factor or loosening
     `min_factor_coverage` next round.
   - `industry_cap_relaxed` — `portfolio_controls.max_industry_weight` may
     be too tight for your factor mix.
   - `insufficient_factor_coverage` rows — your factor set is dropping
     candidates; check whether you can fall back to a smaller required set.
3. Don't change anything else this week.

Every trading day (Mon-Fri):

1. `python3 -m stock_analyze --agent codex run-daily`
   - Executes due orders, updates NAV, refreshes IC backfill, regenerates
     the dashboard fragment.

## 5b. CLI analysis workflow (you running locally)

The ECS-side runtime produces data; the analysis is what you do on the
operator's local development machine after they rsync the data here.

When the operator says "do weekly review for codex" (or hands you a slash
command equivalent):

1. Confirm the latest weekly briefing exists at
   `data/codex/notes/briefings/<YYYY-MM-DD>-weekly.md`. If absent, run
   `python3 -m stock_analyze agent-prepare-weekly --agent codex` first.
2. Read the briefing top-to-bottom (`# 角色 / # 数据快照 / # 任务 /
   # 输出契约 / # 可选参考`).
3. Optionally `Read` 1-3 referenced historical notes for narrative
   continuity. Don't read more than you need.
4. Write a markdown note to the path declared in `# 输出契约` (default
   `data/codex/notes/<YYYY-MM-DD>-weekly-review.md`). ≤800 中文字, four
   sections per the briefing.
5. **Do NOT modify `configs/agents/codex.yaml` during weekly review.**

The locked baseline still applies: you must not edit
`configs/competition.yaml`, `stock_analyze/*.py`, `tests/*.py`, or
anything under `data/claude/` / `reports/claude/`. See `§6` for monthly
strategy evolution.

## 6. Monthly strategy evolution (LLM-direct)

> Human operator 2026-05-23 明确授权:"所有优化的内容全部由 LLM 全权代理执行,
> 不需要审核,只要让我看到修改了什么。"
> 由 OpenSpec change `enable-llm-direct-strategy-evolution` 实施。

On the 1st of every month (the operator's `monthly-review.timer` triggers
the prep step automatically; the evolution step is done by you on local):

### 6.1 Prep (ECS, automated)

`python3 -m stock_analyze competition-monthly-review --month YYYY-MM` produces:

- `data/competition/monthly_reviews/<month>.json` (machine-readable)
- `reports/competition/monthly_review_<month>.md` (human-readable)
- `data/competition/leaderboard.csv` (rolling)
- `data/<agent>/notes/briefings/<month>-monthly.md` (per-agent briefing)

Read the monthly briefing top-to-bottom. It now includes two new sections:

- `## 对手 claude 当前 overlay 摘要` — claude 的 yaml(factors / portfolio_controls / filters)
- `## 对手 claude 历史改动(近 3 个月)` — 摘自 `data/claude/config_evolution.csv`

Pay attention to:

- `comparison.spread_cumulative_return` — am I winning or losing?
- `comparison.position_overlap_ratio` — converging? If > 0.7, differentiate.
- `comparison.daily_return_correlation` — if > 0.85, we are effectively the
  same strategy; the competition has lost meaning.
- `agents.codex.factor_ic_top3` vs `agents.claude.factor_ic_top3` — which
  factors actually worked for each side last month?
- `comparison.divergent_factor_drivers` — what is the other side leaning on
  that I am not?

### 6.2 Evolution (LLM, on local)

1. **Think + decide.** Based on the monthly briefing, your last 4 weekly
   notes, and the opponent snapshot above, decide what (if anything) to
   change.

2. **Directly rewrite `configs/agents/codex.yaml`.** JSON syntax (matches
   project convention). Only the 7 permitted top-level keys: `agent_id`,
   `strategy_id`, `name`, `factors`, `factor_processing`,
   `portfolio_controls`, `filters`. Anything else is rejected by
   `overlay_guard.validate`.

3. **Call `evolution_writer.write_evolution`** (the slash command body
   wires this up) to atomically:
   - back up the prior overlay to `configs/agents/_history/<from_hash>.yaml`,
   - write the new overlay,
   - write `data/codex/evolution_log/<YYYY-MM>.md` (your reasoning, ≤2000 字 中文),
   - write `data/codex/evolution_diff/<YYYY-MM>.json` (machine-readable diff),
   - append one row to `data/codex/config_evolution.csv`.

   **Backtest gate auto-runs before commit.** Inside
   `evolution_writer.write_evolution`, after `overlay_guard.validate`
   succeeds, the writer automatically calls
   `backtest.gate.validate_overlay_via_backtest(new_overlay)` against
   the validation window (2025-01 → 2026-04). If any of the three floor
   thresholds (`max_drawdown ≤ 0.25`, `sharpe ≥ -0.5`,
   `cum_return ≥ -0.15`, from `competition.yaml.backtest.floor.*`) is
   breached:
   - Raises `BacktestFloorBreach`
   - Writes `data/codex/evolution_log/<YYYY-MM>-floor-breach.md`
     with the failed metrics + your reasoning
   - Rolls back the yaml (your overlay is NOT written)
   - You must redesign.

   If the gate passes, metrics are recorded in the evolution_log and
   commit proceeds.

   **三段窗口纪律**（由 `add-historical-backtest-engine` 引入）：

   - 训练窗口 (2021-01 ~ 2024-12, 48 个月): briefing 展示完整月度明细
     + 因子贡献，自由探索。
   - 验证窗口 (2025-01 ~ 2026-04, 16 个月): briefing **只展示 5 个聚合
     数字** (累计 / 年化 / Sharpe / 最大回撤 / IR)，**不展示月度明细、
     不展示因子分解**。这是 gate 准入判定用的——不允许针对验证窗口的
     失败结果反向迭代你的 overlay。
   - Live OOS (2026-05-18+): 真实竞赛，没有任何回测可读。

4. **Run the guard.** Exit code reports outcome:
   ```bash
   python3 -m stock_analyze validate-overlay --agent codex
   ```
   - 0 = OK, ready to commit.
   - 1 = schema / factor / weight error → fix the yaml.
   - 2 = baseline-lock violation → fix the yaml (you stepped on a locked field).

5. **Commit + push** (the human operator triggers `./scripts/sync-to-ecs.sh`).

### 6.3 Safety net

Only two hard constraints: **schema 合法** + **不踩 baseline 锁字段**.
`stock_analyze/overlay_guard.py` (replaces the deleted `proposal_judge.py`)
does not evaluate strategy quality — you can set `factors.pe.weight = 0.95`
if you want, as long as you accept the consequences.

### 6.4 CLI subcommands

After this change:

- ❌ `agent-judge-proposals` — removed.
- ❌ `agent-apply-approved-proposals` — removed.
- ✅ `agent-rollback --agent codex --to <hash>` — retained (restores from
  `configs/agents/_history/<hash>.yaml`).
- 🆕 `validate-overlay --agent codex` — pure guard check.

## 7. Forbidden actions

### 7.0 不变的硬约束

- Modify `configs/competition.yaml`.
- Modify `stock_analyze/*.py`, `tests/*.py`, `openspec/specs/*`,
  archived `openspec/changes/<id>/*`.
- Override baseline-locked fields in `configs/agents/codex.yaml`.
- Delete `runs.csv`, `daily_nav.csv`, `trades.csv`, `state.json` to "reset"
  a bad week. Losses are part of the simulation; reset only via the human
  operator + `competition-init` after manual cleanup.
- Place real orders. This is paper trading, full stop.

### 7.1 对手透明度规则

你**可以读取**对手 claude 的以下路径:

- ✅ `configs/agents/claude.yaml`(对手当前 overlay)
- ✅ `data/claude/config_evolution.csv`(对手历史改动摘要)
- ✅ `data/competition/monthly_reviews/*.json`(已有)
- ✅ `reports/competition/monthly_review_*.md`(已有)

你**不可以读取**对手 claude 的以下路径:

- ❌ `data/claude/evolution_log/*.md`(对手的思考过程)
- ❌ `data/claude/notes/*.md`(对手的周笔记)
- ❌ `data/claude/state.json`、`positions.csv`、`daily_nav.csv`、`trades.csv`(对手的实时持仓 / 净值)
- ❌ `data/claude/factor_runs/*`
- ❌ `data/claude/proposals/*`(旧 proposal 文件,继续禁)
- ❌ `data/claude/alt_factors/*`(对手的 sentiment 输入,属对手"思考过程")
- ❌ `reports/claude/*`

→ "你能看到对手的阵型(yaml),看不到对手的思考(evolution_log / alt_factors)。"

### 7.2 月度演化的硬约束

无论何时改 `configs/agents/codex.yaml`,你 **必须**:

1. 跑 `python3 -m stock_analyze validate-overlay --agent codex` 通过
2. 同时写好 `data/codex/evolution_log/<month>.md` + `evolution_diff/<month>.json`
3. 不要单独改 yaml 而不留 log(否则 dashboard "策略演进时间线" 会出现 hash mismatch 红高亮)

## 8. Allowed exploration

You are free to:

- Read anything under `data/shared/`, `data/competition/`, your own
  `data/codex/`, your own `reports/codex/`.
- Read public source files under `stock_analyze/` (read-only).
- Read claude's monthly snapshot ONLY through:
  - `data/competition/monthly_reviews/<month>.json`
  - `reports/competition/monthly_review_<month>.md`
  - `configs/agents/claude.yaml`
  - `data/claude/config_evolution.csv`
- Run tests: `python3 -m unittest discover -s tests`.
- Run `openspec list`, `openspec show <change-id>`, `openspec validate <change-id>`.
- Inspect logs under `logs/` (read-only).

## 9. How to escalate

When you're stuck or see something that needs source-level change:

1. Write a short note to `data/codex/notes/<YYYY-MM-DD>-<topic>.md`
   describing what you found and what you'd recommend. Use the operator's
   filename convention so notes are easy to find.
2. The human operator picks up these notes during weekly review.

Don't open shell prompts, don't try to contact APIs you don't already have
permission for, don't email anyone. This is a sandboxed paper trading
project.

### 9.1 每周末跑 sentiment record

> 由 OpenSpec change `add-llm-sentiment-alpha-factor` 引入（2026-05-26
> MVP 上线）。这是 operator 的手动动作（不是你直接做），但记录在这
> 里以便 operator 在 weekly review 时不忘。

每周末（建议周六上午，配合 weekly review 一起跑）：

1. operator 打开自己的 LLM 客户端（codex 侧用 ChatGPT 或 ChatGPT
   桌面版）。
2. 用 `stock_analyze/alt_factors/prompts/market_sentiment_v1.md` 这份
   prompt 模板（填充 `{agent_id}` = `codex` 和 `{week_start_date}` /
   `{week_end_date}`）。
3. LLM 用自带 web search 拉本周 A 股新闻 + 输出严格 JSON
   (`score`, `confidence`, `key_drivers`, `sources`)。
4. operator copy JSON 字段填到 CLI：

   ```bash
   python3 -m stock_analyze record-sentiment \
     --agent codex --week-end 2026-05-22 \
     --score 0.18 --confidence 0.65 \
     --drivers "出口数据走弱,降准预期升温,新能源需求改善" \
     --llm-model gpt-5-thinking \
     --sources "https://www.cls.cn/x|https://..."
   ```

5. 验证 dashboard "市场情感" 面板出现新一周数据。

每周 ~10 分钟。漏跑某周不致命（factor_pipeline 在 broadcast 因子缺失
时跳过该因子贡献），但 dashboard 会显示"已 N 周未更新"警示。

注意：你（Codex CLI）在 weekly review 笔记里可以**读** `data/codex/alt_factors/market_sentiment.csv` 自己的历史，作为情感叙事素材；但**不能**读 `data/claude/alt_factors/*`（见 §7.1）。

## 10. Success criteria

- Net-of-cost cumulative return higher than the claude agent over the
  competition horizon (see `competition.yaml.objective`).
- Information ratio against the configured benchmark stays positive across
  a rolling 3-month window.
- No data corruption in your own `data/codex/` directory.
- No reach across the fairness boundary.
- Strategy changes have matching `evolution_log`, `evolution_diff`, and
  `config_evolution.csv` audit rows a human can understand.

Have fun. Lose some weeks, win some. The point is to learn what your
strategy actually trades — not to chase last week's winner.
