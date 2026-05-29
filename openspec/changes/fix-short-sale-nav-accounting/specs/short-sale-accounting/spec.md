## ADDED Requirements

### Requirement: Opening a short at fair value preserves NAV net of fees

When a simulator opens a short position, the short-sale proceeds (`gross = shares * execution_price`) SHALL be credited to the `cash_collateral` bucket, and `cash` SHALL be debited only by the transaction fees (`stamp_tax + commission`; zero for US). The position's `shares` SHALL become negative and its per-position `short_collateral` SHALL record the deposited proceeds. As a result, the account NAV computed by `update_nav` immediately after opening a short, at the same price it was opened, SHALL equal the pre-open NAV minus the open fees.

The simulator SHALL NOT debit `cash` for a separately-computed collateral amount in addition to (or instead of) crediting the proceeds — doing so understates NAV by approximately one notional for the duration of the short.

#### Scenario: HK short opened at fair value leaves equity unchanged except for fees

- **GIVEN** an HK account with `cash = 100000`, `cash_collateral = 0`, no positions
- **AND** stamp tax `0.13%` and commission `0.03%` (combined `0.16%`)
- **WHEN** a `short` order for 100 shares of `0700.HK` fills at `100.0`
- **THEN** `cash == 99984` (only the `16` of fees leaves cash)
- **AND** `cash_collateral == 10000` (the full short-sale proceeds)
- **AND** the position is `{shares: -100, avg_cost: 100.0, short_collateral: 10000}`
- **AND** calling `update_nav` at price `100.0` yields `total_value == 99984`, i.e. starting equity `100000` minus the `16` open fees

#### Scenario: US short opened at fair value leaves equity exactly unchanged

- **GIVEN** a US account with `cash = 50000`, `cash_collateral = 0`, no positions, and zero fees
- **WHEN** a `short` order for 100 shares of `TSLA` fills at `200.0`
- **THEN** `cash == 50000` (unchanged — no fees, no collateral debit)
- **AND** `cash_collateral == 20000` (the short-sale proceeds)
- **AND** calling `update_nav` at price `200.0` yields `total_value == 50000`, exactly the starting equity

### Requirement: Mark-to-market NAV of an open short reflects unrealized P/L

While a short position is open, `update_nav` SHALL value it as `positions_value -= abs(shares) * current_price`, netted against the proceeds held in `cash_collateral`, so that the account's `total_value` moves by exactly the short's unrealized profit or loss as the price moves. A price increase (adverse for a short) SHALL reduce NAV; a price decrease (favorable) SHALL increase NAV; the magnitude SHALL equal `abs(shares) * (open_price - current_price)`.

#### Scenario: Adverse price move reduces NAV by the unrealized loss

- **GIVEN** an HK account holding a short of 100 shares opened at `100.0`, with `cash = 99984` and `cash_collateral = 10000`
- **WHEN** `update_nav` runs at a current price of `120.0`
- **THEN** `total_value == 97984`
- **AND** the change from the post-open baseline (`99984`) is `-2000`, equal to `100 * (100 - 120)`

#### Scenario: Favorable price move increases NAV by the unrealized gain

- **GIVEN** the same account holding a short of 100 shares opened at `100.0`, with `cash = 99984` and `cash_collateral = 10000`
- **WHEN** `update_nav` runs at a current price of `80.0`
- **THEN** `total_value == 101984`
- **AND** the change from the post-open baseline (`99984`) is `+2000`, equal to `100 * (100 - 80)`

### Requirement: Covering a short releases collateral and realizes P/L

When a simulator covers (buys back) `n` shares of an open short, it SHALL release the proportional collateral `released = short_collateral * (n / abs(open_short_shares))`, pay the buyback cost `n * cover_price` plus cover fees from `cash`, and reduce `cash_collateral` by `released`. The net cash change SHALL be `released - (n * cover_price) - cover_fees`, which embeds the realized P/L. The position's `shares` SHALL move toward zero by `n` and its `short_collateral` SHALL be reduced by `released`; a fully covered position SHALL be deleted. A full round trip with no price move SHALL return NAV to the pre-trade value minus total (open + cover) fees.

#### Scenario: Cover below the open price realizes a profit (HK)

- **GIVEN** an HK account with `cash = 99984`, `cash_collateral = 10000`, holding `{shares: -100, avg_cost: 100.0, short_collateral: 10000}`
- **WHEN** a `cover` order for 100 shares fills at `80.0` (stamp + commission `0.16%` of the `8000` buyback = `12.8`)
- **THEN** `cash == 101971.2` (`99984 + 10000 - 8000 - 12.8`)
- **AND** `cash_collateral == 0`
- **AND** the position is deleted
- **AND** the round-trip realized P/L is `+1971.2` = `2000` gross profit minus `16` open fees minus `12.8` cover fees

#### Scenario: Partial cover releases collateral proportionally

- **GIVEN** an HK account holding `{shares: -100, avg_cost: 100.0, short_collateral: 10000}` with `cash_collateral = 10000`
- **WHEN** a `cover` order for 40 shares fills at `90.0`
- **THEN** the released collateral is `4000` (`10000 * 40/100`)
- **AND** `cash` increases by `4000 - 3600 - cover_fees`
- **AND** the residual position is `{shares: -60, short_collateral: 6000}` with `cash_collateral == 6000`

#### Scenario: Round trip with no price move returns NAV to start minus fees

- **GIVEN** a US account (zero fees) with starting equity `50000` and no positions
- **WHEN** a `short` of 100 shares fills at `200.0` and a `cover` of 100 shares fills at `200.0`
- **THEN** the final `total_value` equals `50000`, the original equity (zero fees, no price move)

### Requirement: HK and US simulators honor identical short accounting

Both `stock_analyze.markets.hk.simulator` and `stock_analyze.markets.us.simulator` SHALL implement the short-open, mark-to-market, and cover rules above identically, differing only in fee constants (HK applies `STAMP_TAX_RATE` + `COMMISSION_RATE`; US applies zero) and settlement lag (HK T+2, US T+1). The NAV invariant `total_value = cash + cash_collateral + positions_value` SHALL hold in both at open, throughout the hold, and at cover. Neither simulator SHALL debit `cash` for short collateral; both SHALL credit short-sale proceeds to `cash_collateral`. The short-on-top-of-an-existing-long case SHALL remain blocked (the `short` order does not fill when the prior position is long).

#### Scenario: Both simulators leave NAV unchanged net of fees on a fair-value short-open

- **GIVEN** identical accounts in the HK and US simulators, each with starting equity `E` and no positions
- **WHEN** each opens a short of the same share count at the same fair-value price `p`
- **THEN** the HK account's post-open NAV equals `E - hk_open_fees`
- **AND** the US account's post-open NAV equals `E` (zero fees)
- **AND** in both, `cash_collateral` equals the short-sale proceeds `shares * p` and `positions_value` equals `-(shares * p)` at price `p`

#### Scenario: Shorting on top of a long does not fill in either simulator

- **GIVEN** an account in either the HK or US simulator already holding a long position in `CODE` (`shares > 0`)
- **WHEN** a `short` order for `CODE` is submitted
- **THEN** the order does not fill (no trade record is produced)
- **AND** `cash`, `cash_collateral`, and the existing long position are left unchanged
