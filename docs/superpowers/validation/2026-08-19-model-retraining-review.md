# Model Retraining Review — 2026-08-19

## Decision

No existing machine-learning artifact is approved for blind retraining or
promotion. The four account-scoped candidates found at the start of this review
were deterministic transparent rules, not fitted estimators. A post-deployment
quality audit proved that all four had entered under the retired
`personal-quant-shadow-v1` contract without passing the current historical
quality gate. They were backed up and rejected. No model is now Active or
Shadow, and no model is used by the formal paper accounts.

One missing evaluation is being completed without changing the frozen protocol:
the ZZ500 scope under `full-history-rebuild-v1`. HS300, QDII Hong Kong exposure,
and QDII US exposure already have complete 2018-2024 development evaluations.
All three returned `no_pass`.

## Asset inventory

| Market / scope | Current iteration candidate | Kind | Lifecycle | Retrain decision |
|---|---|---|---|---|
| A-share / HS300 | `rule-a-mom-02-02cd521a00a7` | transparent rule | Rejected | Retired admission contract; historical quality gate not passed |
| A-share / ZZ500 | `rule-a-mom-02-d0539ed9d01a` | transparent rule | Rejected | Retired admission contract; historical quality gate not passed |
| QDII / HK exposure | `rule-q-track-02-89b14a2fe59f` | transparent rule | Rejected | Retired admission contract; historical quality gate not passed |
| QDII / US exposure | `rule-q-track-01-5b20eaa7b1b6` | transparent rule | Rejected | Retired admission contract; historical quality gate not passed |

The model files created by the earlier local/Trae-era work remain Research or
Rejected. They have no Champion pointer and are not consumed by formal paper
accounts. The account-scoped 8 August/14 August tournament artifacts failed
ranker and/or executable portfolio gates; the older unscoped 3/5/10/20-day
artifacts are legacy research, not current mainlines.

## Baseline-first results from the 2026-08-13 immutable bundles

The bundles are real training/evaluation outputs, but every account selected the
transparent baseline over the bounded 10% residual model. Therefore no model
bundle was exported to Shadow.

| Scope | Candidate minus baseline net excess | Positive improvement folds | Decision |
|---|---:|---:|---|
| HS300 | -0.26 percentage points | 2/3 | baseline wins |
| ZZ500 | -0.65 percentage points | 1/3 | baseline wins |
| HK exposure | -0.06 percentage points | 1/3 | baseline wins |
| US exposure | -3.58 percentage points | 0/3 | baseline wins |

The A-share bundle used a feature snapshot beginning in July 2023, so it is not
the final full-history evidence. A separate `full-history-rebuild-v1` campaign
used the materialized 2018 history and is the controlling evidence below.

## Full-history frozen-protocol evidence

The same declared 2018-2024 outer folds, candidate families, costs, and
point-in-time audit were used. No thresholds or variants were changed after
observing results.

| Scope | Selected family | Rank IC | ICIR | Net excess | Max drawdown | Annual turnover | Result |
|---|---|---:|---:|---:|---:|---:|---|
| HS300 | Elastic Net | -0.0236 | -0.1155 | -9.66% | 60.93% | 34.86x | no pass |
| HK exposure | Additive | -0.0120 | -0.0225 | -8.32% | 56.06% | 17.02x | no pass |
| US exposure | Additive | -0.0164 | -0.0331 | -15.12% | 49.84% | 8.99x | no pass |
| ZZ500 | frozen run pending at review start | — | — | — | — | — | complete once, do not iterate |

These failures are not close enough to be repaired by appending several recent
sessions. The training/development window ends at 2024-12-31, so new 2026 rows
do not change the frozen development evidence.

## Snapshot freshness and fail-closed training input

Daily snapshots and model-training snapshots are not interchangeable:

- A-share `20260814` materialization: 1,672,726 rows, 1,402 instruments,
  2018-01-02 through 2026-08-14, with a complete materialization manifest.
- A-share `20260818` daily snapshot: 604,947 rows, 1,068 instruments. It starts
  in 2018 but does not have a matching full materialization manifest and its
  historical membership is much smaller.
- QDII `20260814` full-history snapshot: 43,527 rows from 2018-01-02.
- QDII `20260818` daily snapshot: 28,880 rows starting in 2023-08-18.

The training-bundle exporter now requires a 2018+ panel, required schemas, and,
for A-share, a matching complete `a-share-materialization-v1` manifest. It skips
newer truncated snapshots and records every rejection in the immutable bundle
manifest. Thus the monthly job fails closed instead of silently retraining on a
shorter daily panel.

## Shadow evidence correction

Account-scoped predictions are executed in a combined `scoped-<hash>` portfolio,
while the cycle tracker previously searched a nonexistent per-account model
portfolio. That caused real NAV rows to be reported as `daily_nav_missing` and
kept usable Shadow cycles at zero. The resolver now uses the composite portfolio
only when its persisted account-to-version mapping matches the requested
candidate. The recovered two-week evidence was negative for HS300, ZZ500, and
QDII HK exposure and slightly positive for QDII US exposure, but forward results
cannot repair the missing historical admission gate. Formal accounts and orders
were untouched.

## Subsequent authorized scenario experiment

The operator subsequently authorized `scenario-specialists-v1`. Its immutable
result is recorded in
`docs/superpowers/validation/2026-08-19-scenario-specialists-result.md`. HS300
and ZZ500 trained but failed their complete gates; both QDII scopes failed data
qualification before fitting. It produced no Shadow candidate.

## Conditions for a future model fit

A new fit is justified only when at least one of the following is true before
return inspection:

1. a new qualified full-history snapshot changes the frozen 2018-2024 inputs or
   the point-in-time contract, not merely the latest as-of date;
2. an independently authorized feature family passes its transparent primary
   gates and receives a preregistered bounded residual ablation;
3. a production defect materially changes labels, membership, costs, or the
   executable replay and requires one same-spec reproducibility rerun; or
4. a scheduled refresh has accumulated a meaningful number of newly matured
   labels inside a predeclared rolling training protocol.

None of the six event datasets collected on 18 August is automatically added to
the current model feature set, and this review does not reopen the stopped event
interaction campaign.
