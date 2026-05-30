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

---

## 实测结论(2026-05-30 · operator 本地 Mac,底层 = 杭州联通家宽)

> 临时授权覆盖 CLAUDE.md §7.0 改文档禁令,把实测结论并入本指南(operator 2026-05-30 口头授权)。

### TL;DR

- **数据源选定:yfinance(Yahoo)** —— 港美一体、数据新鲜(实测到上一交易日 2026-05-29)。**不用 akshare/东财**。
- **前提:必须走「香港住宅」代理出口**(HKT / AS4760,如 `42.200.172.x`)。机房节点不行。
- 跑 yfinance 的进程**保持继承 `http_proxy` 环境变量**(走代理);别关代理。

### 实测矩阵(出口 × 数据源)

| 出口 | yfinance / Yahoo | akshare / 东财 |
|---|---|---|
| 联通家宽**直连**(CN) | ❌ 403/429(Yahoo 封 CN IP) | ✅ 通(代理关掉/绕过时,单发 200 + 真实数据) |
| 香港**机房**代理(Akari AS38136) | ❌ 429 | ❌ 东财掐连接 |
| 香港**住宅**代理(HKT AS4760) | ✅ **可用**(港股基本面 3/3、美股 3/3、日线最新 2026-05-29) | ❌ 东财掐连接(HTTP 000) |

官方脚本最终判定:`[OK] 用 yfinance（能连 Yahoo；akshare 的东财行情连不上）`。

### 两家封锁逻辑不同(关键认知)

| | 封锁维度 | 需要的出口 |
|---|---|---|
| **Yahoo / yfinance** | 按 IP **类型/信誉**:机房 IP 吃 429,住宅 IP 放行 | 香港/境外**住宅** IP ✅(机房 ❌) |
| **东财 / akshare** | 按**地理位置**:非大陆一律掐(住宅也掐) | **大陆** IP ✅(任何香港都 ❌) |

→ 两者出口要求**互斥**,一条线路喂不饱两家。yfinance 港美一体,只用它即可。

证据:把请求强制从香港**住宅** IP(HKT `42.200.173.220`)出去打东财 → `HTTP 000` 被掐;香港**机房**(Akari)也被掐 —— 两类香港都死,说明东财按地理封,与住宅/机房无关。而同一条香港住宅 IP,Yahoo chart API 返回 `200`、yfinance 全通。

### 两个坑

1. **verify 脚本结果强依赖当前代理状态**。脚本用 `requests`(默认 `trust_env=True`)会吃代理;若 Clash 开 **TUN/增强模式**,`env -u http_proxy` 都绕不掉,会**全红假阴性**。跑前先确认出口:`curl -s https://ipinfo.io/json | grep -E '"(ip|country|org)"'`,要 yfinance 须是香港住宅(HKT)。
2. **本机 Clash 把 `*.eastmoney.com` 划进了「走代理」**。实测 `myip.ipip.net` 走 Clash 直连(出口仍 CN),但 `push2.eastmoney.com` 走 Clash 被转去香港 → 被东财掐。所以 akshare(`trust_env=True`)会被劫去香港而失败;只有显式绕代理(`trust_env=False` / `--noproxy` / 退出 Clash)才走大陆直连。若将来要用 akshare,需退出 Clash 或给 `*.eastmoney.com`(含 `push2*`、`*.push2his.eastmoney.com`)加 DIRECT 规则。

### 每周拉数操作要点

1. 数据源 = yfinance;provider 代码已就绪(`markets/hk` + `markets/us`)。
2. 拉数时**开着香港住宅代理**,进程继承 `http_proxy`。代理节点选「住宅/家宽」线路,别用「机房」。
3. 偶发 `curl:(35) TLS connect error` 是代理隧道抖动,**重跑即可**,非封锁。别短时间猛拉。
4. 拉完上传 ECS(ECS 阿里云机房 IP 被 Yahoo 硬限流,故采「本地家宽拉 → 传 ECS」模式)。

---

## Clash 分流配置(让一台机器同时满足两家)· 2026-05-30 实测落地

> operator 同条授权延续(临时覆盖 §7.0):把实际配出的 Clash 方案 + 验证结果写入本指南,供后续 agent 接手。
> 适用客户端:**Clash Verge Rev**(Mihomo 核心,本机实测)。

### 目标

同一台家宽机器,按域名分流:

- `*.eastmoney.com`(akshare/东财)→ **DIRECT**(走大陆联通出口,东财才认)
- `*.yahoo.com` 等(yfinance/Yahoo)→ **香港住宅节点**(HKT 家宽,Yahoo 才放行)
- 其它流量 → 订阅默认,不变

这样代理常开也能两家同时可用(yfinance 是主源,东财 DIRECT 当备份)。

### 踩坑:`prepend-rules` merge 指令在本版本不生效

最初把规则写进「全局扩展配置 → **Merge**」,用 `prepend-rules` / `prepend-proxy-groups`。
**本版本 Clash Verge 的 merge 引擎不消化这两个指令**——会把它们当普通字段原样输出成
顶层 `prepend-rules:` 废 key,Mihomo 不认 → 规则等于没写(东财仍被转香港)。

→ 结论:**别用 `prepend-rules` merge 写法,改用全局 Script(JS)**,程序化注入分组 + 前置规则,100% 生效。

### 可用方案:全局 Script(JS)

「全局扩展配置 → **Script**」整段替换为:

```javascript
function main(config, profileName) {
  const groupName = "📈行情-港住宅";

  // 1) 港住宅节点组:从订阅节点里筛名字含「香港家宽」的(HKT 住宅)
  const hkHome = (config.proxies || [])
    .map(p => p.name)
    .filter(n => n && n.indexOf("香港家宽") !== -1);

  config["proxy-groups"] = config["proxy-groups"] || [];
  if (!config["proxy-groups"].some(g => g.name === groupName)) {
    config["proxy-groups"].unshift({
      name: groupName,
      type: "select",
      proxies: hkHome.length ? hkHome : ["DIRECT"]
    });
  }

  // 2) 规则前置(first-match-wins):东财直连 + Yahoo 走港住宅
  config.rules = [
    "DOMAIN-SUFFIX,eastmoney.com,DIRECT",
    "DOMAIN-SUFFIX,yahoo.com," + groupName,
    "DOMAIN-SUFFIX,yimg.com," + groupName,
    "DOMAIN-SUFFIX,yahooapis.com," + groupName
  ].concat(config.rules || []);

  // 3) 清掉之前 merge 误入的字面 key(防御)
  delete config["prepend-rules"];
  delete config["prepend-proxy-groups"];

  return config;
}
```

对应的「全局扩展配置 → Merge」还原成最简(别再留 `prepend-*`):

```yaml
profile:
  store-selected: true
dns:
  use-system-hosts: false
```

保存后**回订阅页重选一次当前订阅**(或重启)触发重新生成 `clash-verge.yaml`。
生成正确的标志:运行时配置 `rules:` 段**头 4 条**就是上面那 4 条,且**无残留** `prepend-rules:` 顶层 key。

### 验证(实测结果 2026-05-30)

```bash
# 路由探测
curl -s --max-time 15 -o /dev/null -w "东财(走代理) %{http_code}\n" \
  "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=2&fid=f3&fs=m:128&fields=f12,f14"
curl -s --max-time 15 -o /dev/null -w "Yahoo(走代理) %{http_code}\n" \
  "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d"
# 官方脚本
python3 scripts/verify_data_sources.py
```

实测:

- **yfinance:`[OK]` 6/6 全绿**(港股 0700/0005/0941 + 美股 AAPL/MSFT/NVDA 基本面 3/3,
  日线最新 2026-05-29)。「Yahoo → 港住宅」稳定 200/200/200。
- **东财 → DIRECT 路由正确**(单发 `200`)。注意:脚本里 akshare 仍可能 `[FAIL]`,那是
  **东财反爬假阴性**——短时间高频/burst(`stock_us_spot_em` 一次翻 133 页)会触发东财掐连接,
  **不是路由问题**。冷却后单发即正常,akshare 自带重试。

### 给接手 agent 的落地结论

| 流量 | 路由 | 出口 | 状态 |
|---|---|---|---|
| `*.yahoo.com`(yfinance) | `📈行情-港住宅` 组 | 香港家宽 HKT | ✅ 稳定,数据新鲜 |
| `*.eastmoney.com`(akshare) | DIRECT | 大陆联通 | ✅ 路由正确(反爬冷却后即用) |
| 其它 | 订阅默认 | 不变 | ✅ 未受影响 |

→ **每周拉数定为 yfinance(港美一体,代理常开走港住宅),provider 代码已就绪
(`markets/hk` + `markets/us`)。东财 DIRECT 作备份。** 下一步可由后续 agent 把
yfinance 每周拉数 → 上传 ECS 的流程串起来。
