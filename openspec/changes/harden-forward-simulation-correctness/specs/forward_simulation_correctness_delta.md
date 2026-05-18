# Delta: Forward Simulation Correctness

**Change ID:** `harden-forward-simulation-correctness`  
**Affects:** signal scheduling, simulated execution, pending order lifecycle, NAV persistence, dashboard visibility

---

## ADDED

### Requirement: Trading Calendar Scheduling

Weekly rebalance shall schedule execution on the next known A-share trading day when calendar data is available.

#### Scenario: Calendar Available
- GIVEN a weekly rebalance is generated for a signal date
- WHEN the provider can load an A-share trading calendar
- THEN `execute_after` is the next trading date after the signal date

#### Scenario: Calendar Unavailable
- GIVEN a weekly rebalance is generated for a signal date
- WHEN the trading calendar cannot be loaded
- THEN the system falls back to weekday-only `next_business_day`
- AND records the degradation in provider health

### Requirement: Conservative Execution Quote

Simulated execution shall only use market data visible on or before the current run date.

#### Scenario: Quote Missing By Run Date
- GIVEN a pending order is due
- WHEN no execution quote exists between `execute_after` and the current run date
- THEN no trade is generated
- AND the order remains pending with `unfilled_reason`

#### Scenario: Future Data Exists After Run Date
- GIVEN future market data exists after the current run date
- WHEN an order is executed for the current run date
- THEN the future row is ignored
- AND the order remains pending if no visible quote exists

### Requirement: A-Share Tradeability Blocks

Simulated execution shall conservatively block known non-tradeable cases.

#### Scenario: Buy At Limit Up
- GIVEN a buy order is due
- WHEN the execution quote is at or above the estimated limit-up price
- THEN no trade is generated
- AND the order remains pending with reason `limit_up_buy_blocked`

#### Scenario: Sell At Limit Down
- GIVEN a sell order is due
- WHEN the execution quote is at or below the estimated limit-down price
- THEN no trade is generated
- AND the order remains pending with reason `limit_down_sell_blocked`

#### Scenario: Paused Security
- GIVEN an order is due
- WHEN the execution quote is marked paused
- THEN no trade is generated
- AND the order remains pending with reason `paused`

### Requirement: T+1 Sellability Approximation

The simulator shall prevent shares bought on the current run date from being sold on that same date.

#### Scenario: Same-Day Sell Attempt
- GIVEN a position has shares bought on the current run date
- WHEN a sell order attempts to sell those shares on the same run date
- THEN the simulator only sells shares that were already available
- AND retains the residual order if the target was not reached

### Requirement: Pending Order Lifecycle

Pending orders shall remain auditable across failed and partial execution attempts.

#### Scenario: Failed Execution Attempt
- GIVEN an order is due
- WHEN the simulator cannot fill it
- THEN the order remains in `pending_orders.json`
- AND includes `status`, `attempts`, `last_attempt_at`, and `unfilled_reason`

#### Scenario: Partial Execution Attempt
- GIVEN an order is due
- WHEN only part of the requested quantity can be filled
- THEN a trade is generated for the filled quantity
- AND the residual order remains pending with updated `current_shares` and `delta_shares`

### Requirement: NAV Upsert

Daily NAV persistence shall have one row per `date + account_id`.

#### Scenario: Same Day Re-run
- GIVEN NAV already exists for an account/date
- WHEN the NAV update runs again for the same account/date
- THEN the previous row is replaced by the latest row
- AND performance calculations read one point for that account/date

---

## MODIFIED

### Requirement: Target Position Sizing

Target order sizing shall respect `trading.max_single_weight` when the field is configured.

#### Scenario: Max Single Weight Lower Than Equal Weight
- GIVEN `max_single_weight` is below `1 / top_n`
- WHEN target orders are generated
- THEN each selected stock target value is capped by `account_total_value * max_single_weight`

### Requirement: Dashboard Pending Orders

Dashboard pending-order views shall show order lifecycle status and unfilled reason.

#### Scenario: Unfilled Pending Orders Exist
- GIVEN pending orders include failed or partial attempts
- WHEN the dashboard is generated
- THEN the pending-order table shows status and unfilled reason

---

## REMOVED

- Optimistic reference-price fallback for normal simulated execution.
