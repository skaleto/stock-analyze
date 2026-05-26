# scripts/ — operator workflow shortcuts

This directory holds the **operator-facing** entry points. ECS-side
automation lives in `deploy/systemd/`; this directory is what the
human operator (or **the local Claude agent acting on their behalf**)
invokes from a laptop.

---

## TL;DR — the operator's mental model

> "I don't want to type 4 commands every weekend. Tell Claude to run
> the cycle." — operator, 2026-05-24

The operator prefers **conversational triggers** over command-line
typing. When they say any of the trigger phrases below, the local
Claude agent (you, reading this) should silently invoke the right
script and report back when done. Do NOT ask the operator to type
the command themselves — they already told you what they want.

| Operator says (any of these) | You should | Underlying command |
|---|---|---|
| "跑本周复盘" / "周末同步" / "weekly cycle" / "走一遍周末流程" / "做 weekly review" | run `./scripts/weekly.sh` | sync-from + Claude review + Codex review + sync-to |
| "跑月度演化" / "月度策略" / "monthly cycle" / "月初演化" / "策略调整一下" | run `./scripts/monthly.sh` | sync-from + Claude rewrite yaml + Codex rewrite yaml + diff pause + sync-to |
| "拉一下 ECS" / "sync from ecs" | run `./scripts/sync-from-ecs.sh --exclude-cache` | just rsync ECS → local |
| "推上去" / "sync to ecs" | run `./scripts/sync-to-ecs.sh` | just rsync local → ECS + refresh dashboard |
| "检查定时任务" / "timer check" | run `./scripts/check-ecs-timers.sh` | verify ECS pipeline timers and old timer cleanup |

**Always run via `Bash` tool, in foreground, with adequate timeout
(weekly: 600s, monthly: 1200s). Stream the output to the operator
as it comes back, then summarize what changed.**

---

## scripts/check-ecs-timers.sh — pipeline health canary

**When**: run weekly, or any time you suspect the ECS automation has
drifted (missing dashboard refresh, suspiciously short `runs.csv`, etc.).

**What it does** (two checks, both via one SSH session):

1. **Timer layout** — confirms the three expected timers
   (`stock-analyze-market-data.timer`, `stock-analyze-weekly-trigger.timer`,
   `stock-analyze-monthly-review.timer`) are enabled and active, and that
   the deprecated single-agent / per-agent timers from earlier iterations
   are not enabled.
2. **Ledger consistency** — for each `(agent, cadence) ∈ {claude,codex}×{daily,weekly}`,
   compares the most recent `Finished` event from
   `journalctl -u stock-analyze-<agent>-<cadence>.service` (last 7 days,
   `-t systemd`) against the most recent matching `started_at` in
   `data/<agent>/runs.csv`. A drift of more than one day means the
   service ran but `RunLedger` never appended a row — the regression
   first observed on 2026-05-20/21 before the per-agent ledger path was
   wired up (see [investigation note](../data/claude/notes/2026-05-26-historical-pipeline-investigation.md)
   §副产物 #2).

**Sample output (clean run)**:

```
NEXT                        LEFT          LAST  PASSED  UNIT                                    ACTIVATES
Tue 2026-05-26 17:25:00 CST  3h            Mon …  …       stock-analyze-market-data.timer         stock-analyze-market-data.service
…
OK: stock-analyze dual-agent pipeline timers are enabled and old timers are disabled.

Checking service-vs-runs.csv ledger consistency (last 7 days)...
OK: stock-analyze-claude-daily.service Finished=2026-05-25, runs.csv latest run-daily=2026-05-25.
OK: stock-analyze-claude-weekly.service Finished=2026-05-22, runs.csv latest run-weekly=2026-05-22.
OK: stock-analyze-codex-daily.service Finished=2026-05-25, runs.csv latest run-daily=2026-05-25.
OK: stock-analyze-codex-weekly.service Finished=2026-05-22, runs.csv latest run-weekly=2026-05-22.
OK: service journal and run_ledger are consistent.
```

**Sample output (drift detected)** — e.g. the service ran today but the
ledger row from 2 days ago is the most recent:

```
…
Checking service-vs-runs.csv ledger consistency (last 7 days)...
WARN: service ran but run_ledger missing for claude daily on 2026-05-26 (latest runs.csv row: 2026-05-24, drift 2d).
OK: stock-analyze-claude-weekly.service Finished=2026-05-22, runs.csv latest run-weekly=2026-05-22.
OK: stock-analyze-codex-daily.service Finished=2026-05-26, runs.csv latest run-daily=2026-05-26.
OK: stock-analyze-codex-weekly.service Finished=2026-05-22, runs.csv latest run-weekly=2026-05-22.
ERROR: ledger drift detected — at least one service ran without an accompanying runs.csv row.
```

Exit code is non-zero on drift, so wrappers can detect it.

**Manual drift test** (only do this on staging or with operator approval):
temporarily rename one `runs.csv` (`mv data/claude/runs.csv data/claude/runs.csv.bak`),
then `systemctl start stock-analyze-claude-daily.service`. After it
finishes, re-run this script — expect the `WARN` line above. Restore
`runs.csv` immediately afterwards.

---

## scripts/weekly.sh — one-shot weekly cycle

**When**: every Sunday after ECS Saturday weekly-trigger has produced
new briefings (`data/<agent>/notes/briefings/<friday>-weekly.md`).

**What it does** (4 steps, ~2-5 minutes total):

```
[1/4] sync-from-ecs --exclude-cache
      → pull briefings + state from ECS

[2/4] claude -p "<weekly-review prompt>" --add-dir <repo> --allowedTools "Read,Write,Edit,Bash,Glob,Grep"
      → spawns a nested Claude session that reads CLAUDE.md +
        data/claude/notes/briefings/<latest>.md, writes
        data/claude/notes/<date>-weekly-review.md
      → output → logs/weekly-<ts>/02-claude-weekly.log

[3/4] codex exec --cd <repo> --sandbox workspace-write \
        --dangerously-bypass-approvals-and-sandbox \
        "<weekly-review prompt>" \
        -o logs/weekly-<ts>/03-codex-weekly-last.txt
      → spawns Codex CLI non-interactively that reads AGENTS.md +
        data/codex/notes/briefings/<latest>.md, writes
        data/codex/notes/<date>-weekly-review.md
      → output → logs/weekly-<ts>/03-codex-weekly.log

[4/4] sync-to-ecs
      → push reviews back to ECS, refresh competition dashboard
```

**Logs**: all four steps land in `logs/weekly-<YYYYMMDD-HHMMSS>/`. If
any step fails, that step's log file has the full traceback.

---

## scripts/monthly.sh — one-shot monthly strategy evolution

**When**: 1st-3rd of each month, after ECS `monthly-review.timer`
(01:00 UTC = 09:00 CST on day 1) has produced the monthly briefing.

**What it does** (4 phases + human pause, ~5-10 minutes):

```
[1/4] sync-from-ecs → pull monthly briefing
[2/4] claude -p     → Claude reads briefing, rewrites configs/agents/claude.yaml,
                      writes evolution_log/<month>.md + evolution_diff/<month>.json,
                      backs up prior yaml, appends config_evolution.csv,
                      runs `validate-overlay --agent claude`
[3/4] codex exec    → Codex does the symmetric thing for codex.yaml
[4/4] git diff      → prints the yaml diffs for both agents,
                      then pauses with `read -r` for operator review.
                      Operator presses Enter to continue.
sync-to-ecs         → push new yamls + evolution artifacts to ECS
```

⚠️ **The Enter-to-continue pause in step 4 is intentional**: monthly
runs actually CHANGE strategy. If the operator is on a phone or AFK,
you (Claude) running `monthly.sh` will block at the diff pause until
they confirm. That's correct — never auto-skip the pause.

---

## Prerequisites (one-time, on this laptop)

```bash
which claude     # Claude Code CLI (this thing)
which codex      # Codex CLI from OpenAI
ssh ai-baby-aliyun "echo OK"   # SSH alias must resolve
```

Both LLM CLIs need to be logged in. If `codex exec` fails with auth
error, the operator needs to `codex login` once.

---

## What about the slash commands?

`.claude/commands/weekly-review.md` and `.claude/commands/monthly-strategy.md`
are still there. They define what a SINGLE agent does for ONE side
(claude OR codex). The shell scripts above orchestrate BOTH sides
plus the sync steps.

| Tool | Scope | Trigger |
|---|---|---|
| `/weekly-review claude` (slash) | Claude side only | inside Claude Code IDE |
| `/weekly-review codex` (slash) | Codex side only | inside Codex CLI |
| `./scripts/weekly.sh` | Both sides + sync, end-to-end | operator's terminal OR Claude on their behalf |

---

## What the operator does NOT want

- Typing 4 sync/review/sync commands manually every weekend.
- Switching between Claude Code IDE and Codex CLI windows.
- Reading raw `claude -p` JSON output. Summarize.
- Being asked "should I run X?" when they already said "跑本周复盘".

---

## Error handling

If a step in `weekly.sh` fails:

```bash
# See which step failed
ls -lt logs/weekly-* | head -3

# Read that step's full log
cat logs/weekly-<ts>/<NN>-<step>.log

# Re-run just that step manually after diagnosing
# e.g. claude -p failed → claude --version && check auth
# e.g. codex exec failed → codex --version && check auth
# e.g. sync-from-ecs failed → ssh ai-baby-aliyun "echo OK"
```

The operator pays you (a small amount of Anthropic credits) to do
this triage. If you can't pinpoint the failure in 2 messages, just
paste the error log back and ask which CLI they want to re-auth.

---

## Why these scripts exist

The original docs/ workflow had 4 separate user actions per week
(see docs/competition-runbook.md §A "Mode A: LLM 在本地"). That made
sense for the design phase. In practice the operator wanted "one
button". These wrappers compose the documented steps without
changing the underlying contract — each step still hits the same
slash command body, just orchestrated non-interactively.

If you (the future Claude reading this) ever feel tempted to bypass
the wrappers and call `codex exec` directly: do it through the
wrapper. The wrapper logs every step under `logs/weekly-<ts>/` —
that audit trail matters when the operator asks "what did you write
to my yaml last month".
