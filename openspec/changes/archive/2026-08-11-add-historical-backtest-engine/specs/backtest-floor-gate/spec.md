## ADDED Requirements

### Requirement: Gate exposes `validate_overlay_via_backtest` and integrates with `evolution_writer`

`stock_analyze.backtest.gate.validate_overlay_via_backtest(overlay) -> BacktestMetrics` SHALL run a backtest of `overlay` over the fixed validation window (default `2025-01-01` → `2026-04-30`) and either:

- return the computed `BacktestMetrics` when all floor thresholds hold, OR
- raise `BacktestFloorBreach(breach_type, metrics)` when any floor threshold is violated.

`stock_analyze.evolution_writer.write_evolution` SHALL call `validate_overlay_via_backtest(new_overlay)` immediately after `overlay_guard.validate(...)` and before the `_history/` backup. If the gate raises, the writer SHALL NOT rewrite `configs/agents/<agent>.yaml`, SHALL NOT create the `_history/` backup, and SHALL NOT append a row to `data/<agent>/config_evolution.csv`. The writer SHALL persist a `data/<agent>/evolution_log/<YYYY-MM>-floor-breach.md` describing the failure and re-raise the exception so the LLM is forced to redesign.

#### Scenario: Passing overlay returns metrics and writer proceeds

- **GIVEN** an overlay that backtests over the validation window with `max_drawdown=-0.08`, `sharpe=1.2`, `cum_return=0.05`
- **WHEN** `evolution_writer.write_evolution` calls `validate_overlay_via_backtest(new_overlay)`
- **THEN** the gate returns a `BacktestMetrics(cum_return=0.05, sharpe=1.2, max_drawdown=-0.08, ...)`
- **AND** the writer proceeds to back up the prior overlay to `configs/agents/_history/<old_hash>.yaml`
- **AND** `configs/agents/<agent>.yaml` is rewritten with the new content
- **AND** `data/<agent>/evolution_log/<YYYY-MM>.md` records the metrics

#### Scenario: Synthetic crash overlay triggers breach and yaml is unchanged

- **GIVEN** a synthetic overlay that backtests to `cum_return=-0.50`, `max_drawdown=-0.45`, `sharpe=-1.2` over the validation window
- **AND** the existing `configs/agents/claude.yaml` has content hash `H_pre`
- **WHEN** `evolution_writer.write_evolution(agent_id='claude', new_overlay=crash, ...)` is called
- **THEN** `validate_overlay_via_backtest` raises `BacktestFloorBreach` whose `breach_type` identifies the failed threshold (e.g. `max_dd_exceeded`) and whose `metrics` carries the five numbers
- **AND** `configs/agents/claude.yaml` still has content hash `H_pre` (unchanged)
- **AND** no new file is created under `configs/agents/_history/`
- **AND** no new row is appended to `data/claude/config_evolution.csv`
- **AND** `data/claude/evolution_log/<YYYY-MM>-floor-breach.md` exists and contains the failure type, the five validation metrics, the rejected overlay snapshot, and the LLM's original reasoning

### Requirement: Floor thresholds are configurable via `competition.yaml.backtest.floor`

The gate SHALL read its three floor thresholds from `configs/competition.yaml` under the `backtest.floor` key:

- `max_drawdown` (default `0.25`): backtest is rejected when `abs(max_drawdown) > max_drawdown` (i.e. max DD worse than -25% triggers breach).
- `sharpe_floor` (default `-0.5`): backtest is rejected when `sharpe < sharpe_floor`.
- `cum_return_floor` (default `-0.15`): backtest is rejected when `cum_return < cum_return_floor`.

These three fields are **not** baseline-locked — the operator MAY adjust them — but the agent overlay SHALL NOT override them (the `overlay_guard` rejects any attempt to set `backtest.*` from an overlay because only the seven permitted overlay top-level keys are allowed).

#### Scenario: Gate reads thresholds from competition.yaml

- **GIVEN** `configs/competition.yaml` contains `backtest.floor: {max_drawdown: 0.25, sharpe_floor: -0.5, cum_return_floor: -0.15}`
- **WHEN** the gate validates an overlay whose validation-window metrics are `max_drawdown=-0.30`, `sharpe=0.1`, `cum_return=0.02`
- **THEN** the gate raises `BacktestFloorBreach(breach_type='max_dd_exceeded', metrics=...)` because `abs(-0.30) > 0.25`

#### Scenario: Borderline overlay (just above floor) is accepted

- **GIVEN** thresholds `{max_drawdown: 0.25, sharpe_floor: -0.5, cum_return_floor: -0.15}`
- **AND** an overlay whose validation window metrics are `max_drawdown=-0.249`, `sharpe=-0.49`, `cum_return=-0.14`
- **WHEN** `validate_overlay_via_backtest(overlay)` is called
- **THEN** the gate returns the metrics (no raise) — all three floors hold by a thin margin

#### Scenario: Operator-tightened floor takes effect on next gate call

- **GIVEN** the operator edits `configs/competition.yaml` to set `backtest.floor.max_drawdown: 0.15`
- **WHEN** the next `validate_overlay_via_backtest` runs against an overlay with `max_drawdown=-0.20`
- **THEN** the gate raises `BacktestFloorBreach(breach_type='max_dd_exceeded', ...)` even though the same overlay would have passed under the prior default of `0.25`

### Requirement: Gate only blocks catastrophic backtests, never sub-optimal ones

The gate SHALL be designed as a floor (anti-disaster guard), not a relative-performance filter. The gate SHALL NOT require the new overlay to beat the previous overlay, beat the benchmark, or improve any metric. Only the three floor thresholds matter. This is to avoid incentivising the LLM to overfit to the validation window through iterative re-tuning.

#### Scenario: Overlay that underperforms benchmark by 3pp is still accepted

- **GIVEN** an overlay with validation-window `cum_return=0.02`, `sharpe=0.1`, `max_drawdown=-0.08`
- **AND** the benchmark return over the same window is `0.05`
- **WHEN** the gate validates the overlay
- **THEN** the gate returns the metrics (no raise) because all three floor thresholds hold, even though the overlay underperforms the benchmark by 3pp

#### Scenario: Overlay with flat performance (Sharpe ~ 0) is still accepted

- **GIVEN** an overlay whose validation window metrics are `cum_return=0.00`, `sharpe=0.05`, `max_drawdown=-0.10`
- **WHEN** the gate validates it
- **THEN** the gate returns metrics (no raise) — weak but not catastrophic

### Requirement: Gate uses in-memory mode for speed

The gate SHALL invoke `run_backtest(..., in_memory=True)` so that per-day disk writes are skipped during validation. Only the final `BacktestMetrics` result is required to make the pass/fail decision; the gate SHALL NOT persist `daily_nav.csv` / `trades.csv` for its own runs (those go to `data/<agent>/backtest/validation/<YYYY-MM>/` only when triggered for briefing display, which is a separate flow).

#### Scenario: Gate completes within wall-clock budget

- **GIVEN** the validation window 2025-01-01 → 2026-04-30 (~16 months, ~340 trading days)
- **WHEN** `validate_overlay_via_backtest(overlay)` runs end-to-end
- **THEN** the call completes in under 5 minutes on a standard development machine
- **AND** the returned `BacktestMetrics` is identical (within floating-point tolerance) to a `run_backtest(..., in_memory=False)` run over the same window
