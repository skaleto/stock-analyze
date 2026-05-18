## ADDED Requirements

### Requirement: Data-source health is recorded
The system SHALL record provider attempts, retries, failures, cache hits, row counts, and short messages in `data/data_health.json` during daily and weekly runs.

#### Scenario: Provider fails and fallback succeeds
- **WHEN** a public data endpoint fails and another source succeeds
- **THEN** the health file records the failed source and the successful fallback source for later inspection

### Requirement: Realtime and historical fallback semantics are separated
The data provider SHALL treat realtime spot data and daily historical data as different capabilities, with Baostock used for daily/historical fallback but not as a full-market realtime quote replacement.

#### Scenario: Eastmoney realtime fails
- **WHEN** Eastmoney realtime quote retrieval fails
- **THEN** the provider retries, attempts configured realtime alternatives, and uses cache if available without claiming Baostock realtime coverage

### Requirement: Historical price fallback supports multiple public sources
The data provider SHALL attempt historical daily data through multiple sources and cache successful normalized output for future runs.

#### Scenario: Eastmoney history fails
- **WHEN** Eastmoney historical data fails for a stock
- **THEN** the provider attempts Tencent, Sina, Baostock, and finally local cache before marking price data missing

### Requirement: Financial and valuation gaps use fallbacks
The data provider SHALL fill valuation and financial gaps from fallback sources when primary interfaces return missing, invalid, or failed responses.

#### Scenario: Financial summary returns invalid data
- **WHEN** AkShare financial summary cannot be parsed for a stock
- **THEN** the provider attempts Baostock financial metrics and records the fallback status

### Requirement: Turnover amount units are normalized
The data provider SHALL normalize known turnover amount unit differences before liquidity filters calculate `avg_amount_20`.

#### Scenario: Tencent turnover is reported in ten-thousand yuan
- **WHEN** a historical source returns small positive turnover values consistent with ten-thousand-yuan units
- **THEN** the provider converts the series to yuan before scoring or filtering

### Requirement: Strict filters degrade gracefully
The strategy SHALL apply strict required-field filters first and SHALL fall back to documented `fallback_require_fields` when strict filtering empties the candidate pool.

#### Scenario: Strict data completeness empties a pool
- **WHEN** all candidates are removed by strict required-field filtering
- **THEN** the strategy records a warning and applies relaxed fallback requirements so the simulation can still produce observable results
