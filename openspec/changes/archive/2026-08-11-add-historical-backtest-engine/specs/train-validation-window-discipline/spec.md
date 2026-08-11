## ADDED Requirements

### Requirement: Monthly briefing renders training and validation windows with different detail levels

`stock_analyze.agent_briefing.build_monthly_briefing(agent_id, month)` SHALL render two distinct backtest sections in the briefing markdown:

- `## 训练窗口回测（2021-01-01 → 2024-12-31）` with `detail_level=full` — shows monthly returns, factor-by-factor contribution decomposition, per-month drawdown, industry exposure, and the five summary metrics.
- `## 验证窗口回测（2025-01-01 → 2026-04-30）` with `detail_level=aggregate_only` — shows exactly five numbers (`cum_return`, `annual_return`, `sharpe`, `max_drawdown`, `information_ratio`) and nothing else.

Each section SHALL be sourced from a backtest run already persisted under `data/<agent>/backtest/training/<latest_run>/` or `data/<agent>/backtest/validation/<latest_run>/` respectively. The briefing builder SHALL NOT recompute the backtest at briefing time; it SHALL only read the latest persisted summary.

#### Scenario: Training section includes monthly detail; validation section does not

- **GIVEN** persisted training results at `data/claude/backtest/training/2026-06/` (with `daily_nav.csv`, `factor_runs/`, `performance_summary.json`)
- **AND** persisted validation results at `data/claude/backtest/validation/2026-06/` (with the same files)
- **WHEN** `build_monthly_briefing(agent_id='claude', month='2026-06')` is called
- **THEN** the rendered markdown includes a `## 训练窗口回测` section that contains a monthly table (12+ rows for years 2021-2024), a factor contribution table (one row per active factor), and the five summary metrics
- **AND** the rendered markdown includes a `## 验证窗口回测` section that contains exactly five labelled numbers (累计收益 / 年化收益 / Sharpe / 最大回撤 / 信息比率) and nothing else — no monthly table, no factor contribution table, no per-stock breakdown, no per-industry attribution

### Requirement: Validation `aggregate_only` mode emits exactly five numbers

When `detail_level='aggregate_only'` is rendered, the output SHALL contain exactly five numeric fields. The renderer SHALL NOT emit per-month, per-factor, per-stock, per-industry, or any other lower-granularity decomposition for the validation window — those would defeat the information-isolation purpose of the validation window.

#### Scenario: `aggregate_only` output has exactly 5 numbers

- **GIVEN** a validation-window backtest result with `cum_return=0.05`, `annual_return=0.038`, `sharpe=1.1`, `max_drawdown=-0.08`, `information_ratio=0.6`
- **WHEN** the briefing renders the validation section in `aggregate_only` mode
- **THEN** the section markdown contains the five numbers `+5.0%`, `+3.8%`, `1.10`, `-8.0%`, `0.60` (rendered with their labels)
- **AND** the section does NOT contain any of the strings `月度`, `factor`, `因子`, `行业`, `industry`, `单股`, or per-month dates such as `2025-01`, `2025-02`, etc.
- **AND** counting numeric tokens in the section (where a numeric token is a `±d+\.\d+%` or `\d+\.\d+` pattern) yields exactly 5

#### Scenario: Training `full` mode surfaces factor decomposition

- **GIVEN** a training-window backtest result with non-zero contributions for `pe`, `roe`, `momentum_60`
- **WHEN** the briefing renders the training section in `full` mode
- **THEN** the section markdown contains a `因子贡献` table with one row per non-zero-contribution factor
- **AND** the section contains a `月度热力图` table or list with at least one entry per training-window month (≥ 36 rows for 2021-2024)

### Requirement: Window boundaries are fixed and documented

The three windows SHALL have these fixed boundaries:

| Window     | Range                       | Information Visibility for LLM     |
|------------|-----------------------------|------------------------------------|
| Training   | `2021-01-01` → `2024-12-31` | full detail                        |
| Validation | `2025-01-01` → `2026-04-30` | aggregate (5 numbers) only         |
| Live OOS   | `2026-05-18` onward         | not persisted as backtest output   |

The boundaries SHALL be constants in `stock_analyze.backtest.engine` (e.g. `TRAINING_START`, `TRAINING_END`, `VALIDATION_START`, `VALIDATION_END`) and SHALL NOT be overridable per agent overlay.

#### Scenario: Validation window dates are fixed in engine constants

- **GIVEN** the `stock_analyze.backtest.engine` module loaded
- **WHEN** the gate (`validate_overlay_via_backtest`) constructs its run
- **THEN** the run uses `start=date(2025, 1, 1)` and `end=date(2026, 4, 30)` regardless of agent
- **AND** these dates are read from module-level constants, not from per-agent overlay

#### Scenario: Overlay cannot move the validation window

- **GIVEN** an overlay that attempts to set `backtest.validation_start: 2024-01-01`
- **WHEN** `overlay_guard.validate(...)` runs
- **THEN** the guard raises `OverlayUnknownTopLevelKey` because `backtest` is not in the seven permitted overlay top-level keys

### Requirement: Documented soft-constraint against validation-window over-fitting

`CLAUDE.md` §10 and `AGENTS.md` §10 SHALL contain language stating: "验证窗口的回测结果只用于'是否通过 gate'。不允许针对验证窗口的失败反复迭代 overlay — 应基于训练窗口的发现重新设计。" This is a documentation-level soft constraint; the engine SHALL NOT enforce it at runtime. The `aggregate_only` detail level for validation in the briefing is the primary engineering mitigation.

#### Scenario: CLAUDE.md and AGENTS.md include the soft-constraint clause

- **GIVEN** the current versions of `CLAUDE.md` and `AGENTS.md` after this change is applied
- **WHEN** searching the files for the string `验证窗口`
- **THEN** both files contain a §10 paragraph stating that the validation-window backtest is for gate admission only
- **AND** the paragraph explicitly says strategy redesign SHALL be grounded in the training window, not in iterative tuning against the validation window
