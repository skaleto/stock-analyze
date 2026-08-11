# 本机情报产物 worker

这个 worker 将公告 PDF 下载或解析任务从 ECS 搬到 macOS 本机执行，降低
ECS 的 CPU、内存和 OCR 压力。完整实现契约和 Coding Plan 交接顺序见
`docs/superpowers/plans/2026-07-30-local-artifact-worker-harness.md`。

## 数据权威边界

ECS 上的
`/opt/stock-analyze/app/data/shared/intelligence/intelligence.sqlite3`
是权威数据库。本机不直接写 SQLite，也不挂载或同步数据库文件。

完整数据流固定为：

1. ECS `intelligence-artifact-job-export` 从权威库租约任务并生成任务目录。
2. 本机通过 SSH/rsync 拉取该目录。
3. 本机 `intelligence-artifact-job-run` 只读任务输入并写任务结果文件。
4. 结果目录通过 rsync 回传原 ECS 任务目录。
5. ECS `intelligence-artifact-job-import` 校验结果并写入权威数据库。

只有第 1、5 步可以接触 ECS 数据库。本机任务目录位于
`<LOCAL_ROOT>/.local-intelligence-artifact-worker/jobs/`。

本机 Coding Plan/runner 是**可信执行边界**。任务哈希、结果哈希、确定性
chunk/table ID 和源码指纹用于发现传输损坏、误改和版本漂移，不能证明一个
主动伪造结果的本机执行器是诚实的。不要把任务目录交给不受信任的第三方程序。

## 手动运行

默认 stage 是 `parse`，每个任务最多 10 个产物，本机并发数为 2：

```bash
./scripts/run-local-intelligence-artifact-worker.sh --once
```

可用参数：

- `--stage parse|download`：选择任务阶段，默认 `parse`。
- `--limit N`：单个任务包的最大产物数。
- `--workers N`：本机 job-run 并发数。
- `--max-jobs N`：单次调用最多处理的批数，默认 10。
- `--max-runtime-seconds N`：单次调用最长时间，默认 3600 秒。
- `--job-timeout-seconds N`：单个本机 job 的 watchdog，默认 1800 秒。
- `--remote USER@HOST`：SSH 目标，默认
  `root@120.55.188.242`。
- `--ssh-key PATH`：SSH 私钥路径，默认
  `~/.ssh/<ssh-key-file>`。
- `--local-root PATH`：本机仓库根目录，默认当前脚本所属仓库。
- `--once`：最多领取并处理一个任务；不传时持续处理，直到 ECS 无任务。
- `--allow-battery`：显式允许电池供电时运行。

默认租约为 14400 秒。单次调用同时受 10 批和 1 小时双重上限约束，单批
本机计算再受 30 分钟 watchdog 保护；任一批
返回 `partial` 时立即停止，不会在同一次调用中重新领取失败文档。可用
`SA_LOCAL_INTELLIGENCE_LEASE_SECONDS` 调整，也可用
`SA_LOCAL_INTELLIGENCE_WORKER_ID` 固定 worker ID。

服务端对 retryable 文档执行 1 小时退避；累计 3 次仍失败后进入本机 worker
隔离集合，后续 export 不再领取。`intelligence-artifact-job-status` 的
`quarantined_documents` 展示隔离数量。

worker 默认要求 `pmset` 显示 AC Power。电池供电时正常跳过并返回 0；
只有明确传入 `--allow-battery` 才会覆盖这一保护。

解析任务会在领取租约前确认本机 Python 解析依赖和 Tesseract
`chi_sim+eng` 语言包。macOS 可执行：

```bash
brew install tesseract tesseract-lang
```

## launchd

安装当前用户的 LaunchAgent：

```bash
./scripts/install-local-intelligence-artifact-worker-launchd.sh
```

安装脚本接受 `--stage`、`--limit`、`--workers`、`--remote`、
`--ssh-key`、`--local-root` 和 `--allow-battery`。生成的任务固定传入
`--once`，每 30 分钟尝试一次。它使用 `StartInterval=1800`，
`RunAtLoad=false`，不设置 `KeepAlive` 或日历持久化；本机睡眠期间无需补跑。

查看状态与日志：

```bash
launchctl print gui/"$UID"/com.stock-analyze.local-intelligence-artifact-worker
tail -n 100 .local-intelligence-artifact-worker/logs/launchd.stdout.log
tail -n 100 .local-intelligence-artifact-worker/logs/launchd.stderr.log
```

## 失败处理与安全

- 使用原子目录锁
  `.local-intelligence-artifact-worker/worker.lock`，同一仓库不会并发运行；
  崩溃遗留且 PID 已失效的锁会自动回收。
- 领取和导入复用 ECS 的
  `/run/stock-analyze-intelligence-reconcile.lock`；领取时避让正在运行的
  ECS 回填，导入时等待该锁，避免双方选择同一文档。
- 下载、本机运行、上传或导入任一步失败时，任务目录保留，不会清理证据。
- 本机 job 超过 watchdog 时返回 124，保留现场且不上传半成品。
- 成功 import 后删除大体积任务目录，仅保留
  `history/<job_id>.import.json` 收据。
- ECS import 后也删除远端 job 的 `inputs/outputs/tmp` 大载荷，只保留
  manifest、result、run report 和数据库审计。
- 本机 CLI 输出保存在任务目录的 `local-worker.log`，不会回显到终端。
- 远端 export/import 原始输出不会回显；终端只显示阶段、任务 ID 和退出码。
- 脚本不会读取或打印私钥内容，也不使用 `set -x`。凭据只以文件路径传给
  SSH。
- 所有本机路径、SSH key 路径和任务目录都按独立参数传递，支持空格。

失败后先查看保留目录与 `local-worker.log`。修复原因后等待退避窗口再运行；
ECS 租约、import fencing 和幂等写入负责最终一致性，不要手工编辑权威
数据库。
