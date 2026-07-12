# Current-Season Daily Decision Design

## Decision

Keep `dual_strategy_2026_s1`, its `2026-07-11` effective date, account cash,
positions, pending-order history, and NAV anchor. Starting with the next trading
day, remove the calendar requirement that portfolio targets are generated only
after the weekly review.

## Runtime Contract

- `run-daily` executes orders due for the current trading day, updates NAV, then
  evaluates the latest close and writes one fresh target decision for the next
  trading day.
- Before a fresh target is persisted, unresolved older targets are superseded.
  If signal generation fails, the prior pending file is restored.
- `run-weekly` updates diagnostics, report, dashboard, and the A-share briefing;
  it never calls `generate_rebalance_orders`.
- Daily evaluation does not imply daily trading. Existing factor ranking,
  hold-buffer, industry concentration, maximum holding period, lot size,
  commissions, tax, slippage, and settlement rules continue to determine
  whether an order is emitted or filled.
- Both strategy slots use the same decision and execution clock. Their existing
  overlays continue to provide different turnover characteristics.

## Scheduling And Fairness

- A-share and mainland cross-border ETF baselines declare daily after-close
  decisions and next-trading-day execution.
- The existing weekday market-data and daily-agent timers remain the execution
  surface. Weekly timers remain enabled only for reporting and review.
- This is a rule change inside S1, effective `2026-07-13`; it does not reset the
  comparison. The rule-change date is recorded in the strategy registry and
  operator documentation.

## Safety

- Active overlays are not edited.
- Daily target replacement is transactional at the pending-file level.
- Same-day reruns replace the same target rather than accumulating duplicate
  batches.
- Deployment continues to use `SA_SKIP_AGENT_CONFIG_SYNC=1`; the shared
  competition baselines and source are deployed, while agent overlays remain
  untouched.

## Acceptance

- Tests prove call order: execute, NAV, clear stale targets, generate new target.
- Tests prove restoration of old targets when generation fails.
- Tests prove `run-weekly` does not generate orders.
- ECS deployment gate passes and timers remain healthy.
- A controlled online daily run for the latest cached trading date completes
  for all four market/strategy accounts without resetting state.
