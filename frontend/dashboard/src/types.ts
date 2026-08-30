export type AgentId = string;

export type PermanentPortfolioMetricSet = {
  cumulative_return?: number | null;
  annualized_return?: number | null;
  annualized_volatility?: number | null;
  sharpe_vs_cash?: number | null;
  sortino_vs_cash?: number | null;
  max_drawdown?: number | null;
  calmar?: number | null;
  annualized_turnover?: number | null;
  total_cost?: number | null;
};

export type PermanentPortfolioSeriesPoint = {
  date: string;
  normalized_nav?: number | null;
  drawdown?: number | null;
  volatility_63d?: number | null;
};

export type PermanentPortfolioNav = {
  date?: string;
  cash?: number;
  market_value?: number;
  total_value?: number;
  strategy?: string;
};

export type PermanentPortfolioPosition = {
  strategy?: string;
  role?: string;
  code?: string;
  shares?: number;
  last_price?: number;
  market_value?: number;
};

export type PermanentPortfolioTarget = {
  strategy?: string;
  role?: string;
  signal_date?: string;
  target_weight?: number;
  reason?: string;
};

export type PermanentPortfolioTrade = {
  signal_date?: string;
  trade_date?: string;
  strategy?: string;
  role?: string;
  code?: string;
  side?: string;
  shares?: number;
  price?: number;
  commission?: number;
  slippage_cost?: number;
};

export type PermanentPortfolioResult = {
  metrics?: PermanentPortfolioMetricSet;
  series?: PermanentPortfolioSeriesPoint[];
  nav?: PermanentPortfolioNav[];
  trades?: PermanentPortfolioTrade[];
  targets?: PermanentPortfolioTarget[];
  positions?: PermanentPortfolioPosition[];
  pending?: PermanentPortfolioTarget[];
};

export type PermanentPortfolioWindow = {
  status: string;
  start_date?: string;
  end_date?: string;
  stage_boundaries?: Array<{
    date?: string;
    before_label?: string;
    after_label?: string;
  }>;
  portfolios?: Record<string, PermanentPortfolioResult>;
};

export type PermanentPortfolioData = {
  schemaVersion: 1;
  generatedAt: string | null;
  status: string;
  study: {
    studyId: string;
    status: string;
    initialCash: number;
    contractSha256?: string | null;
    dataSha256?: string | null;
    developmentSha256?: string | null;
    holdoutSha256?: string | null;
    holdoutEnd?: string | null;
    forwardAsOf?: string | null;
  };
  assets: Array<{ role: string; code: string; name: string }>;
  strategies: Array<{ id: string; name: string }>;
  benchmarks: Array<{ id: string; name: string }>;
  windows: {
    historical: PermanentPortfolioWindow;
    forward: PermanentPortfolioWindow;
  };
  errors: string[];
};

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
  strategy_variant?: string;
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

export type MarketIntelligence = {
  market: string;
  agent: string;
};

export type IntelligenceDecision = "canonical" | "no_event" | "quarantined" | "failed";

export type IntelligenceDecisionRow = {
  decision_id: string;
  decision: IntelligenceDecision;
  document_id: number;
  event_type?: string | null;
  lifecycle?: string | null;
  issuer_name: string;
  issuer_code: string;
  event_subject?: string;
  title: string;
  effective_at: string;
  direction?: number | null;
  materiality?: number | null;
  relevance?: number | null;
  novelty?: number | null;
  confidence?: number | null;
  reason?: string | null;
};

export type IntelligenceSummary = {
  generated_at: string;
  market: string;
  agent: string;
  pipeline: {
    status: string;
    documents: number;
    artifacts: Record<string, number>;
    stages: {
      catalogued: number;
      pdfReady: number;
      parsed: number;
      semanticCompleted: number;
      canonicalEvents: number;
    };
    backlog: {
      download: number;
      parse: number;
      semantic: number;
      total: number;
    };
    artifactWorkers: {
      status: string;
      activeLeases: number;
      leasedDocuments: number;
      completedDocuments: number;
      downloadedDocuments: number;
      parsedDocuments: number;
      latestFinishedAt?: string | null;
      stages: Record<
        "download" | "parse",
        {
          leased: number;
          importing: number;
          imported: number;
          partial: number;
          failed: number;
          expired: number;
        }
      >;
    };
    snapshotGeneratedAt?: string | null;
    sources: {
      source: string;
      documents: number;
      latestPublishedAt?: string | null;
      lastIngestedAt?: string | null;
      freshnessStatus: string;
      latestRunStatus: string;
      fetched: number;
      inserted: number;
      error?: string;
      cursor?: string | null;
      cursorUpdatedAt?: string | null;
    }[];
  };
  extraction: {
    status: string;
    semanticRuns: Record<string, number>;
    decisions: Record<IntelligenceDecision, number>;
    latestBatch: {
      batchKey: string;
      profileId: string;
      provider: string;
      model: string;
      promptVersion: string;
      schemaVersion: string;
      taxonomyVersion: string;
      parserVersion: string;
      batchDate: string;
      startedAt?: string | null;
      finishedAt?: string | null;
      runs: number;
      succeeded: number;
      noEvent: number;
      quarantined: number;
      failed: number;
      deferred: number;
      remaining: number;
      inputTokens: number;
      outputTokens: number;
      costMicrounits: number;
      requestCount: number;
      validationRepairs: number;
      validationRepairFailures: number;
      successRate?: number | null;
      qualityStatus: string;
    } | null;
    contract: {
      profileId: string;
      promptVersion?: string;
      schemaVersion?: string;
      taxonomyVersion?: string;
      evidenceContract?: string;
    };
  };
  factorSupply: {
    status: string;
    snapshotDate?: string | null;
    rows: number;
    reportName?: string | null;
    factorSet?: string | null;
    factorSets: {
      name: string;
      state: string;
      features: string[];
    }[];
    factors: {
      name: string;
      state: string;
      coverage?: number | null;
      activationRate?: number | null;
      dailyIcCount?: number | null;
      meanRankIc?: number | null;
      icSignStability?: number | null;
      recommendation?: string | null;
      gateReasons: string[];
    }[];
    lifecycleCounts: Record<string, number>;
    suppliedFactors: number;
    modelEligible: boolean;
    modelEligibleFactors: string[];
  };
  modelImpact: {
    status: string;
    asOf?: string | null;
    snapshotDate?: string | null;
    reportName?: string | null;
    factorSet?: string | null;
    qualifiedHorizons: number;
    activation: string;
    adopted: boolean;
    activeFactors: string[];
    iterationFactors: string[];
    reason: string;
    horizons: {
      horizon: string;
      status: string;
      reason?: string | null;
      support: Record<string, number | null>;
      deltas: Record<string, number | null>;
      baseMetrics: Record<string, number | null>;
      candidateMetrics: Record<string, number | null>;
    }[];
  };
  decisions: Record<IntelligenceDecision, number>;
  rows?: IntelligenceDecisionRow[];
  rowsByDecision?: Record<IntelligenceDecision, IntelligenceDecisionRow[]>;
};

export type IntelligenceEventDetail = {
  generated_at: string;
  market: string;
  agent: string;
  decision: IntelligenceDecision;
  reason?: string | null;
  event: {
    event_id: string;
    event_type?: string | null;
    lifecycle?: string | null;
    effective_at: string;
  };
  issuer: { name: string; code: string; industry?: string };
  scores: {
    direction?: number | null;
    materiality?: number | null;
    relevance?: number | null;
    novelty?: number | null;
    confidence?: number | null;
  };
  versions: {
    model: string;
    prompt_version: string;
    schema_version: string;
    taxonomy_version: string;
    parser_version?: string | null;
    scoring_version?: string | null;
  };
  evidence: {
    evidence_id: string;
    chunk_id?: string;
    page_number: number;
    start_char?: number;
    end_char?: number;
    quote: string;
  }[];
  facts: {
    fact_name: string;
    ordinal?: number;
    raw_value?: string | null;
    numeric_value?: string | null;
    text_value?: string | null;
    unit?: string | null;
    currency?: string | null;
    period?: string | null;
    evidence_ids?: string[];
    provenance?: string;
  }[];
  document: {
    document_id: number;
    title: string;
    source?: string;
    source_url: string;
    published_at: string;
  };
};

export type IntelligenceDocumentDetail = {
  generated_at?: string;
  market?: string;
  agent?: string;
  document: {
    document_id: number;
    title: string;
    source_url: string;
    published_at?: string;
    status?: string;
  };
  security_links?: { ts_code: string; name: string; provenance: string }[];
  artifacts: {
    artifact_id: string;
    artifact_type: string;
    status: string;
    parser_version?: string;
    byte_size?: number;
    updated_at?: string;
  }[];
  decisions: IntelligenceDecisionRow[];
};

export type StrategyModelUsage = {
  market: string;
  agent: string;
  strategy_label: string;
  as_of?: string | null;
  status: string;
  applied_candidates: number;
  candidate_coverage: number;
  model_versions: Record<string, string>;
  fallback_reason: string;
  accounts: number;
};

export type SystemModelOverview = {
  market: string;
  market_label: string;
  iteration: ModelIterationStatus;
};

export type SystemOverviewError = {
  code:
    | "market_summary_read_unavailable"
    | "model_lineage_read_unavailable"
    | "strategy_model_usage_read_unavailable"
    | "intelligence_read_unavailable";
  section: "markets" | "models" | "strategy_model_usage" | "intelligence";
  market?: "a_share" | "cn_qdii_etf";
  message: string;
};

export type SystemOverviewData = {
  generated_at: string;
  markets: MarketSummary[];
  models: SystemModelOverview[];
  strategy_model_usage: StrategyModelUsage[];
  intelligence: Pick<
    IntelligenceSummary,
    "pipeline" | "extraction" | "factorSupply" | "modelImpact" | "decisions"
  > & {
    recentEvents: IntelligenceDecisionRow[];
  };
  errors: SystemOverviewError[];
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
    distinctness?: {
      status: "qualified" | "breached" | "insufficient_samples" | string;
      qualified: boolean;
      distinctness_score: number | null;
      weighted_position_overlap: number | null;
      return_correlation: number | null;
      daily_decision_agreement: number | null;
      factor_exposure_distance: number | null;
      turnover_style_distance: number | null;
      breaches: { metric?: string; reason?: string }[];
      sample_sizes?: Record<string, number>;
      thresholds?: Record<string, number>;
    };
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
  daily_return?: number | null;
  daily_return_display?: string;
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

export type ModelVersionSummary = {
  market?: string;
  horizon?: number;
  model_version?: string;
  display_version?: string;
  status?: string;
  status_label?: string;
  champion_model_version?: string | null;
  shadow_cycles?: number;
  shadow_cycles_remaining?: number;
  registered_at?: string | null;
  selected_at?: string | null;
  candidate_kind?: string;
  admission_grade?: string;
  source_campaign?: string;
  source_trial_id?: string;
  promotion_policy?: string;
};

export type ModelIterationHistory = ModelVersionSummary & {
  outcome?: string;
  ended_at?: string | null;
};

export type ModelDecisionFunnelStage = {
  key: string;
  label: string;
  count: number;
};

export type ModelDecisionNearMiss = {
  code: string;
  name?: string | null;
  confidence: number;
  p_up: number;
  p_down: number;
  expected_excess_return: number;
  failed_rules: string[];
};

export type ModelDecisionDiagnostics = {
  outcome: "cash" | "selected";
  summary: string;
  regime?: string;
  funnel: ModelDecisionFunnelStage[];
  near_misses: ModelDecisionNearMiss[];
};

export type ModelIterationStatus = {
  status?: string;
  label?: string;
  portfolio_label?: string;
  isolation?: string;
  source_agent?: string;
  source_type?: string;
  as_of?: string;
  prediction_as_of?: string | null;
  horizon?: number;
  model_version?: string;
  display_version?: string;
  model_versions?: string[];
  decision_changed?: boolean;
  candidate_rows?: number;
  eligible_rows?: number;
  selected_count?: number;
  invalidated_rows?: number;
  minimum_confidence?: number;
  cash_only?: boolean;
  cash_reason?: string | null;
  decision_diagnostics?: ModelDecisionDiagnostics | null;
  trades_executed?: number;
  pending_orders?: number;
  updated_at?: string;
  candidate?: ModelVersionSummary | null;
  champion?: ModelVersionSummary | null;
  version_history?: ModelIterationHistory[];
};

export type ModelShadowStatus = ModelIterationStatus;

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

export type PredictionRow = {
  as_of?: string;
  code: string;
  name?: string;
  horizon: number;
  p_up: number;
  p_flat: number;
  p_down: number;
  confidence: number;
  expected_absolute_return?: number | null;
  expected_excess_return?: number | null;
  return_q10?: number | null;
  return_q50?: number | null;
  return_q90?: number | null;
  regime?: string;
  reasons?: string[];
  invalidation?: string[];
  model_version?: string;
  active_status?: string;
};

export type PredictionSummary = {
  status: "available" | "unavailable" | string;
  as_of?: string | null;
  horizons: number[];
  total?: number;
  rows: PredictionRow[];
};

export type PredictionAlert = {
  id: string;
  type: "opportunity" | "downside" | "data" | "model";
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  code?: string;
  horizon?: number;
};

export type ModelHealth = {
  status: "available" | "unavailable" | string;
  accuracy?: {
    status?: string;
    evaluated?: number;
    hit_rate?: number | null;
    mean_brier_score?: number | null;
    mean_absolute_return_error?: number | null;
  };
  prediction_diagnostics?: {
    invalidated?: number;
    mean_out_of_distribution_ratio?: number;
    max_out_of_distribution_ratio?: number;
    max_psi?: number;
  };
  models: {
    model_version?: string;
    horizon?: number;
    calibration_method?: string;
    use_boosting?: boolean;
    sample_support?: number;
    metrics?: Record<string, unknown>;
    split_dates?: Record<string, string>;
    status?: string;
    is_champion?: boolean;
    gate_passed?: boolean | null;
    gate_reasons?: string[];
    gate_target?: string;
    shadow_cycles?: number;
    shadow_cycles_remaining?: number;
  }[];
};

export type RegimeSummary = {
  status: "available" | "unavailable" | string;
  current?: Record<string, unknown> | null;
  history?: Record<string, unknown>[];
  industries?: Record<string, unknown>[];
};

export type SourceHealth = { source: string; status: string; rows?: number; failed?: boolean; error?: string };

export type GovernanceAction = {
  severity: "critical" | "warning" | "info" | string;
  title: string;
  detail: string;
};

export type DashboardGovernance = {
  generated_at: string;
  market: string;
  agent: string;
  action_state: {
    status: "healthy" | "warning" | "critical" | string;
    items: GovernanceAction[];
  };
  lineage: {
    status: string;
    database_integrity?: string;
    counts: Record<string, number>;
    decision_runs: Record<string, unknown>[];
    decision_funnel?: {
      evaluated: number;
      eligible: number;
      selected: number;
      rejection_counts: Record<string, number>;
    };
    candidates: Record<string, unknown>[];
    allocations: Record<string, unknown>[];
    orders: Record<string, unknown>[];
    fills: Record<string, unknown>[];
    attributions: Record<string, unknown>[];
    experiments: Record<string, unknown>[];
  };
  risk: {
    status: string;
    portfolios: Record<string, unknown>[];
  };
  attribution: {
    status: string;
    rows: Record<string, unknown>[];
  };
  drift: Record<string, Record<string, unknown>>;
  experiments: Record<string, unknown>[];
  intelligence_evidence: {
    factor_validation?: Record<string, unknown>;
    quality?: Record<string, unknown>;
  };
  distinctness: Record<string, unknown>;
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
  predictions?: PredictionRow[];
  event_evidence?: Record<string, unknown>[];
  source_health?: SourceHealth[];
  warning?: string | null;
};

export type DashboardDetail = {
  generated_at: string;
  market: string;
  market_label: string;
  currency: string;
  agent: string;
  strategy: StrategyProfile;
  model_iteration?: ModelIterationStatus | null;
  model_shadow?: ModelShadowStatus | null;
  selection?: SelectionSnapshot;
  lookthrough?: PortfolioLookthrough | Record<string, never>;
  research?: QDIIResearch;
  intelligence?: MarketIntelligence;
  prediction_summary?: PredictionSummary;
  regimes?: RegimeSummary;
  alerts?: PredictionAlert[];
  model_health?: ModelHealth;
  source_health?: SourceHealth[];
  governance?: DashboardGovernance;
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

export type DashboardOverview = {
  generated_at: string;
  market: string;
  market_label: string;
  currency: string;
  agent: string;
  strategy: StrategyProfile;
  latest_nav: NavPoint | null;
  model_iteration?: ModelIterationStatus | null;
  model_shadow?: ModelShadowStatus | null;
};

export type DashboardPerformance = Pick<DashboardDetail, "generated_at" | "market" | "agent" | "nav">;

export type DashboardPortfolio = Pick<
  DashboardDetail,
  "generated_at" | "market" | "agent" | "activity" | "orders" | "positions" | "trades"
>;

export type DashboardPredictions = Pick<
  DashboardDetail,
  "generated_at" | "market" | "agent" | "prediction_summary" | "alerts" | "regimes" | "model_health" | "source_health"
>;

export type DashboardResearch = Pick<
  DashboardDetail,
  "generated_at" | "market" | "agent" | "selection" | "lookthrough" | "research"
>;

export type DashboardOperations = Pick<
  DashboardDetail,
  "generated_at" | "market" | "agent" | "runs" | "weekly_report"
>;
