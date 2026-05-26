## ADDED Requirements

### Requirement: CacheMiss exception type

`stock_analyze.data_provider` SHALL 导出 `CacheMiss` 异常类，含 `method`（fetch 方法名）与 `cache_name`（缓存键）字段；异常消息格式 `cache_miss:<method>:<cache_name>`。

#### Scenario: Exception carries diagnostic context
- **WHEN** `raise CacheMiss(method="price_history", cache_name="history_000001_20260522_220")`
- **THEN** `str(exc)` 等于 `"cache_miss:price_history:history_000001_20260522_220"`
- **AND** `exc.method == "price_history"` 与 `exc.cache_name == "history_000001_20260522_220"` 都可读

### Requirement: AkshareProvider offline_mode flag

`AkshareProvider.__init__` SHALL 接受 `offline: bool = False` 参数；该字段控制 cache miss 时的行为：`offline=False` 走网络后写 cache，`offline=True` 直接 raise `CacheMiss`。

#### Scenario: offline=True never opens network sockets
- **GIVEN** `AkshareProvider(cache_dir="data/shared/cache", offline=True)`
- **AND** `data/shared/cache/` 为空
- **WHEN** 调用 `provider.spot()`（或任何 9 个 fetch 方法）
- **THEN** 抛 `CacheMiss`
- **AND** 进程内没有任何 outbound HTTP 请求

#### Scenario: offline=False (default) keeps existing network-then-cache behavior on cache miss
- **GIVEN** `AkshareProvider(cache_dir=..., offline=False)`
- **AND** cache 为空
- **WHEN** 调用 fetch 方法
- **THEN** 走网络 fetch
- **AND** 成功后写 cache

### Requirement: Cache-first semantics for all fetch methods

`AkshareProvider` 的 9 个 fetch 方法（`spot`、`index_constituents`、`price_history`、`trading_calendar`、`basic_info`、`valuation_metrics`、`financial_metrics`、`dividend_yield`、`benchmark_close`）SHALL 在调用网络前先调 `load_cache` 检查；命中即直接返回，**不打网络**。

#### Scenario: Warm cache hits skip network for spot
- **GIVEN** `data/shared/cache/spot_<YYYYMMDD>.csv` 存在且非空
- **WHEN** `provider.spot()` 被调用
- **THEN** 返回 cache 内容，无网络请求

#### Scenario: Warm cache hits skip network for price_history
- **GIVEN** `data/shared/cache/history_000001_20260522_220.csv` 存在且非空
- **WHEN** `provider.price_history("000001", as_of="2026-05-22", days=220)`
- **THEN** 返回 cache 内容，无网络请求

#### Scenario: benchmark_close newly gains date-keyed cache
- **GIVEN** `provider.benchmark_close("000300", as_of="2026-05-22")` 被调用（cache 为空，offline=False）
- **THEN** 网络获取成功后写 `data/shared/cache/benchmark_000300_20260522.csv`
- **AND** 第二次同样调用直接返回 cache，无网络
- **AND** 同样调用 `offline=True` 时直接 hit cache，不 raise

#### Scenario: All nine methods raise CacheMiss in offline mode without cache
- **GIVEN** `provider = AkshareProvider(cache_dir=tmp, offline=True)` 且 tmp 为空
- **WHEN** 依次调用 `spot()` / `index_constituents("hs300")` / `price_history("000001", ...)` / `trading_calendar()` / `basic_info("000001")` / `valuation_metrics("000001")` / `financial_metrics("000001")` / `dividend_yield("000001")` / `benchmark_close("000300")`
- **THEN** 每个调用都 raise `CacheMiss`
- **AND** `CacheMiss.method` 等于对应方法名

### Requirement: Backward compatibility for non-agent paths

单 agent 老路径（不带 `--agent` / 不带 `--offline`）SHALL 保持现有行为；`AkshareProvider` 默认 `offline=False`，cache-first 改造对老路径只是"happy path 多了一次 load_cache 检查"，性能影响可忽略。

#### Scenario: Legacy single-agent run still works
- **WHEN** 运行 `python3 -m stock_analyze run-daily`（无 `--agent` 无 `--offline`）
- **THEN** AkshareProvider 实例 `offline=False`
- **AND** 现有 cache-first 的 4 个方法 / 网络-first 改 cache-first 的 5 个方法 行为对调用者无可见差异（接口签名、返回类型、异常类型不变）
