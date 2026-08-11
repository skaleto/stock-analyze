# Tasks

Each numbered section ends with the full HK/US suite green, so every section
is independently committable. The five test files that must stay green
throughout (unchanged):
`tests/test_markets_hk_bootstrap.py`,
`tests/test_markets_hk_data_provider.py`,
`tests/test_markets_hk_simulator.py`,
`tests/test_markets_hk_strategy.py`,
`tests/test_markets_us.py` (57 tests total on this branch).

## 1. Extract the provider base (both providers still self-contained)

- [ ] 1.1 Create `stock_analyze/markets/_yfinance_base.py` with the five
  canonical math helpers (`_safe_float`, `_pct_change`,
  `_trailing_volatility`, `_apply_slippage`, `_pd_index_isoformat`).
  `_apply_slippage` takes the bps as an argument / reads `self.slippage_bps`
  instead of a module global.
- [ ] 1.2 Add `YFinanceProviderBase` with `__init__`, `universe`,
  `price_snapshot`, `spot`, `execution_quote`, `_info`, `_history` lifted
  verbatim from today's HK provider, but building `self.snapshot_cls` /
  `self.quote_cls` and calling `self.normalize_symbol(code)` before fetch.
- [ ] 1.3 Add the subclass contract: class attrs `snapshot_cls`,
  `quote_cls`, `slippage_bps`; methods `normalize_symbol` (default
  identity), `lot_size` (abstract), `is_shortable` (default `True`),
  `_fetch_info` / `_fetch_history` (overridable seam).
- [ ] 1.4 Do NOT touch `hk/` or `us/` yet.
- [ ] 1.5 Run `python3 -m unittest discover -s tests` — green (new module
  has no callers).

## 2. Make the HK provider a thin subclass

- [ ] 2.1 In `stock_analyze/markets/hk/data_provider/__init__.py`, keep
  `HKPriceSnapshot`, `HKExecutionQuote`, the module-level `_fetch_ticker_info`
  / `_fetch_ticker_history`, and `make_provider` exactly where they are.
- [ ] 2.2 Rewrite `YFinanceHKProvider` to extend `YFinanceProviderBase`:
  set `snapshot_cls = HKPriceSnapshot`, `quote_cls = HKExecutionQuote`,
  `slippage_bps = mechanics.SLIPPAGE_BPS`; implement `lot_size` →
  `mechanics.lot_size_for(code)`; implement `_fetch_info` /
  `_fetch_history` to call the **module-level** `_fetch_ticker_info` /
  `_fetch_ticker_history` (so `patch("…hk.data_provider._fetch_ticker_info")`
  still works).
- [ ] 2.3 Delete the now-inherited method bodies and the five duplicated
  local helper definitions; re-export the helpers from the base
  (`from .._yfinance_base import _safe_float, …`) for any stray importers.
- [ ] 2.4 Confirm `__all__` is unchanged.
- [ ] 2.5 Run `python3 -m unittest tests.test_markets_hk_data_provider
  tests.test_markets_hk_bootstrap tests.test_markets_hk_strategy` — green,
  unchanged.

## 3. Make the US provider a thin subclass

- [ ] 3.1 In `stock_analyze/markets/us/data_provider/__init__.py`, keep
  `USPriceSnapshot`, `USExecutionQuote`, the module-level `_fetch_ticker_info`
  / `_fetch_ticker_history`, and `make_provider` where they are.
- [ ] 3.2 Rewrite `YFinanceUSProvider` to extend `YFinanceProviderBase`:
  `snapshot_cls = USPriceSnapshot`, `quote_cls = USExecutionQuote`,
  `slippage_bps = mechanics.SLIPPAGE_BPS`; `lot_size` → `1`; `_fetch_info`
  / `_fetch_history` → module-level US `_fetch_ticker_*`.
- [ ] 3.3 Delete the duplicated method bodies + the five duplicated helper
  copies; re-export helpers from the base.
- [ ] 3.4 Confirm `__all__` is unchanged.
- [ ] 3.5 Run `python3 -m unittest tests.test_markets_us` — green,
  unchanged. Provider duplication is now eliminated.

## 4. Extract the settlement-simulator base

- [ ] 4.1 Create `stock_analyze/markets/_settlement_simulator.py` defining
  `MechanicsProtocol` (`typing.Protocol`: `SETTLEMENT_DAYS`,
  `STAMP_TAX_RATE`, `COMMISSION_RATE`, `SHORTING_COLLATERAL_RATIO`,
  `lot_size_for`).
- [ ] 4.2 Add `SettlementSimulatorBase` with `mechanics`, `order_cls`,
  `market_id` attributes and `initialize`, `execute_due_orders`,
  `update_nav`, `generate_rebalance_orders`, plus internals
  `_next_business_day`, `_drain_settlement`, `_coerce_order`,
  `_execute_order` (buy/sell/short/cover), `_quote_side`, `_trade_record`
  — lifted from today's HK simulator.
- [ ] 4.3 Replace HK-literal fee math with `gross * self.mechanics.STAMP_TAX_RATE`
  and `gross * self.mechanics.COMMISSION_RATE`; derive the NAV `source`
  label from `self.market_id` (`f"{market_id}-daily"`).
- [ ] 4.4 No callers yet — run `python3 -m unittest discover -s tests` —
  green.

## 5. Thin the HK simulator

- [ ] 5.1 In `stock_analyze/markets/hk/simulator.py`, keep the `HKOrder`
  dataclass. Construct a module singleton
  `_SIM = SettlementSimulatorBase(mechanics=hk.mechanics, order_cls=HKOrder,
  market_id="hk")`.
- [ ] 5.2 Replace `initialize` / `execute_due_orders` / `update_nav` /
  `generate_rebalance_orders` with byte-identical-signature free functions
  that delegate to `_SIM`. Delete the duplicated helper + execution bodies.
- [ ] 5.3 Keep `__all__` byte-identical (`["HKOrder", "execute_due_orders",
  "generate_rebalance_orders", "initialize", "update_nav"]`).
- [ ] 5.4 Run `python3 -m unittest tests.test_markets_hk_simulator` —
  green, unchanged.

## 6. Thin the US simulator

- [ ] 6.1 In `stock_analyze/markets/us/simulator.py`, keep `USOrder`;
  construct `_SIM = SettlementSimulatorBase(mechanics=us.mechanics,
  order_cls=USOrder, market_id="us")`.
- [ ] 6.2 Replace the four public functions with delegating free functions;
  delete the duplicated bodies. Confirm the zero-fee US trade records still
  show `commission == 0.0` and `stamp_tax == 0.0`.
- [ ] 6.3 Keep `__all__` byte-identical (`["USOrder", …]`).
- [ ] 6.4 Run `python3 -m unittest tests.test_markets_us` — green,
  unchanged. Simulator duplication is now eliminated.

## 7. Full-suite + public-surface verification

- [ ] 7.1 Run `python3 -m unittest discover -s tests` — all green.
- [ ] 7.2 Assert public surface unchanged: `from stock_analyze.markets.hk
  import make_provider, execute_due_orders, update_nav,
  generate_rebalance_orders, initialize, build_signals` and the `markets.us`
  equivalent both resolve; `isinstance(make_provider(), YFinanceHKProvider)`
  / `YFinanceUSProvider` still hold.
- [ ] 7.3 Confirm no edits were made to any file under `tests/`, to
  `markets/{hk,us}/__init__.py`, `markets/{hk,us}/strategy.py`,
  `markets/{hk,us}/mechanics.py`, `markets/{hk,us}/universe.py`, or to
  `markets/a_share/*`.
- [ ] 7.4 Grep the two `data_provider/__init__.py` + two `simulator.py`
  files to confirm the five math helpers and the settlement/execution
  bodies appear only once (in the bases), not duplicated.

## 8. Documentation touch-ups (non-blocking)

- [ ] 8.1 Update the HK `data_provider` docstring's "When the US module
  lands … the common code moves into `markets/_yfinance_base.py`" line to
  past tense ("now lives in `markets/_yfinance_base.py`").
- [ ] 8.2 Update the US `data_provider` "kept duplicated for v1 isolation"
  comment to point at the shared base.
