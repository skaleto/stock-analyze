# Tasks

## 1. Scaffolding (no behaviour change)

- [ ] 1.1 Add `stock_analyze/backtest/scoring.py` with empty
  `score_with_overlay(view, overlay, as_of, universe) -> pd.DataFrame`
  signature and docstring. No callers yet.
- [ ] 1.2 Add a feature flag read in `engine._compute_signals`:
  `use_full_pipeline = overlay.get("backtest", {}).get("use_full_pipeline", False)`.
  Default OFF so this PR is a no-op until the rest of the chain lands.
- [ ] 1.3 Verify suite still passes with `python3 -m unittest discover -s tests`.

## 2. PointInTimeView broadcast accessor

- [ ] 2.1 Add `PointInTimeView.broadcast(factor_name: str, as_of: date) -> float`.
  Returns `0.0` for the training window (no sentiment cache),
  reads from `data/<agent>/alt_factors/market_sentiment.csv` for the
  validation window if it exists.
- [ ] 2.2 Add unit test asserting:
  - returns `0.0` when sentiment cache is missing,
  - returns the score from the most recent row with `week_end <= as_of`,
  - never returns a future row (point-in-time).
- [ ] 2.3 Document the contract in `data_view.py` module docstring.

## 3. score_with_overlay implementation

- [ ] 3.1 Assemble the per-stock factor frame from the view:
  `code, industry, pe, pb, roe, momentum_20, momentum_60, low_volatility_60, dividend_yield, gross_margin, debt_ratio, net_profit_growth`
  by composing `view.daily_basic` + `view.daily` (for momentum) +
  `view.fina_indicator` (for growth/margin/debt) + `view.dividend`.
- [ ] 3.2 Apply the overlay's `min_factor_coverage` filter to drop rows.
- [ ] 3.3 Call `factor_pipeline.process_factors(frame, overlay, ...)` with
  the exact same arg signature live `build_signals` uses. We do not
  re-implement winsorize / zscore / neutralize — we reuse them.
- [ ] 3.4 Apply broadcast factor shift using `view.broadcast(...)`.
- [ ] 3.5 Apply portfolio_controls (industry caps, holding buffer) — share
  the same module as live (`stock_analyze/portfolio_controls.py`).
- [ ] 3.6 Return `pd.DataFrame[signal_date, account_id, code, score, reason, selected]`.
- [ ] 3.7 Write `tests/test_backtest_scoring.py` that constructs a small
  hand-crafted PointInTimeView fixture and asserts the output schema
  matches live `build_signals`. ≥6 tests.

## 4. Replace engine._compute_signals

- [ ] 4.1 Update `_compute_signals` to branch on `use_full_pipeline`:
  - True → delegate to `score_with_overlay`.
  - False → keep current MVP body (zero-risk rollback path).
- [ ] 4.2 Verify the suite still passes with default OFF.
- [ ] 4.3 Add an integration test `tests/test_backtest_engine_full_pipeline.py`
  that runs `run_backtest(...)` twice on the same overlay (full-pipeline
  vs MVP) and asserts:
  - both produce identical column schemas in daily_nav.csv,
  - full-pipeline produces a non-trivial number of distinct scores
    (not all 0.5, not all ascending integers),
  - full-pipeline holdings count per account is within `[top_n * 0.8, top_n]`.

## 5. Gate structural-equivalence checks

- [ ] 5.1 Add `_check_structural_equivalence(result: BacktestResult)` to
  `gate.py` that picks 3 random as_of dates in the validation window
  and asserts:
  - `n_unique_scores >= 0.5 * universe_size` (catches degenerate scoring),
  - `n_holdings_per_account ∈ [top_n * 0.8, top_n]` (catches sizing collapse).
- [ ] 5.2 Raise `BacktestStructuralBreach` with a specific message tag
  matching the same `BacktestFloorBreach` style.
- [ ] 5.3 Add tests for each failure mode.

## 6. Report comparison panel

- [ ] 6.1 When `--compare-mvp` is passed to `backtest` CLI, run both
  engines and emit a "与 MVP PE-only 信号对比" markdown panel in
  `report.md` with the 4-row stats table.
- [ ] 6.2 Add the same panel to the dashboard fragment.
- [ ] 6.3 Unit-test the markdown rendering against a fixture.

## 7. Migration

- [ ] 7.1 Land everything above with `use_full_pipeline=False` default.
- [ ] 7.2 For one monthly cycle, the monthly briefing template surfaces
  **both** the current (MVP) gate verdict and a "what would full pipeline
  say" pre-verdict. Operator approves the flip.
- [ ] 7.3 Flip default to `True` in `competition.yaml`.
- [ ] 7.4 After two clean cycles, remove the MVP branch from
  `engine._compute_signals` and delete the feature flag.

## 8. Documentation

- [ ] 8.1 Update `docs/historical-backtest-flow.md` Gate-vs-Research
  section to reflect full-pipeline scoring.
- [ ] 8.2 Update `CLAUDE.md` §5b (monthly-strategy step) noting the gate
  is now structurally faithful.
- [ ] 8.3 Add a changelog row in `data/competition/CHANGELOG.md` (the
  one operators read) explaining the gate behaviour change.
