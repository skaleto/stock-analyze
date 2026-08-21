# Online runtime fixes and ECS worktree commit

## Goal

Repair the two currently failing online research services without weakening
fail-closed data-quality rules, then audit and commit the intended source,
configuration, documentation, and test changes already present in the ECS
worktree. Runtime data, secrets, generated markers, and scratch artifacts stay
out of the commit.

## Root-cause evidence

- `stock-analyze-tabular-forward.service` fails while technical features are
  computed: a `pyarrow` extension array is assigned into a pre-created NumPy
  `float64` column. The same run also reports only `0.586` adjusted-price
  coverage for the requested snapshot, which must remain a fail-closed input
  guard rather than being bypassed.
- `stock-analyze-intelligence-semantic.service` completes the provider job and
  import, then spends the full two-hour timeout refreshing the full research
  feature snapshot. The refresh emits pandas fragmentation warnings while
  adding missing event-factor columns one at a time.

## Implementation steps

1. Add a red regression test for technical-feature computation with Arrow-backed
   numeric columns and a missing-price row; add a red test for the tabular
   incomplete-coverage command result being a bounded deferred/guard outcome.
2. Make derived technical columns use an explicit numeric NumPy-compatible
   representation before assignment. Preserve the existing coverage threshold
   and make its expected data-deferred outcome return a recognized exit status
   so systemd health checks do not report a false service failure.
3. Add a red regression test that runs event-factor attachment against a
   deliberately fragmented DataFrame with pandas performance warnings treated
   as errors. Batch default factor columns and derived output columns so the
   refresh does not repeatedly insert columns into a large frame.
4. Run focused tests, then the repository verification commands required by
   `AGENTS.md`. Commit the local source fix and deploy it only through
   `scripts/deploy-app-to-ecs.sh`.
5. Verify the deployed release marker, HTTP/dashboard health, service results,
   child ledgers, and expected semantic/tabular artifacts. Treat a current
   incomplete data snapshot as deferred, not successful research output.
6. On ECS, inspect every dirty path and diff. Stage only reviewed source,
   configuration, documentation, archive, and test files; exclude local
   settings, `.semantic-hotfix/`, deployment/runtime markers, logs, generated
   data, and protected production state. Create one clearly named commit and
   re-check the worktree.

## Verification

- Focused regression tests for technical features, CLI guard behavior, and
  intelligence factors.
- `git diff --check`.
- `python3 -m compileall -q stock_analyze tests`.
- `python3 -m unittest discover -s tests`.
- `cd frontend/dashboard && npm test && npm run build`.
- ECS service/ledger/artifact checks and a final remote `git status`/commit
  inspection.
