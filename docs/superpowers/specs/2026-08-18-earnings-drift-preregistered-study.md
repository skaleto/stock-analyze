# Earnings Drift Preregistered Study

## Decision

Model and hyperparameter search is frozen. The only new alpha hypothesis in
this cycle is post-announcement drift after an A-share earnings forecast or
earnings flash. This stage performs no fitting and cannot register a model,
change Shadow state, or create formal orders.

## Frozen windows

- Development: 2018-01-01 through 2024-12-31.
- Historical diagnostic: 2025-01-01 onward; remains closed unless development
  passes every evidence and economic gate.
- Future live OOS: 2026-08-18 onward.

The study may read a later immutable feature snapshot only with row filters
that stop at the development end. Results from 2025 onward cannot be used to
change this protocol.

## Hypothesis

Material positive earnings_forecast and earnings_flash events are
under-reacted to and produce positive benchmark-relative returns over 5, 20,
and 60 trading sessions after next-open execution.

The transparent score is direction times strength times confidence. The
tradable baseline is long-only: direction must be positive, confidence at
least 0.70, and strength at least 0.25. Negative events are retained only as a
diagnostic; A-shares are not assumed shortable.

## Execution and costs

- Signal availability uses the point-in-time intelligence available_at.
- Entry is the first eligible market open strictly after the local available
  date and no more than seven calendar days later.
- Exit is the close of the 5th, 20th, or 60th eligible trading session.
- Active return is security return minus its account benchmark return.
- Round-trip cost is 21 bps; stress cost is 1.5 times that value.

## Evidence gate

Before judging economics, the development window requires at least:

- 60 mature earnings events;
- 30 positive events;
- 30 unique securities;
- three event years;
- 15 observations in each evaluated account scope.

If this gate fails, the only valid result is insufficient_data. No model may
be trained.

## Economic gate

For all three horizons, positive events must have positive net active return
at base and stress costs. At least two thirds of event years must be positive,
the year-cluster bootstrap probability of positive net active return must be
at least 95%, and no year may contribute more than 50% of total positive
contribution. Both HS300 and ZZ500 scopes must pass where evidence is mature.

Passing creates only a transparent_baseline_candidate. Machine learning is
still prohibited until a separate, predeclared residual-layer protocol is
approved. Failing with adequate evidence falsifies this hypothesis.

## Current known limitation

At protocol creation, the canonical intelligence database contains only a
small set of normalized earnings events, concentrated in legacy 2005-2010
fixtures and 2026 live collection. The expected first result is therefore
insufficient_data; this is a valid outcome and defines the backfill needed
before any economic claim.
