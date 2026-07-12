# Weekly Review

Use the optional argument as `week_end=YYYY-MM-DD`; otherwise select the latest
Friday whose four weekly ledgers are complete.

1. Read `AGENTS.md` and the repository workflow skill.
2. Verify ECS timers and all four `run-weekly` results:
   `a_share` and `cn_qdii_etf`, each for `claude` and `codex`.
3. Sync current ECS state without overwriting newer local source changes.
4. Review weekly reports, A-share briefings, pending orders, positions, trades,
   costs, NAV, data health, QDII selection snapshots, and strategy comparison.
5. Write `reports/competition/reviews/<week_end>-weekly.md` with:
   data correctness, defensive attribution, offensive attribution, comparison,
   risks, and observations for the monthly decision.
6. Do not change any strategy overlay during weekly review.
7. Refresh the competition dashboard and sync the new review if needed.
8. Report the exact week, four pipeline statuses, material findings, and files.
