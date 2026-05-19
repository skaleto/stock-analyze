# 双 Agent 竞赛运维手册

本手册面向运营者（人类）。Agent 自己的工作流见 `AGENTS.md`（Codex）与
`configs/agents/claude.yaml` 上方说明（Claude）。

只做模拟交易：不连券商、不真实下单、不构成投资建议。

## 模式概览

双 agent（默认 `claude`、`codex`）共享一份公平基线（`configs/competition.yaml`），
各自维护一份策略 overlay（`configs/agents/<agent>.yaml`），同步在同一信号日跑
同一基准、同一成本下的纸面策略；每月跑一次对比 review，把双方业绩与风格沉淀
为 `data/competition/` 下的产物。

公平字段（baseline-locked）：

- `competition_id`, `start_date`
- `initial_cash`、`accounts.*.cash`、`accounts.*.top_n`、`accounts.*.scope`、`accounts.*.benchmark`
- `schedule.execution`, `schedule.signal_day`
- `trading.*`

可变字段（overlay 自由）：

- `factors` 因子选择与权重
- `factor_processing` winsorize/zscore/行业中性化/覆盖度阈值
- `portfolio_controls` 行业上限、hold buffer、最长持有期
- `filters` 流动性、市值上下限、required_fields、fallback_require_fields

## 目录布局

```
configs/
  competition.yaml         # 公平基线（不可由 overlay 覆盖）
  agents/
    claude.yaml            # Claude 的策略 overlay
    codex.yaml             # Codex 的策略 overlay

data/
  shared/
    cache/                 # AkShare/Baostock 共享缓存
    data_health.json
  claude/                  # Claude 独占的运行状态
    state.json, daily_nav.csv, trades.csv, positions.csv,
    pending_orders.json, latest_signals.csv,
    performance_summary.json, runs.csv,
    configs/<hash>.json, factor_runs/, factor_diagnostics/
  codex/                   # Codex 独占的运行状态（结构同上）
  competition/
    competition_metadata.json
    monthly_reviews/<month>.json
    leaderboard.csv

reports/
  claude/                  # Claude 自己的 dashboard.html + dashboard_fragment.html + weekly_report.md
  codex/                   # 同上
  competition/             # 聚合 dashboard.html + monthly_review_<month>.md
```

## 一次性初始化

```bash
python3 -m stock_analyze competition-init
```

幂等。会：

1. 校验 `configs/competition.yaml` 与 `configs/agents/*.yaml` 存在。
2. 创建 `data/{shared,claude,codex,competition}/` 与 `reports/{claude,codex,competition}/`。
3. 给两侧分别跑 `simulator.initialize(merged_config, store)` 写 `state.json` 与 `pending_orders.json`。
4. 写 `data/competition/competition_metadata.json` 记录 `competition_id`、`start_date`、`baseline_hash`。

若 overlay 试图覆盖 baseline-locked 字段，命令立即失败并打印 `competition_baseline_locked:<field>`。

## 周/日常运行

```bash
# Claude 侧
python3 -m stock_analyze --agent claude run-weekly
python3 -m stock_analyze --agent claude run-daily

# Codex 侧（互不干扰）
python3 -m stock_analyze --agent codex run-weekly
python3 -m stock_analyze --agent codex run-daily

# 任一侧单独刷新 dashboard
python3 -m stock_analyze --agent claude dashboard
```

`--agent <id>` 推导出：

- `--config configs/agents/<id>.yaml`（经 `competition.load` 合并 baseline）
- `--data-dir data/<id>`
- `--reports-dir reports/<id>`
- AkShare cache 指向 `data/shared/cache`
- `data_health.json` 写到 `data/shared/`

不带 `--agent` 时仍走老路径（`configs/strategy_v1.yaml` + `data/` + `reports/`），与单 agent 模式完全兼容。

## 月度对比

```bash
# 默认跑上一个自然月
python3 -m stock_analyze competition-monthly-review

# 指定月份
python3 -m stock_analyze competition-monthly-review --month 2026-05

# 只回顾子集 agent
python3 -m stock_analyze competition-monthly-review --month 2026-05 --agents claude codex
```

产物：

- `data/competition/monthly_reviews/<month>.json` — 机器可读，agent 用它做下个月决策的输入。
- `reports/competition/monthly_review_<month>.md` — 人类可读，含双方指标横向对比表、共同/分歧驱动因子、自动生成的差异化建议（不构成投资建议）。
- `data/competition/leaderboard.csv` — 每月一行，按累计收益和信息比率分别记录胜方；同月再跑会 upsert。

## 聚合 Dashboard

```bash
python3 -m stock_analyze competition-dashboard
```

输出 `reports/competition/dashboard.html`，三 tab：

- `Claude` — 嵌入 `reports/claude/dashboard_fragment.html`
- `Codex`  — 嵌入 `reports/codex/dashboard_fragment.html`
- `对比`   — 4 张顶部卡片、双线 NAV 曲线、9 行关键指标横向对比表、最近一期持仓重叠条、滚动战绩条、月度报告链接

本地查看：

```bash
python3 -m stock_analyze --reports-dir reports/competition serve-dashboard --host 127.0.0.1 --port 8765
# 然后浏览器打开 http://127.0.0.1:8765/dashboard.html
```

服务器场景建议通过 SSH 隧道：

```bash
ssh -L 8765:127.0.0.1:8765 user@your-server
```

## systemd 部署

模板放在 `deploy/systemd/`：

- `stock-analyze-claude-daily.{service,timer}`（周一到周五 16:30）
- `stock-analyze-claude-weekly.{service,timer}`（周五 17:00）
- `stock-analyze-codex-daily.{service,timer}`（周一到周五 16:35，错峰 5 分钟）
- `stock-analyze-codex-weekly.{service,timer}`（周五 17:05）
- `stock-analyze-monthly-review.{service,timer}`（每月 1 号 09:00）
- `stock-analyze-dashboard.service`（常驻 127.0.0.1:8765，指向 `reports/competition`）

安装：

```bash
sudo cp deploy/systemd/stock-analyze-*.service /etc/systemd/system/
sudo cp deploy/systemd/stock-analyze-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-analyze-dashboard.service
sudo systemctl enable --now stock-analyze-claude-daily.timer
sudo systemctl enable --now stock-analyze-claude-weekly.timer
sudo systemctl enable --now stock-analyze-codex-daily.timer
sudo systemctl enable --now stock-analyze-codex-weekly.timer
sudo systemctl enable --now stock-analyze-monthly-review.timer
```

模板里假设代码部署到 `/opt/stock-analyze/app/`，虚拟环境在 `/opt/stock-analyze/venv/`，日志写到 `/opt/stock-analyze/logs/`。按需调整。

## 故障排查

### 启动报 `competition_baseline_locked:<field>`

overlay 里出现了不允许覆盖的字段。打开 `configs/agents/<agent>.yaml` 把对应字段删掉。允许的 overlay 顶层键只有：`agent_id`、`strategy_id`、`name`、`factors`、`factor_processing`、`portfolio_controls`、`filters`。

### Claude / Codex 哪边没跑

```bash
ls -lt data/claude data/codex
tail logs/claude-weekly.log logs/codex-weekly.log
column -ts, data/claude/runs.csv | tail
column -ts, data/codex/runs.csv | tail
```

### 月度对比报告空数据

`compute_review` 读以下文件，缺一就把对应字段置 `null`：

- `data/<agent>/performance_summary.json`
- `data/<agent>/daily_nav.csv`
- `data/<agent>/positions.csv`
- `data/<agent>/factor_diagnostics/forward_ic.csv`

确保两侧都跑过至少一次 `run-weekly` 与一次 `run-daily` 再做月度 review。

### Dashboard 三 tab 但 Codex tab 空

`reports/codex/dashboard_fragment.html` 不存在。先：

```bash
python3 -m stock_analyze --agent codex run-weekly
# 或 (若不需要重新选股)
python3 -m stock_analyze --agent codex dashboard
```

`dashboard` / `run-daily` / `run-weekly` 命令现在会同时写 `dashboard.html` 与 `dashboard_fragment.html`。

### 共享缓存写竞争

两侧 systemd timer 已错峰 5 分钟。如果还是冲突（极少见），把双方的 daily/weekly 拉到不同小时即可。共享缓存只在写入瞬间冲突，写完都是原子覆盖。

### Codex 越权访问 Claude 目录

`AGENTS.md` 明确告诉 Codex 不能动 Claude 目录。如果发现 Codex 仍在跨界：

```bash
git diff data/claude/
git diff configs/agents/claude.yaml
```

把异常 diff 退回，并在 issue / 注释里记录这次违规。MVP 阶段我们不做 OS 级权限隔离。

## 本地分析工作流（ECS 跑数据，本地 agent 分析）

适用于没有 LLM API key、agent 分析在本地用 Claude Code / Codex CLI 完成的场景。
ECS 只跑选股 + 出 briefing；本地 sync 后让 agent 读 briefing、写笔记、生成提案；再 sync 回 ECS 让 dashboard 显示。

```text
ECS 端（systemd 自动）
  └ run-weekly --agent <id>
      └ 自动 build_weekly_briefing → data/<id>/notes/briefings/<date>-weekly.md
  └ competition-monthly-review
      └ 对每个 agent 自动 build_monthly_briefing → data/<id>/notes/briefings/<month>-monthly.md

本地（人 + agent CLI）
  └ scripts/sync-from-ecs.sh       拉数据/配置/报告
  └ Claude Code: /weekly-review claude       agent 读 briefing → 写 data/claude/notes/<date>-weekly-review.md
  └ Codex CLI: "do weekly review for codex"  agent 读 briefing → 写 data/codex/notes/<date>-weekly-review.md
  └ （月底再各跑一次 /monthly-strategy <id>，产 proposal JSON）
  └ scripts/sync-to-ecs.sh         推 notes / proposals 回 ECS

ECS 端
  └ competition-dashboard          下次刷新时 notes 出现在 dashboard
```

### 一次性环境变量

```bash
export SA_ECS_REMOTE=user@your-ecs-host:/opt/stock-analyze/app
# 可选：覆盖本地仓库路径，默认 $(pwd)
# export SA_ECS_LOCAL_REPO=$HOME/code/stock-analyze
```

### 周度本地分析步骤

```bash
# 1. 拉最新数据（含 ECS 自动生成的 briefing）
./scripts/sync-from-ecs.sh --exclude-cache

# 2a. 在 Claude Code 中打开仓库，输入：
/weekly-review claude

# 2b. 同时在 Codex CLI 中（也在该仓库）输入：
do weekly review for codex
# Codex 会按 AGENTS.md §5b 的流程跑

# 3. 笔记落地（agent 写到 data/<agent>/notes/<date>-weekly-review.md）

# 4. 推回 ECS
./scripts/sync-to-ecs.sh
```

### 月度本地分析步骤

```bash
# 0. 先在 ECS 上确认月度对比已经跑过（或本地跑一次也行）：
#    python3 -m stock_analyze competition-monthly-review --month 2026-05

./scripts/sync-from-ecs.sh

# 1a. Claude Code:
/monthly-strategy claude 2026-05

# 1b. Codex CLI:
do monthly strategy for codex for 2026-05

# 2. 检查产物
ls data/claude/notes/2026-05-monthly-review.md
ls data/claude/proposals/2026-05-strategy.json
ls data/codex/notes/2026-05-monthly-review.md
ls data/codex/proposals/2026-05-strategy.json

# 3. 你自己 review proposal JSON（rationale + risks + patch）。决定 approve 哪些，
#    手动编辑 configs/agents/<agent>.yaml（Phase 2 之前都是人工合入）。

# 4. 推 notes 回 ECS
./scripts/sync-to-ecs.sh
```

### Briefing 五段结构（agent 看到的就是这个）

| 段 | 作用 |
| --- | --- |
| `# 角色` | 你是谁、目录边界、不可改清单 |
| `# 数据快照` | 本周/本月的 runs / NAV / 信号 / 交易 / 持仓 / 待执行 / 覆盖率 / IC，markdown 表格形式 |
| `# 任务` | 明确说"做 X，不做 Y"（周度不改 config；月度可提案） |
| `# 输出契约` | 写到哪个路径、什么格式、JSON schema（月度） |
| `# 可选参考` | 历史笔记/提案的路径，agent 可选择性读入 |

### 关键边界

- 周度 agent **只写笔记**，不改 config。
- 月度 agent 写 markdown 笔记 + JSON proposal；proposal 不会自动应用，由你审核后手动合入 `configs/agents/<agent>.yaml`。
- agent 不能跨写对方目录（CLAUDE.md / AGENTS.md 强约束）；月度只能通过 `data/competition/monthly_reviews/<month>.json` 看到对手公开数据。
- 不要把 LLM API key 放到这个仓库里——本工作流不需要。

### 手动重新生成 briefing

```bash
python3 -m stock_analyze agent-prepare-weekly --agent claude
python3 -m stock_analyze agent-prepare-monthly --agent codex --month 2026-05
```

正常 ECS workflow 已经自动产 briefing；这两条命令只是数据状态变化后手动刷新用。

## 推荐工作节奏

| 频率 | 命令 | 谁触发 |
| --- | --- | --- |
| 每个交易日 16:30 / 16:35 | `--agent claude/codex run-daily` | ECS systemd timer |
| 每周五 17:00 / 17:05 | `--agent claude/codex run-weekly`（自带 briefing） | ECS systemd timer |
| 周五晚 | sync-from-ecs → `/weekly-review claude` + 同步 codex → sync-to-ecs | 本地人工 + agent |
| 每月 1 号 09:00 | `competition-monthly-review` + `competition-dashboard` | ECS systemd timer |
| 每月 1-2 号 | sync-from-ecs → `/monthly-strategy claude` + 同步 codex → 人工审 → 手动合入 → sync-to-ecs | 本地人工 + agent |
| 任意时刻 | `competition-dashboard` | 人或 timer |

## 风险与边界

- 仅模拟。任何输出都不构成投资建议。
- 公开数据接口可能失败/限流；`data/shared/data_health.json` 会记录降级路径。
- 两侧 overlay 同质化（`daily_return_correlation > 0.85`）时对比意义下降，月度报告会自动给出提醒。
- 学习模式（agent 自动生成 config patch 并应用）暂未启用；下一个 OpenSpec change `enable-monthly-config-evolution` 跟踪。
