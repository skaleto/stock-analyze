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

**Always run via `Bash` tool, in foreground, with adequate timeout
(weekly: 600s, monthly: 1200s). Stream the output to the operator
as it comes back, then summarize what changed.**

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
