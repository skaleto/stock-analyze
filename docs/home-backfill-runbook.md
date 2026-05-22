# 家宽端市场数据回填手册

> **谁读这份文档**：在家庭宽带（电信/联通/移动 居民动态 IP，**不挂 VPN / 公司代理**）的 Mac 或 Linux 机器上的 Claude Code / Codex CLI agent。
>
> **为什么需要这份文档**：东方财富对**云数据中心 IP 段**（阿里云、字节内网等）做了反爬封禁，ECS 上 `push2.eastmoney.com`（实时行情子域）100% 不通。家宽居民 IP 不在封锁名单里，可以打通。本文档让 agent 知道如何在家宽机器上：(1) 拉取最新代码、(2) 跑 `prepare-market-data` 完成五月（或任意月份）数据回填、(3) 把 cache 同步到 ECS 供 daily/weekly agent 离线消费。

---

## 1. 前置条件

跑之前你（agent）必须确认这 5 件事都满足：

### 1.1 网络出口是大陆居民 IP

```bash
unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
curl -s --noproxy '*' https://api.ipify.org && echo
```

- ✅ **OK**：返回 `218.xxx.xxx.xxx` / `117.xxx.xxx.xxx` / `100.xxx.xxx.xxx` 之类的国内 ISP 居民动态 IP
- ❌ **NOT OK**：返回 `203.208.x.x` / `198.18.x.x` / `13.x.x.x` 等海外或云段 IP
  - 解决：关闭 ClashX / Surge / V2Ray / 公司 VPN；改用手机 4G/5G 热点
  - 如果你是字节内网机器，**这条路走不通**，需要切到家用电脑

### 1.2 push2.eastmoney.com 可达

```bash
unset HTTPS_PROXY HTTP_PROXY
curl -sS --noproxy '*' -o /tmp/em_test.json -w 'HTTP %{http_code} | size=%{size_download}B\n' \
  'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:1+t:2&fields=f1,f2,f3' \
  -H 'User-Agent: Mozilla/5.0' -H 'Referer: https://quote.eastmoney.com/' \
  --connect-timeout 5 --max-time 15
head -c 200 /tmp/em_test.json && echo
```

- ✅ **OK**：返回类似 `{"rc":0,"rt":4,"svr":..., "data":{"total":..., "diff":[...]}}` 的 JSON
- ❌ **NOT OK**：`HTTP 000 | size=0B` 或 `Empty reply from server` → 这台机器/网络仍在封锁名单上，**不要跑**

### 1.3 Python 3.11+ 与 venv 准备

```bash
python3 --version  # 应 ≥ 3.11
test -d .venv || python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # akshare, pandas, requests, baostock 等
```

### 1.4 仓库代码是 main 最新

```bash
git fetch origin main
git checkout main
git pull --ff-only origin main
git log --oneline -1
```

最新 commit 应该包含 `prepare-market-data` 子命令（提交 `94ec757` 或更新）。

### 1.5 SSH 到 ECS 通

后面需要把 cache 推回 ECS：

```bash
ssh -i ~/.ssh/ai_baby_aliyun -o ConnectTimeout=5 root@120.55.188.242 'echo connected; hostname'
```

应输出 `connected` + ECS 主机名。

---

## 2. 跑回填

### 2.1 一键脚本（推荐）

仓库根目录提供 `scripts/home-backfill.sh`：

```bash
# 回填整个五月（默认 5/6 ~ 5/22 13 个交易日）
./scripts/home-backfill.sh --month 2026-05

# 只跑指定日期
./scripts/home-backfill.sh --dates 2026-05-19,2026-05-20,2026-05-21,2026-05-22

# 跑完不自动 rsync 到 ECS（用于先本地看一眼）
./scripts/home-backfill.sh --month 2026-05 --no-sync

# 强制重新拉（即便今日 snapshot.json 已存在）
./scripts/home-backfill.sh --month 2026-05 --force
```

脚本会：

1. 检查前置条件 1.1 / 1.2 / 1.3 / 1.5
2. 对每个交易日跑 `python3 -m stock_analyze --as-of <day> prepare-market-data [--force]`
3. 跑完后打印 snapshot.json 摘要（行数 / 错误数 / 状态 / 关键字段覆盖率）
4. 默认结束后 `rsync -av data/shared/cache/ data/shared/market_snapshot_*.json` 到 ECS

### 2.2 手动单日（如脚本有问题，需要排错）

```bash
python3 -m stock_analyze --as-of 2026-05-22 prepare-market-data --force --max-workers 5
```

跑完查看：

```bash
cat data/shared/market_snapshot_2026-05-22.json | python3 -m json.tool | head -30
```

应看到：

```json
{
  "as_of": "2026-05-22",
  "started_at": "2026-05-22T...",
  "status": "success",   // success / partial / failed
  "candidates_fetched": 250,
  "rows": {
    "spot": 5400,          // 5000+ 表示 spot 走通了
    "constituents_000300": 300,
    "constituents_000905": 500,
    "price_history": 250,
    "valuation": 250,
    "financial": 248,
    "dividend": 247,
    "benchmark_000300": 1,
    "benchmark_000905": 1
  },
  "errors": [...]
}
```

---

## 3. 验证字段覆盖率（关键）

家宽跑出来与 ECS fallback 跑出来的**核心差异**就是字段完整度。验证：

```bash
python3 <<'PY'
import pandas as pd
from pathlib import Path

date = "2026-05-22"
df = pd.read_csv(f"data/shared/cache/spot_{date.replace('-','')}.csv")
print(f"spot 行数: {len(df)}")
print(f"pe 非空率: {df['pe'].notna().mean()*100:.1f}%")
print(f"pb 非空率: {df['pb'].notna().mean()*100:.1f}%")
print(f"market_cap_yi 非空率: {df['market_cap_yi'].notna().mean()*100:.1f}%")
PY
```

**家宽跑出来的应该是**：

| 指标 | 家宽（push2 通） | ECS fallback（push2 死） |
| --- | --- | --- |
| spot 行数 | 5400+ | 5400+ |
| pe 非空率 | **80%+** | 0% |
| pb 非空率 | **80%+** | 0% |
| market_cap_yi 非空率 | **95%+** | 0% |

如果你的家宽跑出来 pe/pb/market_cap_yi 仍然 0%，说明 push2 也被你这台机器封了——回到 §1.1/1.2 重新查网络。

---

## 4. 把 cache 推到 ECS

> 如果你用了 §2.1 的 `home-backfill.sh`（默认带 sync），可跳过本节。

回填完成后，agent 自己生成的所有数据都在 `data/shared/cache/` 和 `data/shared/market_snapshot_<date>.json`。推到 ECS：

```bash
rsync -av --delete \
  -e "ssh -i ~/.ssh/ai_baby_aliyun" \
  data/shared/cache/ \
  root@120.55.188.242:/opt/stock-analyze/app/data/shared/cache/

rsync -av \
  -e "ssh -i ~/.ssh/ai_baby_aliyun" \
  data/shared/market_snapshot_*.json \
  root@120.55.188.242:/opt/stock-analyze/app/data/shared/
```

> ⚠️ **不要** rsync `--delete` 时把整个 `data/shared/` 一锅端，因为 ECS 上还有 `runs.csv`、`data_health.json` 等 ECS 自己的状态。**只 rsync `cache/` 子目录和 `market_snapshot_*.json` 文件**。

---

## 5. ECS 端确认

```bash
ssh -i ~/.ssh/ai_baby_aliyun root@120.55.188.242 \
  "ls /opt/stock-analyze/app/data/shared/cache/spot_*.csv | tail -15"
```

应该看到 5/6 - 5/22 的所有交易日 spot 文件。

ECS 上下一次 daily/weekly agent 跑（17:25 systemd timer 触发 → `--offline` 启动）会自动读这些 cache，**不打外网**。

---

## 6. 故障排查

### 6.1 单日 `partial` 状态

`prepare-market-data` 完成但 `status=partial` 表示部分股票某些接口失败。可接受（错误聚合到 `snapshot.errors`，整体继续）。常见原因：

- 单只股票数据源临时 503（重跑即可）
- akshare 的 `stock_a_indicator_lg`（dividend）对新上市股没数据 → 正常

### 6.2 单日 `failed` 状态

`status=failed` 表示 critical 失败（spot 全失败 / 全部 benchmark 失败）。检查：

```bash
cat data/shared/market_snapshot_<date>.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['fetch_summary'])"
```

`fatal` 列表里有 `spot` → push2 子域失败 → 回到 §1
`fatal` 列表里有 `all_benchmarks` → push2 + push2his 都失败 → 同上

### 6.3 鉴权 / 限流

家宽连续高频跑可能短暂触发限流。建议：

- 每日 prepare 之间间隔 30 秒以上
- `--max-workers` 不要超过 5
- 跑全月 13 天大约 2-3 小时

### 6.4 卡死 / 单股拉很久

某只股票 baostock 慢登录，单股 20-30 秒。脚本超过 15 分钟没新进度可以 Ctrl-C 杀掉重跑（`--force` 跳过已写的 cache）。

---

## 7. agent 行为约束（重要）

跑这个回填的 agent 还是要遵守 [CLAUDE.md](../CLAUDE.md) / [AGENTS.md](../AGENTS.md) 的边界：

- **不要** 改 `stock_analyze/*.py`、`tests/*.py`、`configs/competition.yaml`、`configs/agents/codex.yaml`
- **不要** 读写 `data/codex/`（如果你是 claude 视角）/ `data/claude/`（如果你是 codex 视角）
- **可以** 读写 `data/shared/cache/` 和 `data/shared/market_snapshot_*.json`——这些是共享数据，两个 agent 都需要
- **可以** 修改你自己的 `data/<agent>/notes/`、`data/<agent>/proposals/`

回填操作本身**完全在 `data/shared/` 范围内**，不踩 agent 私有目录。

---

## 8. 验证清单（agent 跑完后自查）

```
[ ] §1 五个前置条件全部 ✅
[ ] §2 回填脚本退出码 0
[ ] §3 字段覆盖率 pe / market_cap_yi ≥ 80%
[ ] §4 rsync 完成无 error
[ ] §5 ECS 上 ls /opt/stock-analyze/app/data/shared/cache/ 看到新文件
[ ] data/shared/market_snapshot_<date>.json 每天都有，status ∈ {success, partial}
[ ] 没改任何 stock_analyze/*.py 或 configs/
```

全部 ✅ 之后可以告诉人类操作员"五月回填完成"。
