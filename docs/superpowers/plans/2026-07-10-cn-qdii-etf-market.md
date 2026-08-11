# CN QDII ETF Market Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `cn_qdii_etf` paper-trading market backed by Tushare fund APIs for domestic cross-border ETF/QDII ETF simulation.

**Architecture:** Add a market package that mirrors the HK/US config-first public API, but uses Tushare `fund_*` endpoints instead of yfinance. Reuse the shared settlement simulator with domestic ETF mechanics and add config/overlay files for codex and claude.

**Tech Stack:** Python 3, pandas, unittest, existing `stock_analyze` CLI/competition/PortfolioStore/factor_pipeline modules, Tushare Pro on ECS.

---

## File Structure

- Create `stock_analyze/markets/cn_qdii_etf/`: provider, universe, mechanics, simulator, strategy, run facade, package exports.
- Modify `stock_analyze/competition.py`: add `cn_qdii_etf` to supported markets.
- Modify `stock_analyze/cli.py`: parser choices come from `competition.MARKETS`; no custom command required.
- Modify `stock_analyze/overlay_guard.py`: add the ETF-native factor whitelist for `cn_qdii_etf`.
- Modify `stock_analyze/notifier.py`, `stock_analyze/dashboard_aggregator.py`, and scripts only where they hard-code market labels/lists.
- Create `configs/competition_cn_qdii_etf.yaml` and `configs/agents/{codex,claude}_cn_qdii_etf.yaml`.
- Add tests under `tests/` for dispatch/config, provider, strategy, simulator, and sync/script coverage.

### Task 1: Dispatch And Config

**Files:**
- Test: `tests/test_markets_cn_qdii_etf_bootstrap.py`
- Modify: `stock_analyze/competition.py`
- Modify: `stock_analyze/overlay_guard.py`
- Create: `configs/competition_cn_qdii_etf.yaml`
- Create: `configs/agents/codex_cn_qdii_etf.yaml`
- Create: `configs/agents/claude_cn_qdii_etf.yaml`

- [ ] **Step 1: Write failing tests**

Assert `cn_qdii_etf` is accepted by market dispatch, configs load for both agents, and allowed factors include ETF-native factor names.

- [ ] **Step 2: Verify red**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_bootstrap`

Expected: fail because market/config/package do not exist yet.

- [ ] **Step 3: Implement minimal dispatch/config**

Add the market id to `competition.MARKETS`, create baseline and overlay JSON files, add the factor whitelist entry, and scaffold the package exports.

- [ ] **Step 4: Verify green**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_bootstrap`

Expected: pass.

### Task 2: Provider

**Files:**
- Test: `tests/test_markets_cn_qdii_etf_provider.py`
- Create: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Create: `stock_analyze/markets/cn_qdii_etf/universe.py`

- [ ] **Step 1: Write failing tests**

Use mocked Tushare clients and pandas frames to verify `spot`, `price_snapshot`, `execution_quote`, cache reuse, code normalization, `discount_premium`, `avg_amount_20`, `momentum_20`, `momentum_60`, and `low_volatility_60`.

- [ ] **Step 2: Verify red**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_provider`

Expected: fail because provider functions do not exist.

- [ ] **Step 3: Implement provider**

Implement dataclasses for `ETFPriceSnapshot` and `ETFExecutionQuote`; implement a Tushare-backed provider with cache-aware `fund_daily`, `fund_nav`, `fund_basic`, and `fund_adj` helpers; expose `make_provider`.

- [ ] **Step 4: Verify green**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_provider`

Expected: pass.

### Task 3: Strategy And Simulator

**Files:**
- Test: `tests/test_markets_cn_qdii_etf_strategy.py`
- Test: `tests/test_markets_cn_qdii_etf_simulator.py`
- Create: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Create: `stock_analyze/markets/cn_qdii_etf/mechanics.py`
- Create: `stock_analyze/markets/cn_qdii_etf/simulator.py`
- Create: `stock_analyze/markets/cn_qdii_etf/run.py`
- Modify: `stock_analyze/markets/_settlement_simulator.py`

- [ ] **Step 1: Write failing tests**

Assert strategy emits per-account signal rows from ETF factors; simulator uses lot size 100, zero stamp tax, CNY source labels, and persisted NAV keeps `market_value`.

- [ ] **Step 2: Verify red**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_strategy tests.test_markets_cn_qdii_etf_simulator`

Expected: fail because strategy/simulator are missing and NAV persistence currently drops market value.

- [ ] **Step 3: Implement strategy/simulator**

Mirror HK/US run orchestration; bind settlement simulator to ETF mechanics; fix the shared NAV row keys to include both `market_value` and `benchmark_close` columns expected by `PortfolioStore`.

- [ ] **Step 4: Verify green**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_strategy tests.test_markets_cn_qdii_etf_simulator tests.test_markets_hk_simulator tests.test_markets_us`

Expected: pass.

### Task 4: Operations Integration

**Files:**
- Test: `tests/test_sync_to_ecs.py`
- Test: `tests/test_dashboard_multi_market.py`
- Modify: `scripts/sync-to-ecs.sh`
- Modify: `stock_analyze/notifier.py`
- Modify: `stock_analyze/dashboard_aggregator.py` if labels are hard-coded.

- [ ] **Step 1: Write failing tests**

Assert sync/dashboard/notifier hard-coded market lists include `cn_qdii_etf`.

- [ ] **Step 2: Verify red**

Run: `python3 -m unittest tests.test_sync_to_ecs tests.test_dashboard_multi_market tests.test_notifier_multi_market`

Expected: fail on missing `cn_qdii_etf` coverage if hard-coded lists remain.

- [ ] **Step 3: Implement script/label changes**

Prefer deriving market lists from `competition.MARKETS` in Python and update shell list explicitly where shell cannot import Python cheaply.

- [ ] **Step 4: Verify green**

Run the same tests again and confirm pass.

### Task 5: End-To-End Verification

**Files:**
- No new production files unless verification exposes a bug.

- [ ] **Step 1: Run targeted test suite**

Run: `python3 -m unittest tests.test_markets_cn_qdii_etf_bootstrap tests.test_markets_cn_qdii_etf_provider tests.test_markets_cn_qdii_etf_strategy tests.test_markets_cn_qdii_etf_simulator tests.test_competition_market_dispatch tests.test_cli_market_flag tests.test_sync_to_ecs`

- [ ] **Step 2: Run full tests if time allows**

Run: `python3 -m unittest discover -s tests`

- [ ] **Step 3: Run local CLI smoke with mocked/offline cache if available**

Run: `python3 -m stock_analyze --market cn_qdii_etf --agent codex init --data-dir /tmp/cn_qdii_etf_codex --reports-dir /tmp/cn_qdii_etf_reports`

- [ ] **Step 4: Deploy to ECS**

Use targeted `rsync` for touched source, config, script, and tests files to `root@120.55.188.242:/opt/stock-analyze/app/`.

- [ ] **Step 5: Remote smoke**

Run on ECS with secrets loaded:

`python3 -m stock_analyze.cli --market cn_qdii_etf --agent codex init`

`python3 -m stock_analyze.cli --market cn_qdii_etf --agent codex run-weekly --as-of 2026-07-10`

Expected: provider pulls Tushare fund data, creates pending ETF orders, updates NAV, and writes reports under `data/cn_qdii_etf/codex` and `reports/cn_qdii_etf/codex`.
