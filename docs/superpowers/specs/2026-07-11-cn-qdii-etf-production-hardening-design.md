# CN QDII ETF Production Hardening Design

## Goal

Turn the existing `cn_qdii_etf` MVP into a reproducible ECS paper-trading loop
whose scheduled orders execute safely, persist consistently, and remain
truthful in the React dashboard.

This remains paper trading. It never connects to a broker and never places a
real order.

## Scope

The hardening work covers five linked surfaces:

1. QDII order lifecycle and persisted portfolio state.
2. ETF strategy filters and portfolio controls that are already present in the
   overlay schema.
3. QDII market-data health, benchmark marking, and factor correctness.
4. ECS scheduling, deployment versioning, and post-deploy trial runs.
5. Dashboard data integrity, request ordering, benchmark presentation,
   responsive density, and keyboard interaction.

The work does not redesign the dark visual language, add real brokerage
integration, or change competition baseline fields.

## Runtime Invariants

### Orders

- An order is due when `trade_date <= as_of`.
- Missing or paused execution data must retain the order with an auditable
  `unfilled_reason`; it must never silently disappear.
- A delayed daily run executes against the first quote visible on the actual
  retry date. It must not backfill a prior close as a current-day fill.
- A successful fill is removed from pending orders exactly once.
- Re-running the same day is idempotent because the filled order no longer
  exists in the pending file.

### Persistence

After every successful execution, these views must agree:

- `state.json` contains the position and updated cash.
- `trades.csv` contains the fill and costs.
- `positions.csv` contains the current position.
- `daily_nav.csv` marks the position at the latest visible close.

`update_nav` refreshes position mark-to-market fields and rewrites
`positions.csv`, so the dashboard cannot remain stale after NAV succeeds.

### Strategy Configuration

Before factor processing, the ETF strategy applies:

- `paused == false`;
- `min_listing_days` when listing metadata is available;
- `min_avg_amount_20`;
- `max_fetch_candidates`, ranked by liquidity.

The rebalance stage applies `hold_buffer_pct` and `max_holding_days`. Existing
holdings inside the buffer remain eligible; holdings past the maximum holding
period are re-evaluated against the strict top-N set. The existing baseline
`max_single_weight` remains locked.

### Market Data

- Raw exchange prices remain the execution source.
- Momentum and volatility use adjusted ETF closes when `fund_adj` is
  available, with a documented raw-close fallback.
- NAV-based discount/premium is omitted when NAV is missing or too stale.
- Provider calls record method, code, status, source date, and warning. The
  provider persists a health snapshot next to the QDII shared cache.
- Each account benchmark is marked independently in `daily_nav.csv`.

## ECS Scheduling

QDII is ECS-owned because it uses the same mainland Tushare access as A-share.
Codex gets two dedicated units:

- Daily timer, Mon-Fri after the mainland fund data window, runs
  `--market cn_qdii_etf --agent codex run-daily`.
- Weekly timer, Saturday after Friday data is available, runs
  `--market cn_qdii_etf --agent codex run-weekly`.

The units load `/etc/stock-analyze/secrets.env`, write dedicated logs, trigger
the existing pipeline-failure notifier on failure, and refresh the aggregate
dashboard on success. The deployment installs and enables only the Codex QDII
units in this change; it does not run or inspect Claude private state.

The timer-health check must inspect child service `Result` and recent failed
events. Parent trigger success is not sufficient evidence.

## Reproducible Deployment

The feature branch becomes the only source snapshot for this release. The
previous A-share operational fixes currently present only in the main working
tree are incorporated into this branch before deployment.

The frontend build remains generated, but deployment must run the checked-in
build script before syncing `reports/app`. A `DEPLOY_VERSION` file records the
exact local commit SHA on ECS. `RunLedger.code_version()` prefers this marker
over the stale remote `.git/HEAD` value.

Source deployment uses path-preserving rsync for explicitly tracked source,
tests, scripts, frontend source/build output, and systemd units. Runtime data is
not deleted or reset.

## Dashboard Contract

- Missing files are valid empty state; malformed or unreadable files are API
  errors and must not render as a normal empty portfolio.
- Invalid market or agent query values return a client error without exposing
  filesystem paths.
- Selection changes clear stale detail immediately and abort older requests.
- Auto-refresh responses cannot overwrite data for a newer selection.
- A multi-account portfolio exposes `benchmark_codes`; it never labels the
  combined NAV with an arbitrary first-account benchmark.
- Table rows support mouse, Enter, and Space activation.
- The detail drawer has dialog semantics, Escape close, and focus restoration.
- At narrow widths, controls become a compact toolbar so the first viewport
  still contains portfolio data. The existing dark terminal palette remains.

## Test Strategy

The implementation follows red-green-refactor discipline:

1. Settlement tests reproduce late orders, missing quotes, backdated quotes,
   and missing CSV persistence.
2. Strategy tests prove filters and hold controls change the selected set.
3. Provider tests cover adjusted prices, stale NAV, health output, and account
   benchmark quotes.
4. Dashboard API tests distinguish empty from corrupt data and cover multiple
   benchmarks plus invalid query values.
5. Frontend tests cover request races, errors, keyboard activation, drawer
   behavior, and compact responsive structure.
6. Shell tests cover QDII systemd commands, timer installation, frontend build,
   deployment versioning, and failed-child detection.
7. Local full suites run before deployment. ECS runs targeted tests, then a
   controlled Codex QDII weekly/daily trial validates generated files and the
   live dashboard API.

## Online Trial Acceptance

The release is accepted only when all of the following are true on ECS:

- QDII Codex daily and weekly timers are enabled and have future trigger times.
- A controlled weekly run produces pending orders.
- A controlled daily run on an eligible execution date reduces pending orders
  and writes trades, positions, NAV, and a successful run-ledger row.
- No fill has a quote date before the actual execution date.
- Dashboard detail returns the same order, position, trade, NAV, and run counts
  as the persisted files.
- `/app.html`, summary API, and Codex QDII detail API return HTTP 200.
- The browser shows the dark dashboard, supports search/sort/down-drill, and
  has no console errors at desktop or mobile width.

