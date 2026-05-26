## ADDED Requirements

### Requirement: DataProvider abstract base class

`stock_analyze.data_provider` SHALL 定义 `DataProvider` ABC,统一所有数据源的接口契约,声明以下 8 个 fetch 方法签名:`spot(as_of)`, `daily(code, start, end)`, `fina_indicator(code)`, `stock_basic()`, `index_weight(scope, trade_date)`, `index_daily(code, as_of)`, `dividend(code)`, `trade_cal()`。

#### Scenario: All providers expose the same interface
- **GIVEN** `TushareProvider` 与 `BaostockProvider` 均继承 `DataProvider`
- **WHEN** 检查类方法签名
- **THEN** 两个 provider 都有上述 8 个方法,签名一致(参数名 + 类型 hint)

### Requirement: TushareProvider as primary source

`TushareProvider` SHALL 通过 `tushare` Python SDK 调用 Tushare Pro API,token 从环境变量 `TUSHARE_TOKEN` 读取,所有 fetch 方法 cache-first(命中 cache 不打网络),与 `introduce-shared-market-data-pipeline` 已定义的 `offline=True` → `CacheMiss` 契约一致。

#### Scenario: Token missing fails fast at provider init
- **GIVEN** 环境变量 `TUSHARE_TOKEN` 未设置或为空字符串
- **WHEN** 实例化 `TushareProvider(token=os.environ.get("TUSHARE_TOKEN"))`
- **THEN** raise `RuntimeError` 或专门的 `TushareTokenMissing`,消息含"see docs/tushare-token-setup.md"

#### Scenario: Token never written to disk or log
- **GIVEN** `TushareProvider` 已实例化,token 在内存中
- **WHEN** 任何 fetch 方法被调用 + 日志写出 + cache 文件写出
- **THEN** token 字符串**完全不出现在**`logs/`、`data/`、`reports/` 任何文件中
- **AND** 也**不出现在**`runs.csv` 的 error_summary 字段中

#### Scenario: Tushare daily_basic units normalised
- **WHEN** `provider.spot(as_of="2026-05-22")` 调用,Tushare 返回 `total_mv=10000`(万元)
- **THEN** 返回的 DataFrame `market_cap_yi` 列对应值为 `1.0`(亿元)

#### Scenario: Tushare daily amount unit converted
- **WHEN** `provider.daily(code="600519.SH", start, end)` 返回 `amount=1234567`(千元)
- **THEN** 输出 `amount` 字段(供下游 avg_amount_20 计算)为 `1234567000.0`(元)

### Requirement: BaostockProvider as fallback

`BaostockProvider` SHALL 实现 `DataProvider` 接口,使用 `baostock` SDK,无 token,提供与 `TushareProvider` 字段对齐的数据。当 Tushare 临时不可用时(`ConnectionError` / 5xx / 限频)自动接管。

#### Scenario: Baostock kicks in when Tushare raises ConnectionError on spot
- **GIVEN** `make_provider` 已配置 Tushare 主 / Baostock 备
- **AND** Tushare API 临时不可达
- **WHEN** `provider.spot(as_of)` 调用
- **THEN** 自动调 Baostock 路径,结果 DataFrame 字段名与 Tushare 路径一致
- **AND** 在 `runs.csv` 当行记录 `data_source=baostock_fallback`

#### Scenario: Baostock provides same fields as Tushare for fina_indicator
- **WHEN** Baostock 路径调用 `fina_indicator("sh.600519")`
- **THEN** 返回 DataFrame 至少含 `roe`, `grossprofit_margin`, `debt_to_assets`, `netprofit_yoy`(字段名与 Tushare 一致)

### Requirement: make_provider factory

`stock_analyze.data_provider.make_provider(token, cache_dir, offline=False, as_of=None) -> DataProvider` SHALL 根据 token 是否提供来返回 `TushareProvider`(有 token)或 `BaostockProvider`(无 token),前者总是 primary,后者仅在 primary 失败时 fallback。

#### Scenario: Token provided → TushareProvider
- **WHEN** `make_provider(token="32_char_token", cache_dir=tmp)` 调用
- **THEN** 返回的 instance 是 `TushareProvider`
- **AND** primary 链路为 Tushare,fallback 为 Baostock

#### Scenario: Token absent → BaostockProvider only
- **WHEN** `make_provider(token=None, cache_dir=tmp)` 调用
- **THEN** 返回的 instance 是 `BaostockProvider`
- **AND** 无 Tushare 链路尝试(避免无 token 抛错延迟)

### Requirement: Cache key schema unchanged

迁移后的 cache 文件名 SHALL 与现有 schema 完全一致,以保证向后兼容:`spot_<YYYYMMDD>.csv`、`history_<code>_<end>_<days>.csv`、`financial_<code>_<YYYYMMDD>.csv`、`valuation_<code>_<YYYYMMDD>.csv`、`constituents_<index>_<YYYYMMDD>.csv`、`benchmark_<code>_<YYYYMMDD>.csv`、`dividend_<code>.csv`。

#### Scenario: Existing cache loaded by new provider
- **GIVEN** ECS 上已有 `data/shared/cache/spot_20260522.csv`(由旧 AkshareProvider 写入)
- **WHEN** `TushareProvider(offline=True)` 调用 `spot(as_of="2026-05-22")`
- **THEN** cache 命中,返回内容与文件一致,**不打网络**

### Requirement: ts_code format normalisation

Tushare 用 `<code>.SH/.SZ` 格式(如 `600519.SH`),内部表示统一为 6 位纯数字(如 `600519`)。Provider SHALL 在 input / output 边界做双向转换,确保下游 `factor_pipeline` / `simulator` 不需修改。

#### Scenario: Symbol conversion both ways
- **WHEN** 调用 `provider.daily(code="600519", ...)`
- **THEN** 内部转换为 `600519.SH` 调用 Tushare;返回 DataFrame `ts_code` 列归一回 `600519`(无后缀)

### Requirement: AKShare removed entirely

迁移后 `stock_analyze/` 任何 `.py` 文件 SHALL 不再 `import akshare`、不再调用 `ak.*` 方法。`requirements.txt` SHALL 不再包含 `akshare`。

#### Scenario: No akshare import anywhere
- **WHEN** `grep -rn "import akshare\|from akshare" stock_analyze/ tests/`
- **THEN** 结果为空

#### Scenario: requirements.txt clean
- **WHEN** `cat requirements.txt | grep -i akshare`
- **THEN** 结果为空
- **AND** `cat requirements.txt | grep tushare` 返回 `tushare>=1.4.0`(或更高)

### Requirement: home-backfill workflow removed

`scripts/home-backfill.sh`、`docs/home-backfill-runbook.md` SHALL 被删除;README.md SHALL 移除对应警告段。

#### Scenario: Files gone
- **WHEN** `ls scripts/home-backfill.sh docs/home-backfill-runbook.md`
- **THEN** 两个文件都不存在
