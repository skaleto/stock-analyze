## Why

The backtest engine landed in `add-historical-backtest-engine` is **calibrated by a different scoring model than live trading**. That undermines the whole point of using the backtest as the LLM gatekeeper for monthly strategy evolution.

Concrete gap (see `stock_analyze/backtest/engine.py:240-280`, function `_compute_signals`):

```python
# MVP simplification: rank candidates by ascending PE_TTM (low PE first),
# take top N from each account's universe. Real forward simulator uses the
# full factor_pipeline; bridging that is future work.
```

The live forward pipeline (`stock_analyze/strategy.py:build_signals` →
`stock_analyze/factor_pipeline.py:process_factors`) does:

1. Load all factor columns active in the overlay (`pe`, `pb`, `roe`,
   `momentum_20`, `momentum_60`, `low_volatility_60`, `dividend_yield`,
   `gross_margin`, `debt_ratio`, `net_profit_growth`, `claude_market_sentiment_1w`,
   …) per the overlay's `factors` block.
2. Winsorize each factor at the overlay's `winsorize_lower` /
   `winsorize_upper` percentiles.
3. Z-score within the day.
4. Optionally industry-neutralize (`neutralize_industry: true`).
5. Combine via the overlay's per-factor weights (broadcast factors enter
   via `_broadcast_shift`).
6. Drop rows below `min_factor_coverage`.
7. Apply industry/holding-buffer/max-single-weight portfolio controls.

The backtest engine bypasses every step except a naive PE-ascending sort, so:

| Failure mode | Mechanism |
|---|---|
| Gate accepts bad overlays | Overlay's actual factor mix is never evaluated; gate sees same PE-rank for everyone. |
| Training-window NAV doesn't match what the same overlay would have done live | Different signals → different positions → different P/L. |
| LLM monthly evolution learns false signal | "Backtest cum return = +12%" is meaningless if the backtest used PE-only logic, not the overlay being judged. |
| Forward-IC vs backtest-IC drift | Forward IC measures the overlay's real factors; backtest IC currently measures PE only. |

Once the gate is mature, this is the **single highest-leverage** quality lever in the whole research loop — it converts the backtest from "directionally indicative" to "trustworthy calibration".

## What Changes

### 1. Add `backtest.scoring.score_with_overlay()` adapter

```python
# stock_analyze/backtest/scoring.py  (NEW)
def score_with_overlay(
    view: PointInTimeView,
    overlay: dict,
    as_of: date,
    universe: list[str],
) -> pd.DataFrame:
    """Replay factor_pipeline.process_factors against a PointInTimeView.

    Returns a DataFrame with columns [account_id, code, score, reason,
    selected, factor_*] mirroring what live build_signals would produce.
    Caller (engine._compute_signals) then takes top_n per account.
    """
```

The adapter:
- Calls `view.daily_basic`, `view.daily`, `view.fina_indicator`,
  `view.dividend`, `view.industry`, `view.broadcast(factor_name, as_of)`
  to assemble the same factor frame `process_factors` expects.
- Delegates to the existing `factor_pipeline.process_factors` so logic stays
  in one place — we **do not** re-implement winsorize / zscore / neutralise.
- Returns the same `signal_date`/`account_id`/`code`/`score`/`reason` shape
  that `_compute_signals` returns today, so callers don't change.

### 2. Replace `engine._compute_signals` body

```python
def _compute_signals(view, overlay, as_of, universe):
    from .scoring import score_with_overlay
    return score_with_overlay(view, overlay, as_of, universe).to_dict("records")
```

Behind a feature flag `overlay.get("backtest", {}).get("use_full_pipeline", True)` so:
- Default: full pipeline (the new behaviour).
- Opt-out: keep MVP PE-only for fast spike testing.

### 3. Add `view.broadcast(factor_name, as_of) -> float` to PointInTimeView

Today's `PointInTimeView` only knows per-stock factor columns. Broadcast factors
(`<agent>_market_sentiment_1w`) need a separate accessor that returns the
single broadcast value at `as_of`, with the same point-in-time rules as the
per-stock factors. This mirrors `factor_pipeline._broadcast_shift` exactly.

For the training window where historical sentiment doesn't exist yet, the
accessor returns `0.0` (neutral). This is documented as **"backtest gate
checks factor structure, not sentiment-conditioned alpha"** in the report.

### 4. Update gate floor with structural-equivalence verification

After scoring, the gate runs a sanity check:

```
For 3 random as_of dates in the validation window:
    n_unique_scores >= 0.5 * universe_size  → fail if not (degenerate scoring)
    n_holdings_per_account ∈ [top_n * 0.8, top_n]  → fail if not
```

This catches "pipeline silently produces all-zero scores so everyone ties"
which is a real failure mode when a factor source is missing.

### 5. Report comparison panel

`report.py` adds a new panel:

```
## 与 MVP PE-only 信号对比

| 指标 | 当前 overlay (full pipeline) | MVP PE-only |
|---|---|---|
| 累计收益 | +12.3% | +8.1% |
| 最大回撤 | -7.2% | -11.4% |
| Sharpe | 1.4 | 0.8 |
| 信息系数 (IC) 均值 | 0.04 | 0.02 |
```

So a human reading the report can see whether the overlay's factor mix
is doing useful work or is no better than naive low-PE.

## Impact

### Affected specs

- **backtest**: capability gains "full-pipeline scoring" requirement; gate gains "structural-equivalence" requirement.
- **factor-pipeline**: no spec change (we reuse it as-is), but its module-level invariant gets a new caller.

### Affected code

| File | Change |
|---|---|
| `stock_analyze/backtest/scoring.py` | NEW (~120 LoC) |
| `stock_analyze/backtest/engine.py` | `_compute_signals` body replaced (~10 LoC delta) |
| `stock_analyze/backtest/data_view.py` | `+ broadcast()` accessor (~30 LoC) |
| `stock_analyze/backtest/gate.py` | `+ _check_structural_equivalence()` (~40 LoC) |
| `stock_analyze/backtest/report.py` | `+ MVP comparison panel` (~50 LoC) |
| `stock_analyze/factor_pipeline.py` | unchanged (we call `process_factors` from the adapter) |
| `tests/test_backtest_scoring.py` | NEW (~200 LoC: replays a known overlay through both engines and asserts identical signals at 3 fixed as_of dates) |
| `tests/test_backtest_gate_structural.py` | NEW (~80 LoC) |
| `docs/historical-backtest-flow.md` | update Gate-vs-Research scenarios |

### Risk

- Behaviour change in the gate: overlays that *currently pass* under PE-only logic might *fail* under full-pipeline (because the overlay's actual factor mix produces worse training-window stats than naive low-PE). This is by design — the gate is now telling us something true. We migrate by running both old and new gate side-by-side for one monthly cycle and surfacing the diff in the monthly briefing before flipping the floor.
- Performance: full pipeline is heavier (winsorize + zscore + neutralize per signal day). Estimate: training window of 1000 trade days × ~800 codes × 11 factors ≈ 9M cell ops. Acceptable on Apple Silicon (~30s end-to-end). On ECS 2-core ~2 min. Both within the current `--quick` budget.

### Migration

1. Land the adapter + feature flag (default OFF for one cycle).
2. Monthly briefing surfaces both old-gate verdict and new-gate verdict + the report comparison panel.
3. After one cycle of side-by-side observation, flip default to ON.
4. After two cycles with no production incident, remove the MVP PE-only branch.

### Out of scope

- Forward-only factors (factors that only exist in the live pipeline because the data isn't yet available in the backtest cache). Those keep returning neutral (`0.0`) in the backtest as today.
- Cross-sectional sentiment ranking (the `claude_market_sentiment_1w` factor is broadcast-only by design; per-stock sentiment is a separate change tracked in `add-llm-sentiment-alpha-factor` Phase 2+).
- Tushare cost: `process_factors` uses per-stock fina_indicator — but `data_view.py` already caches those during `prepare-backtest-data`, so we re-read from disk, no new HTTP load.
