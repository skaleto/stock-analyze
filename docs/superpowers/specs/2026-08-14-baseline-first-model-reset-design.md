# Baseline-First Model Reset Design

> Date: 2026-08-14
> Scope: classical research and paper trading only. No broker integration or
> real orders.

## Problem Statement

The current model loop is technically strict but economically misdirected.
The A-share candidate dilutes the strongest observed public signal by blending
momentum and low volatility equally, while the QDII candidate uses a flexible
daily HGBR ranker on a small ETF universe. Both paths produce weak ranking
skill, excessive turnover or no deployable trades. Repeatedly changing a model
after reading the same final window has also consumed that window as a tuning
set, so it can no longer be treated as clean promotion evidence.

This reset changes the question from "can a model pass many gates?" to "does a
small learned residual improve a transparent, tradable baseline on untouched
development folds?"

## Evidence Boundary

- Existing tournament final-window results remain immutable diagnostics.
- No parameter, feature, threshold or model choice may be selected from those
  results after this reset.
- Candidate selection uses purged walk-forward folds inside the historical
  development window only.
- A frozen development winner may enter a paper-trading shadow account, but it
  cannot become a formal strategy until enough future observations exist.
- Promotion thresholds are not lowered to manufacture a passing version.

## A-Share Mainline

- Return engine: deterministic 20/60-session momentum anchor.
- Risk control: volatility, liquidity, industry and position constraints stay
  in portfolio construction; low volatility is not half of the alpha target.
- Learned component: a regularized linear residual with a predeclared maximum
  10% contribution. It may refine the baseline but cannot overturn it.
- Frequency: monthly rebalance, matching the 20-session horizon.
- Admission: the learned candidate must improve net excess return over the pure
  momentum baseline on development folds without materially worsening drawdown
  or turnover. If it does not, the simple baseline is the research winner.

## QDII Mainline

- Return engine: a deterministic absolute-trend anchor built from NAV and
  account-residual momentum, with volatility and premium/tracking penalties.
- Learned component: regularized linear residual, capped at 10%.
- Frequency: weekly rebalance. Daily cross-sectional HGBR is archived and is no
  longer the default mainline.
- Labels: every tournament must use the current `next-open-v2` contract. Stale
  snapshots fail before model fitting and are refreshed by the monthly job.
- Admission: compare the residual candidate with the pure trend anchor on the
  same development folds, net of costs.

## Experiment Budget

For this reset, each market/account scope has at most three predeclared trials:

1. transparent baseline;
2. baseline plus regularized residual;
3. one robustness variant only if the second trial fails for a diagnosed,
   predeclared reason.

The system records distinct trial specifications by protocol and refuses a
fourth trial. A failed family is archived instead of being tuned indefinitely.

## Evaluation Contract

Every development report must include, for candidate and baseline on identical
dates:

- fold-level and aggregate Rank IC / ICIR;
- net excess return, maximum drawdown and turnover;
- capital utilization, trade count and cost contribution;
- candidate-minus-baseline return and drawdown deltas;
- point-in-time and label-contract audit;
- explicit `development_pass`, `baseline_wins` or `insufficient_evidence`
  status.

The learned candidate passes only when its net incremental effect is positive
in aggregate and in a majority of eligible folds, point-in-time checks pass,
and risk/cost limits remain satisfied. A positive Rank IC by itself is not a
pass.

## Shadow Lifecycle

- A development winner is frozen by model/version/hash before shadow use.
- The shadow account records decisions and paper fills independently from
  research reruns.
- First decision after 12 distinct trading weeks of usable evidence.
- Evidence may extend to 16 weeks only when the sample is insufficient; any
  hard quality failure or failure at week 16 rejects the version.
- The next version is trained separately and never changes the frozen shadow
  model in place.

## Acceptance

1. QDII stale `next-open-v1` labels cannot silently enter training.
2. Portfolio replay supports deterministic weekly rebalancing.
3. A-share default scoring uses momentum plus at most 10% learned residual.
4. QDII default scoring uses a deterministic trend anchor, ridge residual and
   weekly rebalance; HGBR remains available only as archived research history.
5. Development reports compare baseline and candidate on identical folds and
   expose incremental economics.
6. Trial four is mechanically refused for a reset protocol.
7. No old model is promoted and no formal paper account is reset.
8. Real-data runs either produce a qualifying frozen candidate or an honest
   stop report naming the failed hypothesis and next required evidence.

