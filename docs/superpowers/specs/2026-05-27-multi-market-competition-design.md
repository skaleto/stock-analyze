# Multi-Market Competition — Extending Claude vs Codex to HK & US

**Status**: design approved (brainstorming complete), pending implementation plan
**Date**: 2026-05-27
**Author**: operator + Claude Code (brainstorming session)
**Supersedes**: none (extends existing A-share competition)

## Purpose

Extend the existing A-share Claude-vs-Codex paper-trading competition to also cover Hong Kong (港股) and US (美股) markets as three **independent playgrounds** (no cross-market PNL aggregation, no shared portfolio). Each market runs its own competition with its own baseline, locked fields, agents (same `claude` + `codex` identities, separate per-market overlays), and monthly evolution flow.

## Goals (v1)

- HK and US markets each get the same minimal core that A-share has:
  - Daily run (market-data fetch → claude/codex simulator → aggregate dashboard)
  - Weekly Friday signal generation
  - Per-market simulator that faithfully models the market's trading mechanics
  - Per-market dashboard
  - Monthly LLM-direct strategy evolution flow (rewriting per-market overlays)
- One consolidated daily Lark DM containing all three markets' status (NAV, positions, sanity-check, pending actions)
- Faithful market mechanics including simplified short-selling (no margin engine, 100% cash collateral)

## Non-Goals (v1, deferred to v2)

- **Backtest engine + gate** for HK/US. v1 monthly evolution relies only on the schema-level `overlay_guard`.
- **Sentiment alt-factor** for HK/US (the LLM-curated weekly market sentiment that A-share has).
- **ROE / gross_margin / debt_ratio / net_profit_growth** factors for HK/US (requires parsing quarterly financials from yfinance; v1 starts with 6 factors derivable from `yfinance.info` + price history).
- Cross-market PNL leaderboard or combined competition.yaml.
- Full short-selling realism (borrow availability, Reg-T margin engine, overnight financing).
- FX conversion (each market starts in native currency).
- Options, ETFs, fractional shares (US fractional shares would be a v2/v3 add).

## §1 Total Architecture (Option A: symmetric subpackages)

A-share code moves into `stock_analyze/markets/a_share/`. HK and US join as sibling subpackages. Shared modules (factor_pipeline, overlay_guard, evolution_writer, reporting, notifier, sanity_check) stay at the top level and gain a `market` parameter for dispatch.

```
stock_analyze/
├── markets/
│   ├── __init__.py
│   ├── a_share/                        (current code, relocated)
│   │   ├── data_provider/              (Tushare; existing base.py etc.)
│   │   ├── simulator.py                (T+1/±10%/lot=100/Tier1+2 sizing)
│   │   ├── strategy.py
│   │   ├── universe.py                 (HS300 + ZZ500)
│   │   └── mechanics.py                (A-share constants)
│   ├── hk/                             (NEW)
│   │   ├── data_provider/              (yfinance HK wrapper)
│   │   ├── simulator.py                (T+2 / no limit / var lot / simplified shorting)
│   │   ├── strategy.py
│   │   ├── universe.py                 (HSI + HSCEI)
│   │   └── mechanics.py                (HK constants)
│   └── us/                             (NEW)
│       ├── data_provider/              (yfinance US wrapper)
│       ├── simulator.py                (T+1 / no limit / lot=1 / simplified shorting)
│       ├── strategy.py
│       ├── universe.py                 (S&P 500 + NASDAQ-100)
│       └── mechanics.py                (US constants)
│
├── factor_pipeline.py                  (SHARED: winsorize / zscore / neutralize)
├── overlay_guard.py                    (SHARED: + AVAILABLE_FACTORS_BY_MARKET)
├── evolution_writer.py                 (SHARED: + market param)
├── notifier.py                         (SHARED: daily summary covers 3 markets)
├── sanity_check.py                     (SHARED: per-market checks)
├── reporting/                          (SHARED: per-market dashboard routing)
├── competition.py                      (SHARED: + resolve_market_paths, get_market_module)
├── cli.py                              (SHARED: + --market flag)
└── utils.py                            (SHARED)

configs/
├── competition.yaml                    (A-share baseline, renamed competition_a_share.yaml)
├── competition_hk.yaml                 (NEW)
├── competition_us.yaml                 (NEW)
└── agents/
    ├── claude_a_share.yaml             (renamed from claude.yaml)
    ├── codex_a_share.yaml              (renamed from codex.yaml)
    ├── claude_hk.yaml + codex_hk.yaml  (NEW)
    └── claude_us.yaml + codex_us.yaml  (NEW)

data/
├── a_share/{claude,codex}/             (existing data, relocated)
├── hk/{claude,codex}/                  (NEW)
├── us/{claude,codex}/                  (NEW)
└── competition_{a_share,hk,us}/        (per-market monthly_reviews)

reports/{a_share,hk,us}/{claude,codex}/ (3 markets x 2 agents)
reports/index.html                      (NEW: multi-market landing page)

deploy/systemd/                         (per-market services + timers; ~6-8 new units)
```

**Key design principles**:

1. **Market isolation**: each `markets/<m>/` subpackage is self-contained (data_provider + simulator + strategy + universe + mechanics). No cross-market imports.
2. **Shared reuse**: factor processing, overlay validation, evolution writing, reporting, notification — all logically uniform — stay at the top level and accept `market: str` for dispatch.
3. **CLI single entry**: `python3 -m stock_analyze --market <id> --agent <id> <subcmd>`. Default `--market=a_share` preserves backward compatibility for existing systemd ExecStart commands and operator habits.
4. **Locked field symmetry**: `competition_hk.yaml` and `competition_us.yaml` each have their own `locked_baseline` set (initial_cash, top_n, scope, benchmark, schedule, trading.*). `overlay_guard` loads the relevant baseline by market.
5. **Per-market independent histories**: `data/<market>/<agent>/evolution_log/<month>.md` — claude HK's June evolution can't pollute claude A-share's history.

## §2 Module Decomposition + API Surfaces

### Each market subpackage exposes a consistent interface

```python
# stock_analyze/markets/<m>/__init__.py
from .data_provider import make_provider
from .simulator import (
    execute_due_orders, update_nav, generate_rebalance_orders, initialize,
)
from .strategy import build_signals
from .universe import resolve_universe
from .mechanics import (
    SETTLEMENT_DAYS, DAILY_LIMIT_PCT, DEFAULT_LOT_SIZE, ALLOW_SHORTING,
    LOT_SIZE_FOR, STAMP_TAX_RATE, COMMISSION_RATE, SLIPPAGE_BPS,
    TRADING_HOURS_TZ, MARKET_CLOSE_LOCAL, MARKET_CLOSE_BJT,
)
```

CLI routing:
```python
market_mod = competition.get_market_module(args.market)
market_mod.execute_due_orders(...)
```

### `markets/<m>/mechanics.py` — market constants

| field | A-share | HK | US |
|---|---|---|---|
| `SETTLEMENT_DAYS` | 1 (T+1) | 2 (T+2) | 1 (T+1) |
| `DAILY_LIMIT_PCT` | 0.10 | `None` | `None` |
| `DEFAULT_LOT_SIZE` | 100 | per-stock via `LOT_SIZE_FOR` | 1 |
| `ALLOW_SHORTING` | False | True | True |
| `STAMP_TAX_RATE` | 0.001 | 0.0013 | 0 |
| `COMMISSION_RATE` | 0.00025 | 0.0003 | 0 |
| `SLIPPAGE_BPS` | 5 | 5 | 3 |
| `TRADING_HOURS_TZ` | "Asia/Shanghai" | "Asia/Hong_Kong" | "America/New_York" |
| `MARKET_CLOSE_LOCAL` | 15:00 | 16:00 | 16:00 |
| `MARKET_CLOSE_BJT` | 15:00 | 16:00 | 04:00 / 05:00 (DST) |

### `markets/<m>/data_provider/` — provider implementations

- `a_share/data_provider/` is the existing Tushare implementation, relocated unchanged. Continues to inherit from `DataProvider` in `markets/_base.py`.
- `hk/data_provider/yfinance_provider.py` and `us/data_provider/yfinance_provider.py` are NEW. They share ~80% code via a common base `YFinanceProvider` (in `markets/_yfinance_base.py`); per-market wrappers only differ in symbol suffixing (`.HK` vs naked ticker), universe membership, and basic-info field mapping.

Extended provider interface (over A-share's current `DataProvider`):
- `lot_size(code) -> int`
- `is_shortable(code) -> bool` (v1 returns True for all; v2 adds borrow check)

### `markets/<m>/simulator.py` — key deltas

A-share simulator (relocated): logic unchanged from current `stock_analyze/simulator.py`. Already covers T+1, ±10%, lot=100, commission/stamp/slippage, Tier-1+2 sizing fix. Only import paths change.

**HK simulator** key deltas (built from a_share template):
- T+2 settlement queue: extend the existing `available_shares` field (currently "settled vs in-flight") to track "settles in N days" for each lot.
- Drop `limit_up_buy_blocked` / `limit_down_sell_blocked` checks (no daily limit in HK).
- Variable lot: `_compute_target_shares` uses `lot_size = mechanics.LOT_SIZE_FOR(code)` instead of a constant 100.
- Shorting: allow `target_shares < 0`; on short, freeze 100% cash as `collateral_for_shorts` (a new field in state.json's cash structure).
- Stamp tax 0.13% on both buy and sell.

**US simulator** key deltas:
- T+1 (same as A-share).
- `lot_size = 1` for all stocks.
- No daily limit.
- Zero commission, zero stamp tax.
- Shorting same as HK simplified mechanism.

### Shared module changes

| module | change |
|---|---|
| `competition.py` | add `MARKETS = ['a_share','hk','us']`; add `resolve_market_paths(market, agent) -> AgentPaths`; add `get_market_module(market) -> module`. |
| `overlay_guard.py` | add `AVAILABLE_FACTORS_BY_MARKET: dict[str, set[str]]`; `validate(market, agent_id, overlay, repo_root)` gains `market` parameter (default `"a_share"` for back-compat). |
| `evolution_writer.py` | `write_evolution(market, agent_id, ...)` adds `market` parameter; path resolution goes through `competition.resolve_market_paths`. |
| `cli.py` | add `--market {a_share,hk,us}` flag with default `a_share`; subcommands route via `competition.get_market_module(market)`. |
| `notifier.py` | `build_daily_summary` accepts `markets: list[str]`; loops through each, generating per-market NAV/positions/sanity sections within one DM. |
| `sanity_check.py` | `check_agent(market, agent, repo_root)` gains `market`. |
| `reporting/` | per-market dashboard paths `reports/<market>/<agent>/dashboard.html`; new top-level `reports/index.html` linking to 9 dashboards (3 markets × {claude, codex, compare}). |
| `factor_pipeline.py` | no change (already market-agnostic). |

### Data path migration (one-time)

```
Before                            After
data/claude/        ─────────>    data/a_share/claude/
data/codex/         ─────────>    data/a_share/codex/
reports/claude/     ─────────>    reports/a_share/claude/
configs/competition.yaml         (kept; semantically becomes "a_share")
configs/agents/claude.yaml ─>     configs/agents/claude_a_share.yaml
                                  data/{hk,us}/{claude,codex}/     (NEW)
                                  reports/{hk,us}/{claude,codex}/  (NEW)
                                  configs/competition_{hk,us}.yaml (NEW)
```

ECS-side migration is a single rsync + path-rewrite step coordinated with systemd unit redeploy.

## §3 Data Flow + Timing

### Daily run pipelines (3 independent chains)

```
─── A-share chain (CST 17:25) ─────────────────────────────────
   market-data.timer fires
   → market-data.service: Tushare snapshot
   → claude-daily + codex-daily (OnSuccess=)
       • simulator.execute_due_orders(as_of=today)
       • simulator.update_nav(as_of=today)
       • build_signals (Friday only) → pending orders
   → aggregate-dashboard (OnSuccess=)
       • competition-dashboard refresh
       • [v1] no DM here; DM is emitted by the cross-market summary

─── HK chain (CST 16:30, same timezone) ──────────────────────
   hk-market-data.timer fires (after HK 16:00 close)
   → hk-market-data.service: yfinance pulls HSI + HSCEI daily
   → hk-claude-daily + hk-codex-daily
       • simulator.execute_due_orders (T+2 settlement queue)
       • simulator.update_nav
       • build_signals (Friday only)
   → hk-aggregate-dashboard

─── US chain (CST 06:00 next-day, DST-aware) ─────────────────
   us-market-data.timer fires after US 16:00 EST close
   → us-market-data.service: yfinance pulls S&P 500 + NASDAQ-100
   → us-claude-daily + us-codex-daily
       • simulator.execute_due_orders (T+1)
       • simulator.update_nav
       • build_signals (Friday only)
   → us-aggregate-dashboard

─── Cross-market summary DM (CST 06:30 next-day) ─────────────
   cross-market-summary.timer (NEW)
   → notifier.cli_send_daily_summary(markets=['a_share','hk','us'])
      builds one DM containing all 3 markets' NAV / positions / sanity
   → operator wakes at 09:00, reads one comprehensive global daily report
```

### Key timing decisions

- **Three chains are mutually independent.** Failure of one market does not cascade to the others.
- **One daily DM, not three.** Move the existing A-share `ExecStartPost=notify-daily-summary.sh` off `aggregate-dashboard.service` and onto the new `cross-market-summary.timer` (fires at 06:30 CST, after US chain). The operator sees one comprehensive daily DM each morning.
- **Per-market signal day = Friday** (using each market's own trading calendar).
- **HK T+2 means Friday signals execute on Tuesday** (not Monday like A-share or Monday-US). The simulator handles this naturally because `mechanics.SETTLEMENT_DAYS` drives the pending order's `trade_date`.

### Monthly evolution flow (v1)

```
1st of month, 09:00 CST (3 monthly-review timers fire in parallel):
  ├─ monthly-review --market a_share → data/competition_a_share/monthly_reviews/<m>.json
  ├─ monthly-review --market hk
  └─ monthly-review --market us

1st of month, 09:30 CST:
  → cross-market-monthly-summary DM:
    "📊 月度演化提醒  A 股 / HK / US: claude vs codex 上月数据 ..."

1st of month, operator-triggered (≥ 12:00):
  → Claude Code reads briefing data/<market>/<agent>/notes/briefings/<month>-monthly.md
  → Claude rewrites configs/agents/<agent>_<market>.yaml
  → evolution_writer.write_evolution(market=<market>, agent=<agent>, ...)
  → overlay_guard.validate(market, ...) passes (v1: no backtest gate)
```

Each agent runs **3 monthly reviews per month** (one per market) — three independent evolution_logs.

### Error handling at the flow level

| failure | response |
|---|---|
| yfinance pull fails (HK or US market-data) | retry × 3 → fail → systemd `OnFailure` → `PIPELINE_FAILURES.log` + (if `SA_LARK_WEBHOOK` set) group webhook. **Does not cascade** to agent-daily. agent-daily will raise `CacheMiss` if cache lookup also fails; the DM will note "HK data missing today, A-share+US ran". |
| an agent's simulator crashes | OnFailure handler runs notify-pipeline-failure.sh; other market/agent unaffected. |
| cross-market DM push fails | journal only, no retry, no alert (status channel, not alert channel). |
| Friday build_signals fails | OnFailure alert; holdings stay from previous week; next weekly retries. |

### Sanity-check timing

Bundled with the cross-market summary at 06:30 CST: `sanity_check.check_agent(market, agent)` runs for each of the 6 (market × agent) pairs. Critical findings render a red banner at the top of the daily DM.

## §4 Key Parameters (locked baseline + tunable overlay)

### HK baseline (`configs/competition_hk.yaml`)

```yaml
competition_id: "claude-vs-codex-hk"
start_date: "2026-06-15"
initial_cash: 1000000.0          # HK$1M (~¥920K equivalent)

accounts:
  - id: "hsi"
    scope: "hsi"
    top_n: 50
    cash: 500000
    benchmark: "^HSI"
  - id: "hscei"
    scope: "hscei"
    top_n: 50
    cash: 500000
    benchmark: "^HSCE"

schedule:
  execution: "weekly"
  signal_day: "friday"

trading:
  commission_rate: 0.0003
  stamp_tax_rate: 0.0013         # HK stamp duty
  slippage_bps: 5
  settlement_days: 2             # T+2
  daily_limit_pct: null          # no daily limit
  max_single_weight: 0.05
  allow_shorting: true
  shorting_collateral_ratio: 1.0
  lot_size_default: 100          # fallback; actual per-stock via yfinance lotSize
```

### US baseline (`configs/competition_us.yaml`)

```yaml
competition_id: "claude-vs-codex-us"
start_date: "2026-06-15"
initial_cash: 150000.0           # $150K (~¥1.07M equivalent)

accounts:
  - id: "sp500"
    scope: "sp500"
    top_n: 50
    cash: 75000
    benchmark: "^GSPC"
  - id: "ndx100"
    scope: "ndx100"
    top_n: 50
    cash: 75000
    benchmark: "^NDX"

schedule:
  execution: "weekly"
  signal_day: "friday"

trading:
  commission_rate: 0.0           # commission-free retail
  stamp_tax_rate: 0.0
  slippage_bps: 3
  settlement_days: 1             # T+1 (since May 2024)
  daily_limit_pct: null
  max_single_weight: 0.05
  allow_shorting: true
  shorting_collateral_ratio: 1.0
  lot_size_default: 1            # any whole share
```

### v1 factor set (HK + US, identical)

Pulled directly from yfinance with minimal post-processing:

| factor | source |
|---|---|
| `pe` | `Ticker.info["trailingPE"]` |
| `pb` | `Ticker.info["priceToBook"]` |
| `momentum_20` | 20-day return on `Ticker.history()` |
| `momentum_60` | 60-day return |
| `low_volatility_60` | 60-day return std (reverse-weighted) |
| `dividend_yield` | `Ticker.info["dividendYield"]` |

**v2 will add** (deferred to keep v1 scope tight): ROE, gross_margin, debt_ratio, net_profit_growth — these require parsing `quarterly_financials` DataFrames.

### Locked fields (overlay cannot override)

Same lock rules as the A-share competition, applied per market:
- `competition_id`, `start_date`
- `initial_cash`, `accounts.*.cash`, `accounts.*.top_n`, `accounts.*.scope`, `accounts.*.benchmark`
- `schedule.execution`, `schedule.signal_day`
- the entire `trading.*` block (commission, stamp, slippage, settlement_days, daily_limit_pct, max_single_weight, allow_shorting, shorting_collateral_ratio, lot_size_default)

`overlay_guard.AVAILABLE_FACTORS_BY_MARKET` per market:
- A-share: existing 10 + `<agent>_market_sentiment_1w`
- HK (v1): the 6 listed above (no broadcast factor in v1)
- US (v1): the 6 listed above (no broadcast factor in v1)

### Overlay-tunable fields (per market overlay yaml)

Each `configs/agents/<agent>_<market>.yaml` contains the same 7 top-level keys A-share uses today: `agent_id`, `strategy_id`, `name`, `factors`, `factor_processing`, `portfolio_controls`, `filters`. `agent_id` is explicitly suffixed (e.g. `claude_hk`, `codex_us`) to keep agent identities globally unique.

### Numeric trade-offs explained

| param | A-share | HK | US | note |
|---|---|---|---|---|
| initial capital | ¥1M | HK$1M | $150K | all ≈ ¥1M equivalent; no FX conversion across markets |
| universe size | ~800 | ~100 | ~600 | HK blue-chip pool is naturally smaller |
| top_n × accounts | 50 × 2 = 100 | 50 × 2 = 100 | 50 × 2 = 100 | consistent structure simplifies operator mental model |
| signal_day | Friday | Friday | Friday | cross-market aligned |
| execution lag | T+1 | T+2 | T+1 | market-specific via `mechanics.SETTLEMENT_DAYS` |

## §5 Error Handling + Testing + Rollout

### yfinance-specific failure modes

| failure | response |
|---|---|
| Yahoo returns empty DataFrame | retry × 3 (exponential backoff) → still empty → fall back to latest cache (`source="cache"`) → still nothing → raise `CacheMiss` |
| `info` dict missing field (e.g. `trailingPE = None`) | write NaN into factor frame; `min_factor_coverage` filter handles it naturally |
| Yahoo HTTP 429 rate limit | already designed: per-stock sleep 0.5s (800 stocks ≈ 7 min, well within budget) |
| Yahoo schema change (field disappears) | try/except per field in provider; failure → NaN + log `field_missing:<name>` so the dashboard surfaces drift |
| Symbol delisted / merged | universe.py keeps a static list; failed pulls go through the same "missing data" path |
| Dividend / split history adjustment shifts momentum | use yfinance `auto_adjust=True` (default), consistent with A-share's adjusted-price convention |

### Cache strategy

Mirrors A-share's Tushare cache pattern:
- `data/shared/cache_hk/spot/YYYY-MM-DD.csv` — daily snapshots
- `data/shared/cache_us/spot/YYYY-MM-DD.csv`
- `--offline` mode looks up cache; miss raises `CacheMiss`. No remote retry in offline mode.

### Test strategy

```
tests/
├── markets/
│   ├── test_a_share_simulator.py       (existing tests relocated; only import paths change)
│   ├── test_hk_simulator.py            (NEW)
│   │   ├── T+2 settlement queue correctness
│   │   ├── no daily-limit block (verify `limit_up_buy_blocked` is unreachable)
│   │   ├── variable lot size in `_compute_target_shares`
│   │   ├── simplified shorting: collateral freeze/release
│   │   ├── stamp tax 0.13% on buy + sell
│   │   └── short-cover P/L calculation
│   ├── test_us_simulator.py            (NEW)
│   │   ├── T+1 (same as A-share)
│   │   ├── lot=1 (no rounding)
│   │   ├── zero commission / zero stamp
│   │   ├── shorting (same simplified mechanism)
│   │   └── DST timezone transition does not affect `trade_date`
│   ├── test_hk_data_provider.py        (NEW; mock yfinance.Ticker)
│   ├── test_us_data_provider.py        (NEW)
│   └── test_market_router.py           (NEW; competition.get_market_module)
├── test_overlay_guard_multi_market.py  (NEW; AVAILABLE_FACTORS_BY_MARKET)
├── test_evolution_writer_multi_market.py (NEW)
├── test_notifier_multi_market.py       (extend existing test_notifier)
└── (existing tests all sweep import paths + pass)
```

Coverage targets: ~15 cases per market simulator, ~10 cases per data_provider (mock `yfinance.Ticker`), end-to-end CLI smoke test on 3 markets × 2 agents = 6 scenarios.

**Regression protection**: after A-share relocation, all 360+ existing tests must still pass (including sanity_check, notifier, reporting, backtest, sentiment).

### Rollout phases (each phase ships independently)

```
Phase 1 — Refactor (no functional change)
  ├─ Move A-share code to stock_analyze/markets/a_share/
  ├─ Sweep ~80 import sites: from stock_analyze.X → from stock_analyze.markets.a_share.X
  ├─ Move data/{claude,codex}/ → data/a_share/{claude,codex}/
  ├─ Move reports/{claude,codex}/ → reports/a_share/{claude,codex}/
  ├─ Rename configs/agents/{claude,codex}.yaml → configs/agents/{claude_a_share,codex_a_share}.yaml
  ├─ CLI adds --market flag, default=a_share, all existing commands still work
  ├─ systemd unit rename + ExecStart path updates
  └─ 360/360 tests pass; A-share daily + weekly E2E smoke pass → ship
     (Sensitive window: deploy before the next 5/30 weekly so the next cycle uses the new layout)

Phase 2 — HK online
  ├─ markets/hk/{data_provider, simulator, strategy, universe, mechanics}.py
  ├─ Shared modules (overlay_guard / evolution_writer / notifier / sanity_check) accept `market` param
  ├─ configs/competition_hk.yaml + agents/{claude,codex}_hk.yaml initial overlay
  ├─ tests/markets/test_hk_*.py all pass
  ├─ HK systemd unit + ECS deploy + first run dry test
  └─ Run 1 week of paper trading; observe → ship

Phase 3 — US online
  ├─ markets/us/{data_provider, simulator, ...}.py
  ├─ configs/competition_us.yaml + overlay
  ├─ tests
  ├─ US overnight timer + cross-market summary DM
  └─ ship

Phase 4 (later) — backtest + sentiment for HK/US
```

### Risk / rollback

| risk | mitigation |
|---|---|
| Phase 1 refactor breaks A-share daily | develop on new branch; full local + shadow ECS path E2E pass before cut over; retain `data/_pre_migration_backup/` |
| yfinance suddenly down | persistent cache guarantees ≥ 1 day offline runnability; alert channel (DM) surfaces it |
| HK / US data quality issues | sanity_check gains HK/US-specific checks (universe hit rate, price jumps) |
| Overnight US timer drifts or fails | OnFailure alert; cross-market DM displays "US data missing today" so operator can manually patch |
| Daylight Saving Time confusion for US | use systemd `OnCalendar=` with America/New_York timezone + a +1h buffer; tests assert `trade_date` correctness across DST transition |

## Out of scope (explicit non-goals)

- Cross-market PNL aggregation, combined leaderboard, multi-market "global" claude vs codex winner.
- Full short-selling realism (borrow-availability check, Reg-T 50% / FINRA 25% margin, overnight financing, forced liquidation).
- FX conversion between markets.
- Options, ETFs, fractional shares.
- HK secondary listings / dual-class shares special handling beyond what yfinance returns.
- After-hours / pre-market US trading.
- Intraday execution (everything is daily close-price simulation).
- Real broker integration (this remains paper trading).

## Open items handled by the implementation plan

The following are decided in this design but require explicit code-level resolution in the implementation plan (next skill):

- Exact algorithm for the T+2 settlement queue inside `markets/hk/simulator.py` (state.json schema change).
- `LOT_SIZE_FOR(code)` static table vs dynamic yfinance lookup (with cache).
- Universe definition for HSI / HSCEI / S&P 500 / NASDAQ-100 (static CSVs maintained manually, or fetched from yfinance and snapshotted weekly).
- DST timer schedule for US (`OnCalendar=06:00` in `America/New_York`? `Asia/Shanghai`? — needs concrete cron syntax).
- Migration script for moving existing data paths + ensuring no in-flight A-share run gets interrupted mid-cutover.
- Notification DM template when 1 of 3 markets is missing today's data (don't break the whole DM).
