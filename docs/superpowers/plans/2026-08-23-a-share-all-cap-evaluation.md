# A 股全市场分层策略测评 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在冻结决策日上复用现有因子、下一开盘模拟器和治理门槛，完成四袖套基线、候选、成本压力与一次性留出集测评。

**Architecture:** 新 campaign adapter 把已验证的季度成员表和每日硬状态转换成现有研究 pipeline 可读的 PIT membership，不改变旧指数研究路径；特征只在候选的决策日生成。所有基线和候选回报写入同一 TrialRegistry，先完成 2018–2024，再由不可变 guard 决定是否允许打开 2025+。

**Tech Stack:** Python、pandas、PyArrow、现有 research pipeline/models/governance/activation、现有 A 股 simulator、unittest。

---

### Task 1: 决策日和 PIT 袖套适配器

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_features.py`
- Modify: `stock_analyze/research/universe.py`
- Test: `tests/test_research_a_share_all_cap_features.py`
- Test: `tests/test_research_universe.py`

- [ ] **Step 1: Write failing decision-calendar tests**

```python
def test_decision_dates_are_strategy_and_sleeve_specific(self) -> None:
    dates = build_decision_calendar(self.open_dates, self.contract)
    self.assertEqual(step_sizes(dates, "claude", "micro"), {20})
    self.assertEqual(step_sizes(dates, "codex", "large"), {5})
    self.assertEqual(step_sizes(dates, "codex", "small"), {10})

def test_membership_effective_date_prevents_same_day_use(self) -> None:
    attached = attach_all_cap_membership(self.features, self.membership)
    self.assertNotIn("000001", attached.loc[attached.trade_date.eq("20240628"), "code"].tolist())
    self.assertIn("000001", attached.loc[attached.trade_date.eq("20240701"), "code"].tolist())
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_features -v`

Expected: import failure for `a_share_all_cap_features`.

- [ ] **Step 3: Implement exact-date filtering and attachment**

```python
def attach_all_cap_membership(features: pd.DataFrame, membership: pd.DataFrame) -> PointInTimeUniverseResult:
    required = {"code", "trade_date"}
    if required.difference(features.columns):
        raise ValueError("all_cap_feature_schema")
    eligible = membership.loc[membership["eligible"].eq(True)].copy()
    eligible["trade_date"] = eligible["effective_date"].astype("string")
    merged = asof_membership_join(features, eligible, by="code", on="trade_date")
    return decorate_all_cap_result(merged, contract_version="pit-all-cap-v1")
```

The adapter must expose `account_id=stable_sleeve`, `research_scope=stable_sleeve`, the configured benchmark, membership snapshot, size rank and eligibility evidence. It must reject duplicate code/effective-date rows and any source date later than signal date.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_features tests.test_research_universe -v`

```bash
git add stock_analyze/research/a_share_all_cap_features.py stock_analyze/research/universe.py tests/test_research_a_share_all_cap_features.py tests/test_research_universe.py
git commit -m "feat: attach PIT all-cap sleeves on frozen decision dates"
```

### Task 2: 同成本基线和透明候选回放

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_campaign.py`
- Test: `tests/test_research_a_share_all_cap_campaign.py`

- [ ] **Step 1: Write failing parity and baseline tests**

```python
def test_runs_every_declared_baseline_on_identical_rows(self) -> None:
    result = run_development_campaign(self.fixture, self.contract)
    self.assertEqual(set(result.trials), {
        "official_sleeve_index", "pit_sleeve_cap_weight", "pit_sleeve_equal_weight",
        "legacy_transparent_scope", "sleeve_router_only", "all_cap_v2",
    })
    self.assertEqual({trial.evaluation_dates for trial in result.trials.values()}, {self.expected_dates})

def test_limit_locked_open_never_fills(self) -> None:
    result = replay_next_open(self.limit_locked_order)
    self.assertEqual(result.filled_shares, 0)
    self.assertEqual(result.status, "limit_locked")
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_campaign -v`

Expected: import failure for `a_share_all_cap_campaign`.

- [ ] **Step 3: Implement the fixed candidate set**

```python
TRIAL_IDS = (
    "official_sleeve_index", "pit_sleeve_cap_weight", "pit_sleeve_equal_weight",
    "legacy_transparent_scope", "sleeve_router_only", "all_cap_v2",
)

def run_development_campaign(inputs: CampaignInputs, contract: AllCapContract) -> CampaignResult:
    folds = build_purged_expanding_folds(inputs.labels, count=4)
    trials = {
        trial_id: replay_trial(trial_id, inputs=inputs, folds=folds, contract=contract)
        for trial_id in TRIAL_IDS
    }
    assert_same_rows_dates_and_costs(trials)
    return CampaignResult(trials=trials, folds=folds)
```

`all_cap_v2` must read factor weights unchanged from the two current overlays, normalize within sleeve, obey the frozen decision intervals, and use the existing target-weight optimizer. The replay must use exact `stk_limit`, PIT status, next-open prices, T+1, lot size, commission, stamp tax, baseline slippage, square-root impact, 2% base participation and 5% hard participation.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_campaign -v`

```bash
git add stock_analyze/research/a_share_all_cap_campaign.py tests/test_research_a_share_all_cap_campaign.py
git commit -m "feat: replay all-cap baselines and transparent candidates"
```

### Task 3: 袖套级指标、容量和不可隐藏准入

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_evaluation.py`
- Modify: `stock_analyze/research/activation.py`
- Test: `tests/test_research_a_share_all_cap_evaluation.py`
- Test: `tests/test_research_activation.py`

- [ ] **Step 1: Write failing independent-sleeve gate tests**

```python
def test_aggregate_cannot_hide_failed_micro_sleeve(self) -> None:
    evidence = passing_evidence_by_sleeve()
    evidence["micro"]["double_cost_net_excess_return"] = -0.001
    report = evaluate_all_cap_gate(evidence, self.contract)
    self.assertFalse(report.passed)
    self.assertEqual(report.sleeves["micro"].reasons, ("double_cost_net_excess_return",))

def test_capacity_gate_counts_adv_and_liquidation_days(self) -> None:
    metrics = capacity_metrics(self.orders, base_adv_fraction=0.02, hard_adv_fraction=0.05)
    self.assertEqual(metrics["orders_within_hard_adv"], 1.0)
    self.assertLessEqual(metrics["maximum_liquidation_days"], 5)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_evaluation -v`

Expected: import failure for `a_share_all_cap_evaluation`.

- [ ] **Step 3: Implement metrics and gate composition**

```python
def evaluate_all_cap_gate(evidence_by_sleeve: Mapping[str, Mapping[str, float]], contract: AllCapContract) -> AllCapGateReport:
    sleeve_reports = {
        sleeve: evaluate_sleeve_gate(metrics, contract.raw["evaluation_gates"])
        for sleeve, metrics in evidence_by_sleeve.items()
    }
    funded = {item.name for item in contract.sleeves if item.capital_weight > 0}
    passed = all(sleeve_reports[name].passed for name in funded)
    return AllCapGateReport(passed=passed, sleeves=sleeve_reports)
```

Compute gross/net return, benchmark excess, Rank IC/ICIR, fold and calendar-year stability, drawdown and benchmark drawdown multiple, turnover, fill rate, cost attribution, 1x/2x cost stress, participation percentiles, liquidation days, concentration, DSR and PBO. The aggregate report must include a fixed-weight 35/30/25/10 account and the CSI All Share comparison, but cannot change a sleeve verdict.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_evaluation tests.test_research_activation -v`

```bash
git add stock_analyze/research/a_share_all_cap_evaluation.py stock_analyze/research/activation.py tests/test_research_a_share_all_cap_evaluation.py tests/test_research_activation.py
git commit -m "feat: gate all-cap evidence per sleeve and capacity"
```

### Task 4: 一次性 holdout guard 和不可变结果

**Files:**
- Create: `stock_analyze/research/a_share_all_cap_holdout.py`
- Test: `tests/test_research_a_share_all_cap_holdout.py`
- Modify: `stock_analyze/cli.py`

- [ ] **Step 1: Write failing guard tests**

```python
def test_holdout_refuses_failed_development_gate(self) -> None:
    with self.assertRaisesRegex(ValueError, "all_cap_holdout:development_gate"):
        open_holdout(self.failed_development, self.contract, self.root)

def test_holdout_refuses_second_open(self) -> None:
    first = open_holdout(self.passing_development, self.contract, self.root)
    with self.assertRaisesRegex(ValueError, "all_cap_holdout:already_opened"):
        open_holdout(self.passing_development, self.contract, self.root)
    self.assertTrue(first["immutable"])
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_holdout -v`

Expected: import failure for `a_share_all_cap_holdout`.

- [ ] **Step 3: Implement content-addressed guard and CLI commands**

```python
def open_holdout(development: Mapping[str, Any], contract: AllCapContract, repo_root: Path) -> dict[str, Any]:
    require_development_pass(development)
    marker = repo_root / "data/research/a_share_all_cap/v1/holdout/opened.json"
    if marker.exists():
        raise ValueError("all_cap_holdout:already_opened")
    payload = build_holdout_authorization(development, contract)
    write_json_exclusive(marker, payload)
    return payload
```

Add `run-a-share-all-cap-development` and `run-a-share-all-cap-holdout`. The holdout command requires the development artifact checksum and exact contract hash, writes the authorization marker before reading holdout returns, and writes pass/fail/insufficient_data without replacing prior output.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_holdout tests.test_cli_research_all_cap -v`

```bash
git add stock_analyze/research/a_share_all_cap_holdout.py stock_analyze/cli.py tests/test_research_a_share_all_cap_holdout.py tests/test_cli_research_all_cap.py
git commit -m "feat: guard one-time all-cap holdout evaluation"
```

### Task 5: Evaluation verification and immutable report

**Files:**
- Create: `docs/superpowers/validation/2026-08-23-a-share-all-cap-development-result.md`

- [ ] **Step 1: Run focused and full verification**

Run: `python3 -m unittest tests.test_research_a_share_all_cap_features tests.test_research_a_share_all_cap_campaign tests.test_research_a_share_all_cap_evaluation tests.test_research_a_share_all_cap_holdout -v`

Run: `git diff --check && python3 -m compileall -q stock_analyze tests && python3 -m unittest discover -s tests`

Expected: all commands exit 0.

- [ ] **Step 2: Run development only**

Run: `/opt/stock-analyze/venv/bin/python -m stock_analyze run-a-share-all-cap-development --contract configs/research/a_share_all_cap_v2.yaml`

Expected: one immutable result with all declared trials, four folds, per-sleeve verdicts and no 2025+ observations.

- [ ] **Step 3: Record the result unchanged**

The validation document must state the contract hash, source/member/feature manifests, all six trial IDs, every sleeve gate, aggregate metrics, DSR/PBO trial count, and one of `pass`, `failed`, or `insufficient_data`. A failed candidate stops; a passing candidate may proceed to the separate holdout command.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/validation/2026-08-23-a-share-all-cap-development-result.md
git commit -m "research: record all-cap development result"
```
