# Personal Quant Falsification-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop dual-strategy and multi-model racing, first determine whether the existing A-share value/quality core and QDII trend core have honest net-of-cost economic value, and only then build one personal paper portfolio around the evidence that survives.

**Architecture:** Reuse current point-in-time data, signal generation, simulator, execution costs, attribution, and Dashboard infrastructure. Split portfolio replay into an explicit rule contract and model contract; rules rebalance mechanically from rank/buffer targets while models additionally require forecast edge to cover cost and uncertainty. The active path becomes one portfolio with zero, one, or two validated risky sleeves plus a defensive sleeve; ML and announcement intelligence remain optional overlays after 60 forward paper days.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn, JSON/YAML, CSV/Parquet/SQLite, unittest, React/Vite, systemd, Tushare/iFinD market data, existing paper settlement and cost models.

---

## 中文结论

这版计划已经根据两轮独立反方评审和源码复核重写。最重要的变化不是参数，而是顺序：

1. 先修收益计算和回放契约。
2. 三个工作日内，只回放两个预先指定的规则核：A股价值/质量/低波核心，以及QDII跨境ETF趋势核心。
3. 它们是两个经济假设，不是参赛选手。另两套旧规则只做固定对照，不能因为某段历史赢了就替补晋升。
4. 某个核心在开发窗口扣费后没有正收益和正超额，该 sleeve 就停止，不为它开发新页面、定时任务或换一组权重继续尝试。
5. 至少一个核心通过前置证伪后，才创建单组合；唯一一次封存回测通过后，才改 Dashboard 和 ECS 调度。

第一阶段最多约3.5个工作日就会得到一个明确结论：`proceed`、`negative_hypothesis` 或 `data_blocked`。

## 0. Review Provenance

2026-08-09 使用本机 `claude` CLI 进行了两轮只读反方评审，并由 Codex 逐行复核关键源码。需要明确记录：当前用户级 Claude Code 配置把 `opus` 路由到 `glm-5.2[1M]`；绕过用户配置直连 Anthropic OAuth 返回 `401 OAuth access token has been revoked`。因此这是“Claude Code 当前配置提供方”的评审，不应冒充 Anthropic Claude 原生模型评审。

评审中被采纳的意见都已由当前源码或数据产物独立验证；未验证的观点没有写成事实。

## 1. Corrected Current Diagnosis

### 1.1 Confirmed defects

1. `stock_analyze/research/portfolio_replay.py` compounds arithmetic daily active returns and annualizes them as `net_excess_return`. Canonical cumulative excess must be relative wealth.
2. `stock_analyze/research/classical_tournament.py` hardcodes `baseline_cash.net_excess_return=0`, ignoring cash yield minus benchmark return.
3. Development and sealed-final metrics are merged before activation evaluation, obscuring which window supplied each gate.
4. The development walk-forward path at `stock_analyze/research/models.py:1398-1399` replaces raw ranking with edge-calibrated expected returns. When calibration is unavailable, development ranking becomes all zero.

### 1.2 Corrected zero-trade explanation

The original plan incorrectly claimed that sealed-final rankings were also overwritten. They are not:

- `ModelBundle.predict_ranking_score()` returns the raw cross-sectional score.
- `classical_tournament.py` places that raw score into `evaluation["score"]`.
- Separately, `predict_excess_return()` uses `EdgeCalibrator.predict_distribution()`.
- An unavailable calibrator returns expected return `0` and uncertainty `1`.
- `apply_cost_aware_transition()` rejects the trade because expected edge cannot cover uncertainty and cost.

The latest 2026-08-07 artifacts confirm that every candidate has `trade_count=0` and `no_trade_reason=insufficient_net_edge`. Two HS300 candidates had an available calibrator and still generated zero trades, so the expected-return mapping or economic gate may genuinely be saying there is no tradable model edge.

This does not prove the underlying rule strategy is bad. It proves rule strategies and prediction models were evaluated through the wrong shared decision contract.

### 1.3 Still unknown

- Whether the A-share value/quality/low-volatility rule core has positive net excess return on an honest replay.
- Whether the QDII trend core has positive net excess after premium, liquidity, tracking, and execution costs.
- Whether historical HS300/ZZ500 membership and delisted QDII coverage are sufficient to avoid material survivorship bias.
- Whether a combined portfolio can reach the final return and drawdown gates.

No new sleeve should be built until these questions are answered.

## 2. External Research Constraints

- Mature research starts from one cause-and-effect hypothesis and constrains experiments. QuantConnect flags more than 30 backtests or more than 10 parameters as increasingly exposed to overfitting: [Research Guide](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/research-guide).
- Universe, alpha, portfolio construction, risk, and execution should have explicit contracts: [Algorithm Framework](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview).
- Qlib reports both signal metrics and executable portfolio metrics; RankIC alone proves neither profitability nor failure: [Qlib benchmarks](https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md).
- Time-series trend has broad cross-asset evidence, while volatility-managed exposure can improve risk-adjusted performance: [AQR time-series momentum](https://www.aqr.com/insights/research/journal-article/time-series-momentum), [NBER volatility-managed portfolios](https://www.nber.org/papers/w22208).
- China-specific behavior must be tested locally. Weekly/monthly A-share momentum evidence is weak, and most replicated anomalies do not produce significant A-share spreads: [NBER China momentum](https://www.nber.org/papers/w31839), [A-share anomaly replication](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4365416).

These sources justify hypotheses; they do not replace point-in-time, net-of-cost replay.

## 3. Two Replay Contracts

### 3.1 Rule replay

Used by existing rule cores and `personal_quant_v1`.

Required inputs:

```text
trade_date, code, name, score, entry_price, benchmark_entry_price,
universe_eligibility, liquidity, volatility, account/market contract
```

- Rank and hold buffer create the frictionless target.
- Capped equal/inverse-volatility sizing converts targets to weights.
- Industry/exposure caps, no-trade band, and turnover cap adjust weights.
- A target change above the no-trade band creates an order; hard exits bypass the band.
- There is no forecast-edge gate because a rule score is a rank, not a calibrated return forecast.
- Real costs, next-open execution, lot size, suspension, limits, settlement, and cash remain enabled.

### 3.2 Model replay

Used only for a future Base versus Base+Model paired test. It additionally requires:

```text
expected_excess_return, prediction_uncertainty, calibration_status,
model_version, feature_schema_hash, dataset_hash
```

```python
trade_allowed = hard_risk_exit or (
    target_change >= no_trade_band
    and expected_benefit > 1.5 * round_trip_cost + uncertainty
)
```

Unavailable model calibration fails closed to no model-driven trade. It never blocks the Base rule portfolio.

### 3.3 Canonical metrics

```python
periods_per_year = 252
daily_rf = (1.0 + annual_rf) ** (1.0 / periods_per_year) - 1.0

portfolio_cagr = (portfolio_nav_end / portfolio_nav_start) ** (
    periods_per_year / observed_trading_days
) - 1.0

benchmark_cagr = (benchmark_nav_end / benchmark_nav_start) ** (
    periods_per_year / observed_trading_days
) - 1.0

cumulative_relative_wealth = (
    (portfolio_nav_end / portfolio_nav_start)
    / (benchmark_nav_end / benchmark_nav_start)
    - 1.0
)

annualized_excess_wealth = (1.0 + cumulative_relative_wealth) ** (
    periods_per_year / observed_trading_days
) - 1.0

sharpe = (
    (daily_portfolio_return - daily_rf).mean()
    / daily_portfolio_return.std(ddof=1)
    * periods_per_year ** 0.5
)
```

Arithmetic active returns remain available for tracking error and information ratio, but are never compounded as cumulative excess.

## 4. Six-Stage Delivery

| Stage | Duration | Purpose | Stop condition |
|---|---:|---|---|
| 0 | 0.5 day | Repair metric truth and split replay contracts | Tests fail or NAV cannot reconcile |
| 1 | Up to 3 days | Falsify the two intended rule cores on development data | `negative_hypothesis` or `data_blocked` |
| 2 | 3-4 days, conditional | Build one portfolio from surviving sleeves | No surviving sleeve |
| 3 | 2-3 days, conditional | Walk-forward validation and one sealed final test | `paper_only_fail` |
| 4 | 2-3 days, conditional | Runner, Dashboard, ECS timers, browser verification | Incomplete same-date evidence |
| 5 | 20 trading days | Provisional forward paper evidence | Operational or risk failure |
| 6 | 60 trading days | Stable evidence and legacy active-route retirement | Evidence remains insufficient |

Stages 2-6 do not begin merely because Stage 0 tests pass.

## 5. Stage 0: Repair Evaluation Truth

**Files:**
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/classical_tournament.py`
- Modify: `stock_analyze/research/edge_calibration.py`
- Test: `tests/test_research_portfolio_replay.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_classical_tournament.py`
- Test: `tests/test_research_edge_calibration.py`

- [x] **Step 1: Add a hand-computed relative-wealth test**

```python
def test_cumulative_excess_is_relative_wealth(self) -> None:
    portfolio = pd.Series([100.0, 110.0, 99.0])
    benchmark = pd.Series([100.0, 105.0, 105.0])
    actual = cumulative_relative_wealth(portfolio, benchmark)
    self.assertAlmostEqual(actual, 99.0 / 105.0 - 1.0)
```

- [x] **Step 2: Run the focused test and verify the old metric fails**

Run: `python3 -m unittest -v tests.test_research_portfolio_replay`

Expected: the new assertion fails before implementation.

- [x] **Step 3: Persist NAV-based return metrics**

Store `portfolio_nav`, `benchmark_nav`, `portfolio_cagr`, `benchmark_cagr`, `cumulative_relative_wealth`, and `annualized_excess_wealth`. Replace every cumulative or annualized compounding of `active_return`.

- [x] **Step 4: Correct the cash baseline**

```python
cash_period_return = (1.0 + annual_rf) ** (1.0 / 252.0) - 1.0
cash_nav = (1.0 + cash_period_return) ** np.arange(len(reference_dates) + 1)
```

Evaluate cash against the same benchmark dates and rebalance convention.

- [x] **Step 5: Preserve raw ranking in development evaluation**

`ranking_score` always stores the raw cross-sectional ranking. Calibration writes separate `expected_excess_return` and `prediction_uncertainty`; unavailable calibration cannot overwrite RankIC inputs.

- [x] **Step 6: Split development and final evidence**

```python
report = {
    "development_selection": development_metrics,
    "sealed_final_evaluation": final_metrics,
    "activation_evidence": activation_metrics,
}
```

Activation fields identify their source window. No merged dictionary may silently supply development IC and final portfolio return under one label.

- [x] **Step 7: Add explicit replay functions**

```python
def replay_rule_portfolio(frame, contract):
    rule_frame = frame.drop(
        columns=["expected_excess_return", "prediction_uncertainty_bps"],
        errors="ignore",
    )
    return replay_executable_portfolio(
        rule_frame,
        contract=contract,
        execution_policy=None,
    )

def replay_model_portfolio(frame, contract):
    required = {"expected_excess_return", "prediction_uncertainty_bps"}
    if not required.issubset(frame.columns):
        raise ValueError("model_replay_missing_economic_prediction")
    return replay_executable_portfolio(frame, contract=contract)
```

- [x] **Step 8: Run focused tests**

Run: `python3 -m unittest -v tests.test_research_portfolio_replay tests.test_research_models tests.test_research_classical_tournament tests.test_research_edge_calibration`

Expected: all pass, rule replay trades from a ranked fixture, model replay fails closed without economic predictions, and NAV identities reconcile.

## 6. Stage 1: Three-Day Rule-Core Falsifier

### 6.1 Predeclared hypotheses

These are not four racing candidates:

| Market | Intended core | Hypothesis | Fixed control |
|---|---|---|---|
| A-share | `configs/agents/claude_a_share.yaml` | Value, quality, low volatility, and low turnover produce positive net excess | `codex_a_share.yaml`, 1/N, benchmark |
| QDII | `configs/agents/codex_cn_qdii_etf.yaml` | Trend across domestic cross-border ETFs produces positive net excess | `claude_cn_qdii_etf.yaml`, 1/N, benchmark |

The control cannot replace the intended core merely because it has a better development-period return. A failed intended core closes that hypothesis; changing economic direction requires a new versioned research plan.

### 6.2 Data window and audit

- Use only the oldest 60% chronological development window.
- Do not open the next 20% validation or newest 20% sealed final window.
- A-share requires at least eight calendar years of point-in-time history for eventual admission.
- Report `data_blocked` when coverage is too weak; never report strategy failure from biased data.

Audit:

- Historical HS300/ZZ500 membership coverage by date.
- Delisted/ST/suspended security coverage.
- Financial publication-date coverage and restatement handling.
- QDII listed/delisted universe by year.
- Fund name, underlying exposure, NAV/premium, liquidity, and tracking-data coverage.
- Missing entry-price and benchmark-price days.

Minimum gate:

```text
historical membership coverage >= 95%
entry and benchmark price coverage >= 98%
eligible security/fund names = 100%
financial publication-date coverage >= 95% for required A-share factors
```

### 6.3 Files and steps

**Files:**
- Create: `stock_analyze/research/rule_core_diagnostic.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_rule_core_diagnostic.py`
- Test: `tests/test_cli_research.py`

- [x] **Step 1: Add a deterministic diagnostic fixture**

The fixture contains one profitable intended core, one losing control, real costs, and a missing-membership case. Assert that the intended core is judged independently and the control never becomes the chosen core.

- [x] **Step 2: Implement the diagnostic command**

```text
python3 -m stock_analyze run-rule-core-diagnostic --offline --as-of 20260807
```

Output under `data/research/rule_core_diagnostics/20260807/`:

```text
data_audit.json
a_share_intended.json
a_share_controls.json
qdii_intended.json
qdii_controls.json
nav.parquet
trades.parquet
attribution.parquet
decision.json
report.md
```

- [x] **Step 3: Use rule replay only**

Do not synthesize `expected_excess_return=1`. Do not call the model economic gate. Real execution costs and account mechanics remain enabled.

- [x] **Step 4: Report model-gate diagnostics separately**

For current model artifacts, report calibrated expected benefit, round-trip cost, uncertainty, calibration availability, and rejection reasons. This cannot alter rule-core results or relax model gates.

- [x] **Step 5: Apply the Stage 1 decision per intended core**

```python
def development_hypothesis_status(metrics: Metrics, audit: DataAudit) -> str:
    if not audit.passes:
        return "data_blocked"
    if metrics.trade_count == 0:
        return "negative_hypothesis"
    if metrics.portfolio_cagr <= 0.0:
        return "negative_hypothesis"
    if metrics.annualized_excess_wealth <= 0.0:
        return "negative_hypothesis"
    if metrics.total_execution_cost >= metrics.gross_profit:
        return "negative_hypothesis"
    return "proceed"
```

- [x] **Step 6: Enforce the stop rule**

- Both intended cores fail: stop and publish two negative hypotheses.
- Only A-share passes: Stage 2 may build A-share plus defensive assets only.
- Only QDII passes: Stage 2 may build QDII plus defensive assets only.
- Both pass: Stage 2 may build the provisional 55/35/10 portfolio.
- Any core is `data_blocked`: repair data only; do not tune signals.

- [x] **Step 7: Run tests and one real development diagnostic**

Run: `python3 -m unittest -v tests.test_rule_core_diagnostic tests.test_research_portfolio_replay tests.test_cli_research`

Expected: tests pass and the real run ends in one of the three explicit statuses without opening validation/final data.

## 7. Stage 2: Build One Portfolio, Conditionally

Stage 2 starts only for sleeves whose Stage 1 status is `proceed`. A failed or
missing risky sleeve is replaced by the defensive allocation; its budget is
never reassigned to the other risky sleeve merely to keep capital invested.

### 7.1 Allocation prior

| Sleeve | Initial target | Hard cap | Failure behavior |
|---|---:|---:|---|
| A-share | 55% | 60% | Move the full sleeve weight to defensive assets |
| Cross-border QDII ETF | 35% | 40% | Move the full sleeve weight to defensive assets |
| Defensive | 10% minimum | 100% | Absorb unused and risk-reduced capital |

These weights are declared priors, not estimates. An inverse-volatility
allocation is calculated once as a sanity report, but it cannot optimize or
replace the frozen allocation before the sealed test.

### 7.2 A-share sleeve contract

- Reuse the intended value/quality/low-vol core that passed Stage 1.
- Use point-in-time fundamentals by publication date; no latest-value joins.
- Rebalance entries on the declared schedule while allowing daily hard exits.
- Enter at rank 15 or better and retain until rank falls below 25.
- Hold 15-20 stocks, with a 7.5% per-name cap and 25% industry cap.
- Allocate within the sleeve by inverse volatility, subject to those caps.
- Suppress trades smaller than 1% of portfolio NAV.
- Enforce an 8% one-way daily portfolio-turnover limit.

### 7.3 QDII sleeve contract

- Reuse the intended trend core that passed Stage 1.
- Maintain Chinese security names and an underlying-exposure group for every ETF.
- Admit only funds that pass liquidity, premium/NAV, tracking, and executable-price checks.
- Rank weekly, but evaluate hard exits every trading day.
- After a hard exit, apply a 10-trading-day cooldown to the same underlying exposure.
- Cap each order at 1% of trailing average daily traded value.
- Trip a hard breaker when projected annualized one-way turnover exceeds 6x.
- Hold the top 3-5 eligible funds; if fewer than two have positive trend, hold defensive assets.

### 7.4 Files and steps

**Files:**
- Create: `configs/personal_quant_v1.yaml`
- Create: `stock_analyze/personal_quant/__init__.py`
- Create: `stock_analyze/personal_quant/config.py`
- Create: `stock_analyze/personal_quant/contracts.py`
- Create: `stock_analyze/personal_quant/a_share.py`
- Create: `stock_analyze/personal_quant/qdii.py`
- Create: `stock_analyze/personal_quant/allocator.py`
- Create: `stock_analyze/personal_quant/risk.py`
- Create: `stock_analyze/personal_quant/storage.py`
- Test: `tests/test_personal_quant_config.py`
- Test: `tests/test_personal_quant_sleeves.py`
- Test: `tests/test_personal_quant_allocator.py`
- Test: `tests/test_personal_quant_storage.py`

- [ ] **Step 1: Freeze the configuration before validation**

The configuration records universe versions, factor weights, ranking buffers,
allocation weights, risk limits, transaction-cost assumptions, and a content
hash. Validation and final reports must echo the same hash.

- [ ] **Step 2: Define immutable decision contracts**

```python
@dataclass(frozen=True)
class TargetPosition:
    account: str
    ts_code: str
    name: str
    target_weight: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioDecision:
    as_of: str
    config_hash: str
    targets: tuple[TargetPosition, ...]
    risk_flags: tuple[str, ...]
```

The decision artifact is the only input accepted by the paper-execution layer.

- [ ] **Step 3: Isolate portfolio state**

Write new strategy state under `data/personal_quant/` and reports under
`reports/personal_quant/`. Never reuse or reset competition account state.

- [ ] **Step 4: Implement sleeve target generation**

Each sleeve emits target weights and reason codes. It does not create orders,
mutate cash, or read another strategy's state.

- [ ] **Step 5: Apply portfolio allocation and risk scaling**

The allocator combines only passed sleeves, inserts the defensive residual,
then applies name, industry, liquidity, turnover, and portfolio-volatility
limits in a deterministic order.

- [ ] **Step 6: Add storage and idempotency tests**

The same `(as_of, config_hash, data_snapshot_hash)` must reproduce the same
decision and cannot append duplicate ledger rows.

- [ ] **Step 7: Run focused tests**

Run: `python3 -m unittest -v tests.test_personal_quant_config tests.test_personal_quant_sleeves tests.test_personal_quant_allocator tests.test_personal_quant_storage`

Expected: all tests pass, failed sleeves remain defensive, and no competition
files are written.

## 8. Stage 3: Validation and One Sealed Final Test

### 8.1 Strategic benchmark

For a two-sleeve portfolio, use this frozen monthly-rebalanced benchmark:

```yaml
benchmark:
  000300.SH: 0.275
  000905.SH: 0.275
  513100.SH: 0.175
  159920.SZ: 0.175
  CASH: 0.10
  rebalance: monthly
```

Charge realistic initial and rebalance costs. If a sleeve does not pass Stage
1, assign its corresponding benchmark weight to cash before opening validation;
do not retrospectively choose a more favorable benchmark.

### 8.2 Data split and sufficiency

- Oldest 60%: Stage 1 development and diagnostics.
- Next 20%: validation, opened only after configuration freeze.
- Newest 20%: final test, opened exactly once.
- If point-in-time QDII history has fewer than five executable years, mark the
  sleeve `provisional_only`. It cannot independently pass the historical gate,
  and its weight remains defensive in the historically admitted portfolio.

### 8.3 Final admission gate

All mandatory conditions must pass on the sealed final period:

```text
portfolio CAGR >= 6%
annualized relative-wealth excess >= 2 percentage points
annualized Sharpe of daily portfolio return over risk-free >= 0.70,
with annual risk-free rate = 2%
maximum drawdown <= 15%
return under 2.5x transaction-cost stress > 0
predeclared-neighbor pass ratio >= 70%
non-overlapping 12-month positive-window ratio >= 65%, only when >= 5 windows
```

When fewer than five non-overlapping 12-month windows exist, report rolling
12-month stability but do not treat it as a pass/fail gate.

The report must show the effective joint requirement implied by return,
relative-wealth excess, and Sharpe. These conditions are stricter together,
but they are not mathematically contradictory.

### 8.4 Neighbor and ablation discipline

Define no more than ten neighbors before validation:

- A-share/QDII allocation shifted by 5 percentage points in either direction.
- Largest factor weight shifted 5 percentage points toward the second-largest.
- Portfolio target volatility shifted by 1 percentage point.
- Entry/hold rank buffer shifted by two ranks.

Neighbor pass/fail means CAGR at least 6% and maximum drawdown at most 15%.
Sharpe and excess are reported for diagnosis and cannot be used to add more
neighbors after seeing results.

Run these mandatory ablations:

```text
A-share only versus its benchmark allocation
QDII only versus its benchmark allocation
full portfolio versus defensive allocation
without volatility scaling
without rank buffer and cooldown
base costs versus 2.5x costs
```

### 8.5 Files and steps

**Files:**
- Create: `stock_analyze/personal_quant/backtest.py`
- Create: `stock_analyze/personal_quant/metrics.py`
- Create: `stock_analyze/personal_quant/research_ledger.py`
- Test: `tests/test_personal_quant_backtest.py`
- Test: `tests/test_personal_quant_metrics.py`
- Test: `tests/test_personal_quant_research_ledger.py`
- Modify: `stock_analyze/cli.py`

- [ ] **Step 1: Register the immutable hypothesis**

Persist the configuration hash, data split boundaries, benchmark, metrics,
neighbors, ablations, and gate before validation is read.

- [ ] **Step 2: Implement point-in-time replay**

Reuse the corrected rule replay contract, real costs, executable next-session
prices, trading calendars, suspension/limit constraints, and publication dates.

- [ ] **Step 3: Limit validation work**

Allow no more than 30 predeclared validation and ablation runs. Any changed
hypothesis receives a new ledger entry and cannot reuse the old sealed final.

- [ ] **Step 4: Open the final period once**

Write a tamper-evident result containing data snapshot hash, config hash,
metrics, trade ledger, attribution, and pass/fail reason.

- [ ] **Step 5: Stop on failure**

A failed sealed final becomes `paper_only_fail`. Do not tune against it, promote
it, or replace it with the best validation neighbor.

- [ ] **Step 6: Run tests and verify artifact reproducibility**

Run: `python3 -m unittest -v tests.test_personal_quant_backtest tests.test_personal_quant_metrics tests.test_personal_quant_research_ledger`

Expected: a second run with identical snapshots reproduces hashes and metrics;
the final partition cannot be reopened as development data.

## 9. Stage 4: Productize Only After Admission

Do not spend time on production scheduling or a primary Dashboard before Stage
3 passes. A failed strategy keeps research artifacts only.

### 9.1 Runtime commands

```text
python3 -m stock_analyze personal-backtest --config configs/personal_quant_v1.yaml
python3 -m stock_analyze personal-run-daily --as-of YYYYMMDD
python3 -m stock_analyze personal-run-weekly --as-of YYYYMMDD
python3 -m stock_analyze personal-run-monthly --month YYYY-MM
```

- Daily: refresh prices, execute due paper orders, evaluate hard exits, update
  NAV/risk/attribution, and render same-date operational evidence.
- Weekly: refresh rankings, generate target transitions, and create paper orders.
- Monthly: rebalance strategic sleeve weights, produce a causal review, and
  record whether evidence supports keeping the frozen strategy.
- None of these commands changes parameters automatically.

### 9.2 Dashboard information architecture

Keep the current dark visual language and make the admitted personal portfolio
the primary path:

```text
Portfolio overview
  -> NAV, benchmark, drawdown, risk budget, active alerts
Holdings and decisions
  -> sleeve grouping, target/current weight, reason codes, trade markers
Attribution and review
  -> market selection, sizing, turnover, cost, exits, benchmark effect
Research evidence
  -> frozen version, split, gate, ablations, neighbor stability
Operations
  -> daily/weekly/monthly runs, freshness, failures, artifact links
```

The historical dual-agent competition remains available as a secondary archive,
not as the default navigation model.

### 9.3 API and UI files

**Files:**
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `dashboard/src/App.tsx`
- Create: `dashboard/src/pages/PersonalPortfolioPage.tsx`
- Create: `dashboard/src/components/personal/PortfolioOverview.tsx`
- Create: `dashboard/src/components/personal/HoldingsBySleeve.tsx`
- Create: `dashboard/src/components/personal/AttributionReview.tsx`
- Create: `dashboard/src/components/personal/ResearchEvidence.tsx`
- Create: `dashboard/src/components/personal/RunHealth.tsx`
- Test: `tests/test_dashboard_api_personal_quant.py`
- Test: `dashboard/src/__tests__/personal-portfolio.test.tsx`

Split endpoints by ownership and freshness rather than returning one aggregate:

```text
/api/personal/summary
/api/personal/nav
/api/personal/holdings
/api/personal/decisions
/api/personal/attribution
/api/personal/research
/api/personal/runs
```

### 9.4 ECS services and schedule

**Files:**
- Create: `deploy/systemd/stock-personal-daily.service`
- Create: `deploy/systemd/stock-personal-daily.timer`
- Create: `deploy/systemd/stock-personal-weekly.service`
- Create: `deploy/systemd/stock-personal-weekly.timer`
- Create: `deploy/systemd/stock-personal-monthly.service`
- Create: `deploy/systemd/stock-personal-monthly.timer`
- Modify: `scripts/sync-to-ecs.sh`
- Modify: `docs/operations-runbook.md`

Proposed Asia/Shanghai schedule:

| Job | Schedule | Purpose |
|---|---|---|
| Market-data readiness | Trading days 18:00 | Confirm same-date prices and calendars |
| Personal daily | Trading days 18:30 | Execute due paper orders and update NAV/risk |
| Personal weekly | Friday 20:30 | Refresh rankings and target transitions |
| Personal monthly | Days 1-7 at 21:15 | Idempotently run on the first trading day |

Install and validate new units before disabling legacy active units. Deployment
passes only when the same date has run-ledger, NAV, positions, decisions, orders,
attribution, healthy service state, and HTTP 200 Dashboard evidence.

### 9.5 Verification

- [ ] Run all focused Python tests.
- [ ] Run `npm test` and `npm run build` in `dashboard/`.
- [ ] Start the local API and frontend; inspect desktop and mobile views.
- [ ] Verify each endpoint independently, including empty/error/loading states.
- [ ] Deploy to ECS and perform one online paper canary.
- [ ] Reread same-date artifacts and check active/failed systemd units.

## 10. Stages 5-6: Paper Evidence and Legacy Retirement

### 10.1 First 20 trading days

- Run the admitted configuration unchanged.
- Review execution feasibility, stale data, turnover, costs, rejected orders,
  benchmark drift, and reason-code accuracy.
- Do not auto-adjust parameters from short-run PnL.
- Mark the strategy `provisional` throughout this period.

### 10.2 First 60 trading days

Require complete scheduled-run evidence and no unresolved state corruption,
look-ahead findings, or repeated operational failures. Track realized cost,
turnover, drawdown, exposure, attribution, and benchmark-relative behavior.

This is an operational stability gate, not a promise that 60 live days prove
long-run alpha.

### 10.3 Retire legacy active paths

After 60 stable trading days, complete the following within ten working days:

- Remove competition/tournament jobs from active scheduling.
- Remove legacy competition routes from primary Dashboard navigation.
- Stop treating `configs/agents/*` as inputs to the admitted portfolio.
- Move active legacy entry points behind an explicit archive/compatibility boundary.
- Preserve all historical NAV, orders, trades, reports, audit logs, and immutable
  research artifacts permanently.

Never reset paper losses or destructively rewrite historical evidence.

## 11. Optional Model and Intelligence Overlays

These are post-Stage-6 enhancements. The base portfolio must work without an
LLM provider, semantic event pipeline, or machine-learning model.

### 11.1 Classical ML residual ranker

- Train one LightGBM residual ranker per active sleeve, not one cross-market model.
- Limit its blend weight to 20% of the sleeve score.
- Use walk-forward folds with point-in-time features and purging around labels.
- Keep rule and model replays separate; unavailable calibration fails closed.
- Admit only after paired ablation shows at least 1 percentage point annualized
  excess improvement, non-worse Sharpe, drawdown deterioration no greater than
  2 percentage points, positive 2.5x-cost return, and improvement in at least
  60% of eligible folds.

### 11.2 Announcement intelligence

- Consume only validated canonical numeric/categorical event features with
  provenance, event time, publication time, confidence, and revision lineage.
- Initially use events only as a negative veto or risk scaler.
- Never turn raw prose, an LLM summary, or one announcement into a direct buy.
- Cap an admitted positive blend contribution at 10%; use zero before admission.
- Keep semantic extraction provider-neutral so Codex, Claude, DeepSeek, or a
  future executor can produce the same schema and pass the same validator.
- The daily strategy runtime must not require any specific LLM provider.

## 12. Expected Outcome and Honest Time Box

### 12.1 What is knowable quickly

Within 3.5 working days, Stages 0-1 should produce one honest answer for each
intended core:

```text
proceed              evidence supports building the sleeve
negative_hypothesis  the declared rule core did not show net historical edge
data_blocked         point-in-time or executable-price evidence is insufficient
```

This is the first value checkpoint. It prevents another long implementation
cycle built on an unverified premise.

### 12.2 Conditional delivery estimate

If at least one sleeve proceeds:

- Stage 2 portfolio construction: 3-4 working days.
- Stage 3 validation and sealed final: 2-3 working days.
- Stage 4 runtime, Dashboard, and ECS canary: 2-3 working days.
- Provisional operational evidence: 20 trading days.
- Stable operational evidence: 60 trading days.

Therefore an admitted paper-trading candidate is possible about 7-10 working
days after Stage 1, but only if the sealed gate passes. No plan can honestly
promise positive future return or a passing model by a fixed date.

### 12.3 Improvement versus the current system

- Zero-trade failures become attributable to rule evidence, calibration, costs,
  uncertainty, or data quality rather than one ambiguous score.
- Rule strategies can be tested without being silently blocked by an unavailable
  forecast calibrator.
- Excess return, Sharpe, and cash benchmarks become mathematically auditable.
- The number of researched hypotheses is bounded and recorded.
- Portfolio, benchmark, costs, neighbors, and final gate are frozen before use.
- Product and operations work occurs only after strategy evidence exists.
- Legacy complexity gains a dated retirement condition instead of remaining an
  indefinite second production path.

## 13. Final Self-Review Gate

Before declaring this plan implemented, verify all of the following:

- [x] Arithmetic active return is never geometrically compounded.
- [x] Cash baseline includes financing opportunity cost.
- [x] Rule replay and model replay have distinct typed inputs and gates.
- [x] Stage 1 evaluates intended cores; controls cannot replace them.
- [x] Stage 1 reads only the oldest 60% and emits one explicit status per sleeve.
- [x] All point-in-time and executable-price coverage thresholds pass or block.
- [x] No final-period result is used to tune or select a replacement.
- [x] ML and intelligence overlays remain optional and cannot directly trade prose.

The following checks are conditional on Stage 2 opening. They are intentionally
not applicable while `stage2_allowed_markets` is empty:

- [ ] Portfolio and benchmark allocations are frozen and monthly rebalanced.
- [ ] 2.5x cost stress, neighbor stability, and mandatory ablations are present.
- [ ] New paper state is isolated from competition state.
- [ ] Dashboard build, focused tests, live endpoints, and ECS units are verified.
- [ ] Legacy active routes have a 60-day stability gate and ten-day retirement SLA.

The implementation is complete only when every applicable item has artifact
evidence. A passing unit test or a rendered Dashboard alone is not completion.

## 14. Implementation Checkpoint (2026-08-09)

Stages 0-1 are implemented and verified. The conditional delivery gate did not
open Stages 2-6, so this plan deliberately made no personal-portfolio Dashboard,
ECS runner, timer, or strategy activation change.

### 14.1 Verified implementation

- Canonical return metrics now use NAV relative wealth; cash earns the declared
  risk-free rate before comparison with the benchmark.
- Rule replay and model replay have separate economic contracts. A missing or
  uncertain model calibration cannot suppress the mechanical rule replay.
- Next-open execution persists limit and suspension evidence, portfolio replay
  applies rank buffers, industry caps, turnover limits, no-trade bands, and real
  commission/slippage, and attribution reconciles to NAV.
- Securities that become ineligible remain in the quote set so existing
  positions can still be marked and liquidated, while they are excluded from
  new selection. Missing OHLCV, limit, or suspension evidence fails closed and
  cannot manufacture a tradable session.
- The Stage 1 command accepts the documented argument order and writes fixed
  intended-core, control, NAV, trade, attribution, audit, model-gate, decision,
  and report artifacts.
- A-share historical data preparation now fetches listed, delisted, and paused
  securities. Old listed-only metadata triggers a deterministic repair fetch.
  Trade calendars, financial indicators, adjustment factors, and benchmark
  histories merge adjacent date ranges instead of replacing prior coverage;
  code-scoped endpoint progress is idempotent per requested range.

### 14.2 Real Stage 1 result

Command:

```bash
python3 -m stock_analyze run-rule-core-diagnostic --offline --as-of 20260807
```

Result:

| Market | Audit | Intended-core result | Decision |
|---|---|---|---|
| A-share | Failed | Not replayed | `data_blocked` |
| Cross-border QDII ETF | Passed | CAGR 2.21%, benchmark CAGR 19.40%, annualized relative excess -14.40%, drawdown 20.33%, Sharpe 0.11, turnover 29.68x | `negative_hypothesis` |

The A-share snapshot has only 1,123 calendar days versus the required eight
years; historical constituent-size coverage is 39.8%, names are absent, and
point-in-time valuation, publication/revision, ST, status, and suspension
provenance are below their gates. This is a data result, not a strategy-loss
result.

The QDII intended trend core made 5,123 development-period trades and incurred
64,132.17 of modeled execution cost. It remained profitable in absolute terms
but materially underperformed its fixed benchmark, had high turnover, and did
not pass both account sleeves. The fixed defensive control nearly matched the
benchmark but also had negative relative excess; it remains a control and is
not promoted as a replacement hypothesis.

Overall status is `data_repair_required`; `stage2_allowed_markets` is empty.
This is the predeclared stop condition. The next admissible work is A-share data
repair and a rerun of the same frozen diagnostic, not signal tuning.

### 14.3 Verification evidence

- 91 focused replay/model/calibration/label/source/CLI/data-preparation tests
  passed, including 23 A-share backtest-data preparation tests.
- The final real diagnostic exited 0 with empty stderr.
- A second identical real run reproduced the exact SHA-256 hashes of
  `decision.json`, `nav.parquet`, `trades.parquet`, and `attribution.parquet`.
- Attribution contains 858 account-days and reconciles with maximum absolute
  error below `2.9e-16`; NAV has 858 rows and the intended ledger has 5,123
  trades.
