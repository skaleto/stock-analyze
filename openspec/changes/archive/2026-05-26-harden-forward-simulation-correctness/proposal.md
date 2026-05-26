# Proposal: Harden Forward Simulation Correctness

**Change ID:** `harden-forward-simulation-correctness`  
**Created:** 2026-05-18  
**Status:** Implementation Complete  
**Completed:** 2026-05-18

---

## Problem Statement

The current A-share forward simulation can generate weekly signals, pending orders, simulated trades, NAV rows, reports, and a dashboard. However, the model review found several correctness gaps that can materially distort simulation results:

- Order execution can fall back to reference prices when market data is missing.
- Pending orders can disappear after an attempted execution even when no trade happened.
- The execution date uses a weekday-only approximation instead of an A-share trading calendar.
- Simulated execution does not block obvious paused, limit-up buy, limit-down sell, or T+1 sell cases.
- NAV rows are appended repeatedly and can create duplicate account/date points.
- `max_single_weight` is documented in config but not enforced when target orders are created.

These issues affect the user, who is using the system as a beginner-friendly A-share simulation and dashboard. The highest priority is to make the paper-trading ledger conservative and auditable before adding richer factors or live-trading integrations.

## Proposed Solution

Implement a P0 correctness pass for the forward simulation:

- Use an A-share trading calendar when available, with the existing business-day fallback only as degradation.
- Only execute orders using market data visible on or before the current run date.
- Keep unfilled or partially filled orders in `pending_orders.json` with status, attempt count, and `unfilled_reason`.
- Block simulated fills for paused securities, limit-up buys, limit-down sells, no quote, no sellable shares, and insufficient cash.
- Track approximate T+1 sellability through `available_shares` and `last_buy_date` in position state.
- Enforce `max_single_weight` during target-order sizing.
- Upsert NAV rows by `date + account_id`.
- Document the new behavior in the runbook and model review.

## Scope

### In Scope

- `stock_analyze/data_provider.py`: trading calendar, execution quote, limit detection helpers.
- `stock_analyze/simulator.py`: conservative execution, pending order retention, T+1 sellability, max single weight.
- `stock_analyze/store.py`: NAV upsert and position columns.
- `stock_analyze/reporting.py`: pending order status/reason visibility.
- `docs/forward-simulation-runbook.md` and model review documentation.
- Unit tests covering the new correctness contracts.

### Out of Scope

- Full historical point-in-time fundamentals.
- Historical index constituent database.
- Multi-year backtest engine.
- Factor IC/RankIC research tables.
- SQLite/DuckDB run ledger.
- Real brokerage execution.

## Impact Analysis

| Component | Change Required | Details |
|-----------|-----------------|---------|
| Database | No | Runtime files remain CSV/JSON for this change. |
| API | Yes | CLI behavior stays compatible, but execution becomes conservative by default. |
| State | Yes | Pending orders gain status fields; positions gain T+1 sellability fields; NAV becomes date/account upsert. |
| UI | Yes | Dashboard pending-order table shows status and unfilled reason. |

## Architecture Considerations

This change keeps the current lightweight file-based architecture. It introduces conservative domain rules inside the existing provider/simulator boundary:

- `AkshareProvider` remains responsible for market-data-derived tradeability.
- `simulator.py` remains responsible for order lifecycle and account state.
- `PortfolioStore` remains responsible for persistence mechanics.

The implementation should prefer explicit fallback labels and retained pending orders over optimistic forced fills.

## Success Criteria

- [x] Weekly rebalance chooses an A-share trading day for `execute_after` when calendar data is available.
- [x] A missing execution quote leaves the order pending with `unfilled_reason`.
- [x] Paused securities, limit-up buys, and limit-down sells do not simulate a fill.
- [x] Same-day purchases are not sellable under the T+1 approximation.
- [x] Re-running NAV update for the same date/account does not duplicate NAV rows.
- [x] Target order sizing respects `trading.max_single_weight`.
- [x] Dashboard exposes pending order status and reason.
- [x] Unit tests and Python syntax checks pass.

## Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Public trading-calendar API fails | Medium | Medium | Cache calendar data and fall back to weekday behavior with a data-health warning. |
| Limit detection is approximate under adjusted prices | Medium | Medium | Use conservative open-price checks and record reasons; treat this as P0 approximation, not final microstructure simulation. |
| Existing local positions lack T+1 fields | High | Low | Treat legacy positions as sellable unless `last_buy_date` proves they were bought today. |
| Pending orders may accumulate | Medium | Medium | Add status/reason visibility; later change can add expiry rules. |
