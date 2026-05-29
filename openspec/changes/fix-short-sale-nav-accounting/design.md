# Design

## Goal

Make short-position mark-to-market NAV correct in the HK and US simulators by representing the short-sale **proceeds** as an asset, so opening a short at fair value does not change equity (net of fees) and the daily NAV reflects true unrealized P/L throughout the short's life.

## Non-goals

- No margin engine, no borrow-availability check, no overnight borrow fee. Collateral stays a flat `SHORTING_COLLATERAL_RATIO` of notional.
- No change to the long buy/sell paths, the T+2 (HK) / T+1 (US) settlement queue, lot sizing, or slippage.
- No change to A-share, which has no short/cover path.
- We do NOT enable shorting on top of a long; that stays blocked.

## The accounting model

A short sale is: borrow shares, sell them now (receive cash = proceeds), buy them back later (pay buyback cost), return them. Equity at any instant is:

```
equity = cash + proceeds_held + (-liability_to_buy_back)
```

The liability to buy back is `|shares| * current_price`. The proceeds you received were `|shares| * open_price`. So unrealized P/L = proceeds − liability = `|shares| * (open_price − current_price)` — exactly the short's economic P/L.

### State buckets

The simulator already keeps three buckets in `state.json` per account:

- `cash` — settled, spendable cash.
- `cash_collateral` — cash set aside / frozen for open shorts.
- `positions[code].shares` — signed share count; negative = short.

And `update_nav` already computes:

```
total = cash + cash_collateral + Σ position_market_value
where  position_market_value =  shares * px        if shares > 0  (long)
                             = -|shares| * px       if shares < 0  (short)
```

The NAV formula is **already correct given correct bucket contents**. The only bug is what goes into `cash` vs `cash_collateral` at short-open. We therefore do NOT change `update_nav`; we change only the `short` and `cover` branches.

### Model A (the fix)

> **Model A: route the short-sale proceeds INTO `cash_collateral`.**

Rationale: the proceeds and the collateral are both "money tied up by the open short." Putting the full proceeds into `cash_collateral` (rather than debiting `cash` for a separately-computed collateral amount) means the liability in `positions_value` nets against the proceeds in `cash_collateral`, and `cash` only ever moves by the fees. This is the minimal change that makes the existing NAV formula correct.

With `SHORTING_COLLATERAL_RATIO == 1.0` (the current value), the proceeds equal the collateral, so "deposit proceeds" and "freeze 100% collateral" coincide. The design below is written so it stays correct if the ratio is ever changed (see Edge case E5).

**Short-open** (`gross = shares * px`, `fees = stamp + commission`):

```
cash            -= fees
cash_collateral += gross
position.shares  = prior_shares - shares      # more negative
position.short_collateral += gross            # track proceeds held for THIS position
position.avg_cost = blended open price
```

(For HK, `fees = gross*STAMP_TAX_RATE + gross*COMMISSION_RATE`; for US, `fees = 0`.)

**Cover** (buy back `n` shares at `px_cover`, `prior_shares` negative):

```
released = position.short_collateral * (n / |prior_shares|)   # proportional release
buyback  = n * px_cover
fees     = buyback*STAMP_TAX_RATE + buyback*COMMISSION_RATE    # 0 for US
cash            += released - buyback - fees
cash_collateral -= released
position.shares += n                                           # less negative
position.short_collateral -= released
# if shares == 0: delete position
```

Note `released - buyback` already embeds the P/L: with 100% ratio, `released = n * open_price`, so `released - buyback = n * (open_price - px_cover)` — profit when you cover below the open price.

## Worked numerical example

Start equity `C = 100000`. HK fee rates: stamp `0.13%`, commission `0.03%` → combined `0.16%` of notional. (US example follows with zero fees.)

### HK: short open 100 @ $100

```
gross = 100 * 100      = 10000
fees  = 10000 * 0.0016 = 16        (stamp 13 + commission 3)
cash            = 100000 - 16      = 99984
cash_collateral = 0 + 10000        = 10000
position        = {shares: -100, avg_cost: 100, short_collateral: 10000}
```

NAV via `update_nav` at px = 100 (no move):

```
positions_value = -|−100| * 100 = -10000
total = 99984 + 10000 + (-10000) = 99984  =  C - fees  ✓
```

Under the **old buggy code** this same open produced `cash = 89984`, `cash_collateral = 10000`, `positions_value = -10000` → `total = 89984` ❌ (lost a full 10000 notional on a fair-value open).

### HK: mark-to-market while open

Price rises to $120 (adverse — we shorted, price went up):

```
positions_value = -100 * 120 = -12000
total = 99984 + 10000 + (-12000) = 97984
```

Unrealized P/L vs the C−fees baseline = `97984 - 99984 = -2000` = `100 * (100 - 120)` ✓ (lost $2000 on the adverse move). This is the number a correct NAV must show; the old code would have shown `87984`.

Price falls to $80 (favorable):

```
positions_value = -100 * 80 = -8000
total = 99984 + 10000 + (-8000) = 101984
```

Unrealized P/L = `+2000` = `100 * (100 - 80)` ✓.

### HK: cover 100 @ $80 (profit)

```
released = 10000 * (100/100) = 10000
buyback  = 100 * 80          = 8000
fees     = 8000 * 0.0016     = 12.8     (stamp 10.4 + commission 2.4)
cash            = 99984 + (10000 - 8000 - 12.8) = 99984 + 1987.2 = 101971.2
cash_collateral = 10000 - 10000 = 0
position        = deleted (shares 0)
```

NAV after cover = `101971.2 + 0 + 0 = 101971.2`. Round-trip P/L = `101971.2 - 100000 = +1971.2` = `+2000 profit − 16 open-fees − 12.8 cover-fees` ✓.

Sanity check against the mark-to-market just before cover (px=80 → NAV 101984): NAV drops by exactly the cover fees `12.8` (101984 − 12.8 = 101971.2) ✓ — covering at the prevailing price only costs the transaction fee, no jump.

### US: short open 100 @ $200, zero fees

```
gross = 100 * 200 = 20000 ; fees = 0
cash            = 50000 - 0     = 50000      (unchanged!)
cash_collateral = 0 + 20000     = 20000
position        = {shares: -100, avg_cost: 200, short_collateral: 20000}
NAV = 50000 + 20000 - 20000 = 50000 = C  ✓  (zero fees → equity exactly unchanged)
```

Old buggy US code: `cash = 30000`, NAV = `30000 + 20000 - 20000 = 30000` ❌.

US cover 100 @ $180 (profit), zero fees:

```
released = 20000 ; buyback = 18000 ; fees = 0
cash            = 50000 + (20000 - 18000 - 0) = 52000
cash_collateral = 0
NAV = 52000 = C + 2000  ✓   (profit 100*(200-180) = 2000)
```

## The NAV invariant (the contract)

At every step of a short's life:

```
total = cash + cash_collateral + positions_value
```

with, for a short position, `positions_value = -|shares| * current_px` and `cash_collateral` holding the proceeds. Equivalently:

```
total = cash + Σ_long(shares*px) + Σ_short(short_collateral_i - |shares_i|*px_i)
```

and `short_collateral_i - |shares_i|*px_i = |shares_i|*(open_px_i - px_i)` = unrealized short P/L. So `total = settled_cash + long_market_value + unrealized_short_pnl_relative_to_proceeds`, which is the textbook definition of equity. Opening a short at `px == open_px` adds `+gross` to collateral and `-gross` to positions_value → zero net change to NAV (minus fees). This is the property tests must lock down.

## Edge cases

### E1 — Partial cover

Cover `n < |prior_shares|`. Release collateral **proportionally**: `released = short_collateral * (n / |prior_shares|)`. The remaining position keeps `short_collateral -= released` and `shares += n`. The remaining short continues to mark to market against its unchanged `avg_cost`. Worked: open 100 @ $100 (collateral 10000), cover 40 @ $90 → released `10000*40/100 = 4000`, buyback `3600`, cash += `4000-3600-fees`; remaining position `{shares: -60, short_collateral: 6000, avg_cost: 100}`; NAV invariant holds for the residual 60-share short.

### E2 — Multiple independent shorts (different codes)

Each position carries its own `short_collateral`. `cash_collateral` is the sum across positions; `update_nav` sums `positions_value` across positions. The invariant holds per-position and therefore in aggregate. No cross-position netting is needed.

### E3 — Adding to an existing short (same code, two short orders)

`avg_cost` is re-blended: `new_cost_basis = |prior_shares|*old_avg_cost + gross_new` over `|new_shares|`. `short_collateral` accumulates `+= gross_new`. `cash_collateral` accumulates `+= gross_new`. The invariant holds because each tranche deposited its own proceeds.

### E4 — Short on top of a long (currently BLOCKED)

The short branch blocks `prior_shares > 0` and returns `None` (no fill). This change KEEPS that block. Mixing a long and a short in the same code under one position dict would require splitting market value and collateral tracking, which is out of scope. Documented so reviewers know the `None`-return is intentional, not a regression. (Symmetric note: the buy branch does not block buying on top of a short — but no order generator emits that combination, and it is out of scope to harden here.)

### E5 — Collateral ratio ≠ 100%

If `SHORTING_COLLATERAL_RATIO` is ever set below 1.0, the proceeds still equal `gross` (you sold `gross` worth of stock and received that cash), but only `ratio*gross` need be *frozen*. Under Model A we deposit the **full proceeds** (`gross`) into `cash_collateral` regardless of ratio, because the proceeds are real cash you hold; the ratio would instead govern how much *additional* margin must be posted from `cash`. For the MVP (ratio == 1.0) this distinction is moot and the simple "deposit gross" rule is exactly right. If a sub-1.0 ratio is introduced later, revisit whether the surplus proceeds (`gross` above the frozen margin) should live in spendable `cash` instead of `cash_collateral`. This change does not introduce a sub-1.0 ratio.

### E6 — Price snapshot missing on a NAV day

`update_nav` falls back to `pos.avg_cost` when the provider returns no close. For a short, that yields `positions_value = -|shares|*avg_cost`, which nets against the deposited proceeds (`|shares|*avg_cost` for 100% ratio) to ~zero unrealized P/L — a reasonable "no information, assume flat" behavior. No change needed; called out so the fallback is understood.

## Test-rewrite requirement

These tests currently assert the **buggy** values and MUST be rewritten to the Model A numbers above:

| Test | File | Old (buggy) assertion | New (Model A) assertion |
|---|---|---|---|
| `test_short_freezes_collateral_and_creates_negative_position` | `tests/test_markets_hk_simulator.py` | `cash == 100000 - 10016` | `cash == 100000 - 16` (only fees leave cash); `cash_collateral == 10000`; `short_collateral == 10000` |
| `test_cover_releases_collateral_and_applies_pnl` | `tests/test_markets_hk_simulator.py` | open-state `cash == 89984` then cover → `101971.2` | open-state `cash == 99984`; cover @80 → `cash == 101971.2`, `cash_collateral == 0` (same final cash, different intermediate split) |
| `test_nav_short_position_reduces_equity` | `tests/test_markets_hk_simulator.py` | `total_value == 88000` (cash 90000, coll 10000, −12000) | with correct buckets (`cash == C−fees`, `cash_collateral == 10000`), open short marked at $120 → `total_value == 97984` |
| `test_short_freezes_collateral` | `tests/test_markets_us.py` | `cash == 30000` (collateral debited) | `cash == 50000` (zero fees, cash unchanged at open); `cash_collateral == 20000` |

New tests to ADD (one per simulator, mirrored):

- `test_short_open_at_fair_value_preserves_nav` — open a short at price `p`, immediately `update_nav` at the same `p`, assert `total_value == starting_equity - fees` (HK) / `== starting_equity` (US, zero fees).
- `test_short_round_trip_no_move_returns_nav_minus_fees` — open then cover at the same price, assert NAV returns to `starting_equity - open_fees - cover_fees` (HK) / `== starting_equity` (US).
- `test_short_mark_to_market_reflects_unrealized_pnl` — open at `p`, mark at `p ± Δ`, assert `total_value - (starting_equity - fees) == |shares| * (open - current)` for an adverse and a favorable move.
- (HK) `test_partial_cover_releases_proportional_collateral` — open 100, cover 40, assert released collateral `== short_collateral * 0.4` and residual position state.

The rewritten constants are derived from the worked examples in this design; reviewers should check them against this document, not against the prior test file (which encoded the bug).

## Sequencing relative to C2 (`extract-yfinance-provider-base`)

The HK and US short/cover/`update_nav` code is duplicated. Two valid sequencings:

1. **Preferred — after C2 lands:** the shared simulator scaffolding lives in one base module; this fix edits the short/cover accounting **once**, and both markets inherit it. Tests for both markets still run independently.
2. **Standalone — if C2 is not yet landed:** apply the identical Model A edit to both `hk/simulator.py` and `us/simulator.py`, and add a code comment in each `short`/`cover` branch noting the two copies MUST stay in lock-step until C2 dedupes them. The spec's "HK and US honor identical accounting" requirement is the guardrail that keeps them aligned.

Either way the spec and tests are written market-agnostic so the requirement survives the C2 refactor.
