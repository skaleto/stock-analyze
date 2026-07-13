export type FieldFormat = "text" | "number" | "integer" | "percent" | "money" | "date";

export type FieldDefinition = {
  label: string;
  explanation: string;
  format: FieldFormat;
};

const FIELDS: Record<string, FieldDefinition> = {
  account_id: { label: "账户", explanation: "该持仓或订单所属的模拟账户。", format: "text" },
  account_label: { label: "账户", explanation: "该持仓或订单所属的模拟账户。", format: "text" },
  code: { label: "证券代码", explanation: "交易所使用的证券标识。", format: "text" },
  name: { label: "证券名称", explanation: "股票或基金的简称。", format: "text" },
  side: { label: "交易方向", explanation: "买入表示增加仓位，卖出表示减少仓位。", format: "text" },
  side_label: { label: "交易方向", explanation: "买入表示增加仓位，卖出表示减少仓位。", format: "text" },
  shares: { label: "份额", explanation: "模拟持有或计划成交的证券数量。", format: "integer" },
  available_shares: { label: "可用份额", explanation: "当前可以卖出的证券数量。", format: "integer" },
  price: { label: "成交价", explanation: "模拟成交时使用的价格。", format: "number" },
  last_price: { label: "最新价", explanation: "最近一次用于估值的市场价格。", format: "number" },
  avg_cost: { label: "持仓成本", explanation: "当前仓位的平均买入成本。", format: "number" },
  market_value: { label: "持仓市值", explanation: "最新价乘以持有份额。", format: "money" },
  target_value: { label: "目标金额", explanation: "订单希望达到的模拟持仓金额。", format: "money" },
  target_weight: { label: "目标权重", explanation: "该证券计划占账户资产的比例。", format: "percent" },
  current_weight: { label: "当前权重", explanation: "该证券当前占账户资产的比例。", format: "percent" },
  unrealized_pnl: { label: "浮动盈亏", explanation: "尚未卖出部分按最新价格计算的盈亏。", format: "money" },
  net_amount: { label: "成交净额", explanation: "计入费用后的模拟成交金额。", format: "money" },
  gross_amount: { label: "成交金额", explanation: "未计费用前的模拟成交金额。", format: "money" },
  commission: { label: "佣金", explanation: "模拟交易计入的佣金成本。", format: "money" },
  stamp_tax: { label: "印花税", explanation: "模拟卖出交易计入的印花税。", format: "money" },
  slippage: { label: "滑点", explanation: "模拟成交价相对市场价的执行偏差。", format: "money" },
  score: { label: "综合评分", explanation: "策略将多个因子标准化并加权后的选股分数。", format: "number" },
  execute_after: { label: "计划执行日", explanation: "订单最早可以模拟成交的交易日。", format: "date" },
  trade_date: { label: "成交日期", explanation: "模拟交易实际记账日期。", format: "date" },
  date: { label: "日期", explanation: "该条数据对应的交易日期。", format: "date" },
  reason: { label: "策略原因", explanation: "系统生成这笔订单或持仓的原因摘要。", format: "text" },
  exposure_group: { label: "底层市场", explanation: "跨境 ETF 实际跟踪资产所在的市场。", format: "text" },
  theme: { label: "跟踪主题", explanation: "基金主要跟踪的指数、行业或资产主题。", format: "text" },
  industry: { label: "行业", explanation: "A 股公司所属行业。", format: "text" },
  pe: { label: "市盈率 PE", explanation: "股价相对每股盈利的倍数，通常越低越便宜，但需要结合行业和增长判断。", format: "number" },
  pb: { label: "市净率 PB", explanation: "股价相对每股净资产的倍数，反映市场给公司资产的定价。", format: "number" },
  roe: { label: "净资产收益率 ROE", explanation: "公司用股东投入的净资产创造利润的效率，通常越高越好。", format: "percent" },
  gross_margin: { label: "毛利率", explanation: "销售收入扣除直接成本后的利润比例，体现产品盈利空间。", format: "percent" },
  debt_ratio: { label: "资产负债率", explanation: "负债占总资产的比例，过高可能意味着偿债压力更大。", format: "percent" },
  net_profit_growth: { label: "净利润增速", explanation: "净利润相较上一期的变化速度，用来观察盈利是否改善。", format: "percent" },
  dividend_yield: { label: "股息率", explanation: "过去一年现金分红相对股价的比例。", format: "percent" },
  momentum_20: { label: "近20日涨跌", explanation: "最近20个交易日的价格变化，正值表示近期走势偏强。", format: "percent" },
  momentum_60: { label: "近60日涨跌", explanation: "最近60个交易日的价格变化，用来观察中期趋势。", format: "percent" },
  low_volatility_60: { label: "近60日波动率", explanation: "最近60个交易日涨跌的离散程度，数值越低通常越稳定。", format: "percent" },
  avg_amount_20: { label: "20日平均成交额", explanation: "最近20个交易日的平均成交金额，用来判断流动性。", format: "money" },
  discount_premium: { label: "折溢价率", explanation: "ETF市场价格相对基金净值的偏离，正值为溢价、负值为折价。", format: "percent" },
  roic: { label: "投入资本回报率 ROIC", explanation: "经营利润相对投入资本的回报。", format: "percent" },
  net_profit_margin: { label: "净利率", explanation: "每一元收入最终形成净利润的比例。", format: "percent" },
  cash_conversion: { label: "经营现金转化率", explanation: "经营现金流相对收入的比例。", format: "percent" },
  accrual_ratio: { label: "应计比率", explanation: "利润与经营现金流差额相对资产的比例。", format: "percent" },
  high_value_add_proxy: { label: "高附加值代理", explanation: "综合盈利、现金与研发质量的研究指标。", format: "percent" },
  declining_marginal_cost_proxy: { label: "边际成本递减代理", explanation: "收入增长相对成本和经营利润变化的研究指标。", format: "percent" },
  profit_pool_concentration: { label: "业务集中度", explanation: "按主营业务收入计算的集中度。", format: "percent" },
  premium_persistence_20: { label: "20日平均折溢价", explanation: "近20个交易日价格相对净值偏离的平均水平。", format: "percent" },
  tracking_difference_20: { label: "20日跟踪差", explanation: "ETF价格涨跌与基金净值涨跌之间的差异。", format: "percent" },
  tracking_error_20: { label: "20日跟踪误差", explanation: "ETF日收益偏离净值日收益的年化波动。", format: "percent" },
  fund_share_change_20: { label: "20日份额变化", explanation: "基金份额近20个交易日的变化。", format: "percent" },
  run_id: { label: "运行编号", explanation: "一次任务运行的唯一标识。", format: "text" },
  command: { label: "运行任务", explanation: "系统实际执行的任务类型。", format: "text" },
  status: { label: "状态", explanation: "任务、订单或成交当前所处的状态。", format: "text" },
  started_at: { label: "开始时间", explanation: "任务开始运行的时间。", format: "date" },
  finished_at: { label: "完成时间", explanation: "任务结束运行的时间。", format: "date" },
  duration_ms: { label: "耗时", explanation: "任务从开始到结束消耗的毫秒数。", format: "number" },
};

const ACCOUNT_LABELS: Record<string, string> = {
  hs300: "沪深300账户",
  zz500: "中证500账户",
  us_exposure: "美国市场ETF账户",
  hk_exposure: "香港市场ETF账户",
};

const SIDE_LABELS: Record<string, string> = { buy: "买入", sell: "卖出" };
const EVENT_LABELS: Record<string, string> = {
  macd_golden_cross: "MACD金叉", macd_death_cross: "MACD死叉",
  macd_zero_cross_up: "MACD上穿零轴", macd_zero_cross_down: "MACD下穿零轴",
  macd_hist_reversal_up: "MACD柱转强", macd_hist_reversal_down: "MACD柱转弱",
  macd_hist_sign_up: "MACD柱翻正", macd_hist_sign_down: "MACD柱翻负",
  ma_golden_cross_5_20: "5日均线上穿20日均线", ma_death_cross_5_20: "5日均线下穿20日均线",
  price_breakout_20: "突破20日高点", price_breakdown_20: "跌破20日低点",
  rsi_oversold_exit: "RSI离开超卖区", rsi_overbought_exit: "RSI离开超买区",
  adx_trend_strengthening: "趋势强度增强", adx_trend_weakening: "趋势强度减弱",
  bollinger_breakout_up: "向上突破布林带", bollinger_breakout_down: "向下突破布林带",
  bollinger_squeeze: "布林带收窄", macd_bearish_divergence: "MACD顶背离",
  macd_bullish_divergence: "MACD底背离", flow_price_confirmation_up: "资金与价格同步转强",
  flow_price_confirmation_down: "资金与价格同步转弱", flow_price_divergence_bearish: "价升资金背离",
  flow_price_divergence_bullish: "价跌资金改善", industry_breadth_reversal_up: "行业宽度转强",
  industry_breadth_reversal_down: "行业宽度转弱", volume_price_rise_confirmed: "量增价升",
  volume_price_rise_divergent: "量缩价升", volume_price_fall_confirmed: "量增价跌",
  volume_price_fall_exhausting: "量缩价跌", volume_expansion_flat_price: "放量滞涨",
  volume_contraction_flat_price: "缩量盘整",
};

export function fieldMeta(key: string): FieldDefinition {
  return FIELDS[key] ?? { label: key, explanation: "该字段暂未配置中文说明。", format: "text" };
}

export function accountLabel(value: string): string {
  return ACCOUNT_LABELS[value] ?? (value || "未分账户");
}

export function sideLabel(value: string): string {
  return SIDE_LABELS[value.toLowerCase()] ?? (value || "-");
}

export function eventLabel(value: unknown): string {
  const key = String(value ?? "");
  return EVENT_LABELS[key] ?? (key || "-");
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

export function formatMoney(value: unknown, currency = "¥"): string {
  const number = finiteNumber(value);
  if (number === null) return "-";
  return `${currency}${number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatPercent(value: unknown): string {
  const number = finiteNumber(value);
  if (number === null) return "-";
  return `${(number * 100).toFixed(2)}%`;
}

export function formatFieldValue(key: string, value: unknown, currency = "¥"): string {
  if (value === null || value === undefined || value === "") return "-";
  if (key === "side" || key === "side_label") return sideLabel(String(value));
  if (key === "account_id" || key === "account_label") return accountLabel(String(value));
  if (key === "reason") return formatStrategyReason(value);
  const definition = fieldMeta(key);
  const number = finiteNumber(value);
  if (definition.format === "percent") return formatPercent(value);
  if (definition.format === "money") {
    if (key === "avg_amount_20" && number !== null) {
      return `${(number / 10_000).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}万`;
    }
    return formatMoney(value, currency);
  }
  if (definition.format === "integer" && number !== null) return Math.round(number).toLocaleString("zh-CN");
  if (definition.format === "number" && number !== null) {
    return number.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  }
  return String(value);
}

export function visibleRowEntries(row: Record<string, unknown>): [string, unknown][] {
  const hidden = new Set(["account_label", "side_label", "status_label", "event_type"]);
  return Object.entries(row).filter(([key]) => !hidden.has(key));
}

export function formatStrategyReason(reason: unknown): string {
  const text = String(reason ?? "").trim();
  if (!text) return "策略调仓";
  const translated = text.split(/\s*;\s*/).map((part) => {
    const match = part.match(/^([a-z0-9_]+)=([+-]?\d+(?:\.\d+)?)$/i);
    if (!match) return part;
    const [, key, rawValue] = match;
    const metadata = fieldMeta(key);
    if (metadata.label === key) return part;
    const value = Number(rawValue);
    const formatted = metadata.format === "percent" ? formatPercent(value) : formatFieldValue(key, value);
    const sign = rawValue.startsWith("+") && !formatted.startsWith("-") ? "+" : "";
    return `${metadata.label} ${sign}${formatted}`;
  });
  return translated.join(" · ");
}
