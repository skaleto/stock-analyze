import { fieldMeta } from "./finance";

export type TermKind =
  | "source"
  | "family"
  | "feature"
  | "factor"
  | "algorithm"
  | "metric";

export type TermDefinition = {
  label: string;
  explanation: string;
  known: boolean;
};

type KnownTerm = Omit<TermDefinition, "known">;

const term = (label: string, explanation: string): KnownTerm => ({
  label,
  explanation,
});

const SOURCES: Record<string, KnownTerm> = {
  adjusted_ohlcv: term("复权行情数据", "证券的开盘、最高、最低、收盘、成交量和成交额，并按除权除息事件修正。"),
  market: term("行情价格数据", "用于计算收益、波动、趋势和成交活跃度的日频市场行情。"),
  announced_financials_and_industry_membership: term("已披露财务与行业归属", "只使用当时已经公开的财务报表和行业分类，避免未来数据泄漏。"),
  tushare_daily_basic: term("Tushare 每日估值指标", "每日市盈率、市净率、换手率、市值等估值与交易指标。"),
  tushare_fina_indicator_announced: term("Tushare 公告口径财务指标", "按公告日期可获得的盈利能力、成长性和偿债能力指标。"),
  tushare_financial_announced: term("Tushare 公告口径财务报表", "按公告时间对齐的利润表、资产负债表和现金流量表。"),
  tushare_anns_d: term("Tushare 全量公告", "来自 Tushare 的上市公司公告目录与原始文件。"),
  tushare_announcement: term("Tushare 全量公告", "来自 Tushare 的上市公司公告目录与原始文件。"),
  ifind: term("iFinD 资讯", "同花顺 iFinD 提供的资讯和结构化市场信息。"),
  ifind_announcement: term("iFinD 公告核验", "用于补充和交叉核验上市公司公告。"),
  cninfo: term("巨潮资讯公告", "来自巨潮资讯网的上市公司法定披露信息。"),
  gov_policy: term("国家政策文件", "来自中国政府公开渠道的宏观与产业政策。"),
  ndrc_policy: term("发改委政策文件", "来自国家发展改革委的产业、价格和投资政策。"),
  announcement: term("公司公告", "上市公司发布的业绩、合同、回购、处罚等正式披露文本。"),
  news: term("新闻资讯", "与公司、行业或宏观环境相关的新闻文本。"),
  policy: term("政策信息", "可能影响行业和公司的政策、监管与产业规划信息。"),
  events: term("结构化事件", "从公告和资讯中识别并通过校验的标准事件。"),
  balancesheet: term("资产负债表", "公司的资产、负债和所有者权益数据。"),
  income: term("利润表", "公司的营业收入、成本和利润数据。"),
  fina_indicator: term("财务分析指标", "由财务报表派生的盈利、成长、现金流和偿债指标。"),
  finance: term("财务数据", "公司财务报表和由报表计算的分析指标。"),
  daily_basic: term("每日估值与交易指标", "每日市盈率、市净率、换手率和市值等数据。"),
  moneyflow: term("个股资金流", "按大中小单统计的主动买卖和资金净流入数据。"),
  margin: term("融资融券汇总", "市场层面的融资余额、融券余额和交易规模。"),
  margin_detail: term("融资融券明细", "证券层面的融资买入、偿还和融券交易明细。"),
  hsgt_top10: term("沪深港通活跃成交", "北向与南向交易中成交活跃证券的统计数据。"),
  index_classify: term("行业指数分类", "申万等行业分类的层级与代码定义。"),
  index_member_all: term("行业指数成分", "证券在不同时间所属行业指数的成员关系。"),
  index_global: term("全球市场指数", "海外主要股票市场指数的行情数据。"),
  fund_nav: term("基金净值", "基金单位净值和累计净值，用于计算折溢价与跟踪偏差。"),
  fund_share: term("基金份额", "基金流通份额变化，用于观察申购赎回和资金流向。"),
  fx_daily: term("汇率行情", "人民币与外币的日频汇率，用于跨境资产收益换算。"),
  audit: term("点时审计证据", "记录特征是否严格使用当时可获得的数据。"),
  cn_cpi: term("中国居民消费价格", "CPI 反映居民消费品和服务价格的同比与环比变化。"),
  cn_pmi: term("中国采购经理指数", "PMI 用于观察制造业和服务业景气度。"),
  cn_ppi: term("中国工业生产者价格", "PPI 反映工业品出厂价格变化和上游成本压力。"),
  cn_m: term("中国货币供应量", "M0、M1、M2 等货币供应量及其变化。"),
  shibor: term("上海银行间同业拆借利率", "反映人民币市场短期资金价格和流动性状况。"),
  shibor_lpr: term("贷款市场报价利率", "LPR 反映银行贷款定价基准及货币政策传导。"),
  us_tycr: term("美国国债收益率曲线", "不同期限美国国债收益率及期限利差，反映全球利率环境。"),
};

const FAMILIES: Record<string, KnownTerm> = {
  fundamental: term("基本面特征", "衡量估值、盈利质量、成长、现金流和偿债能力。"),
  industry_chain: term("产业链特征", "衡量行业景气、相对强弱、利润扩散和周期位置。"),
  macro_regime: term("宏观环境特征", "衡量经济景气、通胀、货币与利率环境。"),
  technical: term("技术面特征", "从价格、成交量和资金行为中刻画趋势、波动和交易节奏。"),
  intelligence: term("文本情报特征", "由公告、新闻和政策中的结构化事件计算得到。"),
};

const FEATURES: Record<string, KnownTerm> = {
  ad: term("累积派发线 A/D", "结合涨跌位置和成交量，观察资金是偏向吸筹还是派发。"),
  adx_14: term("14日趋势强度 ADX", "衡量趋势强弱，不直接判断上涨或下跌方向。"),
  atr_14: term("14日真实波幅 ATR", "衡量价格日内跳空和波动幅度。"),
  natr_14: term("14日标准化波幅", "ATR 除以价格，便于不同证券之间比较波动。"),
  mfi_14: term("14日资金流量指标 MFI", "综合价格和成交量判断资金流入流出及超买超卖。"),
  amount_ratio_5_20: term("5日/20日成交额比", "短期成交活跃度相对中期水平的变化。"),
  bollinger_position: term("布林带位置", "价格在布林带上下轨之间的位置。"),
  bollinger_width: term("布林带宽度", "布林带上下轨间距，反映波动扩张或收缩。"),
  gap_return: term("跳空收益", "当日开盘相对前一交易日收盘的价格变化。"),
  momentum_5: term("近5日涨跌", "最近5个交易日的价格变化，反映短期动量。"),
  macd_dif: term("MACD 快线 DIF", "短周期与长周期指数均线之差，用于观察趋势方向。"),
  macd_dea: term("MACD 慢线 DEA", "DIF 的平滑均线，用于判断趋势信号。"),
  macd_hist: term("MACD 柱值", "MACD 快线 DIF 与慢线 DEA 的差值，反映趋势动能强弱。"),
  macd_cross: term("MACD 交叉状态", "记录快线与慢线金叉、死叉或无交叉状态。"),
  macd_cross_age: term("MACD 交叉距今天数", "最近一次 MACD 交叉发生后经过的交易日数。"),
  macd_hist_slope: term("MACD 柱变化斜率", "衡量 MACD 动能近期增强或减弱的速度。"),
  macd_hist_acceleration: term("MACD 柱加速度", "衡量 MACD 动能变化速度是否继续加快。"),
  macd_zero_state: term("MACD 零轴位置", "DIF 位于零轴上方或下方，表示中期趋势环境。"),
  industry_breadth: term("行业上涨宽度", "行业内上涨证券占比，反映行情参与范围。"),
  industry_momentum_20: term("行业20日动量", "所属行业近20个交易日的整体涨跌。"),
  industry_relative_momentum_20: term("行业相对动量", "所属行业相对全市场近20日的强弱。"),
  industry_volatility_20: term("行业20日波动", "所属行业近20个交易日收益的波动程度。"),
  industry_cycle_score: term("行业周期位置", "综合景气、价格和盈利变化判断行业周期阶段。"),
  industry_earnings_diffusion: term("行业盈利扩散", "行业内盈利改善公司的占比及扩散程度。"),
  business_profit_margin: term("主营业务利润率", "主营业务利润相对主营业务收入的比例。"),
  gross_profit_to_assets: term("毛利润/总资产", "衡量公司资产创造毛利润的效率。"),
  free_cashflow_to_assets: term("自由现金流/总资产", "衡量资产转化为可自由支配现金流的能力。"),
  growth_acceleration: term("增长加速度", "观察收入或利润增速是否进一步改善。"),
  cost_growth: term("成本增速", "营业成本相对上一期的增长速度。"),
  cpi_change: term("通胀变化", "居民消费价格增速相对上一期的变化。"),
  m2_change: term("M2增速变化", "广义货币供应量增速相对上一期的变化。"),
};

const FACTORS: Record<string, KnownTerm> = {
  event_net_strength_5d: term("5日事件净强度", "近5日正面与负面事件经过时间衰减后的综合强度。"),
  event_net_materiality_20d: term("20日事件净重要性", "近20日正负事件对公司经营和估值的重要程度之差。"),
  event_relevance_20d: term("20日事件相关性", "近20日事件与目标公司和投资判断的相关程度。"),
  event_certainty_20d: term("20日事件确定性", "近20日事件事实是否明确、已发生或具有可靠依据。"),
  event_revision_risk_20d: term("20日事件修订风险", "公告后续被更正、撤回或发生重大变化的风险。"),
  announcement_novelty_20d: term("20日公告新颖度", "近20日公告相对既有信息新增了多少有效内容。"),
  event_source_confirmation: term("多来源确认度", "同一事件是否得到多个独立数据来源交叉确认。"),
  event_data_coverage: term("事件数据覆盖率", "有效事件数据覆盖证券和日期样本的比例。"),
  event_positive_decay_5d: term("5日正面事件衰减", "正面事件影响随时间递减后的剩余强度。"),
  event_negative_decay_5d: term("5日负面事件衰减", "负面事件影响随时间递减后的剩余强度。"),
  event_materiality_positive_20d: term("20日正面事件重要性", "近20日正面事件对经营和估值的潜在影响。"),
  event_materiality_negative_20d: term("20日负面事件重要性", "近20日负面事件对经营和估值的潜在影响。"),
  event_price_volume_confirmation: term("事件量价确认", "公告事件发生后是否得到价格和成交量行为的印证。"),
  earnings_event_score_20d: term("20日业绩事件得分", "业绩预告、快报和定期报告事件的方向与强度。"),
  buyback_event_score_20d: term("20日回购事件得分", "股份回购事件的规模、进度和确定性综合得分。"),
  contract_event_score_60d: term("60日合同事件得分", "重大合同或订单对未来经营影响的综合得分。"),
  corporate_action_event_score_60d: term("60日公司行动得分", "分红、并购、重组等公司行动的综合影响。"),
  capital_structure_event_score_60d: term("60日资本结构事件得分", "增发、减持、质押等资本结构变化的综合影响。"),
  legal_risk_event_score_60d: term("60日法律风险得分", "诉讼、仲裁、调查和处罚事件的负面风险。"),
  delisting_risk_event_score_60d: term("60日退市风险得分", "风险警示、财务异常和退市相关事件的风险程度。"),
  policy_event: term("政策事件信号", "政策变化对公司或行业可能产生的方向性影响。"),
};

const ALGORITHMS: Record<string, KnownTerm> = {
  boosting_ensemble: term("提升树集成模型", "组合多棵逐步纠错的决策树，学习非线性特征关系。"),
  gradient_boosting: term("梯度提升模型", "通过逐轮修正预测误差训练的树模型。"),
  lightgbm: term("LightGBM 提升树", "适合表格数据的高效梯度提升树模型。"),
  logistic_regression: term("逻辑回归", "输出上涨概率的线性分类基线模型。"),
  temporal_deep_model: term("时序深度模型", "从多期特征序列中学习时间依赖关系。"),
};

const METRICS: Record<string, KnownTerm> = {
  rank_ic: term("排序相关性", "预测排序与未来实际收益排序的相关程度，绝对值越大说明排序能力越强。"),
  icir: term("预测稳定性", "Rank IC 的均值相对其波动，越高表示预测能力越稳定。"),
  brier_score: term("概率误差", "预测概率与实际结果的均方误差，越低越好。"),
  auc: term("分类区分能力", "模型把上涨样本排在下跌样本之前的能力，越高越好。"),
  sharpe: term("风险调整收益", "单位波动所获得的超额收益，越高通常越好。"),
};

const REGISTRIES: Record<TermKind, Record<string, KnownTerm>> = {
  source: SOURCES,
  family: FAMILIES,
  feature: FEATURES,
  factor: FACTORS,
  algorithm: ALGORITHMS,
  metric: METRICS,
};

const unknownLabels: Record<TermKind, string> = {
  source: "未收录来源",
  family: "未收录分组",
  feature: "未收录特征",
  factor: "未收录因子",
  algorithm: "未收录算法",
  metric: "未收录指标",
};

const unknownNouns: Record<TermKind, string> = {
  source: "来源",
  family: "分组",
  feature: "特征",
  factor: "因子",
  algorithm: "算法",
  metric: "指标",
};

export function termMeta(code: string, kind: TermKind): TermDefinition {
  const normalized = String(code ?? "").trim();
  const explicit = REGISTRIES[kind][normalized];
  if (explicit) return { ...explicit, known: true };

  if (kind === "feature" || kind === "factor") {
    const financial = fieldMeta(normalized);
    if (financial.label !== normalized) {
      return {
        label: financial.label,
        explanation: financial.explanation,
        known: true,
      };
    }
  }

  return {
    label: unknownLabels[kind],
    explanation: `该${unknownNouns[kind]}尚未配置中文说明，请结合原始编码追溯。`,
    known: false,
  };
}
