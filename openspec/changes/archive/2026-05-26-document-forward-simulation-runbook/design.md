## Context

This repository now contains a forward simulation toolkit for A-share research. It is intentionally limited to simulated trading: it builds weekly signals, creates pending simulated orders, executes those orders with modeled costs on the next trading day, refreshes NAV, and renders Markdown/HTML reports. It does not connect to broker APIs and does not place real orders.

The system depends on public market-data interfaces that can fail, throttle, return different units, or change schema. Recent work therefore added source-level health tracking, cache files, fallback providers, and dashboard language that makes degraded data visible rather than hiding it.

## Goals / Non-Goals

**Goals:**

- Make the implemented simulation chain understandable from source-controlled documents.
- Provide a fresh-machine setup path for local development, local simulation, and Linux/systemd deployment.
- Capture data-source resilience decisions so future tuning does not accidentally reintroduce single-provider failure modes.
- Keep the dashboard and reports useful for a beginner investor while preserving the simulation-only risk boundary.

**Non-Goals:**

- No real brokerage integration, order routing, portfolio custody, or trading authorization.
- No guarantee that AkShare, Baostock, Tencent, Sina, or other public interfaces are complete or always available.
- No promise that the strategy is profitable; the output is a research and monitoring aid.
- No hard-coded machine-specific path, username, server address, or private key in repository files.

## Decisions

1. **Keep AkShare as the primary provider and add source-specific fallbacks.**

   AkShare still exposes the broadest set of A-share interfaces needed by the strategy. When Eastmoney realtime or historical endpoints fail, the provider records health events and falls back by data type: Tencent/Sina/Baostock/cache for historical data, Baostock for selected constituent and financial gaps, and cached spot data when realtime sources are unavailable.

2. **Use Baostock as a daily/historical fallback, not a realtime substitute.**

   Baostock can provide index constituents, historical daily bars, PE/PB from daily bars, and quarterly financial metrics. It does not provide a reliable full-market realtime spot feed, so realtime quote semantics stay with AkShare spot interfaces and cache fallback.

3. **Persist data-health before report rendering.**

   Weekly and daily commands write `data/data_health.json` before generating the dashboard and weekly report. This prevents the page from showing stale provider status after a fresh run.

4. **Normalize turnover amount units before applying liquidity filters.**

   Some historical sources return turnover in ten-thousand yuan while others return yuan. The data provider normalizes small positive turnover scales to yuan so `min_avg_amount_20` compares one unit across providers.

5. **Use strict filters first, then a documented relaxed fallback.**

   The strategy first applies configured `require_fields`. If public-data gaps empty the pool, it records `hard_filters_empty_relaxed` and falls back to `fallback_require_fields` so the weekly simulation can continue with visible data-quality warnings instead of failing the entire run.

6. **Keep runtime state out of Git.**

   `data/`, `reports/`, `logs/`, and `backups/` hold generated state and remain ignored except for `.gitkeep` placeholders. This lets another machine clone the code without inheriting stale local runtime state.

## Risks / Trade-offs

- Public data providers can fail or return partial data -> The provider records health rows, retries where useful, falls back by data type, and surfaces status in the dashboard/report.
- Fallback sources can use different units or accounting periods -> The provider normalizes known turnover units and converts Baostock financial ratios to percent-like values; reports still treat these as research inputs.
- Relaxed filters can admit lower-quality rows -> The strategy records a warning and still scores missing factors as zero contribution, making the result observable but not a buy/sell instruction.
- Full weekly runs can be slow on a cold cache -> The configuration caps candidate fetches and stores per-code CSV caches under `data/cache`.
- Systemd deployment can hide Python tracebacks in logs -> Services append stdout/stderr to files under `logs/`; operators should inspect both `.log` and `.err`.

## Migration Plan

1. Pull the latest repository code.
2. Create a Python virtual environment and install `requirements.txt`.
3. Run `python -m py_compile stock_analyze/*.py`.
4. Initialize runtime state with `python -m stock_analyze init`.
5. Run a small custom-pool smoke test or the configured weekly job.
6. For server deployment, sync code to the server app directory, install dependencies in the server venv, restart the dashboard service, and manually trigger the weekly service once.
7. Rollback by restoring the previous app directory and restarting systemd services; generated `data/` can be kept if the schema is unchanged.

## Open Questions

- Whether candidate limits should remain at the current value or be tuned lower after more weekly run history.
- Whether dashboard performance targets should compare against benchmark excess return directly once enough NAV points exist.
- Whether future versions should add a local provider test suite with recorded fixtures for public API schema drift.
