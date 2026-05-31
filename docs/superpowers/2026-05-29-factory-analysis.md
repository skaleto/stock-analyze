# 工厂全面分析报告（产品 + 代码）

> 日期：2026-05-29 · 分析者：Claude (Opus) · 范围：整个 stock-analyze 多市场竞赛系统
> 代码规模：stock_analyze 17.6K 行 + tests 10.0K 行，469 测试全绿
> 方法：通读核心模块（factor_pipeline / simulator / competition / backtest engine /
> notifier）+ 系统文档 + 配置 + 运行态。

---

## 0. 一句话结论

**这是一个设计水平显著高于"个人项目"的系统**——公平基线锁字段、point-in-time 数据可见性、可重现因子快照、三段窗口纪律、审计账本、OpenSpec 变更管理，这些都是机构级 quant 平台才有的纪律。**但当前有 3 个"看起来在工作、实际上没产生价值"的环节**，以及 **1 个新引入的代码正确性 bug**。把这 4 件事修掉，系统的"名义能力"和"实际能力"才会对齐。

评级：**架构 A- / 产品成熟度 B / 当前实效 C+**。差距全在"已搭好管道但还没通水"。

---

## A. 产品层面分析

### A.1 系统本质与成熟度

A 股纸面多因子竞赛（claude vs codex），现已扩展到港股/美股三市场独立 playground。核心价值主张：**两个 LLM agent 在字节级相同的市场条件下各跑策略，每月对比，靠竞争驱动策略进化**。

成熟度盘点：

| 能力 | 状态 | 实评 |
|---|---|---|
| 前向模拟（A股） | ✅ 生产运行中（2026-05-18 起跑） | 真实、保守口径、跑通 |
| 公平基线锁字段 | ✅ 强制（competition.py） | 设计扎实 |
| 因子流水线 | ✅ winsorize→zscore→中性化→加权，可重现 | 质量高 |
| 绩效归因 | ✅ 13 个指标全套 | 完整 |
| 因子诊断（覆盖率+前向IC） | ✅ | 完整 |
| 历史回测引擎 | ⚠️ 引擎在，但**用 PE-only 信号**（见 A.2 P1） | **门面工程** |
| LLM 市场情绪因子 | ⚠️ MVP，**broadcast 标量对排名零影响**（见 A.2 P2） | **零 alpha** |
| 港股/美股 | ⚠️ 代码+测试完成，**未部署、月度演化未接线**（见 A.2 P3） | **半成品** |
| 操作员告警 | ✅ 失败→PIPELINE_FAILURES+Lark；日报 DM | 刚补齐 |
| 审计/可追溯 | ✅ run_ledger + config snapshot + evolution diff | 优秀 |

### A.2 产品级问题（按影响排序）

#### 🔴 P1：回测 gate 用 PE-only 信号，不是它要审的 overlay —— "门面 gate"

`backtest/engine.py:_compute_signals` 第 271 行：`"score": -float(r["pe_ttm"])`。

回测引擎对所有 overlay **一律按低 PE 排序选股**，完全无视 overlay 实际配置的因子组合（PE/PB/ROE/动量/股息/低波 + 各自权重 + 方向）。但这个回测正是月度策略演化的**准入 gate**（`evolution_writer` 在 commit 新 overlay 前跑验证窗口回测，三条底线：max_dd≤25% / sharpe≥-0.5 / cum_return≥-15%）。

**后果链**：
- gate 对 claude 和 codex 跑出**完全相同**的回测结果（都是低 PE top-N），因为它没读各自的因子配置。
- LLM 每月看到的"你这套 overlay 历史回测 +12%"是**关于一个它没在用的策略**的数字 → 误导决策。
- 三段窗口纪律（训练/验证/OOS）建立在一个测错对象的回测上 → 纪律落空。

这是整个研究闭环最高杠杆的修复点。我此前已写好 OpenSpec change `bridge-factor-pipeline-into-backtest`（proposal+design+tasks+spec 都在 `openspec/changes/`），把 `factor_pipeline.process_factors` 适配到 `PointInTimeView`，估 800-1200 行。**未实施**。

#### 🔴 P2：市场情绪因子 MVP 是横截面 no-op —— 每周占用 operator 时间，产生零 alpha

`factor_pipeline._broadcast_shift`：`<agent>_market_sentiment_1w` 是一个**标量**，`sign × weight × value` **统一加到每只候选股的 score 上**。由于是常数平移，**对横截面排名零影响**（所有股票同涨同跌一个数）。选股结果与不加这个因子时**完全相同**。

但流程上：operator 每周要打开 Claude.ai/ChatGPT、跑 prompt、抄 JSON、跑 `record-sentiment` × 2 个 agent ≈ 每周 10-20 分钟。**投入产出比当前为零**（纯属"建管道 + 养习惯"，文档诚实地写明了这点）。

升级到 Phase 3（per-stock 颗粒度）才真正影响排名。在那之前这是**纯成本**。

#### 🟠 P3：港股/美股代码完成但未通电

Phase 2/3 我已经把 markets/{hk,us}/ 全套写完（mechanics/universe/data_provider/simulator/strategy + 配置 + 110 个测试）。但：
- **未部署 ECS**（无 systemd timer，无凌晨美股拉数定时）
- **月度演化未真正按市场路由**：`evolution_writer` / `monthly_review` / `agent_briefing` 只是"signature 接受了 market 参数"，函数体内路径解析尚未真正分市场（Phase 1 Task 8 是 decorative kwarg）
- **backtest gate 对 HK/US 不存在**（design 里 v2 才做）

→ 现在三市场是"A 股真跑 + 港美股代码就绪但休眠"。**别急着通电**：见讨论点 D.1。

#### 🟠 P4：单点数据依赖

- A 股：`TUSHARE_TOKEN` 2000 积分包。港美股：yfinance 免费（Yahoo 端可能限流/封 IP/改 schema）。
- yfinance 无 SLA。一旦 Yahoo 改字段或封 IP，HK/US 数据静默返回空 → 策略空跑。已有 CacheMiss 机制兜底但只能撑 1 天。

#### 🟡 P5：回测幸存者偏差 + 无组合优化器

- 回测用**当下**指数成分倒推历史 → 幸存者偏差（文档已诚实标注，待补历史成分）。
- 组合控制是规则式（行业上限/buffer/holding_days），未接 CVXPY。对当前阶段够用，不急。

### A.3 竞赛设计本身是否成立？

**成立，且巧妙**。claude-vs-codex 同基线对打，把"策略好坏"变成可观测的相对竞争，比单 agent "自说自话"强很多。锁字段设计保证了可比性。**唯一隐忧**：竞赛才 2 周（5/18 起跑），forward IC 还 92% NaN（样本不足，正常），现在谈"谁的策略好"为时过早——这不是问题，是 by design 的耐心。

---

## B. 代码层面分析

### B.1 架构评估

**markets/ 子包重构（Phase 1）是干净的**：a_share/hk/us 各自 self-contained（data_provider/simulator/strategy/universe/mechanics），shared 模块（factor_pipeline/overlay_guard/competition/notifier/sanity_check）留顶层，CLI 用 `--market` + `get_market_module` 路由。对称、可扩展、向后兼容（默认 a_share）。

**优点**：
- 关注点分离清晰，每个 market 改动不影响其它市场。
- 锁字段校验（competition.py `_walk_pattern` / `_validate_locked_paths`）实现严谨，支持 `accounts.*.cash` 通配。
- factor_pipeline 可重现性保证（`score == Σ contribution per code`）+ 长表快照，机构级。
- 测试覆盖广（469 个），TDD 节奏明显。

### B.2 代码级问题（按严重度排序）

#### 🔴 C1（HIGH）：做空 NAV mark-to-market bug（HK + US 我新引入）

`markets/hk/simulator.py:294` 和 `markets/us/simulator.py` 同构：

```python
coll = gross * SHORTING_COLLATERAL_RATIO
net_debit = coll + stamp + commission
account_state["cash"] = cash - net_debit          # 扣了 collateral
account_state["cash_collateral"] = collateral + coll
# ❌ 做空卖出收到的价款 gross 从未入账（既没进 cash 也没进任何资产桶）
```

`update_nav` 里：`total = cash + cash_collateral + positions_value`，其中做空 `positions_value = -|shares|×px`。

**推演**（100% collateral，开仓价 $100，100 股，忽略费用，起始权益 C）：
- 开仓后：cash = C - 10000，collateral = +10000，position = -10000
- NAV = (C-10000) + 10000 + (-10000) = **C - 10000** ❌

即**开一笔做空、价格没动，NAV 瞬间凭空蒸发了一个名义本金**。正确应为 C（开仓不该改变权益）。根因：做空价款（asset）没被表示。

**影响范围**：
- 做空持仓期间的**每日 NAV 被低估约一个名义本金** → Sharpe / 最大回撤 / IR / 累计收益**全部失真**。竞赛按累计收益+IR 计分，**一旦用做空就直接污染计分**。
- 平仓（cover）时 NAV 会"瞬间弹回"正确值——所以**已实现 round-trip 盈亏是对的**，错的只是持仓期间的盯市 NAV。
- 现有测试 `test_nav_short_position_reduces_equity` 实际**把这个 bug 当正确行为编码了**（seed 的 state 自洽于错误模型）。

**为何是 latent（暂不爆炸）**：long-only 的 `generate_rebalance_orders` 永远不会 emit `side: short` 订单。只有 agent 主动配做空才会触发。但这是个**埋着的雷**——HK/US 通电 + 任一 agent 用做空就引爆。

**修复方向**：做空时把卖出价款路由进 collateral 桶而非从 cash 扣（Model A）：开仓 `cash -= fees; collateral += gross`，平仓释放 collateral + 付买回款。需同步改 cover 数学 + 重写那条把 bug 当真值的测试。约 40-60 行 × 2 市场。

#### 🟠 C2（MED）：HK/US ~80% 重复代码

- `data_provider`：`_safe_float` / `_pct_change` / `_trailing_volatility` / `_apply_slippage` / `_pd_index_isoformat` 在 hk 和 us 里**字节级相同**；provider 类结构近乎一致，只差 `.HK` 后缀 + universe + slippage 常数。
- `simulator`：settlement queue + 做空/cover 逻辑几乎一致，只差 `SETTLEMENT_DAYS`（2 vs 1）+ 费率常数。

应抽出 `markets/_yfinance_base.py`（共享 provider 基类 + 数学 helper）和一个共享 settlement/short mixin。设计 spec 里本来就预告了这个抽取（"future refactor"）。**好处**：C1 的做空 bug 只需在一处修，而非两处。**所以 C2 应和 C1 一起做**。

#### 🟠 C3（MED）：CSV 存储的 dtype 脆弱性

`benchmark_code` 被 pandas 推断成 int 把 `000300` 截成 `300` 的那类 bug，是 CSV 无 schema 的系统性风险。已经靠"所有读 CSV 处显式 dtype={...}"打补丁（C1 sweep）+ sanity_check 兜底，但**每新增一处读 CSV 都得记得**。roadmap #6（迁 SQLite/DuckDB）是正解，但工程量大，非紧急。

#### 🟡 C4（MED-LOW）：factor_pipeline 的 per-idx Python 循环

`process_factors` 里为构建 factor_table 长表，对每个 (factor × code) 做 `for idx in df.index` 逐行 dict append（line 233-256）。~800 候选 × ~10 因子 = 8000 次 Python 级 dict 构造/次。前向单次跑可接受（~10ms），但**回测全窗口**（1000+ 交易日 × 每周）会被这个放大。Phase 2 bridge factor_pipeline 进回测时会撞上——届时应向量化。

#### 🟡 C5（LOW）：HK/US 测试全程 mock yfinance

110 个 HK/US 测试都 `patch` 掉 `_fetch_ticker_info` / `_fetch_ticker_history`，喂构造数据。**测的是 wrapper 管道，不是真实数据契约**。一旦 Yahoo 改字段名（`trailingPE`→别的），测试照绿、生产静默坏。建议加 1 个 `@unittest.skipUnless(网络)` 的真实 smoke test，定期手动跑确认 schema 没漂。

#### 🟡 C6（LOW）：competition.py 双路径解析系统并存

`resolve_agent_paths`（legacy，硬编码 a_share）和 `resolve_market_paths`（新，带 market 参数）并存。Phase 1 增量迁移留下的技术债。等 HK/US 真正接线时应统一到后者，删掉 `_DEFAULT_MARKET` / `_OVERLAY_SUFFIX` 这套 a_share-only 兼容层。

#### 🟡 C7（LOW）：几个大文件

`data_provider/__init__.py` 1647 行、`beginner_dashboard.py` 1252 行、`dashboard_aggregator.py` 971 行、`cli.py` 845 行。data_provider 那个尤其值得拆（按 vendor：tushare/baostock/akshare 分文件——base.py 已抽出但主体仍在 __init__）。非紧急。

### B.3 代码亮点（值得保持）

- `competition.py` 锁字段 `_walk_pattern` 的通配实现优雅。
- `factor_pipeline` 的可重现契约 + 缺失因子按比例重分配权重，思路正确。
- `simulator.execute_order` 的保守成交（停牌/涨跌停/T+1/现金不足减档）建模到位。
- notifier 的"凭证缺失→preview 模式 exit 0"降级、"DM 失败不污染 PIPELINE_FAILURES"通道隔离，工程嗅觉好。

---

## C. 优先级改进路线（severity × effort）

| 优先级 | 项 | 严重度 | 工作量 | 建议 |
|---|---|---|---|---|
| **1** | C1 做空 NAV bug（+ C2 抽 base 一起做） | 高（计分污染，latent） | ~1 天 | HK/US 通电前必修 |
| **2** | P1 回测 gate 接真 factor_pipeline | 高（研究闭环根基） | ~800-1200 行/2-3 天 | OpenSpec 已写好，择期实施 |
| **3** | P3 决定 HK/US 是否通电 + 接线 | 中（半成品悬置） | 视决策 | **先讨论**（D.1） |
| **4** | P2 市场情绪因子去留 | 中（纯成本） | 决策为主 | **先讨论**（D.2） |
| **5** | C5 yfinance 真实 schema smoke test | 低 | ~半天 | HK/US 通电时配套 |
| **6** | C3 CSV→SQLite | 低-中 | 大 | roadmap，不急 |
| **7** | C4 factor_pipeline 向量化 | 低 | 中 | 跟 P1 一起做 |
| **8** | C6/C7 技术债清理 | 低 | 中 | 顺手 |

---

## D. 需要和你讨论的不确定点

### D.1 港股/美股要不要现在通电？

代码就绪，但通电意味着：operator 每周负担 ×3（sentiment 采集 3 市场）、每月演化 ×3、美股要凌晨拉数。而 A 股竞赛本身才 2 周、forward IC 还没攒够。**我倾向：HK/US 代码先 park（已 commit、随时能启），等 A 股竞赛跑出 1-2 个月有意义的对比、且 P1 gate 修好后再通电**。否则是在一个还没验证的研究闭环上叠 3 倍运维。你怎么想？

### D.2 市场情绪因子（P2）当前是纯成本，怎么处理？

三个选项：(a) 暂停每周采集，等 Phase 3 per-stock 升级后再恢复；(b) 继续采集"养习惯"+ 攒历史数据为 Phase 3 铺路；(c) 直接上 Phase 3。我倾向 (a) 或 (b)，取决于你觉得每周那 10 分钟"养习惯"值不值。

### D.3 C1 做空 bug 现在修还是 latent 放着？

它是 latent（long-only 路径不触发）。但它埋在我刚写的代码里、且 HK/US 一通电就有风险。我倾向**和 C2 抽 base 一起、在 HK/US 通电前修掉**——反正通电前要动这块代码。除非你想现在就修干净。

### D.4 这份报告要不要落成 OpenSpec change / 进 roadmap？

我可以把 P1/C1/C2 写成正式 OpenSpec changes（P1 的 `bridge-factor-pipeline-into-backtest` 已存在），或更新 system-overview §17 roadmap。看你想要多正式。
