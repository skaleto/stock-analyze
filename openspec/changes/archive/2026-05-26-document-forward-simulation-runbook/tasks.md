## 1. OpenSpec Documentation

- [x] 1.1 Create the OpenSpec change directory for the simulation runbook.
- [x] 1.2 Write the proposal describing why the runbook and data-source contracts are needed.
- [x] 1.3 Write the design covering simulation flow, data-source fallback, deployment, and risks.
- [x] 1.4 Add a `forward-simulation-runbook` spec with fresh-machine setup, runtime output, and verification requirements.
- [x] 1.5 Add a `data-source-resilience` spec with provider health, fallback, turnover normalization, and relaxed-filter requirements.

## 2. Runtime Runbook

- [x] 2.1 Document local clone, virtualenv, dependency installation, and compile checks.
- [x] 2.2 Document CLI commands for `init`, `run-weekly`, `run-daily`, `dashboard`, and `serve-dashboard`.
- [x] 2.3 Document generated runtime files and the Git boundary around local state.
- [x] 2.4 Document Linux/systemd deployment and dashboard access through loopback or SSH tunneling.
- [x] 2.5 Document troubleshooting for public data-source failures, empty candidate pools, and dashboard freshness.

## 3. Code Behavior Captured

- [x] 3.1 Record that AkShare is primary and Baostock is a daily/historical fallback rather than a realtime replacement.
- [x] 3.2 Record retry and fallback behavior for realtime, historical, valuation, financial, and index constituent data.
- [x] 3.3 Record turnover amount normalization before liquidity filtering.
- [x] 3.4 Record strict-filter fallback behavior through `fallback_require_fields`.
- [x] 3.5 Record dashboard/report data-health visibility and the simulation-only investment boundary.

## 4. Verification

- [x] 4.1 Verify Python syntax with `py_compile`.
- [x] 4.2 Verify OpenSpec status reports all artifacts complete.
- [x] 4.3 Verify repository files do not contain machine-specific local markers before publishing.
- [x] 4.4 Commit and push documentation and code to the remote repository.
