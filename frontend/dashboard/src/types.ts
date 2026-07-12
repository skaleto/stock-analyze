export type AgentId = string;

export type TaskStatus = {
  status?: string;
  started_at?: string | null;
  finished_at?: string | null;
  error_summary?: string | null;
};

export type StrategyMetrics = {
  season_return: number | null;
  benchmark_return: number | null;
  excess_return: number | null;
  annualized_volatility: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  cash_ratio: number | null;
  turnover: number | null;
  trading_cost: number | null;
  cost_bps: number | null;
  position_count: number;
  pending_order_count: number;
  trade_count: number;
};

export type StrategyAllocation = {
  label: string;
  value: number;
  weight: number | null;
};

export type SelectionStage = {
  key: string;
  label: string;
  count: number;
};

export type SelectionScope = {
  universe_hash?: string | null;
  stages: SelectionStage[];
  rejections: { reason: string; count: number }[];
  data_gaps?: Record<string, number>;
  ranked?: Record<string, unknown>[];
  selected: Record<string, unknown>[];
  recent_events?: FundEventRow[];
  active_hard_blocks?: number;
};

export type SelectionSnapshot = {
  schema_version: number;
  as_of?: string | null;
  universe_hash?: string | null;
  universe_source_status?: string | null;
  catalog_stats?: Record<string, Record<string, number>>;
  scopes: Record<string, SelectionScope>;
};

export type FundEventRow = {
  event_id?: string;
  code?: string;
  name?: string;
  title?: string;
  event_type?: string;
  severity?: string;
  published_at?: string;
  source_url?: string;
};

export type ShadowMetric = {
  asset_class?: string;
  scope?: string;
  factor_model?: string;
  cumulative_return?: number | null;
  sharpe_ratio?: number | null;
  max_drawdown?: number | null;
  promotion_status?: string;
};

export type QDIIResearch = {
  capacity?: {
    run_id?: string;
    recommendations?: { strategy?: string; scope?: string; recommended_top_n?: number | null }[];
    metrics?: Record<string, unknown>[];
  };
  shadow?: {
    run_id?: string;
    mode?: string;
    metrics?: ShadowMetric[];
    catalog?: Record<string, unknown>[];
    skipped_scopes?: { scope?: string; reason?: string }[];
  };
  events?: {
    total?: number;
    active_hard_blocks?: number;
    latest_observed_at?: string | null;
    source?: string;
    rows?: FundEventRow[];
  };
  theme_sentiment?: {
    agent?: string;
    week_end?: string;
    index_key?: string;
    score?: number;
    confidence?: number;
    drivers?: string;
    sources?: string;
    observed_at?: string;
  }[];
};

export type ExposureWeight = {
  label: string;
  weight: number;
};

export type UnderlyingCompany = {
  symbol: string;
  name: string;
  sector: string;
  weight: number;
};

export type PortfolioLookthrough = {
  status: "complete" | "partial" | "unavailable" | string;
  source: string;
  profile_coverage: number;
  company_weight_coverage: number;
  indexes: { index_key: string; label: string; weight: number; profile_available: boolean }[];
  countries: ExposureWeight[];
  sectors: ExposureWeight[];
  companies: UnderlyingCompany[];
  company_symbols: string[];
  sources: { index_key: string; name?: string; as_of?: string; source_url?: string; source_label?: string }[];
  unsupported_indexes: string[];
};

export type IndexProfile = {
  index_key: string;
  name: string;
  country?: string;
  as_of: string;
  source_url: string;
  source_label?: string;
  constituents: { symbol: string; name: string; sector?: string; weight?: number | null }[];
  sector_weights?: ExposureWeight[];
};

export type StrategyComparisonSide = {
  agent: string;
  label: string;
  description: string;
  color: string;
  strategy_id?: string | null;
  strategy_name?: string | null;
  holdings_source: "positions" | "planned_orders" | string;
  allocations: StrategyAllocation[];
  lookthrough?: PortfolioLookthrough | Record<string, never>;
  research?: QDIIResearch;
  metrics: StrategyMetrics;
};

export type StrategyComparisonPoint = {
  date: string;
  claude: number | null;
  codex: number | null;
  benchmark: number | null;
};

export type StrategyComparisonFactor = {
  key: string;
  label: string;
  explanation: string;
  claude: { weight: number; direction: string | null };
  codex: { weight: number; direction: string | null };
};

export type StrategyComparison = {
  market: string;
  season: {
    id: string;
    name: string;
    effective_date: string;
    anchor_date: string | null;
  };
  strategies: {
    claude: StrategyComparisonSide;
    codex: StrategyComparisonSide;
  };
  pair: {
    position_overlap: number | null;
    underlying_index_overlap: number | null;
    underlying_company_overlap: number | null;
    weighted_company_overlap: number | null;
    return_correlation: number | null;
    factor_distance: number | null;
    factor_distance_floor: number | null;
  };
  nav_series: StrategyComparisonPoint[];
  factor_rows: StrategyComparisonFactor[];
};

export type SummaryAgent = {
  agent: AgentId;
  strategy?: StrategyComparisonSide;
  nav: {
    latest: number | null;
    latest_display: string;
    date: string | null;
    return: number | null;
    return_display: string;
  };
  decision: {
    href: string;
    pending_orders: { total: number; buy: number; sell: number };
    weekly_report_href: string | null;
  };
  tasks: {
    daily: TaskStatus;
    weekly: TaskStatus;
  };
};

export type MarketSummary = {
  market: string;
  label: string;
  currency: string;
  agents: SummaryAgent[];
  comparison?: StrategyComparison | null;
  monthly: { status?: string; href?: string | null; label?: string | null };
};

export type DashboardSummary = {
  generated_at: string;
  markets: MarketSummary[];
  sentiment: unknown[];
};

export type NavPoint = {
  date: string;
  cash?: number | null;
  market_value?: number | null;
  total_value?: number | null;
  total_value_display?: string;
  return?: number | null;
  return_display?: string;
  benchmark_code?: string | null;
  benchmark_codes?: string[];
  benchmark_close?: number | null;
  benchmark_date?: string | null;
  benchmark_return?: number | null;
  benchmark_coverage?: number | null;
};

export type OrderRow = Record<string, string | number | null | undefined> & {
  account_id?: string;
  code?: string;
  name?: string;
  side?: string;
  shares?: number;
  target_weight?: number;
  target_value?: number;
  trade_date?: string;
  score?: number;
  execute_after?: string;
  reason?: string;
  exposure_group?: string;
  theme?: string;
  index_key?: string;
  country?: string;
  sector?: string;
  industry?: string;
  account_label?: string;
  side_label?: string;
  market_value?: number;
  unrealized_pnl?: number;
  last_price?: number;
  avg_cost?: number;
  price?: number;
  net_amount?: number;
  status?: string;
  status_label?: string;
  date?: string;
  command?: string;
  started_at?: string;
  duration_ms?: number;
  run_id?: string;
};

export type StrategyFactor = {
  key: string;
  label: string;
  explanation: string;
  weight: number;
  direction: string;
  direction_label: string;
};

export type StrategyProfile = {
  agent: string;
  agent_label: string;
  strategy_id?: string | null;
  name: string;
  factors: StrategyFactor[];
};

export type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  amount?: number | null;
};

export type InstrumentMetric = {
  key: string;
  label: string;
  explanation: string;
  value: number;
  format: "percent" | "money" | "number" | string;
};

export type InstrumentDetail = {
  generated_at: string;
  market: string;
  agent: string;
  instrument: {
    code: string;
    name?: string | null;
    exposure_group?: string;
    theme?: string;
    index_key?: string;
  };
  underlying?: IndexProfile | null;
  latest: (Candle & { change_pct?: number | null }) | null;
  candles: Candle[];
  metrics: InstrumentMetric[];
  related_trades: OrderRow[];
  warning?: string | null;
};

export type DashboardDetail = {
  generated_at: string;
  market: string;
  market_label: string;
  currency: string;
  agent: string;
  strategy: StrategyProfile;
  selection?: SelectionSnapshot;
  lookthrough?: PortfolioLookthrough | Record<string, never>;
  research?: QDIIResearch;
  nav: {
    latest: NavPoint | null;
    series: NavPoint[];
    accounts: Record<string, unknown>[];
    benchmark_codes?: string[];
    benchmark_label?: string;
  };
  activity: {
    summary: { total: number };
    rows: OrderRow[];
  };
  orders: {
    summary: { total: number; buy: number; sell: number };
    rows: OrderRow[];
  };
  positions: {
    summary: { total: number; market_value?: number; market_value_display?: string };
    rows: OrderRow[];
  };
  trades: {
    summary: { total: number };
    rows: OrderRow[];
  };
  runs: {
    summary: { total: number };
    rows: OrderRow[];
  };
  weekly_report: {
    exists: boolean;
    href: string | null;
    markdown: string;
  };
};
