# Dashboard API Performance Design

## Context

The React dashboard currently requests a market-wide summary and one monolithic
agent detail document. The summary internally builds the full detail document
for every market and strategy, while the detail document includes every model
prediction even though the page renders only eight rows per horizon. On the ECS
dataset the A-share detail response is about 4.8 MB, is formatted with JSON
indentation, disables caching, and takes more than 20 seconds to transfer through
the local tunnel.

## Goals

- Make the first useful dashboard content available without reading research,
  prediction, run-history, and report artifacts that are not needed for it.
- Give each API resource one domain responsibility and let failures remain local
  to the affected panel.
- Bound list responses and provide explicit pagination/top-N semantics.
- Avoid repeated disk scans and repeated transfer of unchanged responses.
- Preserve the existing dark dashboard design, data meaning, paper-trading state,
  and legacy detail endpoint.

## Architecture

### Resource API

`/api/dashboard/summary.json` remains the bootstrap endpoint, but its builder
uses only strategy, NAV, positions, pending orders, trades, and look-through
inputs required by the competition panel. It must not read prediction, model,
regime, source-health, weekly-report, or QDII research artifacts.

The selected market and strategy are loaded from independent endpoints:

- `/api/dashboard/overview.json`: identity, strategy, currency, and latest NAV.
- `/api/dashboard/performance.json`: portfolio and benchmark NAV series.
- `/api/dashboard/portfolio.json`: positions, pending orders, trades, and activity.
- `/api/dashboard/predictions.json`: bounded top predictions by horizon, alerts,
  regime context, model health, and source health.
- `/api/dashboard/research.json`: QDII selection, look-through, and research data.
- `/api/dashboard/operations.json`: recent runs and weekly-report metadata.

All endpoints require a valid `market` and `agent`. Prediction limits are clamped
server-side. The original `/api/dashboard/detail.json` remains available for old
clients and is assembled from the same domain builders.

### HTTP Delivery

The standard-library HTTP server remains appropriate for this read-only,
low-concurrency paper-trading dashboard. A thread-safe in-memory response cache
stores compact JSON and gzip variants for 15 seconds. Responses include `ETag`,
`Cache-Control`, `Vary: Accept-Encoding`, `Server-Timing`, and a request ID.
Matching `If-None-Match` requests receive `304`. Errors are never cached and do
not disclose filesystem paths.

### Frontend Loading

The React app loads summary first, then starts overview, performance, portfolio,
and prediction requests in parallel for the selected strategy. Research and
operations are a second, non-blocking wave. Each wave uses one abort controller
and a monotonically increasing request ID so stale selections cannot overwrite
current state. A failed resource leaves other panels usable and reports its own
error. Auto-refresh uses conditional browser revalidation instead of `no-store`.

## Performance Contract

- Summary construction does not call the legacy full-detail builder.
- Prediction payloads return at most the requested number of rows per horizon.
- JSON is compact; gzip is used when accepted.
- Cached identical requests do not rebuild domain data during the TTL.
- The production-like A-share selected-strategy response is split so no normal
  first-wave resource approaches the former multi-megabyte detail payload.

## Validation

- Python unit tests cover resource boundaries, prediction limits, validation,
  cache hits, ETag/304, gzip, and compact serialization.
- React tests cover independent resource requests, partial failures, stale-request
  protection, and unchanged interaction behavior.
- Real-data benchmarks compare endpoint size, time-to-first-byte, and total time
  before and after deployment.
- The full Python suite, frontend tests/build, and browser checks run before ECS
  deployment; the same endpoint probes run through the ECS tunnel afterward.

