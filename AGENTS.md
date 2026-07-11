# AGENTS.md - Dual Strategy Operating Manual

This repository is a paper-trading research system. It never places real broker
orders and its output is not investment advice.

## 1. Current ownership model

As of 2026-07-11, Codex operates both strategy slots. The historical identifiers
remain unchanged so existing ledgers, systemd units, and URLs keep working:

| Internal ID | Product label | Style |
| --- | --- | --- |
| `claude` | 稳健防守 | value/quality, low volatility, lower turnover |
| `codex` | 趋势进攻 | momentum/growth, faster rotation |

`claude` is now a compatibility ID, not an independent Claude runtime. Codex may
read and maintain both slots, including both config, data, report, and audit
directories. Do not reintroduce the old cross-agent visibility restriction.

The authoritative registry is `configs/strategy_competition.json`. Product UI
must use its labels and colors rather than exposing the compatibility IDs.

## 2. Active markets

Only these markets are active:

- `a_share`: mainland A-share paper account.
- `cn_qdii_etf`: mainland-listed cross-border ETF/QDII paper account.

Direct US/HK stock simulation is archived under `archive/direct-overseas/`.
Do not reactivate it without an explicit operator decision.

Each market has two independent accounts. State is namespaced under
`data/<market>/<agent>/`; reports follow the same market/agent model. Never merge
the two account ledgers just because one runtime owns both strategies.

## 3. Locked competition baseline

The following fields in `configs/competition_<market>.yaml` are fairness and
accounting invariants and must not be overridden from an agent overlay:

- `competition_id`, `start_date`, `initial_cash`
- `accounts.*.cash`, `accounts.*.top_n`, `accounts.*.scope`,
  `accounts.*.benchmark`
- `schedule.execution`, `schedule.signal_day`
- all `trading.*` cost, lot-size, and concentration settings

The loader rejects violations with `competition_baseline_locked:<field>`.
Never delete `state.json`, `runs.csv`, `daily_nav.csv`, `positions.csv`, or
`trades.csv` to improve a result. Losses and costs are part of the experiment.

## 4. Strategy configuration

Active overlays are:

- `configs/agents/claude_a_share.yaml`
- `configs/agents/codex_a_share.yaml`
- `configs/agents/claude_cn_qdii_etf.yaml`
- `configs/agents/codex_cn_qdii_etf.yaml`

The two slots must remain materially different. Run both market guards after any
strategy change:

```bash
python3 -m stock_analyze --market a_share validate-strategy-pair
python3 -m stock_analyze --market cn_qdii_etf validate-strategy-pair
```

The minimum factor-vector distance comes from
`configs/strategy_competition.json`. Do not weaken it merely to make a release
pass.

Agent-prefixed alternative factors remain namespace-bound for data integrity.
For example, a `codex_*` factor belongs only in the `codex` slot even though the
same runtime now operates both slots.

## 5. Audited strategy releases

Multi-account changes must be delivered through a versioned manifest under
`configs/strategy_versions/<release>/manifest.json`, then applied atomically:

```bash
python3 -m stock_analyze apply-strategy-release \
  --manifest configs/strategy_versions/<release>/manifest.json
```

The release flow validates schema and locked fields, runs the A-share historical
gates as a complete preflight, checks pair differentiation, writes config
history, archives pre-release pending orders under
`pending_order_archive/<release_id>.json`, and appends the audit records. If a
gate fails, no active overlay may be changed.

For ECS publishing, use two phases:

1. `SA_SKIP_AGENT_CONFIG_SYNC=1 ./scripts/deploy-app-to-ecs.sh`
2. Apply the manifest on ECS and verify all four overlays/pairs.
3. Run the deploy script normally to synchronize the accepted active configs.

Never copy a candidate directly over an active ECS overlay before its gate runs.

## 6. Runtime workflows

For each active market and each slot:

```bash
python3 -m stock_analyze --market <market> --agent <agent> run-weekly
python3 -m stock_analyze --market <market> --agent <agent> run-daily
```

`run-weekly` generates signals and pending paper orders. `run-daily` executes due
orders, updates NAV, and refreshes output. Weekly generation does not itself
pretend an order was filled.

A-share uses the shared market-data and trigger units. QDII uses four direct
timers:

- `stock-analyze-{claude,codex}-cn-qdii-etf-daily.timer`
- `stock-analyze-{claude,codex}-cn-qdii-etf-weekly.timer`

Run `scripts/check-ecs-timers.sh` after deployment. A successful parent trigger
is not enough; inspect child service journals and `runs.csv` consistency.

## 7. Dashboard contract

The React application is built from `frontend/dashboard/` into `reports/app/`.
The live server exposes:

- `/app.html`
- `/api/dashboard/summary.json`
- `/api/dashboard/detail.json?market=<market>&agent=<agent>`
- `/api/dashboard/instrument.json?...`

The top-level view must compare both strategies across normalized season return,
benchmark excess, volatility, Sharpe, drawdown, cash, turnover, costs, holdings,
pending orders, factor mix, allocation, overlap, and return correlation. Use
`null`/"数据积累中" when the season has insufficient observations; never invent a
number.

## 8. Engineering rules

Codex may modify `stock_analyze/`, tests, frontend, deployment scripts, and both
strategy overlays when the operator requests engineering work. Keep changes
scoped, preserve existing ledgers, and add risk-proportionate tests.

All textually coded CSV identifiers such as `ts_code`, `trade_date`,
`benchmark_code`, and `config_hash` must be read with explicit string dtypes.
Otherwise pandas can silently turn `000300` into `300`.

Before claiming completion:

1. Run the relevant Python and frontend tests.
2. Build the dashboard production artifact.
3. Verify all four live strategy runs and timers on ECS.
4. Check the live JSON APIs and desktop/mobile UI.
5. Confirm no real-order integration was introduced.

## 9. Evaluation goal

The purpose is not to make both configurations converge on the latest winner.
Keep one defensive and one offensive hypothesis, measure their net-of-cost
behavior, and compare risk as well as return. No release can promise better
future returns; its expected benefit must be stated in terms of differentiation,
auditability, observability, or a measured historical gate.
