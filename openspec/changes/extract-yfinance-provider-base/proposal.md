## Why

HK (Phase 2) and US (Phase 3) markets were added as near-clones of each
other. Their `data_provider` and `simulator` modules are ~80% duplicated,
and the duplication is *byte-identical* in the parts that matter most:

**`data_provider/__init__.py` — HK vs US:**

- The five module-level math/IO helpers are **byte-identical** across the
  two files:
  - `_safe_float` (HK `__init__.py:351-362` ≡ US `__init__.py:236-245`)
  - `_pct_change` (HK `:365-373` ≡ US `:248-255`)
  - `_trailing_volatility` (HK `:376-383` ≡ US `:258-264`)
  - `_apply_slippage` (HK `:386-394` ≡ US `:267-275`)
  - `_pd_index_isoformat` (HK `:397-403` ≡ US `:278-283`)
- The provider classes `YFinanceHKProvider` / `YFinanceUSProvider` are
  near-identical (`__init__`, `universe`, `price_snapshot`, `spot`,
  `execution_quote`, `_info`, `_history`, `is_shortable`). They differ
  only in:
  1. ticker symbol convention (HK uses a `.HK` suffix; US uses bare
     tickers — today this difference lives entirely in `universe.py`, the
     providers don't normalize),
  2. the `SLIPPAGE_BPS` import source (`hk.mechanics` vs `us.mechanics`),
  3. `lot_size` (HK delegates to `mechanics.lot_size_for` → 100; US
     hard-codes `1`),
  4. which dataclass they build (`HKPriceSnapshot`/`HKExecutionQuote` vs
     `USPriceSnapshot`/`USExecutionQuote`).
- The US module's own docstring already flags the duplication as
  temporary, and the HK module's docstring (`:15-18`) *predicts the exact
  fix this change implements*: "When the US module lands (Phase 3) the
  common code moves into `markets/_yfinance_base.py`".

**`simulator.py` — HK vs US:**

- The settlement-queue helpers `_next_business_day` (HK `:128-137` ≡ US
  `:68-75`) and `_drain_settlement` (HK `:140-156` ≡ US `:78-90`) are
  byte-identical.
- `initialize`, `execute_due_orders`, `_coerce_order`, `_execute_order`
  (buy / sell / short / cover branches), `_quote_side`, `_trade_record`,
  `update_nav`, and `generate_rebalance_orders` are near-identical. They
  differ only in:
  1. `SETTLEMENT_DAYS` (HK=2, US=1) — sourced from each `mechanics.py`,
  2. fee handling (HK applies `STAMP_TAX_RATE` + `COMMISSION_RATE` on both
     sides; US is zero-fee, so the US `_execute_order` passes `0.0, 0.0`),
  3. `lot_size_for` (HK→100, US→1),
  4. the `Order` dataclass name (`HKOrder` vs `USOrder`) and the
     `"hk-daily"` / `"us-daily"` NAV `source` label.

This duplication is a standing liability:

1. **Double-maintenance.** Every fix or feature must be applied twice, and
   the two copies can silently drift.
2. **The short-sale NAV bug is duplicated.** The `update_nav` short-leg
   accounting (`positions_value -= abs(shares) * px` with collateral added
   separately) currently lives in **both** simulators identically. A
   sibling change, `fix-short-sale-nav-accounting`, targets exactly that
   logic. If C2 (this change) lands first or alongside it, that fix is
   written **once** in the shared base instead of twice — and can never
   drift between markets afterward. This synergy is the main reason to do
   the extraction now rather than later.
3. **The original authors intended this.** The seam was designed in from
   day one (module-level `_fetch_*` mock points, mechanics modules holding
   the per-market constants); only the extraction step was deferred.

## What Changes

- **Add `stock_analyze/markets/_yfinance_base.py`** — a
  `YFinanceProviderBase` class holding the shared fetch/cache/snapshot/
  execution-quote logic plus the five shared math helpers. HK/US providers
  become thin subclasses supplying: symbol normalization, the slippage
  constant, `lot_size`, and the snapshot/quote dataclass types.
- **Add a shared settlement-simulator base** (a base class in
  `stock_analyze/markets/_settlement_simulator.py`) holding the
  settlement-queue helpers + buy/sell/short/cover execution + `update_nav`
  + `generate_rebalance_orders`, parameterized by a small
  `MechanicsProtocol` (settlement_days, fee rates, `lot_size_for`,
  shorting params, NAV source label). HK/US simulators become thin
  wrappers binding their `mechanics` module.
- **Confine per-market differences to `mechanics.py` + symbol
  convention.** After the extraction, the only HK↔US deltas are the
  constants in each `mechanics.py`, the symbol normalization hook, and the
  dataclass types each market builds.
- **Preserve the test-mocking seam.** Existing tests patch
  `stock_analyze.markets.hk.data_provider._fetch_ticker_info` and the US
  equivalent. The refactor keeps a working patch point at those exact
  dotted paths (see `design.md` §"Test-mocking seam").

This change is **planning + structure only** — no behavioural change to
signals, NAV, fees, or order matching is intended. It is a pure
de-duplication.

### Backward-compatibility constraint (hard)

The public API of `markets/hk` and `markets/us` MUST stay **byte-identical**
across the refactor:

- The `markets.hk.__init__` / `markets.us.__init__` exports
  (`make_provider`, `execute_due_orders`, `update_nav`,
  `generate_rebalance_orders`, `initialize`, `build_signals`) keep the same
  names, signatures, and return shapes.
- `make_provider()` still returns a `YFinanceHKProvider` /
  `YFinanceUSProvider` instance (subclasses of the new base), so existing
  `isinstance` assertions in tests pass unchanged.
- The dataclass field layouts (`HKPriceSnapshot` etc.) are unchanged.
- The 57 existing HK/US unit tests
  (`tests/test_markets_hk_bootstrap.py`,
  `tests/test_markets_hk_data_provider.py`,
  `tests/test_markets_hk_simulator.py`,
  `tests/test_markets_hk_strategy.py`, `tests/test_markets_us.py`) pass
  **unchanged**. *(The task brief estimated ~110; the measured count of
  these five files on this branch is 57. The requirement is "all of them,
  unchanged" regardless of the exact number.)*

### Out of scope

- **The A-share simulator and provider are NOT touched.** A-share uses
  Tushare (a different provider/simulator lineage under
  `markets/a_share/`), not yfinance, and has no settlement-queue/shorting
  model of the HK/US shape. It is explicitly excluded.
- **No new market behaviour, factor, or fee model.** This change does not
  add fractional shares, real lot-size lookups, holiday calendars, or
  borrow checks — those remain v1 stubs exactly as they are.
- **The `fix-short-sale-nav-accounting` fix itself is a separate change.**
  This change only makes that fix land in one place; it does not apply it.

## Impact

- **Affected specs:** adds capability `yfinance-provider-base` (this
  change's `specs/yfinance-provider-base/spec.md`).
- **Affected code (created):**
  - `stock_analyze/markets/_yfinance_base.py` (new)
  - `stock_analyze/markets/_settlement_simulator.py` (new)
- **Affected code (thinned, behaviour preserved):**
  - `stock_analyze/markets/hk/data_provider/__init__.py`
  - `stock_analyze/markets/us/data_provider/__init__.py`
  - `stock_analyze/markets/hk/simulator.py`
  - `stock_analyze/markets/us/simulator.py`
- **Unchanged public surface:** `markets/hk/__init__.py`,
  `markets/us/__init__.py`, `markets/hk/strategy.py`,
  `markets/us/strategy.py`, `markets/{hk,us}/mechanics.py`,
  `markets/{hk,us}/universe.py`, and all CLI/notifier/dashboard callers.
- **Tests:** the 57 HK/US tests run unchanged. No A-share, backtest, or
  competition tests are affected.
- **Synergy:** unblocks `fix-short-sale-nav-accounting` to be a
  single-site fix.
