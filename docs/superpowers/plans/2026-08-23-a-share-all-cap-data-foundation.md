# A 股全市场分层数据基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不复制现有全市场行情缓存的前提下，采集并校验参考指数、PIT 行业、涨跌停和历史状态，生成按年分区的季度全市场可投资池、每日硬状态与稳定规模袖套。

**Architecture:** 新 collector 只写 `data/research/a_share_all_cap/v1/sources/`，并通过 checksum manifest 发布；新 universe builder 只读该目录与 `data/shared/backtest_cache/`，输出季度成员和每日硬状态分区。旧 A 股 materializer、正式账户和模型 Registry 不变。

**Tech Stack:** Python 3.11、pandas、PyArrow、Tushare、Baostock、unittest、现有原子写入工具。

---

### Task 1: 机器可读合同加载与路径模型

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_contract.py`
- Test: `tests/test_research_a_share_all_cap_contract.py`
- Read: `configs/research/a_share_all_cap_v2.yaml`

- [ ] **Step 1: Write the failing contract tests**

```python
class AllCapContractTests(unittest.TestCase):
    def test_loads_frozen_boundaries_and_weights(self) -> None:
        contract = load_all_cap_contract(self.root / "configs/research/a_share_all_cap_v2.yaml")
        self.assertEqual(contract.size_boundaries, (300, 800, 1800, 3800))
        self.assertEqual(sum(item.capital_weight for item in contract.sleeves), 1.0)
        self.assertEqual(contract.holdout_policy, "open_once_after_data_code_and_development_gates")

    def test_rejects_non_monotonic_size_boundaries(self) -> None:
        payload = self.valid_payload()
        payload["universe"]["size_rank_boundaries"] = [800, 300, 1800, 3800]
        with self.assertRaisesRegex(ValueError, "all_cap_contract:size_boundaries"):
            parse_all_cap_contract(payload)
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_contract -v`

Expected: import failure for `stock_analyze.research.a_share_all_cap_contract`.

- [ ] **Step 3: Implement immutable dataclasses and validation**

```python
@dataclass(frozen=True)
class SleeveContract:
    name: str
    rank_min: int
    rank_max: int | None
    benchmark: str
    capital_weight: float

@dataclass(frozen=True)
class AllCapContract:
    campaign_id: str
    development_start: date
    development_end: date
    holdout_start: date
    holdout_end: date
    holdout_policy: str
    size_boundaries: tuple[int, int, int, int]
    boundary_buffer_fraction: float
    sleeves: tuple[SleeveContract, ...]
    raw: Mapping[str, Any]

def load_all_cap_contract(path: str | Path) -> AllCapContract:
    return parse_all_cap_contract(load_config(path))
```

Validation must reject non-Research mode, changing window order, capital weights not summing to 1 within `1e-9`, missing benchmark codes, a holdout policy other than the frozen value, and a free-space floor below 0.15.

- [ ] **Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_contract -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/research/a_share_all_cap_contract.py tests/test_research_a_share_all_cap_contract.py configs/research/a_share_all_cap_v2.yaml docs/superpowers/specs/2026-08-23-a-share-all-cap-strategy-v2.md
git commit -m "spec: freeze A-share all-cap research contract"
```

### Task 2: 参考数据 collector 与可验证 manifest

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_sources.py`
- Test: `tests/test_research_a_share_all_cap_sources.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli_research_all_cap.py`

- [ ] **Step 1: Write failing normalization and publication tests**

```python
class AllCapSourceCollectorTests(unittest.TestCase):
    def test_collects_reference_indexes_and_both_industry_states(self) -> None:
        result = collect_all_cap_sources(
            repo_root=self.root,
            pro_client=FakePro(),
            start=date(2018, 1, 2),
            end=date(2024, 12, 31),
        )
        self.assertEqual(result["status"], "complete")
        manifest = load_verified_all_cap_sources(self.root)
        self.assertEqual(set(manifest.index_daily), {"000300.SH", "000905.SH", "000852.SH", "932000.CSI", "000985.CSI"})
        self.assertEqual(set(manifest.industry_membership["is_new"]), {"Y", "N"})

    def test_does_not_advance_latest_after_checksum_failure(self) -> None:
        latest = self.write_existing_latest()
        with self.assertRaisesRegex(ValueError, "all_cap_source_checksum"):
            publish_all_cap_sources(self.corrupt_staging_dir(), self.root)
        self.assertEqual(latest.read_text(), "old")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_sources -v`

Expected: import failure for `a_share_all_cap_sources`.

- [ ] **Step 3: Implement source collection**

```python
REFERENCE_INDEXES = {
    "000300.SH": "large",
    "000905.SH": "mid",
    "000852.SH": "small",
    "932000.CSI": "micro",
    "000985.CSI": "all_share",
}

def collect_all_cap_sources(*, repo_root: Path, pro_client: object, start: date, end: date) -> dict[str, object]:
    root = repo_root / "data/research/a_share_all_cap/v1/sources"
    with staged_directory(root.parent, prefix=".all-cap-sources-") as staging:
        collect_monthly_index_weights(pro_client, staging, start, end)
        collect_index_daily(pro_client, staging, start, end)
        collect_sw2021_membership(pro_client, staging)
        collect_stk_limit(pro_client, staging, start, end)
        manifest = build_source_manifest(staging, start=start, end=end)
        verify_source_manifest(staging, manifest)
        publish_staged_tree(staging, root)
    return {"status": "complete", "manifest": str(root / "manifest.json")}
```

The collector must query `index_member_all(l1_code=industry_code, is_new="Y")` and `is_new="N"` for every one of the 31 L1 codes, deduplicate on `(l1_code,l2_code,l3_code,ts_code,in_date,out_date,is_new)`, and reject overlaps for the same stock/date. It must query `stk_limit` once per open trading date and write compressed yearly parquet. CSI2000 weight collection starts at `2023-09-01`; earlier empty periods are recorded as pre-inception, not fabricated.

- [ ] **Step 4: Add CLI command**

```python
all_cap_sources = sub.add_parser("refresh-a-share-all-cap-sources")
all_cap_sources.add_argument("--start", required=True)
all_cap_sources.add_argument("--end", required=True)

if args.command == "refresh-a-share-all-cap-sources":
    result = collect_all_cap_sources(
        repo_root=Path.cwd(),
        pro_client=_make_tushare_client(),
        start=parse_date(args.start),
        end=parse_date(args.end),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0
```

- [ ] **Step 5: Run source and CLI tests**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_sources tests.test_cli_research_all_cap -v`

Expected: all tests pass and no test reaches the network.

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/research/a_share_all_cap_sources.py stock_analyze/cli.py tests/test_research_a_share_all_cap_sources.py tests/test_cli_research_all_cap.py
git commit -m "feat: collect verified all-cap reference sources"
```

### Task 3: 季度 PIT 可投资池、每日硬状态和稳定规模袖套

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_universe.py`
- Test: `tests/test_research_a_share_all_cap_universe.py`

- [ ] **Step 1: Write failing PIT and buffer tests**

```python
class AllCapUniverseTests(unittest.TestCase):
    def test_excludes_future_listing_st_and_missing_status(self) -> None:
        result = build_review_membership(self.inputs(), review_date="20240628", contract=self.contract)
        rows = result.set_index("code")
        self.assertFalse(rows.loc["000002", "eligible"])
        self.assertIn("not_listed", rows.loc["000002", "exclusion_reasons"])
        self.assertFalse(rows.loc["000003", "eligible"])
        self.assertIn("st", rows.loc["000003", "exclusion_reasons"])
        self.assertFalse(rows.loc["000004", "eligible"])
        self.assertIn("status_missing", rows.loc["000004", "exclusion_reasons"])

    def test_retains_previous_sleeve_inside_ten_percent_boundary_buffer(self) -> None:
        row = assign_stable_sleeve(size_rank=315, previous="large", boundaries=(300, 800, 1800, 3800), buffer_fraction=0.10)
        self.assertEqual(row, "large")
        row = assign_stable_sleeve(size_rank=331, previous="large", boundaries=(300, 800, 1800, 3800), buffer_fraction=0.10)
        self.assertEqual(row, "mid")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_universe -v`

Expected: import failure for `a_share_all_cap_universe`.

- [ ] **Step 3: Implement eligibility and stable assignment**

```python
MEMBERSHIP_COLUMNS = (
    "review_date", "effective_date", "code", "eligible", "exclusion_reasons",
    "size_rank", "raw_sleeve", "stable_sleeve", "total_mv", "circ_mv",
    "avg_amount_252", "non_trading_days_252", "industry_l1", "industry_l2",
    "industry_l3", "industry_source_date", "status_source", "universe_contract_version",
)

def assign_stable_sleeve(*, size_rank: int, previous: str | None, boundaries: tuple[int, int, int, int], buffer_fraction: float) -> str:
    raw = raw_sleeve_for_rank(size_rank, boundaries)
    if previous is None or previous == raw:
        return raw
    lower, upper = buffered_rank_interval(previous, boundaries, buffer_fraction)
    return previous if lower <= size_rank <= upper else raw
```

`build_review_membership` must compute lifecycle and listing age as of the review date, use only trailing data ending on that date, require new entries to remain above the 20th amount percentile while retaining existing members above the 10th percentile, use SW intervals with `in_date <= review_date < out_date`, assign ranks above 3,800 to unfunded `nano_watch`, and apply the previous published sleeve only after raw eligibility is established. A separate daily hard-status partition applies ST, delisting, suspension and limit data between quarterly reviews.

- [ ] **Step 4: Implement partitioned publication and verification**

```python
def materialize_all_cap_universe(*, repo_root: Path, contract: AllCapContract) -> dict[str, object]:
    sources = load_verified_all_cap_sources(repo_root)
    cache = verify_shared_backtest_cache(repo_root, contract.development_start, contract.development_end)
    output = repo_root / "data/research/a_share_all_cap/v1/universe"
    partitions = build_year_partitions(cache=cache, sources=sources, contract=contract)
    manifest = publish_parquet_partitions(output, partitions, key_columns=("review_date", "code"))
    verify_membership_manifest(output, manifest, minimum_free_fraction=0.15)
    return manifest
```

The writer must estimate final bytes before replacing `latest.json`, reject duplicate `(review_date, code)`, require all critical text columns to use explicit string dtype, and preserve old verified partitions on failure.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_universe -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/research/a_share_all_cap_universe.py tests/test_research_a_share_all_cap_universe.py
git commit -m "feat: materialize PIT all-cap sleeve membership"
```

### Task 4: 全市场状态与财报缺口补齐作业

**Files:**
- Modify: `docs/system-harness.md`
- Modify: `scripts/system-audit.sh`
- Test: `tests/test_system_audit_script.py`

- [ ] **Step 1: Add failing audit assertions**

```python
self.assertIn("a_share_all_cap_source_manifest", output)
self.assertIn("a_share_all_cap_universe_manifest", output)
self.assertIn("backtest_statement_code_coverage", output)
self.assertIn("backtest_status_code_coverage", output)
```

- [ ] **Step 2: Run the audit-script test and verify failure**

Run: `python3 -m unittest tests.test_system_audit_script -v`

Expected: the four all-cap checks are absent.

- [ ] **Step 3: Extend the audit without reading formal ledgers as scratch**

The audit must report, as counts rather than file names, source manifest checksum status, membership years, active/delisted stock master counts, statement completed codes, Baostock status completed ranges, filesystem free fraction, and the result `PASS`, `WARN`, or `FAIL`. Missing PIT status is `FAIL`; a free fraction below 0.15 is `FAIL` before publication.

- [ ] **Step 4: Document exact resumable production commands**

```bash
/opt/stock-analyze/venv/bin/python -m stock_analyze prepare-backtest-data \
  --start 2018-01-02 --end 2026-08-21 \
  --phases statements --code-scope all

/opt/stock-analyze/venv/bin/python -m stock_analyze prepare-backtest-data \
  --start 2018-01-02 --end 2026-08-21 \
  --phases status --code-scope all --status-provider baostock

/opt/stock-analyze/venv/bin/python -m stock_analyze refresh-a-share-all-cap-sources \
  --start 2018-01-02 --end 2024-12-31
```

The runbook must require a pre-run disk check, a transient systemd unit with captured journal, post-run manifest verification, and no use of `--force` for resumable gaps.

- [ ] **Step 5: Run tests and audits**

Run: `python3 -m unittest tests.test_system_audit_script -v`

Run: `./scripts/system-audit.sh`

Expected: local script tests pass; missing local data is a controlled warning, not a fabricated pass.

- [ ] **Step 6: Commit**

```bash
git add docs/system-harness.md scripts/system-audit.sh tests/test_system_audit_script.py
git commit -m "ops: audit all-cap research data readiness"
```

### Task 5: Full data-foundation verification

**Files:**
- Modify: `docs/superpowers/validation/2026-08-23-a-share-all-cap-data-readiness.md`

- [ ] **Step 1: Run targeted tests**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_contract tests.test_research_a_share_all_cap_sources tests.test_research_a_share_all_cap_universe tests.test_cli_research_all_cap tests.test_system_audit_script -v`

Expected: all tests pass.

- [ ] **Step 2: Run repository verification**

Run: `git diff --check`

Run: `python3 -m compileall -q stock_analyze tests`

Run: `python3 -m unittest discover -s tests`

Expected: all commands exit 0.

- [ ] **Step 3: Write immutable readiness evidence**

Record source checksums, date bounds, row/code counts, coverage by year and sleeve, missing-status count, statement completion counts, filesystem bytes before/after, and the final `ready` or `insufficient_data` decision. Do not include secret values or absolute secret paths.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/validation/2026-08-23-a-share-all-cap-data-readiness.md
git commit -m "docs: record all-cap data readiness evidence"
```
