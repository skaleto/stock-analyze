## Why

The HK and US paper-trading simulators mark short positions to market **incorrectly**. Opening a short at fair value instantly destroys roughly one notional of NAV, so every daily NAV snapshot taken while a short is open is wrong.

Concrete bug (see `stock_analyze/markets/hk/simulator.py:285-314`, the `order.side == "short"` branch; `stock_analyze/markets/us/simulator.py:193-216` is structurally identical):

```python
if order.side == "short":
    gross = order.shares * px              # short notional
    coll = gross * SHORTING_COLLATERAL_RATIO
    net_debit = coll + stamp + commission  # collateral leaves cash
    account_state["cash"] = cash - net_debit
    account_state["cash_collateral"] = collateral + coll
    # BUG: the short-sale PROCEEDS (`gross`) are never credited anywhere.
```

`update_nav` (HK `:385-434`, US `:267-306`) then computes:

```
total = cash + cash_collateral + positions_value
```

where a short position contributes `positions_value -= abs(shares) * px`.

Worked example — open a short of 100 shares @ $100, 100% collateral, ignore fees, starting equity `C`:

| Bucket | After short-open (buggy) |
|---|---|
| cash | `C - 10000` |
| cash_collateral | `+10000` |
| positions_value | `-10000` |
| **NAV** | **`C - 10000`** ❌ |

Opening a position at fair value must not change equity; correct NAV is `C`. The root cause is that the short-sale proceeds are a **real asset** (cash you received from selling borrowed shares) and the current code never represents that `+gross` anywhere. The collateral is debited from cash, the liability shows up in `positions_value`, but the offsetting proceeds are missing.

### Scoring-corruption risk

The competition is scored on **net-of-cost cumulative return** and **information ratio** against the benchmark (`configs/competition.yaml.objective`). During a short's entire holding period the daily NAV is understated by ~one notional. That corrupts every NAV-derived metric simultaneously:

| Metric | Effect |
|---|---|
| Cumulative return | Understated whenever a short is open; recovers only at cover |
| Max drawdown | Phantom drawdown of ~one notional at every short-open |
| Sharpe | Daily-return series gets a spurious down-spike at open and up-spike at cover |
| Information ratio | Rolling 3-month IR is polluted by the same artifacts |

So **any** agent that configures shorting in HK or US would have its competition scoring directly corrupted. Realized round-trip P/L is *correct* — NAV snaps back to the true value at cover — so the bug is invisible in trade-level accounting and only shows up in the daily mark-to-market between open and cover. That makes it easy to miss.

### Why it is latent (not yet firing)

The long-only `generate_rebalance_orders` in both simulators only ever emits `side: buy` / `side: sell` orders — it never emits `side: short`. A-share live trading never touches this code path at all. The bug therefore fires only when **both** conditions hold:

1. The HK or US market is actually deployed and running, AND
2. An agent's strategy emits `short` / `cover` orders (no current rebalance logic does).

It is a correctness landmine sitting under a feature that is wired up (the `short` / `cover` branches exist and are tested) but not yet driven by any order generator. Fixing it now — while there is no live short history to migrate — is cheap; fixing it after shorting goes live means reconstructing corrupted NAV series.

## What Changes

### Adopt "Model A": route short proceeds into the collateral bucket

Instead of debiting cash for the collateral, deposit the short-sale **proceeds** into `cash_collateral` and let the liability in `positions_value` net against them.

**On short-open** (per share notional `gross = shares * px`):

```
cash            -= fees            # only fees (stamp + commission) leave cash
cash_collateral += gross           # proceeds held as collateral
position.shares  = -shares         # liability shows up in positions_value as -gross
```

NAV at open `= (C - fees) + gross + (-gross) = C - fees` ✓ (only the transaction cost is lost, which is correct).

**On cover** (buy back `n` shares at `px_cover`):

```
released_collateral = short_collateral * (n / |open_short_shares|)
buyback_cost        = n * px_cover
cash               += released_collateral - buyback_cost - fees
cash_collateral    -= released_collateral
position.shares    += n             # less negative; deleted at zero
```

P/L flows through naturally: profit `(avg_cost - px_cover) * n` is the difference between the proceeds released and the buyback cost. The invariant `total = cash + cash_collateral + positions_value` stays correct at open, during the hold (mark-to-market reflects unrealized P/L), and at cover.

### Apply the fix in ONE shared place

The HK and US short branches are character-for-character identical except for fee constants (HK has stamp + commission, US has zero fees) and settlement lag (T+2 vs T+1). Fixing the bug in two files invites the two copies to drift.

This change SHOULD be done **together with, or immediately after,** the `extract-yfinance-provider-base` change (referred to here as C2), which extracts the shared yfinance/simulator scaffolding into a common base. If C2 has landed, the short/cover/`update_nav` accounting lives in one shared module and this fix lands once. If C2 has not landed, this change fixes both `hk/simulator.py` and `us/simulator.py` identically and leaves a note that the two copies must be kept in lock-step until C2 dedupes them.

### Rewrite the tests that currently assert the buggy behavior

The following tests encode the buggy values as "correct" and MUST be rewritten to assert Model A:

- `tests/test_markets_hk_simulator.py`:
  - `ShortOrderTests.test_short_freezes_collateral_and_creates_negative_position` — currently asserts `cash == 100000 - 10016` (collateral debited from cash). Under Model A, only fees leave cash.
  - `ShortOrderTests.test_cover_releases_collateral_and_applies_pnl` — cover arithmetic changes because the open-state cash/collateral split changes.
  - `UpdateNAVTests.test_nav_short_position_reduces_equity` — currently asserts `total_value == 88000` for an open short at a $20 adverse move; the constant changes because the collateral bucket now holds the proceeds (`+gross`) rather than the smaller `coll` amount.
- `tests/test_markets_us.py`:
  - `SimulatorShortTests.test_short_freezes_collateral` — currently asserts `cash == 30000` (collateral debited). Under Model A (zero US fees), cash is unchanged at open.

A NEW NAV-invariant test SHALL assert that opening a short at fair value leaves NAV unchanged net of fees, and that a full short→cover round-trip with no price move returns NAV to its pre-trade value minus total fees.

## Impact

### Affected specs

- **short-sale-accounting**: NEW capability — formalizes the NAV invariant, the mark-to-market rule, the cover/realization rule, and the HK/US equivalence requirement.

### Affected code

| File | Change |
|---|---|
| `stock_analyze/markets/hk/simulator.py` | `short` + `cover` branches reworked to Model A (~15 LoC delta). `update_nav` formula unchanged — it already nets `positions_value` against `cash_collateral`; only the bucket contents change. |
| `stock_analyze/markets/us/simulator.py` | Same change, zero-fee variant. (If C2 landed, this is the same shared module, fixed once.) |
| `tests/test_markets_hk_simulator.py` | Rewrite 3 tests (`ShortOrderTests` ×2, `UpdateNAVTests.test_nav_short_position_reduces_equity`) + add NAV-invariant test. |
| `tests/test_markets_us.py` | Rewrite `SimulatorShortTests.test_short_freezes_collateral` + add NAV-invariant test. |

### Risk

- **No live data migration.** Because the bug is latent (no order generator emits shorts and no market has a live short history), there is no corrupted NAV series to rebuild. The fix is purely forward-looking.
- **Test-value churn is intentional.** The rewritten test constants are the *point* of the change — the old constants encoded the bug. Reviewers should verify the new constants against the worked examples in `design.md`, not against the old test file.
- **A-share unaffected.** A-share's simulator does not have a short/cover path and is out of scope.

### Out of scope

- A real margin engine, borrow-availability checks, or overnight borrow fees. Collateral stays a simple ratio (`SHORTING_COLLATERAL_RATIO`).
- Shorting on top of an existing long. The current code blocks `prior_shares > 0` in the short branch; this change keeps that block and documents it as an edge case rather than enabling it.
- Wiring `short` / `cover` into any order generator. This change only fixes the accounting; it does not make any strategy start shorting.
