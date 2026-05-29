## ADDED Requirements

### Requirement: A shared YFinanceProviderBase exists and HK/US providers subclass it

The system SHALL provide a `YFinanceProviderBase` class in
`stock_analyze/markets/_yfinance_base.py` that owns the yfinance
fetch/cache/snapshot/execution-quote logic shared by the HK and US markets,
together with the five math/IO helpers (`_safe_float`, `_pct_change`,
`_trailing_volatility`, `_apply_slippage`, `_pd_index_isoformat`) as their
canonical definitions.

`YFinanceHKProvider` and `YFinanceUSProvider` SHALL each subclass
`YFinanceProviderBase` and supply only: the snapshot/quote dataclass types
(`snapshot_cls` / `quote_cls`), the slippage constant (`slippage_bps`,
sourced from the market's `mechanics`), the `lot_size` implementation, and a
symbol-normalization hook. A parallel `SettlementSimulatorBase` (in
`stock_analyze/markets/_settlement_simulator.py`) SHALL likewise own the
settlement-queue, buy/sell/short/cover execution, `update_nav`, and
`generate_rebalance_orders` logic, parameterized by a `MechanicsProtocol`.

The shared bases SHALL be imported only by the HK and US markets; the
A-share market (Tushare lineage) SHALL NOT depend on them and SHALL remain
unchanged.

#### Scenario: Both providers are subclasses sharing one implementation

- **GIVEN** the refactored `stock_analyze.markets` package
- **WHEN** `YFinanceHKProvider` and `YFinanceUSProvider` are imported
- **THEN** both are subclasses of
  `stock_analyze.markets._yfinance_base.YFinanceProviderBase`
- **AND** the methods `universe`, `price_snapshot`, `spot`,
  `execution_quote`, `_info`, and `_history` are defined once on the base
  (not overridden with duplicated bodies in either subclass)
- **AND** the five math helpers `_safe_float`, `_pct_change`,
  `_trailing_volatility`, `_apply_slippage`, `_pd_index_isoformat` are
  defined once in `_yfinance_base.py`

#### Scenario: A-share is untouched by the extraction

- **GIVEN** the refactored package
- **WHEN** the A-share provider and simulator under
  `stock_analyze/markets/a_share/` are inspected
- **THEN** they do not import `_yfinance_base` or `_settlement_simulator`
- **AND** their source is unchanged by this change

### Requirement: HK/US public API stays byte-identical across the refactor

The system SHALL keep the public API exported by `stock_analyze.markets.hk`
and `stock_analyze.markets.us` byte-identical before and after the refactor.
Specifically, `make_provider`, `execute_due_orders`, `update_nav`,
`generate_rebalance_orders`, `initialize`, and `build_signals` SHALL keep
the same names, call signatures, and return shapes, and the `__all__` of
each `simulator.py` SHALL be unchanged.

`make_provider()` SHALL continue to return a concrete
`YFinanceHKProvider` / `YFinanceUSProvider` instance (a subclass of the
base), and the snapshot/quote dataclass field layouts SHALL be unchanged, so
existing callers, the CLI, the notifier, the dashboard, and `isinstance`
assertions continue to work without edits.

#### Scenario: make_provider returns the concrete subclass type

- **GIVEN** the refactored HK and US markets
- **WHEN** `stock_analyze.markets.hk.make_provider()` and
  `stock_analyze.markets.us.make_provider()` are called
- **THEN** the first returns an instance of `YFinanceHKProvider`
- **AND** the second returns an instance of `YFinanceUSProvider`
- **AND** both returned instances are also instances of
  `YFinanceProviderBase`

#### Scenario: Public function names and simulator __all__ are unchanged

- **GIVEN** the refactored markets
- **WHEN** `stock_analyze.markets.hk` and `stock_analyze.markets.us` are
  imported
- **THEN** each exposes `make_provider`, `execute_due_orders`,
  `update_nav`, `generate_rebalance_orders`, `initialize`, and
  `build_signals`
- **AND** `markets.hk.simulator.__all__` equals
  `["HKOrder", "execute_due_orders", "generate_rebalance_orders", "initialize", "update_nav"]`
- **AND** `markets.us.simulator.__all__` equals
  `["USOrder", "execute_due_orders", "generate_rebalance_orders", "initialize", "update_nav"]`

### Requirement: Per-market differences are confined to mechanics and symbol convention

The system SHALL, after the refactor, confine the behavioural differences
between the HK and US yfinance provider/simulator pair to: (1) the constants in each
market's `mechanics.py` (`SETTLEMENT_DAYS`, `STAMP_TAX_RATE`,
`COMMISSION_RATE`, `SHORTING_COLLATERAL_RATIO`, `SLIPPAGE_BPS`,
`lot_size_for`), (2) the symbol-normalization hook, and (3) the
snapshot/quote/order dataclass types each market builds.

The simulator base SHALL compute fees as `gross * mechanics.STAMP_TAX_RATE`
and `gross * mechanics.COMMISSION_RATE` rather than hard-coding any
market-specific fee branch, so that the US zero-fee behaviour is reproduced
exactly because `us.mechanics` defines both rates as `0.0`. The NAV `source`
label SHALL be derived from the bound market id (`hk-daily` / `us-daily`).

This confinement SHALL make a single-site fix possible for the sibling
change `fix-short-sale-nav-accounting`: after this refactor, the short-leg
NAV accounting in `update_nav` lives in exactly one place
(`SettlementSimulatorBase.update_nav`) and applies to both markets without
drift.

#### Scenario: US zero-fee path is reproduced via mechanics constants, not a branch

- **GIVEN** the refactored US simulator bound to `us.mechanics`
  (`STAMP_TAX_RATE = 0.0`, `COMMISSION_RATE = 0.0`)
- **WHEN** a US buy order and a US sell order are executed through
  `execute_due_orders`
- **THEN** each resulting trade record has `commission == 0.0` and
  `stamp_tax == 0.0`
- **AND** the simulator base contains no US-specific fee branch — the zero
  comes from multiplying `gross` by the zero rates in `us.mechanics`

#### Scenario: HK fees and T+2 settlement come from hk.mechanics

- **GIVEN** the refactored HK simulator bound to `hk.mechanics`
  (`SETTLEMENT_DAYS = 2`, `STAMP_TAX_RATE = 0.0013`,
  `COMMISSION_RATE = 0.0003`)
- **WHEN** an HK sell order is executed on a trade date `T`
- **THEN** the trade record's `stamp_tax` equals `gross * 0.0013` and
  `commission` equals `gross * 0.0003`
- **AND** the credited cash is queued with `settle_date = T + 2` business
  days, matching the pre-refactor behaviour

#### Scenario: Short-sale NAV logic is single-site after the refactor

- **GIVEN** the refactored simulators
- **WHEN** the short-leg NAV accounting (the `positions_value` adjustment
  for negative share counts plus collateral handling) is located
- **THEN** it is defined exactly once, in
  `SettlementSimulatorBase.update_nav`
- **AND** neither `markets/hk/simulator.py` nor `markets/us/simulator.py`
  contains its own copy of that logic

### Requirement: The existing HK/US tests pass unchanged with the mock seam preserved

All existing HK and US unit tests SHALL pass **unchanged** after the
refactor — namely `tests/test_markets_hk_bootstrap.py`,
`tests/test_markets_hk_data_provider.py`,
`tests/test_markets_hk_simulator.py`,
`tests/test_markets_hk_strategy.py`, and `tests/test_markets_us.py`. No
edits to any file under `tests/` are permitted by this change.

To achieve this, the module-level functions `_fetch_ticker_info` and
`_fetch_ticker_history` SHALL remain defined in
`stock_analyze/markets/hk/data_provider/__init__.py` and
`stock_analyze/markets/us/data_provider/__init__.py` (their current homes),
because the tests patch them at exactly those dotted paths
(`stock_analyze.markets.hk.data_provider._fetch_ticker_info` and the US
equivalent). The provider base SHALL reach these functions through an
overridable instance-method seam (`_fetch_info` / `_fetch_history`) whose
subclass override resolves the module-level name at call time, so that
`unittest.mock.patch` on the existing target continues to take effect.

#### Scenario: Patching the existing dotted path still controls the fetch

- **GIVEN** the refactored HK provider obtained from `make_provider()`
- **WHEN** a test patches
  `stock_analyze.markets.hk.data_provider._fetch_ticker_info` and
  `stock_analyze.markets.hk.data_provider._fetch_ticker_history` and then
  calls `provider.price_snapshot("0700.HK")`
- **THEN** the snapshot is built from the patched return values (no real
  network call occurs)
- **AND** the same holds for `stock_analyze.markets.us.data_provider`
  patched targets with `provider.price_snapshot("AAPL")`

#### Scenario: Full HK/US suite is green without test edits

- **GIVEN** the completed refactor
- **WHEN** `python3 -m unittest tests.test_markets_hk_bootstrap
  tests.test_markets_hk_data_provider tests.test_markets_hk_simulator
  tests.test_markets_hk_strategy tests.test_markets_us` is run
- **THEN** every test passes
- **AND** no file under `tests/` was modified to make them pass
