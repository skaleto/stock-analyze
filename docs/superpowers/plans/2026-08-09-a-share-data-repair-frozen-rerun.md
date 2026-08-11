# A-Share Data Repair and Frozen Rerun Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the existing A-share backtest cache into a complete point-in-time research snapshot, eliminate every current A-share data blocker, and rerun the already frozen value/quality rule-core diagnostic without tuning its signal.

**Architecture:** Keep Tushare acquisition, immutable research snapshots, and the rule-core diagnostic as separate contracts. Extend the resumable backtest cache only for missing status history, add one deterministic materializer from `data/shared/backtest_cache/` to the existing research inputs, then rebuild the snapshot and rerun the same Stage 1 command. No strategy, Dashboard, ECS runtime, or model activation work is admitted before the data audit passes.

**Tech Stack:** Python 3.11, pandas, Parquet/CSV/JSON, Tushare Pro, unittest, existing `ResearchStore`, atomic file helpers, and the corrected rule replay.

---

## 1. 中文执行摘要

当前 A 股不是策略失败，而是研究快照没有真正消费完整的历史回测缓存：

- 回测缓存位于 `data/shared/backtest_cache/`，研究流水线主要读取 `data/shared/cache/history_*.csv` 与 `data/research/raw/a_share/`。
- 当前研究快照只有 1,123 个日历日，远少于八年。
- 默认保留 500 只股票完整历史，无法覆盖历史 HS300 与 ZZ500 成分股并集。
- `daily_basic`、财务公告日期虽然能下载，但尚未完整物化进研究快照。
- 历史 ST、停牌和上市状态没有进入特征行。

本计划只做数据桥接和冻结复验。最终必须得到以下三种结果之一：

```text
proceed             A股规则核通过，可进入单组合阶段
negative_hypothesis 数据合格但规则核跑输，正式关闭该假设
data_blocked        明确列出仍缺失的端点或日期，不允许调参
```

QDII 当前趋势假设保持 `negative_hypothesis`，本计划不调整其参数。

## 2. Frozen Scope and Gates

### 2.1 Frozen research contract

- Data cutoff: `20260807`.
- Required history: `2018-01-01` through `2026-08-07`.
- Intended A-share core: `configs/agents/claude_a_share.yaml`.
- Fixed controls: `configs/agents/codex_a_share.yaml`, 1/N, and account benchmark.
- Development window: oldest 60% only.
- No factor-weight, filter, rank-buffer, cost, or portfolio-policy edits.
- Do not read the validation or sealed-final partitions.

### 2.2 Data admission gate

The rebuilt snapshot must satisfy the existing diagnostic thresholds:

```text
full history >= 8 calendar years
trading-date density >= 85% overall and per year
point-in-time membership contract coverage >= 95%
HS300 and ZZ500 constituent-size coverage >= 95%
entry and benchmark price coverage >= 98%
entry execution/status coverage >= 98%
security names = 100%
required daily_basic factor coverage >= 95%
financial publication-date and restatement-policy coverage >= 95%
security status, historical ST, and suspension flags >= 95%
```

Any failed item returns `data_blocked`. A source failure may never be replaced by
a constant default merely to open the gate.

## 3. Data Flow

```text
Tushare Pro
  -> data/shared/backtest_cache/                 raw resumable cache
  -> AShareResearchMaterializer                 deterministic bridge
  -> data/shared/cache/history_*.csv            technical history input
  -> data/research/raw/a_share/20260807/*.parquet
  -> ResearchPipeline.prepare_data(force=True)
  -> data/research/features/a_share/20260807.parquet
  -> run-rule-core-diagnostic --offline --as-of 20260807
```

The materializer owns no strategy logic. It only normalizes point-in-time source
records, produces hashes and coverage counts, and fails closed.

## 4. Task 1: Complete Historical Status Acquisition

**Files:**
- Modify: `stock_analyze/markets/a_share/backtest/data_prep.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_backtest_data_prep.py`
- Test: `tests/test_cli_prepare_backtest_data.py`

- [x] **Step 1: Add failing tests for missing status history**

Cover these contracts:

```python
def test_namechange_ranges_merge_and_preserve_history(): ...
def test_stock_st_and_suspend_dates_are_resumable(): ...
def test_baostock_status_fallback_preserves_source_provenance(): ...
def test_schema_invalid_status_response_is_retried(): ...
def test_valid_empty_status_range_can_be_marked_complete(): ...
```

- [x] **Step 2: Verify the tests fail**

Run:

```bash
python3 -m unittest -v tests.test_backtest_data_prep
```

Expected: the new tests fail because no historical name/status endpoints are
persisted yet.

- [x] **Step 3: Add resumable status endpoints**

Persist historical status results under:

```text
data/shared/backtest_cache/namechange/<ts_code>.csv
data/shared/backtest_cache/stock_st/<YYYY-MM-DD>.csv
data/shared/backtest_cache/suspend_d/<YYYY-MM-DD>.csv
data/shared/backtest_cache/baostock_status/<ts_code>.csv
```

Required name-change columns:

```text
ts_code, name, start_date, end_date, ann_date, change_reason
```

Required suspension columns:

```text
ts_code, trade_date, suspend_timing, suspend_type
```

Required ST columns:

```text
ts_code, name, trade_date, type, type_name
```

Use `stock_st` as the preferred historical ST flag when entitled. The live
2026-08-09 probe confirmed that this account lacks `stock_st` access, so use
Baostock historical `isST` as the production fallback and persist
`st_source=baostock_history_isST_v1`. Use `namechange` only for point-in-time
display names and as a consistency check; never infer a historical ST flag from
today's security name. Cross-check Baostock `tradestatus` against Tushare
`suspend_d`; disagreements remain quarantined instead of defaulting to tradable.

Use the existing atomic merge helper and record range progress only after schema
validation. Preserve a previous complete file when a retry returns a partial or
invalid response.

Add `--code-scope historical-index-union` to the preparation command. Fetch
monthly index weights before code-scoped endpoints, derive the historical
HS300/ZZ500 member union, and limit `fina_indicator`, `adj_factor`, and
`namechange` calls to that union. Daily, daily_basic, stock_st, and suspend_d
remain one full-market call per trading date and are filtered during
materialization. Existing callers that omit the option retain their current
behavior.

The real probe also verified Baostock history for a normal security, an ST
security, and a suspended security. Fetch Baostock status once per historical
union code only when Tushare `stock_st` is unavailable; it is not a replacement
price source.

- [x] **Step 4: Run focused tests**

Run:

```bash
python3 -m unittest -v \
  tests.test_backtest_data_prep \
  tests.test_cli_prepare_backtest_data
```

Expected: all tests pass and leading-zero security/date identifiers remain text.

### 4.1 Execution Evidence (2026-08-09)

- Focused tests: 33 passed.
- ECS backfill window: 2018-01-01 through 2026-08-07.
- Trading dates: daily 2,086/2,086; daily_basic 2,086/2,086;
  suspend_d 2,086/2,086.
- Historical index snapshots: 208/208 files across 104 months; full union
  1,402 securities; 2018-2020 union 1,018 securities.
- 2018-2020 code ranges: fina_indicator 1,018/1,018; adj_factor
  1,018/1,018.
- Full-window code ranges: namechange 1,402/1,402; Baostock status
  1,402/1,402.
- File/schema audit: zero missing files, zero invalid schemas, zero unexpected
  empty files for every required endpoint above.
- Audit artifact: `reports/research/a_share_data_backfill_audit_20260809.json`.

Tushare `suspend_d` and Baostock `tradestatus` are complementary raw signals,
not interchangeable labels. The materializer must distinguish event/timing
records from full-day tradability and must preserve provenance when they differ.

## 5. Task 2: Build the Backtest-to-Research Materializer

**Files:**
- Create: `stock_analyze/research/a_share_materializer.py`
- Modify: `stock_analyze/cli.py`
- Create: `tests/test_a_share_research_materializer.py`
- Modify: `tests/test_cli_research.py`

- [x] **Step 1: Write a compact point-in-time fixture**

The fixture must include:

```text
one normal listed date
one historical ST interval
one suspension date with no daily quote
one delisted security
two monthly index-membership snapshots
two revisions of the same financial period
daily_basic valuation rows and benchmark OHLC
```

Assert that no record dated after the row's trade date changes that row.

- [x] **Step 2: Add the materializer contract**

Expose:

```python
materialize_a_share_research_data(
    *, repo_root: Path, cache_root: Path, start: date, end: date, as_of: str
) -> dict[str, object]
```

The implementation must:

1. Derive the union of historical HS300 and ZZ500 members from monthly weights.
2. Build each member's trading-calendar history without inventing executable
   prices on suspended or missing-quote days.
3. Attach point-in-time `name`, `list_date`, `delist_date`, `security_status`,
   `is_st`, and `is_suspended`.
4. Write normalized `history_<code>_<as_of>_<rows>.csv` inputs atomically.
5. Aggregate `daily_basic`, `fina_indicator`, stock master, status history, and
   benchmark frames into `data/research/raw/a_share/<as_of>/`.
6. Write `snapshot_manifest.json` and `materialization_manifest.json` containing
   row counts, date ranges, endpoint coverage, source hashes, output hashes, and
   the exact union-security count.

- [x] **Step 3: Add a CLI command**

```text
python3 -m stock_analyze materialize-a-share-research-data \
  --start 2018-01-01 \
  --end 2026-08-07 \
  --as-of 20260807 \
  --cache-root data/shared/backtest_cache \
  --repo-root .
```

Return non-zero when the source cache is incomplete or a required schema is
missing. Never emit a success manifest for partial input.

- [x] **Step 4: Verify determinism**

Run the fixture twice and assert identical hashes for every manifest and
materialized file.

## 6. Task 3: Preserve Point-in-Time Fields Through Feature Building

**Files:**
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/research/rule_core_diagnostic.py`
- Modify: `tests/test_research_pipeline.py`
- Modify: `tests/test_research_source_features.py`
- Modify: `tests/test_rule_core_diagnostic.py`

- [x] **Step 1: Add regression tests**

```python
def test_materialized_status_fields_survive_history_normalization(): ...
def test_historical_membership_union_is_not_truncated_by_random_500_cap(): ...
def test_financial_revision_is_visible_only_after_its_announcement(): ...
def test_complete_materialized_fixture_passes_a_share_audit(): ...
```

- [x] **Step 2: Retain status fields**

Allow normalized A-share histories to retain:

```text
name, industry, list_date, delist_date,
security_status, is_st, is_suspended
```

These fields are metadata/risk evidence, not alpha factors.

- [x] **Step 3: Select full history from historical membership**

When a verified materialization manifest exists, retain full history for its
declared HS300/ZZ500 union. Do not use the deterministic random 500-instrument
sample for the Stage 1 audit.

- [x] **Step 4: Preserve financial revisions**

Deduplicate exact `(ts_code, end_date, ann_date)` versions, then let the backward
as-of join choose the latest revision observable on each trading day. Never drop
the initial report merely because a later revision exists.

- [x] **Step 5: Run focused tests**

```bash
python3 -m unittest -v \
  tests.test_a_share_research_materializer \
  tests.test_research_pipeline \
  tests.test_research_source_features \
  tests.test_rule_core_diagnostic \
  tests.test_cli_research
```

Expected: fixture coverage passes and every future-leak test remains green.

## 7. Task 4: Run the Real Local Backfill

**Files produced:**
- `data/shared/backtest_cache/_meta.json`
- `data/shared/backtest_cache/daily/*.csv`
- `data/shared/backtest_cache/daily_basic/*.csv`
- `data/shared/backtest_cache/fina_indicator/*.csv`
- `data/shared/backtest_cache/adj_factor/*.csv`
- `data/shared/backtest_cache/namechange/*.csv`
- `data/shared/backtest_cache/stock_st/*.csv`
- `data/shared/backtest_cache/suspend_d/*.csv`
- `data/shared/backtest_cache/baostock_status/*.csv`
- `data/shared/backtest_cache/index_weight/*.csv`
- `data/shared/backtest_cache/benchmark_daily/*.csv`

- [x] **Step 1: Capture disk and source baseline**

Require at least 15 GB free locally. Record the existing cache manifest, source
date ranges, and file hashes before backfill. Do not delete prior cache files.

- [x] **Step 2: Run the resumable acquisition locally**

```bash
python3 -m stock_analyze prepare-backtest-data \
  --start 2018-01-01 \
  --end 2026-08-07 \
  --code-scope historical-index-union \
  --cache-root data/shared/backtest_cache
```

Do not use `--force` for the first run. Resume the same command after rate-limit
or network failures; completed partitions must be skipped.

- [x] **Step 3: Verify source completion**

Check endpoint-specific completed ranges, file schemas, yearly trading-day
density, monthly index-weight coverage, and benchmark coverage. A metadata flag
without a valid file is a failure.

- [x] **Step 4: Materialize research inputs**

Run the command from Task 2 and require a complete manifest. Keep the old
research snapshot until the new materialization and snapshot both pass.

## 8. Task 5: Rebuild and Audit the Frozen Snapshot

**Files produced:**
- `data/research/features/a_share/20260807.parquet`
- `data/research/features/a_share/20260807.metadata.json`
- `data/research/labels/a_share/20260807.parquet`
- `data/research/raw/a_share/20260807/materialization_manifest.json`
- `reports/research/a_share_data_repair_20260807.md`

- [x] **Step 1: Rebuild offline from materialized inputs**

```bash
python3 -m stock_analyze \
  --market a_share \
  --agent codex \
  --as-of 2026-08-07 \
  prepare-research-data \
  --offline \
  --force \
  --max-full-history-instruments 5000
```

- [x] **Step 2: Rebuild labels from the same frozen feature snapshot**

```bash
python3 -m stock_analyze \
  --market a_share \
  --agent codex \
  --as-of 2026-08-07 \
  run-prediction-research \
  --offline \
  --max-full-history-instruments 5000
```

The diagnostic requires a feature/label pair with the same snapshot date.
Never reuse an older label snapshot after rebuilding the feature snapshot.

- [x] **Step 3: Run the data audit before strategy replay**

Write a before/after coverage table for every reason currently present in
`decision.json`. Do not proceed to replay while any A-share audit reason remains.

- [x] **Step 4: Verify snapshot reproducibility**

Rebuild features and labels twice from the same materialization manifest and
require identical feature snapshot, label snapshot, and metadata hashes.

### 8.1 Execution Evidence (2026-08-10)

- Acquisition ran on the ECS source runtime, then the immutable cache files
  were synchronized locally. This replaces only the physical location stated
  in Task 4; the endpoint, range, manifest, and no-force/resume contracts are
  unchanged.
- The 2016-2017 financial warm-up added 5,894 pre-2018 rows for 842 securities,
  so 2018 point-in-time joins do not start with an artificial empty history.
- The adjustment-factor fetch now persists per-security completed windows,
  rejects an empty response inside the active lifecycle, and resumes only the
  missing windows. The materialization gate checks every historical member in
  its membership months plus the preceding 60 trading sessions, both globally
  and per security.
- The first post-review audit found 36 securities missing part of the 2020-10-09
  through 2020-12-31 warm-up. Only those 36 securities were refetched from the
  ECS Tushare source. All 36 succeeded; the final per-security failure count is
  zero. The frozen audit is recorded in
  `reports/research/a_share_adj_factor_backfill_audit_20260810.json`.
- Final historical HS300/ZZ500 union: 1,402 securities. Daily and daily-basic
  partitions: 2,086/2,086 each. Materialized adjustment rows: 2,509,601;
  point-in-time membership coverage: 100%; membership plus 60-session warm-up
  coverage: 99.999766%. Active-lifecycle coverage (94.102470%) is retained as
  an informational metric because non-member dates are outside this frozen
  research scope.
- Materialization was run twice with byte-identical outputs. Manifest SHA-256:
  `8c8b1b316758c23cd91d27606891930bfdb0cd2eb69af23c99d63988db1a0a58`;
  source digest: `0c5d0d1d6f2b9075e68eded177e7df7cad14719a436a211d925eebd833b97536`;
  output digest: `125d6363b03147ccbdcfc696d94c1d5aae828e0b75c964cf691ac98024ab2d2d`.
- Feature build was run twice with byte-identical Parquet and metadata:
  1,668,726 rows, 1,402 instruments, 174 columns. Feature SHA-256:
  `f03d829a910da904de0791597bcf572cb9c7e04578038f1d33f487635b52d211`;
  metadata SHA-256:
  `71f6d1ba10326b7e4ecee961d6a9489c42ef3686334a41ae2ca4037f095360e6`.
- Prediction research was run twice with byte-identical labels, events,
  regimes, and event studies. Label SHA-256:
  `548c645eacfe33d9ebf43b3d359f440ca166d69d6950de2aa230560292a2f453`.
  Event, regime, and event-study SHA-256 values are respectively
  `391ea6651d6b0722f83e52ef34f02c21bbf9269d3c7dea973e7194bcb0748ab1`,
  `37dcd295951b72ef688517a40e1fcdf25fdb7c8b269fc1ccafe5913b1633428f`,
  and `feacdd5ca6c76338e050ee929bf33ada93960d0b87661f76cc316ad032c29650`.
  Recent 60-session coverage for momentum 20/60, MACD histogram percentile,
  and RSI14 is 99.9729%.
- `daily_basic` is now joined only on the exact market date; stale backward
  as-of valuation carry is forbidden. Publication dates must be valid calendar
  dates, and the financial restatement policy must match the declared
  whitelist. Intended and alternate overlays pass separate audits.

## 9. Task 6: Rerun the Frozen Rule Core and Stop Correctly

**Files produced:**
- `data/research/rule_core_diagnostics/20260807/data_audit.json`
- `data/research/rule_core_diagnostics/20260807/a_share_intended.json`
- `data/research/rule_core_diagnostics/20260807/a_share_controls.json`
- `data/research/rule_core_diagnostics/20260807/decision.json`
- `data/research/rule_core_diagnostics/20260807/report.md`

- [x] **Step 1: Run the unchanged diagnostic**

```bash
python3 -m stock_analyze run-rule-core-diagnostic \
  --offline \
  --as-of 20260807
```

- [x] **Step 2: Apply the predeclared decision**

```text
data_blocked        repair only the named source; do not tune
negative_hypothesis archive the A-share core; both current risky hypotheses close
proceed             open existing Stage 2 for A-share + defensive assets only
```

QDII remains excluded because its frozen trend hypothesis already failed.

- [x] **Step 3: Verify exact reproducibility**

Run twice and compare SHA-256 for decision, NAV, trades, attribution, feature
snapshot, label snapshot, and manifests.

- [x] **Step 4: Run the full focused suite**

```bash
python3 -m unittest -v \
  tests.test_backtest_data_prep \
  tests.test_cli_prepare_backtest_data \
  tests.test_a_share_research_materializer \
  tests.test_research_pipeline \
  tests.test_research_source_features \
  tests.test_research_labels \
  tests.test_research_portfolio_replay \
  tests.test_rule_core_diagnostic \
  tests.test_cli_research
```

Expected: all pass, attribution reconciles to NAV, and stderr contains no
unexpected runtime error. The CLI failure-path test intentionally emits its
tested non-zero error message.

### 9.1 Execution Evidence (2026-08-10)

- A-share data audit: `passes=true`, `reasons=[]`; all 14 original blockers
  were removed without changing either frozen overlay. The intended and
  alternate overlay audits both independently report `passes=true` and no
  reasons. Exact-date daily-basic coverage is 99.0118%.
- Final decision: `a_share=negative_hypothesis` and
  `cn_qdii_etf=negative_hypothesis`; no market was admitted to Stage 2.
- A-share intended core: -5.8870% annualized net-return metric, 30.9060%
  maximum drawdown, -0.8028 Sharpe, and 1,283 trades. Alternate overlay:
  -6.0685%, 27.9651%, -0.5247, and 11,363 trades. The fixed `1/N` control:
  +5.1163%, 14.1669%, +0.3121, and 8,157 trades.
- The diagnostic ran twice. SHA-256 matched for all 11 outputs, including
  decision, NAV, trades, attribution, both market controls, both intended
  results, data audit, model gates, and report. Run times were 837.98 and
  830.26 seconds; peak RSS was 14,001,651,712 and 14,007,730,176 bytes;
  both runs reported zero swaps.
- Fresh combined focused suite: 192 tests passed in 75.541 seconds with
  `FutureWarning` promoted to an error. `py_compile`, tracked diff checks, and
  untracked-file trailing-whitespace checks also passed.
- The first independent review rejected four issues: empty active adjustment
  windows, missing pre-membership warm-up enforcement, permissive/stale
  point-in-time evidence, and a control overlay that reused the intended
  audit. All four were fixed with regression tests. One independent post-fix
  review returned `APPROVED`; the original reviewer then found a fifth legacy
  compatibility issue: a coarse completed-range marker could bypass the new
  window validator. An integration test reproduced the zero-call skip. The
  caller now always enters `_fetch_adj`, which keeps valid window progress
  API-idempotent and refetches legacy partial ranges before recording progress.
  The original reviewer inspected the final patch and returned
  `QUALITY_APPROVED`.

## 10. Delivery Boundary

This plan is complete when the A-share result is no longer ambiguous. It does
not promise that the strategy will pass.

Explicitly excluded until `proceed`:

- New factor weights or replacement strategies.
- QDII trend tuning.
- Classical-model or deep-learning training.
- Personal-portfolio Dashboard work.
- ECS strategy timers or activation.
- Announcement-text factors in trading decisions.

If A-share reaches `proceed`, execute Stages 2-3 in
`2026-08-09-personal-quant-single-portfolio.md`. Dashboard and ECS work still
wait for the sealed Stage 3 gate.

## 11. Schedule and Expected Outcome

| Work | Expected duration | Result |
|---|---:|---|
| Status endpoints and materializer | 1-2 working days | Complete deterministic bridge |
| Local historical backfill | 0.5-2 elapsed days | Eight-year point-in-time cache |
| Snapshot rebuild and audit | 0.5-1 working day | Every blocker quantified |
| Frozen rerun and verification | 0.5 working day | One auditable decision |

Engineering target: 3-5 working days, excluding vendor outages or missing
Tushare entitlements. If A-share proceeds, Stage 2-3 needs another 5-7 working
days, followed by 20 and 60 trading days of forward paper evidence.

No daily operator action is expected. User input is needed only if Tushare
reports a missing endpoint entitlement, local free space falls below 15 GB, or
the frozen result closes both hypotheses and a new economic hypothesis must be
chosen.
