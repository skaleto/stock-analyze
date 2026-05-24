# 双 Agent 竞赛运维手册

> 更概括的系统视角见 [docs/system-overview.md](system-overview.md)。本手册聚焦运维细节。

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
    cache/                 # Tushare/Baostock 共享缓存
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
  claude/                  # Claude 自己的 dashboard.html + weekly_report.md
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
- Tushare/Baostock cache 指向 `data/shared/cache`
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

## 月度策略演化（LLM 全权代理）

> Human operator 2026-05-23 明确授权: "所有优化的内容全部由 LLM 全权代理执行,
> 不需要审核,只要让我看到修改了什么。"
> 由 OpenSpec change `enable-llm-direct-strategy-evolution` 实施。

月度流程现在是 **LLM 直接改 yaml + 守卫只校验锁字段** 一步走，不再有 referee
判 approved/rejected/needs_human。

```bash
# 守卫纯检查（LLM 改完 yaml 后必跑一次）
python3 -m stock_analyze validate-overlay --agent codex

# 应急回滚（人类操作员手动触发）
python3 -m stock_analyze agent-rollback --agent codex --to <config_hash>
```

详见 `docs/llm-evolution-flow.md`。核心步骤:

1. ECS 每月 1 号 09:00 跑 `competition-monthly-review`,产生 monthly_reviews + briefing。
2. 本地操作员 `./scripts/sync-from-ecs.sh` 拉数据。
3. 本地操作员触发 `/monthly-strategy claude`（或 `/monthly-strategy codex`）。
   LLM 在 slash command 内自主:
   - 读 briefing（含对手 overlay 摘要 + 历史改动）
   - 思考并直接改 `configs/agents/<agent>.yaml`
   - 调 `evolution_writer.write_evolution` 落 history backup + evolution_log + evolution_diff + config_evolution.csv
   - 跑 `validate-overlay` 通过
4. 操作员 `./scripts/sync-to-ecs.sh` 推回（ECS 端无 referee/apply 步骤，dashboard 自动刷新）。

守卫只看两件事:

- **schema 合法**: 顶层键 ⊂ `{agent_id, strategy_id, name, factors, factor_processing, portfolio_controls, filters}`; factor 名 ⊂ AVAILABLE_FACTORS; factor weight ∈ `[0, 1]`。
- **baseline 锁字段不被侵入**: `initial_cash` / `accounts.*` / `schedule.*` / `trading.*` 全部禁动。

策略好坏 LLM 自负。`agent-judge-proposals` / `agent-apply-approved-proposals` 已删除。

## 聚合 Dashboard

```bash
python3 -m stock_analyze competition-dashboard
```

一次产出两份视图(同一份 `data/*`,不同渲染层):

| 文件 | 视图 | 说明 |
|---|---|---|
| `reports/competition/dashboard.html` | 专业版,三 tab | `Claude` / `Codex` / `对比`,完整因子覆盖率 / 前向 IC / 运行账本 |
| `reports/competition/simple.html`    | 新手简化版    | 总资产 / 双 AI 成绩 / 净值曲线 / 持仓 Top10 / 持仓重叠 / 最近 5 笔成交 / 本月策略调整摘要 |
| `reports/competition/simple/claude.html` | Claude 单 agent 简化版 |   |
| `reports/competition/simple/codex.html`  | Codex 单 agent 简化版  |   |

`serve-dashboard` 路由:

```
GET /                    → reports/competition/simple.html   (默认新手)
GET /pro.html            → reports/competition/dashboard.html (专业版别名)
GET /simple/claude.html  → reports/competition/simple/claude.html
GET /simple/codex.html   → reports/competition/simple/codex.html
GET /competition/dashboard.html   (向后兼容,不变)
```

本地查看(默认新手视图):

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765/         → 新手简化版
# 浏览器打开 http://127.0.0.1:8765/pro.html → 专业版(原 dashboard.html)
```

服务器场景建议通过 SSH 隧道:

```bash
ssh -L 8765:127.0.0.1:8765 user@your-server
```

## systemd 部署

> ⚠️ **二选一**：仓库同时包含两套 systemd 单元——单 agent 老路径（`stock-analyze-{daily,weekly}.{service,timer}`，从 `configs/strategy_v1.yaml` 跑）和**双 agent 竞赛 pipeline 路径**（`stock-analyze-market-data.{service,timer}` + `stock-analyze-weekly-trigger.{service,timer}` + 4 个 agent service + `monthly-review.{service,timer}` + `dashboard.service`）。**只 enable 一套**。同时启用会重复拉行情、写两份不相关的 NAV，但不会 corruption。

### 双 agent pipeline 模式（推荐）

一条 pipeline timer 每天 17:25 拉一次共享数据，然后并行触发两 agent；周六单独一条 timer 触发 weekly。两个 agent **永远 `--offline`**——任何 cache miss 即 fail-fast，不偷打网络。

模板放在 `deploy/systemd/`：

- `stock-analyze-market-data.{service,timer}`（Mon-Fri 17:25 CST = `Mon..Fri *-*-* 09:25:00 UTC`）
  - ExecStart 跑 `prepare-market-data`，写 `data/shared/cache/*.csv` + `data/shared/market_snapshot_<date>.json`
  - ExecStartPost 通过 `systemctl start --no-block` 拉起两个 daily agent service
  - ExecStart 失败时 ExecStartPost **不执行**，agent 不会跑出脏数据
- `stock-analyze-weekly-trigger.{service,timer}`（Sat 10:00 CST = `Sat *-*-* 02:00:00 UTC`）
  - ExecStart=/bin/true（占位，**不再次拉数据**）
  - ExecStartPost 拉起两个 weekly agent service，它们读周五 17:25 写入的 cache
- `stock-analyze-{claude,codex}-{daily,weekly}.service`（**没有对应的 timer**，只能被上面两个 trigger 拉起）
  - `ExecStart` 末尾带 `--offline`
- `stock-analyze-monthly-review.{service,timer}`（每月 1 号 09:00，与本变更独立）
- `stock-analyze-dashboard.service`（常驻 127.0.0.1:8765）

安装：

```bash
sudo cp deploy/systemd/stock-analyze-*.service /etc/systemd/system/
sudo cp deploy/systemd/stock-analyze-*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 1) 若之前启用过单 agent 老 timer，先停掉避免冲突
sudo systemctl disable --now stock-analyze-daily.timer 2>/dev/null || true
sudo systemctl disable --now stock-analyze-weekly.timer 2>/dev/null || true

# 2) 若之前启用过老版 per-agent timer（已被本变更替换），全部停掉并清理
for unit in stock-analyze-claude-daily.timer stock-analyze-claude-weekly.timer \
            stock-analyze-codex-daily.timer  stock-analyze-codex-weekly.timer; do
  sudo systemctl disable --now "$unit" 2>/dev/null || true
  sudo rm -f "/etc/systemd/system/$unit"
done
sudo systemctl daemon-reload

# 3) 启用新 pipeline timer + dashboard + monthly-review
sudo systemctl enable --now stock-analyze-dashboard.service
sudo systemctl enable --now stock-analyze-market-data.timer
sudo systemctl enable --now stock-analyze-weekly-trigger.timer
sudo systemctl enable --now stock-analyze-monthly-review.timer

# 4) 验证 timer 列表只显示 market-data + weekly-trigger + monthly-review
systemctl list-timers stock-analyze-*
```

模板里假设代码部署到 `/opt/stock-analyze/app/`，虚拟环境在 `/opt/stock-analyze/venv/`，日志写到 `/opt/stock-analyze/logs/`。按需调整。

### 一周节拍

```
周一 17:25  market-data.service  → ExecStartPost → claude-daily + codex-daily 并行
周二 17:25  同上
周三 17:25  同上
周四 17:25  同上
周五 17:25  同上（仍是 daily，不再混合 weekly）
周六 10:00  weekly-trigger.service → ExecStartPost → claude-weekly + codex-weekly 并行（读周五 cache）
周日       无任务
```

### 故障路径速查

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `data/shared/runs.csv` 当天 status=failed | prepare-market-data 内部 fatal（spot 全失败 / 全部 benchmark 失败） | `journalctl -u stock-analyze-market-data.service`；修复后 `systemctl start stock-analyze-market-data.service` 手动重跑，ExecStartPost 会接力 |
| `data/claude/runs.csv` 当天 status=failed 且 error_summary 含 `cache_miss:` | prepare-market-data 没把该方法的 cache 写出来（partial 失败 / 接口超时） | 先看 market_snapshot.json 的 errors 段；缺哪个方法补哪个方法，要么 `--force` 重跑 prepare 要么改 overlay filter 把该股剔除 |
| 周六 weekly 全挂 CacheMiss | 周五 prepare-market-data 失败且未补 | `prepare-market-data --as-of <周五> --force`；再 `systemctl start stock-analyze-{claude,codex}-weekly.service` |
| `systemctl list-timers` 出现 `stock-analyze-{claude,codex}-{daily,weekly}.timer` | 老 timer 没清干净 | 重跑安装脚本第 2 步 |

### `configs/agents/_history/` 是什么

`evolution_writer.write_evolution` 每次直接改 yaml 之前会把当前 overlay 备份到 `configs/agents/_history/<config_hash>.yaml`（哈希内容是 sha256[:12]）。这些文件**进入 git**，作为审计轨迹。可以：

- 在任意 clone 上跑 `python3 -m stock_analyze agent-rollback --agent <id> --to <hash>` 回滚。
- 通过 `git log configs/agents/_history/` 查看历次演化时间线。
- 累计大约每月每 agent 1 个文件 (~1KB)；不需要清理。

不要手动编辑这些文件——`agent-rollback` 是唯一受支持的恢复路径。

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

`data/_dashboard_build/codex/fragment.html` 不存在。先：

```bash
python3 -m stock_analyze --agent codex run-weekly
# 或 (若不需要重新选股)
python3 -m stock_analyze --agent codex dashboard
```

`dashboard` / `run-daily` / `run-weekly` 命令现在会同时写用户可见的 `dashboard.html` 与内部聚合片段 `data/_dashboard_build/<agent>/fragment.html`。

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
ECS 只跑选股 + 出 briefing；本地 sync 后让 agent 读 briefing、写笔记或月度演化；再 sync 回 ECS 让 dashboard 显示。

```text
ECS 端（systemd 自动）
  └ run-weekly --agent <id>
      └ 自动 build_weekly_briefing → data/<id>/notes/briefings/<date>-weekly.md
  └ competition-monthly-review
      └ 对每个 agent 自动 build_monthly_briefing → data/<id>/notes/briefings/<month>-monthly.md
      └ 仅写月度 briefing 与 dashboard；不 judge/apply

本地（人 + agent CLI）
  └ scripts/sync-from-ecs.sh       拉数据/配置/报告
  └ Claude Code: /weekly-review claude       agent 读 briefing → 写 data/claude/notes/<date>-weekly-review.md
  └ Codex CLI: "do weekly review for codex"  agent 读 briefing → 写 data/codex/notes/<date>-weekly-review.md
  └ （月底再各跑一次 /monthly-strategy <id>，直接演化本 agent overlay）
  └ scripts/sync-to-ecs.sh         推 notes / evolution 产物 / overlay 回 ECS，并刷新 dashboard

ECS 端
  └ dashboard 显示 notes 与 evolution 时间线；新 overlay 下次交易周期生效
```

### 一次性环境变量

```bash
export SA_ECS_REMOTE=user@your-ecs-host:/opt/stock-analyze/app
# 可选：覆盖本地仓库路径，默认 $(pwd)
# export SA_ECS_LOCAL_REPO=$HOME/code/stock-analyze
# 可选：远端 dashboard 刷新使用的 ssh 参数
# export SA_ECS_SSH_OPTS="-i ~/.ssh/your_key"
# export SA_ECS_AFTER_SYNC=0  # 只同步，不触发远端 dashboard 刷新
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
ls data/claude/evolution_log/2026-05.md
ls data/claude/evolution_diff/2026-05.json
ls data/codex/notes/2026-05-monthly-review.md
ls data/codex/evolution_log/2026-05.md
ls data/codex/evolution_diff/2026-05.json
git diff configs/agents/  # 看 LLM 改了什么

# 3. 推回 ECS。无需 referee/apply 步骤：
./scripts/sync-to-ecs.sh
```

### Briefing 五段结构（agent 看到的就是这个）

| 段 | 作用 |
| --- | --- |
| `# 角色` | 你是谁、目录边界、不可改清单 |
| `# 数据快照` | 本周/本月的 runs / NAV / 信号 / 交易 / 持仓 / 待执行 / 覆盖率 / IC + 对手 overlay 快照 + 对手历史改动，markdown 表格形式 |
| `# 任务` | 明确说"做 X，不做 Y"（周度不改 config；月度直接改 yaml + 写 evolution_log） |
| `# 输出契约` | 写到哪个路径、什么格式 |
| `# 可选参考` | 历史笔记 / 演化记录的路径，agent 可选择性读入 |

### 关键边界

- 周度 agent **只写笔记**，不改 config。
- 月度 agent 直接改 `configs/agents/<agent>.yaml` 并写 `evolution_log/<month>.md`；守卫只校验锁字段，不评判策略好坏。
- agent 不能跨写对方目录（CLAUDE.md / AGENTS.md 强约束）；月度可以读对手 `configs/agents/<other>.yaml` 与 `data/<other>/config_evolution.csv`，但不能读对手 `evolution_log/*` 与 `notes/*`。
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
| Mon-Fri 17:25 CST | `prepare-market-data` → 两 agent `run-daily --offline` | ECS systemd timer |
| Sat 10:00 CST | `weekly-trigger` → 两 agent `run-weekly --offline`（自带 briefing） | ECS systemd timer |
| 周六 / 周末 | sync-from-ecs → `/weekly-review claude` + 同步 codex → sync-to-ecs | 本地人工 + agent |
| 每月 1 号 09:00 | `competition-monthly-review` + `competition-dashboard` | ECS systemd timer |
| 每月 1-2 号 | sync-from-ecs → `/monthly-strategy claude` + 同步 codex → sync-to-ecs | 本地人工 + agent |
| 任意时刻 | `competition-dashboard` | 人或 timer |

## 风险与边界

- 仅模拟。任何输出都不构成投资建议。
- 公开数据接口可能失败/限流；`data/shared/data_health.json` 会记录降级路径。
- 两侧 overlay 同质化（`daily_return_correlation > 0.85`）时对比意义下降，月度报告会自动给出提醒。
- 策略自动演进由本地 LLM 直接改本 agent overlay；守卫只校验 schema、锁字段、因子白名单、权重和方向合法性，策略好坏由月度表现与人工回滚机制约束。
