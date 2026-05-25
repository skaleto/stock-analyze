# Historical Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a historical backtest engine that serves both an automatic "gate" (called by `evolution_writer` when LLM commits a new overlay) and a manual research CLI, by parameterizing the clock + data roots of the existing `simulator.py`.

**Architecture:** Reuse `stock_analyze/simulator.py` to drive a day-by-day loop over a historical window, reading point-in-time market data from a separate `data/shared/backtest_cache/`. Forward simulation behavior is preserved (default parameters maintain current behavior). Gate enforces three floor thresholds on validation window (2025-01 → 2026-04); research CLI accepts arbitrary windows. Training (2021-2024) / validation / live OOS (2026-05+) window discipline is documented and partially enforced through monthly briefing detail levels.

**Tech Stack:** Python 3.11+, Tushare Pro (`pro.daily`, `pro.daily_basic`, `pro.fina_indicator`, `pro.index_weight`, `pro.adj_factor`, `pro.stock_basic`, `pro.trade_cal`), pandas, numpy, pytest/unittest, existing `stock_analyze/*` modules.

---

## Reading Guide

This plan has **17 sections** (Tasks 1-17). Sections are sequenced so that each builds on prior work but can also be paused/resumed between sections. Within each task, TDD pattern is used: write failing test → run → implement → run → commit.

For each file modified, see the "Files" header. Existing code is shown with `Modify:` and the relevant line range when known. New code is shown with `Create:`.

Implementation references:
- Design: `openspec/changes/add-historical-backtest-engine/design.md`
- Tasks (high-level): `openspec/changes/add-historical-backtest-engine/tasks.md`
- Proposal: `openspec/changes/add-historical-backtest-engine/proposal.md`

---

## Task 1: OpenSpec foundation and capability specs

**Files:**
- Create: `openspec/changes/add-historical-backtest-engine/specs/historical-backtest-engine/spec.md`
- Create: `openspec/changes/add-historical-backtest-engine/specs/backtest-floor-gate/spec.md`
- Create: `openspec/changes/add-historical-backtest-engine/specs/backtest-research-cli/spec.md`
- Create: `openspec/changes/add-historical-backtest-engine/specs/train-validation-window-discipline/spec.md`

- [ ] **Step 1.1: Create specs/ subdirectory**

```bash
mkdir -p openspec/changes/add-historical-backtest-engine/specs/historical-backtest-engine
mkdir -p openspec/changes/add-historical-backtest-engine/specs/backtest-floor-gate
mkdir -p openspec/changes/add-historical-backtest-engine/specs/backtest-research-cli
mkdir -p openspec/changes/add-historical-backtest-engine/specs/train-validation-window-discipline
```

- [ ] **Step 1.2: Write historical-backtest-engine spec**

Create `openspec/changes/add-historical-backtest-engine/specs/historical-backtest-engine/spec.md` with sections:
- `## Purpose` — single-paragraph statement of the capability
- `## Interface` — `run_backtest(overlay, start, end, universe, data_root, out_dir, *, in_memory=False) -> BacktestResult`
- `## Invariants` — output schema matches forward simulation; reuses simulator.py; respects point-in-time
- `## Acceptance criteria` — engine runs end-to-end on 1-month mock data; outputs daily_nav.csv / trades.csv / signals.csv / performance_summary.json

- [ ] **Step 1.3: Write backtest-floor-gate spec**

Create `openspec/changes/add-historical-backtest-engine/specs/backtest-floor-gate/spec.md`:
- `## Purpose` — guard `evolution_writer` from committing overlays that backtest catastrophically
- `## Interface` — `validate_overlay_via_backtest(overlay) -> Metrics`; raises `BacktestFloorBreach(breach_type, metrics)` on breach
- `## Thresholds` — `max_drawdown <= 0.25`, `sharpe >= -0.5`, `cum_return >= -0.15`, read from `competition.yaml.backtest.floor.*`
- `## Acceptance criteria` — synthetic overlay that crashes -50% triggers breach; passing overlay returns metrics

- [ ] **Step 1.4: Write backtest-research-cli spec**

Create `openspec/changes/add-historical-backtest-engine/specs/backtest-research-cli/spec.md`:
- `## Purpose` — operator can invoke backtest with arbitrary windows and overlays
- `## Interface` — `python3 -m stock_analyze backtest --agent --start --end --overlay --output [--in-memory] [--universe hs300|zz500|both]`
- `## Acceptance criteria` — CLI parses args, validates input, dispatches to engine, writes report

- [ ] **Step 1.5: Write train-validation-window-discipline spec**

Create `openspec/changes/add-historical-backtest-engine/specs/train-validation-window-discipline/spec.md`:
- `## Purpose` — information isolation between training (full detail) and validation (5 aggregate metrics only) windows in monthly briefing
- `## Interface` — `agent_briefing.build_monthly_briefing` renders "训练窗口" with `detail_level=full` and "验证窗口" with `detail_level=aggregate_only`
- `## Acceptance criteria` — aggregate_only mode produces output containing exactly 5 numbers (cum/annual/sharpe/maxDD/IR) with no per-month or per-factor breakdown

- [ ] **Step 1.6: Run openspec validate**

```bash
openspec validate add-historical-backtest-engine --strict
```

Expected: validate passes; warnings about TBD/empty fields if any, fix inline.

- [ ] **Step 1.7: Commit**

```bash
git add openspec/changes/add-historical-backtest-engine/specs/
git commit -m "specs: add 4 capability specs for add-historical-backtest-engine"
```

---

## Task 2: simulator.py clock + path parameterization

**Files:**
- Modify: `stock_analyze/simulator.py` (add `as_of` / `data_root` / `market_data_root` kwargs to `execute_due_orders`, `update_nav`, `generate_rebalance_orders`)
- Test: `tests/test_simulator_clock_injection.py`

- [ ] **Step 2.1: Write failing test for `execute_due_orders(as_of=...)`**

Create `tests/test_simulator_clock_injection.py`:

```python
"""Tests that simulator functions accept as_of and data_root kwargs for backtest mode."""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from stock_analyze import simulator


class ClockInjectionTests(unittest.TestCase):
    def test_execute_due_orders_accepts_as_of_kwarg(self):
        """execute_due_orders should accept an as_of kwarg and use it as 'today'."""
        target_date = date(2023, 6, 30)
        with patch('stock_analyze.simulator._do_execute_orders') as mocked:
            simulator.execute_due_orders(as_of=target_date, data_root=Path('/tmp/x'),
                                          market_data_root=Path('/tmp/y'))
            args, kwargs = mocked.call_args
            self.assertEqual(kwargs.get('today'), target_date)

    def test_execute_due_orders_defaults_to_today_when_as_of_none(self):
        """When as_of is None, execute_due_orders should use date.today()."""
        with patch('stock_analyze.simulator._do_execute_orders') as mocked, \
             patch('stock_analyze.simulator.date') as mocked_date:
            mocked_date.today.return_value = date(2026, 5, 26)
            simulator.execute_due_orders()
            args, kwargs = mocked.call_args
            self.assertEqual(kwargs.get('today'), date(2026, 5, 26))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
python3 -m unittest tests.test_simulator_clock_injection -v
```

Expected: FAIL with `TypeError: execute_due_orders() got an unexpected keyword argument 'as_of'` or similar.

- [ ] **Step 2.3: Refactor `execute_due_orders` in simulator.py**

Open `stock_analyze/simulator.py`. Locate the current `execute_due_orders` function. It currently looks something like:

```python
def execute_due_orders():
    today = datetime.now().date()
    data_root = get_default_data_root()
    market_root = get_default_market_data_root()
    return _do_execute_orders(today=today, data_root=data_root, market_root=market_root)
```

Refactor it to:

```python
def execute_due_orders(
    *,
    as_of: date | None = None,
    data_root: Path | None = None,
    market_data_root: Path | None = None,
):
    """Execute pending orders due by as_of (defaults to today).

    Args:
        as_of: Cutoff date for which orders are due. None = today.
        data_root: Root directory for simulator's own state files (state.json,
                   pending_orders.json, daily_nav.csv, etc). None = default agent dir.
        market_data_root: Root directory for read-only market data cache.
                          None = data/shared/cache/.
    """
    today = as_of if as_of is not None else date.today()
    data_root = data_root if data_root is not None else _default_agent_data_root()
    market_root = market_data_root if market_data_root is not None else _default_market_data_root()
    return _do_execute_orders(today=today, data_root=data_root, market_root=market_root)
```

- [ ] **Step 2.4: Run test again, expect pass for execute_due_orders**

```bash
python3 -m unittest tests.test_simulator_clock_injection.ClockInjectionTests.test_execute_due_orders_accepts_as_of_kwarg -v
python3 -m unittest tests.test_simulator_clock_injection.ClockInjectionTests.test_execute_due_orders_defaults_to_today_when_as_of_none -v
```

Expected: PASS for both.

- [ ] **Step 2.5: Write tests for update_nav and generate_rebalance_orders**

Append to `tests/test_simulator_clock_injection.py`:

```python
    def test_update_nav_accepts_as_of_kwarg(self):
        target_date = date(2023, 6, 30)
        with patch('stock_analyze.simulator._do_update_nav') as mocked:
            simulator.update_nav(as_of=target_date, data_root=Path('/tmp/x'),
                                  market_data_root=Path('/tmp/y'))
            self.assertEqual(mocked.call_args.kwargs.get('today'), target_date)

    def test_generate_rebalance_orders_accepts_as_of_kwarg(self):
        target_date = date(2023, 6, 30)
        with patch('stock_analyze.simulator._do_generate_rebalance') as mocked:
            simulator.generate_rebalance_orders(as_of=target_date, data_root=Path('/tmp/x'),
                                                 market_data_root=Path('/tmp/y'))
            self.assertEqual(mocked.call_args.kwargs.get('today'), target_date)
```

- [ ] **Step 2.6: Run and verify fail**

```bash
python3 -m unittest tests.test_simulator_clock_injection -v
```

Expected: 2 new tests FAIL.

- [ ] **Step 2.7: Refactor update_nav and generate_rebalance_orders**

Same pattern as Step 2.3, applied to `update_nav` and `generate_rebalance_orders`. Each grows three optional kwargs.

- [ ] **Step 2.8: Run all simulator tests**

```bash
python3 -m unittest tests.test_simulator_clock_injection -v
python3 -m unittest discover -s tests -p 'test_simulator*' -v
```

Expected: all PASS. Existing forward-mode simulator tests must still pass — this validates "default args = current behavior".

- [ ] **Step 2.9: Commit**

```bash
git add stock_analyze/simulator.py tests/test_simulator_clock_injection.py
git commit -m "simulator: parameterize clock (as_of) and data roots for backtest mode"
```

---

## Task 3: backtest package skeleton

**Files:**
- Create: `stock_analyze/backtest/__init__.py`
- Create: `stock_analyze/backtest/types.py` (BacktestResult, CoverageReport, etc.)
- Test: `tests/test_backtest_types.py`

- [ ] **Step 3.1: Write failing test for BacktestResult dataclass**

Create `tests/test_backtest_types.py`:

```python
"""Tests for backtest result dataclasses."""
import unittest
from datetime import date
from pathlib import Path

from stock_analyze.backtest.types import BacktestResult, BacktestMetrics


class BacktestResultTests(unittest.TestCase):
    def test_backtest_result_construction(self):
        result = BacktestResult(
            out_dir=Path('/tmp/bt'),
            start=date(2021, 1, 1),
            end=date(2024, 12, 31),
            metrics=BacktestMetrics(
                cum_return=0.183,
                annual_return=0.087,
                sharpe=1.4,
                max_drawdown=-0.087,
                information_ratio=0.92,
            ),
        )
        self.assertEqual(result.metrics.sharpe, 1.4)
        self.assertEqual(result.out_dir, Path('/tmp/bt'))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3.2: Run test, expect ImportError**

```bash
python3 -m unittest tests.test_backtest_types -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'stock_analyze.backtest'`.

- [ ] **Step 3.3: Create backtest package**

Create `stock_analyze/backtest/__init__.py`:

```python
"""Historical backtest engine.

See openspec/changes/add-historical-backtest-engine/design.md for full design.
"""
from stock_analyze.backtest.types import BacktestResult, BacktestMetrics, CoverageReport

__all__ = ['BacktestResult', 'BacktestMetrics', 'CoverageReport']
```

Create `stock_analyze/backtest/types.py`:

```python
"""Backtest result types."""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List


@dataclass
class BacktestMetrics:
    cum_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float  # negative number, e.g. -0.087 for -8.7%
    information_ratio: float


@dataclass
class BacktestResult:
    out_dir: Path
    start: date
    end: date
    metrics: BacktestMetrics


@dataclass
class CoverageReport:
    complete: bool
    missing_weeks: List[str] = None
    missing_pct: float = 0.0
```

- [ ] **Step 3.4: Run test, expect pass**

```bash
python3 -m unittest tests.test_backtest_types -v
```

Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add stock_analyze/backtest/__init__.py stock_analyze/backtest/types.py tests/test_backtest_types.py
git commit -m "backtest: scaffold package + BacktestResult dataclasses"
```

---

## Task 4: Data preparation — Tushare batch fetch

**Files:**
- Create: `stock_analyze/backtest/data_prep.py`
- Test: `tests/test_backtest_data_prep.py`

The data prep CLI is responsible for one-time fetch of 5 years of historical market data into `data/shared/backtest_cache/`. It is idempotent — already-fetched dates are skipped on rerun.

- [ ] **Step 4.1: Write failing test for `prepare_backtest_data` happy path**

Create `tests/test_backtest_data_prep.py`:

```python
"""Tests for backtest data preparation."""
import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import pandas as pd

from stock_analyze.backtest import data_prep


class PrepareBacktestDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache_root = Path(self.tmp.name) / 'backtest_cache'
        self.cache_root.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_daily_csv_per_date(self):
        """prepare_backtest_data fetches pro.daily per date and writes a CSV per date."""
        fake_df = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ'],
            'open': [10.0, 20.0],
            'close': [10.5, 19.8],
            'high': [11.0, 20.5],
            'low': [9.8, 19.5],
            'vol': [1000, 2000],
            'amount': [10000.0, 39600.0],
        })
        with patch('stock_analyze.backtest.data_prep._tushare_pro') as mocked:
            mocked.return_value.daily.return_value = fake_df
            mocked.return_value.trade_cal.return_value = pd.DataFrame({
                'cal_date': ['20210104', '20210105'],
                'is_open': [1, 1],
            })
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4),
                end=date(2021, 1, 5),
                cache_root=self.cache_root,
            )

        for trade_date in ['2021-01-04', '2021-01-05']:
            out = self.cache_root / 'daily' / f'{trade_date}.csv'
            self.assertTrue(out.exists(), f'Expected {out} to exist')
            df = pd.read_csv(out)
            self.assertEqual(len(df), 2)
            self.assertIn('ts_code', df.columns)

    def test_idempotent_skips_existing(self):
        """Already-fetched dates should be skipped on rerun."""
        # Pre-populate one date as "already fetched"
        existing = self.cache_root / 'daily' / '2021-01-04.csv'
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text('ts_code,open,close,high,low,vol,amount\n000001.SZ,10,10.5,11,9.8,1000,10000\n')
        meta_path = self.cache_root / '_meta.json'
        meta_path.write_text(json.dumps({'daily_dates_done': ['2021-01-04']}))

        with patch('stock_analyze.backtest.data_prep._tushare_pro') as mocked:
            mocked.return_value.trade_cal.return_value = pd.DataFrame({
                'cal_date': ['20210104'],
                'is_open': [1],
            })
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4),
                end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
            # daily was NOT called because date was in _meta.json
            mocked.return_value.daily.assert_not_called()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 4.2: Run, expect FAIL (module not found)**

```bash
python3 -m unittest tests.test_backtest_data_prep -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'stock_analyze.backtest.data_prep'`.

- [ ] **Step 4.3: Implement `data_prep.py` (minimal viable)**

Create `stock_analyze/backtest/data_prep.py`:

```python
"""One-time batch fetch of historical market data from Tushare Pro into backtest_cache/."""
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
import tushare as ts


def _tushare_pro():
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        raise RuntimeError('TUSHARE_TOKEN env var not set')
    ts.set_token(token)
    return ts.pro_api()


def _load_meta(cache_root: Path) -> dict:
    meta_path = cache_root / '_meta.json'
    if not meta_path.exists():
        return {'daily_dates_done': [], 'daily_basic_dates_done': [],
                'fina_codes_done': [], 'index_weight_months_done': [],
                'adj_factor_codes_done': [], 'stock_basic_done': False,
                'trade_cal_done': False}
    return json.loads(meta_path.read_text())


def _save_meta(cache_root: Path, meta: dict) -> None:
    (cache_root / '_meta.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def _fetch_trade_cal(pro, start: date, end: date) -> List[str]:
    df = pro.trade_cal(start_date=start.strftime('%Y%m%d'),
                       end_date=end.strftime('%Y%m%d'))
    return df[df['is_open'] == 1]['cal_date'].tolist()


def _fetch_daily_for_date(pro, trade_date: str, out_path: Path) -> None:
    df = pro.daily(trade_date=trade_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)


def _fetch_daily_basic_for_date(pro, trade_date: str, out_path: Path) -> None:
    df = pro.daily_basic(trade_date=trade_date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)


def prepare_backtest_data(start: date, end: date, cache_root: Path,
                           force: bool = False) -> None:
    """Fetch historical market data from Tushare into cache_root/.

    Idempotent: dates already in _meta.json are skipped unless force=True.
    Resumable: progress saved after each batch to _meta.json.
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    pro = _tushare_pro()
    meta = _load_meta(cache_root)

    trade_dates = _fetch_trade_cal(pro, start, end)

    daily_done = set(meta.get('daily_dates_done', []))
    daily_basic_done = set(meta.get('daily_basic_dates_done', []))

    for raw_d in trade_dates:
        d_iso = f'{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:8]}'

        # daily
        if force or d_iso not in daily_done:
            out = cache_root / 'daily' / f'{d_iso}.csv'
            _fetch_daily_for_date(pro, raw_d, out)
            daily_done.add(d_iso)

        # daily_basic
        if force or d_iso not in daily_basic_done:
            out = cache_root / 'daily_basic' / f'{d_iso}.csv'
            _fetch_daily_basic_for_date(pro, raw_d, out)
            daily_basic_done.add(d_iso)

        # Save progress every 20 dates
        if len(daily_done) % 20 == 0:
            meta['daily_dates_done'] = sorted(daily_done)
            meta['daily_basic_dates_done'] = sorted(daily_basic_done)
            _save_meta(cache_root, meta)

    meta['daily_dates_done'] = sorted(daily_done)
    meta['daily_basic_dates_done'] = sorted(daily_basic_done)
    _save_meta(cache_root, meta)
```

- [ ] **Step 4.4: Run test, expect PASS**

```bash
python3 -m unittest tests.test_backtest_data_prep -v
```

Expected: PASS for both tests. If daily test passes but daily_basic was also queried, that's fine — the test only asserts the daily files exist.

- [ ] **Step 4.5: Add tests for fina_indicator, index_weight, adj_factor, stock_basic**

Append to `tests/test_backtest_data_prep.py`:

```python
    def test_fetches_fina_indicator_per_code(self):
        """Each stock's fina_indicator is fetched and written to a per-code file."""
        fake_codes = pd.DataFrame({'ts_code': ['000001.SZ', '000002.SZ'],
                                    'name': ['平安银行', '万科A'],
                                    'list_date': ['19910403', '19910129'],
                                    'industry': ['银行', '房地产']})
        fake_fina = pd.DataFrame({
            'ts_code': ['000001.SZ'] * 4,
            'ann_date': ['20210330', '20210430', '20210830', '20211030'],
            'end_date': ['20201231', '20210331', '20210630', '20210930'],
            'roe': [10.5, 2.5, 5.0, 7.5],
            'grossprofit_margin': [40.0, 39.5, 40.2, 40.8],
            'debt_to_assets': [92.3, 92.5, 92.7, 92.6],
            'netprofit_yoy': [3.5, 5.0, 7.5, 10.0],
        })
        with patch('stock_analyze.backtest.data_prep._tushare_pro') as mocked:
            pro = mocked.return_value
            pro.stock_basic.return_value = fake_codes
            pro.fina_indicator.return_value = fake_fina
            pro.trade_cal.return_value = pd.DataFrame({
                'cal_date': ['20210104'], 'is_open': [1]
            })
            pro.daily.return_value = pd.DataFrame()
            pro.daily_basic.return_value = pd.DataFrame()
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        self.assertTrue((self.cache_root / 'fina_indicator' / '000001.SZ.csv').exists())
```

- [ ] **Step 4.6: Run, expect FAIL (fina_indicator path not yet implemented)**

```bash
python3 -m unittest tests.test_backtest_data_prep.PrepareBacktestDataTests.test_fetches_fina_indicator_per_code -v
```

- [ ] **Step 4.7: Extend `prepare_backtest_data` with fina_indicator, index_weight, adj_factor, stock_basic, trade_cal**

Append the following to `data_prep.py`'s `prepare_backtest_data` function (before the final `_save_meta`):

```python
    # stock_basic (once)
    if force or not meta.get('stock_basic_done', False):
        sb = pro.stock_basic(exchange='', list_status='L',
                              fields='ts_code,symbol,name,area,industry,list_date,delist_date')
        sb.to_csv(cache_root / 'stock_basic.csv', index=False)
        meta['stock_basic_done'] = True

    sb_df = pd.read_csv(cache_root / 'stock_basic.csv')

    # fina_indicator per code
    fina_done = set(meta.get('fina_codes_done', []))
    for code in sb_df['ts_code']:
        if force or code not in fina_done:
            df = pro.fina_indicator(
                ts_code=code,
                start_date=start.strftime('%Y%m%d'),
                end_date=end.strftime('%Y%m%d'),
                fields='ts_code,ann_date,end_date,roe,grossprofit_margin,'
                       'debt_to_assets,netprofit_yoy',
            )
            out = cache_root / 'fina_indicator' / f'{code}.csv'
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False)
            fina_done.add(code)
            if len(fina_done) % 50 == 0:
                meta['fina_codes_done'] = sorted(fina_done)
                _save_meta(cache_root, meta)
    meta['fina_codes_done'] = sorted(fina_done)

    # adj_factor per code (same pattern as fina_indicator)
    adj_done = set(meta.get('adj_factor_codes_done', []))
    for code in sb_df['ts_code']:
        if force or code not in adj_done:
            df = pro.adj_factor(
                ts_code=code,
                start_date=start.strftime('%Y%m%d'),
                end_date=end.strftime('%Y%m%d'),
            )
            out = cache_root / 'adj_factor' / f'{code}.csv'
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False)
            adj_done.add(code)
    meta['adj_factor_codes_done'] = sorted(adj_done)

    # index_weight monthly snapshots for hs300 + zz500
    iw_done = set(meta.get('index_weight_months_done', []))
    current = date(start.year, start.month, 1)
    while current <= end:
        ym = current.strftime('%Y-%m')
        if force or ym not in iw_done:
            for idx_code, fname in [('000300.SH', '000300'), ('000905.SH', '000905')]:
                df = pro.index_weight(
                    index_code=idx_code,
                    trade_date=current.strftime('%Y%m%d'),
                )
                out = cache_root / 'index_weight' / f'{fname}_{ym}.csv'
                out.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(out, index=False)
            iw_done.add(ym)
        # advance to next month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    meta['index_weight_months_done'] = sorted(iw_done)
```

- [ ] **Step 4.8: Run all data_prep tests**

```bash
python3 -m unittest tests.test_backtest_data_prep -v
```

Expected: all PASS.

- [ ] **Step 4.9: Commit**

```bash
git add stock_analyze/backtest/data_prep.py tests/test_backtest_data_prep.py
git commit -m "backtest: prepare_backtest_data CLI (idempotent Tushare fetch)"
```

---

## Task 5: prepare-backtest-data CLI subcommand

**Files:**
- Modify: `stock_analyze/cli.py` (add subcommand)
- Test: `tests/test_cli_prepare_backtest_data.py`

- [ ] **Step 5.1: Write failing test for CLI subcommand**

Create `tests/test_cli_prepare_backtest_data.py`:

```python
"""Tests for prepare-backtest-data CLI subcommand."""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from stock_analyze import cli


class PrepareBacktestDataCLITests(unittest.TestCase):
    def test_subcommand_invokes_prepare_backtest_data(self):
        with patch('stock_analyze.backtest.data_prep.prepare_backtest_data') as mocked:
            cli.main(['prepare-backtest-data', '--start', '2021-01-01', '--end', '2026-04-30'])
            args, kwargs = mocked.call_args
            self.assertEqual(kwargs.get('start') or args[0], date(2021, 1, 1))
            self.assertEqual(kwargs.get('end') or args[1], date(2026, 4, 30))

    def test_subcommand_passes_force_flag(self):
        with patch('stock_analyze.backtest.data_prep.prepare_backtest_data') as mocked:
            cli.main(['prepare-backtest-data', '--start', '2021-01-01',
                      '--end', '2021-01-31', '--force'])
            self.assertTrue(mocked.call_args.kwargs.get('force') is True)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 5.2: Run test, expect FAIL (no such subcommand)**

```bash
python3 -m unittest tests.test_cli_prepare_backtest_data -v
```

- [ ] **Step 5.3: Add subcommand to cli.py**

Open `stock_analyze/cli.py`. Find the `argparse` `add_subparsers()` block. Add:

```python
    # prepare-backtest-data
    p_prep = subparsers.add_parser(
        'prepare-backtest-data',
        help='One-time fetch of historical market data from Tushare into backtest_cache.',
    )
    p_prep.add_argument('--start', type=_parse_date, required=True,
                         help='Start date (YYYY-MM-DD).')
    p_prep.add_argument('--end', type=_parse_date, required=True,
                         help='End date (YYYY-MM-DD).')
    p_prep.add_argument('--cache-root', type=Path,
                         default=Path('data/shared/backtest_cache'),
                         help='Cache root directory.')
    p_prep.add_argument('--force', action='store_true',
                         help='Re-fetch even if already cached.')
    p_prep.set_defaults(func=_cmd_prepare_backtest_data)
```

And add the dispatcher function:

```python
def _cmd_prepare_backtest_data(args):
    from stock_analyze.backtest import data_prep
    data_prep.prepare_backtest_data(
        start=args.start, end=args.end,
        cache_root=args.cache_root, force=args.force,
    )
```

`_parse_date` should already exist; if not, add:

```python
def _parse_date(s: str) -> date:
    return date.fromisoformat(s)
```

- [ ] **Step 5.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_cli_prepare_backtest_data -v
```

- [ ] **Step 5.5: Commit**

```bash
git add stock_analyze/cli.py tests/test_cli_prepare_backtest_data.py
git commit -m "cli: add prepare-backtest-data subcommand"
```

---

## Task 6: PointInTimeView data access layer

**Files:**
- Create: `stock_analyze/backtest/data_view.py`
- Test: `tests/test_backtest_data_view.py`

PointInTimeView is the single chokepoint for all backtest data reads. Its job: given a date `t`, return only data that was knowable at `t` (no future leakage).

- [ ] **Step 6.1: Write failing tests for `PointInTimeView`**

Create `tests/test_backtest_data_view.py`:

```python
"""Tests for backtest's point-in-time data view."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_analyze.backtest.data_view import PointInTimeView


class PointInTimeViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        # Set up minimal cache structure
        (self.cache / 'daily').mkdir(parents=True)
        (self.cache / 'daily_basic').mkdir(parents=True)
        (self.cache / 'fina_indicator').mkdir(parents=True)
        (self.cache / 'index_weight').mkdir(parents=True)

        # daily for 2023-06-29 and 2023-06-30
        pd.DataFrame({'ts_code': ['000001.SZ'], 'close': [12.5], 'open': [12.3],
                      'high': [12.8], 'low': [12.2], 'vol': [1e6], 'amount': [1.25e10]}
                     ).to_csv(self.cache / 'daily' / '2023-06-29.csv', index=False)
        pd.DataFrame({'ts_code': ['000001.SZ'], 'close': [12.7], 'open': [12.5],
                      'high': [12.9], 'low': [12.4], 'vol': [1.1e6], 'amount': [1.40e10]}
                     ).to_csv(self.cache / 'daily' / '2023-06-30.csv', index=False)

        # fina_indicator with two ann_dates
        pd.DataFrame({
            'ts_code': ['000001.SZ', '000001.SZ'],
            'ann_date': ['20230420', '20230820'],
            'end_date': ['20230331', '20230630'],
            'roe': [3.5, 7.0],
        }).to_csv(self.cache / 'fina_indicator' / '000001.SZ.csv', index=False)

        # index_weight for 2023-06
        pd.DataFrame({
            'index_code': ['000300.SH', '000300.SH'],
            'con_code': ['000001.SZ', '000002.SZ'],
            'weight': [0.5, 0.5],
            'trade_date': ['20230601', '20230601'],
        }).to_csv(self.cache / 'index_weight' / '000300_2023-06.csv', index=False)

        (self.cache / 'stock_basic.csv').write_text(
            'ts_code,name,list_date,delist_date,industry\n'
            '000001.SZ,平安银行,19910403,,银行\n'
            '000002.SZ,万科A,19910129,,房地产\n'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_returns_data_for_exact_date(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        df = view.daily(as_of=date(2023, 6, 29))
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]['close'], 12.5)

    def test_fina_indicator_filters_by_ann_date(self):
        """Looking up fina at 2023-06-30 should only see ann_date <= 2023-06-30."""
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        df = view.fina_for_code('000001.SZ', as_of=date(2023, 6, 30))
        # Only 20230420 row visible; 20230820 row is future
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]['roe'], 3.5)

    def test_universe_returns_hs300_constituents_at_date(self):
        view = PointInTimeView(as_of=date(2023, 6, 30), cache_root=self.cache)
        codes = view.universe(as_of=date(2023, 6, 30), indices=['hs300'])
        self.assertIn('000001.SZ', codes)
        self.assertIn('000002.SZ', codes)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 6.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_backtest_data_view -v
```

- [ ] **Step 6.3: Implement PointInTimeView**

Create `stock_analyze/backtest/data_view.py`:

```python
"""Point-in-time data access layer for backtest.

All data reads during backtest go through this layer.
The contract: given as_of date t, return only data knowable at t.
- daily / daily_basic: trade_date < t  (no current-day open access pre-open)
- fina_indicator:      ann_date <= t
- index_weight:        most recent monthly snapshot <= t
- stock_basic:         list_date <= t and (delist_date is null or delist_date > t)
"""
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import pandas as pd


@dataclass
class PointInTimeView:
    as_of: date
    cache_root: Path

    def daily(self, as_of: Optional[date] = None) -> pd.DataFrame:
        d = as_of or self.as_of
        path = self.cache_root / 'daily' / f'{d.isoformat()}.csv'
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path)

    def daily_basic(self, as_of: Optional[date] = None) -> pd.DataFrame:
        d = as_of or self.as_of
        path = self.cache_root / 'daily_basic' / f'{d.isoformat()}.csv'
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path)

    def fina_for_code(self, ts_code: str, as_of: Optional[date] = None) -> pd.DataFrame:
        d = as_of or self.as_of
        path = self.cache_root / 'fina_indicator' / f'{ts_code}.csv'
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path)
        if 'ann_date' not in df.columns:
            return df
        # Convert ann_date (YYYYMMDD int or str) to date
        df['ann_date_parsed'] = pd.to_datetime(df['ann_date'].astype(str), format='%Y%m%d').dt.date
        return df[df['ann_date_parsed'] <= d].drop(columns=['ann_date_parsed'])

    def universe(self, as_of: Optional[date] = None,
                  indices: List[str] = None) -> List[str]:
        d = as_of or self.as_of
        indices = indices or ['hs300', 'zz500']
        code_map = {'hs300': '000300', 'zz500': '000905'}

        all_codes: set[str] = set()
        for idx in indices:
            fname_prefix = code_map[idx]
            # Find most recent monthly snapshot <= d
            iw_dir = self.cache_root / 'index_weight'
            if not iw_dir.exists():
                continue
            target_ym = d.strftime('%Y-%m')
            candidates = sorted(
                p for p in iw_dir.glob(f'{fname_prefix}_*.csv')
                if p.stem.split('_')[1] <= target_ym
            )
            if not candidates:
                continue
            df = pd.read_csv(candidates[-1])
            if 'con_code' in df.columns:
                all_codes |= set(df['con_code'].astype(str))

        # Filter by stock_basic listed-not-delisted
        sb_path = self.cache_root / 'stock_basic.csv'
        if sb_path.exists():
            sb = pd.read_csv(sb_path, dtype={'list_date': str, 'delist_date': str})
            sb = sb[sb['ts_code'].isin(all_codes)]
            sb['list_date_parsed'] = pd.to_datetime(sb['list_date'], format='%Y%m%d').dt.date
            sb = sb[sb['list_date_parsed'] <= d]
            if 'delist_date' in sb.columns:
                # Keep if delist is empty/NaN or > d
                def keep(row):
                    val = row['delist_date']
                    if pd.isna(val) or val in ('', 'nan'):
                        return True
                    try:
                        return pd.to_datetime(val, format='%Y%m%d').date() > d
                    except Exception:
                        return True
                sb = sb[sb.apply(keep, axis=1)]
            return sorted(sb['ts_code'].tolist())
        return sorted(all_codes)
```

- [ ] **Step 6.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_backtest_data_view -v
```

- [ ] **Step 6.5: Add test for "no future leakage"**

Append to `tests/test_backtest_data_view.py`:

```python
    def test_no_future_leakage_in_fina(self):
        """Even if as_of points to a future date, only data with ann_date <= as_of returns."""
        view = PointInTimeView(as_of=date(2024, 1, 1), cache_root=self.cache)
        # At 2023-04-19, no fina row should be visible (first ann_date = 2023-04-20)
        df = view.fina_for_code('000001.SZ', as_of=date(2023, 4, 19))
        self.assertEqual(len(df), 0)
```

- [ ] **Step 6.6: Run, expect PASS**

```bash
python3 -m unittest tests.test_backtest_data_view -v
```

- [ ] **Step 6.7: Commit**

```bash
git add stock_analyze/backtest/data_view.py tests/test_backtest_data_view.py
git commit -m "backtest: PointInTimeView (no future leakage; index_weight monthly snapshot lookup)"
```

---

## Task 7: Engine main loop

**Files:**
- Create: `stock_analyze/backtest/engine.py`
- Test: `tests/test_backtest_engine.py`

The engine loops day-by-day, dispatching to existing `simulator.*` functions with the new `as_of` and `data_root` / `market_data_root` kwargs introduced in Task 2.

- [ ] **Step 7.1: Write failing test for `run_backtest` smoke test (1 week, mock simulator)**

Create `tests/test_backtest_engine.py`:

```python
"""Tests for backtest engine main loop."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from stock_analyze.backtest import engine


class RunBacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = Path(self.tmp.name) / 'cache'
        self.out = Path(self.tmp.name) / 'out'
        self.cache.mkdir(parents=True)
        self.out.mkdir(parents=True)

        # Minimal trade_cal: 5 days, ending on a Friday for rebalance trigger
        (self.cache / 'trade_cal.csv').write_text(
            'cal_date,is_open\n'
            '20230626,1\n20230627,1\n20230628,1\n20230629,1\n20230630,1\n'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_backtest_drives_simulator_one_call_per_day(self):
        """For 5 trading days, simulator funcs should be called 5x each."""
        overlay = {'agent_id': 'claude', 'strategy_id': 'test',
                    'factors': {'pe': {'weight': 1.0, 'direction': 'low'}}}
        with patch('stock_analyze.simulator.execute_due_orders') as ex, \
             patch('stock_analyze.simulator.update_nav') as un, \
             patch('stock_analyze.simulator.generate_rebalance_orders') as gr:
            engine.run_backtest(
                overlay=overlay,
                start=date(2023, 6, 26), end=date(2023, 6, 30),
                universe=['hs300', 'zz500'],
                market_data_root=self.cache, out_dir=self.out,
                in_memory=True,
            )
        self.assertEqual(ex.call_count, 5)
        self.assertEqual(un.call_count, 5)
        # Friday rebalance: 2023-06-30 is a Friday → 1 call
        self.assertEqual(gr.call_count, 1)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 7.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_backtest_engine -v
```

- [ ] **Step 7.3: Implement `engine.run_backtest`**

Create `stock_analyze/backtest/engine.py`:

```python
"""Backtest engine main loop. Reuses simulator.* by parameterizing clock and data roots."""
from datetime import date
from pathlib import Path
from typing import List, Optional

import pandas as pd

from stock_analyze import simulator
from stock_analyze.backtest.types import BacktestResult, BacktestMetrics


SIGNAL_DAY_WEEKDAY = 4  # Friday


def _load_trade_cal(cache_root: Path, start: date, end: date) -> List[date]:
    path = cache_root / 'trade_cal.csv'
    df = pd.read_csv(path, dtype={'cal_date': str})
    df = df[df['is_open'] == 1]
    df['d'] = pd.to_datetime(df['cal_date'], format='%Y%m%d').dt.date
    return df[(df['d'] >= start) & (df['d'] <= end)]['d'].tolist()


def _init_backtest_state(overlay: dict, out_dir: Path) -> None:
    """Initialize a fresh state.json / pending_orders.json / daily_nav.csv in out_dir."""
    import json
    initial_cash = overlay.get('initial_cash', 1_000_000)
    accounts = overlay.get('accounts', {
        'main': {'cash': initial_cash / 2, 'top_n': 50, 'scope': 'hs300'},
        'satellite': {'cash': initial_cash / 2, 'top_n': 50, 'scope': 'zz500'},
    })
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'state.json').write_text(json.dumps({
        'cash_by_account': {acc: conf['cash'] for acc, conf in accounts.items()},
        'positions': {},
    }, indent=2))
    (out_dir / 'pending_orders.json').write_text('[]')
    (out_dir / 'daily_nav.csv').write_text(
        'date,account_id,cash,positions_value,total_value\n')
    (out_dir / 'trades.csv').write_text(
        'date,account_id,ts_code,side,quantity,price,commission,stamp_tax,slippage\n')


def _is_signal_day(d: date) -> bool:
    return d.weekday() == SIGNAL_DAY_WEEKDAY


def _compute_metrics_from_nav(daily_nav_path: Path) -> BacktestMetrics:
    if not daily_nav_path.exists():
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    df = pd.read_csv(daily_nav_path)
    if df.empty:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    portfolio = df.groupby('date')['total_value'].sum()
    if len(portfolio) < 2:
        return BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    returns = portfolio.pct_change().dropna()
    cum = portfolio.iloc[-1] / portfolio.iloc[0] - 1
    n_days = len(returns)
    annual = (1 + returns.mean()) ** 252 - 1 if n_days else 0.0
    vol = returns.std() * (252 ** 0.5) if n_days > 1 else 0.0
    sharpe = annual / vol if vol > 0 else 0.0
    cummax = portfolio.cummax()
    drawdown = portfolio / cummax - 1
    max_dd = drawdown.min()
    # IR approximated as sharpe for MVP
    return BacktestMetrics(
        cum_return=float(cum), annual_return=float(annual),
        sharpe=float(sharpe), max_drawdown=float(max_dd),
        information_ratio=float(sharpe),
    )


def run_backtest(
    overlay: dict,
    start: date,
    end: date,
    universe: List[str],
    market_data_root: Path,
    out_dir: Path,
    *,
    in_memory: bool = False,
) -> BacktestResult:
    """Run a historical backtest of `overlay` over [start, end]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _init_backtest_state(overlay, out_dir)

    trade_cal = _load_trade_cal(market_data_root, start, end)

    for d in trade_cal:
        simulator.execute_due_orders(as_of=d, data_root=out_dir,
                                      market_data_root=market_data_root)
        simulator.update_nav(as_of=d, data_root=out_dir,
                              market_data_root=market_data_root)
        if _is_signal_day(d):
            simulator.generate_rebalance_orders(as_of=d, data_root=out_dir,
                                                  market_data_root=market_data_root)

    metrics = _compute_metrics_from_nav(out_dir / 'daily_nav.csv')
    return BacktestResult(out_dir=out_dir, start=start, end=end, metrics=metrics)
```

- [ ] **Step 7.4: Run test, expect PASS**

```bash
python3 -m unittest tests.test_backtest_engine -v
```

- [ ] **Step 7.5: Add test for `_compute_metrics_from_nav` with synthetic NAV series**

Append to `tests/test_backtest_engine.py`:

```python
class ComputeMetricsTests(unittest.TestCase):
    def test_metrics_with_5pct_gain(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'daily_nav.csv'
            rows = ['date,account_id,cash,positions_value,total_value']
            base = 1_000_000
            for i in range(10):
                v = base * (1 + 0.005 * i)  # +0.5%/day → ~5% over 10 days
                rows.append(f'2023-06-{20+i:02d},main,{v},0,{v}')
            p.write_text('\n'.join(rows))
            m = engine._compute_metrics_from_nav(p)
            self.assertGreater(m.cum_return, 0.04)
            self.assertGreater(m.sharpe, 1.0)
```

- [ ] **Step 7.6: Run, expect PASS**

```bash
python3 -m unittest tests.test_backtest_engine -v
```

- [ ] **Step 7.7: Commit**

```bash
git add stock_analyze/backtest/engine.py tests/test_backtest_engine.py
git commit -m "backtest: engine.run_backtest main loop (reuses simulator funcs)"
```

---

## Task 8: Backtest research CLI subcommand

**Files:**
- Modify: `stock_analyze/cli.py` (add `backtest` subcommand)
- Test: `tests/test_cli_backtest.py`

- [ ] **Step 8.1: Write failing test**

Create `tests/test_cli_backtest.py`:

```python
"""Tests for the `backtest` research CLI subcommand."""
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_analyze import cli


class BacktestCLITests(unittest.TestCase):
    def test_backtest_invokes_run_backtest(self):
        with patch('stock_analyze.backtest.engine.run_backtest') as mocked, \
             patch('stock_analyze.competition.load_overlay') as load_overlay:
            load_overlay.return_value = {'agent_id': 'claude', 'factors': {}}
            cli.main([
                'backtest', '--agent', 'claude',
                '--start', '2021-01-01', '--end', '2024-12-31',
                '--overlay', 'configs/agents/claude.yaml',
                '--output', '/tmp/bt_run',
            ])
            args, kwargs = mocked.call_args
            self.assertEqual(kwargs.get('start') or args[1], date(2021, 1, 1))
            self.assertEqual(kwargs.get('end') or args[2], date(2024, 12, 31))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 8.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_cli_backtest -v
```

- [ ] **Step 8.3: Add `backtest` subcommand to cli.py**

Append to the subparsers section of `stock_analyze/cli.py`:

```python
    # backtest research CLI
    p_bt = subparsers.add_parser(
        'backtest',
        help='Run historical backtest of an overlay over arbitrary window.',
    )
    p_bt.add_argument('--agent', required=True, choices=['claude', 'codex'])
    p_bt.add_argument('--start', type=_parse_date, required=True)
    p_bt.add_argument('--end', type=_parse_date, required=True)
    p_bt.add_argument('--overlay', type=Path, required=True,
                       help='Path to agent overlay YAML.')
    p_bt.add_argument('--output', type=Path, required=True,
                       help='Output directory for backtest products.')
    p_bt.add_argument('--in-memory', action='store_true',
                       help='Skip per-day disk writes.')
    p_bt.add_argument('--universe', default='both',
                       choices=['hs300', 'zz500', 'both'])
    p_bt.add_argument('--cache-root', type=Path,
                       default=Path('data/shared/backtest_cache'))
    p_bt.set_defaults(func=_cmd_backtest)
```

Add dispatcher:

```python
def _cmd_backtest(args):
    from stock_analyze.backtest import engine
    from stock_analyze import competition

    overlay = competition.load_overlay(args.overlay)
    universe = {'hs300': ['hs300'], 'zz500': ['zz500'],
                 'both': ['hs300', 'zz500']}[args.universe]
    result = engine.run_backtest(
        overlay=overlay,
        start=args.start, end=args.end,
        universe=universe,
        market_data_root=args.cache_root,
        out_dir=args.output,
        in_memory=args.in_memory,
    )
    print(f'✓ backtest complete: {result.metrics}')
```

- [ ] **Step 8.4: Run test, expect PASS**

```bash
python3 -m unittest tests.test_cli_backtest -v
```

- [ ] **Step 8.5: Commit**

```bash
git add stock_analyze/cli.py tests/test_cli_backtest.py
git commit -m "cli: add backtest research subcommand"
```

---

## Task 9: Markdown report renderer

**Files:**
- Create: `stock_analyze/backtest/report.py`
- Test: `tests/test_backtest_report.py`

- [ ] **Step 9.1: Write failing test**

Create `tests/test_backtest_report.py`:

```python
"""Tests for backtest markdown report renderer."""
import unittest
from datetime import date
from pathlib import Path

from stock_analyze.backtest.report import render_markdown_report
from stock_analyze.backtest.types import BacktestMetrics, BacktestResult


class RenderMarkdownReportTests(unittest.TestCase):
    def test_renders_4_sections(self):
        result = BacktestResult(
            out_dir=Path('/tmp/bt'),
            start=date(2023, 1, 1), end=date(2024, 12, 31),
            metrics=BacktestMetrics(
                cum_return=0.183, annual_return=0.087, sharpe=1.4,
                max_drawdown=-0.087, information_ratio=0.92,
            ),
        )
        md = render_markdown_report(result)
        self.assertIn('## 总结', md)
        self.assertIn('## 因子贡献分解', md)
        self.assertIn('## 月度热力图', md)
        self.assertIn('## 风险归因', md)
        # Numbers appear in summary
        self.assertIn('+18.3%', md)
        self.assertIn('1.4', md)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 9.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_backtest_report -v
```

- [ ] **Step 9.3: Implement render_markdown_report**

Create `stock_analyze/backtest/report.py`:

```python
"""Render a backtest result as markdown report (and dashboard fragment)."""
from stock_analyze.backtest.types import BacktestResult


def render_markdown_report(result: BacktestResult) -> str:
    m = result.metrics
    lines = [
        f'# 回测报告 · {result.start.isoformat()} → {result.end.isoformat()}',
        '',
        '## 总结',
        '',
        f'- 累计收益: {m.cum_return:+.1%}',
        f'- 年化收益: {m.annual_return:+.1%}',
        f'- Sharpe: {m.sharpe:.2f}',
        f'- 最大回撤: {m.max_drawdown:+.1%}',
        f'- 信息比率: {m.information_ratio:.2f}',
        '',
        '## 因子贡献分解',
        '',
        '(MVP: 因子贡献分解需要 factor_runs/*.csv 输入。后续 PR 接入。)',
        '',
        '## 月度热力图',
        '',
        '(MVP: 需要按月聚合 daily_nav.csv。后续 PR 接入。)',
        '',
        '## 风险归因',
        '',
        f'- 单月最差: (MVP 占位)',
        f'- 单月最佳: (MVP 占位)',
        '',
    ]
    return '\n'.join(lines)
```

- [ ] **Step 9.4: Run, expect PASS**

```bash
python3 -m unittest tests.test_backtest_report -v
```

- [ ] **Step 9.5: Hook report into CLI**

Append at end of `_cmd_backtest` in `stock_analyze/cli.py`:

```python
    from stock_analyze.backtest.report import render_markdown_report
    md = render_markdown_report(result)
    (args.output / 'report.md').write_text(md)
    print(f'✓ report written: {args.output / "report.md"}')
```

- [ ] **Step 9.6: Commit**

```bash
git add stock_analyze/backtest/report.py tests/test_backtest_report.py stock_analyze/cli.py
git commit -m "backtest: markdown report renderer + hook into research CLI"
```

---

## Task 10: competition.yaml backtest.floor configuration

**Files:**
- Modify: `configs/competition.yaml` (add backtest.floor.* fields)
- Modify: `stock_analyze/competition.py` (loader exposes backtest.floor)
- Test: `tests/test_competition_backtest_floor.py`

**Note:** `configs/competition.yaml` is normally locked. This task is a one-time exception that introduces new fields. `backtest.floor.*` are explicitly NOT in the locked field set; agent overlays cannot override them.

- [ ] **Step 10.1: Write failing test for loader**

Create `tests/test_competition_backtest_floor.py`:

```python
"""Tests that competition loader exposes backtest.floor.*"""
import unittest

from stock_analyze import competition


class BacktestFloorTests(unittest.TestCase):
    def test_loader_exposes_backtest_floor_defaults(self):
        cfg = competition.load()
        self.assertIn('backtest', cfg)
        floor = cfg['backtest']['floor']
        self.assertAlmostEqual(floor['max_drawdown'], 0.25)
        self.assertAlmostEqual(floor['sharpe_floor'], -0.5)
        self.assertAlmostEqual(floor['cum_return_floor'], -0.15)

    def test_agent_overlay_cannot_override_backtest_floor(self):
        """backtest.floor is non-locked but agent overlay cannot include it."""
        with self.assertRaises(competition.OverlayUnknownField):
            competition.merge_overlay({'backtest': {'floor': {'max_drawdown': 0.1}}})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 10.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_competition_backtest_floor -v
```

- [ ] **Step 10.3: Add backtest.floor.* to competition.yaml**

Open `configs/competition.yaml`. Add (location: a new top-level section):

```yaml
backtest:
  floor:
    max_drawdown: 0.25
    sharpe_floor: -0.5
    cum_return_floor: -0.15
```

**Be careful** — this file uses JSON syntax (project convention despite `.yaml` extension). Make sure to add as JSON:

```json
{
  ...,
  "backtest": {
    "floor": {
      "max_drawdown": 0.25,
      "sharpe_floor": -0.5,
      "cum_return_floor": -0.15
    }
  }
}
```

- [ ] **Step 10.4: Verify competition loader supports new section**

The existing loader in `stock_analyze/competition.py` should already pass through nested sections it doesn't know about. If `OverlayUnknownField` is needed but doesn't exist, add it:

```python
class OverlayUnknownField(Exception):
    pass


# Inside merge_overlay or validate_overlay:
TOP_LEVEL_FIELDS = {'agent_id', 'strategy_id', 'name',
                     'factors', 'factor_processing',
                     'portfolio_controls', 'filters'}

def merge_overlay(overlay: dict, baseline: dict) -> dict:
    for k in overlay.keys():
        if k not in TOP_LEVEL_FIELDS:
            raise OverlayUnknownField(f'Overlay cannot contain top-level field: {k}')
    # ... rest of merge logic
```

- [ ] **Step 10.5: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_competition_backtest_floor -v
```

- [ ] **Step 10.6: Commit**

```bash
git add configs/competition.yaml stock_analyze/competition.py tests/test_competition_backtest_floor.py
git commit -m "competition: add backtest.floor.* non-locked config (defaults 0.25/-0.5/-0.15)"
```

---

## Task 11: Gate — validate_overlay_via_backtest

**Files:**
- Create: `stock_analyze/backtest/gate.py`
- Create: `stock_analyze/backtest/exceptions.py`
- Test: `tests/test_backtest_gate.py`

- [ ] **Step 11.1: Write failing test**

Create `tests/test_backtest_gate.py`:

```python
"""Tests for backtest floor gate."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze.backtest import gate
from stock_analyze.backtest.exceptions import BacktestFloorBreach
from stock_analyze.backtest.types import BacktestMetrics, BacktestResult


class ValidateOverlayViaBacktestTests(unittest.TestCase):
    def test_breach_on_max_drawdown(self):
        bad = BacktestResult(
            out_dir=Path('/tmp'), start=date(2025, 1, 1), end=date(2026, 4, 30),
            metrics=BacktestMetrics(
                cum_return=-0.2, annual_return=-0.15, sharpe=-0.8,
                max_drawdown=-0.32, information_ratio=-1.4,
            ),
        )
        with patch('stock_analyze.backtest.engine.run_backtest', return_value=bad):
            with self.assertRaises(BacktestFloorBreach) as ctx:
                gate.validate_overlay_via_backtest({'agent_id': 'claude', 'factors': {}})
            self.assertEqual(ctx.exception.breach_type, 'max_drawdown_exceeded')

    def test_pass_on_acceptable_overlay(self):
        good = BacktestResult(
            out_dir=Path('/tmp'), start=date(2025, 1, 1), end=date(2026, 4, 30),
            metrics=BacktestMetrics(
                cum_return=0.05, annual_return=0.04, sharpe=0.8,
                max_drawdown=-0.10, information_ratio=0.6,
            ),
        )
        with patch('stock_analyze.backtest.engine.run_backtest', return_value=good):
            metrics = gate.validate_overlay_via_backtest({'agent_id': 'claude', 'factors': {}})
            self.assertAlmostEqual(metrics.sharpe, 0.8)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 11.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_backtest_gate -v
```

- [ ] **Step 11.3: Implement exceptions.py**

Create `stock_analyze/backtest/exceptions.py`:

```python
"""Exceptions raised by backtest layer."""
from stock_analyze.backtest.types import BacktestMetrics


class BacktestFloorBreach(Exception):
    """Raised when an overlay's validation-window backtest fails one of the floor thresholds."""

    def __init__(self, breach_type: str, metrics: BacktestMetrics):
        self.breach_type = breach_type
        self.metrics = metrics
        super().__init__(f'Backtest floor breach: {breach_type}; metrics={metrics}')
```

- [ ] **Step 11.4: Implement gate.py**

Create `stock_analyze/backtest/gate.py`:

```python
"""Backtest floor gate. Called by evolution_writer before committing a new overlay."""
from datetime import date
from pathlib import Path

from stock_analyze import competition
from stock_analyze.backtest import engine
from stock_analyze.backtest.exceptions import BacktestFloorBreach
from stock_analyze.backtest.types import BacktestMetrics


VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2026, 4, 30)


def validate_overlay_via_backtest(
    overlay: dict,
    *,
    cache_root: Path = Path('data/shared/backtest_cache'),
    out_dir: Path = Path('data/_temp/backtest_validation'),
) -> BacktestMetrics:
    """Run validation-window backtest of overlay; raise BacktestFloorBreach on any breach."""
    cfg = competition.load()
    floor = cfg['backtest']['floor']

    result = engine.run_backtest(
        overlay=overlay,
        start=VALIDATION_START, end=VALIDATION_END,
        universe=['hs300', 'zz500'],
        market_data_root=cache_root,
        out_dir=out_dir,
        in_memory=True,
    )

    m = result.metrics
    if m.max_drawdown < -floor['max_drawdown']:  # max_dd is negative; threshold is positive
        raise BacktestFloorBreach('max_drawdown_exceeded', m)
    if m.sharpe < floor['sharpe_floor']:
        raise BacktestFloorBreach('sharpe_below_floor', m)
    if m.cum_return < floor['cum_return_floor']:
        raise BacktestFloorBreach('cum_return_below_floor', m)

    return m
```

- [ ] **Step 11.5: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_backtest_gate -v
```

- [ ] **Step 11.6: Commit**

```bash
git add stock_analyze/backtest/gate.py stock_analyze/backtest/exceptions.py tests/test_backtest_gate.py
git commit -m "backtest: floor gate (max_dd / sharpe / cum_return checks)"
```

---

## Task 12: evolution_writer integration

**Files:**
- Modify: `stock_analyze/evolution_writer.py`
- Test: `tests/test_evolution_writer_backtest_gate.py`

- [ ] **Step 12.1: Write failing test**

Create `tests/test_evolution_writer_backtest_gate.py`:

```python
"""Tests for evolution_writer's backtest gate integration."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import evolution_writer
from stock_analyze.backtest.exceptions import BacktestFloorBreach
from stock_analyze.backtest.types import BacktestMetrics


class EvolutionWriterBacktestGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_breach_aborts_yaml_write_and_writes_breach_log(self):
        with patch('stock_analyze.evolution_writer.overlay_guard.validate'), \
             patch('stock_analyze.evolution_writer.gate.validate_overlay_via_backtest') as g:
            g.side_effect = BacktestFloorBreach(
                'max_drawdown_exceeded',
                BacktestMetrics(-0.2, -0.15, -0.8, -0.32, -1.4),
            )
            old = {'agent_id': 'claude', 'factors': {}}
            new = {'agent_id': 'claude', 'factors': {'pe': {'weight': 0.95, 'direction': 'low'}}}
            with self.assertRaises(BacktestFloorBreach):
                evolution_writer.write_evolution(
                    agent_id='claude', old_overlay=old, new_overlay=new,
                    reasoning_md='# test', month='2026-06',
                    repo_root=self.repo,
                )
            breach_log = self.repo / 'data' / 'claude' / 'evolution_log' / '2026-06-floor-breach.md'
            self.assertTrue(breach_log.exists())
            self.assertIn('max_drawdown_exceeded', breach_log.read_text())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 12.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_evolution_writer_backtest_gate -v
```

- [ ] **Step 12.3: Modify evolution_writer.write_evolution**

Open `stock_analyze/evolution_writer.py`. Find the `write_evolution` function. After `overlay_guard.validate(...)` call, insert:

```python
    from stock_analyze.backtest import gate
    from stock_analyze.backtest.exceptions import BacktestFloorBreach

    try:
        metrics = gate.validate_overlay_via_backtest(new_overlay)
    except BacktestFloorBreach as breach:
        # Write breach log; do NOT touch yaml or _history.
        _write_breach_log(
            agent_id=agent_id, month=month, breach=breach,
            reasoning_md=reasoning_md, repo_root=repo_root,
        )
        raise  # bubble up to abort commit
```

Then add helper:

```python
def _write_breach_log(agent_id, month, breach, reasoning_md, repo_root):
    breach_dir = repo_root / 'data' / agent_id / 'evolution_log'
    breach_dir.mkdir(parents=True, exist_ok=True)
    out = breach_dir / f'{month}-floor-breach.md'
    content = (
        f'# {agent_id} 回测准入失败 · {month}\n\n'
        f'## 失败原因\n- 类型: {breach.breach_type}\n'
        f'- 验证窗口指标: {breach.metrics}\n\n'
        f'## LLM 的原始 reasoning\n\n{reasoning_md}\n'
    )
    out.write_text(content)
```

Also, in the happy-path branch, ensure `metrics` is injected into the evolution_log and evolution_diff JSON (these already exist; just thread `metrics` through their signatures).

- [ ] **Step 12.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_evolution_writer_backtest_gate -v
```

- [ ] **Step 12.5: Run full evolution_writer test suite**

```bash
python3 -m unittest discover -s tests -p 'test_evolution_writer*' -v
```

Expected: existing tests still pass (because mocked gate returns metrics in happy path).

- [ ] **Step 12.6: Commit**

```bash
git add stock_analyze/evolution_writer.py tests/test_evolution_writer_backtest_gate.py
git commit -m "evolution_writer: integrate backtest gate (breach → abort + breach log)"
```

---

## Task 13: agent_briefing — information isolation between training and validation

**Files:**
- Modify: `stock_analyze/agent_briefing.py`
- Test: `tests/test_agent_briefing_backtest_isolation.py`

- [ ] **Step 13.1: Write failing test**

Create `tests/test_agent_briefing_backtest_isolation.py`:

```python
"""Tests that monthly briefing isolates training (full detail) from validation (aggregate-only)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import agent_briefing


class BriefingIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / 'data' / 'claude' / 'backtest' / 'training' / '2026-06').mkdir(parents=True)
        (self.repo / 'data' / 'claude' / 'backtest' / 'validation' / '2026-06').mkdir(parents=True)

        # Synthetic outputs
        for kind in ['training', 'validation']:
            d = self.repo / 'data' / 'claude' / 'backtest' / kind / '2026-06'
            (d / 'performance_summary.json').write_text(
                '{"cum_return": 0.183, "annual_return": 0.087, "sharpe": 1.4, '
                '"max_drawdown": -0.087, "information_ratio": 0.92, '
                '"month_breakdown": [{"month": "2026-01", "ret": 0.02}]}'
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_validation_section_shows_only_5_aggregate_numbers(self):
        text = agent_briefing.render_validation_section(
            agent_id='claude', month='2026-06', repo_root=self.repo,
        )
        # Must contain 5 metric labels
        for label in ['累计', '年化', 'Sharpe', '最大回撤', 'IR']:
            self.assertIn(label, text)
        # Must NOT contain monthly breakdown
        self.assertNotIn('2026-01', text)
        self.assertNotIn('month_breakdown', text)

    def test_training_section_shows_full_detail(self):
        text = agent_briefing.render_training_section(
            agent_id='claude', month='2026-06', repo_root=self.repo,
        )
        # Should contain monthly breakdown
        self.assertIn('2026-01', text)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 13.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_agent_briefing_backtest_isolation -v
```

- [ ] **Step 13.3: Implement render_training_section and render_validation_section**

In `stock_analyze/agent_briefing.py`, add:

```python
def render_validation_section(agent_id: str, month: str, repo_root: Path) -> str:
    """Render validation-window backtest summary: ONLY 5 aggregate metrics.

    NO monthly breakdown, NO factor decomposition. Information isolation
    by design (CLAUDE.md §10 / AGENTS.md §10).
    """
    import json
    path = repo_root / 'data' / agent_id / 'backtest' / 'validation' / month / 'performance_summary.json'
    if not path.exists():
        return f'## 验证窗口表现\n\n(尚无数据)\n'
    p = json.loads(path.read_text())
    return (
        f'## 验证窗口表现\n\n'
        f'- 累计: {p.get("cum_return", 0):+.1%}\n'
        f'- 年化: {p.get("annual_return", 0):+.1%}\n'
        f'- Sharpe: {p.get("sharpe", 0):.2f}\n'
        f'- 最大回撤: {p.get("max_drawdown", 0):+.1%}\n'
        f'- IR: {p.get("information_ratio", 0):.2f}\n'
    )


def render_training_section(agent_id: str, month: str, repo_root: Path) -> str:
    """Render training-window backtest summary: full detail."""
    import json
    path = repo_root / 'data' / agent_id / 'backtest' / 'training' / month / 'performance_summary.json'
    if not path.exists():
        return f'## 训练窗口表现\n\n(尚无数据)\n'
    p = json.loads(path.read_text())
    lines = [
        f'## 训练窗口表现',
        '',
        f'- 累计: {p.get("cum_return", 0):+.1%}',
        f'- 年化: {p.get("annual_return", 0):+.1%}',
        f'- Sharpe: {p.get("sharpe", 0):.2f}',
        f'- 最大回撤: {p.get("max_drawdown", 0):+.1%}',
        f'- IR: {p.get("information_ratio", 0):.2f}',
        '',
        f'### 月度明细',
        '',
    ]
    for row in p.get('month_breakdown', []):
        lines.append(f'- {row["month"]}: {row["ret"]:+.2%}')
    return '\n'.join(lines)
```

Wire into `build_monthly_briefing`: insert calls in the briefing template at appropriate sections.

- [ ] **Step 13.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_agent_briefing_backtest_isolation -v
```

- [ ] **Step 13.5: Commit**

```bash
git add stock_analyze/agent_briefing.py tests/test_agent_briefing_backtest_isolation.py
git commit -m "agent_briefing: information isolation between training (full) and validation (5 metrics) windows"
```

---

## Task 14: Dashboard — backtest vs live panel + strategy evolution timeline column

**Files:**
- Modify: `stock_analyze/reporting.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Test: `tests/test_dashboard_backtest_panel.py`

- [ ] **Step 14.1: Write failing test for backtest-vs-live panel renderer**

Create `tests/test_dashboard_backtest_panel.py`:

```python
"""Tests for dashboard backtest-vs-live panel."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import reporting


class BacktestVsLivePanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        # synthetic training daily_nav.csv
        d = self.repo / 'data' / 'claude' / 'backtest' / 'training' / '2026-06'
        d.mkdir(parents=True)
        (d / 'daily_nav.csv').write_text(
            'date,account_id,cash,positions_value,total_value\n'
            '2021-01-04,main,500000,0,500000\n'
            '2024-12-30,main,650000,0,650000\n'
        )
        # synthetic live daily_nav.csv
        live = self.repo / 'data' / 'claude'
        live.mkdir(parents=True, exist_ok=True)
        (live / 'daily_nav.csv').write_text(
            'date,account_id,cash,positions_value,total_value\n'
            '2026-05-18,main,500000,0,500000\n'
            '2026-05-25,main,510000,0,510000\n'
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_panel_contains_both_backtest_and_live_series(self):
        html = reporting.render_backtest_vs_live_panel(
            agent_id='claude', repo_root=self.repo,
        )
        self.assertIn('历史回测', html)
        self.assertIn('真实运行', html)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 14.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_dashboard_backtest_panel -v
```

- [ ] **Step 14.3: Implement `render_backtest_vs_live_panel`**

In `stock_analyze/reporting.py`, add:

```python
def render_backtest_vs_live_panel(agent_id: str, repo_root: Path) -> str:
    """Render the historical-backtest-vs-live-NAV panel.

    Reads:
      - data/<agent>/backtest/training/<latest_month>/daily_nav.csv  (lighter shade)
      - data/<agent>/daily_nav.csv                                    (live, darker shade)
    """
    import pandas as pd

    # Find latest training run
    train_root = repo_root / 'data' / agent_id / 'backtest' / 'training'
    if not train_root.exists():
        return '<div class="panel">历史回测面板：尚无训练窗口回测数据。请先跑 prepare-backtest-data + 自动月度训练回测。</div>'
    candidates = sorted(p for p in train_root.iterdir() if p.is_dir())
    if not candidates:
        return '<div class="panel">历史回测面板：尚无数据。</div>'
    bt_nav_path = candidates[-1] / 'daily_nav.csv'

    live_nav_path = repo_root / 'data' / agent_id / 'daily_nav.csv'

    bt_df = pd.read_csv(bt_nav_path) if bt_nav_path.exists() else pd.DataFrame()
    live_df = pd.read_csv(live_nav_path) if live_nav_path.exists() else pd.DataFrame()

    bt_portfolio = bt_df.groupby('date')['total_value'].sum() if not bt_df.empty else pd.Series()
    live_portfolio = live_df.groupby('date')['total_value'].sum() if not live_df.empty else pd.Series()

    bt_cum = (bt_portfolio.iloc[-1] / bt_portfolio.iloc[0] - 1) if len(bt_portfolio) >= 2 else 0.0
    live_cum = (live_portfolio.iloc[-1] / live_portfolio.iloc[0] - 1) if len(live_portfolio) >= 2 else 0.0
    diff = bt_cum - live_cum

    warn_class = 'warn' if abs(diff) > 0.05 else ''

    return f'''
<div class="panel">
  <h3>历史回测 vs 真实运行</h3>
  <div class="chart-placeholder">[折线图 — 实际渲染时插入 SVG]</div>
  <table>
    <tr><th></th><th>累计</th></tr>
    <tr><td>历史回测</td><td>{bt_cum:+.1%}</td></tr>
    <tr><td>真实运行</td><td>{live_cum:+.1%}</td></tr>
    <tr class="{warn_class}"><td>差异</td><td>{diff:+.1%}</td></tr>
  </table>
</div>
'''.strip()
```

- [ ] **Step 14.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_dashboard_backtest_panel -v
```

- [ ] **Step 14.5: Add column to strategy evolution timeline**

Open `stock_analyze/reporting.py`. Find `render_strategy_evolution_panel`. Locate the table-rendering portion. Add a new column "验证回测指标" reading from `evolution_diff/<month>.json::backtest_metrics.{cum_return, sharpe, max_drawdown}`:

```python
# In the timeline row builder:
bt_metrics = evolution_diff.get('backtest_metrics', {})
backtest_cell = (
    f'{bt_metrics.get("cum_return", 0):+.1%} / '
    f'S={bt_metrics.get("sharpe", 0):.1f} / '
    f'DD={bt_metrics.get("max_drawdown", 0):+.0%}'
    if bt_metrics else '-'
)
# Inject `backtest_cell` into the table row HTML
```

- [ ] **Step 14.6: Commit**

```bash
git add stock_analyze/reporting.py stock_analyze/dashboard_aggregator.py tests/test_dashboard_backtest_panel.py
git commit -m "dashboard: backtest-vs-live panel + strategy evolution timeline column for backtest metrics"
```

---

## Task 15: CLAUDE.md / AGENTS.md updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

**Note:** `CLAUDE.md` / `AGENTS.md` are normally locked. This task is a one-time exception to add the train/validation/live OOS discipline rules. Operator approves these specific edits.

- [ ] **Step 15.1: Add §9 — three-window discipline to CLAUDE.md**

Open `CLAUDE.md`. Add a new sub-section under §9 "Tool usage tips":

```markdown
### 9.x 三段窗口纪律（由 add-historical-backtest-engine 引入）

回测引擎将历史时间划分为三段：

- **训练窗口** (2021-01 ~ 2024-12, 48 个月): 你可以读月度明细、因子贡献、单股贡献，自由探索。
- **验证窗口** (2025-01 ~ 2026-04, 16 个月): briefing **只展示 5 个总结指标** (累计 / 年化 / Sharpe / 最大回撤 / IR)，**不展示月度明细、不展示因子分解**。这是 gate 准入判定用的。
- **Live OOS** (2026-05-18+): 真实竞赛，没有任何回测可读。

不允许针对验证窗口的失败结果反向迭代你的 overlay；应基于训练窗口的发现重新设计。这是软约束，工程层无法强制，但通过 briefing 信息密度控制降低风险。
```

- [ ] **Step 15.2: Update §5b — monthly strategy evolution flow**

In §5b (Monthly strategy evolution), after the existing "validate-overlay" step, add:

```markdown
6. **The gate runs backtest automatically.** `evolution_writer.write_evolution` calls
   `backtest.gate.validate_overlay_via_backtest(new_overlay)` after `overlay_guard.validate`.
   - If backtest floor breach → yaml is NOT written, a `<month>-floor-breach.md` is
     created with the breach reason, and you must redesign.
   - If backtest passes → metrics are recorded in evolution_log and evolution_diff;
     commit proceeds.

7. (Renumbered from old 6) Human operator triggers `./scripts/sync-to-ecs.sh` to push.
```

- [ ] **Step 15.3: Mirror changes into AGENTS.md**

Apply equivalent edits to `AGENTS.md` (same 9.x discipline + 5b backtest gate step). The two files mirror each other.

- [ ] **Step 15.4: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: add three-window discipline + backtest gate step to operating manuals"
```

---

## Task 16: System documentation

**Files:**
- Create: `docs/historical-backtest-flow.md`
- Modify: `docs/system-overview.md`

- [ ] **Step 16.1: Create docs/historical-backtest-flow.md**

Write a comprehensive operator's guide covering:
- 3-window discipline (training / validation / live OOS)
- One-time prepare-backtest-data workflow + ETA
- How the gate works during monthly evolution
- Research CLI usage
- Reading the dashboard panel
- Floor thresholds and how to tune (operator can edit competition.yaml.backtest.floor)
- Rollback if a gate breach blocks an overlay you really want

Aim for ~250-300 lines markdown.

- [ ] **Step 16.2: Update docs/system-overview.md**

Remove §1's "不是回测系统" claim. Update §16 "限制与不在范围" — remove the "历史回测留给 change" line. Remove §17 (路线图) old item #1.

In §4c, after the existing monthly flow description, add a sentence:

```markdown
ECS 端 monthly review 完成后，自动跑一次该 agent 的训练窗口回测（2021-2024），
落 `data/<agent>/backtest/training/<YYYY-MM>/`，供 monthly briefing 引用。
```

In §13 "关键产物清单"，append:

```
| data/<agent>/backtest/<run_id>/daily_nav.csv | backtest engine | 回测每日 NAV |
| data/<agent>/backtest/<run_id>/performance_summary.json | backtest engine | 回测全套指标 |
| data/<agent>/backtest/training/<YYYY-MM>/* | monthly-review hook | 每月训练窗口回测 |
| data/<agent>/backtest/validation/<YYYY-MM>/* | evolution_writer gate | 每月验证窗口回测 |
| data/<agent>/evolution_log/<YYYY-MM>-floor-breach.md | evolution_writer | 回测 gate breach 时的报告 |
```

- [ ] **Step 16.3: Commit**

```bash
git add docs/historical-backtest-flow.md docs/system-overview.md
git commit -m "docs: add historical-backtest-flow.md + update system-overview"
```

---

## Task 17: End-to-end manual validation

**Files:**
- (no files; this is a manual verification task)

These steps are operator-driven and serve as the final acceptance gate before declaring this change complete.

- [ ] **Step 17.1: Prepare backtest data for a SMALL window first (1 month, smoke test)**

```bash
python3 -m stock_analyze prepare-backtest-data \
  --start 2024-01-01 --end 2024-01-31
```

Expected: completes in ~3-5 minutes. `data/shared/backtest_cache/_meta.json` shows all 7 data types present.

- [ ] **Step 17.2: Run backtest research CLI on the small window**

```bash
python3 -m stock_analyze backtest \
  --agent claude \
  --start 2024-01-04 --end 2024-01-31 \
  --overlay configs/agents/claude.yaml \
  --output data/claude/backtest/smoke-test-2024-01 \
  --in-memory
```

Expected:
- Completes in ~30-60 seconds for 1 month
- `data/claude/backtest/smoke-test-2024-01/report.md` exists with 4 sections
- Performance numbers are not all zero / NaN

- [ ] **Step 17.3: Test gate breach scenario with fixture overlay**

Create a fixture overlay that should breach the gate (e.g. an obviously broken weight scheme). Use a test path, NOT `configs/agents/claude.yaml`:

```bash
mkdir -p /tmp/breach-test
cat > /tmp/breach-test/overlay.yaml <<EOF
{
  "agent_id": "claude",
  "strategy_id": "test-breach",
  "name": "Should breach floor",
  "factors": {"pe": {"weight": 0.95, "direction": "high"}},
  "factor_processing": {"winsorize_lower": 0.01, "winsorize_upper": 0.99},
  "portfolio_controls": {"max_industry_weight": 1.0, "hold_buffer_pct": 0.5,
                          "max_holding_days": 365, "industry_unclassified_label": "未分类"},
  "filters": {"exclude_st": true, "max_fetch_candidates": 850,
              "min_listing_days": 365, "min_pe": -1000, "min_avg_amount_20": 0,
              "min_market_cap_yi": 0, "max_market_cap_yi": 100000,
              "require_fields": [], "fallback_require_fields": []}
}
EOF

python3 -c "
from pathlib import Path
import json
from stock_analyze.backtest import gate
from stock_analyze.backtest.exceptions import BacktestFloorBreach
overlay = json.loads(Path('/tmp/breach-test/overlay.yaml').read_text())
try:
    metrics = gate.validate_overlay_via_backtest(overlay)
    print('UNEXPECTED PASS:', metrics)
except BacktestFloorBreach as b:
    print(f'EXPECTED BREACH: {b.breach_type}')
"
```

Expected: prints `EXPECTED BREACH: <type>`. Whether or not the overlay actually breaches depends on the validation window data; if it accidentally passes, intentionally craft a more extreme overlay (e.g., weight schema that loses money).

- [ ] **Step 17.4: Full prepare-backtest-data (long-running, plan a 15-minute window)**

```bash
python3 -m stock_analyze prepare-backtest-data \
  --start 2021-01-01 --end 2026-04-30
```

Expected: ~15 minutes. Resumable if interrupted (rerun the same command).

- [ ] **Step 17.5: Confirm dashboard panels render**

Run `competition-dashboard` after backtest data is ready. Open `reports/competition/dashboard.html` (professional view). Verify:
- "历史回测 vs 真实运行" panel renders
- Strategy evolution timeline shows "验证回测指标" column

- [ ] **Step 17.6: Mark the change as ready for archival**

Once 17.1-17.5 all pass:

```bash
# Update tasks.md task statuses to [x]
# Update README.md status from DRAFT to ACTIVE
# (Archive happens later via openspec archive command, after live operation)
```

- [ ] **Step 17.7: Final commit**

```bash
git add openspec/changes/add-historical-backtest-engine/
git commit -m "add-historical-backtest-engine: all tasks complete; ready for live use"
```

---

## Self-Review Checklist

After implementing this plan, run through this checklist:

- [ ] All 17 Tasks have at least one passing test
- [ ] `python3 -m unittest discover -s tests` passes with no failures
- [ ] `pyflakes stock_analyze/` reports no issues
- [ ] `openspec validate add-historical-backtest-engine --strict` passes
- [ ] Forward-mode simulator behavior unchanged (run a forward `run-daily` and check daily_nav.csv unchanged)
- [ ] Backtest output schema matches forward output schema (column names + dtypes)
- [ ] PointInTimeView prevents future leakage (Task 6 tests assert this)
- [ ] Gate breach correctly aborts yaml write (Task 12 test asserts this)
- [ ] Three-window discipline documented in CLAUDE.md + AGENTS.md (Task 15)
- [ ] Dashboard panel renders without errors (Task 14)

If any item fails, return to the corresponding task and fix.
