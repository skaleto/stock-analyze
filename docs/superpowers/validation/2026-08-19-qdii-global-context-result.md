# QDII Global Context PIT Backfill Result — 2026-08-19

## Decision

The missing QDII global-index and USD/CNH point-in-time history is now complete
under `qdii-global-context-v1`. The repaired data passed coverage, identity,
checksum, and next-day-availability gates. This data repair does not authorize a
model promotion: the frozen QDII scenario experiment was rerun unchanged; Hong
Kong exposure completed training but returned `no_pass`, while US exposure still
returned `insufficient_data` because of structural 200-session ETF warm-up.

No formal overlay, paper account, order, position, model Registry, Champion, or
Shadow pointer was changed.

## Checksummed raw asset

The asset is stored under `data/research/qdii_global_context/v1/` with contract
hash `5a220f1e084e68839b258d8f64bde50e07124eed84f069bef4708d0e7f24f87c`.

| Source | Rows | Window | SHA-256 |
|---|---:|---|---|
| SPX | 2,168 | 2018-01-02 to 2026-08-18 | inside index asset manifest |
| IXIC | 2,168 | 2018-01-02 to 2026-08-18 | inside index asset manifest |
| DJI | 2,168 | 2018-01-02 to 2026-08-18 | inside index asset manifest |
| HSI | 2,121 | 2018-01-02 to 2026-08-18 | inside index asset manifest |
| Combined index Parquet | 8,625 | same | `9a124ea9b672fd1318c99799df451de376fe7c6e20c91b43486788630b9ca46b` |
| USD/CNH | 2,668 | 2018-01-01 to 2026-08-18 | `51e8cdfc42a5a1227f9bf1c9332116da45492cb8b92ae76b3f29319967cae1da` |

Manifest SHA-256:
`c09c15f1381035a89643562e6448a825319f5b88cfc202e107eba4b8aed02ba0`.

Tushare returned no rows for NDX, HSCEI, or HSTECH under the current endpoint.
The contract therefore labels broad substitutes as `family_proxy`, never as the
tracked product index. Exact mappings are limited to S&P 500→SPX, Dow Jones→DJI,
and Hang Seng→HSI. Nasdaq-family products use IXIC; other US and Hong Kong themes
use SPX or HSI broad context. All 32 current catalog index keys are mapped.

## PIT and snapshot audit

Every source close is available only on the next calendar day. Each repaired row
retains its source code, mapping kind, source trade date, and available date.
There were zero index-source or FX rows whose source date was the same as or
later than the mainland target date.

The complete `20260814` QDII snapshot remained at 43,527 rows with no duplicate
feature or label keys. Its feature hash changed from
`55d29f392f6e15eec1ff2fc872818308c850479b436f01615ee3a459aad76c6c` to
`a81a3a63e44019f98414953a7e0fc12cada153d94db3f45d5f18520f3fdae607`.
The original is retained under `data/research/feature_revisions/`.

| Scope | Global momentum | Global volatility | USD/CNH | SMA-200 in account view |
|---|---:|---:|---:|---:|
| HK exposure | 99.84% | 99.92% | 99.87% | 78.69% |
| US exposure | 99.60% | 99.78% | 99.69% | 80.72% |

The `20260817` and `20260818` daily snapshots were also repaired; all three
global context fields reached 100% coverage in those shorter panels. Their
previous versions were retained by hash. The current QDII prediction workflow
then completed with 180 predictions over four horizons and no prediction
failure.

## Frozen model rerun

The original `scenario-specialists-v1` contract, features, thresholds, costs,
folds, and random seed were not changed.

### Hong Kong exposure

The data gate was removed: `global_index_momentum` coverage rose from 0% to
99.74% in the frozen development dataset. All three scenes had enough training
dates and the model completed four folds.

- Scenario-specialist net excess: **+0.81%**
- Transparent reference: **-0.06%**
- Router only / pooled residual: **+0.61%**
- Rank IC: **+0.0191**
- Maximum drawdown: **24.37%**
- Annual turnover: **5.63x**

It still failed the preregistered gates: aggregate improvement was only +0.87
percentage points versus the transparent reference (required +1.00), +0.20
points versus router/pooled (required +0.50), and only 2/4 folds improved versus
router/pooled (required 3/4). Status remains `no_pass` and Research-only.

### US exposure

`global_index_momentum` coverage rose from 0% to 99.36%, so the source-data gap
is closed. The frozen development dataset still has only 68.92% coverage for
`sma_distance_200`, below its predeclared 70% floor. This is caused by ETFs that
have fewer than 200 prior trading sessions after listing. Filling it would
fabricate pre-listing or warm-up history, so the candidate remains
`insufficient_data` and was not fitted.

## Operational state

Daily collection now requests global context from 2018-01-01 instead of a
370-day rolling window and includes DJI. The standalone checksummed asset is
preferred when present. Repair is transactional: coverage or leakage failure is
checked before replacing a snapshot, with a regression test proving that the
original bytes remain unchanged on failure.
