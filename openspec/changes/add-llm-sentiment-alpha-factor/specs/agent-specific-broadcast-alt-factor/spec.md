## ADDED Requirements

### Requirement: factor_pipeline recognises broadcast factor names

`stock_analyze.factor_pipeline.is_broadcast_factor(name) -> bool` SHALL return `True` for any factor name matching the regular expression `^(claude|codex)_market_sentiment_1w$`, and `False` for everything else (including the ten classic factors and unknown names).

#### Scenario: Classic and broadcast factors are correctly classified

- **WHEN** `is_broadcast_factor("pe")` is called
- **THEN** the returned value is `False`

- **WHEN** `is_broadcast_factor("claude_market_sentiment_1w")` is called
- **THEN** the returned value is `True`

- **WHEN** `is_broadcast_factor("codex_market_sentiment_1w")` is called
- **THEN** the returned value is `True`

- **WHEN** `is_broadcast_factor("made_up_factor")` is called
- **THEN** the returned value is `False`

### Requirement: Broadcast factor value is uniformly applied across candidates

`stock_analyze.factor_pipeline.compute_composite_score(candidates, overlay, *, as_of, agent_id, repo_root)` SHALL — when the overlay's `factors` dict contains a broadcast factor whose value resolves to a scalar — add `sign × weight × value` uniformly to every candidate's composite score, where `sign = +1` for `direction='high'` and `sign = -1` for `direction='low'`.

The broadcast factor SHALL skip winsorize / z-score / industry-neutralization (those pre-processing steps require cross-sectional variance which a constant lacks).

#### Scenario: Broadcast factor shifts every candidate by the same delta

- **GIVEN** three candidates with distinct classic-factor scores (PE: 5/10/8, ROE: 0.08/0.12/0.10)
- **AND** the sentiment CSV holds a single row `(week_end=2026-05-22, score=0.5)` for `claude`
- **AND** overlay_a uses only classic factors `{pe: 0.5/low, roe: 0.5/high}`
- **AND** overlay_b is overlay_a with `claude_market_sentiment_1w: 0.10/high` added (other weights rescaled to 0.45/0.45)
- **WHEN** `compute_composite_score(candidates, overlay_a, as_of=date(2026, 5, 25), agent_id="claude", repo_root=tmp)` and `compute_composite_score(candidates, overlay_b, ..., agent_id="claude", ...)` are called
- **THEN** the pairwise difference between any two candidates' scores is identical under both overlays (`scores_b[i] - scores_b[j] == scores_a[i] - scores_a[j]`)
- **AND** all candidates' scores under overlay_b differ from overlay_a by exactly the same constant

#### Scenario: Missing sentiment row yields no contribution

- **GIVEN** an overlay with `claude_market_sentiment_1w: 0.10/high`
- **AND** no sentiment row exists for the relevant week
- **WHEN** `compute_composite_score(...)` is called
- **THEN** the broadcast factor contributes `0` (or the existing missing-factor re-weighting is applied)
- **AND** the function does not raise

### Requirement: overlay_guard rejects cross-agent factor references

`stock_analyze.overlay_guard.validate_factor_name(name, agent_id)` SHALL:

- Return without error when `name` is one of the ten classic factors (`pe`, `pb`, `roe`, `gross_margin`, `debt_ratio`, `net_profit_growth`, `momentum_20`, `momentum_60`, `low_volatility_60`, `dividend_yield`).
- Return without error when `name` matches `^(claude|codex)_market_sentiment_1w$` AND the regex's agent prefix equals `agent_id`.
- Raise `OverlayCrossAgentFactor` when `name` matches the regex but the agent prefix differs from `agent_id` (e.g. `claude` overlay using `codex_market_sentiment_1w`).
- Raise `OverlayUnknownFactor` for anything else.

#### Scenario: Own agent's alt-factor is allowed

- **WHEN** `validate_factor_name("claude_market_sentiment_1w", agent_id="claude")` is called
- **THEN** no exception is raised

#### Scenario: Cross-agent reference is rejected

- **WHEN** `validate_factor_name("codex_market_sentiment_1w", agent_id="claude")` is called
- **THEN** the function raises `OverlayCrossAgentFactor`
- **AND** the message mentions both the offending factor name and the actual `agent_id`

#### Scenario: Unknown factor is rejected with distinct error

- **WHEN** `validate_factor_name("made_up_factor", agent_id="claude")` is called
- **THEN** the function raises `OverlayUnknownFactor`
- **AND** does NOT raise `OverlayCrossAgentFactor`

#### Scenario: Classic factor still works

- **WHEN** `validate_factor_name("pe", agent_id="claude")` is called
- **THEN** no exception is raised
