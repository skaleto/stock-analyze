# Stock Analyze System Consolidation Design

## Goal

Consolidate the current production system into one understandable source tree,
one factual architecture document, and one executable operator/agent harness.
Retired direct HK/US simulation and obsolete scheduling paths must no longer
look active, while all current paper-trading, research, model, and audit data
remain intact.

## Source Of Truth

The deployed ECS runtime at `/opt/stock-analyze/app`, its enabled systemd
timers, service journals, data ledgers, and Dashboard APIs are the runtime
truth. The `market-intelligence` worktree is the implementation baseline
because it contains the unified mature-quant, model-lifecycle, intelligence,
Dashboard, and alerting changes currently deployed.

## Structure

The active product has five bounded layers:

1. Data acquisition and immutable caches.
2. Point-in-time features, labels, event studies, and model research.
3. Versioned model governance and two independent strategy policies.
4. Paper execution, positions, trades, NAV, benchmarks, and audit ledgers.
5. Resource-oriented Dashboard APIs, React UI, systemd operations, and Lark
   notifications.

Only `a_share` and `cn_qdii_etf` are active markets. Internal identifiers
`claude` and `codex` remain storage-compatible strategy slot IDs and mean
`稳健防守` and `趋势进攻`; they do not mean two unattended LLM runtimes.

## Documentation Contract

- `docs/system-overview.md` contains current facts only.
- `docs/system-harness.md` contains executable procedures only.
- `AGENTS.md` is the concise Codex entry point and delegates operational detail
  to the Harness.
- `CLAUDE.md` is only a compatibility redirect, not a second strategy manual.
- `.claude/skills/stock-analyze-workflows/SKILL.md` mirrors the same current
  scheduling and safety contract.

## Cleanup Contract

Active source cleanup removes dead notification scripts, disabled fixed QDII
daily timers, old single-account systemd units, accidental local `/opt` copies,
Python bytecode, and active-tree HK/US implementations/configuration. Historical
HK/US source is retained under `archive/direct-overseas/` for audit.

ECS cleanup is allowlisted and removes only retired HK/US runtime data and
reports, abandoned feature-batch scratch directories, legacy single-account
data roots, and retired unit files. It never deletes active market accounts,
research outputs, intelligence data, shared market/backtest caches, model
registries, notifications, or competition audit history.

## Verification

Completion requires static structure tests, Python regression tests, frontend
tests/build, remote deployment tests, a passing timer/ledger audit, healthy
Dashboard APIs, zero failed units, and a read-back of the created Lark document.
