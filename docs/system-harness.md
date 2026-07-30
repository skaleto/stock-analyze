# Stock Analyze Harness

更新日期：2026-07-30

这份文档是开发、运行、部署和排障的唯一执行入口。系统事实优先级为：ECS 当前 service/timer 与账本 > 当前代码和测试 > 本文档 > 历史计划和归档。

## 1. 快速入口

```bash
# 本地结构、关键后端、Dashboard 和 shell 检查
./scripts/system-audit.sh

# 本地检查 + ECS timers/账本/API/情报状态
export SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app
export SA_ECS_SSH_OPTS='-i $HOME/.ssh/<ssh-key-file>'
./scripts/system-audit.sh --remote

# Dashboard-only 发布：远端预镜像与本地 release input 必须分别审阅
./scripts/deploy-dashboard-workspaces-to-ecs.sh capture-preimage \
  > /tmp/dashboard-preimage.manifest
./scripts/deploy-dashboard-workspaces-to-ecs.sh validate-manifest \
  /tmp/dashboard-preimage.manifest
./scripts/deploy-dashboard-workspaces-to-ecs.sh capture-release-input \
  > /tmp/dashboard-release-input.manifest
./scripts/deploy-dashboard-workspaces-to-ecs.sh validate-release-input \
  /tmp/dashboard-release-input.manifest
# 将两份清单交给独立审阅；部署命令不会现场生成并自行接受 release input
export SA_DASHBOARD_PREIMAGE_MANIFEST=/tmp/dashboard-preimage.manifest
export SA_DASHBOARD_RELEASE_INPUT_MANIFEST=/tmp/dashboard-release-input.manifest
./scripts/deploy-dashboard-workspaces-to-ecs.sh deploy

# 在 ECS 使用现有飞书应用凭据发布系统总览，并回读验证
/opt/stock-analyze/venv/bin/python scripts/publish_system_doc_to_lark.py \
  --source docs/system-overview.md \
  --output data/competition/system-doc-archive.json
```

Dashboard 发布只同步固定后端/测试文件、`scripts/system-audit.sh`、两份系统
文档与 `reports/app`，只重启 `stock-analyze-dashboard.service`。外部审阅的
release-input 清单必须绑定当前 40 位 commit，且所有发布输入必须与该 commit
完全一致；dirty 或未跟踪文件会在连接 ECS 前被拒绝。

脚本在远端取得独占锁后核对预镜像 SHA 并创建回滚备份。同步、目标测试、HTTP
状态、250 KB 体积或 0.5 秒热响应门禁失败时，只恢复 Dashboard 白名单与静态
资源；恢复后重新核对整份预镜像、检查 service，并验证系统总览 API 和应用页。
结果写入 `rollback-result.txt` 与 `release-manifest.txt`，之后才释放锁。它不会
同步配置、清理运行时、安装 unit 或改动 timer。

## 2. 先确认范围

- 生产市场只能是 `a_share`、`cn_qdii_etf`。
- 正式策略是 `claude=稳健防守`、`codex=趋势进攻`。
- 直接港股/美股只允许存在于 `archive/direct-overseas/`。
- 模拟交易不连接券商，不允许出现真实下单凭据或接口。
- Canonical 路径是 `data/<market>/<agent>/`、`reports/<market>/<agent>/`、`configs/agents/<agent>_<market>.yaml`。

## 3. 修改前检查

```bash
git status --short
./scripts/system-audit.sh
python3 -m stock_analyze --help
```

保留用户已有 dirty worktree。不要 reset、checkout 或删除不属于本次工作的改动。策略改动必须同时保留版本 manifest、演化日志和可回滚 overlay。

## 4. 常用运行

```bash
# 指定市场和策略的每日模拟
python3 -m stock_analyze --market a_share --agent claude run-daily --offline
python3 -m stock_analyze --market cn_qdii_etf --agent codex run-daily --offline

# 周任务只做诊断和报告，不生成订单
python3 -m stock_analyze --market a_share --agent claude run-weekly --offline

# Dashboard
python3 -m stock_analyze competition-dashboard
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765

# 模型与情报
python3 -m stock_analyze --market a_share run-model-iteration --offline
python3 -m stock_analyze intelligence-status
python3 -m stock_analyze intelligence-evaluate --market a_share
python3 -m stock_analyze intelligence-artifact-job-status \
  --repo-root .

# 决策/风控/归因证据
curl -s 'http://127.0.0.1:8765/api/dashboard/governance.json?market=a_share&agent=codex'
```

`run-daily` 顺序固定为执行到期订单、更新净值、生成下一交易日目标。`run-weekly` 不下单。
候选模型由 `stock-analyze-model-iteration.service` 独立运行；即使候选预测缺失或
模型组合失败，四个正式账户仍会按 Active 模型或固定规则路径继续执行。

历史公告 PDF 下载和解析可以交给本机有界 worker，但 ECS SQLite 始终是唯一
权威库。本机不需要 OSS 或 LLM 凭据。worker 单次默认最多 10 批/1 小时，
单批最多 30 分钟，失败执行退避和隔离。执行与交接入口见
[local artifact worker harness](superpowers/plans/2026-07-30-local-artifact-worker-harness.md)。

## 5. 周度和月度人工动作

飞书周摘要到达后，在 Codex 中发送：

```text
运行 YYYY-MM-DD 周度复盘
```

飞书月摘要到达后发送：

```text
运行 YYYY-MM 月度策略演化
```

周度只复盘，不直接改 overlay。月度改动必须通过四策略版本 manifest、pair validation、历史 gate 和部署后线上检查。

## 6. 验收矩阵

| 变更类型 | 最低测试 | 线上证据 |
| --- | --- | --- |
| 策略/执行 | 对应 simulator、strategy、execution policy、回归测试 | `runs.csv` success + orders/trades/nav |
| 数据源/特征 | provider、storage、feature、time-availability 测试 | cache/Parquet 新鲜度 + source health |
| 模型 | labels、models、prediction、model iteration 测试 | 版本状态、门禁指标、预测覆盖率 |
| Dashboard | Python API 测试 + `npm test` + `npm run build` | `/api/health`、资源 API、页面加载 |
| systemd/通知 | unit 文本测试 + shell syntax | timer active、child ledger、飞书聚合/失败记录 |
| 文档/结构 | `tests.test_system_structure` | 远端无退役路径和 unit |

全量后端：

```bash
python3 -m unittest discover -s tests
```

前端：

```bash
cd frontend/dashboard
npm test
npm run build
```

## Dashboard Workspace Runtime Contract

- `dashboard_runtime.py` 只通过 `systemctl show` 读取固定 service/timer 白名单，不提供启停或重跑控制。
- Dashboard 响应缓存提供 15 秒热缓存；systemd bus 暂时不可用时，runtime reader 只保留最近一次成功的白名单快照。
- 前一日的 `Result=success` 不计作今日任务成功。
- PDF 回填以退出码 75 且 `Result=success` 结束，表示 reconcile 锁占用了 worker 槽位，页面显示为“已跳过”。
- 运行中心最多读取 20 行运行账本，不读取完整 journal；模型研究、数据与情报、运行中心三个接口的 UTF-8 JSON 都必须小于 250 KB。
- 单个资源读取失败只写入稳定的 `errors[].resource/reason`，其余有效阶段、矩阵、计划和历史继续展示；异常文本、文件路径与凭据不得进入响应。

## 7. ECS 检查

```bash
export SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app
export SA_ECS_SSH_OPTS='-i $HOME/.ssh/<ssh-key-file>'
./scripts/check-ecs-timers.sh
./scripts/system-audit.sh --remote
```

必须同时检查：

- 预期 timer 已启用且 active，退役 timer 不存在或 disabled。
- 子 service 最近结果不是 Failed。
- `stock-analyze-model-iteration.service` 的失败单独告警，不得造成四个正式 daily
  service 缺账；正式策略使用规则降级时，原因必须进入决策账本。
- 四个 `runs.csv` 有对应 cadence 的成功行。
- `/api/dashboard/summary.json`、`governance.json` 和带市场/策略参数的资源 API 可访问。
- 情报采集有水位线，失败不会无限重复发飞书。

## 8. 清理契约

`scripts/cleanup-retired-runtime.sh` 默认只预览；只有明确执行 `--apply` 才修改。它只能处理白名单中的港股/美股、旧根目录 agent 数据、退役 timer/script 和临时批处理目录。

禁止删除：

- `data/a_share/`
- `data/cn_qdii_etf/`
- `data/shared/cache/`、`data/shared/backtest_cache/`
- `data/research/models/`
- `data/model_iterations/`
- `data/shared/research_lineage.sqlite3`
- `data/shared/intelligence/`
- `data/competition/`

账本损失不能通过删除 `daily_nav.csv`、`trades.csv`、`positions.csv` 或 `state.json` 重置。

## 9. 故障处理

1. 先看 `/api/operations` 和飞书聚合摘要，确认失败 unit 与目标日期。
2. 用 `journalctl -u <unit> --since ...` 看第一次失败，不从重复提醒猜原因。
3. 检查上游 cache/研究快照是否存在，再决定是否重跑子任务。
4. 修复后只重跑最小必要链路，并验证 `runs.csv` 与产物。
5. 相同 unit 的失败提醒有六小时冷却；冷却不等于错误消失，完整记录在 `logs/PIPELINE_FAILURES.log`。

## 10. Dashboard 访问

```bash
ssh -N \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=6 \
  -i $HOME/.ssh/<ssh-key-file> \
  -L 18765:127.0.0.1:8765 \
  root@120.55.188.242
```

访问 `http://127.0.0.1:18765/app.html`。电脑休眠会中断 TCP，恢复后重新执行命令即可。

## 11. 完成定义

只有同时满足以下条件才能宣告完成：代码和文档已更新；本地回归通过；前端构建通过；ECS 部署版本已刷新；退役逻辑清理完成；线上 timer/账本/API 正常；正式账户在模型单元失败时仍可独立运行；用户能访问 Dashboard；重要系统文档已回读存档。
