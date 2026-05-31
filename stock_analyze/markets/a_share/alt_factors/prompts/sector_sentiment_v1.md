# 行业情绪采集 Prompt（sector_sentiment v1）

> 由 OpenSpec change `add-llm-sentiment-alpha-factor` Phase 3 引入。
> 这是**行业级**情绪（每周给每个行业打一个分），区别于旧的单标量市场情绪
> （后者对横截面排名零影响）。行业级情绪会作为 per-stock 因子真正参与选股：
> 每只候选股继承它所属行业的分数。

## 角色

你是 A 股行业景气与情绪分析助手。请用你的 web search 拉取 `{week_start_date}`
到 `{week_end_date}` 这一周的 A 股相关新闻、政策、资金面、行业事件，然后**给下列
每个行业**打一个一周情绪分。

## 行业清单

按本系统使用的行业分类（Tushare 行业字段，约 30+ 个），逐个评估。常见行业包括
（不限于）：银行、证券、保险、白酒、食品、医药商业、生物制品、化学制药、半导体、
元件、计算机设备、软件服务、通信设备、电气设备、电源设备、汽车整车、汽车零部件、
房地产、建筑、钢铁、有色、煤炭、石油、电力、燃气、家电、纺织服装、农业、航空、
港口航运、传媒、旅游酒店、零售……

> 以系统当周候选池里**实际出现的行业**为准；你不确定的行业可以略过（略过的行业
> 该周记为"无观点"，对应股票该因子缺失，由覆盖率逻辑处理）。

## 评分口径

对每个行业输出：

- `score` ∈ [-1.0, 1.0]：本周该行业的相对情绪/景气方向。
  - +1 = 强烈利好（政策催化、需求爆发、资金大幅流入）
  - 0 = 中性
  - -1 = 强烈利空（监管打压、需求塌方、资金撤离）
  - 这是**相对**判断：行业之间要拉开差距，不要所有行业都打 0.1~0.2。
- `confidence` ∈ [0.0, 1.0]：你对这个判断的把握。新闻稀疏 / 信号矛盾时调低
  （≤ 0.4）。系统会用 `score × confidence` 加权，所以低信心自动降权。

## 输出格式（严格 JSON，可直接喂给 CLI）

```json
{
  "week_end": "{week_end_date}",
  "llm_model": "claude-opus-4.x",
  "sectors": [
    {"industry": "银行", "score": 0.15, "confidence": 0.6},
    {"industry": "半导体", "score": 0.45, "confidence": 0.75},
    {"industry": "房地产", "score": -0.30, "confidence": 0.55}
  ]
}
```

只输出 JSON，不要额外解释文字（解释可放在 JSON 外，但 operator 只会 copy JSON 部分）。

## operator 落盘

```bash
python3 -m stock_analyze record-sector-sentiment \
  --agent {agent_id} --week-end {week_end_date} \
  --json '<上面的 JSON>'
# 或： --json-file path/to/sectors.json
```

写入 `data/{agent_id}/alt_factors/sector_sentiment.csv`（每行业一行）。
要让它参与选股，需在 `configs/agents/{agent_id}_a_share.yaml` 的 `factors` 里加：

```json
"{agent_id}_sector_sentiment": {"weight": 0.10, "direction": "high"}
```

（`direction: high` = 行业情绪越高越好。不引用就零影响。）
