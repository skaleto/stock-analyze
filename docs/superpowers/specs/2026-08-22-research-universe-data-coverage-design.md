# Research Universe Data Coverage Design

## Goal

Make every active A-share security discoverable with transparent classifications,
restore CSI1000 detail coverage through a research-only price cache, and make
eligible offshore OTC funds useful for research by displaying persisted NAV
history and risk/return statistics.

## Boundaries

- The formal HS300 and ZZ500 accounts, protected account ledgers, model
  registry, and strategy configurations are not read or mutated by the new
  collectors.
- Dashboard handlers read only local catalog and collector artifacts. They do
  not call Tushare or any other provider.
- OTC NAV is displayed as an adjusted-NAV line, never fabricated into OHLC
  candles. It is research information and never creates an order.

## Catalog and browsing

`refresh-research-universes` will read the active A-share master with
`ts_code,name,industry,market,list_date` and one latest valid `daily_basic`
cross-section with `total_mv,circ_mv`. It publishes all active records, keeping
the existing HS300/ZZ500/CSI1000 memberships as additive scopes. Each record
has `industry`, `board`, `list_date`, latest market-cap fields, a source date,
and an explicit `size_bucket`: 微盘 (<= 50亿元), 小盘 (50–200亿元), 中盘
(200–1,000亿元), 大盘 (> 1,000亿元), or 未分类 when no valid market cap is
available. The UI can filter one common dimension (`指数范围` / `板块` /
`行业` / `市值分层`) and displays industry, board, and size columns.

With the current source snapshot, the prior three index memberships are 1,800
records while the active master is 5,549 records. The full active catalog is
therefore the supported route for browsing the remaining 3,749 A-shares,
including small and micro-cap candidates; it does not extend formal strategy
or execution scope.

## Dedicated data artifacts

Two independent collection commands write append-safe research artifacts:

- `refresh-a-share-research-prices` writes normalized current A-share OHLCV to
  `data/research/a_share_prices/v1/<ts_code>.csv` and a manifest. Its initial
  target is exactly the CSI1000 membership; it is later reusable for any
  catalog scope.
- `refresh-otc-fund-nav` writes Tushare `fund_nav` rows to
  `data/research/otc_fund_nav/v1/<ts_code>.csv` and a manifest. Its default
  target is the current Nasdaq-100 and S&P 500 OTC catalog scopes.

Both artifact readers verify fixed headers, identifier/date syntax, positive
values, sorted/de-duplicated dates, and bounded response sizes. A failed code
is recorded in the manifest and does not destroy an older valid file.

## Detail API and UI

The existing research instrument endpoint keeps its account-isolated response
contract and gains optional `navSeries` and `navLatest` fields. A-share detail
first looks in the new research price cache and then retains the existing legacy
history fallback. OTC detail reads the NAV artifact and emits adjusted NAV
series plus metrics for one/three/twelve month return, annualized return,
annualized volatility, and maximum drawdown. A missing artifact is an honest,
per-instrument warning, not an API failure and not a provider request.

## Verification

Backend tests cover catalog enrichment and filtering, cache normalization and
manifest behavior, dashboard projection and its fallback behavior. Frontend
tests cover rendered classifications and the distinct NAV view. Deployment
verification checks local Dashboard HTTP responses, formal ledgers, active
timers, and the new artifact manifests before reporting completion.
