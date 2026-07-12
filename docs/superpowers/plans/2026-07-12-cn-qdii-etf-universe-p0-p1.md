# CN QDII ETF Universe P0/P1 Implementation Plan

**Goal:** Expand the mainland-listed US/HK exposure universe from a static seed list into a versioned, auditable catalog, then add ETF-specific risk gates, selection diagnostics, and underlying-index look-through to the dynamic dashboard.

**Constraints:** Keep the competition baseline and both strategy overlays unchanged. Both agents must consume the same market snapshot and universe hash. Tushare `etf_basic`, `etf_share_size`, and `fund_portfolio` are not available to the current production token, so the runtime must use available `fund_basic`, `fund_daily`, `fund_nav`, and `fund_share` data with explicit degraded-source status.

## Task 1: Dynamic shared universe and amount normalization

**Files:**
- Modify: `stock_analyze/markets/cn_qdii_etf/universe.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Modify: `stock_analyze/dashboard_finance.py`
- Test: `tests/test_markets_cn_qdii_etf_provider.py`
- Test: `tests/test_dashboard_finance.py`

1. Add failing tests for Tushare thousand-yuan amount conversion, catalog classification, index-level deduplication, and stable snapshot hashing.
2. Build deterministic US/HK index classification from `fund_basic.name + benchmark` with the existing static list as a degraded fallback.
3. Preselect a bounded number of funds per index, fetch three-year price history, then retain at most two liquid ETFs per underlying index.
4. Persist `data/cn_qdii_etf/shared/universe_snapshots/<as_of>.json` and a latest pointer with a content hash shared by both agents.
5. Enrich dashboard metadata from the shared snapshot instead of relying only on the static seed table.

## Task 2: ETF risk gates, diversity, and selection funnel

**Files:**
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/run.py`
- Test: `tests/test_markets_cn_qdii_etf_strategy.py`
- Test: `tests/test_markets_cn_qdii_etf_simulator.py`

1. Add failing tests for legacy liquidity threshold migration, stale/paused prices, listing age, abnormal premium, small fund size, peer tracking error, and reason counts.
2. Calculate fund size from `fund_share` (ten-thousand shares) and fresh NAV when available; do not fabricate unavailable values.
3. Add a default abnormal-premium gate and optional fund-size/tracking-error gates. Missing optional data remains visible as unknown rather than silently passing as measured.
4. Reorder scored signals so the target portfolio uses at most one ETF per underlying index before any relaxed fill.
5. Persist `data/cn_qdii_etf/<agent>/selection_snapshot.json` with every funnel stage, rejection reason, selected target, and shared universe hash.

## Task 3: Underlying look-through and strategy comparison

**Files:**
- Add: `stock_analyze/markets/cn_qdii_etf/lookthrough.py`
- Add: `stock_analyze/markets/cn_qdii_etf/index_profiles.json`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/strategy_comparison.py`
- Test: `tests/test_qdii_lookthrough.py`
- Test: `tests/test_strategy_comparison.py`
- Test: `tests/test_dashboard_app_api.py`

1. Add failing tests for source-dated index profiles, weighted portfolio aggregation, partial-coverage reporting, index overlap, and company overlap.
2. Store only official, source-dated constituent or sector observations. Profiles without published weights remain name-only and are excluded from weighted concentration calculations.
3. Aggregate actual/planned ETF allocations into country, underlying index, sector, and company exposures with an explicit measured coverage ratio.
4. Add index and constituent overlap to the two-strategy comparison payload.
5. Add instrument-level underlying profile data beside each ETF candlestick view.

## Task 4: Dynamic dashboard presentation

**Files:**
- Add: `frontend/dashboard/src/EtfResearchPanel.tsx`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/CompetitionPanel.tsx`
- Modify: `frontend/dashboard/src/InstrumentDrawer.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/styles.css`
- Test: `frontend/dashboard/src/App.test.tsx`
- Test: `frontend/dashboard/src/CompetitionPanel.test.tsx`
- Test: `frontend/dashboard/src/InstrumentDrawer.test.tsx`

1. Add failing component tests for the selection funnel, universe hash, real exposure coverage, underlying overlap, and ETF constituent list.
2. Add a compact professional research band between performance and holdings; retain the existing dark terminal visual system.
3. Show top exposures only, with source date and incomplete-coverage warnings.
4. Keep target orders at the bottom and preserve all existing interactions.

## Task 5: Verification and production rollout

1. Run focused Python and frontend tests, then the full Python and frontend suites.
2. Build the dashboard bundle and inspect desktop/mobile screenshots through the in-app browser.
3. Commit and push the branch.
4. Deploy source, tests, index profiles, and frontend bundle to ECS using the repository deploy path.
5. Run a real online QDII weekly cycle for both strategies, verify identical universe hashes, candidate counts, funnel output, orders, dashboard API, and three-year cache coverage for every selected ETF.
6. Return the live dashboard URL and measured before/after universe and coverage results.
