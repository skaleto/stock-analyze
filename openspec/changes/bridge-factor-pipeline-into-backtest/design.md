# Design

## Goal

Make the backtest engine score overlays with **the same factor pipeline live trading uses**, so the gate's pass/fail verdict and the training window's stats actually reflect what the overlay would do in production.

## Non-goals

- We are NOT adding new factors. Live and backtest stay on the same factor whitelist (`AVAILABLE_FACTORS` in `overlay_guard.py`).
- We are NOT changing the simulator's clock parameterization, T+1 rules, lot sizing, or slippage. Only the **scoring** step changes.
- We are NOT adding LLM-based per-stock financial analysis. That's a separate proposal (see `add-llm-sentiment-alpha-factor` Phase 2+ for the long-term roadmap).

## Why now

This was flagged as a known gap in the original backtest proposal (`add-historical-backtest-engine`, line referencing `_compute_signals` MVP comment). Now that:

1. The backtest engine landed and the gate is wired into evolution_writer,
2. `factor_pipeline.process_factors` has been stable for several months,
3. PointInTimeView covers all the data the live pipeline needs,

…the bridge has small surface area and a clear unit-test story. The longer we leave it, the more LLM strategy decisions get made on a misleading signal.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                  Live forward pipeline                     │
│ build_signals(...)                                         │
│   └─ assemble factor frame from provider                   │
│   └─ factor_pipeline.process_factors(...)  ←─┐             │
│   └─ portfolio_controls                       │ shared     │
│   └─ store.write_signals                      │            │
└───────────────────────────────────────────────┼────────────┘
                                                │
┌───────────────────────────────────────────────┼────────────┐
│                  Backtest engine              │            │
│ run_backtest(...)                             │            │
│   └─ for each signal day:                     │            │
│       └─ _compute_signals(...)                │            │
│           └─ score_with_overlay (NEW)         │            │
│              └─ assemble frame from view ─────┘            │
│              └─ factor_pipeline.process_factors            │
│              └─ portfolio_controls                         │
└────────────────────────────────────────────────────────────┘
```

The single shared dependency is `factor_pipeline.process_factors`. Both code paths call it with the same arg shape. The diff is only in **how the factor frame is assembled** — live reads from a provider, backtest reads from a PointInTimeView.

## Why a separate `scoring.py` instead of inlining in `engine.py`

Three reasons:

1. **Testability**: `scoring.score_with_overlay()` can be tested without spinning up the whole simulator loop. A test passes a hand-crafted view + overlay and checks the output frame.
2. **Reuse**: the adapter is also useful for the report layer (the comparison panel needs to call it directly, not through the whole engine).
3. **Symmetry**: live trading has `strategy.build_signals` as its scoring entry point; the parallel `scoring.score_with_overlay` makes the symmetry obvious to a new reader.

## Why a feature flag instead of a hard cut-over

The audit ran on 2026-05-26. The next monthly evolution cycle is 2026-06-01 (~5 days). We don't want to land a behaviour change a week before the LLM tries to ship a new overlay through the gate. The flag lets us:

- Land the code without changing live behaviour (default False).
- Run both engines for one cycle, side by side, with the diff surfaced in the monthly briefing.
- Operator + LLM both see exactly what changes before we flip.
- Rollback is one config line if anything looks wrong.

## Why PointInTimeView.broadcast returns 0.0 for training window

The sentiment factor is a **forward-looking innovation** introduced after the training window (2021-2024). Historical sentiment doesn't exist for those dates. Two reasonable choices:

1. **Synthesize historical sentiment** from news archives → defeats the point of having LLM-driven sentiment, since it'd be backfilled by a different process.
2. **Return neutral 0.0** → the training-window backtest scores the overlay's other factors fairly, and sentiment contributes nothing.

We choose (2). The report panel will warn `"训练窗口不评估 sentiment 因子贡献（数据始于 2026-05）"` so a human reader understands.

For the validation window (2025-01-01 to 2026-04-30), sentiment also doesn't exist yet. Same treatment.

For the live window (2026-05-18+), sentiment is recorded weekly via the `record-sentiment` flow.

## Why structural-equivalence checks

The audit found a real risk: **a refactor that breaks factor coverage so badly that every stock gets the same score**. Today's gate would still pass that overlay (no NAV jump, no drawdown) because it executes a noop "buy top N" against a tied universe — and gets ordinary returns by accident.

The structural check is the cheap insurance:

```
For 3 random as_of dates:
  n_unique_scores < 0.5 * universe_size  →  scoring is degenerate
  holdings_per_account ∉ [top_n * 0.8, top_n]  →  sizing is broken
```

Neither check would have ever failed in normal operation; both fire instantly if the pipeline is silently misbehaving.

## Performance budget

Training window backtest, 1000 trading days, ~800 codes per day, ~11 active factors:

- Per signal day: 800 × 11 = 8800 cell operations for winsorize+zscore. Plus a single industry-neutralize regression. ~10ms.
- Per 200 signal days in the validation window (~Wednesday rebalance, ~52 weeks × 4 years): 200 × 10ms = 2s.
- Training (4 years × 52 weeks ≈ 208 signal days): same as above.

Total ≈ 4 seconds of scoring overhead per backtest run. The simulator loop dwarfs this. Acceptable.

## What we explicitly will NOT preserve

- The current MVP `_compute_signals`' deterministic tie-breaking (it sorted by PE ascending). If two stocks have identical full-pipeline scores, full-pipeline's tie-breaking is whatever `pd.DataFrame.sort_values` does — typically stable + insertion order. This is fine; live trading has the same behaviour.

## Out-of-scope sketches (for future PRs)

- **Cross-sectional LLM sentiment** would need a new column on the factor frame; would require an OpenSpec change extending `factor_pipeline.AVAILABLE_FACTORS`.
- **Walk-forward gate**: today the gate runs one validation-window backtest. A walk-forward variant (12 rolling 1-year backtests) gives a more honest distribution of outcomes. Mentioned here only to clarify scope; separate proposal.
