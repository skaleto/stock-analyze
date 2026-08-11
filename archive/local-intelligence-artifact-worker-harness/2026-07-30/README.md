# Local Intelligence Artifact Worker Harness Archive

Frozen: 2026-07-30  
Purpose: source handoff to another Coding Plan for bounded historical PDF
download and deterministic parsing

## Start here

1. Read `docs/local-intelligence-artifact-worker-coding-plan-handoff.md`.
2. Verify every source file with `MANIFEST.sha256`.
3. Use the canonical worktree at:
   `/Users/bytedance/.config/superpowers/worktrees/New project/market-intelligence`.
4. Run only `scripts/run-local-intelligence-artifact-worker.sh`; do not create
   another downloader, parser, queue, or database writer.

This bundle is a source snapshot/overlay, not a standalone clone of the whole
Stock Analyze repository. `SOURCE_FILES.txt` lists every included repo-relative
file.

## Frozen production evidence

- ECS root: `/opt/stock-analyze/app`
- Authoritative DB:
  `/opt/stock-analyze/app/data/shared/intelligence/intelligence.sqlite3`
- Schema: V14
- Bucket: `stock-analyze-hz`
- ECS harness source hash for
  `stock_analyze/intelligence/artifact_exchange.py`:
  `bf710a7ef7e0240326d7617a0f95066350a2ab2369bce175a6dae5cf41382e2f`
- Successful parse canary:
  `awj-007f935d59584c3f9a7e1dd50b12a823`
- Successful download canary:
  `awj-e771206eb1b645438da0cad1828b63aa`
- Pre-deployment ECS backup:
  `/opt/stock-analyze/backups/local-artifact-harness-pre-20260730T101400`

At freeze time the Dashboard and direct Python readback both reported one
successfully imported download document and one successfully imported parse
document. The normal ECS artifact backfill timer was restored. No local
LaunchAgent was installed.

## Integrity

From the extracted bundle root:

```bash
shasum -a 256 -c \
  archive/local-intelligence-artifact-worker-harness/2026-07-30/MANIFEST.sha256
```

The tarball itself has a sibling `.sha256` file outside the tarball.

## Deliberately excluded

- `data/`, `reports/`, `logs/`
- downloaded PDFs and parsed payloads
- `.local-intelligence-artifact-worker/`
- SSH private keys and any credential values
- `/etc/stock-analyze/secrets.env`
- Python virtual environments
- frontend `node_modules/` and generated `dist/`
- unrelated dirty-worktree files

The harness does not call an LLM and consumes no DeepSeek, Codex, Claude, or
other model tokens. Semantic extraction remains an independent ECS pipeline.
