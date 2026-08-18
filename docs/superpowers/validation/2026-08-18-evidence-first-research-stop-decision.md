# Evidence-First Research Stop Decision

## Decision

As of 2026-08-18, no newly tested A-share event family authorizes model
training, Shadow admission, overlay mutation, or formal-strategy change. The
current historical campaign stops adding thresholds, subgroups, and interactions
to these disclosed-event datasets. The unused 2025+ window remains closed.

This is not a claim that every event field is useless. It is a decision that the
predeclared transparent rules did not establish a stable typical-stock effect,
and that further in-sample rescue would be post-selection.

## Unified primary-horizon evidence

All numbers are net benchmark-relative 20-session returns under the study's
frozen event and evidence rules.

| Candidate mechanism | Mature events | Mean net | Median net | Stress mean | Bootstrap | Positive years | Max year share | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Positive earnings drift | 4,500 obs | +0.65% | -0.58% | +0.54% | 0.964 | 5/7 | 0.363 | Family falsified: 5/60 failed |
| Completed repurchase | 2,324 | -0.39% | -1.41% | -0.49% | 0.214 | 2/7 | 0.838 | Falsified |
| Company-holder increase | 1,205 | -0.62% | -1.23% | -0.72% | 0.001 | 1/7 | 1.000 | Falsified |
| Management increase | 119 | -0.57% | -0.45% | -0.68% | 0.307 | 3/7 | 0.745 | Falsified |
| Individual increase | 96 | +0.02% | -1.49% | -0.09% | 0.505 | 4/7 | 0.530 | Falsified |
| Quarterly holder concentration | 1,807 | +0.45% | -0.91% | +0.34% | 0.857 | 4/7 | 0.423 | Falsified |
| Large-unlock benchmark substitution | 623 | -2.21% | -0.79% | -2.32% | 0.000 | 0/6 | 1.000 | Falsified |
| Annual dividend growth | 935 | +0.18% | -0.68% | +0.07% | 0.811 | 4/6 | 0.614 | Falsified |
| Block-trade premium | 401 | -0.37% | -1.56% | -0.48% | 0.189 | 2/7 | 0.990 | Falsified |

The dominant failure mode is a negative median and unstable year/scope
contribution. Positive means in several families come from right tails rather
than a repeatable transparent baseline.

## Reusable PIT assets now complete

The following structured histories are checksummed, resumable, and available for
future hypotheses without repeating collection:

| Dataset | Window | Partitions | Raw rows | Normalized use |
|---|---|---:|---:|---|
| Earnings forecast/express | 2018-2024 | 2,641 | 60,713 normalized records | Earnings direction and strength |
| Repurchase + holder trade | 2018-2024 | 168 | 154,356 | Lifecycle, amount/PIT market cap, holder-type direction |
| Shareholder counts | 2018-2024 | 84 | 249,531 | Earliest quarterly PIT counts, pre-listing pairs removed |
| Restricted-share unlocks | 2018-2024 | 84 | 4,659,975 | Confirmed stock-date shares/PIT total shares |
| Implemented annual dividends | 2018-2024 | 2,557 | 24,280 | Continuous-fiscal-year total cash and PIT materiality |
| Block trades | 2018-2024 | 2,557 | 392,696 | Stock-day VWAP, raw-close premium, PIT market-cap intensity |

Discarded source candidates and reasons:

- `pledge_stat`: historical snapshot dates are irregular and lack a defensible
  announcement/availability timestamp.
- `pledge_detail`: the same pledge/release item appears in duplicate and
  near-duplicate versions without a stable unique item key; net changes cannot
  be made auditable without guessing.
- `top10_floatholders`: earliest-quarter snapshot completeness shifts from about
  98% complete ten-holder rows in 2018-2020 to about 51% in 2023; stock-specific
  queries do not restore missing holders.
- `report_rc`: historical `create_time` reflects a 2022 batch migration and the
  interface is limited to one request per minute; original PIT completeness
  cannot be verified.

These sources must not be used until their PIT contracts are independently
repaired by stronger provenance, not inferred from returns.

## Stop rule

The following work is prohibited for the current development campaign:

1. changing any tested threshold after seeing the result;
2. promoting a secondary 5- or 60-session result over a failed primary horizon;
3. selecting only HS300, ZZ500, one year, one industry, named buyer/seller, or
   valuation/momentum subgroup because it looks better in the same history;
4. combining multiple falsified event flags and asking a model to discover a
   profitable interaction;
5. opening 2025+ to repair development results;
6. retraining existing models merely because the structured data now exists.

## Conditions to restart historical research

A new historical study may start only if all are true before return unblinding:

1. **Independent information**: a new licensed/official source or a genuinely
   different economic mechanism, not another threshold on the six assets above;
2. **PIT proof**: original publication/availability timestamp, stable item key,
   revision policy, unit proof, complete 2018-2024 coverage, and fail-closed
   manifest;
3. **One economic question**: one candidate family, one primary horizon, fixed
   costs and thresholds, with controls declared in advance;
4. **Typical-stock gate**: positive median remains mandatory; right-tail mean is
   insufficient;
5. **Scope and time stability**: both account scopes and preregistered year/
   bootstrap/concentration gates remain mandatory;
6. **Immutable result**: pass, falsification, or insufficient data is committed
   without redesign against the observed return table.

Examples of acceptable new information are licensed order-flow/quote imbalance,
verified analyst-estimate revision timestamps from an entitled source, or an
official supply-chain/operating dataset with revision history. They are not
currently available under a proven PIT contract.

## Model-training permission

The number of models authorized for retraining remains **zero**. At most one
bounded Challenger may be trained only after a future transparent candidate
passes every primary gate. Its role must be a small preregistered residual or
ranking refinement against the same baseline; it must improve net results on the
same folds and costs, cannot reverse the baseline direction, and remains Research
until future Shadow evidence is earned.

## Operational state

Formal paper accounts, competition overlays, and locked baseline files were not
changed by these studies. Data collection and research reports are isolated from
the daily execution chain. The research conclusion is therefore a controlled
negative result, not a production incident.
