# 永久组合 v2 会计修复与纠错封存复测结果

日期：2026-08-31（最终部署与远端复核完成于 2026-09-01）

## 结论

永久组合 v1 的会计缺陷真实存在，修复范围保持在事先冻结的边界内：

- 不再为 ETF 上市前日期反向填充价格；
- 原始开盘价负责成交，原始收盘价负责估值，复权价格只负责动量；
- 除息日开盘交易前，现金分红只计入此前已持有的份额；
- 标的、权重、阈值、动量窗口、成本、整手规则和基准均未调整。

v2 在 Development 和一次纠错封存复测中都取得正收益并显著跑赢现金基准，说明永久组合方向有效。但是，复杂的动态配置并没有稳定胜过简单版本：纠错复测期内，固定永久组合的风险调整后表现最好。因此，当前证据支持把固定永久组合作为后续隔离纸面跟踪的首选，把动态版本保留为研究对照，不支持直接升级为正式策略。

2025-01-01 至 2026-08-28 的结果属于 `bug_corrected_sealed_retest`，不能再描述为 pristine blind holdout。下一份真正未见过的样本只能来自 2026-08-28 之后的前向纸面运行。

## 冻结身份

| 项目 | 值 |
|---|---|
| Git revision | `c072002863f0b7de4478144cf386198073156c2e` |
| Study | `permanent_portfolio_v2` |
| Accounting | `cash_distributions_v2` |
| Evidence class | `bug_corrected_sealed_retest` |
| Contract SHA-256 | `3a3d433ffe5fa3df91b6c7e3a183a8b41f9111405083caf01d49c68d3c58c6e2` |
| Code SHA-256 | `fc741c6a37248307d6562e48bac4fb5d2a82449cb3d59ebbcdc70f77d6292975` |
| Market bundle SHA-256 | `660dc3380aee767fd9392a3245a1bcd14348a6ab3a6269922b128f7da7469b72` |
| Development artifact | `4175dedb18e3d0b6fccc6384c6685fb2ae32a453c19ecf675173fdb6293857f4` |
| Holdout marker | `3909f6c767b1225c73570f6b27cbd5a434e548b626f33070c06b2916bd534e42` |
| Corrected retest artifact | `fc193d47e434e9fea5630e0430ba0ebc902866618df0584a1da9aade9908696d` |

## 数据审计

原始数据归档位于 `/private/tmp/permanent-v2-raw-20260831.tar.gz`，归档 SHA-256 为
`5e4aa247b1e17e234dc70399376e08b69ef7897e6a680483307e34f922ba3180`；原始清单规范哈希为
`42916fdb5bf8c86004305c7d10925f67e81b3d29766eaccae2cd79bd1e71789a`。

Development publication：

- 数据 SHA-256：`665426195df23084839f0928d71959941e5779f5bf5c50289f267ae5b984bccf`
- 7,681 行，2016-12-01 至 2024-12-31（含动量预热）
- 17 个可审计现金分红事件
- 最大除息参考价误差：`0.004545225758704419`

Corrected retest publication：

- 数据 SHA-256：`4af20ee02379eedb92ee8e4706f23608876bd72074866c4f968de8584c1398a9`
- 2,660 行，2023-12-01 至 2026-08-28（含动量预热）
- 10 个可审计现金分红事件
- 最大除息参考价误差：`0.0048677685950337946`

所有分区 Parquet、manifest 与 publication 指针 SHA-256 均独立复核。四个代码无重复，`511260.SH` 从首个真实报价日 2017-08-24 开始，不存在上市前伪造记录。

## Development：2018-09-03 至 2024-12-31

| 组合 | 年化收益 | 累计收益 | 最大回撤 | 相对现金 Sharpe | 成本（bps） | 交易数 |
|---|---:|---:|---:|---:|---:|---:|
| 动态永久组合 | 7.07% | 51.57% | -8.24% | 0.793 | 107.27 | 163 |
| 四资产等权 | 6.65% | 48.00% | -9.69% | 0.741 | 7.29 | 4 |
| 固定永久组合 | 6.50% | 46.74% | -9.69% | 0.720 | 10.33 | 8 |
| 沪深300持有 | 4.50% | 30.73% | -40.17% | 0.228 | 8.01 | 1 |
| 现金 ETF 持有 | 1.87% | 11.97% | -0.17% | — | 7.82 | 1 |

Development 中动态版本收益和回撤均优于固定版本及等权版本，并显著降低了单持沪深300的回撤，但付出了明显更高的换手与交易成本。

## 纠错封存复测：2025-01-01 至 2026-08-28

| 组合 | 年化收益 | 累计收益 | 最大回撤 | 相对现金 Sharpe | 成本（bps） | 交易数 |
|---|---:|---:|---:|---:|---:|---:|
| 沪深300持有 | 15.11% | 25.09% | -10.07% | 0.881 | 8.23 | 1 |
| 动态永久组合 | 14.97% | 24.85% | -14.62% | 1.067 | 30.83 | 44 |
| 固定永久组合 | 14.24% | 23.59% | -8.50% | 1.391 | 10.12 | 8 |
| 四资产等权 | 13.11% | 21.65% | -11.91% | 1.113 | 7.26 | 4 |
| 现金 ETF 持有 | 1.09% | 1.74% | -0.07% | — | 7.61 | 1 |

纠错复测期内，动态版本没有表现出稳定的复杂度溢价：年化收益略低于沪深300，最大回撤反而更大。固定永久组合虽然绝对收益稍低，但最大回撤和相对现金 Sharpe 最优，是更稳健的后续前向候选。

## 不变性与验证

- v1 marker、state、Development/Holdout manifest、Parquet、result 与 Dashboard 文件的 SHA-256 前后完全一致。
- 本地受保护路径 digest 前后一致；正式账户、Registry、season 和旧 sealed campaign 均未修改。
- 永久组合聚焦后端测试：73/73 通过；其中包含账户派息再投资后与复权总回报链逐期对账的 parity 回归测试。
- Dashboard 全量前端测试：263/263 通过；production build 通过。
- 本地规范审计：203/203 通过（隔离 worktree 缺少大体积全市场数据根，仅产生预期 warning）。
- 远端部署 canary：845/845 通过；`stock-analyze-dashboard.service` 为 active，摘要与永久组合 API 正常。
- Python 全量 discover 运行了 2,263 个测试；永久组合相关测试全部通过。其余有 1 个浮点精确相等失败和 42 个缺少可选依赖的导入错误，均不在本次变更模块内，因此未声称全量 Python suite 通过。

远端项目级 `SA_SYSTEM_AUDIT_DATA_ONLY=1` 仍因既有 A 股全市场数据基础失败：universe manifest 合同无效，Baostock 完成 5,540/5,880 个代码。该问题不被永久组合读取，也不是本次部署造成，但仍应作为独立运维/数据任务处理。总审计还会把两个带 `-` 前缀、允许失败的 iFinD 补充源命令状态码 2 误报为主行情失败；主行情与 QDII 两个必需命令实际均为状态码 0，systemd `Result=success`。

Claude Code 2.1.215 对 `c072002^..c072002`、合同、计划、失效记录、结果与测试做了最终只读审查，结论为 `APPROVED`，没有 P0/P1。它指出的唯一实质性 P2 是计划中的 buy-and-hold parity 测试缺失；该测试随后补齐并通过。另有两套被最终 publication 替代的孤儿数据目录、v2 一次性 marker 自身仍使用 marker schema 1、计划 checkbox 未回填三项留痕问题；latest/state 均未引用孤儿目录，marker 不可重写，因此按证据保全原则保留原状。

## 部署

规范部署 release stamp 为 `20260831-155621`，release manifest 状态为 `deployed`，
`DEPLOY_VERSION` 为 `c072002863f0b7de4478144cf386198073156c2e`。远端只读 Dashboard 报告 SHA-256 为
`4ce308e3ec3cb5f6b2bc1466c6ddec0be2a6630f4ab78b68f5c13de3dddde99d`。

线上 API 已返回：

- `studyId=permanent_portfolio_v2`
- `validity=corrected_retest`
- `accountingVersion=cash_distributions_v2`
- Development/Holdout artifact SHA-256 与上述冻结值一致
- 历史结果 complete，forward 仍为 unavailable
