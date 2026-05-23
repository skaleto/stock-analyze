---
description: Run the monthly strategy evolution (LLM-direct) for the given agent (claude|codex). Reads the monthly briefing + opponent overlay snapshot, then directly rewrites configs/agents/<agent>.yaml + writes evolution_log + evolution_diff + appends config_evolution.csv (via evolution_writer).
argument-hint: "<agent_id> [<YYYY-MM>]"
---

You will produce the **monthly strategy evolution** for agent `$ARGUMENTS`
(first token) in the dual-agent A-share paper trading competition.

> Human operator 2026-05-23 authorised LLM full agency:
> "所有优化的内容全部由 LLM 全权代理执行,不需要审核,只要让我看到修改了什么。"
> This slash command implements OpenSpec change
> `enable-llm-direct-strategy-evolution`.

Argument parsing:
- First token = `<agent_id>`. Required. Must be `claude` or `codex`.
- Second token (optional) = target month `YYYY-MM`. If absent, default to
  the previous calendar month (the system's `default_month_for()`).

Steps:

1. Read the operating manual:
   - If agent is `claude`, read `CLAUDE.md` (especially §3, §4, §5b, §7).
   - If agent is `codex`, read `AGENTS.md` (especially §3, §4, §5b, §6, §7).

2. Find or generate the monthly briefing:
   - Path: `data/<agent>/notes/briefings/<YYYY-MM>-monthly.md`.
   - If absent, run via Bash:
     `python3 -m stock_analyze agent-prepare-monthly --agent <agent> --month <YYYY-MM>`.

3. Read the briefing top-to-bottom. It contains:
   - `competition_id`, baseline summary, monthly comparison JSON excerpt.
   - **`## 对手 <other> 当前 overlay 摘要`** — your opponent's current
     `factors`, `portfolio_controls`, `filters` (allowed to read).
   - **`## 对手 <other> 历史改动(近 3 个月)`** — opponent's CSV history
     (allowed to read; the markdown evolution_log itself is OFF-LIMITS).
   - Baseline-locked paths you MUST NOT touch in the new overlay.

4. Read your own current overlay:
   - `Read` `configs/agents/<agent>.yaml`. This is the **old_overlay**.

5. Read up to 4 of your most recent weekly notes (paths under
   `# 可选参考`). Skim, don't memorise.

6. Decide what to change. Frame it as a delta on `old_overlay`. You may:
   - Change weights in `factors.<name>.weight` (must stay in `[0, 1]`).
   - Toggle `factors.<name>.direction` between `high`/`low`.
   - Add/remove factors (only those in
     `stock_analyze.overlay_guard.AVAILABLE_FACTORS`).
   - Change anything in `factor_processing`, `portfolio_controls`,
     `filters` — subject to baseline locks.
   - Decide on no change. Still write the evolution_log explaining why.

7. Produce two outputs:

   a. **Monthly note** (your high-level reflection) at
      `data/<agent>/notes/<YYYY-MM>-monthly-review.md`.
      Markdown, ≤1500 中文字. Three sections:
      月度复盘 / 与对手差异化分析 / 策略调整方向与理由.

   b. **Evolution log** (the audit trail for your strategy change) at
      `data/<agent>/evolution_log/<YYYY-MM>.md`.
      Markdown, ≤2000 中文字. Six sections per `design.md` §3:
      - 月度复盘(数据驱动)
      - 与对手差异化分析(读 `configs/agents/<other>.yaml` + monthly_reviews)
      - 改动列表(field / 旧值 / 新值 / 理由 表格)
      - 改动理由(展开)
      - 预期效果(超额 / 行业暴露 / 风险预期)
      - 不在范围(明确声明不动的字段)

8. Apply the evolution atomically. Use `Bash` to run a one-shot Python
   that invokes `evolution_writer.write_evolution`. Example structure:

   ```python
   import json
   from pathlib import Path
   from stock_analyze.evolution_writer import write_evolution
   old = json.loads(Path("configs/agents/<agent>.yaml").read_text(encoding="utf-8"))
   new = {...}  # your decided overlay
   reasoning = Path("data/<agent>/evolution_log/<YYYY-MM>.md").read_text(encoding="utf-8")
   result = write_evolution(
       agent_id="<agent>",
       old_overlay=old,
       new_overlay=new,
       reasoning_md=reasoning,
       month="<YYYY-MM>",
   )
   print(result)
   ```

   This call atomically:
   - Runs `overlay_guard.validate` on `new` (raises on any violation).
   - Backs `configs/agents/_history/<from_hash>.yaml` up.
   - Overwrites `configs/agents/<agent>.yaml`.
   - Writes `data/<agent>/evolution_diff/<YYYY-MM>.json`.
   - Appends a row to `data/<agent>/config_evolution.csv`.

9. Run the guard one more time to be safe:
   ```bash
   python3 -m stock_analyze validate-overlay --agent <agent>
   ```
   Exit 0 = OK. Any non-zero = fix the yaml.

10. Forbidden in this run:
    - Editing `configs/competition.yaml`, `configs/agents/<other>.yaml`,
      `stock_analyze/`, `tests/`, `openspec/`, `docs/`, `CLAUDE.md`,
      `AGENTS.md`.
    - Reading the other agent's `data/<other>/evolution_log/*`,
      `data/<other>/notes/*`, `data/<other>/state.json`,
      `positions.csv`, `daily_nav.csv`, `trades.csv`, `factor_runs/*`,
      `reports/<other>/*`. (You may read `configs/agents/<other>.yaml`
      and `data/<other>/config_evolution.csv` per transparency rules.)
    - Running `git commit` / `git push` / network calls. The human
      operator handles sync via `./scripts/sync-to-ecs.sh`.

11. When done, summarise to the user:
    - Target paths written (monthly note, evolution_log, evolution_diff,
      config_evolution.csv row, history backup).
    - `from_hash → to_hash`.
    - One-sentence diff summary.
    - Reminder for the operator to run `./scripts/sync-to-ecs.sh`.
