# Capital Actions Preregistered Study

## Scope and separation

This is a new economic hypothesis. It does not reuse the earnings-drift result
to choose parameters. Development is 2018-2024, the 2025+ historical diagnostic
remains closed, and future OOS begins on 2026-08-18. No model fitting, Shadow
registration, or formal strategy change is allowed in this stage.

## Structured sources

- Tushare repurchase for stock repurchase lifecycle and realized amount.
- Tushare stk_holdertrade for company, management, and individual holder
  increases and decreases.

The LLM announcement pipeline is optional corroboration only. It is not a
prerequisite for the base event or its numerical materiality.

## Frozen event families

Positive candidate families are evaluated independently:

1. repurchase_completed: proc equals 完成;
2. holder_company_increase: holder_type C and in_de IN;
3. holder_management_increase: holder_type G and in_de IN;
4. holder_individual_increase: holder_type P and in_de IN.

Repurchase proposals and shareholder approvals are controls, not candidates.
Stopped repurchases and all decrease families are risk diagnostics; no A-share
short sale is assumed.

## Materiality

- A completed repurchase is eligible only when realized amount divided by the
  point-in-time total market value is at least 0.5%. Tushare total_mv is in ten
  thousand yuan and is converted to yuan before division. Same-code, same-date,
  same-lifecycle-family rows are aggregated so one tradable announcement has
  one outcome.
- A holder trade is eligible only when absolute change_ratio is at least 0.1%
  of total shares. Same-code, same-date, same-holder-type, same-direction rows
  are aggregated before applying the threshold.

Records with unavailable denominators remain in coverage diagnostics but are
not eligible candidates. Lifecycle records are never backdated.

## Execution and horizons

Entry is the first eligible open strictly after the announcement date. Returns
are measured at 5, 20, and 60 sessions against the PIT account benchmark. The
primary horizon is fixed at 20 sessions. The 5- and 60-session values are
secondary diagnostics and cannot replace a failed primary result.

Round-trip cost is 21 bps and stress cost is 1.5 times that amount.

## Family evidence gate

Each positive family requires at least 60 mature events, 30 unique securities,
three event years, and 15 events in both HS300 and ZZ500. A family below these
floors is insufficient_data, not a failure and not a candidate.

## Primary economic gate

At 20 sessions every candidate family must have:

- positive mean net active return;
- positive median net active return, preventing right-tail-only alpha;
- positive mean active return under stress cost;
- positive mean net active return in both account scopes;
- at least two thirds of event years positive;
- year-cluster bootstrap probability of positive mean at least 95%;
- no single year above 50% of total positive contribution.

Passing creates only a transparent_baseline_candidate for future observation.
No residual model is authorized by this protocol.
