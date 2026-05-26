## ADDED Requirements

### Requirement: Overlay guard validates schema and lock fields only

`stock_analyze.overlay_guard.validate(agent_id, overlay, repo_root, baseline=None)` SHALL raise an exception for any of the following violations, but SHALL NOT judge strategy quality (returns / IR / style drift / overfitting):

- Top-level keys outside `{agent_id, strategy_id, name, factors, factor_processing, portfolio_controls, filters}` → `OverlayUnknownTopLevelKey`
- Touching baseline-locked fields (`initial_cash`, `accounts.*`, `schedule.*`, `trading.*`) → `OverlayBaselineLocked`
- `factors.<name>` where name not in `AVAILABLE_FACTORS` whitelist → `OverlayUnknownFactor`
- `factors.<name>.weight` not in `[0, 1]` → `OverlayInvalidWeight`
- Invalid YAML structure → `OverlayInvalidYAML`

#### Scenario: Guard accepts a strategy-aggressive but schema-valid overlay
- **GIVEN** overlay with `factors.pe.weight = 0.95` and all other weights summing close to 1
- **AND** no baseline lock violation, all factors in whitelist
- **WHEN** `overlay_guard.validate(...)` is called
- **THEN** returns normally (no raise)
- **AND** no warning printed about "extreme weight"

#### Scenario: Guard rejects baseline lock violation
- **GIVEN** overlay containing `accounts: [{id: hs300, cash: 600000}]`(试图改基线现金)
- **WHEN** `overlay_guard.validate(...)`
- **THEN** raises `OverlayBaselineLocked` with field name `accounts.0.cash`

#### Scenario: Guard rejects unknown factor name
- **GIVEN** overlay with `factors.pez.weight = 0.10`(typo)
- **WHEN** `overlay_guard.validate(...)`
- **THEN** raises `OverlayUnknownFactor` with name `pez` and the valid whitelist printed

### Requirement: LLM writes overlay directly with audit trail

When an LLM agent decides on monthly strategy changes, it SHALL:

1. Write the new `configs/agents/<agent>.yaml` directly(overwriting the old one)
2. Write `data/<agent>/evolution_log/<YYYY-MM>.md` containing Chinese markdown reasoning(≤2000 字)
3. Write `data/<agent>/evolution_diff/<YYYY-MM>.json` with structured diff
4. Append one row to `data/<agent>/config_evolution.csv` including `reasoning_file` and `diff_file` columns
5. Before overwriting yaml, the previous yaml SHALL be auto-backed-up to `configs/agents/_history/<old_config_hash>.yaml`

#### Scenario: Monthly evolution writes all 4 outputs
- **GIVEN** claude agent decides to raise `factors.pe.weight` from 0.17 to 0.20 in 2026-06
- **WHEN** `evolution_writer.write_evolution(agent_id="claude", ...)` is called with old + new overlay + reasoning text
- **THEN** `configs/agents/claude.yaml` has new content
- **AND** `configs/agents/_history/128cadd70473.yaml` contains the prior content
- **AND** `data/claude/evolution_log/2026-06.md` exists and contains the reasoning text
- **AND** `data/claude/evolution_diff/2026-06.json` exists with `diff.factors.pe.weight.from=0.17`, `to=0.20`
- **AND** `data/claude/config_evolution.csv` last row has `month=2026-06`, `from_hash=128cadd70473`, `to_hash=<new>`, non-empty `reasoning_file` and `diff_file`

#### Scenario: Guard failure aborts atomic
- **GIVEN** LLM attempts to write an overlay containing `trading.commission_rate: 0.0001`(baseline lock)
- **WHEN** `evolution_writer` runs the overlay_guard pre-check
- **THEN** raises `OverlayBaselineLocked`
- **AND** `configs/agents/claude.yaml` is unchanged
- **AND** no evolution_log / diff / csv row is written
- **AND** no `_history/` backup is created

### Requirement: Referee CLI commands removed

The CLI subcommands `agent-judge-proposals` and `agent-apply-approved-proposals` SHALL no longer exist after this change. `agent-rollback` is retained, but its implementation uses `configs/agents/_history/<hash>.yaml` instead of the old apply-decision audit chain.

#### Scenario: CLI lists only the new validation command
- **WHEN** `python3 -m stock_analyze --help`
- **THEN** stdout includes `validate-overlay`
- **AND** stdout does NOT include `agent-judge-proposals` or `agent-apply-approved-proposals`
- **AND** stdout includes `agent-rollback`

### Requirement: Opponent transparency rules

LLM agent of side `X` SHALL be permitted to read:

- `configs/agents/<other>.yaml` (full content)
- `data/<other>/config_evolution.csv` (full content)
- `data/competition/monthly_reviews/*.json`(unchanged)
- `reports/competition/monthly_review_*.md`(unchanged)

And SHALL NOT read:

- `data/<other>/evolution_log/*.md`
- `data/<other>/notes/*.md`
- `data/<other>/state.json`, `positions.csv`, `daily_nav.csv`, `trades.csv`
- `data/<other>/factor_runs/*`

This rule is enforced by `CLAUDE.md §7` and `AGENTS.md §7` documentation only (no runtime check). Monthly briefing SHALL include an "opponent overlay snapshot" and "opponent history" section so agents do not need to read across the boundary to know what the opponent is doing.

#### Scenario: claude briefing includes codex overlay snapshot
- **WHEN** `build_monthly_briefing(agent_id="claude", month="2026-06")` is called
- **THEN** the markdown output includes a `## 对手 codex 当前 overlay 摘要` section
- **AND** the section lists codex's factors / portfolio_controls / filters values
- **AND** includes a `## 对手 codex 历史改动(近 3 个月)` section
- **AND** does NOT include any of codex's evolution_log or notes content
