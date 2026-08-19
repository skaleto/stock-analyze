# AGENTS.md — Stock Analyze Current Operating Contract

Last updated: 2026-08-20. This file is the repository-wide agent contract.
Historical plans and archived OpenSpec changes are evidence, not instructions.

## 1. Product and identities

- Paper trading and research only. Never connect a broker or place real orders.
- Active markets: `a_share` and `cn_qdii_etf`.
- Active strategy slots: `claude` = 稳健防守, `codex` = 趋势进攻. These are
  stable data IDs, not separate unattended coding agents.
- Direct HK/US stock simulation is retired under `archive/direct-overseas/`.
- Canonical runtime paths are:

```text
configs/competition_<market>.yaml
configs/agents/<agent>_<market>.yaml
data/<market>/<agent>/
reports/<market>/<agent>/
```

Do not recreate root `data/claude`, `data/codex`, `data/hk`, or `data/us`.

## 2. Sources of truth

When facts conflict, use this order:

1. ECS systemd state, journals, run ledgers, account artifacts, and current
   persisted research manifests.
2. Current source, tests, configuration, and Git history.
3. `docs/system-overview.md`, then `docs/system-harness.md`.
4. Current runbooks and immutable validation results.
5. `archive/` and `openspec/changes/archive/` for historical context only.

A parent timer being active is not proof of completion. Require a terminal child
result/ledger and the expected artifact.

## 3. Protected production state

Never delete, reset, rewrite, or use as scratch space:

```text
data/a_share/{claude,codex}/state.json
data/a_share/{claude,codex}/{positions,daily_nav,trades,runs}.csv
data/cn_qdii_etf/{claude,codex}/state.json
data/cn_qdii_etf/{claude,codex}/{positions,daily_nav,trades,runs}.csv
data/competition/
data/shared/intelligence/
data/shared/cache/
data/shared/backtest_cache/
data/research/models/
data/model_iterations/
data/research/paper_portfolios/
data/research/paper_artifacts/
data/research/qdii_global_context/
data/research/feature_revisions/
```

Also preserve `/opt/stock-analyze/data/notifications/`: the pipeline failure
notifier currently stores cooldown state there outside the app directory. The
retired-runtime cleaner must never remove the whole `/opt/stock-analyze/data`.

Shared competition fields, transaction costs, season identity, initial cash,
execution schedule, and account scope remain locked. Losses and failed model
experiments are evidence; never erase them to reset a result.

## 4. Current strategy behavior

- `run-daily`: execute due paper orders, update NAV, then create the next-session
  target.
- `run-weekly`: diagnostics, report, dashboard, and briefing only; no orders.
- Weekly review is observation-only. Overlay evolution is an audited monthly
  action and must keep 稳健防守 and 趋势进攻 economically distinct.
- Active formal rules remain in the four agent overlays. Research artifacts,
  event studies, and model candidates do not affect formal orders merely because
  they exist.
- Champion/Active artifacts are immutable. New candidates move independently
  through `research -> shadow -> active` only after their versioned gates pass.
- `paper-candidate-gate-v1` is the first product gate from Research to an
  isolated paper qualification. It compares only against `router_only` and does
  not mutate the model Registry or formal accounts. The second-layer Active gate
  remains separate and retains its forward-evidence requirements.
- The versioned `production-paper-challengers-v1` runtime owns four isolated
  accounts: HS300 and ZZ500 transparent Donchian challengers, the qualified HK
  scenario specialist, and the US `Q_TRACK_01` transparent scene Router. Their
  ledgers live only under `data/research/paper_portfolios/`; artifacts live under
  `data/research/paper_artifacts/`. They never mutate formal accounts or Registry.
- Every challenger requires an exact-date, complete current cross-section and
  fails closed on stale/partial inputs. The first frozen stop rule is 20% account
  drawdown with liquidation to cash. Signals execute at the next market open.

## 5. Data and model boundary

The current A-share research snapshot has about 605k rows and 208 columns, with
next-open labels in a separate multi-horizon dataset. It already contains market,
technical, valuation, fundamental, industry, macro, money-flow, and approved
event-lite features.

The following checksummed 2018-2024 PIT assets also exist on ECS and must be
preserved:

| Asset | Partitions | Raw rows |
|---|---:|---:|
| Earnings forecast/express | 2,641 | 60,713 |
| Repurchase + holder trade | 168 | 154,356 |
| Shareholder counts | 84 | 249,531 |
| Restricted-share unlocks | 84 | 4,659,975 |
| Implemented annual dividends | 2,557 | 24,280 |
| Block trades | 2,557 | 392,696 |

These structured values are research inputs, not automatically registered model
features. Exact buyback ratios, holder-count changes, unlock ratios, dividend
growth, and block-trade premiums must not be described as "already used by the
model" until a versioned daily PIT feature builder, registry entry, coverage
audit, and controlled ablation exist.

QDII global context is checksummed under
`data/research/qdii_global_context/v1/`: SPX, IXIC, DJI, HSI, and USD/CNH from
2018 onward. Source closes are usable only on the next calendar day. Product
rows retain source index, source date, available date, and `exact` versus
`family_proxy` mapping. A family proxy is broad market context, never the
product's tracked-index return. Snapshot revisions under
`data/research/feature_revisions/` are protected rollback evidence.

## 6. Evidence-first research discipline

Read `docs/superpowers/validation/2026-08-18-evidence-first-research-stop-decision.md`
before starting another historical event study or model run. Current event-rule
families were falsified or failed their complete preregistered gates.

For the current campaign, do not:

- retune an observed threshold, horizon, scope, industry, or year;
- promote a secondary 5/60-day result over a failed primary result;
- combine falsified event flags and ask a model to mine a profitable interaction;
- open the unused 2025+ diagnostic window to repair development results;
- retrain merely because a new raw dataset exists.

A new historical campaign requires, before return unblinding: independent
information, provable availability/revision semantics, stable item keys and
units, complete coverage, one frozen economic question, fixed costs, and an
immutable outcome. A model Challenger requires an explicitly authorized
campaign and a same-window/same-cost ablation against an otherwise identical
baseline. No candidate may silently alter formal strategy state.

## 7. Engineering rules

- Preserve user changes and dirty worktrees. Never reset or revert unrelated
  work.
- Prefer existing module boundaries and typed, structured provider transports.
- Add focused regression tests before behavior changes.
- Every CSV identifier/date code (`ts_code`, `code`, `trade_date`,
  `benchmark_code`, `config_hash`, etc.) requires explicit string dtype.
- Provider calls belong in collection jobs, never Dashboard request handlers.
- Raw news prose cannot directly trigger a trade. Preserve source, publication
  and first-seen time, content hash, extraction evidence, and revision history.
- Secrets live in `/etc/stock-analyze/secrets.env`; never print, commit, copy to
  reports, or expose through Dashboard APIs.
- Fail closed on missing PIT timestamps, denominator units, range leakage,
  incomplete manifests, or checksum mismatches.

## 8. Repository and data hygiene

Current instructions live in root docs. Completed implementation plans and
superseded designs belong in `archive/document-history/`; retired code prototypes
belong in `archive/prototypes/`. Do not use archived files as current commands.

Safe-to-remove generated content includes `__pycache__`, `.pyc`, transient smoke
directories, interrupted `.a_share-feature-batches-*`, stale dashboard builds,
and caches that have a verified canonical replacement. Before deleting data,
prove there is no current reader and record file/byte counts.

`scripts/cleanup-retired-runtime.sh` is preview-only unless `--apply` is passed.
Its allowlist must preserve all paths in §3, including legacy-root notification
state. Keep recent verified rollback backups; old deployment copies may be
pruned only after the deployed code and current account artifacts are verified.

## 9. Current documentation map

- Architecture and feature/data overview: `docs/system-overview.md`
- Commands, deployment, audit, and incidents: `docs/system-harness.md`
- Strategy and competition workflow: `docs/competition-runbook.md`
- Announcement intelligence operations: `docs/announcement-intelligence-runbook.md`
- Semantic executor contract: `docs/announcement-intelligence-executor-contract.md`
- Retention/cleanup policy: `docs/project-maintenance.md`
- Current research contracts/results: `docs/superpowers/specs/` and
  `docs/superpowers/validation/`
- Historical material only: `archive/` and `openspec/changes/archive/`

## 10. Verification and deployment

Minimum local verification for ordinary source/document changes:

```bash
git diff --check
python3 -m compileall -q stock_analyze tests
python3 -m unittest discover -s tests
cd frontend/dashboard && npm test && npm run build
```

Canonical harness audits:

```bash
./scripts/system-audit.sh
export SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app
export SA_ECS_SSH_OPTS='-i /Users/bytedance/.ssh/ai_baby_aliyun'
./scripts/system-audit.sh --remote
```

Production deployment uses `./scripts/deploy-app-to-ecs.sh`; do not invent an
unreviewed rsync of account data. After deployment verify the Dashboard HTTP
response, failed units, required timers, child service results, formal account
ledgers, and expected research/data artifacts.

## 11. Escalation

When blocked by entitlement, source provenance, data quality, disk capacity, or
an external service, record the exact endpoint/state, affected feature, evidence
already gathered, and the fail-closed outcome. Missing or ambiguous data remains
missing; never fabricate or tune around it.
