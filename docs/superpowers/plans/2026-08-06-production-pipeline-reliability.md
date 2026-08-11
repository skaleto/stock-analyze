# Stock-Analyze 生产流水线可靠性改造实施计划

> 日期：2026-08-06  
> 范围：ECS 日度模拟盘、公告情报后台链路、运行通知与 Dashboard 运维真值。  
> 安全边界：不修改策略参数，不连接真实券商，不用当前数据补写历史交易，不让原始文本直接触发下单。

## 一、当前现状与根因

线上最近的失败不是单一业务异常，而是任务编排和资源边界共同造成的：

1. `stock-analyze-market-data.service` 依赖 `stock-analyze-intelligence.service`。公告情报超时会阻塞 18:30 的行情主链。
2. ECS 仅约 1.6 GiB 内存，研究、PDF 解析、质量全库扫描并发时，单进程峰值可接近 1.3 GiB，已触发 OOM。
3. `intelligence-status` 在高频增量任务末尾执行全库质量检查；数据库已达数十万文档，这不应属于高频热路径。
4. PDF 回填、对账、语义抽取和日度交易缺少统一的资源窗口，后台任务可能在收盘主链期间占用内存。
5. 飞书日报按固定时间发送，不检查四个正式策略是否全部形成终态，因此可能把“仍在运行”误报成“当日失败”。
6. Dashboard 的后台回填新鲜度只看分布式 worker lease，忽略本机回填状态；磁盘只在 85% 后提示，且线上源码存在一次不完整覆盖的潜在重启风险。
7. 研究源目录每天保存的是累计快照，但读取器会把所有历史累计快照再次全部载入并合并；20 个运行日已造成约 1.1 GiB 压缩数据重复解压，日跑随历史天数持续变慢并大量换页。

## 二、改造后预期效果

### 1. 日度主链

- 18:30 行情任务不再启动或等待公告情报任务。
- 行情、研究和四个正式策略按顺序执行；4/4 成功后再启动候选模型迭代，公告 PDF/语义后台失败不再阻断模拟盘。
- 四个正式策略全部成功后才发送成功日报；失败则由失败单元即时告警，并由 21:30 兜底摘要汇总。
- 不自动重放 2026-08-05 等历史缺口，避免用未来数据污染模拟交易；缺口只展示并进入人工审计。

### 2. ECS 资源

- 工作日 17:45-21:30 为日度关键窗口，PDF 回填与重型质量扫描主动让路。
- PDF 回填、文档对账、质量扫描分时运行，避免重任务并发。
- 当前 1.6 GiB 主机应能串行完成日度链；仍建议后续升级至至少 4 GiB，升级用于提高吞吐，不作为本次正确性的前置条件。
- 研究源快照使用显式 `cumulative` manifest；成功快照之后只读取最新完整基线及其后的未压实增量，不再每天重读全部历史累计副本。

### 3. 情报链路

- 高频任务只做增量元数据、规则抽取和轻量状态快照。
- 全库质量报告拆为独立低频任务，不再拖慢每次增量。
- PDF 下载/解析继续使用“文本层优先、必要时 OCR”，失败保留可重试状态与错误原因，不丢文档。
- DeepSeek 语义抽取保留为独立低优先级增量通道；输入仍经过通用契约，输出必须通过确定性校验后才能写入 canonical event/factor。

### 4. Dashboard 与通知

- 运维中心分别展示正式日度主链和后台情报，不再把情报状态当作交易主链前置条件。
- 展示本机回填阶段、最近更新时间、暂停原因、积压量与磁盘预警。
- 磁盘 80% 给出 warning，88% 给出 critical；后台陈旧判断使用本地/分布式两类最新证据。
- 飞书日报具备完成屏障与幂等键，同一日期不会重复发送成功摘要。

## 三、实施任务

### Task 1：冻结基线与验收证据

**涉及文件**
- `docs/superpowers/plans/2026-08-06-production-pipeline-reliability.md`
- ECS `systemctl`、`journalctl`、`runs.csv`、情报状态 JSON（只读基线）

**步骤**
1. 保存改造前的服务状态、最近运行结果、内存/磁盘和积压计数。
2. 记录本地与 ECS 关键文件哈希，避免覆盖线上特有修复。
3. 将每项结论标注为“运行证据”“静态配置”或“尚待下一次定时运行验证”。

### Task 2：实现日度完成屏障

**涉及文件**
- `stock_analyze/workflow_notifications.py`
- `stock_analyze/cli.py`
- `tests/test_workflow_notifications.py`
- `tests/test_workflow_summary_systemd.py`

**步骤（TDD）**
1. 先增加失败测试：存在 `missing/running/failed` 时，`--require-complete` 不发送、不写 delivery ledger，并返回临时退出码 75。
2. 增加可配置等待时间和轮询间隔；四项均为 success 后才继续生成并发送摘要。
3. 保持 `--preview` 无副作用，便于 ECS 在线验收。
4. 验证旧的 weekly/monthly 通知行为不受影响。

### Task 3：重构日度 systemd 编排

**涉及文件**
- `deploy/systemd/stock-analyze-market-data.service`
- `deploy/systemd/stock-analyze-research.service`
- 四个 `stock-analyze-*-daily.service`
- 新增 `deploy/systemd/stock-analyze-daily-finalize.service`
- `deploy/systemd/stock-analyze-daily-summary.timer`
- `tests/test_prediction_systemd.py`
- `tests/test_workflow_summary_systemd.py`

**步骤（TDD）**
1. 先写 unit 静态测试，要求 market-data/research 不再 `Wants/After intelligence`。
2. 四个正式策略成功后触发幂等 finalizer；finalizer 等待四份 ledger 终态后发送日报并刷新 Dashboard。
3. 固定日报 timer 移至 21:30，作为失败/超时兜底，不替代成功完成事件。
4. 为重型 Python 进程设置低线程/低 arena 环境，降低小内存 ECS 的额外峰值。
5. 用 `systemd-analyze verify` 校验依赖图和 unit 语法。
6. 为研究源快照增加版本化累计 manifest；没有 manifest 的旧目录保持兼容合并，最近完整基线之后只读取必要快照。

### Task 4：拆分并错峰公告情报任务

**涉及文件**
- `deploy/systemd/stock-analyze-intelligence.service`
- 新增 `stock-analyze-intelligence-quality.service/.timer`
- `stock-analyze-intelligence*.timer/service`
- `stock_analyze/intelligence/artifact_backfill.py`
- `tests/test_intelligence_systemd.py`
- `tests/test_intelligence_artifact_backfill.py`

**步骤（TDD）**
1. 高频 intelligence service 移除全库 `intelligence-status`，只保留增量与轻量 semantic status。
2. 全库质量检查放到独立低频 timer；增量、对账、回填、语义和质量任务统一使用共享后台资源锁，设置各自等待上限、超时和内存上限。
3. reconcile 移到凌晨；artifact backfill 避开 17:45-21:30，并继续使用可恢复 state。
4. 在回填 runner 增加可测试的关键窗口 guard；deferred 返回 75，不触发失败告警。
5. 降低频繁状态扫描，保留下载/解析/语义各自的可观测快照。

### Task 5：修正 Dashboard 运维真值

**涉及文件**
- `stock_analyze/dashboard_workspace_api.py`
- `stock_analyze/dashboard_runtime.py`
- `tests/test_dashboard_workspace_api.py`
- `tests/test_dashboard_resource_api.py`

**步骤（TDD）**
1. 先加入本机 `artifact_backfill_state.json` 的测试数据，要求 API 返回阶段、状态、原因和更新时间。
2. 陈旧告警使用本机状态与分布式 lease 的较新时间，避免误报。
3. 增加 80%/88% 两级磁盘提示；显示最近正式策略成功日期和缺口，而不是将“服务 active”当作任务完成。
4. 合并 ECS 已存在的资源字段上限修复，部署完整源文件，消除 Dashboard 重启后加载残缺文件的风险。
5. 运行前端/API 测试并检查响应大小上限。

### Task 6：本地回归、ECS 发布与在线试跑

**本地门禁**
1. 定向单测：通知、systemd、artifact backfill、Dashboard API。
2. 相关测试集合和 Python 编译检查。
3. `systemd-analyze verify`（本机可用时）或 ECS 发布前离线 verify。
4. 对实际变更做 diff 审阅，确认未修改策略 overlay、交易数据和历史 ledger。

**发布步骤**
1. 仅同步本次代码、unit、测试/文档所需文件；不使用会覆盖交易状态的全目录同步。
2. ECS 备份待覆盖文件，安装 unit，`daemon-reload`，读取 timer/依赖关系确认生效。
3. 不强杀正在写库的任务；等待或让其可恢复退出后切换。

**在线验收**
1. `market-data` 的依赖树中不再出现 intelligence；在后台空闲后手工启动当日主链。
2. 行情、研究和四个正式策略形成当日成功 ledger；NAV/order/dashboard 产物日期一致。若数据源尚未发布或主机资源仍不足，必须原样报告失败证据，不以静态测试代替。
3. `notify-workflow-summary --require-complete --preview`：未完成时返回 75；完成时返回 0 且显示 4/4。
4. 关键窗口运行 artifact backfill 时快速 deferred；窗口外 canary 能继续推进 state。
5. intelligence 增量 canary 不再因全库质量检查拖到 25 分钟超时。
6. Dashboard API 可访问，返回本机回填状态、积压、两级磁盘风险和正确的主/后台分层。
7. 记录发布前后耗时、内存峰值、任务结果与仍需基础设施处理的事项。
8. 用真实源目录确认累计基线把读取范围从 20 个历史运行日收敛到 1 个最新快照，并记录首次旧路径与后续新路径的耗时/换页差异。

## 四、验收矩阵

| 目标 | 通过标准 | 证据 |
|---|---|---|
| 情报不阻断日度主链 | market-data/research 无 intelligence 依赖 | `systemctl cat/show` |
| 日度策略完整 | 两市场 x 两策略均有目标日 success | 四份 `runs.csv` + 产物日期 |
| 通知不抢跑 | 未完成返回 75，不写发送账本；完成返回 0 | CLI 测试 + ECS preview/ledger |
| 后台不抢资源 | 关键窗口回填为 deferred | service result + state JSON |
| 情报增量不再 25m 超时 | 增量 service 在限时内成功，质量检查独立 | journal + service duration |
| PDF 可恢复 | 失败保留 reason，下一轮仍可重试 | SQLite/state 摘要，不删失败行 |
| 研究快照不重复解压 | 有 manifest 时只加载最近累计基线及其后增量 | 单测 + ECS manifest + 运行资源对照 |
| Dashboard 反映真值 | API 含 local backfill、磁盘分级、正式任务新鲜度 | API 响应 + 前端加载 |
| 无交易污染 | 未改策略参数、未补写历史 ledger/交易 | diff + 日期审计 |

## 五、本次不做与后续容量建议

- 不以本次代码改造替代 ECS 扩容。若串行日度链仍接近物理内存上限，至少升级至 4 GiB；PDF 历史回填要明显提速则建议 8 GiB。
- 不自动生成缺失历史日期订单。只有保存了当日不可变行情/特征快照时，才允许单独设计审计型重放。
- 不在本次调整模型门槛、策略权重或持仓逻辑；先恢复可靠样本，再评估收益表现。
- 不恢复“每篇公告双模型重复跑”的旧 Candidate A/B 生产模式；DeepSeek/Codex/Claude 都继续遵守同一抽取契约与校验器。

## 六、实施与在线验收结果（2026-08-07）

1. ECS 真实日链形成 `4/4` 成功：QDII 两条分别约 17/19 秒，A 股两条分别约 140/150 秒；中间 `2/4` 时 completion gate 返回 75、模型未启动，`4/4` 后返回 0 并启动模型。
2. 模型迭代于 `23:56:44` 启动、`23:56:53` 完成；A 股与 QDII 的 3/5/10/20 日候选状态均更新到 2026-08-06，本次 stderr 无新增。
3. 旧研究源读取在 20 个累计运行日上造成服务 swap 峰值约 3.6 GiB；新 manifest 真实只读 canary 仅打开 `20260806` 一个目录的 21 个 Parquet，读取 553,765 行耗时 1.78 秒、最大 RSS 约 535 MiB、0 swap。
4. 后台 timer 恢复时，增量情报获得共享锁并在 48 秒内成功；artifact backfill 同时触发但立即延期，没有并发争抢。统一 timer/ledger 健康检查通过。
5. Dashboard 运维 API 冷请求约 1.85 秒，缓存后 2.8-8.8 毫秒，响应 15,285 bytes、无错误或截断；本地隧道页面/API 均返回 HTTP 200。
6. 验证门禁：ECS 后端相关测试 165 条通过，前端 201 条通过，生产构建通过，unit 哈希一致，`systemd-analyze verify` 通过（仅报告无关的云监控既存警告）。
7. 试跑后先扩展了 QDII 离线日期解析；次日自然运行发现 unit 参数仍未进入离线路径，完整修正与验证见第七节。为避免改写审计历史，既有空值不回填。
8. 最终资源核对发现 Linux root 保留块会令原算法把 `df` 的 83% 误报为 78.8%；改为按普通服务可用容量计算后，线上 API 报告 82.7% 并正确产生 warning。Dashboard 后端 108 条、运维/API 前端 31 条追加回归通过。

## 七、次日自然运行与补充修正（2026-08-08）

1. 2026-08-07 自然日链按新依赖自动完成：A 股两策略分别约 101/108 秒，QDII 两策略分别约 15/13 秒；四份 ledger 均为 success，finalizer 在四策略完成后启动模型迭代。2026-08-08 的四条周任务、QDII 研究和周摘要也全部 success。
2. 次日审计发现 QDII unit 未传 `--offline`，所以 2026-08-07/08 的既有 ledger `as_of` 仍为空；同时周末在线请求生成的 `fund_daily_*_20260808.csv` 不能代表交易日。补充修正为：四个 QDII 日/周 unit 强制使用共享离线快照，日期解析优先读取已完成的 `market_snapshot_YYYY-MM-DD.json`。真实 ECS 只读 canary 对 A 股与 QDII 均解析为 `2026-08-07`；既有审计行不回写，待下个自然任务写入新值。
3. 00:30 对账服务曾因直接解析异常 PDF 卡住，3 小时后 timeout；日志同时出现超大图片和损坏 PDF 警告。删除 reconcile 中重复的 parse 循环后，PDF 解析只由具备 120 秒单文档超时、延期和恢复能力的 artifact backfill 承担。
4. 修正版生产 reconcile canary 于 13:54:34 启动、14:08:37 完成，结果 success，服务主进程约 13 分 42 秒，内存峰值 693.4 MiB、swap 峰值 0。Dashboard stale-while-refresh 完成后于 14:09:42 正确显示 reconcile success、errors 为空。
5. 补充测试 51 条通过，ECS `py_compile`、`systemd-analyze verify` 和 unit 参数回读通过；verify 仅保留无关的 Alibaba CloudMonitor 既存警告。
6. 情报吞吐从 2026-08-06 的 67,367 个 PDF ready / 21,931 个 parsed 推进到 74,895 / 23,579；语义完成从 2,411 增至 2,414。磁盘已达 85.1%（可用约 5.6 GiB），仍是当前最明确的容量风险；回填器会在可用空间低于 5 GiB 时自动延期，但需要尽快清理或扩容，不能把该保护当作长期容量方案。
