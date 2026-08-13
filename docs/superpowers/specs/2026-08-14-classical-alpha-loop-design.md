# Classical Alpha Loop Design

> Date: 2026-08-14
> Scope: research and paper trading only. No broker integration or real orders.

## Goal

Turn the existing classical-model research stack into one understandable,
repeatable path per market. The system must distinguish ranking skill from an
actually tradable portfolio, explain why returns differ from the benchmark,
and refuse promotion when evidence is incomplete. It must not create another
open-ended model race.

## Findings

- The tournament already produces two portfolios: a fixed ranking diagnostic
  portfolio and a calibrated executable portfolio. The Dashboard currently
  hides most of that distinction.
- Research entry points disagree on their default horizons: A-share runs use
  both 3 and 20 sessions, while QDII runs use both 5 and 10 sessions.
- The formal A-share rule replay hard-codes an 8% daily turnover ceiling and a
  1% target-change band. The live paper-trading optimizer defaults to full
  adjustment unless the strategy overlay declares a limit. That mismatch can
  create artificial cash drag in historical comparisons.
- Calibration rejects any non-monotonic raw bucket means. Finite samples often
  violate that condition even when a monotonic projection contains useful
  ranking information.
- The strongest formal trend strategy already uses momentum. The current
  A-share H20 candidate excludes it, so the learned model has to rediscover a
  strong public signal from noisier quality and low-volatility inputs.
- Existing attribution reconciles gross return, cost, and net return, but it
  does not separate cash drag from security selection.

## Target Architecture

### One declared mainline per market

- A-share: 20-session horizon, one momentum-anchored residual classical model.
- QDII: 10-session horizon, one declared HGBR mainline.
- Historical candidates stay in the registry for audit and comparison, but
  default training, arena, APIs, and scheduled jobs use only the mainline.
- The mainline policy lives in one Python module and is consumed by CLI,
  pipeline, shell automation, and Dashboard projection.

### Diagnostic and deployable tracks

- The diagnostic track always builds a fixed Top-N rank portfolio. It answers
  whether the score orders securities usefully without forecast calibration,
  edge gating, or a dynamic cash decision.
- The deployable track applies training-only calibration, expected-cost and
  uncertainty gates, lot size, liquidity, turnover controls, and cash.
- Promotion requires the deployable track. Diagnostic success alone never
  activates a model.

### Monotonic calibration

- Fit score buckets on the calibration fold only.
- Preserve raw bucket returns for audit.
- Project bucket means to a non-decreasing curve with weighted isotonic
  regression. Validation and final-window labels are never used to fit it.
- Reject flat or non-positive calibrated curves, insufficient dates, and
  insufficient score variation. Do not reject a useful curve solely because
  adjacent raw sample means cross.
- Keep existing v2 artifacts readable while writing v3 artifacts.

### Momentum-anchored residual model

- Build an anchor from point-in-time 20- and 60-session momentum ranks.
- Train the classical estimator on cross-sectional future-return rank minus
  the anchor. At prediction time add the same contemporaneous anchor back.
- Keep the anchor formula deterministic and serialize its target version in
  the model bundle.
- Compute clipping bounds from the actual fitted target rather than raw excess
  returns.

### Replay parity and attribution

- The formal-rule historical contract must derive only controls that the live
  strategy actually declares. Absent controls use the live defaults: no target
  band, full adjustment, and no extra turnover ceiling.
- Record beginning risky exposure for every replay period.
- Reconcile active return exactly into cash drag, security selection, and
  execution cost:

  `active = cash_drag + selection + execution_cost`

- Expose both cumulative diagnostics and period-level components.

### Dashboard

- Show one current mainline per market and the number of archived experiments.
- Present ranking diagnosis and deployable portfolio side by side.
- Show calibration status and blockers in Chinese.
- For formal participants, show cash drag, selection contribution, and cost
  contribution so a loss is attributable rather than merely reported.

## Safety

- No candidate becomes active because this work completed.
- No formal strategy overlay changes unless corrected same-window evidence
  passes existing validation and improves predeclared metrics.
- Historical final windows remain historical diagnostics, not live OOS proof.
- Existing registries and artifacts remain immutable audit evidence.

## Acceptance

1. All production research entry points resolve to A-share H20 and QDII H10.
2. Exactly one default classical candidate is trained per market/account scope.
3. Calibration v3 accepts a noisy but projectable monotonic relationship and
   remains training-only.
4. Momentum residual bundles round-trip through serialization and prediction.
5. Formal replay defaults match live controls and no longer inherit an
   undeclared 8% turnover cap.
6. Every replay period reconciles active-return attribution within `1e-10`.
7. Dashboard contract tests, frontend tests/build, targeted research tests,
   and the full Python suite pass.
8. Real-data arenas complete and report before/after metrics. Promotion remains
   blocked unless every declared deployable gate passes.

