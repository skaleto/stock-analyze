# CN QDII ETF P2 Roadmap

## Objective

Expand the research surface beyond US/HK equity exposure without weakening the
current competition's fairness, data provenance, or trading realism. P2 is a
new capability track, not an in-place edit to the live `top_n`, account cash, or
competition baseline.

## Current Production Evidence

The 2026-07-12 ECS `fund_basic` catalog contains the following exchange-listed
domestic products relevant to the original P2 request:

| Exposure | Visible products | Production implication |
| --- | ---: | --- |
| Japan | 5 | Nikkei 225 and TOPIX can form a research scope |
| Germany DAX | 2 | Enough for peer liquidity selection, not broad Europe |
| France CAC40 | 1 | Single-product concentration must be explicit |
| Saudi Arabia | 2 | Enough for a small Saudi research scope |
| India | 1 QDII-LOF | Not enough for an ETF competition account |
| Overseas oil | 2 QDII ETFs plus several QDII-LOFs | Requires commodity-specific factors and LOF mechanics |
| Overseas bonds | 1 visible QDII-LOF | Research only; insufficient product breadth |

This evidence means global equity can move first. Bond exposure should not be
promoted to an active competition account yet.

## P2.0 Historical And Capacity Foundation

Build the missing QDII historical evaluation layer before adding accounts:

1. Reconstruct weekly signals from point-in-time catalog, prices, NAV, fund
   share, listing status, and fees.
2. Add realistic 100-share lots, commissions, slippage, premium gates, stale
   quote handling, and next-trading-day execution.
3. Run walk-forward sensitivity for `top_n = 4, 5, 6, 8, 10`.
4. Compare net return, Sharpe, maximum drawdown, turnover, cost bps, effective
   correlation clusters, and exposure concentration.
5. Keep training, validation, and live periods separate. Do not tune repeatedly
   against the validation window.

Deliverable: a capacity report that can justify whether the US sleeve remains
at five and whether the HK sleeve should move to six or eight in a future
season.

## P2.1 Fund Event And Announcement Pipeline

Add a source-dated `fund_events` store before adding more exotic products.

Required event types:

- suspension/resumption and termination/liquidation;
- purchase/redemption restriction and quota notices;
- premium-risk warnings;
- fund share merger, split, dividend, and manager/index changes;
- index methodology and constituent changes.

Every event stores `published_at`, `observed_at`, `effective_at`, source URL,
event type, affected code, raw-content hash, and parser version. Hard events
block new orders; warning events appear in the selection funnel and Dashboard.
No event may affect a historical signal before its observable timestamp.

## P2.2 Global Equity Research Sandbox

Extend the shared catalog taxonomy with non-active scopes:

- `japan_exposure`: Nikkei 225 and TOPIX;
- `europe_exposure`: DAX and CAC40, later STOXX products when available;
- `saudi_exposure`: FTSE Saudi Arabia;
- `other_global_exposure`: research-only products that do not yet meet breadth.

Reuse the existing QDII provider, risk gates, shared universe hash, selection
snapshot, and one-index-one-seat rule. Add FX-aware benchmark metadata and
constituent/country look-through.

Run these scopes in shadow mode for at least four weekly cycles. They do not
change live orders, NAV, or competition ranking. A later season may create a
separate global sleeve only after data coverage, liquidity, and backtest gates
pass. Current US/HK account cash is not silently redistributed.

## P2.3 Commodity And Bond Research

Do not reuse equity scoring unchanged.

Commodity factors:

- 20/60-day momentum and volatility;
- futures curve/roll-yield proxy where available;
- RMB/USD movement;
- NAV premium persistence and liquidity;
- equity-producer ETF versus physical/futures exposure classification.

Bond factors:

- duration and credit class;
- yield/rate momentum and spread proxy;
- FX exposure and hedging status;
- NAV premium, liquidity, and distribution yield.

QDII-LOFs require an explicit product type because premium behavior, liquidity,
and benchmark composition differ from ETFs. A bond account requires at least
three liquid, independently benchmarked products; the current catalog does not
meet that gate.

## P2.4 Structured Information And Sentiment

Sentiment returns only after event data is stable. The old broadcast scalar is
not restored because it shifts every candidate equally and cannot rank funds.

New factor shape:

- one score per underlying index/theme, not one score per whole market;
- source URLs and timestamps required;
- confidence and decay applied;
- stale or missing evidence becomes unavailable, never neutral by invention.

Strategy usage remains different:

- `稳健防守`: negative regime tightens premium/liquidity gates and tilts toward
  lower volatility; it does not chase positive headlines.
- `趋势进攻`: positive theme evidence may confirm price momentum, but cannot
  override liquidity, premium, or drawdown controls.

Initially the LLM only classifies sourced events and writes an auditable
research record. Activation as a trading factor requires a cross-sectional IC
study and a new strategy release.

## P2.5 Dashboard And Operator Flow

Add research-only tabs for global scopes and asset classes with:

- catalog and eligibility funnel;
- data coverage and source freshness;
- benchmark, FX, sector, and constituent exposure;
- event timeline and active hard blocks;
- shadow NAV versus benchmark;
- explicit `研究中` state until promotion gates pass.

Weekly Lark summaries mention material event blocks and shadow-data failures,
but do not add routine news lists. Monthly reminders link the accumulated P2
evidence into the Codex evolution task.

## Release Sequence

1. Historical QDII engine and `top_n` capacity study.
2. Fund event/announcement pipeline and hard risk gates.
3. Global equity scopes in research-only mode.
4. Four-week shadow run and data-quality review.
5. Commodity model and QDII-LOF mechanics.
6. Bond research only after product breadth improves.
7. Structured per-index sentiment last.
8. Start a new versioned competition season for any promoted account or changed
   baseline; preserve the current season unchanged.

## Promotion Gates

- Shared deterministic universe hash across both strategies.
- Three-year-or-since-listing history complete through the latest trading day.
- At least 95% measured coverage for required risk fields.
- No look-ahead in catalog, event, NAV, or benchmark data.
- Backtest and validation floors pass net of costs.
- Four weekly shadow runs complete without order, data, or notification drift.
- Dashboard and Lark clearly distinguish research data from active positions.
