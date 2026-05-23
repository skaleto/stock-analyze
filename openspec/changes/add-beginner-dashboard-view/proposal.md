## Why

当前 dashboard(`reports/competition/dashboard.html`,~270 KB)对**深度信息**做了很好的展示:因子覆盖率热力图、前向 RankIC、因子贡献明细、运行账本、订单执行细节等。**对量化老手友好,对新手不友好**:

- 一打开就是 15 个 section,信息密度过高
- 关键的"我账户里现在有多少钱、今天涨了还是跌了、我现在买了什么"埋在中间
- 新手要"花了多少钱在每只票上"、"哪些票赚哪些票亏"的快速回答,要翻多个表格

2026-05-23 human operator 明确表达:

> "用一些比较直白的语言。我希望能够看到嗯,对新手比较友好的,比如说你选了哪些股,然后你的大概花费了多少钱,然后每个股花了多少钱?大致的收益情况以及预测情况是怎么样的?"

→ **dashboard 受众分层**。同一份 data/* 数据,渲染成两个视图:专业视图 + 新手视图。

## What Changes

新增**新手简化版**入口,与现有专业版并存。**不删除现有 dashboard**,只是多个并行视图。

### 1. 路由结构

```
http://127.0.0.1:8765/
  ├─ index.html → 三 tab 专业版(Claude / Codex / Compare)             (现有,不动)
  ├─ simple.html        → 新手版总览(两 agent 对比简化卡片 + 持仓 + NAV) (新)
  ├─ simple/claude.html → claude 单独简化版                              (新)
  ├─ simple/codex.html  → codex 单独简化版                               (新)
  ├─ pro.html → 同 index.html(别名)                                    (新)
  └─ … 其他静态资产
```

### 2. 简化版包含的 section(按从上到下排序)

```
┌────────────────────────────────────────────────────────────────────┐
│ 头部:↓ 三个一键切换标签 ↓                                          │
│   [简化版]  [专业版]  [策略演进]                                    │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│ Section 1:我的账户(顶部大数字)                                   │
│   总资产: ¥1,002,344  今日: -125 (-0.012%)  本月: +2,344 (+0.234%) │
│                                                                    │
│ Section 2:两个 AI 现在的成绩                                       │
│   ┌────────────┐  ┌────────────┐                                   │
│   │ Claude     │  │ Codex      │                                   │
│   │ 累计 +2.3% │  │ 累计 +1.8% │                                   │
│   │ 跑赢沪深   │  │ 跑赢沪深   │                                   │
│   │   +1.3%    │  │   +0.9%    │                                   │
│   └────────────┘  └────────────┘                                   │
│                                                                    │
│ Section 3:净值曲线(双线图)                                       │
│   [折线图:Claude vs Codex vs 沪深 300 vs 中证 500]                │
│                                                                    │
│ Section 4:Claude 现在持仓的 10 只(摘要,点击展开 50)             │
│   股票名 行业 买入价 现价 持仓市值 盈亏%                            │
│   贵州茅台 食饮 1340 1290 ¥X 万元 -3.7%                            │
│   ...                                                              │
│                                                                    │
│ Section 5:Codex 现在持仓的 10 只(同上)                          │
│                                                                    │
│ Section 6:本周双方都买了 / 都卖了哪些                              │
│   都买:5 只 [中际旭创,...]                                         │
│   都卖:2 只 [...]                                                  │
│   Claude 独买:3 只                                                 │
│   Codex 独买:3 只                                                  │
│                                                                    │
│ Section 7:最近 5 笔模拟成交                                        │
│   日期 双方 股票 买/卖 股数 价格 盈亏                              │
│                                                                    │
│ Section 8:本月策略调整摘要(连接到完整 evolution_log)              │
│   Claude:把 PE 权重提升 +3pp,因为本月低 PE 风格跑赢               │
│   Codex:加入股息率因子,因为本月银行股领涨                         │
│   → 点击查看完整 evolution_log                                     │
│                                                                    │
│ 底部链接:专业版 / 历史回测 / 数据源状态                            │
└────────────────────────────────────────────────────────────────────┘
```

### 3. 简化版**不包含**

- 因子覆盖率热力图、前向 RankIC 折线
- 因子贡献明细每只票拆解
- 数据源状态 / 数据健康
- 运行账本(runs.csv)
- 待执行模拟订单详细
- agent 笔记 / briefings 内容
- factor_runs/*.csv 单股因子打分明细

这些都在 `pro.html`。

### 4. CSS 风格差异化

| 维度 | 专业版 | 简化版 |
|---|---|---|
| 默认字号 | 13-14px | 15-16px |
| 颜色 | 中性灰 + 蓝 / 橙 区分 agent | 暖色调,emoji,大字号 |
| 表格 | 多列细密 | 关键 5 列 |
| 单位 | bp(基点)、Sharpe 等 | "1 万 2 千 3 百元"、"涨 / 跌 X%" 中文 |
| 数据精度 | 4 位小数 | 2 位小数 + 千分位逗号 |

### 5. 用户感知到的浏览路径

```
访问 http://127.0.0.1:8765/        → 重定向到 /simple.html(默认新手)
点 [专业版] tab                    → 跳 /pro.html(原来的 dashboard)
点 [简化版] tab                    → 回到 /simple.html
点 [策略演进]                     → 新增页 /evolution.html(选)
```

## 验收

- `python3 -m stock_analyze competition-dashboard` 同时生成 `simple.html` 与 `pro.html`(后者沿用现有 index.html)
- `simple.html` ≤ 80 KB(轻量)
- 现有 `index.html` / `claude/dashboard.html` / `codex/dashboard.html` 路径**不变**
- 新文件 / 部分:`reports/competition/simple.html`、`reports/competition/simple/claude.html`、`reports/competition/simple/codex.html`、`reports/competition/pro.html`(symlink to index.html)
- 数字单位、措辞、布局符合"新手友好"(主观,通过 user review)

## 与已有 change 的关系

- `tighten-audit-findings`(已落地)F5/F10 加的 dashboard "策略演进时间线" 面板 → 在 pro.html 保留,在 simple.html 浓缩为 Section 8 摘要
- `enable-llm-direct-strategy-evolution`(draft)新增的 evolution_log / evolution_diff → simple.html Section 8 直接读它们
- `migrate-data-source-to-tushare-pro`(draft)→ 与本 change 正交,数据源换成 Tushare 后 dashboard 数据流不变

## 风险

- 简化版与专业版双维护成本(代码层面尽量复用 `reporting.py` 的 helper)
- 用户期望蔓延(说"再加一点 X" 容易,导致简化版又变重)→ 立 anti-goal:simple.html ≤ 80KB,超了就拒新增 section
- 中文文案易出错(数字单位 / 涨跌方向)→ 多写 unit test 校验渲染输出

## Agent 来源声明

本 change 触及 `stock_analyze/reporting.py`、`stock_analyze/dashboard_aggregator.py`、`README.md`、`docs/*`,均在 CLAUDE.md §7 禁地。Human operator 显式邀请实施。当前 status = **DRAFT,await confirmation**。
