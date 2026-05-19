---
description: Run the weekly review for the given competition agent (claude|codex). Reads the latest weekly briefing and writes a markdown analysis note.
argument-hint: "<agent_id>"
---

You will run the **weekly review** for agent `$ARGUMENTS` in the dual-agent
A-share paper trading competition.

If `$ARGUMENTS` is empty, default to `claude`. If `$ARGUMENTS` is neither
`claude` nor `codex`, stop and tell the user.

Follow these steps exactly:

1. Read the operating manual for the chosen side. If agent is `claude`,
   read `CLAUDE.md`. If agent is `codex`, read `AGENTS.md`. Internalise
   the identity, locked fields, and workflow sections (§3, §4, §5/5b).

2. Find the latest weekly briefing at
   `data/<agent>/notes/briefings/*-weekly.md` (sort filenames descending,
   take the first). If the directory or file is missing, run
   `python3 -m stock_analyze agent-prepare-weekly --agent <agent>` via
   Bash, then re-list.

3. Read the briefing in full. The briefing contains five sections:
   `# 角色`, `# 数据快照`, `# 任务`, `# 输出契约`, `# 可选参考`.

4. Optionally read up to 3 historical notes referenced under
   `# 可选参考` for narrative continuity. Skip if the briefing's content
   is already enough.

5. Write a markdown note to the target path declared in the briefing's
   `# 输出契约` section (canonical:
   `data/<agent>/notes/<YYYY-MM-DD>-weekly-review.md`).

   The note must be:
   - ≤ 800 中文字.
   - Cover four sections in order: 数据合理性检查 / 本周表现归因 / 观察点 /
     下一步计划草稿.
   - Avoid imperative buy/sell phrasing; this is research, not advice.
   - Plain markdown only. No code fences around the entire body.

6. Forbidden in this run:
   - Modifying `configs/`, `stock_analyze/`, `tests/`, `openspec/`,
     `docs/`, `CLAUDE.md`, `AGENTS.md`, slash command files.
   - Writing to `data/<other-agent>/`, `reports/<other-agent>/`.
   - Creating any file outside the briefing's declared output path.
   - Running `git commit` / `git push` / network calls.

7. When done, briefly summarise to the user in chat: target file written,
   key observations, and any anomalies you want them to notice (e.g.
   factor coverage drops, pending orders piling up, NAV outliers).
