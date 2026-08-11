# AGENTS.md - Stock Analyze Operating Contract

Read `docs/system-overview.md` for architecture and `docs/system-harness.md` for commands before changing or operating this repository.

## Product Scope

- Paper trading only. Never connect a broker or place real orders.
- Active markets: `a_share` and `cn_qdii_etf`.
- Direct HK/US stock simulation is retired under `archive/direct-overseas/`.
- Internal `claude` means strategy `稳健防守`; internal `codex` means `趋势进攻`.
- Both strategy slots are maintained by Codex. The IDs remain for data compatibility, not for unattended Claude/Codex execution.

Canonical paths:

```text
configs/competition_<market>.yaml
configs/agents/<agent>_<market>.yaml
data/<market>/<agent>/
reports/<market>/<agent>/
```

Do not recreate root `data/claude`, `data/codex`, `data/hk`, or `data/us` paths.

## Source Of Truth

Use this priority when facts conflict:

1. Current ECS systemd units, journals, `runs.csv`, and artifacts.
2. Current source, tests, and configuration.
3. `docs/system-overview.md` and `docs/system-harness.md`.
4. Historical plans, OpenSpec changes, and archives.

Do not infer completion from a parent timer alone. Require a terminal success ledger row and the expected artifact.

## Strategy Semantics

- `run-daily`: execute due orders, update positions/NAV, then create the next-session target.
- `run-weekly`: diagnostics, reports, dashboard, and briefing only. It does not create orders.
- New features or models do not enter live decisions merely because they exist. They require time-availability checks, coverage, stability, and a versioned gate.
- Active models are immutable Champions. New Challengers iterate independently through `research -> shadow -> active`.
- Weekly review is observation-only. Strategy overlay evolution is monthly and audited.

Shared competition baseline fields and transaction-cost assumptions stay locked. Never delete ledgers to hide losses or reset a season.

## Engineering Rules

- Preserve dirty worktrees and user changes. Never reset or revert unrelated work.
- Prefer existing module boundaries and structured parsers.
- Add focused tests before bug fixes or behavior changes.
- Every CSV identifier column such as `ts_code`, `code`, `trade_date`, `benchmark_code`, and `config_hash` must be read with an explicit string dtype.
- Dashboard requests read local persisted data only; supplier APIs belong in offline collection jobs.
- Raw news prose cannot directly trigger a trade. Persist provenance and extract structured events first.
- Secrets live in `/etc/stock-analyze/secrets.env`; never print, commit, or return them through Dashboard APIs.

## Verification

Minimum local audit:

```bash
./scripts/system-audit.sh
python3 -m unittest discover -s tests
cd frontend/dashboard && npm test && npm run build
```

ECS audit:

```bash
export SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app
export SA_ECS_SSH_OPTS='-i $HOME/.ssh/<ssh-key-file>'
./scripts/system-audit.sh --remote
```

Production deploy uses `./scripts/deploy-app-to-ecs.sh`. It builds the frontend, runs tests, syncs code, applies the allowlisted retired-runtime cleanup, installs units, restarts services, and performs live checks.

## Operator Workflow

The ECS sends consolidated Lark messages. Human judgement is triggered in Codex:

```text
运行 YYYY-MM-DD 周度复盘
运行 YYYY-MM 月度策略演化
```

Weekly work must not edit active overlays. Monthly evolution must keep the two strategy hypotheses distinct, produce an immutable four-overlay release manifest, pass pair validation and historical gates, and leave a rollback point.

## Escalation

When data entitlement or an external source blocks progress, record the exact source, endpoint, permission state, affected features, and fallback. Missing data remains missing or low-confidence; never fabricate it.
