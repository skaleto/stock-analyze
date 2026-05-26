## 1. OpenSpec Foundation

- [x] 1.1 `openspec new change introduce-shared-market-data-pipeline`。
- [x] 1.2 写 proposal.md 与 design.md，说明方案 X（单数据任务 + offline agent）。
- [ ] 1.3 3 个 capability specs：`shared-market-data-fetch` / `offline-cache-first-provider` / `pipeline-systemd-orchestration`。
- [ ] 1.4 `openspec validate introduce-shared-market-data-pipeline --strict` 通过。

## 2. AkshareProvider cache-first + offline_mode

- [ ] 2.1 在 `data_provider.py` 新增 `class CacheMiss(RuntimeError)`。
- [ ] 2.2 `AkshareProvider.__init__` 接受 `offline: bool = False` 字段。
- [ ] 2.3 改 `spot()`：先 `load_cache("spot_<YYYYMMDD>")` 或 `spot_latest` → 命中 return；miss + offline → raise；miss + online → 网络。
- [ ] 2.4 改 `index_constituents(scope)`：同上模式（`constituents_<index>` cache name 已存在）。
- [ ] 2.5 改 `price_history(code, as_of, days)`：把已有的"网络失败兜底读 cache"逻辑提前到"开网络前先读 cache"。
- [ ] 2.6 改 `trading_calendar()`：同样模式。
- [ ] 2.7 改 `basic_info` / `valuation_metrics` / `financial_metrics` / `dividend_yield` —— 这 4 个已经 cache-first，**只需在 cache miss 路径里 check offline，raise CacheMiss**。
- [ ] 2.8 改 `benchmark_close(code, as_of)`：新增 `benchmark_<code>_<YYYYMMDD>.csv` 日级缓存。
- [ ] 2.9 单元测试：cache hit 不打网络、cache miss + offline 抛 CacheMiss、cache miss + online 调用 mock fetch。

## 3. prepare-market-data 模块与 CLI

- [ ] 3.1 新模块 `stock_analyze/market_data.py`：`prepare_market_data(scope_pool, as_of, repo_root, force)`。
- [ ] 3.2 顺序：trading_calendar → spot → 各 scope 的 index_constituents → preselect 候选 → 并发拉每只候选的 5 个接口 → 拉两个基准。
- [ ] 3.3 用 `ThreadPoolExecutor(max_workers=5)` 并发拉候选，每只股票 5 个调用是顺序的（避免单股 4 个并发触发限流）。
- [ ] 3.4 错误聚合：单只候选某个接口失败 → `snapshot.errors` 加一行，整体继续；只有 spot 或全部基准失败才整体 fail。
- [ ] 3.5 写 `data/shared/market_snapshot_<YYYY-MM-DD>.json`（schema 见 design.md）。
- [ ] 3.6 `cli.py` 新增 `prepare-market-data` 子命令，args：`--as-of` / `--scopes` / `--force` / `--data-dir` / `--logs-dir`。
- [ ] 3.7 用 `RunLedger` 包裹（写 `data/shared/runs.csv`）。
- [ ] 3.8 单元测试：用 mock provider 验证 snapshot 字段完整、errors 聚合、`force=False` 时今日已存在不重复拉。

## 4. Agent CLI --offline 标志

- [ ] 4.1 `run-daily` / `run-weekly` 子命令加 `--offline` 标志。
- [ ] 4.2 `_resolve_runtime` 把 `args.offline` 传给 `AkshareProvider(offline=...)`。
- [ ] 4.3 单元测试：`run-daily --offline` 在空 cache 下抛 CacheMiss 且 RunLedger 写 `failed`。

## 5. systemd 拓扑

- [ ] 5.1 新增 `deploy/systemd/stock-analyze-market-data.service` + `.timer`（Mon-Fri 17:25 CST = `Mon..Fri *-*-* 09:25:00 UTC`）。service 的 `ExecStartPost` 直接 `systemctl start --no-block` 两个 daily agent service。
- [ ] 5.2 新增 `deploy/systemd/stock-analyze-weekly-trigger.service` + `.timer`（Sat 10:00 CST = `Sat *-*-* 02:00:00 UTC`）。service 的 `ExecStart=/bin/true`，`ExecStartPost` 直接 `systemctl start --no-block` 两个 weekly agent service。
- [ ] 5.3 修改 4 个 agent service：`ExecStart` 末尾加 `--offline`。
- [ ] 5.4 **删除** 4 个 agent timer 文件（`stock-analyze-{claude,codex}-{daily,weekly}.timer`）。
- [ ] 5.5 `docs/competition-runbook.md` systemd 章节重写部署/迁移步骤（包括两个新 timer 的 enable 顺序）。

## 6. 文档

- [ ] 6.1 `docs/competition-runbook.md`：新拓扑 + 迁移指南（disable 老 timer / 删老 timer / 装新 timer）。
- [ ] 6.2 `docs/system-overview.md`：架构图与节拍表更新。
- [ ] 6.3 `README.md` 顶部"二选一"提示同步：`stock-analyze-market-data.timer` 是双 agent 模式的唯一调度入口。

## 7. 验证 + 发布

- [ ] 7.1 `python3 -m py_compile stock_analyze/*.py tests/*.py`。
- [ ] 7.2 `python3 -m unittest discover -s tests`：85 既有 + 约 8 新增 ≈ 93 全绿。
- [ ] 7.3 `python3 -m pyflakes stock_analyze/*.py tests/*.py` 干净。
- [ ] 7.4 `openspec validate introduce-shared-market-data-pipeline --strict`。
- [ ] 7.5 烟囱：本地空 cache 下跑 `prepare-market-data --as-of 2026-05-22`，看 snapshot 是否合理；再跑 `--agent claude run-daily --offline` 看是否成功读 cache。
- [ ] 7.6 `git commit` + `git push origin HEAD:main`。
- [ ] 7.7 rsync 到 ECS；按 design.md §migration 部署。
- [ ] 7.8 ECS 上手动跑一次 `prepare-market-data` + `dispatch-agents.sh`，确认两 agent 都成功。
- [ ] 7.9 监控明天（5/22 Fri）首次 weekly 触发是否按新链路跑。

## Completion Checklist

- [ ] 全部 7 个 phase 完成。
- [ ] 单元测试 + pyflakes + openspec validate 三件套通过。
- [ ] ECS 上老 timer 全 disable + 新 timer 启用；list-timers 显示干净的单 pipeline 入口。
- [ ] 5/22 Fri 17:25 第一次自动触发链路跑通，dashboard 显示新 weekly 信号 + 50 只 pending orders。
