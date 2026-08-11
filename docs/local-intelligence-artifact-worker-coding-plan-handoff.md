# 本机历史公告 Worker Coding Plan 交接

日期：2026-07-30  
用途：交给另一个 Coding Plan 执行历史 PDF 下载与确定性解析  
状态：harness 已实现、已部署、已用真实数据完成最小 canary

## 1. 你要做什么

你只负责运行已经实现的本机 worker，逐批消化历史公告的 PDF 下载和解析积压。
这不是语义理解任务，不需要阅读公告、改 prompt、调用 LLM 或自行编写另一套
下载/解析脚本。

使用的唯一入口：

```bash
./scripts/run-local-intelligence-artifact-worker.sh
```

权威数据始终在 ECS：

```text
/opt/stock-analyze/app/data/shared/intelligence/intelligence.sqlite3
```

本机不能复制、覆盖或直接修改该 SQLite，也不能获得 OSS、Tushare、
DeepSeek 或飞书凭据。ECS 负责租约、校验、OSS 和数据库写入。

## 2. Canonical 位置

本机源码工作树：

```text
$HOME/.config/superpowers/worktrees/New project/market-intelligence
```

交接归档：

```text
$HOME/Documents/New project/archive/
  local-intelligence-artifact-worker-harness-2026-07-30.tar.gz
```

先校验归档 SHA-256，再以归档内的 `SOURCE_FILES.txt` 和
`MANIFEST.sha256` 为准。不要从聊天记录重写逻辑。

## 3. 已验证的生产基线

- ECS schema 为 V14。
- 线上 bucket 仅使用同区域内网 bucket `stock-analyze-hz`。
- Dashboard HTTP 与权威 SQLite 均能读取 worker 统计。
- 解析 canary `awj-007f935d59584c3f9a7e1dd50b12a823` 已成功导入。
- 下载 canary `awj-e771206eb1b645438da0cad1828b63aa` 已成功导入。
- 较老 CNInfo 链接可能在本机和 ECS 同时超时，因此下载选择器已改为
  `historical-download-recent-first-v1`。
- 解析选择器为 `historical-parse-ready-first-v1`。
- ECS 原回填 timer 已恢复；本机 worker 通过统一锁和租约与它互相避让。
- 本机 LaunchAgent 未安装、未启用。除非操作者以后明确要求，不要安装。

## 4. 首次预检

在 canonical 工作树执行：

```bash
cd "$HOME/.config/superpowers/worktrees/New project/market-intelligence"

python3 -m unittest \
  tests.test_intelligence_artifact_exchange \
  tests.test_local_intelligence_artifact_worker_scripts

python3 -c \
  'import fitz, pdfplumber, pypdf, pytesseract, yaml, httpx'

for language in chi_sim eng; do
  tesseract --list-langs | grep -Fx "$language"
done

ssh -i ~/.ssh/<ssh-key-file> root@120.55.188.242 \
  'cd /opt/stock-analyze/app &&
   /opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
   intelligence-artifact-job-status \
   --repo-root /opt/stock-analyze/app'
```

继续执行的条件：

- 测试通过。
- PDF Python 依赖均可导入。
- Tesseract 同时存在 `chi_sim` 和 `eng`。
- ECS status 返回 JSON，且没有无法解释的长期 active lease。
- 本机接通电源；不要默认使用 `--allow-battery`。
- 本机可用磁盘不少于 20 GiB。

## 5. 执行策略

先消费已经下载完成的解析积压。一次调用最多 4 批、每批 10 篇、2 个解析
进程、总时长最多 30 分钟：

```bash
./scripts/run-local-intelligence-artifact-worker.sh \
  --stage parse \
  --limit 10 \
  --workers 2 \
  --max-jobs 4 \
  --max-runtime-seconds 1800 \
  --job-timeout-seconds 1500
```

解析队列明显下降后，再交替执行下载。下载仍使用小批次，避免一批被失效链接
拖住：

```bash
./scripts/run-local-intelligence-artifact-worker.sh \
  --stage download \
  --limit 10 \
  --workers 4 \
  --max-jobs 4 \
  --max-runtime-seconds 1800 \
  --job-timeout-seconds 900
```

不要把 `--limit` 提高到 50 以上，不要把解析 `--workers` 提高到 8 以上，
也不要并发启动两个 runner。shell 自带本机互斥，但执行者仍应保持单实例。

## 6. 每轮必须记录

每次运行前后都读取：

```bash
ssh -i ~/.ssh/<ssh-key-file> root@120.55.188.242 \
  'cd /opt/stock-analyze/app &&
   /opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
   intelligence-artifact-job-status \
   --repo-root /opt/stock-analyze/app'

ssh -i ~/.ssh/<ssh-key-file> root@120.55.188.242 \
  'curl -fsS \
   "http://127.0.0.1:8765/api/dashboard/intelligence.json?market=cn_qdii_etf&agent=codex"' \
  | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["pipeline"]["artifactWorkers"])'
```

记录以下字段：

- `jobs`：各 stage/status 的 job 数。
- `active_leases`：本轮结束后应回到 0；短时导入中的 lease 除外。
- Dashboard `artifactWorkers.downloadedDocuments/parsedDocuments`：累计下载、
  解析成功数。
- `quarantined_documents`：达到重试上限的异常文档数。
- 本轮 shell 退出码。
- `.local-intelligence-artifact-worker/history/` 中新增的 import 收据。

成功导入的本机大文件会自动删除。失败现场保留在
`.local-intelligence-artifact-worker/jobs/<job_id>/`，不要直接删掉。

## 7. 暂停与恢复

出现任一情况就停止当前轮，不要继续扩批：

- shell 退出码为 3：出现 partial，先保留现场并等待服务端退避。
- shell 退出码为 124：单个任务 watchdog 超时。
- 依赖、SSH、rsync、哈希、manifest 或 import 校验失败。
- ECS 可用磁盘低于 8 GiB。
- 同一错误连续出现 3 批。
- active lease 超过租约期仍不释放。

恢复时先查看保留 job 的 `local-worker.log` 和 ECS status。不要手工修改
任务 JSON、结果 JSONL 或数据库。租约过期、幂等 import、1 小时退避和累计
3 次隔离由 harness 处理。

## 8. 完成标准

一个执行阶段只有在以下条件同时满足时才算完成：

- 对应历史 backlog 为 0，或操作者指定的本轮预算已耗尽。
- 本机没有仍在运行的 worker。
- ECS 没有过期未收敛的 active lease。
- 最后一批 import 收据存在。
- Dashboard 与 CLI status 的累计数字一致。
- 失败和隔离文档有数量及稳定错误码汇总，没有被静默丢弃。

这套 worker 的输出只是可追溯的 PDF、正文、表格、chunk 和解析 provenance。
后续语义抽取、因子生成和模型评估仍由 ECS 上的独立主线完成；不得从原始正文
直接触发纸面交易。
