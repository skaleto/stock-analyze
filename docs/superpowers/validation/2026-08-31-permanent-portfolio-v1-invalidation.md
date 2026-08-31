# Permanent Portfolio v1 Accounting Invalidation

## Decision

`permanent_portfolio_v1` is retained as immutable historical evidence, but its
Development and Holdout performance numbers are invalid for strategy promotion
or paper-account initialization.

The invalidation is narrow and mechanical. It does not change the frozen asset
selection, target weights, rebalance rules, costs, benchmarks, or date boundary.
It requires a new `permanent_portfolio_v2` campaign and does not authorize
retuning from the observed v1 returns.

## Confirmed defects

1. The replay engine executed shares at raw next-open prices but valued those
   same shares at an adjusted-close unit. After an adjustment-factor change, a
   same-day buyer could receive phantom wealth. A deterministic fixture bought
   at raw `2.00` and was valued immediately at adjusted `4.00`, turning a
   200,000 account into 400,000 before costs.
2. The provider calendar can be descending. The v1 materializer merged that
   calendar before forward filling, so the first future quote could be filled
   backward into prelisting dates. `511260.SH` has 179 prelisting rows in the
   persisted v1 union and 180 such rows in the original Development partition
   inspection; its first real quote is `2017-08-24`.
3. Adjustment-factor increases corresponding to ETF cash distributions were not
   credited to account cash. Across the persisted 2016-12-01 through 2026-08-28
   source, the observed factor chain contains 10 distribution events for
   `510300.SH`, 4 for `511260.SH`, 10 for `511880.SH`, and none for `518880.SH`.

## Repair boundary

- Raw open remains the execution price.
- Raw close becomes the account valuation price.
- Adjusted close is used only for momentum/return signals.
- Audited cash distributions are credited before ex-date opening trades to
  shares held before that opening.
- Calendar alignment is ascending and begins at each instrument's first actual
  quote.
- Development begins on `2018-09-03`, after every frozen instrument has a real
  twelve-month history.
- The 2025-2026 rerun is labelled a bug-corrected sealed retest, not a pristine
  untouched blind test.

## Frozen v1 checksums before repair

These bytes must remain unchanged:

| Artifact | SHA-256 |
| --- | --- |
| `data/research/permanent_portfolio/v1/manifests/holdout_opened.json` | `47d43c7b323520960045a7f476dc36ef895ddec88fbdf10ff2b519d94e8a7441` |
| `data/research/permanent_portfolio/v1/manifests/state.json` | `0b8cc4617cae6baa51ae1ac41efb9dc80ab998fc3e3cfa7b42868734bac45ddf` |
| Development manifest | `b9dbca61d64c0a8543ec54295b695a75287007c54dcde84169bde73d07276b17` |
| Development Parquet | `cda586559bf0a57423f9bc5c4039abf190d4d60b9bfad428dac1602d45db888b` |
| Holdout manifest | `97a7adc2357f7f61d672203a97575235cc7c53e1357111c1024d9a0dd019364d` |
| Holdout Parquet | `28faffa5e19d6dd70840182f75ef556354911bc76276b6f54607313744768e51` |
| Development result | `639d1f7c05a5406d33992e418126b43fbf58b537aae6f4a9c6811076ef1722a9` |
| Holdout result | `2ec2a174a0950bfc592dddd1ac52a1ea0c27bff9ae14eaa1e822dfa9477d6a36` |
| Dashboard report | `90ddd46e38be28dbe78ce99923f790b5b2d90227eae35129a78564033cddc6d8` |

The immutable result payloads also retain their internal artifact hashes:
Development `07502786752f54cdbd1e0dd12ebf07678bf16ebb9520045fa253e2857235a9e3`
and Holdout `6aa2862a824b9ae7687a9861957a1084e0bcf254fa2b64a788e6d023eb55f5bc`.
