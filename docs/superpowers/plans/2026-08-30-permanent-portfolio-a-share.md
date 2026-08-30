# A 股永久投资组合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立两套各以 20 万元起步的 A 股场内 ETF 永久组合，完成 2018-2024 开发回测、一次性 2025-2026 盲测、隔离前向纸面账本和 Dashboard 收益波动展示。

**Architecture:** 新功能位于独立的 `stock_analyze.research.permanent_portfolio` 包中，按配置/封存、数据物化、信号、回放、指标和前向运行拆分。历史结果与纸面账户均写入版本化研究目录；Dashboard 通过独立只读资源加载已完成产物，绝不在 HTTP 请求中采集数据或运行回测。

**Tech Stack:** Python 3、pandas、NumPy、现有 `PortfolioStore`、Tushare provider、JSON-compatible YAML、React 18、TypeScript、Vite、Vitest、Testing Library、Lightweight Charts。

**Design:** `docs/superpowers/specs/2026-08-30-permanent-portfolio-a-share-design.md`

---

## File Map

### New backend files

- `configs/research/permanent_portfolio_v1.yaml`: 唯一冻结研究配置。
- `stock_analyze/research/permanent_portfolio/__init__.py`: 公开 API。
- `stock_analyze/research/permanent_portfolio/contract.py`: 配置类型、日期边界、哈希和状态机。
- `stock_analyze/research/permanent_portfolio/data.py`: ETF 日线、复权和交易日历物化与校验。
- `stock_analyze/research/permanent_portfolio/signals.py`: 固定阈值与双动量目标权重纯函数。
- `stock_analyze/research/permanent_portfolio/engine.py`: 下一开盘、成本、整手、停牌和现金账本回放。
- `stock_analyze/research/permanent_portfolio/metrics.py`: 收益、波动、回撤、换手与基准指标。
- `stock_analyze/research/permanent_portfolio/workflow.py`: 开发封存、一次性盲测和报告发布。
- `stock_analyze/research/permanent_portfolio/paper.py`: 两个隔离前向账户的幂等日运行。
- `stock_analyze/dashboard_permanent_portfolio.py`: 有界只读 Dashboard 资源。

### Modified backend files

- `stock_analyze/cli.py`: 注册数据、开发、盲测和纸面运行命令，并接入 Dashboard API。
- `docs/system-overview.md`: 记录研究组合边界、运行方式和页面入口。
- `docs/system-harness.md`: 记录命令、产物和故障检查。

### New backend tests

- `tests/test_permanent_portfolio_contract.py`
- `tests/test_permanent_portfolio_data.py`
- `tests/test_permanent_portfolio_signals.py`
- `tests/test_permanent_portfolio_engine.py`
- `tests/test_permanent_portfolio_metrics.py`
- `tests/test_permanent_portfolio_workflow.py`
- `tests/test_permanent_portfolio_paper.py`
- `tests/test_dashboard_permanent_portfolio.py`

### Frontend files

- Create `frontend/dashboard/src/PermanentPortfolioPage.tsx`
- Create `frontend/dashboard/src/PermanentPortfolioPage.test.tsx`
- Create `frontend/dashboard/src/PermanentPortfolioCharts.tsx`
- Create `frontend/dashboard/src/PermanentPortfolioCharts.test.tsx`
- Modify `frontend/dashboard/src/workspaceRoute.ts`
- Modify `frontend/dashboard/src/workspaceRoute.test.ts`
- Modify `frontend/dashboard/src/WorkspaceShell.tsx`
- Modify `frontend/dashboard/src/WorkspaceShell.test.tsx`
- Modify `frontend/dashboard/src/App.tsx`
- Modify `frontend/dashboard/src/App.test.tsx`
- Modify `frontend/dashboard/src/types.ts`
- Modify `frontend/dashboard/src/api.ts`
- Modify `frontend/dashboard/src/api.test.ts`
- Modify `frontend/dashboard/src/styles.css`

## Task 1: Freeze the contract and state machine

**Files:**
- Create: `configs/research/permanent_portfolio_v1.yaml`
- Create: `stock_analyze/research/permanent_portfolio/__init__.py`
- Create: `stock_analyze/research/permanent_portfolio/contract.py`
- Create: `tests/test_permanent_portfolio_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
# tests/test_permanent_portfolio_contract.py
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stock_analyze.research.permanent_portfolio.contract import (
    STUDY_ID,
    canonical_hash,
    load_contract,
    transition_state,
)


class PermanentPortfolioContractTests(unittest.TestCase):
    def test_repository_contract_is_frozen(self) -> None:
        contract = load_contract("configs/research/permanent_portfolio_v1.yaml")
        self.assertEqual(contract.study_id, STUDY_ID)
        self.assertEqual(contract.initial_cash, 200000.0)
        self.assertEqual(contract.development_start, "20180101")
        self.assertEqual(contract.development_end, "20241231")
        self.assertEqual(contract.holdout_start, "20250101")
        self.assertEqual(
            [asset.code for asset in contract.assets],
            ["510300.SH", "511260.SH", "511880.SH", "518880.SH"],
        )
        self.assertEqual(contract.dynamic_rank_weights, (0.40, 0.30, 0.20, 0.10))

    def test_holdout_cannot_open_twice(self) -> None:
        opened = transition_state(
            {"status": "development_complete"},
            "holdout_opened",
            expected_from="development_complete",
        )
        with self.assertRaisesRegex(ValueError, "permanent_portfolio_state"):
            transition_state(
                opened,
                "holdout_opened",
                expected_from="development_complete",
            )

    def test_hash_is_order_independent(self) -> None:
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))

    def test_development_window_rejects_holdout_date(self) -> None:
        contract = load_contract("configs/research/permanent_portfolio_v1.yaml")
        with self.assertRaisesRegex(ValueError, "development_window"):
            contract.assert_development_date("20250101")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_contract -v
```

Expected: import failure for `stock_analyze.research.permanent_portfolio.contract`.

- [ ] **Step 3: Add the frozen configuration**

```json
{
  "schema_version": 1,
  "study_id": "permanent_portfolio_v1",
  "source_start": "2016-12-01",
  "development_start": "2018-01-01",
  "development_end": "2024-12-31",
  "holdout_start": "2025-01-01",
  "initial_cash": 200000.0,
  "assets": [
    {"role": "equity", "code": "510300.SH", "name": "华泰柏瑞沪深300ETF"},
    {"role": "bond", "code": "511260.SH", "name": "国泰上证10年期国债ETF"},
    {"role": "cash", "code": "511880.SH", "name": "银华日利ETF"},
    {"role": "gold", "code": "518880.SH", "name": "华安黄金ETF"}
  ],
  "fixed": {"lower_band": 0.15, "upper_band": 0.35, "target_weight": 0.25},
  "dynamic": {
    "rebalance_frequency": "monthly",
    "lookbacks_months": [6, 12],
    "skip_recent_months": 1,
    "rank_weights": [0.40, 0.30, 0.20, 0.10],
    "tie_break_order": ["cash", "bond", "gold", "equity"]
  },
  "trading": {
    "lot_size": 100,
    "commission_rate": 0.0003,
    "minimum_commission": 5.0,
    "slippage_rate": 0.0005,
    "stamp_tax_rate": 0.0
  },
  "benchmarks": ["equity_buy_hold", "equal_weight_buy_hold", "cash_buy_hold"]
}
```

- [ ] **Step 4: Implement typed loading, canonical hashing, and legal transitions**

`contract.py` must expose these exact types and functions:

```python
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ...config import load_config

STUDY_ID = "permanent_portfolio_v1"
STATE_ORDER = (
    "draft",
    "development_sealed",
    "development_complete",
    "holdout_opened",
    "holdout_complete",
    "forward_ready",
)


@dataclass(frozen=True)
class AssetSpec:
    role: str
    code: str
    name: str


@dataclass(frozen=True)
class PermanentPortfolioContract:
    study_id: str
    source_start: str
    development_start: str
    development_end: str
    holdout_start: str
    initial_cash: float
    assets: tuple[AssetSpec, ...]
    lower_band: float
    upper_band: float
    fixed_target_weight: float
    dynamic_rank_weights: tuple[float, ...]
    tie_break_order: tuple[str, ...]
    lot_size: int
    commission_rate: float
    minimum_commission: float
    slippage_rate: float
    stamp_tax_rate: float
    raw: Mapping[str, Any]

    def assert_development_date(self, value: str) -> None:
        key = value.replace("-", "")[:8]
        if not self.development_start <= key <= self.development_end:
            raise ValueError(f"permanent_portfolio_development_window:{key}")


def canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_contract(path: str | Path) -> PermanentPortfolioContract:
    raw = load_config(path, apply_migrations=False)
    assets = tuple(AssetSpec(**item) for item in raw["assets"])
    dynamic = raw["dynamic"]
    fixed = raw["fixed"]
    trading = raw["trading"]
    contract = PermanentPortfolioContract(
        study_id=str(raw["study_id"]),
        source_start=str(raw["source_start"]).replace("-", ""),
        development_start=str(raw["development_start"]).replace("-", ""),
        development_end=str(raw["development_end"]).replace("-", ""),
        holdout_start=str(raw["holdout_start"]).replace("-", ""),
        initial_cash=float(raw["initial_cash"]),
        assets=assets,
        lower_band=float(fixed["lower_band"]),
        upper_band=float(fixed["upper_band"]),
        fixed_target_weight=float(fixed["target_weight"]),
        dynamic_rank_weights=tuple(float(value) for value in dynamic["rank_weights"]),
        tie_break_order=tuple(str(value) for value in dynamic["tie_break_order"]),
        lot_size=int(trading["lot_size"]),
        commission_rate=float(trading["commission_rate"]),
        minimum_commission=float(trading["minimum_commission"]),
        slippage_rate=float(trading["slippage_rate"]),
        stamp_tax_rate=float(trading["stamp_tax_rate"]),
        raw=raw,
    )
    if contract.study_id != STUDY_ID:
        raise ValueError("permanent_portfolio_study_id")
    if {item.role for item in assets} != {"equity", "bond", "cash", "gold"}:
        raise ValueError("permanent_portfolio_assets")
    if sum(contract.dynamic_rank_weights) != 1.0:
        raise ValueError("permanent_portfolio_dynamic_weights")
    return contract


def transition_state(
    state: Mapping[str, Any],
    target: str,
    *,
    expected_from: str,
) -> dict[str, Any]:
    current = str(state.get("status") or "draft")
    if current != expected_from or target not in STATE_ORDER:
        raise ValueError(f"permanent_portfolio_state:{current}:{target}")
    return {**state, "status": target}
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_contract -v
```

Expected: all contract tests pass.

Commit after explicit repository-owner authorization:

```bash
git add configs/research/permanent_portfolio_v1.yaml stock_analyze/research/permanent_portfolio/__init__.py stock_analyze/research/permanent_portfolio/contract.py tests/test_permanent_portfolio_contract.py
git commit -m "feat: freeze permanent portfolio research contract"
```

## Task 2: Materialize verified ETF total-return history

**Files:**
- Create: `stock_analyze/research/permanent_portfolio/data.py`
- Create: `tests/test_permanent_portfolio_data.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_cli_prepare_backtest_data.py`

- [ ] **Step 1: Write failing data-contract tests**

```python
# tests/test_permanent_portfolio_data.py
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.data import (
    build_total_return_frame,
    validate_market_frame,
    write_market_publication,
)


class PermanentPortfolioDataTests(unittest.TestCase):
    def test_total_return_uses_adjustment_factor_but_keeps_raw_open(self) -> None:
        daily = pd.DataFrame({
            "ts_code": ["510300.SH", "510300.SH"],
            "trade_date": ["20180102", "20180103"],
            "open": [4.0, 3.9],
            "high": [4.1, 4.0],
            "low": [3.9, 3.8],
            "close": [4.0, 3.9],
            "vol": [1000.0, 1200.0],
            "amount": [4000.0, 4680.0],
        })
        adjustment = pd.DataFrame({
            "ts_code": ["510300.SH", "510300.SH"],
            "trade_date": ["20180102", "20180103"],
            "adj_factor": [1.0, 1.1],
        })
        result = build_total_return_frame(daily, adjustment)
        self.assertEqual(result["open"].tolist(), [4.0, 3.9])
        self.assertAlmostEqual(result.iloc[1]["adjusted_close"], 4.29)

    def test_duplicate_code_date_fails_closed(self) -> None:
        frame = pd.DataFrame({
            "ts_code": ["510300.SH", "510300.SH"],
            "trade_date": ["20180102", "20180102"],
            "open": [4.0, 4.0],
            "close": [4.0, 4.0],
            "adjusted_close": [4.0, 4.0],
        })
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_market_frame(frame, expected_codes={"510300.SH"})

    def test_publication_is_checksummed(self) -> None:
        frame = pd.DataFrame({
            "ts_code": ["510300.SH"],
            "trade_date": ["20180102"],
            "open": [4.0],
            "high": [4.1],
            "low": [3.9],
            "close": [4.0],
            "vol": [1000.0],
            "amount": [4000.0],
            "adj_factor": [1.0],
            "adjusted_close": [4.0],
            "is_open": [True],
        })
        with TemporaryDirectory() as tmp:
            manifest = write_market_publication(
                Path(tmp), frame, source_start="20180102", end_date="20180102"
            )
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len(manifest["data_sha256"]), 64)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_data -v
```

Expected: import failure for `permanent_portfolio.data`.

- [ ] **Step 3: Implement provider injection and validation**

`data.py` must define:

```python
class PermanentPortfolioDataProvider(Protocol):
    def fund_daily(self, *, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame: ...
    def fund_adj(self, *, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame: ...
    def fund_basic(self, *, market: str) -> pd.DataFrame: ...
    def trade_cal(self, *, exchange: str, start_date: str, end_date: str) -> pd.DataFrame: ...


REQUIRED_COLUMNS = (
    "ts_code", "trade_date", "open", "high", "low", "close",
    "vol", "amount", "adj_factor", "adjusted_close", "is_open",
)


def build_total_return_frame(daily: pd.DataFrame, adjustment: pd.DataFrame) -> pd.DataFrame:
    left = daily.copy()
    right = adjustment.copy()
    for frame in (left, right):
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["trade_date"] = frame["trade_date"].astype(str)
    merged = left.merge(
        right[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    merged["adjusted_close"] = (
        pd.to_numeric(merged["close"], errors="coerce")
        * pd.to_numeric(merged["adj_factor"], errors="coerce")
    )
    return merged
```

`validate_market_frame` must reject duplicate `(ts_code, trade_date)`, unknown
codes, non-positive OHLC/adjusted prices, missing adjustment factors, dates
outside the declared range, and unexplained gaps on open exchange dates.
`write_market_publication` writes Parquet and `manifest.json` into a staging
directory, fsyncs both, verifies SHA-256, atomically renames the publication,
then updates `latest.json`.

- [ ] **Step 4: Add the collection CLI**

Register and dispatch:

```text
prepare-permanent-portfolio-data
  --contract configs/research/permanent_portfolio_v1.yaml
  --end YYYY-MM-DD
  --repo-root .
```

The command calls `materialize_market_data(...)`; it must not write under formal
account paths. Add a CLI test that patches only that function and asserts exact
arguments.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_data tests.test_cli_prepare_backtest_data -v
```

Expected: all tests pass and the existing CLI preparation tests remain green.

Commit after authorization:

```bash
git add stock_analyze/research/permanent_portfolio/data.py stock_analyze/cli.py tests/test_permanent_portfolio_data.py tests/test_cli_prepare_backtest_data.py
git commit -m "feat: materialize permanent portfolio ETF history"
```

## Task 3: Implement frozen target-weight rules

**Files:**
- Create: `stock_analyze/research/permanent_portfolio/signals.py`
- Create: `tests/test_permanent_portfolio_signals.py`

- [ ] **Step 1: Write failing signal tests**

```python
# tests/test_permanent_portfolio_signals.py
import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.signals import (
    dynamic_target_weights,
    fixed_target_weights,
)


class PermanentPortfolioSignalTests(unittest.TestCase):
    def test_fixed_rule_does_not_trade_inside_band(self) -> None:
        result = fixed_target_weights(
            {"equity": 0.30, "bond": 0.20, "cash": 0.25, "gold": 0.25},
            lower=0.15,
            upper=0.35,
        )
        self.assertIsNone(result)

    def test_fixed_rule_restores_all_assets_after_breach(self) -> None:
        result = fixed_target_weights(
            {"equity": 0.36, "bond": 0.14, "cash": 0.25, "gold": 0.25},
            lower=0.15,
            upper=0.35,
        )
        self.assertEqual(result, {"equity": 0.25, "bond": 0.25, "cash": 0.25, "gold": 0.25})

    def test_dynamic_rule_maps_rank_to_frozen_weights(self) -> None:
        prices = pd.DataFrame({
            "role": ["equity", "bond", "cash", "gold"] * 4,
            "months_ago": [12] * 4 + [6] * 4 + [1] * 4 + [0] * 4,
            "adjusted_close": [
                100, 100, 100, 100,
                110, 103, 101, 120,
                120, 105, 102, 130,
                121, 106, 103, 131,
            ],
        })
        weights = dynamic_target_weights(prices)
        self.assertEqual(weights["gold"], 0.40)
        self.assertEqual(weights["equity"], 0.30)
        self.assertEqual(weights["bond"], 0.20)
        self.assertEqual(weights["cash"], 0.10)
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_dynamic_rule_fails_when_window_is_incomplete(self) -> None:
        with self.assertRaisesRegex(ValueError, "momentum_window"):
            dynamic_target_weights(pd.DataFrame())
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_signals -v
```

Expected: import failure for `permanent_portfolio.signals`.

- [ ] **Step 3: Implement pure deterministic rules**

```python
ROLES = ("equity", "bond", "cash", "gold")
DEFAULT_TIE_BREAK = ("cash", "bond", "gold", "equity")
RANK_WEIGHTS = (0.40, 0.30, 0.20, 0.10)


def fixed_target_weights(
    actual: Mapping[str, float],
    *,
    lower: float,
    upper: float,
) -> dict[str, float] | None:
    weights = {role: float(actual[role]) for role in ROLES}
    if all(lower <= weights[role] <= upper for role in ROLES):
        return None
    return {role: 0.25 for role in ROLES}


def dynamic_target_weights(
    observations: pd.DataFrame,
    *,
    tie_break: tuple[str, ...] = DEFAULT_TIE_BREAK,
) -> dict[str, float]:
    required = {(role, month) for role in ROLES for month in (12, 6, 1, 0)}
    indexed = observations.set_index(["role", "months_ago"])["adjusted_close"]
    if not required.issubset(indexed.index):
        raise ValueError("permanent_portfolio_momentum_window")
    cash_6_1 = indexed["cash", 1] / indexed["cash", 6] - 1.0
    cash_12_1 = indexed["cash", 1] / indexed["cash", 12] - 1.0
    scores = {"cash": 0.0}
    for role in ("equity", "bond", "gold"):
        scores[role] = 0.5 * (
            indexed[role, 1] / indexed[role, 6] - 1.0 - cash_6_1
        ) + 0.5 * (
            indexed[role, 1] / indexed[role, 12] - 1.0 - cash_12_1
        )
    priority = {role: index for index, role in enumerate(tie_break)}
    ranked = sorted(ROLES, key=lambda role: (-scores[role], priority[role]))
    return {role: RANK_WEIGHTS[index] for index, role in enumerate(ranked)}
```

- [ ] **Step 4: Add boundary and property cases**

Add tests proving exact 15% and 35% do not trade, every dynamic target is between
10% and 40%, ties follow `cash, bond, gold, equity`, and input row order does not
change the result.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_signals -v
```

Expected: all signal tests pass.

Commit after authorization:

```bash
git add stock_analyze/research/permanent_portfolio/signals.py tests/test_permanent_portfolio_signals.py
git commit -m "feat: add frozen permanent portfolio signals"
```

## Task 4: Build the next-open portfolio replay

**Files:**
- Create: `stock_analyze/research/permanent_portfolio/engine.py`
- Create: `tests/test_permanent_portfolio_engine.py`

- [ ] **Step 1: Write failing execution tests**

```python
# tests/test_permanent_portfolio_engine.py
import unittest

import pandas as pd

from stock_analyze.research.permanent_portfolio.engine import replay_strategy


class PermanentPortfolioEngineTests(unittest.TestCase):
    def test_close_signal_executes_at_next_open_with_costs_and_lots(self) -> None:
        market = pd.DataFrame([
            {"trade_date": "20180102", "role": "equity", "code": "510300.SH", "open": 4.00, "close": 4.00, "adjusted_close": 4.00, "is_open": True},
            {"trade_date": "20180103", "role": "equity", "code": "510300.SH", "open": 4.10, "close": 4.20, "adjusted_close": 4.20, "is_open": True},
        ])
        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"equity": 1.0}},
            lot_size=100,
            commission_rate=0.0003,
            minimum_commission=5.0,
            slippage_rate=0.0005,
            stamp_tax_rate=0.0,
        )
        trade = result.trades.iloc[0]
        self.assertEqual(trade["signal_date"], "20180102")
        self.assertEqual(trade["trade_date"], "20180103")
        self.assertEqual(trade["shares"] % 100, 0)
        self.assertGreater(trade["price"], 4.10)
        self.assertGreaterEqual(trade["commission"], 5.0)

    def test_suspended_asset_keeps_pending_order(self) -> None:
        market = pd.DataFrame([
            {"trade_date": "20180102", "role": "gold", "code": "518880.SH", "open": 2.70, "close": 2.70, "adjusted_close": 2.70, "is_open": True},
            {"trade_date": "20180103", "role": "gold", "code": "518880.SH", "open": None, "close": 2.70, "adjusted_close": 2.70, "is_open": False},
        ])
        result = replay_strategy(
            market,
            strategy="fixed",
            initial_cash=200000.0,
            target_schedule={"20180102": {"gold": 1.0}},
        )
        self.assertEqual(result.pending.iloc[0]["reason"], "asset_not_open")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_engine -v
```

Expected: import failure for `permanent_portfolio.engine`.

- [ ] **Step 3: Implement the result schema and fill calculation**

```python
@dataclass(frozen=True)
class ReplayResult:
    nav: pd.DataFrame
    positions: pd.DataFrame
    trades: pd.DataFrame
    targets: pd.DataFrame
    pending: pd.DataFrame


def execution_price(open_price: float, side: str, slippage_rate: float) -> float:
    direction = 1.0 if side == "BUY" else -1.0
    return float(open_price) * (1.0 + direction * float(slippage_rate))


def commission(gross: float, rate: float, minimum: float) -> float:
    return max(float(gross) * float(rate), float(minimum))


def round_lot(shares: float, lot_size: int) -> int:
    return max(0, int(shares) // int(lot_size) * int(lot_size))
```

`replay_strategy` must process each exchange trading date in order:

1. execute only orders whose `signal_date` is earlier than `trade_date`;
2. value suspended assets at the latest valid close and retain their orders;
3. sell before buying;
4. recompute affordable buy lots after commission and slippage;
5. append one NAV row per date;
6. assert non-negative cash/shares and `cash + market_value == total_value`
   within one cent.

- [ ] **Step 4: Add invariant tests**

Add tests for sell-before-buy, insufficient cash, minimum commission, no stamp
tax, deterministic reruns, stale valuation during suspension, non-negative
balances, and one-cent asset identity.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_engine -v
```

Expected: all engine tests pass.

Commit after authorization:

```bash
git add stock_analyze/research/permanent_portfolio/engine.py tests/test_permanent_portfolio_engine.py
git commit -m "feat: replay permanent portfolios at next open"
```

## Task 5: Compute metrics, baselines, and immutable artifacts

**Files:**
- Create: `stock_analyze/research/permanent_portfolio/metrics.py`
- Create: `stock_analyze/research/permanent_portfolio/workflow.py`
- Create: `tests/test_permanent_portfolio_metrics.py`
- Create: `tests/test_permanent_portfolio_workflow.py`

- [ ] **Step 1: Write failing metric and leakage tests**

```python
# tests/test_permanent_portfolio_metrics.py
import unittest
import pandas as pd

from stock_analyze.research.permanent_portfolio.metrics import calculate_metrics


class PermanentPortfolioMetricsTests(unittest.TestCase):
    def test_metrics_include_return_volatility_drawdown_and_costs(self) -> None:
        nav = pd.DataFrame({
            "date": ["2020-01-02", "2020-01-03", "2020-01-06"],
            "total_value": [200000.0, 202000.0, 199000.0],
            "cash_benchmark_value": [200000.0, 200020.0, 200040.0],
        })
        metrics = calculate_metrics(nav, total_turnover=40000.0, total_cost=45.0)
        self.assertIn("annualized_return", metrics)
        self.assertIn("annualized_volatility", metrics)
        self.assertIn("max_drawdown", metrics)
        self.assertIn("sharpe_vs_cash", metrics)
        self.assertEqual(metrics["total_cost"], 45.0)
```

```python
# tests/test_permanent_portfolio_workflow.py
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from stock_analyze.research.permanent_portfolio.workflow import (
    open_holdout_once,
    run_development,
)


class PermanentPortfolioWorkflowTests(unittest.TestCase):
    def test_development_rejects_rows_from_holdout(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "development_window"):
                run_development(
                    repo_root=Path(tmp),
                    contract_path=Path("configs/research/permanent_portfolio_v1.yaml"),
                    market_frame_fixture=[{"trade_date": "20250102"}],
                )

    def test_holdout_open_is_exclusive(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            development = root / "development.json"
            development.write_text(
                json.dumps({"status": "development_complete", "artifact_sha256": "a" * 64}),
                encoding="utf-8",
            )
            open_holdout_once(root, development, expected_sha256="a" * 64)
            with self.assertRaisesRegex(ValueError, "holdout_already_opened"):
                open_holdout_once(root, development, expected_sha256="a" * 64)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_metrics tests.test_permanent_portfolio_workflow -v
```

Expected: imports fail for `metrics` and `workflow`.

- [ ] **Step 3: Implement metrics and three frozen baselines**

`calculate_metrics` returns finite JSON values for:

```python
METRIC_KEYS = (
    "cumulative_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe_vs_cash",
    "sortino_vs_cash",
    "max_drawdown",
    "max_drawdown_duration",
    "calmar",
    "positive_month_ratio",
    "annualized_turnover",
    "trade_count",
    "total_cost",
    "cost_bps",
)
```

Use daily portfolio returns minus daily `511880.SH` total return for Sharpe and
Sortino. Use 252 trading days. Return `None`, not NaN or infinity, when a metric
has an insufficient denominator.

`workflow.py` must generate the fixed strategy, dynamic strategy, equity
buy-and-hold, never-rebalanced equal-weight, and cash buy-and-hold using the same
execution engine and costs. It must also run a 2x commission/slippage stress
scenario without changing the primary result.

Each evaluation window starts from a fresh 200,000 yuan account. Predetermined
fixed and benchmark targets, plus the dynamic target calculated from data ending
before the window, execute at the first window trading day's open. The holdout
runner may read pre-2025 prices only as momentum warmup; all reported holdout
returns start on or after `20250101` and never continue development NAV.

- [ ] **Step 4: Implement exclusive sealing and publication**

Use `os.open(..., O_CREAT | O_EXCL | O_NOFOLLOW)`, `fsync`, canonical JSON and
SHA-256 following `a_share_all_cap_holdout.py`. Publish:

```text
data/research/permanent_portfolio/v1/manifests/state.json
data/research/permanent_portfolio/v1/results/development/{artifact_sha256}/
data/research/permanent_portfolio/v1/results/holdout/{artifact_sha256}/
reports/research/permanent_portfolio/v1/dashboard.json
```

The development runner rejects every row dated `>= 20250101`. The holdout
runner validates the development artifact hash and creates the exclusive
`holdout_opened.json` marker before reading holdout returns.
`manifests/state.json` records `development_artifact` and
`development_sha256`, which are the sole inputs accepted by the holdout command.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_metrics tests.test_permanent_portfolio_workflow -v
```

Expected: all metric, baseline, sealing, hash, and leakage tests pass.

Commit after authorization:

```bash
git add stock_analyze/research/permanent_portfolio/metrics.py stock_analyze/research/permanent_portfolio/workflow.py tests/test_permanent_portfolio_metrics.py tests/test_permanent_portfolio_workflow.py
git commit -m "feat: seal permanent portfolio backtests"
```

## Task 6: Add explicit development and holdout CLI commands

**Files:**
- Modify: `stock_analyze/cli.py`
- Create: `tests/test_cli_permanent_portfolio.py`

- [ ] **Step 1: Write failing CLI dispatch tests**

```python
# tests/test_cli_permanent_portfolio.py
import unittest
from unittest import mock

from stock_analyze import cli


class PermanentPortfolioCliTests(unittest.TestCase):
    @mock.patch("stock_analyze.research.permanent_portfolio.workflow.run_development")
    def test_development_command_uses_frozen_contract(self, run) -> None:
        run.return_value = {"status": "development_complete"}
        exit_code = cli.main([
            "run-permanent-portfolio-development",
            "--repo-root", ".",
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            str(run.call_args.kwargs["contract_path"]),
            "configs/research/permanent_portfolio_v1.yaml",
        )

    @mock.patch("stock_analyze.research.permanent_portfolio.workflow.run_holdout")
    def test_holdout_requires_development_hash(self, run) -> None:
        run.return_value = {"status": "holdout_complete"}
        exit_code = cli.main([
            "open-permanent-portfolio-holdout",
            "--development-artifact", "development.json",
            "--development-sha256", "a" * 64,
            "--repo-root", ".",
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_args.kwargs["expected_development_sha256"], "a" * 64)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_cli_permanent_portfolio -v
```

Expected: parser rejects the new commands.

- [ ] **Step 3: Register exact command contracts**

Add:

```text
run-permanent-portfolio-development
  --contract configs/research/permanent_portfolio_v1.yaml
  --repo-root .

open-permanent-portfolio-holdout
  --contract configs/research/permanent_portfolio_v1.yaml
  --development-artifact PATH
  --development-sha256 SHA256
  --repo-root .
```

Each command prints only the JSON-safe status, artifact path and hash. It must
not print source credentials or raw provider responses.

- [ ] **Step 4: Wire dispatch functions**

Use lazy imports inside `_command_run_permanent_portfolio_development` and
`_command_open_permanent_portfolio_holdout`, call `ensure_dirs(args.logs_dir)`,
return `0` on success, and allow fail-closed `ValueError` messages to reach the
existing CLI error boundary.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_cli_permanent_portfolio tests.test_cli_prepare_backtest_data -v
```

Expected: all CLI tests pass.

Commit after authorization:

```bash
git add stock_analyze/cli.py tests/test_cli_permanent_portfolio.py
git commit -m "feat: expose permanent portfolio research workflow"
```

## Task 7: Add isolated forward paper accounts

**Files:**
- Create: `stock_analyze/research/permanent_portfolio/paper.py`
- Create: `tests/test_permanent_portfolio_paper.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_cli_permanent_portfolio.py`

- [ ] **Step 1: Write failing paper-account tests**

```python
# tests/test_permanent_portfolio_paper.py
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from stock_analyze.research.permanent_portfolio.paper import (
    account_paths,
    run_paper_day,
)


class PermanentPortfolioPaperTests(unittest.TestCase):
    def test_accounts_are_isolated_under_research(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = account_paths(Path(tmp))
            self.assertIn("data/research/paper_portfolios/permanent_fixed_v1", str(paths["fixed"]))
            self.assertIn("data/research/paper_portfolios/permanent_dynamic_v1", str(paths["dynamic"]))

    def test_same_day_run_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            first = run_paper_day(Path(tmp), as_of="2026-09-01", fixture_mode=True)
            second = run_paper_day(Path(tmp), as_of="2026-09-01", fixture_mode=True)
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertEqual(second["status"], "already_complete")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_paper -v
```

Expected: import failure for `permanent_portfolio.paper`.

- [ ] **Step 3: Implement the forward runner**

`run_paper_day` must:

1. require state `holdout_complete` or `forward_ready`;
2. verify current config, market-data and holdout hashes;
3. initialize two `PortfolioStore` instances with one 200,000 yuan account each;
4. execute due next-open orders;
5. update NAV and positions;
6. compute the fixed threshold decision every close;
7. compute the dynamic decision only on the final common trading day of a month;
8. write signals and pending orders atomically;
9. append one terminal run row per strategy;
10. return the prior terminal result without duplicate trades on rerun.

The function must refuse any path outside:

```text
data/research/paper_portfolios/permanent_fixed_v1/
data/research/paper_portfolios/permanent_dynamic_v1/
```

- [ ] **Step 4: Add the paper CLI**

Register:

```text
run-permanent-portfolio-paper --as-of YYYY-MM-DD --repo-root .
```

Add CLI tests that assert the command dispatches once and never passes formal
agent or market account paths.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_permanent_portfolio_paper tests.test_cli_permanent_portfolio -v
```

Expected: all paper and CLI tests pass.

Commit after authorization:

```bash
git add stock_analyze/research/permanent_portfolio/paper.py stock_analyze/cli.py tests/test_permanent_portfolio_paper.py tests/test_cli_permanent_portfolio.py
git commit -m "feat: track isolated permanent portfolio accounts"
```

## Task 8: Add a bounded Dashboard API resource

**Files:**
- Create: `stock_analyze/dashboard_permanent_portfolio.py`
- Create: `tests/test_dashboard_permanent_portfolio.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_cli_dashboard_routes.py`

- [ ] **Step 1: Write failing API and leakage tests**

```python
# tests/test_dashboard_permanent_portfolio.py
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from stock_analyze.dashboard_permanent_portfolio import (
    build_dashboard_permanent_portfolio_data,
)


class PermanentPortfolioDashboardTests(unittest.TestCase):
    def test_unopened_holdout_returns_status_without_results(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "reports/research/permanent_portfolio/v1/dashboard.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema_version": 1,
                "study": {"status": "development_complete"},
                "windows": {
                    "development": {"status": "complete", "metrics": {}},
                    "holdout": {"status": "sealed", "metrics": {"forbidden": 1}},
                },
            }), encoding="utf-8")
            payload = build_dashboard_permanent_portfolio_data(repo_root=root)
            self.assertEqual(payload["windows"]["holdout"], {"status": "sealed"})

    def test_missing_artifact_is_a_bounded_empty_resource(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_dashboard_permanent_portfolio_data(repo_root=Path(tmp))
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["strategies"], [])
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest tests.test_dashboard_permanent_portfolio -v
```

Expected: import failure for `dashboard_permanent_portfolio`.

- [ ] **Step 3: Implement the bounded reader**

The returned schema is:

```python
{
    "schemaVersion": 1,
    "generatedAt": "...",
    "status": "available",
    "study": {
        "studyId": "permanent_portfolio_v1",
        "status": "holdout_complete",
        "initialCash": 200000.0,
        "contractSha256": "...",
        "dataSha256": "...",
    },
    "assets": [],
    "strategies": [],
    "benchmarks": [],
    "windows": {
        "development": {"status": "complete", "series": [], "metrics": {}},
        "holdout": {"status": "complete", "series": [], "metrics": {}},
        "forward": {"status": "available", "series": [], "metrics": {}},
    },
    "errors": [],
}
```

Apply explicit row limits to series, weights and trades. Convert NaN/Infinity to
`None`. If holdout state is earlier than `holdout_complete`, replace the entire
holdout object with `{"status": "sealed"}`. Do not import or instantiate any
market-data provider.

- [ ] **Step 4: Register `/api/dashboard/permanent-portfolio.json`**

Extend `_is_dashboard_api_path` and `_DashboardRequestHandler` dispatch in
`cli.py`. The handler must call:

```python
build_dashboard_permanent_portfolio_data(repo_root=Path(self.directory).parent)
```

Add a route test that patches this builder, asserts status 200, and checks the
exact `repo_root`.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
python3 -m unittest tests.test_dashboard_permanent_portfolio tests.test_cli_dashboard_routes -v
```

Expected: all API and route tests pass.

Commit after authorization:

```bash
git add stock_analyze/dashboard_permanent_portfolio.py stock_analyze/cli.py tests/test_dashboard_permanent_portfolio.py tests/test_cli_dashboard_routes.py
git commit -m "feat: expose permanent portfolio dashboard data"
```

## Task 9: Add the permanent-portfolio Dashboard workspace

**Files:**
- Create: `frontend/dashboard/src/PermanentPortfolioPage.tsx`
- Create: `frontend/dashboard/src/PermanentPortfolioPage.test.tsx`
- Create: `frontend/dashboard/src/PermanentPortfolioCharts.tsx`
- Create: `frontend/dashboard/src/PermanentPortfolioCharts.test.tsx`
- Modify: `frontend/dashboard/src/workspaceRoute.ts`
- Modify: `frontend/dashboard/src/workspaceRoute.test.ts`
- Modify: `frontend/dashboard/src/WorkspaceShell.tsx`
- Modify: `frontend/dashboard/src/WorkspaceShell.test.tsx`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/api.test.ts`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] **Step 1: Write failing route and API decoder tests**

Add to `workspaceRoute.test.ts`:

```typescript
it("round trips the permanent portfolio workspace", () => {
  const route = { view: "permanent-portfolio" } as const;
  expect(serializeWorkspaceRoute(route)).toBe("view=permanent-portfolio");
  expect(parseWorkspaceRoute("?view=permanent-portfolio")).toEqual(route);
});
```

Add to `api.test.ts`:

```typescript
it("loads and validates permanent portfolio data", async () => {
  server.use(http.get("/api/dashboard/permanent-portfolio.json", () => HttpResponse.json({
    schemaVersion: 1,
    generatedAt: "2026-08-30T12:00:00+08:00",
    status: "available",
    study: {
      studyId: "permanent_portfolio_v1",
      status: "development_complete",
      initialCash: 200000,
      contractSha256: "a",
      dataSha256: "b",
    },
    assets: [],
    strategies: [],
    benchmarks: [],
    windows: {
      development: { status: "complete", series: [], metrics: {} },
      holdout: { status: "sealed" },
      forward: { status: "unavailable", series: [], metrics: {} },
    },
    errors: [],
  })));
  const payload = await fetchPermanentPortfolio();
  expect(payload.windows.holdout).toEqual({ status: "sealed" });
});
```

- [ ] **Step 2: Run focused frontend tests and verify RED**

Run:

```bash
cd frontend/dashboard && npm test -- --run src/workspaceRoute.test.ts src/api.test.ts
```

Expected: TypeScript or assertion failure because the route and fetcher do not
exist.

- [ ] **Step 3: Add types, decoder, route, navigation, and lazy page loading**

Add `"permanent-portfolio"` to `WorkspaceView` and `WorkspaceRoute`. Add
`fetchPermanentPortfolio(signal?)` in `api.ts`, with explicit object, array,
number and string validation matching existing decoder helpers.

Add this public shape to `types.ts`:

```typescript
export type PermanentPortfolioMetricSet = {
  cumulativeReturn: number | null;
  annualizedReturn: number | null;
  annualizedVolatility: number | null;
  sharpeVsCash: number | null;
  sortinoVsCash: number | null;
  maxDrawdown: number | null;
  calmar: number | null;
  annualizedTurnover: number | null;
  totalCost: number | null;
};

export type PermanentPortfolioWindow = {
  status: "complete" | "sealed" | "available" | "unavailable";
  metrics?: Record<string, PermanentPortfolioMetricSet>;
  series?: Array<{
    date: string;
    fixed: number | null;
    dynamic: number | null;
    equityBuyHold: number | null;
    equalWeightBuyHold: number | null;
    cashBuyHold: number | null;
    fixedDrawdown: number | null;
    dynamicDrawdown: number | null;
    fixedVolatility63d: number | null;
    dynamicVolatility63d: number | null;
  }>;
  weights?: Array<Record<string, string | number | null>>;
  annualReturns?: Array<Record<string, string | number | null>>;
  monthlyReturns?: Array<Record<string, string | number | null>>;
  trades?: Array<Record<string, string | number | null>>;
};
```

Use `Landmark` from Lucide for the left-rail icon. Lazy-load
`PermanentPortfolioPage` in `App.tsx`; title is `永久投资组合`, subtitle is
`四资产 · 固定配置与趋势倾斜`.

- [ ] **Step 4: Write failing component tests**

```typescript
// PermanentPortfolioPage.test.tsx
it("keeps sealed holdout results hidden", async () => {
  render(<PermanentPortfolioPage refreshToken={0} />);
  expect(await screen.findByText("盲测已封存")).toBeInTheDocument();
  expect(screen.queryByText("forbidden")).not.toBeInTheDocument();
});

it("switches between development, holdout, and forward windows", async () => {
  render(<PermanentPortfolioPage refreshToken={0} />);
  await userEvent.click(await screen.findByRole("button", { name: "开发期" }));
  expect(screen.getByRole("heading", { name: "收益与波动" })).toBeInTheDocument();
});
```

```typescript
// PermanentPortfolioCharts.test.tsx
it("renders non-empty chart containers for complete series", () => {
  render(<PermanentPortfolioCharts series={completeSeries} />);
  expect(screen.getByLabelText("永久组合净值对比图")).toBeInTheDocument();
  expect(screen.getByLabelText("永久组合回撤图")).toBeInTheDocument();
  expect(screen.getByLabelText("永久组合滚动波动图")).toBeInTheDocument();
});
```

- [ ] **Step 5: Implement the page and charts**

The page uses:

- a segmented control for `开发期 / 盲测期 / 前向纸面期`;
- one compact metric row for fixed, dynamic and three baselines;
- `Lightweight Charts` line series for normalized NAV, drawdown and 63-day
  volatility;
- an unframed SVG stacked area chart for four target/actual weights;
- plain tables for annual returns, monthly returns and trades;
- a compact evidence section for status, hashes and data errors.

No provider requests, nested cards, decorative gradients, oversized headings or
explanatory marketing copy. Keep chart heights stable at 280px desktop and
240px mobile; all labels wrap without overlapping.

- [ ] **Step 6: Run focused frontend tests and commit**

Run:

```bash
cd frontend/dashboard && npm test -- --run src/workspaceRoute.test.ts src/WorkspaceShell.test.tsx src/App.test.tsx src/api.test.ts src/PermanentPortfolioPage.test.tsx src/PermanentPortfolioCharts.test.tsx
```

Expected: all focused frontend tests pass.

Commit after authorization:

```bash
git add frontend/dashboard/src/PermanentPortfolioPage.tsx frontend/dashboard/src/PermanentPortfolioPage.test.tsx frontend/dashboard/src/PermanentPortfolioCharts.tsx frontend/dashboard/src/PermanentPortfolioCharts.test.tsx frontend/dashboard/src/workspaceRoute.ts frontend/dashboard/src/workspaceRoute.test.ts frontend/dashboard/src/WorkspaceShell.tsx frontend/dashboard/src/WorkspaceShell.test.tsx frontend/dashboard/src/App.tsx frontend/dashboard/src/App.test.tsx frontend/dashboard/src/types.ts frontend/dashboard/src/api.ts frontend/dashboard/src/api.test.ts frontend/dashboard/src/styles.css
git commit -m "feat: visualize permanent portfolio research"
```

## Task 10: Document operations and run full verification

**Files:**
- Modify: `docs/system-overview.md`
- Modify: `docs/system-harness.md`
- Test: all backend and frontend suites

- [ ] **Step 1: Update architecture and operational documentation**

Document:

```text
prepare-permanent-portfolio-data
run-permanent-portfolio-development
open-permanent-portfolio-holdout
run-permanent-portfolio-paper
/api/dashboard/permanent-portfolio.json
?view=permanent-portfolio
```

State explicitly that these are research-only accounts, that 2025-2026 is a
single-open holdout, and that formal accounts and model Registry are not readers
or writers.

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
python3 -m unittest \
  tests.test_permanent_portfolio_contract \
  tests.test_permanent_portfolio_data \
  tests.test_permanent_portfolio_signals \
  tests.test_permanent_portfolio_engine \
  tests.test_permanent_portfolio_metrics \
  tests.test_permanent_portfolio_workflow \
  tests.test_permanent_portfolio_paper \
  tests.test_dashboard_permanent_portfolio \
  tests.test_cli_permanent_portfolio \
  tests.test_cli_dashboard_routes -v
```

Expected: all focused tests pass with no warnings.

- [ ] **Step 3: Run repository verification**

Run:

```bash
git diff --check
python3 -m compileall -q stock_analyze tests
python3 -m unittest discover -s tests
cd frontend/dashboard && npm test && npm run build
```

Expected: every command exits 0.

- [ ] **Step 4: Verify the rendered Dashboard**

Build and start the existing server:

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

Use Playwright at desktop `1440x900` and mobile `390x844`. Verify:

- `?view=permanent-portfolio` loads;
- charts are nonblank;
- stage controls do not shift layout;
- no labels or tables overflow;
- sealed holdout returns are absent from DOM and network payload;
- unavailable sections degrade independently.

- [ ] **Step 5: Commit documentation after authorization**

```bash
git add docs/system-overview.md docs/system-harness.md
git commit -m "docs: document permanent portfolio research operations"
```

## Task 11: Produce development results, open the holdout once, and publish evidence

**Files:**
- Generated, immutable: `data/research/permanent_portfolio/v1/manifests/`
- Generated, immutable: `data/research/permanent_portfolio/v1/results/development/`
- Generated, immutable: `data/research/permanent_portfolio/v1/results/holdout/`
- Generated: `reports/research/permanent_portfolio/v1/dashboard.json`

- [ ] **Step 1: Collect and verify the complete source window**

Run on the authorized environment:

```bash
python3 -m stock_analyze prepare-permanent-portfolio-data \
  --contract configs/research/permanent_portfolio_v1.yaml \
  --end 2026-08-28 \
  --repo-root .
```

Expected: a complete publication manifest with four codes, no unexplained open
day gaps, verified total-return adjustment, and SHA-256 values.

- [ ] **Step 2: Seal and execute development**

```bash
python3 -m stock_analyze run-permanent-portfolio-development \
  --contract configs/research/permanent_portfolio_v1.yaml \
  --repo-root .
```

Expected: state `development_complete`, immutable artifact path and SHA-256,
with no input row dated 2025 or later.

- [ ] **Step 3: Audit the development artifact before opening holdout**

Check:

```bash
python3 -m unittest tests.test_permanent_portfolio_workflow -v
git diff --check
```

Then verify the artifact records the committed code revision, config hash, data
hash, all five portfolios, primary metrics and 2x cost stress. Do not alter the
contract based on returns.

- [ ] **Step 4: Open the holdout exactly once**

```bash
DEVELOPMENT_ARTIFACT="$(
  python3 -c 'import json; from pathlib import Path; print(json.loads(Path("data/research/permanent_portfolio/v1/manifests/state.json").read_text())["development_artifact"])'
)"
DEVELOPMENT_SHA256="$(
  python3 -c 'import json; from pathlib import Path; print(json.loads(Path("data/research/permanent_portfolio/v1/manifests/state.json").read_text())["development_sha256"])'
)"
python3 -m stock_analyze open-permanent-portfolio-holdout \
  --contract configs/research/permanent_portfolio_v1.yaml \
  --development-artifact "$DEVELOPMENT_ARTIFACT" \
  --development-sha256 "$DEVELOPMENT_SHA256" \
  --repo-root .
```

Expected: state `holdout_complete`; a second invocation with the same arguments
fails with `permanent_portfolio_holdout_already_opened`.

- [ ] **Step 5: Start the two isolated paper accounts**

```bash
python3 -m stock_analyze run-permanent-portfolio-paper \
  --as-of 2026-08-31 \
  --repo-root .
```

Expected: both `permanent_fixed_v1` and `permanent_dynamic_v1` have independent
state, NAV, positions, pending orders, trades and terminal run ledgers. Existing
formal account files remain byte-for-byte unchanged.

- [ ] **Step 6: Verify final evidence and Dashboard**

Run:

```bash
curl -fsS http://127.0.0.1:8765/api/dashboard/permanent-portfolio.json
```

Verify the payload exposes development, holdout and forward statuses; reports
fixed and dynamic return/volatility/drawdown; includes all three baselines; and
contains no NaN, Infinity, secret or raw provider response.

- [ ] **Step 7: Record the immutable result**

Add a validation document under:

```text
docs/superpowers/validation/2026-08-30-permanent-portfolio-result.md
```

The document records exact artifact hashes, dates, fixed/dynamic metrics,
baseline comparisons, data limitations and whether each result is positive,
negative or insufficient. It must not recommend parameter changes from the
blind result.

Commit generated evidence only after explicit repository-owner authorization;
never commit secrets, raw credentials, transient caches or mutable paper
account ledgers.
