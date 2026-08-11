# 模型迭代生命周期设计

## 背景

当前系统已经具备 `model_version`、模型注册表、`research -> shadow -> active`
状态迁移、`champion_model_version` 和四个影子周期的准入门槛。正式策略也只会应用
`active_status=active` 的预测。

缺口在组合和产品层：现有 `model_shadow` 账户读取主预测文件，而主预测会优先选择
Champion。一旦首个版本激活，这个账户可能继续交易 Champion，而不是验证下一代
Challenger；同时所有版本共用一个净值目录，无法准确归因某个版本的收益。Dashboard
也把验证机制展示成一个永久账户，没有呈现版本、晋级和替代关系。

## 目标

1. 正式策略始终固定读取已激活的 Champion，不读取正在迭代的模型。
2. 每个 `市场 x 周期 x 模型版本` 拥有独立的验证组合和完整审计记录。
3. Challenger 晋级为 Champion 后，验证工作台自动固定下一候选版本。
4. 页面以“模型迭代”呈现 Champion、Challenger、闸门和版本历史。
5. 保留 `model_shadow` 内部标识和旧 URL 兼容，用户界面不再出现“模型影子账户”。

## 版本身份

模型的唯一身份仍使用现有 16 位 artifact hash。页面增加稳定的可读别名：

- A股 20日第五个注册版本：`A20-V005`
- 跨境 ETF 5日第四个注册版本：`Q5-V004`

别名按同一市场和周期的 `registered_at` 顺序派生。注册表只追加版本，因此已有别名
不会因新版本加入而改变。原始 hash 作为技术 ID 保留在详情中。

## 生命周期

用户态名称与内部状态映射：

| 内部状态 | 页面名称 | 含义 |
|---|---|---|
| `research` | 研究候选 | 尚未通过离线证据闸门 |
| `shadow` | 模拟验证 | 已通过离线闸门，正在累计验证周期 |
| `active` + champion | 正式使用 | 正式策略允许读取 |
| 历史 outcome `promoted` | 已晋级 | 曾从验证组合晋级为 Champion |
| 历史 outcome `superseded` | 已替代 | 已被后续 Champion 替代 |

每个市场和策略使用周期只有一个固定 Challenger。固定后，即使训练产生更新版本，也
不会在验证中途切换。只有当前 Challenger 成为 Champion、从注册表消失，或被明确终止
时，控制器才选择下一版本。选择优先级为最新 `shadow`，其次最新 `research`，并排除
当前 Champion。

## 数据流

```text
训练产物 + registry.json
        |
        +--> Champion prediction -----------------> 正式策略因子叠加
        |
        +--> pinned Challenger prediction --------> 版本独立验证组合
                                                     |
                                                     +--> NAV / 持仓 / 订单 / 成交
```

候选预测写入：

`data/research/iteration_predictions/<market>/<horizon>/<model_version>/<date>.parquet`

版本组合写入：

`data/model_iterations/<market>/<horizon>/<model_version>/`

迭代控制状态写入：

`data/model_iterations/<market>/<horizon>/iteration_state.json`

旧目录 `data/model_shadow/<market>/` 保留为历史数据，不删除、不重置。新版本组合从统一
初始资金开始，避免把旧版本收益带入新版本。

## 正式策略绑定

正式策略继续读取 canonical prediction。`select_registry_model` 优先 Champion，因此
Challenger 不会影响正式选股。预测融合仍保留两种强度：稳健防守 `0.20`、趋势进攻
`0.35`。

版本身份必须包含周期。正式策略使用的预测周期在策略配置中明确声明，不能再由共享
代码隐式偏好 5 日。首期保持现有正式行为为 5 日，但在 Dashboard 显示绑定周期；后续
可以让稳健防守绑定更长周期，而不改变版本控制协议。

## Dashboard 信息架构

左侧菜单：`模型迭代`

页面标题：`模型迭代工作台`

首屏状态区：

- 正式使用版本：Champion 别名、周期、启用状态、使用它的正式策略
- 验证中版本：Challenger 别名、内部阶段、开始日期、验证周期
- 晋级闸门：已通过项、未通过项、剩余周期
- 当前组合：候选模型模拟组合的净值、持仓、待执行订单

后续内容继续复用已有预测、模型健康、净值、持仓、交易时间线和证券下钻，但所有
“影子输入”改为“验证版输入”，所有“模型影子账户”改为“候选模型模拟组合”。

旧链接 `view=model-shadow` 自动规范化为 `view=model-iteration`。

## 自动化与异常

研究任务每天先生成 Champion 预测，再生成固定 Challenger 预测，最后运行候选版本
组合。若没有候选版本，任务以 `no_candidate` 成功结束并保持现金；若候选预测缺失，
不回退到 Champion，避免污染验证结果。

飞书日报只显示：正式版本、验证版本、验证进度、组合净值和待执行订单。版本切换、
晋级和预测缺失属于需要关注的异常；普通每日重复运行不新增消息。

## 验收标准

1. Champion 和 Challenger 可以是不同版本，正式策略只应用 Champion。
2. Challenger 固定后，新训练版本不会改变其组合目录或历史净值。
3. Challenger 晋级后，下一次运行自动选择下一候选且使用全新组合目录。
4. Dashboard 不显示 `model_shadow` 或“模型影子账户”，并清楚展示版本生命周期。
5. 旧 URL、旧 CLI 和旧数据目录保持兼容且不被删除。
6. 本地全量测试、前端构建、ECS 回归、真实预测试跑和移动端页面检查通过。
