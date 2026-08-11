# Design

## Goal

Kill the HK/US duplication by extracting two shared bases — one for the
yfinance data provider, one for the settlement-style simulator — while
keeping the public API of `markets/hk` and `markets/us` byte-identical and
the 57 existing HK/US tests passing unchanged. Per-market behaviour is
confined to each `mechanics.py` plus a symbol-normalization hook.

This is a structural refactor. The end state must produce bit-for-bit the
same signals, NAV, trades, and fees as today for any given input.

## Current shape (what we're collapsing)

```
markets/
  hk/
    data_provider/__init__.py   # YFinanceHKProvider + 5 module helpers + _fetch_* + make_provider
    simulator.py                # HKOrder + settlement + buy/sell/short/cover + update_nav + rebalance
    mechanics.py                # SETTLEMENT_DAYS=2, STAMP_TAX_RATE, COMMISSION_RATE, SLIPPAGE_BPS=5, lot_size_for→100
    strategy.py, universe.py    # (unchanged by this refactor)
  us/
    data_provider/__init__.py   # YFinanceUSProvider + SAME 5 module helpers + _fetch_* + make_provider
    simulator.py                # USOrder + SAME settlement + buy/sell/short/cover + update_nav + rebalance
    mechanics.py                # SETTLEMENT_DAYS=1, STAMP_TAX_RATE=0, COMMISSION_RATE=0, SLIPPAGE_BPS=3, lot_size_for→1
    strategy.py, universe.py
  a_share/                      # Tushare lineage — OUT OF SCOPE
```

## Target shape

```
markets/
  _yfinance_base.py             # YFinanceProviderBase + the 5 shared helpers (canonical home)
  _settlement_simulator.py      # SettlementSimulatorBase + MechanicsProtocol
  hk/
    data_provider/__init__.py   # YFinanceHKProvider(YFinanceProviderBase) [thin] + _fetch_* seam + make_provider
    simulator.py                # binds hk.mechanics to the base [thin] + HKOrder alias + public fns
    mechanics.py                # unchanged
  us/
    data_provider/__init__.py   # YFinanceUSProvider(YFinanceProviderBase) [thin] + _fetch_* seam + make_provider
    simulator.py                # binds us.mechanics to the base [thin] + USOrder alias + public fns
    mechanics.py                # unchanged
```

## Component 1 — `YFinanceProviderBase`

A base class in `markets/_yfinance_base.py` owning everything the two
providers share. The five module-level math helpers (`_safe_float`,
`_pct_change`, `_trailing_volatility`, `_apply_slippage`,
`_pd_index_isoformat`) move here as the **canonical** definitions (module
functions, importable). `_apply_slippage` currently closes over a
module-global `SLIPPAGE_BPS`; in the base it takes the bps as an argument
(or reads `self.slippage_bps`) so it is no longer market-coupled.

Subclasses declare the per-market specifics as class attributes / small
overrides:

```python
class YFinanceProviderBase:
    # --- subclass contract ---
    snapshot_cls: type      # HKPriceSnapshot / USPriceSnapshot
    quote_cls: type         # HKExecutionQuote / USExecutionQuote
    slippage_bps: float     # from the market's mechanics

    def normalize_symbol(self, code: str) -> str:
        """Map a universe code to the yfinance ticker. Default: identity
        (US). HK overrides only if/when the universe stops carrying the
        `.HK` suffix; today both pass the code straight through, so the
        default identity keeps behaviour byte-identical."""
        return code

    def lot_size(self, code: str) -> int: ...   # subclass supplies
    def is_shortable(self, code: str) -> bool:  # default True (both markets)
        return True

    # --- shared, lifted verbatim from today's HK provider ---
    def __init__(self, cache_dir=None, offline=False, as_of=None): ...
    def universe(self, scope): ...
    def price_snapshot(self, code, as_of=None) -> snapshot_cls: ...
    def spot(self, scope) -> pd.DataFrame: ...
    def execution_quote(self, code, execute_after, side, as_of=None) -> quote_cls: ...
    def _info(self, code): ...        # calls the fetch seam (see below)
    def _history(self, code, period="3mo"): ...

    # --- fetch seam (overridable) ---
    def _fetch_info(self, symbol): ...     # default raises NotImplementedError
    def _fetch_history(self, symbol, *, period="3mo"): ...
```

`price_snapshot` / `execution_quote` build `self.snapshot_cls(...)` /
`self.quote_cls(...)` instead of a hard-coded class, and call
`self.normalize_symbol(code)` before fetching. Because the snapshot dataclass
field layouts are identical across HK and US, the body is otherwise verbatim.

The HK subclass shrinks to:

```python
class YFinanceHKProvider(YFinanceProviderBase):
    snapshot_cls = HKPriceSnapshot
    quote_cls = HKExecutionQuote
    slippage_bps = hk_mechanics.SLIPPAGE_BPS

    def lot_size(self, code):
        return hk_mechanics.lot_size_for(code)

    def _fetch_info(self, symbol):
        return _fetch_ticker_info(symbol)          # module-level seam — see below

    def _fetch_history(self, symbol, *, period="3mo"):
        return _fetch_ticker_history(symbol, period=period)
```

US is the mirror image with `lot_size → 1` and `slippage_bps = 3`.

## Component 2 — `SettlementSimulatorBase` + `MechanicsProtocol`

A `MechanicsProtocol` (a `typing.Protocol`, structural — each `mechanics.py`
already satisfies it by exposing module-level names) captures the per-market
knobs the simulator reads:

```python
class MechanicsProtocol(Protocol):
    SETTLEMENT_DAYS: int
    STAMP_TAX_RATE: float
    COMMISSION_RATE: float
    SHORTING_COLLATERAL_RATIO: float
    def lot_size_for(self, code: str, default: int = ...) -> int: ...
```

The base owns the full order-execution machinery, parameterized by a bound
`mechanics` module + a NAV source label + an order factory:

```python
class SettlementSimulatorBase:
    mechanics: MechanicsProtocol
    order_cls: type            # HKOrder / USOrder
    market_id: str             # "hk" / "us"   -> NAV source "hk-daily"/"us-daily"

    def initialize(self, config, store): ...
    def execute_due_orders(self, store, provider, *, as_of=None): ...
    def update_nav(self, store, provider, *, as_of=None): ...
    def generate_rebalance_orders(self, store, provider, scored, *, as_of=None,
                                  top_n=50, max_single_weight=0.05): ...
    # internal: _next_business_day, _drain_settlement, _coerce_order,
    # _execute_order (buy/sell/short/cover), _quote_side, _trade_record
```

The fee lines in `_execute_order` become `gross * self.mechanics.STAMP_TAX_RATE`
and `gross * self.mechanics.COMMISSION_RATE`. For US these evaluate to `0.0`
because `us.mechanics` defines both as `0.0` — so the US zero-fee behaviour
is reproduced exactly **without** a special-case branch. This is the key
correctness insight: the US "pass `0.0, 0.0`" today is numerically identical
to "multiply by a zero rate", so a single parameterized path covers both.

Each market's `simulator.py` shrinks to a binding + module-level public
functions that delegate to a singleton base instance, preserving the exact
existing free-function API:

```python
# markets/hk/simulator.py  (thin)
from . import mechanics as _mech
from .._settlement_simulator import SettlementSimulatorBase

@dataclass
class HKOrder: ...     # kept where it is (public, re-exported)

_SIM = SettlementSimulatorBase(mechanics=_mech, order_cls=HKOrder, market_id="hk")

def initialize(config, store):                       return _SIM.initialize(config, store)
def execute_due_orders(store, provider, *, as_of=None): return _SIM.execute_due_orders(store, provider, as_of=as_of)
def update_nav(store, provider, *, as_of=None):      return _SIM.update_nav(store, provider, as_of=as_of)
def generate_rebalance_orders(store, provider, scored, *, as_of=None, top_n=50, max_single_weight=0.05):
    return _SIM.generate_rebalance_orders(store, provider, scored, as_of=as_of, top_n=top_n, max_single_weight=max_single_weight)

__all__ = ["HKOrder", "execute_due_orders", "generate_rebalance_orders", "initialize", "update_nav"]
```

The `__all__` and the free-function signatures are byte-identical to today,
so `markets/hk/__init__.py` (which does `from .simulator import ...`) needs
no edit.

## Test-mocking seam (the load-bearing detail)

Today the tests patch the **module-level** functions on the concrete market
module:

- `tests/test_markets_hk_data_provider.py` (and others) patch
  `stock_analyze.markets.hk.data_provider._fetch_ticker_info` and
  `…hk.data_provider._fetch_ticker_history`.
- `tests/test_markets_us.py` patches
  `stock_analyze.markets.us.data_provider._fetch_ticker_info` /
  `…_fetch_ticker_history`.

`unittest.mock.patch` rebinds the name **on the module object given by the
dotted path**. If the base class called a function imported into
`_yfinance_base` (e.g. `from somewhere import _fetch_ticker_info`), patching
`hk.data_provider._fetch_ticker_info` would no longer affect the base, and
the 57 tests would break. We must NOT relocate the fetch functions to the
base module wholesale.

**Decision: keep `_fetch_ticker_info` / `_fetch_ticker_history` as
module-level functions in `hk/data_provider/__init__.py` and
`us/data_provider/__init__.py` (their current homes), and route the base to
them through an overridable instance method.** Concretely:

- The base defines `_fetch_info(self, symbol)` / `_fetch_history(self,
  symbol, *, period)` that subclasses override.
- The HK subclass override calls the **module-level**
  `_fetch_ticker_info(symbol)` defined in `hk/data_provider/__init__.py`.
  Because the override resolves `_fetch_ticker_info` as a global of the HK
  module **at call time**, `patch("…hk.data_provider._fetch_ticker_info")`
  still takes effect. The US subclass mirrors this.
- `_info` / `_history` (in the base) call `self._fetch_info(...)` /
  `self._fetch_history(...)`, so the caching + exception-swallowing logic is
  shared while the patch point stays exactly where the tests expect it.

This satisfies the brief's "preserve a working patch point" option with
**zero test edits**. (The alternative — pushing the seam to a single base
function and updating every `patch(...)` target — is explicitly rejected
here because it would force editing all five test files; this change's
contract is that the 57 tests run unchanged. If a future change *wants* a
single patch point, that is a separate, opt-in migration with its own spec
amendment.)

The five math helpers can move to the base because nothing patches them
(grep confirms only `_fetch_ticker_*` are patched). To be safe and keep any
hypothetical `from …hk.data_provider import _safe_float` callers working,
the thin HK/US modules re-export them: `from .._yfinance_base import
_safe_float, _pct_change, _trailing_volatility, _apply_slippage,
_pd_index_isoformat`. (grep shows no such imports today, so this is belt-and-
suspenders.)

## Migration approach (suite stays green between every step)

1. **Extract the provider base, both providers still self-contained.**
   Create `_yfinance_base.py` with `YFinanceProviderBase` + the five
   canonical helpers. Do not touch HK/US yet. Run the suite — green (new
   file has no callers).
2. **Make HK provider subclass the base.** Rewrite
   `hk/data_provider/__init__.py` so `YFinanceHKProvider` extends
   `YFinanceProviderBase`, keeping `_fetch_ticker_info`/`_fetch_ticker_history`
   module-level and the dataclasses local. Delete the now-duplicated method
   bodies + the five local helper copies (re-export from base). Run the HK
   tests — green.
3. **Make US provider subclass the base.** Same surgery on
   `us/data_provider/__init__.py`. Run the US tests — green. Provider
   duplication is now gone.
4. **Extract the simulator base.** Create `_settlement_simulator.py` with
   `SettlementSimulatorBase` + `MechanicsProtocol`. No callers yet — suite
   green.
5. **Thin the HK simulator.** Replace `hk/simulator.py`'s bodies with the
   binding + delegating free functions. Keep `HKOrder` and `__all__`
   byte-identical. Run HK simulator tests — green.
6. **Thin the US simulator.** Same for `us/simulator.py`. Run US tests —
   green. Simulator duplication is now gone.
7. **Full suite + import-surface check.** `python3 -m unittest discover -s
   tests` all green; spot-check that `from stock_analyze.markets.hk import
   make_provider, update_nav, …` and the US equivalents resolve to the same
   names with the same signatures.

Each numbered step is independently committable with a green suite, which is
also how `tasks.md` is staged.

## Risks & mitigations

- **R1 — public API drift.** *Mitigation:* the thin modules re-declare the
  exact `__all__` and free-function signatures; `make_provider` still
  returns the concrete subclass so `isinstance` tests pass. The spec pins
  byte-identical public surface as a hard requirement (Requirement 2).
- **R2 — broken mock seam → 57 tests fail.** *Mitigation:* keep
  `_fetch_ticker_*` module-level in each market's `data_provider`, route via
  an overridable instance method (see above). No test target moves.
- **R3 — accidental behaviour change in the zero-fee path.** US uses
  literal `0.0` today; the base multiplies by `mechanics.STAMP_TAX_RATE`
  (=0.0) / `COMMISSION_RATE` (=0.0). These are numerically identical.
  *Mitigation:* the US simulator tests assert zero fees and zero stamp on
  trade records; they must pass unchanged.
- **R4 — scope creep into A-share.** *Mitigation:* A-share is explicitly
  excluded; the bases live under `markets/_*` and are imported only by HK/US.
- **R5 — the `.HK` symbol convention.** Today neither provider normalizes;
  the `.HK` suffix is carried by `universe.py`. *Mitigation:* the base's
  `normalize_symbol` defaults to identity, so behaviour is unchanged; the
  hook merely *documents* where HK-specific normalization would go if the
  universe ever stops carrying the suffix.

## Synergy with `fix-short-sale-nav-accounting`

The short-leg NAV math in `update_nav` (`positions_value -= abs(shares)*px`,
collateral added separately) is identical in both simulators today. After
step 6 it lives once in `SettlementSimulatorBase.update_nav`. The sibling
change `fix-short-sale-nav-accounting` then edits a **single** method, and
the fix automatically applies to HK and US with no drift risk. Sequencing
this change first (or co-landing them) is the recommended order; the spec's
Requirement 3 ("per-market differences confined to mechanics + symbol
convention") makes that single-site property explicit and testable.
