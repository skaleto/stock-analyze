# 验证港股/美股数据源连通性（yfinance vs akshare）

> 目的：在你打算用来「每周本地拉港美股数据」的那台机器上，**实测**它能不能拉到
> yfinance（Yahoo）和 akshare（东方财富）的数据，从而决定 HK/US 用哪个源。
> 尤其要在**你家里那条国内家宽**上跑一次。

## TL;DR — 怎么跑

在目标机器上（已 clone 本仓库）：

```bash
python3 -m pip install --quiet yfinance akshare
python3 scripts/verify_data_sources.py
```

脚本纯读取、无副作用，跑约 20 秒，最后给一个【结论】。**把整段输出发回来**即可。

## 为什么要专门验证

两个免费源走的是**不同的站、不同的网络可达性**：

| 源 | 实际拉的站 | 谁容易连不上 |
|---|---|---|
| **yfinance** | Yahoo Finance（**境外**） | 机房/云 IP 被 Yahoo 限流(429)；境内网络可能慢/被墙 |
| **akshare** | 东方财富 / 新浪（**国内**） | 境外/VPN/云 连不上 `push2.eastmoney.com`；并发会被封 |

所以"能不能用"**强依赖这台机器在哪、是什么 IP**，必须实测，不能拍脑袋。

## 我们目前已知（供对照）

| 机器 / 网络 | yfinance(Yahoo) | akshare(东财) |
|---|---|---|
| 当前 dev 机（能连 Yahoo，疑似境外/VPN） | ✅ 美股稳；港股偶发 TLS/限流 | ❌ 连不上 `push2.eastmoney.com` |
| ECS（阿里云机房 IP） | ❌ 硬限流(429)，封数小时 | ?（东财在国内机房可能可达，未净测） |
| **你家国内家宽** | **? 待你测** | **? 待你测**（理论上东财应很顺） |

→ 关键就是补上最后一行：**你那条国内家宽上，哪个源能通。**

## 结论怎么读

脚本对每个源输出三种之一：

- `[OK] 可用` —— 能连、能拉到数据、且数据新鲜（最近交易日）。这个源在这台机器上能用。
- `[FAIL] 限流/连不上` ——
  - yfinance 出现 `429 / Too Many Requests` = 这台 IP 被 Yahoo 限了（多见于机房 IP）。
  - akshare 出现 `Max retries / push2.eastmoney.com` = 这台连不上东方财富行情服务器（多见于境外/VPN）。
- `[WARN] 部分可用` —— 部分能拉（比如美股行、港股偶发失败）。可重跑一两次确认是否偶发。

末尾"这台机器适合用哪个源"会直接给建议。

## 拿到结果后做什么

把输出发回来，按这台机器的结果定：
- **yfinance 通** → 港美股都用 yfinance（代码已就绪），每周这台机器本地拉一次 → 上传 ECS。
- **akshare 通、yfinance 不通** → 港美股改用 akshare（东方财富），我写对应 provider。
- **两个都通** → 优先 yfinance（港美一体）。
- **两个都不通** → 换台机器/网络，或考虑付费 API（EODHD ~$83/月，ECS 直连免折腾）。

## 备注 / 排错

- 结果像偶发错误（TLS、超时），**重跑一两次**再判断。
- akshare 怕**并发**——脚本是串行的；你自己别用多线程猛拉东方财富，容易被封 IP。
- yfinance 怕**频繁/机房 IP**——别短时间反复猛拉；正常一周一次没事。
- 想单独 ping 一下可达性也行：
  ```bash
  curl -sI https://query1.finance.yahoo.com | head -1     # Yahoo 通不通
  curl -sI https://push2.eastmoney.com | head -1          # 东方财富通不通
  ```
