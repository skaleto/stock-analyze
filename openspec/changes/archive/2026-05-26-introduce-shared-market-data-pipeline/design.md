## Context

当前架构里 `AkshareProvider` 的 9 个 fetch 方法分两类：

- **真 cache-first**（4 个）：`basic_info`、`valuation_metrics`、`financial_metrics`、`dividend_yield`。先 `load_cache` 命中即返回。
- **伪 cache-first**（5 个）：`spot`、`index_constituents`、`price_history`、`trading_calendar`、`benchmark_close`。每次都打网络，只在网络全失败时读 cache 兜底。其中 `benchmark_close` 连 cache 写入都没有。

`data/shared/cache/` 目录虽然由两个 agent 共用，但因为 5 个伪 cache-first 方法每次都打网络，两 agent 实际打了几乎完全相同的接口，浪费一半。

经与用户对话确认 weekly 在其执行时点（Fri 17:40）不需要"现拉"任何数据——所有信号原料都可以由一个每日运行的数据任务累积进缓存。因此最干净的架构是：

- 一个独立任务负责拉数据（每天跑），写入 `data/shared/cache/`
- 两个 agent 服务只跑策略计算，强制 offline，cache miss 即 fail-fast

## Goals / Non-Goals

**Goals**

- 把"拉数据"从两个 agent 各跑一次，变成**一个独立任务跑一次**。
- agent 跑步**绝不打网络**。即便 prepare-market-data 失败导致 cache miss，也要 fail-fast 而不是兜底偷打。
- 两个 agent 看到字节级相同的 raw data。
- 任一天 prepare-market-data 失败时，下游 agent service 不被触发（无脏数据写入）。
- 5/22 (Fri) 17:40 第一次 weekly 必须用上 5/22 17:25 拉的全候选池数据。

**Non-Goals**

- 不改 agent 的策略代码（factor_pipeline / portfolio_controls / build_target_orders / build_signals 等签名与行为完全不变）。
- 不改 `data/<agent>/*` 的目录结构和文件 schema。
- 不引入"用昨天 cache 兜底今天"的韧性逻辑（严格 fail-fast；任何静默回退都会让 dashboard 上的 NAV 失真而你不知道）。
- 不调整 `competition.yaml` 锁字段或 agent overlay schema。
- 不接券商、不下真单（永远）。

## Decisions

### 1. 一个数据任务 vs 两个数据任务

考虑过两种切分：

- **方案 Y**（已废弃）：daily-data（轻，拉持仓+基准） + weekly-data（重，拉全候选池）
- **方案 X**（选定）：一个 prepare-market-data 每天都拉全候选池

选 X 的理由：

- weekly 因子需要 60-220 天历史窗口 + 当天最新值。如果数据任务每天拉一次"今天的快照"，到周五就累积了一周完整数据，weekly 直接读 cache 即可。
- 一套调度逻辑、一份 snapshot 元数据，调试简单。
- 数据韧性更高：周五 fetch 挂了，仍有周四 cache。但严格模式下不自动兜底；至少快照在那里，运维可以手工 force-recover。
- 网络成本从 ~2000/周 涨到 ~5000/周，但都是公开免费接口，可以承受。

### 2. `prepare-market-data` 编排

新模块 `stock_analyze/market_data.py`：

```python
def prepare_market_data(
    scope_pool: list[str],            # e.g. ["hs300", "zz500"]
    as_of: str | None = None,         # 默认 today
    repo_root: Path | None = None,
    force: bool = False,              # 即便今日 snapshot 已存在也重拉
) -> dict[str, Any]:                  # 返回 snapshot 元数据
```

执行顺序（按依赖关系）：

1. `provider.trading_calendar()` —— 后续 `next_trading_day` 用得到
2. `provider.spot()` —— 实时行情（含 PE/PB 快照）
3. 对每个 scope（hs300、zz500）：`provider.index_constituents(scope)` —— 拿候选名单
4. 合并候选 + 应用 baseline locked filters 取 top max_fetch_candidates（按现有 preselect 逻辑）
5. 对每只候选并发拉：`basic_info` / `price_history(days=220)` / `valuation_metrics` / `financial_metrics` / `dividend_yield`
6. `benchmark_close(code, as_of)` 对每个基准
7. 写 `data/shared/market_snapshot_<as_of>.json`：

```json
{
  "as_of": "2026-05-22",
  "started_at": "2026-05-22T17:25:01",
  "finished_at": "2026-05-22T17:27:43",
  "duration_ms": 162042,
  "scopes": ["hs300", "zz500"],
  "candidates_fetched": 250,
  "rows": {
    "spot": 5400,
    "trading_calendar": 12450,
    "constituents_000300": 300,
    "constituents_000905": 500,
    "price_history": 250,
    "valuation": 250,
    "financial": 250,
    "dividend": 250,
    "benchmark_000300": 1,
    "benchmark_000905": 1
  },
  "errors": [
    {"code": "001234", "method": "financial_metrics", "message": "..."}
  ],
  "fetch_summary": {
    "ok": 1248,
    "retried": 5,
    "failed": 2
  }
}
```

8. 在 `data/shared/runs.csv` 追加一行（pipeline 自身账本）

### 3. `CacheMiss` 异常与 offline_mode

新异常类：

```python
class CacheMiss(RuntimeError):
    def __init__(self, method: str, cache_name: str):
        super().__init__(f"cache_miss:{method}:{cache_name}")
        self.method = method
        self.cache_name = cache_name
```

`AkshareProvider.__init__(self, cache_dir, offline: bool = False)`：

- `offline=False`（默认，用于 prepare-market-data）：先 load_cache → miss 走网络 → 写 cache
- `offline=True`（agent 服务用）：先 load_cache → miss 直接 `raise CacheMiss(method, cache_name)`

9 个 fetch 方法**统一模板**：

```python
def price_history(self, code, as_of, days):
    cache_key_mem = f"{code}:{as_of}:{days}"
    if cache_key_mem in self._history_cache:
        return self._history_cache[cache_key_mem].copy()

    cache_name = f"history_{code}_{ak_date(as_of)}_{days}"
    cached = self.load_cache(cache_name)
    if not cached.empty:
        normalized = normalize_history(cached)
        self._history_cache[cache_key_mem] = normalized
        return normalized.copy()

    if self.offline:
        raise CacheMiss(method="price_history", cache_name=cache_name)

    # 否则走网络（沿用现有 sources fallback 逻辑）
    ...
```

`benchmark_close` 之前没有 cache，本次新增按 `benchmark_<code>_<YYYYMMDD>.csv` 命名的日级缓存。

### 4. CLI 接口

`prepare-market-data` 子命令：

```bash
python3 -m stock_analyze prepare-market-data \
  [--as-of YYYY-MM-DD] \
  [--scopes hs300 zz500] \
  [--force] \
  [--data-dir data] \
  [--logs-dir logs]
```

默认 scopes 从 `configs/competition.yaml.accounts.*.scope` 推导。

`run-daily` / `run-weekly` 加 `--offline` 标志：

```bash
python3 -m stock_analyze --agent claude run-daily --offline
python3 -m stock_analyze --agent codex run-weekly --offline
```

`--offline` 传给 `AkshareProvider(offline=True)`。systemd service 文件里默认带 `--offline`。

### 5. systemd 拓扑改造

两个独立 pipeline timer，按自然日错开 daily 与 weekly：

**Pipeline 1：Mon-Fri 17:25 数据 + daily agent**

- `stock-analyze-market-data.timer`：`OnCalendar=Mon..Fri *-*-* 09:25:00 UTC`（CST 17:25）
- `stock-analyze-market-data.service`：
  ```
  ExecStart=/opt/stock-analyze/app/venv/bin/python -m stock_analyze.cli prepare-market-data
  ExecStartPost=/bin/systemctl start --no-block stock-analyze-claude-daily.service
  ExecStartPost=/bin/systemctl start --no-block stock-analyze-codex-daily.service
  ```
  两个 ExecStartPost 各一行（systemd 支持多个 ExecStartPost）。ExecStart 成功后才触发 daily；`--no-block` 让两 agent 并行启动。

**Pipeline 2：Sat 10:00 weekly agent（用周五缓存）**

- `stock-analyze-weekly-trigger.timer`：`OnCalendar=Sat *-*-* 02:00:00 UTC`（CST 10:00）
- `stock-analyze-weekly-trigger.service`：
  ```
  ExecStart=/bin/true
  ExecStartPost=/bin/systemctl start --no-block stock-analyze-claude-weekly.service
  ExecStartPost=/bin/systemctl start --no-block stock-analyze-codex-weekly.service
  ```
  这个 service **不**再跑 prepare-market-data——周五 17:25 的 cache 已经包含 weekly 需要的全部数据。ExecStart 用 `/bin/true` 占位（systemd 要求 service 必须有 ExecStart）。

修改：

- 4 个 `stock-analyze-{claude,codex}-{daily,weekly}.service`：`ExecStart` 加 `--offline`
- 4 个 `stock-analyze-{claude,codex}-{daily,weekly}.timer`：**删除**（agent 不再独立调度）

不动：

- `stock-analyze-monthly-review.{service,timer}` —— 每月 1 号 09:00 跑 review，独立链路
- `stock-analyze-dashboard.service` —— 常驻 dashboard，不变

### 5b. 周六 weekly 时间选 10:00 的理由

- **与 daily 自然日分隔**：周五 daily 在 17:25-17:35 跑完，周六上午 weekly 跑——cache 已经"睡了一晚"，时间边界清晰，运维 / dashboard 看时间戳就知道是哪一波。
- **早于上班时间**：10:00 跑完，人到工位（或者上午翻手机）已经能看到一周复盘。
- **不需要赶交易日**：A 股周六不开盘，没有时效性。Mon 收盘后 daily（17:25-17:35）会按 weekly 下的订单 build_target_orders → execute_due_orders，所以 weekly 跑完后到周一收盘有 ~48 小时缓冲，足够人工 review 周六的 proposal / overlay 状态。
- **避免与 monthly-review 同时段冲突**：monthly-review 每月 1 号 09:00 跑，10:00 跑 weekly 即便 1 号撞上周六也不互相挤。

### 5c. 为什么不用 dispatch-agents.sh

早期设计草案里把"今天是 Mon-Thu 触发 daily / 今天是 Fri 触发 weekly"放进一个 dispatch shell 脚本。挪到周六之后这个分发逻辑消失了：

- Mon-Fri timer 永远触发 daily
- Sat timer 永远触发 weekly

每个 timer 的语义单一、ExecStartPost 直接写死目标 service 名即可，shell 脚本反而多一层依赖。

### 6. 老 timer 的迁移

部署时需要在 ECS 上：
1. `systemctl disable --now stock-analyze-{claude,codex}-{daily,weekly}.timer` × 4
2. rsync 新代码与 systemd 文件
3. `cp` 新 unit 到 `/etc/systemd/system/`（`market-data.{service,timer}` + `weekly-trigger.{service,timer}` + 改过的 4 个 agent service）
4. `rm /etc/systemd/system/stock-analyze-{claude,codex}-{daily,weekly}.timer` × 4
5. `systemctl daemon-reload`
6. `systemctl enable --now stock-analyze-market-data.timer stock-analyze-weekly-trigger.timer`

runbook 提供完整脚本。

### 7. 严格 fail-fast，不自动用旧 cache

讨论过"agent 用昨天的 cache 兜底"的方案，否决：

- 静默用旧数据 → dashboard 看不到异常 → NAV 偷偷漂移
- 严格 fail-fast → service `failed` → dashboard 红色 → 立刻被发现

如果将来真的有"低值低频接口允许 stale 几天" 的需求（比如行业字段），可以按方法粒度配置；本次不做。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Fetch 时间过长，agent 启动时数据还没准备好 | agent CacheMiss fail | ExecStartPost 在 `ExecStart` 完成后才触发，systemd 保证顺序 |
| 周六 weekly-trigger 触发时周五 cache 已陈旧 | weekly 用一天前快照算因子 | 接受：A 股周六不开盘，周五收盘后到周六上午没有新数据；动量/估值因子对一天延迟不敏感 |
| 一个 candidate 失败影响整批 | 一只股票数据缺 → 因子覆盖率下降 | 错误聚合到 snapshot.errors，不阻塞整体；当 critical（如 spot / 全部基准）失败时整个 fetch 失败 |
| Mon..Thu 多拉的数据没人用 | 网络流量浪费 ~3000 调用/周 | 接受；公开接口免费；好处是 Fri 拉失败时仍有可用数据 |
| 周五 prepare-market-data 失败，周六 weekly 无最新 cache | weekly CacheMiss fail | 严格 fail-fast；运维手工 `prepare-market-data --as-of 2026-05-22 --force` 补一次，再手 trigger weekly |
| 删除 4 个老 timer 后忘记 enable 新 timer | ECS 空跑 | runbook 部署清单 + smoke test 验证 |
| 删除老 timer 影响向后兼容 | 单 agent 模式的 timer 也被波及？ | 不波及：单 agent 模式用 `stock-analyze-{daily,weekly}.timer`（无前缀），那两个不动 |

## Migration Plan

1. **第一阶段（代码）**：实现 CacheMiss / offline_mode / cache-first / prepare-market-data CLI / market_data 模块。单元测试用 mock provider 覆盖：cache hit、cache miss raise、prepare-market-data 写 snapshot、partial failure 聚合。

2. **第二阶段（systemd）**：写 4 个新 unit（`market-data.{service,timer}` + `weekly-trigger.{service,timer}`）；修 4 个 agent service 加 `--offline`。

3. **第三阶段（部署）**：
   - 本地 commit + push
   - rsync 到 ECS
   - 停老 timers / 删老 timers / 装新 timers / daemon-reload
   - 手动跑一次 `prepare-market-data` 看 snapshot 是否符合预期
   - 手动跑 `--agent claude run-daily --offline` 看是否能跑完
   - 手动跑 `--agent claude run-weekly --offline` 验 weekly 也能读同一份 cache
   - Enable 两个新 timer（market-data + weekly-trigger），等下个工作日 17:25 自动触发

4. **回滚**：单 commit 单次回退；ECS 上把老 timer 4 个重新装回（仓库里保留过、未删除文件）。

## Open Questions

- `prepare-market-data` 是否需要并发拉？250 只股票 × 5 个接口 = 1250 次顺序调用，每次 retry 2-3 秒，总耗时可能到 30-60 分钟。这超出 17:25 → 17:35（daily agent 启动）的窗口。**倾向用 ThreadPoolExecutor(max_workers=5)** 拉候选股票，能压到 ~10 分钟。如果并发触发限流，再调小。
- 是否需要一个 `--dry-run` 来快速看 snapshot 元数据但不真的拉？倾向不需要，runner 跑完会写完整 snapshot.json，看那个就行。
- 周六 weekly-trigger 是否需要先 `prepare-market-data --force`？倾向不需要：周五 17:25 的 cache 已经包含 weekly 全部数据；如果周五拉失败，运维应手动补；自动重拉会让"周六 cache 与周五不同"——这违反了"两 agent 看同一份数据"的核心约束。
