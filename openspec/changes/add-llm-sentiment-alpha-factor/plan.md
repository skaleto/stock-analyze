# LLM Sentiment Alpha Factor (MVP / Path 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single market-level sentiment factor (`<agent>_market_sentiment_1w`) where the operator manually pastes the result of a weekly Claude.ai (or ChatGPT) chat-with-web-search into a CSV; the factor is broadcast to all candidates and integrated into factor_pipeline.

**Architecture:** Operator runs a standard prompt in their LLM chat client each week (~10 minutes per agent), copies the JSON response, and runs `python3 -m stock_analyze record-sentiment ...` to persist it. `factor_pipeline.py` is extended with a "broadcast factor" concept: this factor's value is read once per as_of and added (× weight) to every candidate's composite score, skipping winsorize/z-score/industry-neutralization (since the value is constant across the cross-section). No new ingestion pipeline, no Python LLM API call, no Tushare news subscription, no historical backfill. Live-only; not integrated into backtest gate.

**Tech Stack:** Python 3.11+, pandas, existing `stock_analyze/factor_pipeline.py` + `overlay_guard.py` + `reporting.py`, pytest/unittest.

---

## Reading Guide

This plan has **11 sections** (Tasks 1-11). MVP is intentionally compact — most tasks are ≤30 minutes of work. The total plan is much smaller than `add-historical-backtest-engine` because MVP defers all complex layers (news ingestion, NER, per-stock LLM, backtest integration) to future phases documented in `design.md §11`.

Implementation references:
- Design: `openspec/changes/add-llm-sentiment-alpha-factor/design.md`
- Tasks (high-level): `openspec/changes/add-llm-sentiment-alpha-factor/tasks.md`
- Proposal: `openspec/changes/add-llm-sentiment-alpha-factor/proposal.md`

---

## Task 1: OpenSpec foundation and capability specs

**Files:**
- Create: `openspec/changes/add-llm-sentiment-alpha-factor/specs/weekly-market-sentiment-recording/spec.md`
- Create: `openspec/changes/add-llm-sentiment-alpha-factor/specs/agent-specific-broadcast-alt-factor/spec.md`

- [ ] **Step 1.1: Create specs/ subdirectory**

```bash
mkdir -p openspec/changes/add-llm-sentiment-alpha-factor/specs/weekly-market-sentiment-recording
mkdir -p openspec/changes/add-llm-sentiment-alpha-factor/specs/agent-specific-broadcast-alt-factor
```

- [ ] **Step 1.2: Write weekly-market-sentiment-recording spec**

Create `openspec/changes/add-llm-sentiment-alpha-factor/specs/weekly-market-sentiment-recording/spec.md`:

```markdown
# weekly-market-sentiment-recording

## Purpose
Allow an operator to record (once per agent per week) a market-level sentiment score
derived from a chat session with that agent's LLM client (Claude.ai for claude,
ChatGPT for codex). The recording is durable, validated, and idempotent.

## Interface
```bash
python3 -m stock_analyze record-sentiment \
  --agent <claude|codex> --week-end YYYY-MM-DD \
  --score <-1.0..1.0> --confidence <0.0..1.0> \
  --drivers <comma-separated-strings> \
  --llm-model <name> [--sources <pipe-separated-urls>] [--force]
```

Persists to `data/<agent>/alt_factors/market_sentiment.csv` (append one row).

## Invariants
- Score in [-1.0, 1.0]; confidence in [0.0, 1.0]
- week_end must be a Friday (validated; non-trading-Friday rolls back to prior trade day)
- Same (agent, week_end) cannot be written twice unless --force is passed

## Acceptance criteria
- Running CLI with valid inputs appends one row and prints success line
- Re-running same (agent, week_end) without --force exits 1 with informative error
- Out-of-range score / confidence exits 1
```

- [ ] **Step 1.3: Write agent-specific-broadcast-alt-factor spec**

Create `openspec/changes/add-llm-sentiment-alpha-factor/specs/agent-specific-broadcast-alt-factor/spec.md`:

```markdown
# agent-specific-broadcast-alt-factor

## Purpose
Extend `factor_pipeline` and `overlay_guard` so that a factor named
`<agent_id>_market_sentiment_1w` is recognized as a "broadcast factor":
its value is a single scalar (read from the agent's sentiment CSV) that is
applied uniformly to all candidates' composite scores. Cross-agent reference
is rejected by overlay_guard.

## Interface

`factor_pipeline.is_broadcast_factor(name) -> bool`
`factor_pipeline.load_broadcast_factor(agent_id, name, as_of) -> float`
`factor_pipeline.compute_composite_score(...)` — applies broadcast factors after
classic per-stock factors; broadcasts skip winsorize / z-score / industry-neutral.
`overlay_guard.validate_factor_name(name, agent_id)` — raises
`OverlayCrossAgentFactor` if name's agent prefix doesn't match.

## Invariants
- Broadcast factor value is uniformly applied (no cross-sectional differentiation)
- Cross-agent reference always raises (e.g., claude.yaml referencing codex_* fails)
- Missing sentiment data (no row for this week) treated as factor-NaN; existing
  factor_pipeline missing-factor reweighting logic handles it

## Acceptance criteria
- claude.yaml using `claude_market_sentiment_1w` validates
- claude.yaml using `codex_market_sentiment_1w` raises OverlayCrossAgentFactor
- Broadcast factor's effect is identical across all candidates (verified by
  comparing per-stock score deltas vs the same overlay with the factor removed)
```

- [ ] **Step 1.4: Run openspec validate**

```bash
openspec validate add-llm-sentiment-alpha-factor --strict
```

Expected: passes.

- [ ] **Step 1.5: Commit**

```bash
git add openspec/changes/add-llm-sentiment-alpha-factor/specs/
git commit -m "specs: add 2 capability specs for add-llm-sentiment-alpha-factor"
```

---

## Task 2: alt_factors package skeleton + sentiment.py core

**Files:**
- Create: `stock_analyze/alt_factors/__init__.py`
- Create: `stock_analyze/alt_factors/sentiment.py`
- Test: `tests/test_alt_factors_sentiment.py`

- [ ] **Step 2.1: Write failing tests for sentiment.py core functions**

Create `tests/test_alt_factors_sentiment.py`:

```python
"""Tests for alt_factors.sentiment module."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze.alt_factors import sentiment


class RecordMarketSentimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_happy_path_writes_one_row(self):
        sentiment.record_market_sentiment(
            agent_id='claude', week_end=date(2026, 5, 22),
            score=0.32, confidence=0.78,
            drivers=['AI 算力链回暖', '央行 MLF 偏鸽', '地产新政预期反复'],
            sources=['https://www.cls.cn/x'],
            llm_model='claude-sonnet-4.5',
            prompt_version='v1',
            repo_root=self.repo,
        )
        csv_path = self.repo / 'data' / 'claude' / 'alt_factors' / 'market_sentiment.csv'
        self.assertTrue(csv_path.exists())
        content = csv_path.read_text()
        self.assertIn('2026-05-22', content)
        self.assertIn('0.32', content)
        # Header + 1 data row
        self.assertEqual(len(content.strip().split('\n')), 2)

    def test_score_out_of_range_raises(self):
        with self.assertRaises(ValueError) as ctx:
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=date(2026, 5, 22),
                score=1.5, confidence=0.5, drivers=['x'],
                sources=[], llm_model='x', prompt_version='v1',
                repo_root=self.repo,
            )
        self.assertIn('score', str(ctx.exception).lower())

    def test_confidence_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=date(2026, 5, 22),
                score=0.0, confidence=1.5, drivers=['x'],
                sources=[], llm_model='x', prompt_version='v1',
                repo_root=self.repo,
            )

    def test_duplicate_week_end_rejected_without_force(self):
        for _ in range(1):
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=date(2026, 5, 22),
                score=0.32, confidence=0.78, drivers=['x'],
                sources=[], llm_model='claude-sonnet-4.5',
                prompt_version='v1', repo_root=self.repo,
            )
        with self.assertRaises(sentiment.DuplicateSentimentEntry):
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=date(2026, 5, 22),
                score=0.4, confidence=0.7, drivers=['x'],
                sources=[], llm_model='claude-sonnet-4.5',
                prompt_version='v1', repo_root=self.repo,
            )

    def test_force_overwrites_existing(self):
        for s in (0.32, 0.40):
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=date(2026, 5, 22),
                score=s, confidence=0.78, drivers=['x'],
                sources=[], llm_model='claude-sonnet-4.5',
                prompt_version='v1', repo_root=self.repo,
                force=True,
            )
        rows = sentiment.load_sentiment_history('claude', repo_root=self.repo)
        # After force, only one row for that week
        matching = [r for r in rows if r.week_end == date(2026, 5, 22)]
        self.assertEqual(len(matching), 1)
        self.assertAlmostEqual(matching[0].score, 0.40)


class LoadLatestMarketSentimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_if_no_data(self):
        v = sentiment.load_latest_market_sentiment('claude', date(2026, 5, 22),
                                                     repo_root=self.repo)
        self.assertIsNone(v)

    def test_returns_most_recent_week_le_as_of(self):
        for week, score in [(date(2026, 5, 8), 0.1),
                             (date(2026, 5, 15), 0.2),
                             (date(2026, 5, 22), 0.3)]:
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=week,
                score=score, confidence=0.7, drivers=['x'],
                sources=[], llm_model='x', prompt_version='v1',
                repo_root=self.repo,
            )
        # Looking up as_of = 2026-05-20 should pick the 2026-05-15 row
        v = sentiment.load_latest_market_sentiment('claude', date(2026, 5, 20),
                                                     repo_root=self.repo)
        self.assertAlmostEqual(v, 0.2)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_alt_factors_sentiment -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 2.3: Create alt_factors package**

Create `stock_analyze/alt_factors/__init__.py`:

```python
"""Alt-factor support: agent-specific factors derived outside of the classic
quantitative pipeline (e.g. LLM-derived market sentiment).

See openspec/changes/add-llm-sentiment-alpha-factor/design.md for the MVP design.
"""
```

Create `stock_analyze/alt_factors/sentiment.py`:

```python
"""Market sentiment alt-factor: record + load.

Operator collects sentiment via LLM chat (Claude.ai / ChatGPT) each week and
records it via `record_market_sentiment` (called by the `record-sentiment` CLI).
"""
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional


class DuplicateSentimentEntry(Exception):
    pass


@dataclass
class SentimentRow:
    week_end: date
    score: float
    confidence: float
    drivers: List[str]
    sources: List[str]
    llm_model: str
    prompt_version: str
    recorded_at: str


CSV_HEADER = (
    'week_end_date,sentiment_score,confidence,key_drivers,sources,'
    'llm_model,prompt_version,recorded_at'
)


def _csv_path(agent_id: str, repo_root: Path) -> Path:
    return repo_root / 'data' / agent_id / 'alt_factors' / 'market_sentiment.csv'


def _parse_row(row: dict) -> SentimentRow:
    return SentimentRow(
        week_end=date.fromisoformat(row['week_end_date']),
        score=float(row['sentiment_score']),
        confidence=float(row['confidence']),
        drivers=row['key_drivers'].split(',') if row['key_drivers'] else [],
        sources=row['sources'].split('|') if row['sources'] else [],
        llm_model=row['llm_model'],
        prompt_version=row['prompt_version'],
        recorded_at=row['recorded_at'],
    )


def record_market_sentiment(
    agent_id: str, week_end: date, score: float, confidence: float,
    drivers: List[str], sources: List[str], llm_model: str,
    prompt_version: str, repo_root: Path, force: bool = False,
) -> None:
    """Append (or replace) a sentiment row for (agent_id, week_end)."""
    # Validate
    if not -1.0 <= score <= 1.0:
        raise ValueError(f'score must be in [-1, 1], got {score}')
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f'confidence must be in [0, 1], got {confidence}')
    if not drivers or len(drivers) > 5:
        raise ValueError(f'drivers must have 1..5 entries, got {len(drivers)}')
    if week_end.weekday() != 4:  # Friday = 4
        # MVP soft validation: warn (TODO: trade calendar lookup for non-trading-Friday rollback)
        pass

    path = _csv_path(agent_id, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing rows
    existing: List[SentimentRow] = []
    if path.exists():
        with path.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing.append(_parse_row(row))

    matching = [r for r in existing if r.week_end == week_end]
    if matching and not force:
        raise DuplicateSentimentEntry(
            f'{agent_id} already has sentiment for week_end={week_end.isoformat()}; '
            f'use --force to overwrite'
        )
    if matching and force:
        existing = [r for r in existing if r.week_end != week_end]

    from datetime import datetime
    new_row = SentimentRow(
        week_end=week_end, score=score, confidence=confidence,
        drivers=drivers, sources=sources, llm_model=llm_model,
        prompt_version=prompt_version,
        recorded_at=datetime.now().isoformat(timespec='seconds'),
    )
    existing.append(new_row)
    existing.sort(key=lambda r: r.week_end)

    # Write all rows back
    with path.open('w', newline='') as f:
        f.write(CSV_HEADER + '\n')
        for r in existing:
            line = ','.join([
                r.week_end.isoformat(),
                f'{r.score:.4f}',
                f'{r.confidence:.4f}',
                ','.join(r.drivers).replace(',', ';') if any(',' in d for d in r.drivers)
                    else ','.join(r.drivers),
                '|'.join(r.sources),
                r.llm_model,
                r.prompt_version,
                r.recorded_at,
            ])
            f.write(line + '\n')


def load_sentiment_history(agent_id: str, repo_root: Path,
                            last_n: Optional[int] = None) -> List[SentimentRow]:
    path = _csv_path(agent_id, repo_root)
    if not path.exists():
        return []
    rows = []
    with path.open() as f:
        for row in csv.DictReader(f):
            rows.append(_parse_row(row))
    rows.sort(key=lambda r: r.week_end)
    if last_n is not None:
        rows = rows[-last_n:]
    return rows


def load_latest_market_sentiment(agent_id: str, as_of: date,
                                   repo_root: Path) -> Optional[float]:
    """Return sentiment score from most recent week_end <= as_of, or None."""
    rows = load_sentiment_history(agent_id, repo_root)
    eligible = [r for r in rows if r.week_end <= as_of]
    if not eligible:
        return None
    return eligible[-1].score


def remove_sentiment(agent_id: str, week_end: date, repo_root: Path) -> None:
    """Remove a sentiment row by week_end. Errors if not found."""
    path = _csv_path(agent_id, repo_root)
    if not path.exists():
        raise FileNotFoundError(f'{path} does not exist')
    rows = load_sentiment_history(agent_id, repo_root)
    new_rows = [r for r in rows if r.week_end != week_end]
    if len(new_rows) == len(rows):
        raise ValueError(f'No row found for week_end={week_end.isoformat()}')
    with path.open('w') as f:
        f.write(CSV_HEADER + '\n')
        for r in new_rows:
            line = ','.join([
                r.week_end.isoformat(), f'{r.score:.4f}',
                f'{r.confidence:.4f}', ','.join(r.drivers),
                '|'.join(r.sources), r.llm_model,
                r.prompt_version, r.recorded_at,
            ])
            f.write(line + '\n')
```

- [ ] **Step 2.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_alt_factors_sentiment -v
```

Expected: all PASS.

- [ ] **Step 2.5: Commit**

```bash
git add stock_analyze/alt_factors/__init__.py stock_analyze/alt_factors/sentiment.py tests/test_alt_factors_sentiment.py
git commit -m "alt_factors: sentiment module (record/load/remove with validation)"
```

---

## Task 3: record-sentiment CLI subcommand

**Files:**
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli_record_sentiment.py`

- [ ] **Step 3.1: Write failing test**

Create `tests/test_cli_record_sentiment.py`:

```python
"""Tests for record-sentiment CLI subcommand."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import cli


class RecordSentimentCLITests(unittest.TestCase):
    def test_happy_path_invokes_recording(self):
        with patch('stock_analyze.alt_factors.sentiment.record_market_sentiment') as mocked:
            cli.main([
                'record-sentiment',
                '--agent', 'claude',
                '--week-end', '2026-05-22',
                '--score', '0.32',
                '--confidence', '0.78',
                '--drivers', 'AI 算力链回暖,央行 MLF 偏鸽,地产新政预期反复',
                '--llm-model', 'claude-sonnet-4.5',
                '--sources', 'https://www.cls.cn/x|https://finance.sina.com.cn/y',
            ])
            mocked.assert_called_once()
            kw = mocked.call_args.kwargs
            self.assertEqual(kw['agent_id'], 'claude')
            self.assertAlmostEqual(kw['score'], 0.32)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_cli_record_sentiment -v
```

- [ ] **Step 3.3: Add subcommand to cli.py**

In `stock_analyze/cli.py`, add to subparsers:

```python
    # record-sentiment
    p_rec = subparsers.add_parser(
        'record-sentiment',
        help='Record one week of market sentiment from LLM chat client.',
    )
    p_rec.add_argument('--agent', required=True, choices=['claude', 'codex'])
    p_rec.add_argument('--week-end', type=_parse_date, required=True,
                        help='Friday of the analysed week (YYYY-MM-DD).')
    p_rec.add_argument('--score', type=float, required=True, help='-1.0..1.0')
    p_rec.add_argument('--confidence', type=float, required=True, help='0.0..1.0')
    p_rec.add_argument('--drivers', required=True,
                        help='Comma-separated key drivers (1..5).')
    p_rec.add_argument('--sources', default='',
                        help='Pipe-separated URLs (optional).')
    p_rec.add_argument('--llm-model', required=True)
    p_rec.add_argument('--prompt-version', default='v1')
    p_rec.add_argument('--force', action='store_true')
    p_rec.set_defaults(func=_cmd_record_sentiment)
```

Add dispatcher:

```python
def _cmd_record_sentiment(args):
    from stock_analyze.alt_factors import sentiment
    from stock_analyze.alt_factors.sentiment import DuplicateSentimentEntry
    drivers = [d.strip() for d in args.drivers.split(',') if d.strip()]
    sources = [s.strip() for s in args.sources.split('|') if s.strip()] if args.sources else []
    try:
        sentiment.record_market_sentiment(
            agent_id=args.agent, week_end=args.week_end,
            score=args.score, confidence=args.confidence,
            drivers=drivers, sources=sources,
            llm_model=args.llm_model, prompt_version=args.prompt_version,
            repo_root=Path('.'), force=args.force,
        )
        rows = sentiment.load_sentiment_history(args.agent, Path('.'))
        print(f'✓ recorded {args.agent} {args.week_end}; csv now has {len(rows)} weeks')
    except DuplicateSentimentEntry as e:
        print(f'✗ {e}')
        raise SystemExit(1)
    except ValueError as e:
        print(f'✗ validation: {e}')
        raise SystemExit(1)
```

- [ ] **Step 3.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_cli_record_sentiment -v
```

- [ ] **Step 3.5: Commit**

```bash
git add stock_analyze/cli.py tests/test_cli_record_sentiment.py
git commit -m "cli: add record-sentiment subcommand"
```

---

## Task 4: sentiment-log CLI subcommand

**Files:**
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli_sentiment_log.py`

- [ ] **Step 4.1: Write failing test**

Create `tests/test_cli_sentiment_log.py`:

```python
"""Tests for sentiment-log CLI subcommand."""
import unittest
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_analyze import cli
from stock_analyze.alt_factors import sentiment


class SentimentLogCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for week_end, score in [(date(2026, 5, 8), 0.1),
                                  (date(2026, 5, 15), 0.2),
                                  (date(2026, 5, 22), 0.3)]:
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=week_end,
                score=score, confidence=0.7, drivers=['x'],
                sources=[], llm_model='x', prompt_version='v1',
                repo_root=self.repo,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_shows_all_weeks(self):
        with patch('sys.stdout', new=StringIO()) as captured, \
             patch('stock_analyze.cli.Path', return_value=self.repo):
            cli.main(['sentiment-log', '--agent', 'claude'])
        out = captured.getvalue()
        self.assertIn('2026-05-08', out)
        self.assertIn('2026-05-15', out)
        self.assertIn('2026-05-22', out)

    def test_last_n_filter(self):
        with patch('sys.stdout', new=StringIO()) as captured, \
             patch('stock_analyze.cli.Path', return_value=self.repo):
            cli.main(['sentiment-log', '--agent', 'claude', '--last', '2'])
        out = captured.getvalue()
        self.assertNotIn('2026-05-08', out)
        self.assertIn('2026-05-15', out)


if __name__ == '__main__':
    unittest.main()
```

Note: the `with patch('stock_analyze.cli.Path', ...)` is brittle; a cleaner approach is to make the CLI accept `--repo-root` for testability. Update the CLI accordingly.

- [ ] **Step 4.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_cli_sentiment_log -v
```

- [ ] **Step 4.3: Add subcommand**

In `stock_analyze/cli.py`:

```python
    # sentiment-log
    p_log = subparsers.add_parser(
        'sentiment-log',
        help='Show / manipulate recorded sentiment history.',
    )
    p_log.add_argument('--agent', required=True, choices=['claude', 'codex'])
    p_log.add_argument('--last', type=int, default=None, help='Show last N rows.')
    p_log.add_argument('--remove', action='store_true',
                        help='Remove a row (requires --week-end).')
    p_log.add_argument('--week-end', type=_parse_date, default=None,
                        help='Required with --remove.')
    p_log.add_argument('--repo-root', type=Path, default=Path('.'))
    p_log.set_defaults(func=_cmd_sentiment_log)
```

Dispatcher:

```python
def _cmd_sentiment_log(args):
    from stock_analyze.alt_factors import sentiment
    if args.remove:
        if args.week_end is None:
            print('✗ --remove requires --week-end')
            raise SystemExit(1)
        sentiment.remove_sentiment(args.agent, args.week_end, args.repo_root)
        print(f'✓ removed {args.agent} {args.week_end}')
        return
    rows = sentiment.load_sentiment_history(args.agent, args.repo_root,
                                              last_n=args.last)
    for r in rows:
        print(f'{r.week_end.isoformat()}  score={r.score:+.2f}  '
              f'conf={r.confidence:.2f}  drivers="{",".join(r.drivers)}"')
```

- [ ] **Step 4.4: Adjust test to use --repo-root, run, expect PASS**

Update test to pass `--repo-root` explicitly instead of mocking `Path`:

```python
        cli.main(['sentiment-log', '--agent', 'claude', '--repo-root', str(self.repo)])
```

```bash
python3 -m unittest tests.test_cli_sentiment_log -v
```

- [ ] **Step 4.5: Commit**

```bash
git add stock_analyze/cli.py tests/test_cli_sentiment_log.py
git commit -m "cli: add sentiment-log subcommand (list/last-N/remove)"
```

---

## Task 5: factor_pipeline — broadcast factor concept

**Files:**
- Modify: `stock_analyze/factor_pipeline.py`
- Test: `tests/test_factor_pipeline_broadcast.py`

- [ ] **Step 5.1: Write failing test**

Create `tests/test_factor_pipeline_broadcast.py`:

```python
"""Tests for factor_pipeline broadcast factor handling."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import factor_pipeline
from stock_analyze.alt_factors import sentiment


class BroadcastFactorTests(unittest.TestCase):
    def test_is_broadcast_factor_recognizes_market_sentiment(self):
        self.assertTrue(factor_pipeline.is_broadcast_factor('claude_market_sentiment_1w'))
        self.assertTrue(factor_pipeline.is_broadcast_factor('codex_market_sentiment_1w'))
        self.assertFalse(factor_pipeline.is_broadcast_factor('pe'))
        self.assertFalse(factor_pipeline.is_broadcast_factor('roe'))

    def test_load_broadcast_factor_returns_sentiment_score(self):
        tmp = TemporaryDirectory()
        repo = Path(tmp.name)
        sentiment.record_market_sentiment(
            agent_id='claude', week_end=date(2026, 5, 22),
            score=0.32, confidence=0.78, drivers=['x'],
            sources=[], llm_model='x', prompt_version='v1',
            repo_root=repo,
        )
        v = factor_pipeline.load_broadcast_factor(
            'claude', 'claude_market_sentiment_1w', date(2026, 5, 25),
            repo_root=repo,
        )
        self.assertAlmostEqual(v, 0.32)
        tmp.cleanup()

    def test_broadcast_factor_uniformly_applied_to_all_candidates(self):
        """When overlay uses broadcast factor, all candidates get +sentiment*weight."""
        tmp = TemporaryDirectory()
        repo = Path(tmp.name)
        sentiment.record_market_sentiment(
            agent_id='claude', week_end=date(2026, 5, 22),
            score=0.5, confidence=0.78, drivers=['x'],
            sources=[], llm_model='x', prompt_version='v1',
            repo_root=repo,
        )

        # Construct synthetic candidates with classic-factor diversity
        candidates = factor_pipeline.create_test_candidates([
            ('000001.SZ', {'pe': 5.0, 'roe': 0.08}),
            ('000002.SZ', {'pe': 10.0, 'roe': 0.12}),
            ('000003.SZ', {'pe': 7.5, 'roe': 0.10}),
        ])
        overlay_no_sentiment = {
            'factors': {'pe': {'weight': 0.5, 'direction': 'low'},
                         'roe': {'weight': 0.5, 'direction': 'high'}},
            'factor_processing': {'winsorize_lower': 0.01, 'winsorize_upper': 0.99},
        }
        overlay_with_sentiment = {
            'factors': {'pe': {'weight': 0.45, 'direction': 'low'},
                         'roe': {'weight': 0.45, 'direction': 'high'},
                         'claude_market_sentiment_1w': {'weight': 0.10,
                                                         'direction': 'high'}},
            'factor_processing': {'winsorize_lower': 0.01, 'winsorize_upper': 0.99},
        }

        scores_a = factor_pipeline.compute_composite_score(
            candidates, overlay_no_sentiment, as_of=date(2026, 5, 25),
            agent_id='claude', repo_root=repo,
        )
        scores_b = factor_pipeline.compute_composite_score(
            candidates, overlay_with_sentiment, as_of=date(2026, 5, 25),
            agent_id='claude', repo_root=repo,
        )

        # When sentiment is added, every candidate's score changes by the same amount
        # (0.10 weight × 0.5 score = 0.05 added uniformly, modulo rescaling from
        # removed pe/roe weights). Check that the DIFFERENCE between two candidates
        # remains the same in both scoring schemes.
        diff_a = scores_a['000002.SZ'] - scores_a['000001.SZ']
        diff_b = scores_b['000002.SZ'] - scores_b['000001.SZ']
        self.assertAlmostEqual(diff_a, diff_b, places=4)

        tmp.cleanup()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 5.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_factor_pipeline_broadcast -v
```

- [ ] **Step 5.3: Implement broadcast factor support**

Open `stock_analyze/factor_pipeline.py`. Add at the top of the file:

```python
import re

BROADCAST_FACTOR_PATTERN = re.compile(r'^(claude|codex)_market_sentiment_1w$')


def is_broadcast_factor(name: str) -> bool:
    """Return True if this factor name refers to a broadcast (market-level) factor."""
    return bool(BROADCAST_FACTOR_PATTERN.match(name))


def load_broadcast_factor(agent_id: str, factor_name: str,
                            as_of, repo_root) -> float | None:
    """Read the broadcast factor's value for the agent at as_of, or None if missing."""
    from datetime import date
    from pathlib import Path
    from stock_analyze.alt_factors import sentiment as alt_sent
    if factor_name == f'{agent_id}_market_sentiment_1w':
        return alt_sent.load_latest_market_sentiment(agent_id, as_of, Path(repo_root))
    return None
```

Locate the existing `compute_composite_score` function. Add broadcast-factor handling:

```python
def compute_composite_score(candidates, overlay, *,
                              as_of=None, agent_id=None, repo_root='.'):
    """Compute composite score per candidate.

    Classic factors go through the existing winsorize → z-score → industry
    neutralization → weighted-sum pipeline. Broadcast factors (e.g.
    `<agent>_market_sentiment_1w`) skip preprocessing — they're uniformly
    added to every candidate's score (× weight × direction sign).
    """
    factors_config = overlay.get('factors', {})

    # Partition factors into classic vs broadcast
    classic_factors = {k: v for k, v in factors_config.items()
                        if not is_broadcast_factor(k)}
    broadcast_factors = {k: v for k, v in factors_config.items()
                          if is_broadcast_factor(k)}

    # Compute classic-factor scores (existing logic)
    scores = _compute_classic_composite_score(
        candidates, {'factors': classic_factors, **{k: v for k, v in overlay.items() if k != 'factors'}}
    )

    # Add broadcast factors
    for factor_name, conf in broadcast_factors.items():
        if agent_id is None or as_of is None:
            continue
        value = load_broadcast_factor(agent_id, factor_name, as_of, repo_root)
        if value is None:
            continue  # NaN → factor missing → ignore (existing missing-factor logic
                       # already handles re-normalization of remaining weights)
        sign = +1 if conf.get('direction', 'high') == 'high' else -1
        weight = conf.get('weight', 0.0)
        scores += sign * weight * value  # broadcast to all candidates

    return scores
```

Refactor the existing classic composite score logic into `_compute_classic_composite_score` (just rename — no behavior change).

Also add `create_test_candidates` helper for testability:

```python
def create_test_candidates(triples):
    """Build a list of test Candidate objects from (ts_code, {factor: value}) tuples."""
    import pandas as pd
    # Returns a pd.DataFrame indexed by ts_code with factor columns
    data = {}
    for ts_code, factors in triples:
        data[ts_code] = factors
    return pd.DataFrame.from_dict(data, orient='index')
```

(If your codebase already has a Candidate dataclass with industry / sector / amount fields, instead make `create_test_candidates` build those objects with minimum required fields.)

- [ ] **Step 5.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_factor_pipeline_broadcast -v
python3 -m unittest discover -s tests -p 'test_factor_pipeline*' -v
```

Expected: all pass (including existing factor_pipeline tests, since broadcast logic only fires when broadcast-named factors are in overlay).

- [ ] **Step 5.5: Commit**

```bash
git add stock_analyze/factor_pipeline.py tests/test_factor_pipeline_broadcast.py
git commit -m "factor_pipeline: broadcast factor concept (uniform application across candidates)"
```

---

## Task 6: overlay_guard — agent_*-prefix factor whitelist

**Files:**
- Modify: `stock_analyze/overlay_guard.py`
- Test: `tests/test_overlay_guard_alt_factors.py`

- [ ] **Step 6.1: Write failing test**

Create `tests/test_overlay_guard_alt_factors.py`:

```python
"""Tests for overlay_guard's alt-factor extensions."""
import unittest

from stock_analyze import overlay_guard


class AltFactorValidationTests(unittest.TestCase):
    def test_classic_factor_validates(self):
        overlay_guard.validate_factor_name('pe', agent_id='claude')
        overlay_guard.validate_factor_name('roe', agent_id='claude')

    def test_own_alt_factor_validates(self):
        overlay_guard.validate_factor_name('claude_market_sentiment_1w', agent_id='claude')
        overlay_guard.validate_factor_name('codex_market_sentiment_1w', agent_id='codex')

    def test_other_agent_alt_factor_rejected(self):
        with self.assertRaises(overlay_guard.OverlayCrossAgentFactor):
            overlay_guard.validate_factor_name('codex_market_sentiment_1w', agent_id='claude')
        with self.assertRaises(overlay_guard.OverlayCrossAgentFactor):
            overlay_guard.validate_factor_name('claude_market_sentiment_1w', agent_id='codex')

    def test_unknown_factor_rejected(self):
        with self.assertRaises(overlay_guard.OverlayUnknownFactor):
            overlay_guard.validate_factor_name('made_up_factor', agent_id='claude')

    def test_full_overlay_validate_with_alt_factor(self):
        overlay = {
            'agent_id': 'claude', 'strategy_id': 'test', 'name': 'test',
            'factors': {
                'pe': {'weight': 0.5, 'direction': 'low'},
                'claude_market_sentiment_1w': {'weight': 0.5, 'direction': 'high'},
            },
            'factor_processing': {'winsorize_lower': 0.01, 'winsorize_upper': 0.99},
            'portfolio_controls': {'max_industry_weight': 0.3, 'hold_buffer_pct': 0.5,
                                     'max_holding_days': 365,
                                     'industry_unclassified_label': '未分类'},
            'filters': {'exclude_st': True, 'max_fetch_candidates': 250},
        }
        # Should not raise
        overlay_guard.validate(agent_id='claude', overlay=overlay, repo_root=None)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 6.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_overlay_guard_alt_factors -v
```

- [ ] **Step 6.3: Extend overlay_guard.py**

Open `stock_analyze/overlay_guard.py`. Locate the existing `AVAILABLE_FACTORS` set. Refactor:

```python
CLASSIC_FACTORS = {
    'pe', 'pb', 'roe', 'gross_margin', 'debt_ratio',
    'net_profit_growth', 'momentum_20', 'momentum_60',
    'low_volatility_60', 'dividend_yield',
}

# Keep backward compat: code that imports AVAILABLE_FACTORS still works
AVAILABLE_FACTORS = CLASSIC_FACTORS  # noqa: alias

AGENT_ALT_FACTOR_PATTERN = re.compile(r'^(claude|codex)_market_sentiment_1w$')


class OverlayCrossAgentFactor(Exception):
    pass


# OverlayUnknownFactor already exists; if not, add:
class OverlayUnknownFactor(Exception):
    pass


def validate_factor_name(name: str, agent_id: str) -> None:
    """Raise OverlayUnknownFactor or OverlayCrossAgentFactor if invalid."""
    if name in CLASSIC_FACTORS:
        return
    m = AGENT_ALT_FACTOR_PATTERN.match(name)
    if not m:
        raise OverlayUnknownFactor(f'Unknown factor: {name!r}')
    if m.group(1) != agent_id:
        raise OverlayCrossAgentFactor(
            f'Agent {agent_id!r} cannot reference cross-agent factor {name!r}'
        )
```

Locate the existing `validate(...)` function and find where each factor name is checked. Replace whatever pattern is there with:

```python
    for factor_name in overlay.get('factors', {}).keys():
        validate_factor_name(factor_name, agent_id=agent_id)
```

- [ ] **Step 6.4: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_overlay_guard_alt_factors -v
python3 -m unittest discover -s tests -p 'test_overlay_guard*' -v
```

Expected: all pass. Existing tests on classic-only overlays still pass (they don't introduce alt-factors).

- [ ] **Step 6.5: Commit**

```bash
git add stock_analyze/overlay_guard.py tests/test_overlay_guard_alt_factors.py
git commit -m "overlay_guard: support <agent>_*-prefix alt-factors with cross-agent isolation"
```

---

## Task 7: Prompt template

**Files:**
- Create: `stock_analyze/alt_factors/prompts/__init__.py`
- Create: `stock_analyze/alt_factors/prompts/market_sentiment_v1.md`

- [ ] **Step 7.1: Create prompts package**

```bash
mkdir -p stock_analyze/alt_factors/prompts
touch stock_analyze/alt_factors/prompts/__init__.py
```

- [ ] **Step 7.2: Write the prompt template**

Create `stock_analyze/alt_factors/prompts/market_sentiment_v1.md` with the content from `design.md §3.1` (the standard prompt template). Save as version v1.

The full content (paste verbatim):

```markdown
你是 {agent_id} 的市场情感分析师。

任务：判断 A 股市场在 **{week_start_date} ~ {week_end_date}** 这 7 天的整体情感倾向。

要求：
1. 使用你自带的 web search 工具，搜索本周中国 A 股市场的重要新闻。优先来源：
   - 财联社、新浪财经、同花顺、东方财富
   - 央视新闻、新华社、证券时报
   - 不优先：自媒体、营销号
2. 关注以下维度：
   - 政策面：货币政策、产业政策、监管新规
   - 资金面：北上资金流向、新发基金、IPO 节奏
   - 板块面：本周热点 / 资金流出板块
   - 风险事件：商品价格异动、企业暴雷、地缘政治
   - 海外：美股、美联储、汇率
3. 综合判断本周市场情感，输出严格 JSON 如下（不要任何解释文字）：

{
  "sentiment_score": <-1.0 到 1.0 的小数；-1 = 极度负面，0 = 中性，1 = 极度正面>,
  "confidence": <0.0 到 1.0；信息充分一致 → 0.8+，信息分歧大 → 0.5 以下>,
  "key_drivers": [<3 个最重要驱动事件，每个 ≤ 15 字>],
  "search_sources_used": [<本次主要参考的 5 个新闻链接 URL>]
}

参考样例：
{
  "sentiment_score": 0.32,
  "confidence": 0.78,
  "key_drivers": ["AI 算力链情绪回暖", "央行 MLF 续作偏鸽", "地产新政预期反复"],
  "search_sources_used": ["https://www.cls.cn/...", "..."]
}
```

- [ ] **Step 7.3: Commit**

```bash
git add stock_analyze/alt_factors/prompts/
git commit -m "alt_factors: market_sentiment v1 prompt template"
```

---

## Task 8: Dashboard — market sentiment timeline + cross-LLM comparison panels

**Files:**
- Modify: `stock_analyze/reporting.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Test: `tests/test_dashboard_sentiment_panel.py`

- [ ] **Step 8.1: Write failing test**

Create `tests/test_dashboard_sentiment_panel.py`:

```python
"""Tests for dashboard sentiment panels."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from stock_analyze import reporting
from stock_analyze import dashboard_aggregator
from stock_analyze.alt_factors import sentiment


class SentimentPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        # Populate 6 weeks of claude + codex sentiment
        for week in [date(2026, 4, 10), date(2026, 4, 17), date(2026, 4, 24),
                      date(2026, 5, 1), date(2026, 5, 8), date(2026, 5, 15)]:
            sentiment.record_market_sentiment(
                agent_id='claude', week_end=week, score=0.1 + (week.day % 4) * 0.05,
                confidence=0.7, drivers=['x'], sources=[],
                llm_model='claude-sonnet-4.5', prompt_version='v1',
                repo_root=self.repo,
            )
            sentiment.record_market_sentiment(
                agent_id='codex', week_end=week, score=0.05 + (week.day % 4) * 0.04,
                confidence=0.6, drivers=['y'], sources=[],
                llm_model='gpt-4o', prompt_version='v1',
                repo_root=self.repo,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_agent_panel_renders(self):
        html = reporting.render_market_sentiment_panel('claude', repo_root=self.repo)
        self.assertIn('市场情感', html)
        # Should mention the most recent week
        self.assertIn('2026-05-15', html)

    def test_comparison_panel_shows_both_agents(self):
        html = dashboard_aggregator.render_sentiment_comparison_panel(repo_root=self.repo)
        self.assertIn('claude', html.lower())
        self.assertIn('codex', html.lower())

    def test_stale_data_warning_when_no_recent_recording(self):
        """If most recent week is >2 weeks before today, panel should show warning."""
        # The last week we recorded is 2026-05-15; if 'today' is 2026-06-10 (>3 wk gap), warn
        from unittest.mock import patch
        from datetime import date as Date
        with patch('stock_analyze.reporting._today', return_value=Date(2026, 6, 10)):
            html = reporting.render_market_sentiment_panel('claude', repo_root=self.repo)
            self.assertIn('未更新', html)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 8.2: Run, expect FAIL**

```bash
python3 -m unittest tests.test_dashboard_sentiment_panel -v
```

- [ ] **Step 8.3: Implement render_market_sentiment_panel**

In `stock_analyze/reporting.py`, add:

```python
from datetime import date as _date_cls


def _today() -> _date_cls:
    """Helper for monkey-patching in tests."""
    return _date_cls.today()


def render_market_sentiment_panel(agent_id: str, repo_root) -> str:
    """Render the market-sentiment time-series panel for one agent (professional view only)."""
    from pathlib import Path
    from stock_analyze.alt_factors import sentiment

    rows = sentiment.load_sentiment_history(agent_id, Path(repo_root), last_n=26)
    if not rows:
        return f'<div class="panel"><h3>{agent_id} 市场情感</h3>尚无记录。请跑 record-sentiment。</div>'

    latest = rows[-1]
    last_4 = rows[-4:] if len(rows) >= 4 else rows
    last_8 = rows[-8:] if len(rows) >= 8 else rows
    avg4 = sum(r.score for r in last_4) / len(last_4)
    avg8 = sum(r.score for r in last_8) / len(last_8)

    today = _today()
    days_since = (today - latest.week_end).days
    stale_warning = ''
    if days_since > 14:
        stale_warning = (
            f'<p class="warn">⚠️ {agent_id} 已 {days_since // 7} 周未更新市场情感'
            f'（最近 {latest.week_end.isoformat()}）</p>'
        )

    # Time-series points for inline SVG (simplified — real impl uses better SVG)
    points = ' '.join(f'{i*20},{50 - r.score*40:.1f}' for i, r in enumerate(rows))

    return f'''
<div class="panel">
  <h3>{agent_id} 市场情感（过去 {len(rows)} 周）</h3>
  {stale_warning}
  <svg viewBox="0 0 {len(rows)*20} 100" width="100%" height="100">
    <polyline points="{points}" fill="none" stroke="steelblue" stroke-width="2"/>
  </svg>
  <ul>
    <li>最新 ({latest.week_end.isoformat()}): {latest.score:+.2f} (信心 {latest.confidence:.2f})</li>
    <li>4 周均值: {avg4:+.2f}</li>
    <li>8 周均值: {avg8:+.2f}</li>
  </ul>
  <details>
    <summary>本周关键驱动</summary>
    <ul>{''.join(f'<li>{d}</li>' for d in latest.drivers)}</ul>
  </details>
</div>
'''.strip()
```

- [ ] **Step 8.4: Implement render_sentiment_comparison_panel**

In `stock_analyze/dashboard_aggregator.py`, add:

```python
def render_sentiment_comparison_panel(repo_root) -> str:
    """Compare claude vs codex sentiment time series."""
    from pathlib import Path
    from stock_analyze.alt_factors import sentiment

    claude_rows = sentiment.load_sentiment_history('claude', Path(repo_root), last_n=26)
    codex_rows = sentiment.load_sentiment_history('codex', Path(repo_root), last_n=26)

    if not claude_rows or not codex_rows:
        return '<div class="panel">尚无足够数据做对比。</div>'

    latest_claude = claude_rows[-1]
    latest_codex = codex_rows[-1]

    # Diff stats
    diff = latest_claude.score - latest_codex.score

    return f'''
<div class="panel">
  <h3>claude vs codex 市场情感（过去 26 周）</h3>
  <p>本周（{latest_claude.week_end.isoformat()}）：</p>
  <table>
    <tr><th>claude</th><th>codex</th><th>差值</th></tr>
    <tr><td>{latest_claude.score:+.2f}</td>
        <td>{latest_codex.score:+.2f}</td>
        <td>{diff:+.2f}</td></tr>
  </table>
</div>
'''.strip()
```

- [ ] **Step 8.5: Run tests, expect PASS**

```bash
python3 -m unittest tests.test_dashboard_sentiment_panel -v
```

- [ ] **Step 8.6: Embed panels into dashboard outputs**

In `dashboard_aggregator.py`, find where Claude / Codex / Compare tabs are assembled. Inject:
- Claude tab: `render_market_sentiment_panel('claude', ...)`
- Codex tab: `render_market_sentiment_panel('codex', ...)`
- Compare tab: `render_sentiment_comparison_panel(...)`

- [ ] **Step 8.7: Commit**

```bash
git add stock_analyze/reporting.py stock_analyze/dashboard_aggregator.py tests/test_dashboard_sentiment_panel.py
git commit -m "dashboard: market sentiment timeline + cross-LLM comparison panels"
```

---

## Task 9: CLAUDE.md / AGENTS.md updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`

**Note:** Same exception as Task 15 of add-historical-backtest-engine — operator approves these specific edits.

- [ ] **Step 9.1: Update §4 with new factor info**

In `CLAUDE.md` §4 "What you control", under "Available factors", append:

```markdown
新增 **agent 特有 alt-factor**（由 OpenSpec change `add-llm-sentiment-alpha-factor` 引入）：

- `claude_market_sentiment_1w` — claude 自己的市场情感因子（broadcast，每周 1 个标量值，对所有候选股同样应用）

注意：claude 只能在自己的 overlay 里用 `claude_*` 前缀的 alt-factor；不能引用 `codex_market_sentiment_1w`（overlay_guard 会拒绝）。

⚠️ **MVP 阶段，broadcast factor 不直接影响选股相对排名**（所有股票被同样数值上下平移）。MVP 实质是建数据通路 + 形成行为习惯，等 Phase 3 升级到 per-stock 颗粒度后才真正影响选股。
```

- [ ] **Step 9.2: Update §7 forbidden actions**

Add to §7.1 "对手透明度规则" 的 "❌ 不可读" 列表：

```markdown
- ❌ `data/<other>/alt_factors/*` — 对手的 LLM 情感判断属于对手"思考过程"
```

- [ ] **Step 9.3: Update §10 escalation flow with weekly action**

Add a new sub-section under §10:

```markdown
### 10.x 每周末市场情感记录

每周末（建议周六上午，配合 weekly review 一起跑）：

1. 打开你的 LLM 客户端（claude 跑用 Claude.ai / Claude 桌面版；codex 跑用 ChatGPT / 桌面版）
2. 用 `stock_analyze/alt_factors/prompts/market_sentiment_v1.md` 这份 prompt 模板（填充 {agent_id} 和 {week_*_date}）
3. LLM 用自带 web search 拉本周 A 股新闻 + 输出严格 JSON
4. 把 JSON 字段填到 CLI：
   ```bash
   python3 -m stock_analyze record-sentiment \
     --agent claude --week-end 2026-05-22 \
     --score 0.32 --confidence 0.78 \
     --drivers "AI 算力链回暖,央行 MLF 偏鸽,地产新政预期反复" \
     --llm-model claude-sonnet-4.5 \
     --sources "https://www.cls.cn/x"
   ```
5. 验证 dashboard 上看到新一周的数据

每周 ~10 分钟。漏跑某周不致命（factor_pipeline 会按缺失因子分摊），但会在 dashboard 显示"已 N 周未更新"警示。
```

- [ ] **Step 9.4: Add §11 (or extend §17 roadmap) with evolution path**

Add a final section to CLAUDE.md (or extend system-overview §17 reference):

```markdown
## 11. 情感因子的演进路线

本 MVP 是 Phase 1。完整路线写在 `openspec/changes/add-llm-sentiment-alpha-factor/design.md §11`。简要：

- **Phase 1（当前 MVP）**：单 broadcast 因子 + 手动客户端 + live-only
- **Phase 2**：升 Tushare ¥1000/年 + 加 news_volume + 历史回填 + 回测集成
- **Phase 3**：per-stock LLM sentiment（颗粒度 Z）— 真正影响横截面排名
- **Phase 4**：事件型 / 跨市场 / 其它 alt-data

每个 Phase 独立 OpenSpec change。不在前一个 Phase 跑稳 6+ 月前启动下一个。
```

- [ ] **Step 9.5: Mirror to AGENTS.md**

Apply equivalent edits to `AGENTS.md`.

- [ ] **Step 9.6: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: add market_sentiment alt-factor instructions + evolution roadmap to operating manuals"
```

---

## Task 10: System docs

**Files:**
- Create: `docs/llm-sentiment-factor-flow.md`
- Modify: `docs/system-overview.md`

- [ ] **Step 10.1: Create docs/llm-sentiment-factor-flow.md**

Comprehensive operator guide (~200-300 lines). Sections:

1. **What this factor is** — single market-level sentiment value per week
2. **Why MVP is broadcast (not per-stock)** — and what that means for alpha generation in MVP
3. **Operator weekly workflow** — open Claude.ai / ChatGPT, run prompt, record via CLI
4. **The prompt template** — full content from `stock_analyze/alt_factors/prompts/market_sentiment_v1.md`
5. **What if LLM web search fails / refuses** — fallback paste-headlines workflow
6. **CSV schema** — fields explained
7. **factor_pipeline integration** — how broadcast factor flows into composite score
8. **Cross-agent isolation** — claude can't read codex's sentiment.csv; dashboard aggregates for operator view
9. **Dashboard panels** — what's shown, what to watch for
10. **The evolution roadmap (Phase 1-4)** — as first-class section per operator's requirement
11. **Phase 2 trigger conditions** — what evidence justifies upgrading
12. **Common gotchas** — operator forgot a week / changed LLM model / changed prompt → epoch management

- [ ] **Step 10.2: Update docs/system-overview.md**

In §4 "数据流", add a new sub-section §4e "每周末手动情感记录":

```markdown
### 4e. 每周末（操作员手动）

```
每周六上午（或任何时间，但建议在 weekly-review 一起做）

操作员开 Claude.ai → 用 prompt 模板 → 拿 JSON → 跑:
  python3 -m stock_analyze record-sentiment --agent claude ...

操作员开 ChatGPT → 同上 → 跑:
  python3 -m stock_analyze record-sentiment --agent codex ...

下一次 weekly factor_pipeline 自动读 data/<agent>/alt_factors/market_sentiment.csv
```
```

In §6 "因子流水线", add at end:

```markdown
**广播因子（broadcast factors）**：由 `add-llm-sentiment-alpha-factor` MVP 引入。
当因子名匹配 `<agent_id>_market_sentiment_1w` 时，因子值是一个标量（不是 per-stock），
跳过 winsorize / z-score / 行业中性化，直接广播到所有候选股的综合分上。
```

In §13 关键产物清单，append:

```
| data/<agent>/alt_factors/market_sentiment.csv | record-sentiment CLI | 每周 1 行情感记录 |
| stock_analyze/alt_factors/prompts/market_sentiment_v1.md | repo | 操作员每周用的 prompt 模板 |
```

In §17 路线图，加：

```markdown
[Phase 2 of add-llm-sentiment-alpha-factor]：加 Tushare news 包 + news_volume 因子 + 历史回填 + 回测集成（独立 change）
[Phase 3]：per-stock LLM sentiment（独立 change）
```

- [ ] **Step 10.3: Commit**

```bash
git add docs/llm-sentiment-factor-flow.md docs/system-overview.md
git commit -m "docs: add llm-sentiment-factor-flow.md + update system-overview"
```

---

## Task 11: End-to-end manual validation

**Files:**
- (no files; manual)

- [ ] **Step 11.1: Run the prompt in Claude.ai**

1. Open https://claude.ai (or Claude desktop app)
2. Paste content of `stock_analyze/alt_factors/prompts/market_sentiment_v1.md`, substituting:
   - `{agent_id}` → `claude`
   - `{week_start_date}` → date 7 days before today (a Saturday)
   - `{week_end_date}` → most recent Friday
3. Wait for Claude's response (will use web search → 30-60 sec)
4. Copy the JSON response

- [ ] **Step 11.2: Record via CLI**

```bash
python3 -m stock_analyze record-sentiment \
  --agent claude \
  --week-end <YYYY-MM-DD> \
  --score <copied-score> \
  --confidence <copied-confidence> \
  --drivers "<copied-key-drivers comma-separated>" \
  --llm-model claude-sonnet-4.5 \
  --sources "<copied-URLs pipe-separated>"
```

Expected: `✓ recorded claude <date>; csv now has 1 weeks`.

- [ ] **Step 11.3: Verify CSV**

```bash
cat data/claude/alt_factors/market_sentiment.csv
```

Expected: header + 1 row with the values you entered.

- [ ] **Step 11.4: Repeat for codex side**

Same workflow with ChatGPT / `--agent codex`.

- [ ] **Step 11.5: Run a weekly with sentiment factor in overlay**

Temporarily edit `configs/agents/claude.yaml` (or use a fixture overlay) to include:

```json
{
  ...,
  "factors": {
    ...,
    "claude_market_sentiment_1w": {"weight": 0.05, "direction": "high"}
  }
}
```

Then:

```bash
python3 -m stock_analyze validate-overlay --agent claude
# Expected: exit 0, no error
```

Run weekly:

```bash
python3 -m stock_analyze run-weekly --agent claude
```

Verify in `data/claude/factor_runs/<run_id>.csv` that `claude_market_sentiment_1w` appears as a column with same value across all stocks.

- [ ] **Step 11.6: Verify dashboard renders panels**

```bash
python3 -m stock_analyze competition-dashboard
```

Open `reports/competition/dashboard.html`:
- Claude tab: see "claude 市场情感" panel with 1-week series + latest value
- Codex tab: see "codex 市场情感" panel
- 对比 tab: see "claude vs codex 市场情感" panel

- [ ] **Step 11.7: Test duplicate rejection**

```bash
python3 -m stock_analyze record-sentiment --agent claude --week-end <same-date> \
  --score 0.1 --confidence 0.5 --drivers "x" --llm-model y
# Expected: exit 1, "✗ claude already has sentiment for week_end=..."

python3 -m stock_analyze record-sentiment --agent claude --week-end <same-date> \
  --score 0.1 --confidence 0.5 --drivers "x" --llm-model y --force
# Expected: exit 0, row overwritten
```

- [ ] **Step 11.8: Final commit**

```bash
# Update tasks.md task statuses to [x]
# Update README.md status from DRAFT to ACTIVE
git add openspec/changes/add-llm-sentiment-alpha-factor/
git commit -m "add-llm-sentiment-alpha-factor: MVP complete; ready for live use"
```

---

## Self-Review Checklist

After implementing this plan:

- [ ] All 11 Tasks have at least one passing test
- [ ] `python3 -m unittest discover -s tests` passes
- [ ] `pyflakes stock_analyze/` reports no issues
- [ ] `openspec validate add-llm-sentiment-alpha-factor --strict` passes
- [ ] Operator can record sentiment via CLI (Task 11.1-11.3)
- [ ] Broadcast factor uniformly applied across all candidates (Task 5 test asserts)
- [ ] Cross-agent factor reference rejected (Task 6 test asserts)
- [ ] Dashboard panels render without errors (Task 8)
- [ ] Operating manuals (CLAUDE.md / AGENTS.md) updated with weekly action (Task 9)
- [ ] System docs (llm-sentiment-factor-flow.md + system-overview.md) updated (Task 10)
- [ ] Phase 1-4 evolution roadmap visible in 3 places: design.md §11, docs/llm-sentiment-factor-flow.md, system-overview.md §17

If any item fails, return to the corresponding task and fix.
