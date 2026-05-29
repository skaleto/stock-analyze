# Tasks

## 1. Lock the bug down with failing tests (TDD red)

- [ ] 1.1 Add `test_short_open_at_fair_value_preserves_nav` to `tests/test_markets_hk_simulator.py`: open a short at price `p`, call `update_nav` at the same `p`, assert `total_value == starting_equity - fees`. This test FAILS against current code (it returns `starting_equity - notional - fees`).
- [ ] 1.2 Add the mirrored `test_short_open_at_fair_value_preserves_nav` to `tests/test_markets_us.py` (zero fees → assert `total_value == starting_equity`). FAILS against current code.
- [ ] 1.3 Add `test_short_round_trip_no_move_returns_nav_minus_fees` to both test files: open then cover at the same price; assert NAV returns to `starting_equity - open_fees - cover_fees` (US: `== starting_equity`).
- [ ] 1.4 Add `test_short_mark_to_market_reflects_unrealized_pnl` to both test files: open at `p`, mark at `p+Δ` (adverse) and `p−Δ` (favorable); assert `total_value - (starting_equity - open_fees) == |shares|*(open - current)` for each.
- [ ] 1.5 Add `test_partial_cover_releases_proportional_collateral` to `tests/test_markets_hk_simulator.py`: open 100, cover 40; assert released collateral `== short_collateral * 0.4` and residual `{shares: -60, short_collateral: 6000}`.
- [ ] 1.6 Run `python3 -m unittest tests.test_markets_hk_simulator tests.test_markets_us` and CONFIRM the new tests fail (red) for the expected reason (NAV understated by ~one notional at open). Record the observed failure values.

## 2. Rewrite the tests that encode the buggy behavior

- [ ] 2.1 Rewrite `ShortOrderTests.test_short_freezes_collateral_and_creates_negative_position` (`tests/test_markets_hk_simulator.py`): change `cash == 100000 - 10016` to `cash == 100000 - 16`; keep `cash_collateral == 10000` and `short_collateral == 10000`. (Only fees leave cash under Model A.)
- [ ] 2.2 Rewrite `ShortOrderTests.test_cover_releases_collateral_and_applies_pnl` (`tests/test_markets_hk_simulator.py`): set the open-state seed to `cash == 99984` (was `89984`); cover @ $80 still ends at `cash == 101971.2`, `cash_collateral == 0`. Update the inline comment math to the Model A derivation.
- [ ] 2.3 Rewrite `UpdateNAVTests.test_nav_short_position_reduces_equity` (`tests/test_markets_hk_simulator.py`): seed `cash == 99984`, `cash_collateral == 10000`; open short marked at $120 → assert `total_value == 97984` (was `88000`).
- [ ] 2.4 Rewrite `SimulatorShortTests.test_short_freezes_collateral` (`tests/test_markets_us.py`): change `cash == 30000` to `cash == 50000` (zero fees, cash unchanged at open); keep `cash_collateral == 20000` and `shares == -100`.
- [ ] 2.5 Re-derive every rewritten constant from `design.md`'s worked examples (not from the old test file). Add a one-line comment in each rewritten test pointing at the design section.

## 3. Fix the simulator(s) (TDD green)

- [ ] 3.1 If `extract-yfinance-provider-base` (C2) has landed: edit the shared short/cover accounting in the common base module ONCE per Model A. Skip 3.2–3.3 (HK/US inherit).
- [ ] 3.2 Else (standalone): rewrite the `order.side == "short"` branch in `stock_analyze/markets/hk/simulator.py` to Model A — `cash -= fees`; `cash_collateral += gross`; `short_collateral += gross`. Remove the `coll`/`net_debit` collateral-from-cash logic. Keep the `prior_shares > 0` block.
- [ ] 3.3 Rewrite the `order.side == "cover"` branch in `stock_analyze/markets/hk/simulator.py` to Model A — `released = short_collateral * (n/|prior_shares|)`; `cash += released - buyback - fees`; `cash_collateral -= released`; `short_collateral -= released`.
- [ ] 3.4 Apply the identical Model A edit to the `short` and `cover` branches in `stock_analyze/markets/us/simulator.py` (zero-fee variant). Add a lock-step comment in both files referencing C2 if C2 has not landed.
- [ ] 3.5 Confirm `update_nav` in BOTH simulators is unchanged (the formula `total = cash + cash_collateral + positions_value` is already correct given correct buckets).
- [ ] 3.6 Run `python3 -m unittest tests.test_markets_hk_simulator tests.test_markets_us` and confirm ALL short/cover/NAV tests now pass (green), including the new TDD tests from §1 and the rewritten tests from §2.

## 4. Full-suite regression

- [ ] 4.1 Run the full suite: `python3 -m unittest discover -s tests`. Confirm green — no buy/sell/settlement/rebalance test regressed.
- [ ] 4.2 Spot-check that the buy and sell branches and the A-share simulator are untouched (diff review).

## 5. OpenSpec hygiene

- [ ] 5.1 Run `openspec validate fix-short-sale-nav-accounting` → must print "is valid".
- [ ] 5.2 On archive, fold `specs/short-sale-accounting/spec.md` into the canonical specs per the repo's archive flow.
