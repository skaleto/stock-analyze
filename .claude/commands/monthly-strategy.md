---
description: Run the monthly strategy proposal for the given agent (claude|codex). Reads the monthly briefing and writes both a markdown note and a structured JSON proposal.
argument-hint: "<agent_id> [<YYYY-MM>]"
---

You will produce the **monthly strategy proposal** for agent `$ARGUMENTS`
(first token) in the dual-agent A-share paper trading competition.

Argument parsing:
- First token = `<agent_id>`. Required. Must be `claude` or `codex`.
- Second token (optional) = target month `YYYY-MM`. If absent, default to
  the previous calendar month (the system's `default_month_for()`).

Steps:

1. Read the operating manual:
   - If agent is `claude`, read `CLAUDE.md` (especially §3, §4, §5b).
   - If agent is `codex`, read `AGENTS.md` (especially §3, §4, §5b).

2. Find or generate the monthly briefing:
   - Path: `data/<agent>/notes/briefings/<YYYY-MM>-monthly.md`.
   - If absent, run via Bash:
     `python3 -m stock_analyze agent-prepare-monthly --agent <agent> --month <YYYY-MM>`.

3. Read the briefing top-to-bottom. It contains a `competition_id`,
   baseline summary, the full monthly comparison JSON excerpt, and the
   list of baseline-locked paths that you MUST NOT touch in `patch`.

4. Read up to 4 of your most recent weekly notes (paths under
   `# 可选参考`). Skim, don't memorise.

5. Produce two outputs (use `Write` for both, not `Edit`):

   a. **Monthly note** at
      `data/<agent>/notes/<YYYY-MM>-monthly-review.md`.
      - Markdown, ≤ 1500 中文字.
      - Three sections: 月度复盘 / 与对手差异化分析 / 策略调整方向与理由.

   b. **Strategy proposal** at
      `data/<agent>/proposals/<YYYY-MM>-strategy.json`.
      - **Strict JSON.** No markdown fences, no trailing commas, no
        comments.
      - Schema (from the briefing's `# 输出契约`):
        ```json
        {
          "agent_id": "<agent>",
          "based_on_config_hash": "<latest config_hash from data/<agent>/runs.csv>",
          "proposed_at": "<YYYY-MM-DD>",
          "rationale": "<300 中文字内>",
          "expected_effect": "<一句话>",
          "risks": ["<风险 1>", "..."],
          "no_change": false,
          "patch": {
            "factors": {"<factor>": {"weight": <num>, "direction": "high|low"}},
            "factor_processing": {},
            "portfolio_controls": {},
            "filters": {}
          }
        }
        ```
      - If you decide nothing should change, output `no_change: true` and
        `patch: {}`. Still write the JSON file.
      - `patch` MUST NOT contain any baseline-locked path listed in the
        briefing.

6. Forbidden in this run:
   - Directly editing `configs/agents/<agent>.yaml`. Strategy edits come
     later (Phase 2: `enable-monthly-config-evolution`).
   - Touching `configs/competition.yaml`, `stock_analyze/`, `tests/`,
     `openspec/`, `docs/`, `CLAUDE.md`, `AGENTS.md`.
   - Reading or writing the other agent's `data/<other>/` or
     `reports/<other>/` directories. The monthly review's JSON excerpt is
     your only sanctioned view of the other side.
   - Running `git commit` / `git push` / network calls.

7. When done, summarise to the user: target paths written, the headline
   decision (change vs no_change), and one sentence on the rationale.
