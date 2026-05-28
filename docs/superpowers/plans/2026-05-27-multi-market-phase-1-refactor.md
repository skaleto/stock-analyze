# Multi-Market Competition — Phase 1: A-Share Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Relocate the existing A-share competition code into `stock_analyze/markets/a_share/`, add `--market` CLI flag with `a_share` default, migrate data + config paths, prepare shared modules to accept a `market` parameter — without changing any A-share behavior or breaking the 360+ existing tests.

**Architecture:** Symmetric subpackage layout (Option A from the spec). A-share moves under `markets/a_share/`; HK + US will join in Phases 2-3 as siblings. Shared modules (factor_pipeline, overlay_guard, evolution_writer, notifier, sanity_check, reporting, agent_briefing, dashboard_aggregator, monthly_review, agent_rollback) stay at top level and gain a `market: str = "a_share"` parameter.

**Tech Stack:** Python 3.11, pandas, systemd, no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-27-multi-market-competition-design.md`

**Deployment sensitivity:** Phase 1 ships before the next ECS weekly run (Saturday 2026-05-30 10:00). All migration must be complete + verified by Friday 2026-05-29 evening, with `data/` and `configs/` cut over on ECS Saturday morning before 10:00.

---

## File Structure

**New files**:
- `stock_analyze/markets/__init__.py` — empty package marker
- `stock_analyze/markets/a_share/__init__.py` — re-exports A-share public API
- `scripts/migrate-phase-1-paths.sh` — one-time path migration script

**Moved files** (git mv preserves history):
- `stock_analyze/simulator.py` → `stock_analyze/markets/a_share/simulator.py`
- `stock_analyze/strategy.py` → `stock_analyze/markets/a_share/strategy.py`
- `stock_analyze/market_data.py` → `stock_analyze/markets/a_share/market_data.py`
- `stock_analyze/portfolio_controls.py` → `stock_analyze/markets/a_share/portfolio_controls.py`
- `stock_analyze/diagnostics.py` → `stock_analyze/markets/a_share/diagnostics.py`
- `stock_analyze/data_provider/` → `stock_analyze/markets/a_share/data_provider/`
- `stock_analyze/alt_factors/` → `stock_analyze/markets/a_share/alt_factors/`
- `stock_analyze/backtest/` → `stock_analyze/markets/a_share/backtest/`

**Modified files** (top-level, gain `market` parameter):
- `stock_analyze/competition.py` — add `MARKETS`, `get_market_module`, `resolve_market_paths`
- `stock_analyze/overlay_guard.py` — `AVAILABLE_FACTORS_BY_MARKET` dict, `validate(market, ...)`
- `stock_analyze/evolution_writer.py` — `write_evolution(market, ...)`
- `stock_analyze/notifier.py` — `build_daily_summary(markets, ...)` already accepts a list; ensure path resolution honors `market`
- `stock_analyze/sanity_check.py` — `check_agent(market, ...)`
- `stock_analyze/agent_briefing.py` — `build_weekly_briefing(market, ...)`, `build_monthly_briefing(market, ...)`
- `stock_analyze/dashboard_aggregator.py` — `generate_competition_dashboard(market, ...)`
- `stock_analyze/monthly_review.py` — `compute_review(market, ...)`, `write_review(market, ...)`
- `stock_analyze/agent_rollback.py` — `rollback(market, ...)`
- `stock_analyze/cli.py` — add `--market` arg; route subcommands via `competition.get_market_module`

**Data + config + systemd migrations** (one-shot):
- Move `data/{claude,codex}/` → `data/a_share/{claude,codex}/`
- Move `reports/{claude,codex}/` → `reports/a_share/{claude,codex}/`
- Rename `configs/competition.yaml` → `configs/competition_a_share.yaml`
- Rename `configs/agents/{claude,codex}.yaml` → `configs/agents/{claude,codex}_a_share.yaml`
- Update `deploy/systemd/stock-analyze-*.service` ExecStart paths
- ECS rsync + `systemctl daemon-reload`

---

## Task 1: Bootstrap `markets/` package

**Files:**
- Create: `stock_analyze/markets/__init__.py`
- Create: `stock_analyze/markets/a_share/__init__.py`
- Test: `tests/test_markets_package_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_markets_package_bootstrap.py
import unittest


class MarketsPackageBootstrapTests(unittest.TestCase):
    def test_markets_package_importable(self):
        from stock_analyze import markets  # noqa: F401

    def test_a_share_subpackage_importable(self):
        from stock_analyze.markets import a_share  # noqa: F401


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test, verify it fails**

```bash
python3.11 -m unittest tests.test_markets_package_bootstrap -v
```
Expected: `ModuleNotFoundError: No module named 'stock_analyze.markets'`

- [ ] **Step 3: Create the packages**

```bash
mkdir -p stock_analyze/markets/a_share
```

Create `stock_analyze/markets/__init__.py`:
```python
"""Per-market subpackages.

Each subpackage (a_share, hk, us) is self-contained: data_provider +
simulator + strategy + universe + mechanics. Shared logic
(factor_pipeline, overlay_guard, evolution_writer, etc.) stays at the
top level of stock_analyze/ and accepts a ``market`` parameter for
dispatch.

The list of supported markets lives in stock_analyze.competition.MARKETS.
"""
```

Create `stock_analyze/markets/a_share/__init__.py`:
```python
"""A-share market implementation.

Houses everything specific to the Shanghai + Shenzhen exchanges that
was previously at the top level of ``stock_analyze/``. Public surface
mirrors what HK and US subpackages must also expose:

    make_provider, execute_due_orders, update_nav,
    generate_rebalance_orders, initialize, build_signals.

Per-market mechanics constants live in a ``mechanics.py`` companion
module (to be added in a follow-up task when the modules are moved
into this package).
"""
```

- [ ] **Step 4: Run test, verify it passes**

```bash
python3.11 -m unittest tests.test_markets_package_bootstrap -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/markets/ tests/test_markets_package_bootstrap.py
git commit -m "markets: bootstrap empty markets/{a_share} package skeleton

Phase 1 of multi-market refactor. Empty placeholder packages — files
move in subsequent tasks."
```

---

## Task 2: Move `simulator.py` to `markets/a_share/`

**Files:**
- Move: `stock_analyze/simulator.py` → `stock_analyze/markets/a_share/simulator.py`
- Modify: ~15 import sites in tests + cli.py + other modules

- [ ] **Step 1: Locate all import sites of `stock_analyze.simulator`**

```bash
grep -rEn "from stock_analyze\.simulator|from \.simulator|stock_analyze\.simulator" --include="*.py" . | grep -v __pycache__
```
Expected: list of test files (~10) + `cli.py` + `backtest/engine.py`.

- [ ] **Step 2: `git mv` simulator.py**

```bash
git mv stock_analyze/simulator.py stock_analyze/markets/a_share/simulator.py
```

- [ ] **Step 3: Fix internal relative imports inside the moved file**

In `stock_analyze/markets/a_share/simulator.py`, change:
- `from .data_provider import ...` → keep as-is (data_provider still lives at top level until Task 5; this is a temporary inconsistency until Task 5 lands)
- `from .portfolio_controls import ...` → keep as-is
- `from .factor_pipeline import ...` → change to `from ...factor_pipeline import ...` (3 dots = top level)
- `from .store import ...` → `from ...store import ...`
- `from .config import ...` → `from ...config import ...`
- `from .utils import ...` → `from ...utils import ...`
- `from .run_ledger import ...` → `from ...run_ledger import ...`

Use this sed pattern as a reference, but verify each replacement manually:

```bash
# Identify the current top-level imports inside the moved simulator:
grep -n "^from \." stock_analyze/markets/a_share/simulator.py
```

For each `from .<name>` that points to a module still at the top level (factor_pipeline, store, config, utils, run_ledger, overlay_guard, sanity_check, notifier, reporting, performance), rewrite to `from ...<name>`.

For each `from .<name>` that points to a module *also* moving to markets/a_share/ (data_provider, portfolio_controls, strategy, diagnostics, market_data, alt_factors, backtest), leave as `from .<name>` — they'll be in the same package soon.

- [ ] **Step 4: Update all caller import paths**

Inside the repo, update every `from stock_analyze.simulator import X` to `from stock_analyze.markets.a_share.simulator import X`. Caller files:

```bash
grep -rl "from stock_analyze\.simulator" --include="*.py" .
```

For each file in the list, do an in-place edit replacing the import path. The list typically includes:
- `tests/test_simulation_correctness.py`
- `tests/test_simulator_clock_injection.py`
- `tests/test_sizing_tier2.py`
- `tests/test_strategy_filters.py`
- `tests/test_strategy_cache_miss_resilience.py`
- `stock_analyze/cli.py`
- `stock_analyze/backtest/engine.py` (will move in Task 5; OK to update now)

Also update any `from .simulator import` inside the `stock_analyze/` top level (currently in `cli.py`):

```python
# Before:
from .simulator import execute_due_orders, generate_rebalance_orders, initialize, update_nav

# After:
from .markets.a_share.simulator import execute_due_orders, generate_rebalance_orders, initialize, update_nav
```

- [ ] **Step 5: Run full test suite, verify all tests still pass**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```
Expected: `Ran 36X tests` followed by `OK` (no failures).

If any test fails, the failure is an import path bug — fix the import path, do not change behavior.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "markets: relocate simulator.py to markets/a_share/

Phase 1, Task 2. Mechanical move via git mv; updated all import sites
(tests + cli.py + backtest.engine). No behavior change.

Internal imports inside the moved file: top-level shared modules use
'from ...X', sibling-in-package modules use 'from .X'."
```

---

## Task 3: Move `strategy.py`, `market_data.py`, `portfolio_controls.py`, `diagnostics.py`

**Files:**
- Move: `stock_analyze/{strategy,market_data,portfolio_controls,diagnostics}.py` → `stock_analyze/markets/a_share/{name}.py`

These 4 modules move together because they share the same caller-update pattern as simulator.py.

- [ ] **Step 1: For each module, identify import sites**

```bash
for mod in strategy market_data portfolio_controls diagnostics; do
  echo "=== $mod ==="
  grep -rEn "from stock_analyze\.$mod|from \.$mod\b" --include="*.py" . | grep -v __pycache__
done
```

- [ ] **Step 2: `git mv` all 4 files**

```bash
git mv stock_analyze/strategy.py stock_analyze/markets/a_share/strategy.py
git mv stock_analyze/market_data.py stock_analyze/markets/a_share/market_data.py
git mv stock_analyze/portfolio_controls.py stock_analyze/markets/a_share/portfolio_controls.py
git mv stock_analyze/diagnostics.py stock_analyze/markets/a_share/diagnostics.py
```

- [ ] **Step 3: Fix internal relative imports in moved files**

For each moved file, audit `from .X` lines and change to:
- `from ...X` when X is a top-level shared module (factor_pipeline, store, config, utils, etc.)
- `from .X` when X is now also in `markets/a_share/` (simulator, strategy, portfolio_controls, diagnostics, market_data, data_provider, alt_factors, backtest)

Use:
```bash
for f in stock_analyze/markets/a_share/{strategy,market_data,portfolio_controls,diagnostics}.py; do
  echo "=== $f ==="
  grep -n "^from \." "$f"
done
```
Edit each line manually to add the extra dot where needed.

- [ ] **Step 4: Update caller imports across the repo**

Run:
```bash
for mod in strategy market_data portfolio_controls diagnostics; do
  grep -rl "from stock_analyze\.$mod" --include="*.py" . | while read f; do
    sed -i.bak "s|from stock_analyze\.$mod|from stock_analyze.markets.a_share.$mod|g" "$f"
    rm "${f}.bak"
  done
done
```

Verify no leftover top-level references:
```bash
grep -rE "from stock_analyze\.(strategy|market_data|portfolio_controls|diagnostics)\b" --include="*.py" .
```
Expected: no output.

- [ ] **Step 5: Run full test suite**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK|FAIL:|ERROR:)" | head -10
```
Expected: `OK` (no failures).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "markets: relocate strategy/market_data/portfolio_controls/diagnostics

Phase 1, Task 3. Mechanical batch move; all import sites updated."
```

---

## Task 4: Move `data_provider/`, `alt_factors/`, `backtest/` subpackages

**Files:**
- Move: `stock_analyze/{data_provider,alt_factors,backtest}/` → `stock_analyze/markets/a_share/{name}/`

- [ ] **Step 1: Identify import sites for each subpackage**

```bash
for pkg in data_provider alt_factors backtest; do
  echo "=== $pkg ==="
  grep -rEn "from stock_analyze\.$pkg|stock_analyze\.$pkg" --include="*.py" . | grep -v __pycache__ | head -20
done
```

- [ ] **Step 2: `git mv` the directories**

```bash
git mv stock_analyze/data_provider stock_analyze/markets/a_share/data_provider
git mv stock_analyze/alt_factors stock_analyze/markets/a_share/alt_factors
git mv stock_analyze/backtest stock_analyze/markets/a_share/backtest
```

- [ ] **Step 3: Fix relative imports inside the moved subpackages**

Each subpackage moved from depth-1 (`stock_analyze/<pkg>/`) to depth-3 (`stock_analyze/markets/a_share/<pkg>/`). Inside these files, top-level shared imports change from `from ..X` to `from ....X` (4 dots).

```bash
# Audit:
grep -nE "^from \.\." stock_analyze/markets/a_share/data_provider/*.py
grep -nE "^from \.\." stock_analyze/markets/a_share/alt_factors/*.py
grep -nE "^from \.\." stock_analyze/markets/a_share/backtest/*.py
```

For each `from ..X import ...` where X is a top-level shared module, change to `from ....X import ...`.

For each `from ..X import ...` where X is now a sibling under `markets/a_share/` (e.g. `..simulator`, `..strategy`, `..portfolio_controls`, `..data_provider` from inside backtest), change to `from ...X import ...` (3 dots — go up to markets/a_share/).

Concrete examples for `backtest/engine.py`:
```python
# Before (when backtest was at stock_analyze/backtest/):
from ..simulator import execute_due_orders
from ..strategy import build_signals

# After (now at stock_analyze/markets/a_share/backtest/):
from ..simulator import execute_due_orders   # 2 dots = up to markets/a_share/
from ..strategy import build_signals
```

And:
```python
# Before:
from ..factor_pipeline import process_factors

# After:
from ....factor_pipeline import process_factors  # 4 dots = up to stock_analyze/
```

- [ ] **Step 4: Update caller imports across the repo**

```bash
for pkg in data_provider alt_factors backtest; do
  grep -rl "from stock_analyze\.$pkg" --include="*.py" . | while read f; do
    sed -i.bak "s|from stock_analyze\.$pkg|from stock_analyze.markets.a_share.$pkg|g" "$f"
    rm "${f}.bak"
  done
done
```

Verify:
```bash
grep -rE "from stock_analyze\.(data_provider|alt_factors|backtest)\b" --include="*.py" .
```
Expected: no output.

- [ ] **Step 5: Run full test suite**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK|FAIL:|ERROR:)" | head -10
```
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "markets: relocate data_provider/alt_factors/backtest subpackages

Phase 1, Task 4. Three subpackages move from top level to
markets/a_share/. Internal imports rewritten (2 dots → 3 dots for
sibling-under-markets/a_share/; 2 dots → 4 dots for top-level shared).
All 360+ tests pass."
```

---

## Task 5: Wire `markets/a_share/__init__.py` public API

**Files:**
- Modify: `stock_analyze/markets/a_share/__init__.py`
- Test: `tests/test_markets_a_share_api.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_markets_a_share_api.py
import unittest


class AShareMarketAPITests(unittest.TestCase):
    def test_make_provider_exposed(self):
        from stock_analyze.markets import a_share
        self.assertTrue(callable(a_share.make_provider))

    def test_simulator_functions_exposed(self):
        from stock_analyze.markets import a_share
        for name in ("execute_due_orders", "update_nav",
                     "generate_rebalance_orders", "initialize"):
            self.assertTrue(callable(getattr(a_share, name)),
                            msg=f"a_share.{name} not exposed")

    def test_build_signals_exposed(self):
        from stock_analyze.markets import a_share
        self.assertTrue(callable(a_share.build_signals))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test, expect it to fail**

```bash
python3.11 -m unittest tests.test_markets_a_share_api -v
```
Expected: `AttributeError: module 'stock_analyze.markets.a_share' has no attribute 'make_provider'`.

- [ ] **Step 3: Re-export public API**

Edit `stock_analyze/markets/a_share/__init__.py`:

```python
"""A-share market implementation.

Public API exposed here is the contract every market subpackage must
honour (HK and US will mirror this in Phases 2-3).
"""

from .data_provider import make_provider
from .simulator import (
    execute_due_orders,
    generate_rebalance_orders,
    initialize,
    update_nav,
)
from .strategy import build_signals

__all__ = [
    "build_signals",
    "execute_due_orders",
    "generate_rebalance_orders",
    "initialize",
    "make_provider",
    "update_nav",
]
```

- [ ] **Step 4: Run the test, verify it passes**

```bash
python3.11 -m unittest tests.test_markets_a_share_api -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite re-run**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/markets/a_share/__init__.py tests/test_markets_a_share_api.py
git commit -m "markets: wire a_share public API re-exports

Phase 1, Task 5. markets.a_share now exports make_provider,
execute_due_orders, update_nav, generate_rebalance_orders, initialize,
build_signals — the interface every market subpackage will mirror."
```

---

## Task 6: Add `competition.MARKETS`, `get_market_module`, `resolve_market_paths`

**Files:**
- Modify: `stock_analyze/competition.py`
- Test: `tests/test_competition_market_dispatch.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_competition_market_dispatch.py
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import competition


class MarketDispatchTests(unittest.TestCase):
    def test_markets_constant_lists_supported_markets(self):
        self.assertIn("a_share", competition.MARKETS)
        # Phase 2/3 will add 'hk' and 'us'; v1 only has 'a_share'.

    def test_get_market_module_a_share(self):
        mod = competition.get_market_module("a_share")
        self.assertTrue(callable(mod.execute_due_orders))
        self.assertTrue(callable(mod.make_provider))

    def test_get_market_module_unknown_raises(self):
        with self.assertRaises(competition.UnknownMarket):
            competition.get_market_module("zz_top")

    def test_resolve_market_paths_a_share_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = competition.resolve_market_paths(
                "a_share", "claude", repo_root=root,
            )
            self.assertEqual(paths.data_dir, root / "data" / "a_share" / "claude")
            self.assertEqual(paths.reports_dir, root / "reports" / "a_share" / "claude")
            self.assertEqual(
                paths.config_path,
                root / "configs" / "agents" / "claude_a_share.yaml",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

```bash
python3.11 -m unittest tests.test_competition_market_dispatch -v
```
Expected: `AttributeError: module 'stock_analyze.competition' has no attribute 'MARKETS'`.

- [ ] **Step 3: Add the dispatch surface**

Open `stock_analyze/competition.py` and append (or insert in a logical location):

```python
import importlib
from dataclasses import dataclass


MARKETS = ["a_share"]  # Phase 2/3 add 'hk' and 'us'


class UnknownMarket(ValueError):
    """Raised when a market id is not in :data:`MARKETS`."""

    def __init__(self, market: str) -> None:
        super().__init__(f"unknown market: {market!r}; expected one of {MARKETS}")
        self.market = market


def get_market_module(market: str):
    """Import and return ``stock_analyze.markets.<market>``.

    The returned module is the market's public API (make_provider,
    execute_due_orders, update_nav, generate_rebalance_orders,
    initialize, build_signals) re-exported from its ``__init__.py``.
    Subsequent calls hit Python's import cache.
    """
    if market not in MARKETS:
        raise UnknownMarket(market)
    return importlib.import_module(f"stock_analyze.markets.{market}")


@dataclass
class MarketAgentPaths:
    """Resolved on-disk paths for a (market, agent) pair."""

    market: str
    agent_id: str
    repo_root: Path
    data_dir: Path
    reports_dir: Path
    config_path: Path


def resolve_market_paths(
    market: str,
    agent_id: str,
    repo_root: Path | str | None = None,
) -> MarketAgentPaths:
    """Compute the canonical paths for a (market, agent) pair.

    Convention:
      data/<market>/<agent>/
      reports/<market>/<agent>/
      configs/agents/<agent>_<market>.yaml

    The market suffix on the overlay filename keeps all overlays in one
    flat ``configs/agents/`` directory (no nested subdirs) while still
    being unambiguous about which market each overlay targets.
    """
    if market not in MARKETS:
        raise UnknownMarket(market)
    root = Path(repo_root) if repo_root else Path.cwd()
    return MarketAgentPaths(
        market=market,
        agent_id=agent_id,
        repo_root=root,
        data_dir=root / "data" / market / agent_id,
        reports_dir=root / "reports" / market / agent_id,
        config_path=root / "configs" / "agents" / f"{agent_id}_{market}.yaml",
    )
```

If `dataclass` and `Path` are not yet imported at the top of `competition.py`, add:
```python
from dataclasses import dataclass
from pathlib import Path
```

- [ ] **Step 4: Run test, verify it passes**

```bash
python3.11 -m unittest tests.test_competition_market_dispatch -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Full suite**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/competition.py tests/test_competition_market_dispatch.py
git commit -m "competition: market dispatch (MARKETS, get_market_module, resolve_market_paths)

Phase 1, Task 6. Adds the three primitives every shared module will
use to look up per-market implementation + path resolution. Phase 2/3
extend MARKETS to ['a_share','hk','us']."
```

---

## Task 7: Overlay guard market-aware (`AVAILABLE_FACTORS_BY_MARKET`)

**Files:**
- Modify: `stock_analyze/overlay_guard.py`
- Test: `tests/test_overlay_guard_market.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/test_overlay_guard_market.py
import unittest

from stock_analyze.overlay_guard import (
    AVAILABLE_FACTORS_BY_MARKET,
    validate,
)


class OverlayGuardMarketTests(unittest.TestCase):
    def test_a_share_factor_set_includes_classic_factors(self):
        factors = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        for name in ("pe", "pb", "roe", "momentum_20", "momentum_60",
                     "low_volatility_60", "dividend_yield"):
            self.assertIn(name, factors, msg=f"a_share missing factor {name}")

    def test_a_share_includes_claude_sentiment(self):
        factors = AVAILABLE_FACTORS_BY_MARKET["a_share"]
        self.assertIn("claude_market_sentiment_1w", factors)

    def test_validate_accepts_market_kwarg_default_a_share(self):
        # Backwards-compat: omitting market should behave like market='a_share'
        valid_overlay = {
            "agent_id": "claude",
            "strategy_id": "test",
            "name": "Test",
            "factors": {"pe": 1.0},
            "factor_processing": {},
            "portfolio_controls": {},
            "filters": {},
        }
        # Should not raise:
        validate("claude", valid_overlay)
        validate("claude", valid_overlay, market="a_share")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

Expected: `ImportError: cannot import name 'AVAILABLE_FACTORS_BY_MARKET' from 'stock_analyze.overlay_guard'`.

- [ ] **Step 3: Add the market-aware dispatch**

In `stock_analyze/overlay_guard.py`:

a) Locate the existing `AVAILABLE_FACTORS` set (the current global list of A-share factor names).

b) Replace its definition with:

```python
# Per-market factor whitelists. The 'a_share' set is the union of
# A-share's classic factors + the sentiment alt-factor introduced by
# OpenSpec change ``add-llm-sentiment-alpha-factor``.
AVAILABLE_FACTORS_BY_MARKET: dict[str, set[str]] = {
    "a_share": {
        # Classic per-stock factors
        "pe", "pb", "roe", "gross_margin", "debt_ratio",
        "net_profit_growth", "momentum_20", "momentum_60",
        "low_volatility_60", "dividend_yield",
        # Broadcast alt-factors (one per agent — overlay_guard's
        # cross-agent rule still rejects mismatched prefixes).
        "claude_market_sentiment_1w",
        "codex_market_sentiment_1w",
    },
    # Phase 2/3 add 'hk' and 'us' here.
}

# Backwards-compat alias for code paths that still reference the old
# flat ``AVAILABLE_FACTORS`` name. New code uses
# ``AVAILABLE_FACTORS_BY_MARKET[market]``.
AVAILABLE_FACTORS = AVAILABLE_FACTORS_BY_MARKET["a_share"]
```

c) Modify the `validate()` function signature to accept a `market` parameter (default `"a_share"`):

```python
def validate(
    agent_id: str,
    overlay: dict,
    *,
    market: str = "a_share",
    repo_root: Path | str | None = None,
) -> None:
    """Validate an overlay against the schema + per-market factor whitelist.

    Raises OverlayGuardError or its subclasses on rejection.
    """
    factors_whitelist = AVAILABLE_FACTORS_BY_MARKET.get(market)
    if factors_whitelist is None:
        raise OverlayGuardError(
            f"unknown market {market!r}; expected one of "
            f"{sorted(AVAILABLE_FACTORS_BY_MARKET)}"
        )
    # ... rest of existing validate() body, but everywhere it
    # references AVAILABLE_FACTORS, use factors_whitelist instead.
```

If `validate` currently takes positional arguments in a different order, preserve back-compat by accepting both forms: the existing call sites in `evolution_writer.py` and `cli.py` must continue to work without changes (they'll be migrated to pass `market=` in subsequent tasks).

- [ ] **Step 4: Run test, expect pass**

```bash
python3.11 -m unittest tests.test_overlay_guard_market -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite + verify existing overlay_guard tests still pass**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```
Expected: `OK` (the old `AVAILABLE_FACTORS` alias keeps existing tests green).

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/overlay_guard.py tests/test_overlay_guard_market.py
git commit -m "overlay_guard: per-market factor whitelist (a_share for now)

Phase 1, Task 7. AVAILABLE_FACTORS_BY_MARKET dict + market kwarg on
validate(). Backwards-compat alias AVAILABLE_FACTORS = ...['a_share']
keeps existing callers green. Phase 2/3 add 'hk' and 'us' entries."
```

---

## Task 8: Add `market` parameter to `evolution_writer`, `sanity_check`, `agent_briefing`, `monthly_review`, `agent_rollback`, `dashboard_aggregator`

**Files:**
- Modify: each of the 6 modules above.
- Test: `tests/test_shared_modules_market_param.py` (new)

This task batches 6 small additions because each is the same pattern: add a `market: str = "a_share"` kwarg, pass it through to whichever helper internally resolves paths or factor lists. Keep each module's existing behavior identical when `market="a_share"`.

- [ ] **Step 1: Write failing test for the 6 signatures**

```python
# tests/test_shared_modules_market_param.py
import inspect
import unittest


class SharedModulesAcceptMarketParamTests(unittest.TestCase):
    def _assert_has_market_kwarg(self, func, default="a_share"):
        sig = inspect.signature(func)
        self.assertIn("market", sig.parameters,
                       msg=f"{func.__qualname__} missing 'market' parameter")
        self.assertEqual(
            sig.parameters["market"].default, default,
            msg=f"{func.__qualname__} 'market' default != {default!r}",
        )

    def test_evolution_writer_write_evolution(self):
        from stock_analyze.evolution_writer import write_evolution
        self._assert_has_market_kwarg(write_evolution)

    def test_sanity_check_check_agent(self):
        from stock_analyze.sanity_check import check_agent
        self._assert_has_market_kwarg(check_agent)

    def test_agent_briefing_build_weekly_briefing(self):
        from stock_analyze.agent_briefing import build_weekly_briefing
        self._assert_has_market_kwarg(build_weekly_briefing)

    def test_agent_briefing_build_monthly_briefing(self):
        from stock_analyze.agent_briefing import build_monthly_briefing
        self._assert_has_market_kwarg(build_monthly_briefing)

    def test_monthly_review_compute_review(self):
        from stock_analyze.monthly_review import compute_review
        self._assert_has_market_kwarg(compute_review)

    def test_agent_rollback_rollback(self):
        from stock_analyze.agent_rollback import rollback
        self._assert_has_market_kwarg(rollback)

    def test_dashboard_aggregator_generate_competition_dashboard(self):
        from stock_analyze.dashboard_aggregator import (
            generate_competition_dashboard,
        )
        self._assert_has_market_kwarg(generate_competition_dashboard)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect 7 failures**

```bash
python3.11 -m unittest tests.test_shared_modules_market_param -v
```
Expected: 7 failures (all complaining about missing 'market' parameter).

- [ ] **Step 3: Add `market` kwarg to each of the 6 modules' public functions**

For each module, edit the public function's signature to add `market: str = "a_share"` as a keyword-only argument (placed after `*` separator if not already present). Inside the function body, when resolving paths or fetching factor whitelists, use `competition.resolve_market_paths(market, agent_id, repo_root)` (introduced in Task 6) instead of hardcoded paths.

**evolution_writer.write_evolution** — example pattern:

```python
def write_evolution(
    agent_id: str,
    new_overlay: dict,
    *,
    market: str = "a_share",
    repo_root: Path | str | None = None,
    ...
) -> None:
    paths = competition.resolve_market_paths(market, agent_id, repo_root=repo_root)
    # use paths.data_dir, paths.config_path, paths.reports_dir
    # instead of the previous data/<agent_id>/, configs/agents/<agent_id>.yaml
    overlay_guard.validate(agent_id, new_overlay, market=market, repo_root=repo_root)
    # ... rest of function
```

Apply the same pattern to each of the other 5 modules' public functions. For functions that compute monthly_review or build briefings, the only behavior change is: path lookups for data/<agent>/ become data/<market>/<agent>/ via `resolve_market_paths`.

**Important:** Until Task 13 (data migration) lands, the resolved paths point at locations that don't yet exist on disk. That's OK — these are the *target* paths. Tests run against TemporaryDirectory roots so the migration timing doesn't affect them.

- [ ] **Step 4: Re-run test, expect pass**

```bash
python3.11 -m unittest tests.test_shared_modules_market_param -v
```
Expected: PASS (7 tests).

- [ ] **Step 5: Full suite — existing tests should still pass because default `market="a_share"` preserves old path resolution after Task 13's migration runs**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
```
Expected: `OK`. If existing tests fail because they use the old `data/<agent>/` paths, defer them to Task 13's migration commit.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "shared: add market kwarg to 6 path-aware modules

Phase 1, Task 8. evolution_writer, sanity_check, agent_briefing (×2),
monthly_review, agent_rollback, dashboard_aggregator all gain
``market: str = 'a_share'`` and route path resolution through
``competition.resolve_market_paths``. Behavior unchanged when
market='a_share' (the default), which matches Phase 1 scope."
```

---

## Task 9: CLI `--market` flag

**Files:**
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli_market_flag.py` (new)

- [ ] **Step 1: Write failing test**

```python
# tests/test_cli_market_flag.py
import unittest
from unittest.mock import patch

from stock_analyze.cli import build_parser


class CLIMarketFlagTests(unittest.TestCase):
    def test_parser_accepts_market_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--market", "a_share", "--agent", "claude", "init"]
        )
        self.assertEqual(args.market, "a_share")

    def test_market_defaults_to_a_share_when_absent(self):
        parser = build_parser()
        args = parser.parse_args(["--agent", "claude", "init"])
        self.assertEqual(args.market, "a_share")

    def test_market_rejects_unknown(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--market", "moon", "init"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

```bash
python3.11 -m unittest tests.test_cli_market_flag -v
```
Expected: `AttributeError: 'Namespace' object has no attribute 'market'`.

- [ ] **Step 3: Add the `--market` flag**

In `stock_analyze/cli.py`'s `build_parser()`, after the existing top-level `--agent` flag, add:

```python
from .competition import MARKETS

parser.add_argument(
    "--market",
    choices=MARKETS,
    default="a_share",
    help="Market (a_share | hk | us). Default: a_share (back-compat).",
)
```

Then in `main()`, after parsing `args`, replace direct usage of moved-module functions with market-aware dispatch:

```python
# Before (illustrative — only the dispatch sites change):
from .markets.a_share.simulator import (
    execute_due_orders, generate_rebalance_orders, initialize, update_nav,
)
# ...
execute_due_orders(...)

# After:
from . import competition
market_mod = competition.get_market_module(args.market)
market_mod.execute_due_orders(...)
market_mod.update_nav(...)
market_mod.generate_rebalance_orders(...)
market_mod.initialize(...)
market_mod.build_signals(...)
```

For subcommands that take `--agent`, also pass `args.market` to shared helpers (e.g. `evolution_writer.write_evolution(args.agent, ..., market=args.market)`).

- [ ] **Step 4: Test passes**

```bash
python3.11 -m unittest tests.test_cli_market_flag -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite + smoke test the CLI**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
python3.11 -m stock_analyze --agent claude --market a_share init --help 2>&1 | head -5
```
Expected: `OK` from unittest; help text rendered for the smoke check.

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/cli.py tests/test_cli_market_flag.py
git commit -m "cli: add --market flag with default=a_share

Phase 1, Task 9. All subcommands now route through
competition.get_market_module(args.market). Default 'a_share' preserves
backward compat — existing systemd ExecStart commands still work."
```

---

## Task 10: Migrate data + config + reports paths

**Files:**
- Create: `scripts/migrate-phase-1-paths.sh`
- Move: `data/{claude,codex}/` → `data/a_share/{claude,codex}/`
- Move: `reports/{claude,codex}/` → `reports/a_share/{claude,codex}/`
- Rename: `configs/competition.yaml` → `configs/competition_a_share.yaml`
- Rename: `configs/agents/{claude,codex}.yaml` → `configs/agents/{claude,codex}_a_share.yaml`

This task touches working data — be careful. Run **only when no daily run is in flight** (between 17:35 and the next 17:25 window the following weekday).

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate-phase-1-paths.sh`:

```bash
#!/usr/bin/env bash
# One-shot Phase 1 path migration for the A-share competition.
#
# Moves:
#   data/{claude,codex}/            → data/a_share/{claude,codex}/
#   reports/{claude,codex}/          → reports/a_share/{claude,codex}/
#   configs/competition.yaml         → configs/competition_a_share.yaml
#   configs/agents/{claude,codex}.yaml → configs/agents/{claude,codex}_a_share.yaml
#
# Idempotent: re-running after partial completion is safe (skips
# already-migrated entries).
#
# Usage:
#   bash scripts/migrate-phase-1-paths.sh [--repo-root PATH]
#                                          [--backup-suffix _pre_phase1]
#                                          [--dry-run]
set -euo pipefail

REPO_ROOT="$(pwd)"
BACKUP_SUFFIX="_pre_phase1"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root) REPO_ROOT="$2"; shift 2 ;;
    --backup-suffix) BACKUP_SUFFIX="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  DRY: $*"
  else
    echo "  $*"
    "$@"
  fi
}

echo "→ data/<agent>/ → data/a_share/<agent>/"
mkdir -p "data/a_share${BACKUP_SUFFIX}"
for agent in claude codex; do
  src="data/$agent"
  dst="data/a_share/$agent"
  if [[ -d "$src" && ! -d "$dst" ]]; then
    run mkdir -p "data/a_share"
    run git mv "$src" "$dst"
  else
    echo "  skip (src missing or dst exists): $src → $dst"
  fi
done

echo "→ reports/<agent>/ → reports/a_share/<agent>/"
for agent in claude codex; do
  src="reports/$agent"
  dst="reports/a_share/$agent"
  if [[ -d "$src" && ! -d "$dst" ]]; then
    run mkdir -p "reports/a_share"
    run git mv "$src" "$dst"
  else
    echo "  skip: $src → $dst"
  fi
done

echo "→ configs/competition.yaml → configs/competition_a_share.yaml"
if [[ -f "configs/competition.yaml" && ! -f "configs/competition_a_share.yaml" ]]; then
  run git mv "configs/competition.yaml" "configs/competition_a_share.yaml"
else
  echo "  skip"
fi

echo "→ configs/agents/<agent>.yaml → configs/agents/<agent>_a_share.yaml"
for agent in claude codex; do
  src="configs/agents/$agent.yaml"
  dst="configs/agents/${agent}_a_share.yaml"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    run git mv "$src" "$dst"
  else
    echo "  skip: $src → $dst"
  fi
done

echo "Done."
```

- [ ] **Step 2: Make it executable + dry-run**

```bash
chmod +x scripts/migrate-phase-1-paths.sh
./scripts/migrate-phase-1-paths.sh --dry-run
```
Expected: the script lists every action it would take, prefixed with `DRY:`. Verify the list matches what you expect.

- [ ] **Step 3: Run for real**

```bash
./scripts/migrate-phase-1-paths.sh
```
Expected: actions executed, no errors.

- [ ] **Step 4: Verify the new layout**

```bash
ls -la data/a_share/{claude,codex}/ 2>&1 | head -8
ls -la reports/a_share/{claude,codex}/ 2>&1 | head -8
ls configs/competition_a_share.yaml configs/agents/{claude,codex}_a_share.yaml
```
Expected: all paths exist; old `data/claude/`, `data/codex/`, `configs/competition.yaml`, `configs/agents/{claude,codex}.yaml` no longer present (they were `git mv`'d).

- [ ] **Step 5: Full test suite — existing tests now exercise the new paths**

```bash
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK|FAIL:|ERROR:)" | head -10
```
Expected: `OK`. If any test fails because it hardcoded the old path, update that test to use `competition.resolve_market_paths` or pass `repo_root=Path(tmp)` in a `TemporaryDirectory`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "migration: relocate A-share data + configs to <market>/<agent>/ layout

Phase 1, Task 10. Runs scripts/migrate-phase-1-paths.sh on the live
local repo. ECS deployment cuts over in Task 12 (systemd unit redeploy
+ rsync). The script is idempotent so it can also be safely run on
ECS after rsync."
```

---

## Task 11: Update systemd unit ExecStart paths

**Files:**
- Modify: every `deploy/systemd/stock-analyze-*.service` file

The CLI now accepts `--market`; default is `a_share` so the *behavior* of existing units is unchanged. But the renamed config files require updates to `--config` / `--agent` references inside each unit.

- [ ] **Step 1: Audit current ExecStart strings**

```bash
grep -nE "configs/(competition|agents/)" deploy/systemd/*.service
```
Each match needs to point at the new path (e.g. `configs/competition_a_share.yaml`, `configs/agents/claude_a_share.yaml`).

- [ ] **Step 2: Update each service file**

For every unit file, edit the ExecStart line so:
- `--config configs/competition.yaml` (if present) → `--config configs/competition_a_share.yaml`
- `--agent claude` stays the same (the `_a_share` suffix only lives in the overlay filename, not the agent_id passed via CLI; the CLI uses `args.market` to resolve to the suffixed file via `competition.resolve_market_paths`).

Optionally, add `--market a_share` explicitly to each ExecStart for clarity (it's already the default, but explicit is friendlier for grep).

Example (`stock-analyze-claude-daily.service`):
```ini
# Before:
ExecStart=/opt/stock-analyze/venv/bin/python -m stock_analyze.cli --logs-dir /opt/stock-analyze/logs --agent claude run-daily

# After:
ExecStart=/opt/stock-analyze/venv/bin/python -m stock_analyze.cli --logs-dir /opt/stock-analyze/logs --market a_share --agent claude run-daily
```

- [ ] **Step 3: scp the updated units to ECS + reload**

```bash
for svc in deploy/systemd/stock-analyze-*.service; do
  scp -i ~/.ssh/ai_baby_aliyun "$svc" \
    root@120.55.188.242:/etc/systemd/system/"$(basename "$svc")"
done

ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 "systemctl daemon-reload && systemctl list-timers 'stock-analyze-*' --no-pager | head -10"
```
Expected: rsync completes; `systemctl list-timers` shows the timers still active.

- [ ] **Step 4: Run an ECS-side sync of the migrated paths**

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 "cd /opt/stock-analyze/app && bash scripts/migrate-phase-1-paths.sh"
```
(After the local source has been rsync'd in the next task; ordering here assumes source-rsync precedes systemd reload. Adjust as needed when executing.)

- [ ] **Step 5: Commit**

```bash
git add deploy/systemd/
git commit -m "systemd: update ExecStart paths for Phase 1 a_share migration

Phase 1, Task 11. All units now pass --market a_share explicitly +
reference the renamed competition_a_share.yaml. Default behavior
unchanged; explicit flag makes the post-Phase-2/3 unit-grep easier."
```

---

## Task 12: ECS source sync + smoke test the full A-share pipeline

**Files:**
- (none new; pure deployment)

- [ ] **Step 1: rsync source to ECS**

```bash
cd "/Users/bytedance/Documents/New project"
rsync -avz --exclude '__pycache__' --exclude '*.pyc' \
  -e "ssh -i ~/.ssh/ai_baby_aliyun" \
  stock_analyze/ \
  root@120.55.188.242:/opt/stock-analyze/app/stock_analyze/

rsync -avz --exclude '__pycache__' \
  -e "ssh -i ~/.ssh/ai_baby_aliyun" \
  tests/ \
  root@120.55.188.242:/opt/stock-analyze/app/tests/

rsync -avz -e "ssh -i ~/.ssh/ai_baby_aliyun" \
  scripts/migrate-phase-1-paths.sh \
  root@120.55.188.242:/opt/stock-analyze/app/scripts/
```

- [ ] **Step 2: Clean stale `__pycache__`**

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "find /opt/stock-analyze/app/stock_analyze -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; echo cleaned"
```

- [ ] **Step 3: Run the migration on ECS**

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "cd /opt/stock-analyze/app && bash scripts/migrate-phase-1-paths.sh"
```
Expected: same output as local migration. (Idempotent — safe to retry.)

- [ ] **Step 4: ECS-side test suite**

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "cd /opt/stock-analyze/app && /opt/stock-analyze/venv/bin/python -m unittest discover -s tests 2>&1 | tail -5"
```
Expected: `Ran 36X tests in NN.NNs` followed by `OK`.

- [ ] **Step 5: ECS-side dry-run smoke of one daily cycle**

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "cd /opt/stock-analyze/app && /opt/stock-analyze/venv/bin/python -m stock_analyze --market a_share --agent claude sanity-check"
```
Expected: exit code 0 or 1 (warn-level findings OK; the daily summary structure should render without crashing).

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "cd /opt/stock-analyze/app && /opt/stock-analyze/venv/bin/python -m stock_analyze notify-daily-summary"
```
Expected: same DM body as before Phase 1, but path internally resolved through `data/a_share/<agent>/`.

- [ ] **Step 6: Reload systemd + verify**

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "systemctl daemon-reload && systemctl list-timers 'stock-analyze-*' --no-pager"
```
Expected: 3 timers still listed (market-data, weekly-trigger, monthly-review) with reasonable `NEXT` times.

- [ ] **Step 7: Wait for the next ECS daily run (17:25 CST) — observe its outcome**

After 17:35 the next weekday, check:
```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "journalctl --since '17:00' -u 'stock-analyze-*' --no-pager -o short 2>&1 | tail -25"
```
Expected: all 4 services (market-data, claude-daily, codex-daily, aggregate-dashboard) finish successfully; `data/a_share/claude/daily_nav.csv` mtime updates.

- [ ] **Step 8: Commit the Phase 1 closeout note**

```bash
git add -A  # any post-deployment doc updates
git commit -m "phase-1: A-share migration complete (deployed + verified)

ECS source synced, migration script executed, systemd units reloaded,
one full daily cycle (DATE 17:25 CST) ran cleanly. data/a_share/...
holds the live state; data/{claude,codex}/ no longer exist. Phase 2
(HK online) can begin." --allow-empty
```

---

## Self-Review Notes

After landing all 12 tasks, run:

```bash
cd "/Users/bytedance/Documents/New project"
python3.11 -m unittest discover -s tests 2>&1 | grep -E "^(Ran|FAILED|OK)"
git log --oneline -15
ls stock_analyze/markets/a_share/
ls data/a_share/{claude,codex}/
```

Expected:
- `Ran 36X tests` / `OK` — full suite still green
- 12 commits on the branch matching the task numbers
- `markets/a_share/` contains: `__init__.py`, `simulator.py`, `strategy.py`, `market_data.py`, `portfolio_controls.py`, `diagnostics.py`, plus the 3 subdirs (`data_provider/`, `alt_factors/`, `backtest/`)
- `data/a_share/claude/` and `data/a_share/codex/` contain `daily_nav.csv`, `positions.csv`, `state.json`, etc. moved from `data/<agent>/`

If anything diverges, that's the discrepancy to fix before declaring Phase 1 done.

---

## Out of Scope (for Phase 1)

These belong to Phase 2 / Phase 3 / v2:

- Adding `hk` or `us` to `competition.MARKETS`.
- Adding HK or US factor whitelists to `AVAILABLE_FACTORS_BY_MARKET`.
- Creating `stock_analyze/markets/{hk,us}/` packages.
- Modifying `notifier.build_daily_summary` to loop across multiple markets (still iterates over `["a_share"]` only).
- `cross-market-summary.timer` (Phase 3 wires the cross-market DM).
- Backtest engine or sentiment factor work for non-A-share markets.

If during Phase 1 implementation you encounter an item from this list, defer it explicitly and note in the commit message.
