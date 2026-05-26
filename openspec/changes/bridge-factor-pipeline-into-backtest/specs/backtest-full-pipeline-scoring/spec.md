## ADDED Requirements

### Requirement: Backtest engine scores overlays via the full live factor pipeline

The backtest engine SHALL replay `stock_analyze.factor_pipeline.process_factors` against a `PointInTimeView` so that the scoring step in `stock_analyze.backtest.engine._compute_signals` produces the same factor → score mapping that the live forward `stock_analyze.strategy.build_signals` produces for the same overlay.

The bridge SHALL:

- Live in a dedicated `stock_analyze.backtest.scoring.score_with_overlay(view, overlay, as_of, universe) -> pd.DataFrame` adapter so it is unit-testable without instantiating the simulator loop.
- Assemble the per-stock factor frame from the view (`daily_basic`, `daily`, `fina_indicator`, `dividend`, `industry`) using the same columns the live pipeline consumes.
- Delegate winsorize / zscore / industry-neutralize / weight combination to `factor_pipeline.process_factors` directly. The backtest adapter SHALL NOT re-implement these steps.
- Apply portfolio_controls (industry caps, holding buffer, max_holding_days, max_single_weight) through the existing `stock_analyze.portfolio_controls` module so live and backtest share one constraint implementation.
- Return a DataFrame with the same columns the live pipeline writes to `latest_signals.csv`: `signal_date, account_id, code, score, reason, selected`.

#### Scenario: Same overlay produces identical scoring at a point-in-time as live

- **GIVEN** an overlay with `factors: {pe: 0.4, momentum_20: 0.3, roe: 0.3}` and `neutralize_industry: true`
- **AND** a `PointInTimeView` seeded with daily_basic, daily, fina_indicator, and industry data for `as_of=date(2024, 6, 28)`
- **WHEN** `score_with_overlay(view, overlay, date(2024, 6, 28), ['hs300'])` is called
- **AND** the same overlay is run through live `build_signals` against a provider seeded with identical data at the same point-in-time
- **THEN** the two output DataFrames have the same set of `(account_id, code)` pairs with `selected=True`
- **AND** the `score` column matches within `1e-6` tolerance for every row

#### Scenario: Backtest engine delegates scoring to score_with_overlay when feature flag enabled

- **GIVEN** an overlay containing `backtest: {use_full_pipeline: True}`
- **WHEN** `run_backtest(overlay, ...)` is called
- **THEN** every signal-day invocation of `_compute_signals` SHALL call `score_with_overlay` and return its rows verbatim
- **AND** the MVP PE-only branch in `_compute_signals` SHALL NOT execute

#### Scenario: MVP PE-only branch is preserved behind the feature flag during migration

- **GIVEN** an overlay without `backtest.use_full_pipeline`, or `backtest.use_full_pipeline: False`
- **WHEN** `run_backtest(overlay, ...)` is called
- **THEN** the engine SHALL fall back to the current MVP scoring (low-PE ascending top-N)
- **AND** behaviour matches the engine output prior to this change (byte-equivalent NAV / signals / trades)

### Requirement: PointInTimeView exposes broadcast factor accessor

`PointInTimeView` SHALL provide `broadcast(factor_name: str, as_of: date) -> float` that returns the most-recent broadcast factor value with `week_end <= as_of`. The accessor SHALL respect point-in-time visibility:

- Returns `0.0` (neutral) when no row with `week_end <= as_of` exists in the broadcast factor store.
- Returns `0.0` when the broadcast factor CSV does not exist at all (training-window backtests against pre-sentiment data).
- Never returns a row with `week_end > as_of` even if that row is closer in time.

#### Scenario: broadcast returns 0.0 when the broadcast factor CSV is missing

- **GIVEN** a `PointInTimeView` for a training-window run (no `data/<agent>/alt_factors/` directory)
- **WHEN** `view.broadcast("claude_market_sentiment_1w", date(2023, 6, 1))` is called
- **THEN** the return value is exactly `0.0`

#### Scenario: broadcast returns the latest non-future row

- **GIVEN** a `PointInTimeView` with a sentiment CSV containing rows at `week_end in {2026-05-08, 2026-05-15, 2026-05-22}`
- **WHEN** `view.broadcast("claude_market_sentiment_1w", date(2026, 5, 20))` is called
- **THEN** the return value is the score from the `2026-05-15` row
- **AND** the `2026-05-22` row (future) is never read

### Requirement: Gate runs structural-equivalence checks against backtest output

`stock_analyze.backtest.gate.validate_overlay_via_backtest` SHALL, after running the backtest, sample 3 random signal-day dates from the validation window and assert structural invariants. The gate SHALL raise `BacktestStructuralBreach` (a new subclass of the existing gate error hierarchy) if any of the following are violated on any sampled date:

- `n_unique_scores >= 0.5 * len(universe_at_date)` — catches degenerate scoring where every stock ties.
- `top_n * 0.8 <= holdings_per_account <= top_n` — catches sizing collapse from the Tier 1+2 fallback path.

The raised exception SHALL carry the failing date and the measured metric so the LLM evolution log can reference it.

#### Scenario: Gate raises BacktestStructuralBreach when scoring is degenerate

- **GIVEN** an overlay whose factor pipeline (under `use_full_pipeline: True`) produces identical scores for every stock on every signal day
- **WHEN** the gate validates the overlay
- **THEN** `BacktestStructuralBreach` is raised
- **AND** the exception's `detail` mentions `n_unique_scores` and the sampled signal date

#### Scenario: Gate passes when holdings stay within sizing band

- **GIVEN** an overlay producing healthy, varied scores
- **AND** a backtest result where every signal day yields between `top_n * 0.85` and `top_n` holdings per account
- **WHEN** the gate validates the overlay
- **THEN** no `BacktestStructuralBreach` is raised
- **AND** the gate's existing floor checks (max drawdown, sharpe, cumulative return) remain authoritative for the pass/fail verdict

### Requirement: Backtest report renders a full-pipeline-vs-MVP comparison panel

When `backtest` CLI is invoked with `--compare-mvp`, `stock_analyze.backtest.report` SHALL emit a markdown panel titled "## 与 MVP PE-only 信号对比" comparing the four core metrics (`cum_return`, `max_drawdown`, `sharpe`, `mean_ic`) between the same overlay scored under full pipeline vs MVP PE-only.

The same panel SHALL also be available as an HTML fragment via `report.render_compare_panel(...)` for dashboard inclusion.

#### Scenario: Markdown comparison panel includes all four metrics

- **GIVEN** a backtest result for `use_full_pipeline=True` and `use_full_pipeline=False` against the same overlay and window
- **WHEN** `report.render_compare_panel_markdown(result_full, result_mvp)` is called
- **THEN** the returned string contains a markdown table with four rows: cum_return, max_drawdown, sharpe, mean_ic
- **AND** each row shows both the full-pipeline value and the MVP value
