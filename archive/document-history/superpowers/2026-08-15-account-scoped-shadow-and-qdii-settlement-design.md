# Account-Scoped Shadow and QDII Settlement Design

## Goal

Give each paper account its own auditable Shadow decision and remove the
artificial cash drag caused by treating mainland-listed cross-border ETF sell
proceeds as unavailable for trading until T+1. The strict Active gate and all
formal competition accounts remain unchanged.

## Confirmed Problems

1. Shadow admission currently evaluates only each scope's `display_trial`, then
   selects at most one scope per market. A valid HS300 candidate is therefore
   hidden by a stronger ZZ500 candidate even though the two have separate paper
   accounts.
2. Both historical replay and the forward paper simulator queue all ETF sell
   proceeds until T+1. Mainland exchange rules distinguish trading availability
   from withdrawal availability: proceeds may buy another security on the sale
   day, while withdrawal remains T+1. Weekly strategies consequently leave sale
   proceeds idle for a full rebalance interval in the current implementation.
3. A rejected scope disappears from the admission result, so the Dashboard
   cannot explain whether no strategy was tested, all strategies were unsafe,
   or a strategy merely lost a market-level selection contest.

## Admission Contract

Shadow remains a safety-qualified research account, not a claim of alpha. The
existing hard checks and `promising`/`exploratory` grades remain unchanged.

Selection changes from **one per market** to **one per account scope**:

- A-share: `hs300`, `zz500`;
- mainland-listed cross-border ETF: `hk_exposure`, `us_exposure`.

For every scope, admission evaluates every sealed trial referenced by the
campaign ledger, chooses the best safe trial deterministically, and returns a
decision row even when no trial passes. Ranking remains grade, net excess,
double-cost excess, bootstrap probability, lower drawdown, stable trial id.

The campaign report is only an index. Full trial metrics must be rehydrated from
the immutable `trials.jsonl` ledger and must match campaign, manifest, market,
scope, spec and horizon provenance before selection. A scope with no safe trial
fails closed with its per-trial rejection reasons.

## Cross-Border ETF Cash Semantics

The cross-border ETF mechanics expose two separate facts:

- `SELL_PROCEEDS_REUSABLE_SAME_DAY = True` for secondary-market trading;
- `SETTLEMENT_DAYS = 1` for settlement/withdrawal metadata.

On a sell, net proceeds are credited to tradeable cash immediately. The trade
record still carries its T+1 settle date. Because this paper system does not
model withdrawals, it must not also queue that same amount for a second credit.
Markets without the capability flag keep the existing settlement queue path.

Historical portfolio replay consumes the same capability through the frozen
campaign mechanics contract. This is required parity: the same sell/buy
rebalance must produce the same cash availability in replay and forward Shadow.

## US ETF Interpretation

Correcting settlement is an execution fix, not an alpha claim. The current US
trend candidate may enter Shadow only if a newly sealed campaign proves all
hard safety checks after the fix. If admitted with negative historical excess,
it is labeled `exploratory` and its underperformance remains visible.

The next US research candidate should use a two-stage core/satellite structure:
select index exposure first, then select the most liquid/low-tracking-error ETF
wrapper. That work must be evaluated as a new predeclared trial; it must not be
silently substituted into an already sealed campaign or promoted directly.

## Dashboard Contract

The model-research API and UI show four account rows. Each row includes:

- account scope and benchmark;
- selected Shadow version, strategy id and evidence grade when admitted;
- historical net/excess return, drawdown, fill ratio and stress excess;
- `admitted`, `blocked`, or `not_evaluated` state;
- concise failed checks for blocked scopes.

Market-level summaries are derived counts only. They never hide one scope
because another scope ranked higher.

## Safety and Acceptance

- No formal strategy, champion, order, position, trade or NAV file changes.
- No Active threshold is relaxed and no automatic promotion is enabled.
- Existing T+1 trade metadata remains intact.
- A fresh immutable campaign is required after changing execution semantics.
- Unit tests prove per-scope selection, full-ledger hydration, rejection
  visibility, same-day sell/buy reuse, no double credit, and replay/forward
  parity.
- Release acceptance requires four explicit scope decisions, four Shadow
  admissions only where hard checks pass, same-date isolated cycles, unchanged
  formal-state fingerprints, passing Dashboard contracts, and ECS HTTP/service
  verification.
