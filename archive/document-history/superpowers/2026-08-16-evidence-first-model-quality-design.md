# Evidence-First Model Quality Design

## Goal

Make `shadow` mean that a candidate has already demonstrated positive,
cost-adjusted, statistically governed historical alpha. Candidates that are
merely executable or safe remain `research`; they do not consume Shadow
accounts and are not presented as quality-qualified models.

This change improves lifecycle truthfulness. It does not claim that a model has
improved until frozen historical evidence and later live forward evidence prove
the improvement.

## Confirmed Problem

The current `personal-quant-shadow-v1` contract separates hard execution safety
from Active-quality evidence, but admits every trial that passes the safety
checks. A trial may therefore enter Shadow with:

- negative benchmark-relative return;
- negative excess return under the cost stress;
- only one positive walk-forward fold;
- low stationary-bootstrap probability; and
- `passed_transparent_gates = false`.

The four current ECS Shadow candidates were admitted through this path. Three
have negative historical net excess return, all have zero realized Shadow
cycles, and none passed the transparent Active-quality gate.

## Lifecycle Contract

The new contract is `evidence-first-shadow-v2`.

### Research

Any candidate with complete provenance and executable replay may remain
Research. Research candidates retain their full metrics and failure reasons.
They are not described as Shadow and do not generate Shadow orders.

### Shadow

A transparent candidate may enter Shadow only when all existing execution
safety checks pass and `passed_transparent_gates` is exactly `true`.

The existing transparent gate is reused rather than duplicated. It already
requires the campaign's frozen thresholds, including:

- positive benchmark-relative net return;
- non-negative cost-stress net excess return;
- reconciled attribution and point-in-time audit;
- complete walk-forward evidence;
- Deflated Sharpe Probability at least `0.95`;
- Probability of Backtest Overfit at most `0.50`;
- stationary-bootstrap probability at least `0.95`; and
- portfolio drawdown, fill, liquidity, turnover and trade evidence limits.

An executable trial that fails this quality gate receives
`quality_gate_not_passed` and remains Research. The previous
`promising`/`exploratory` labels remain available as diagnostics only.

### Active

Active promotion remains unchanged and still requires at least 12 usable
forward Shadow cycles plus the role-specific activation gates. Historical
quality is necessary but not sufficient for Active.

## Existing Shadow Audit

A read-only audit scans every account-scoped model registry and reports:

- the registry and model version;
- the admission contract;
- whether historical Active evidence passed;
- the current lifecycle status; and
- the required action.

With `--apply`, only transparent-rule candidates currently in `shadow` are
changed. A candidate is rejected when it uses the legacy v1 admission contract
or explicitly records `active_evidence_passed = false`. Champion and formal
strategy state remain immutable. The operation is idempotent and records a
stable lifecycle event.

## Reporting Contract

Admission output returns one decision for every evaluated account:

- `admitted`: strict historical quality gate passed;
- `blocked`: no quality-qualified trial exists;
- `audit_required`: a legacy Shadow is present and must be reviewed or rejected.

Blocked decisions retain per-trial quality reasons. Dashboard summaries must
not count legacy exploratory candidates as qualified Shadow.

## Anti-Overfitting Rules
- All thresholds and candidate families are frozen before historical evaluation.
- Feature and parameter selection use nested purged walk-forward training only.
- Validation rows never enter model fitting or feature-direction selection.
- Historical diagnostic data cannot redesign a rejected candidate.
- Zero Shadow admissions are an acceptable and reported outcome.
