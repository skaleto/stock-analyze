# Model Shadow Account Design

## Objective

Turn the point-in-time prediction artifacts into an independently funded paper
portfolio whose orders, holdings, costs, NAV, drawdown, and benchmark result can
be inspected without changing either competition strategy.

## Boundaries

- Runtime state lives under `data/model_shadow/<market>/`.
- The prediction source is `data/<market>/codex/predictions/`; the shadow
  account never writes into either agent namespace.
- The account is excluded from competition summaries, leaderboards, strategy
  releases, monthly evolution, and agent overlays.
- Existing market simulators remain the execution source of truth so fees,
  lots, settlement, T+1, and quote behavior match the rest of the system.
- The account is long-only and may hold 100% cash. A bearish forecast is a
  decision, not a reason to force a trade.

## Decision Policy

Each market has one account with CNY 1,000,000 initial capital.

| Market | Horizon | Benchmark | Holdings | Single-name cap |
| --- | ---: | --- | ---: | ---: |
| A-share | 20 trading days | CSI 300 (`000300`) | 10 | 10% |
| Cross-border ETF | 5 trading days | NASDAQ ETF (`513100.SH`) | 5 | 20% |

A prediction is eligible when it is not invalidated, confidence is at least
55%, `p_up > p_down`, and expected excess return is positive. Expected
volatility is derived from the predicted 10th/90th return interval. Candidates
are ranked by confidence-weighted expected return per unit of predicted risk,
with the probability spread as a secondary term. Position sizes use the
existing capped inverse-volatility allocator and a turnover penalty.

The daily sequence is:

1. Execute orders that are due.
2. Mark holdings and append/deduplicate NAV for the run date.
3. Read the latest prediction file no later than the run date.
4. If its decision key is new, cancel stale unfilled targets and write the next
   trading day's target orders. If the key was already consumed, do not create
   duplicates.
5. Persist selected signals and a compact `shadow_status.json` audit record.

## Runtime And Automation

The CLI command is:

```bash
python3 -m stock_analyze --market <market> --as-of YYYY-MM-DD run-model-shadow --offline
```

`stock-analyze-research.service` invokes it after predictions are written for
each market and before the two competition strategies run their daily cycle.
Failures use the existing research service failure path; reruns are idempotent.

## Dashboard

The left rail receives a separate top-level `模型影子账户` view. It is not a
child of `单策略分析` and the public route contains no agent identity:

```text
?market=cn_qdii_etf&view=model-shadow
```

The view keeps the existing dark terminal design and split-resource API. It
shows isolation status, model horizon and prediction date, NAV versus benchmark,
current holdings, transactions, pending targets, model predictions, and run
history. Instrument drill-down reads the shadow account's trades while using
the shared cached OHLCV and Codex prediction evidence.

## Operator Notification

The existing daily Feishu summary gets one compact `模型影子账户` section with
per-market NAV, daily run status, holdings, and pending-order count. No new
standalone notification is created.

## Acceptance

- Repeating a run for the same date/prediction does not duplicate trades,
  pending orders, or NAV rows.
- Agent state and leaderboard outputs are byte-for-byte outside the shadow
  command's write set.
- A bearish A-share snapshot can produce a cash-only decision with an explicit
  explanation.
- A qualifying ETF snapshot creates next-day paper orders with real names and
  prices from the shared cache.
- The new route works on desktop and mobile, and direct instrument drill-down
  includes the shadow account's buy/sell markers.
