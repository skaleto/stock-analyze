import type { ReactNode } from "react";
import type { Candle, InstrumentMetric } from "./types";

export type WorkspaceStatus =
  | "success"
  | "running"
  | "waiting_schedule"
  | "waiting_upstream"
  | "failed"
  | "skipped"
  | "research"
  | "empty"
  | "unavailable";

export type WorkspaceStage = {
  key: string;
  label: string;
  status: WorkspaceStatus;
  primary: string;
  secondary: string;
  updatedAt?: string | null;
  issues?: string[];
};

export type WorkspaceMetric = {
  label: string;
  value: string;
  tone?: "default" | "positive" | "negative" | "warning";
};

export type WorkspacePartialError = {
  resource: string;
  reason: string;
};

export type BoundedColumn<T> = {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
};

export type ModelResearchModel = {
  modelVersion: string;
  accountScope: string;
  specId?: string;
  horizon: number;
  algorithmFamily: string;
  trainedAt?: string | null;
  registeredAt?: string | null;
  sampleSupport: number;
  featureColumns: string[];
  artifactRef?: string | null;
  artifactStatus: string;
  lifecycleStatus?: string;
  gatePassed: boolean;
  gateReasons: string[];
  shadowCycles: number;
  shadowCyclesRemaining: number;
  isChampion: boolean;
  pointInTimeAudit?: boolean | null;
  candidateFeatureCount: number;
  baselineComparison?: Record<string, Record<string, number | string | boolean | null>>;
  accountMetrics?: Record<string, Record<string, number | string | boolean | null>>;
  noTradeReasonCounts?: Record<string, number>;
  diagnosticNetExcessReturn?: number | null;
  netExcessReturn?: number | null;
  calibrationStatus?: string | null;
  capitalUtilization?: number | null;
  metrics: Record<string, number | string | boolean | null>;
};

export type ModelResearchArchive = {
  total: number;
  byStatus: Record<string, number>;
  recent: ModelResearchModel[];
};

export type ModelResearchAccountSummary = {
  accountScope: string;
  accountLabel: string;
  candidateCount: number;
  shadowCount: number;
  rejectedCount: number;
  latestStatus: string;
  bestModelVersion: string;
  bestRankIc?: number | null;
  bestNetExcessReturn?: number | null;
  bestTradeCount: number;
  bestEdgeCalibrationAvailable: boolean;
};

export type ModelResearchCandidate = {
  model_version?: string;
  display_version?: string;
  status?: string;
  status_label?: string;
  selected_at?: string | null;
  registered_at?: string | null;
  shadow_cycles?: number;
  shadow_cycles_remaining?: number;
  horizon?: number | string | null;
  candidate_kind?: string;
  admission_grade?: string;
  source_campaign?: string;
  source_trial_id?: string;
  promotion_policy?: string;
};

export type ModelResearchSimulationAccount = {
  accountId: string | null;
  accountLabel: string;
  isolation: string;
  navRows: number;
  portfolioRef: string | null;
};

export type ModelResearchTabularRun = {
  status: string;
  protocolVersion: string;
  configHash: string;
  accountScope: string;
  asOf: string;
  estimator: string;
  target: string;
  selectedFeatureCount: number;
  developmentStart: string;
  developmentEnd: string;
  oosStart: string;
  oosEnd: string;
  formalOrderSource: boolean;
  registryMutated: boolean;
  metrics: {
    rankIc: number | null;
    icir: number | null;
    rawRankIc: number | null;
    rawIcir: number | null;
    portfolioCagr: number | null;
    benchmarkCagr: number | null;
    netExcessReturn: number | null;
    maxDrawdown: number | null;
    activeMaxDrawdown: number | null;
    annualTurnover: number | null;
    capitalUtilization: number | null;
    portfolioSharpe: number | null;
    informationRatio: number | null;
    deflatedSharpeProbability: number | null;
    probabilityOfBacktestOverfit: number | null;
  };
  gate: {
    passed: boolean;
    reasons: string[];
    checks: Record<string, boolean>;
    positiveFolds: number;
    bucketSpearman: number | null;
  };
  buckets: {
    bucket: number;
    meanExcessReturn: number | null;
    observations: number;
  }[];
  calibration?: {
    enabled: boolean;
    foldCount: number;
    economicPredictionCoverage: number | null;
    positiveLowerBoundCoverage: number | null;
    uncertaintyBpsP50: number | null;
    uncertaintyBpsP90: number | null;
    optimizerTrackingErrorP50: number | null;
    optimizerTrackingErrorP90: number | null;
    noTradeReasons: {
      reason: string;
      count: number;
    }[];
  };
};

export type ModelResearchTabularEvidence = {
  status: string;
  formalStrategyWeight: number;
  formalOrderSource: boolean;
  latest: ModelResearchTabularRun | null;
  best: ModelResearchTabularRun | null;
  experiments: ModelResearchTabularRun[];
  forwardObservation?: {
    status: string;
    lifecycleStatus: string;
    modelId: string;
    configHash: string;
    accountScope: string;
    horizon: number;
    observationStart: string;
    latestPredictionDate: string | null;
    observationDays: number;
    predictionRows: number;
    latestCandidates: number;
    latestSelected: number;
    maturedEvidence: {
      status: string;
      maturedRows: number;
      maturedDays: number;
      latestLabelEnd: string | null;
      rankIc: number | null;
      icir: number | null;
      rawRankIc: number | null;
      rawIcir: number | null;
      topBottomSpread: number | null;
      buckets: {
        bucket: number;
        meanExcessReturn: number | null;
        observations: number;
      }[];
    };
    portfolio: {
      status: string;
      periods: number;
      rebalancePeriods: number;
      trades: number;
      netReturn: number | null;
      benchmarkReturn: number | null;
      netExcessReturn: number | null;
      maxDrawdown: number | null;
      activeMaxDrawdown: number | null;
      informationRatio: number | null;
      annualTurnover: number | null;
      capitalUtilization: number | null;
      executionCostBps: number | null;
    };
    drift: {
      status: string;
      medianFeatureCoverage: number | null;
      medianOutOfRangeRatio: number | null;
    };
    promotion: {
      status: string;
      passedChecks: number;
      totalChecks: number;
      checks: { key: string; passed: boolean }[];
      automaticPromotion: boolean;
    };
    formalStrategyWeight: number;
    formalOrderSource: boolean;
    updatedAt: string | null;
  } | null;
  closure?: {
    status: string;
    asOf: string;
    decision: string;
    bestConfigHash: string;
    officialImmutableTrials: number;
    diagnosticExperiments: number;
    passedChecks: number;
    totalChecks: number;
    formalStrategyWeight: number | null;
    blockers: {
      code: string;
      measured: number | null;
      required: number | null;
      evidence: string;
    }[];
    nextRunConditions: {
      code: string;
      measured: number | null;
      required: number | null;
      evidence: string;
    }[];
  } | null;
};

export type ModelResearchHistoricalComparison = {
  status: string;
  evidenceType: "historical_diagnostic" | string;
  asOf: string | null;
  horizon: number;
  scopes: {
    accountScope: string;
    finalWindow: string[];
    evaluationDateCount: number;
    winner: {
      participantId: string;
      name: string;
      netExcessReturn: number;
    } | null;
    participants: {
      participantId: string;
      participantType: string;
      name: string;
      status: string;
      metrics: {
        netReturn?: number;
        benchmarkReturn?: number;
        netExcessReturn?: number;
        informationRatio?: number;
        sharpe?: number;
        maxDrawdown?: number;
        annualTurnover?: number;
        tradeCount?: number;
        capitalUtilization?: number;
        cashPositionEffectTotal?: number;
        securitySelectionReturnTotal?: number;
        executionCostEffectTotal?: number;
      };
    }[];
  }[];
};

export type ModelResearchStrategyCampaign = {
  status: string;
  campaignId: string | null;
  manifestHash: string | null;
  completedAt: string | null;
  formalStrategyActivated: boolean;
  scopes: {
    accountScope: string;
    status: string;
    selectedRuleSpecId: string | null;
    selectedIncrementalSpecId: string | null;
    bestDiagnosticSpecId: string | null;
    diagnosticOnly: boolean;
    reasons: string[];
    transparentTrialCount: number;
    incrementalTrialCount: number;
    netReturn: number | null;
    benchmarkReturn: number | null;
    netExcessReturn: number | null;
    sharpe: number | null;
    maxDrawdown: number | null;
    targetFillRatio: number | null;
    costStressNetExcessReturn: number | null;
    deflatedSharpeProbability: number | null;
    probabilityOfBacktestOverfit: number | null;
    pairedBootstrapProbability: number | null;
    attribution: Record<string, unknown>;
    folds: Record<string, unknown>[];
    regimes: Record<string, unknown>;
  }[];
};

export type ModelResearchData = {
  generated_at: string;
  errors?: WorkspacePartialError[];
  market: string;
  market_label: string;
  truncated?: boolean;
  truncationReason?: string | null;
  stages: WorkspaceStage[];
  dataPreparation: {
    sources: {
      source: string;
      status: string;
      rows?: number;
      failed?: boolean;
      as_of?: string | null;
      error?: string;
    }[];
    candidateFeatureCount: number;
    selectedFeatureCount: number;
    structuredFeatureCount: number;
    intelligenceFeatureCount: number;
    unclassifiedFeatureCount?: number;
    unclassifiedFeatures?: string[];
    selectedFeatures: string[];
    pointInTimeAudit: string;
    gaps: string[];
  };
  training: {
    models: ModelResearchModel[];
    accounts?: ModelResearchAccountSummary[];
    archive?: ModelResearchArchive;
  };
  validation: {
    passed: number;
    total: number;
    models: ModelResearchModel[];
    accounts?: ModelResearchAccountSummary[];
  };
  tabularResearch?: ModelResearchTabularEvidence;
  historicalComparison?: ModelResearchHistoricalComparison;
  strategyCampaign?: ModelResearchStrategyCampaign;
  simulation: {
    status: string;
    candidate: ModelResearchCandidate | null;
    account: ModelResearchSimulationAccount | null;
    accounts?: {
      accountId: string;
      scope: string;
      benchmark: string;
      selectedCount: number;
      candidateVersion?: string;
      candidateLabel?: string;
      candidateKind?: string;
      admissionGrade?: string;
      candidateStatus?: string;
      candidateStatusLabel?: string;
      sourceCampaign?: string;
      sourceTrialId?: string;
      participationStatus?: string;
      predictionStatus?: string;
      historicalNetReturn?: number | null;
      historicalNetExcessReturn?: number | null;
      historicalCostStressNetExcessReturn?: number | null;
      historicalMaxDrawdown?: number | null;
      historicalTargetFillRatio?: number | null;
      historicalBootstrapProbability?: number | null;
      rebalanceFrequency?: string;
      rebalanceDue?: boolean | null;
      lastRebalanceSignalDate?: string | null;
      targetRiskyExposure?: number | null;
      date?: string | null;
      cash?: number | null;
      marketValue?: number | null;
      totalValue?: number | null;
      benchmarkClose?: number | null;
    }[];
    evaluation?: {
      status: string;
      modelVersion?: string | null;
      simulatorVersion?: string | null;
      grossReturn?: number | null;
      netReturn?: number | null;
      benchmarkReturn?: number | null;
      netExcessReturn?: number | null;
      maxDrawdown?: number | null;
      annualTurnover?: number | null;
      capitalUtilization?: number | null;
      cashRatio?: number | null;
      rebalanceFrequency?: string | null;
      scheduledRebalancePeriods?: number;
      sharpe?: number | null;
      executionCost?: number | null;
      executionCostBps?: number | null;
      impactBpsP50?: number | null;
      impactBpsP90?: number | null;
      impactCappedNotionalRatio?: number | null;
      missingLiquidityNotionalRatio?: number | null;
      executionEvidenceStatus?: string | null;
      executionPolicyVersion?: string | null;
      edgeCalibrationVersion?: string | null;
      allocationContract?: string | null;
      modelTiltCap?: number | null;
      decisionCount?: number;
      tradeAllowedCount?: number;
      noTradeCount?: number;
      noTradeReasonCounts?: Record<string, number>;
      effectivePeriods?: number;
      validTrialCount?: number;
      trialEvidenceStatus?: string;
      baselineComparison: Record<
        string,
        Record<string, number | string | boolean | null>
      >;
      accountMetrics: Record<
        string,
        Record<string, number | string | boolean | null>
      >;
    };
    predictionAsOf?: string | null;
    predictionStatus: string;
    cyclesCompleted: number;
    cyclesRequired: number;
    decision: {
      candidateRows: number;
      modelEligibleRows: number;
      eligibleRows: number;
      scopeRejectedRows: number;
      selectedCount: number;
      tradesExecuted: number;
      pendingOrders: number;
      cashOnly: boolean;
      cashReason?: string | null;
      diagnostics?: Record<string, unknown> | null;
    };
  };
  adoption: {
    champions: {
      modelVersion: string;
      horizon: number;
      activatedAt?: string | null;
      artifactRef?: string | null;
    }[];
    rollbackCandidates: {
      modelVersion: string;
      displayVersion: string;
      outcome: string;
      endedAt?: string | null;
    }[];
    strategyUsage: {
      agent: string;
      strategy_label: string;
      as_of?: string | null;
      status: string;
      applied_candidates: number;
      candidate_coverage: number;
      model_versions: Record<string, string>;
      fallback_reason: string;
      accounts?: number;
    }[];
  };
  attribution?: {
    status: string;
    formalModelApplied: boolean;
    completeCount: number;
    totalCount: number;
    rows: {
      asOf?: string | null;
      strategyId: string;
      accountId: string;
      status: string;
      modelPolicyStatus: string;
      modelVersions: Record<string, string>;
      netPnl?: number | null;
      modelSelectionPnl?: number | null;
      explainedRatio?: number | null;
      residualRatio?: number | null;
      positiveDrivers: unknown[];
      negativeDrivers: unknown[];
      unavailableInputs: string[];
    }[];
  };
};

export type UsageEvidenceCell = {
  status: "used" | "not_used" | "observing" | "unavailable" | string;
  count: number;
  countSemantics: string;
  features: string[];
  evidence: string[];
  formalCount: number;
  formalFactors: string[];
  formalStatus?: string;
  researchCount: number;
  researchFeatures: string[];
  researchStatus?: string;
  missingManifestEvidence?: string[];
  evidenceByNamespace: {
    formal: string[];
    research: string[];
  };
  missingManifest?: boolean;
  lineageStatus?: string | null;
};

export type DataIntelligenceData = {
  generated_at: string;
  errors?: WorkspacePartialError[];
  market: string;
  market_label: string;
  truncated?: boolean;
  truncationReason?: string | null;
  structured: {
    stages: WorkspaceStage[];
    sources: {
      source: string;
      researchFeatureCount: number;
      selectedModelFeatureCount: number;
      strategyFactorCount: number;
      activeStrategyFactorCount: number;
      status: string;
      useLocations: string[];
    }[];
    coverage: {
      status: string;
      rangeStart: string | null;
      rangeEnd: string | null;
      latestTradeDate: string | null;
      snapshotAsOf?: string | null;
      latestSnapshot: string | null;
      snapshotCount?: number;
      inspectedSnapshots?: number;
      readableSnapshots?: number;
      datedSnapshots?: number;
    };
    factorGroups: {
      family: string;
      definedFeatureCount: number;
      selectedFeatureCount: number;
    }[];
    selectedFeatures: string[];
    formalFactorNamespace: {
      definedFactorCount: number;
      activeFactorCount: number;
      activeFactors: string[];
    };
    researchFeatureNamespace: {
      definedFeatureCount: number;
      selectedFeatures: string[];
    };
    quality: {
      status: string;
      modelCount: number;
      pointInTimeAuditedModels: number;
      pointInTimeFailedModels: number;
      missingRateStatus: string;
      outlierStatus: string;
    };
  };
  intelligence: {
    stages: WorkspaceStage[];
    truncated?: boolean;
    truncationReasons?: string[];
    featureNamespace: {
      definedFeatureCount: number;
      selectedFeatureCount: number;
      selectedFeatures: string[];
    };
    pipeline: import("./types").IntelligenceSummary["pipeline"];
    extraction: import("./types").IntelligenceSummary["extraction"];
    factorSupply: import("./types").IntelligenceSummary["factorSupply"];
    modelImpact: import("./types").IntelligenceSummary["modelImpact"];
    decisions: import("./types").IntelligenceSummary["decisions"];
  };
  usageMatrix: {
    consumerKey: string;
    consumerLabel: string;
    structuredData: UsageEvidenceCell;
    traditionalFactors: UsageEvidenceCell;
    intelligenceFactors: UsageEvidenceCell;
    modelAdoption?: {
      status: string;
      modelCount: number;
      resolvableManifestCount: number;
      missingManifestCount: number;
      models: {
        horizon: number;
        modelVersion: string;
        manifestStatus: string;
        evidence: string;
        missingManifestEvidence?: string | null;
      }[];
    };
    impact: string;
    lineageStatus?: string | null;
    missingManifest?: boolean;
  }[];
};

export type OperationsRuntimeStatus = "available" | "unavailable";

export type OperationsUnit = {
  unit: string;
  status: WorkspaceStatus;
  loadState?: string | null;
  activeState?: string | null;
  subState?: string | null;
  result?: string | null;
  exitStatus?: number | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  reason?: string | null;
};

export type OperationsChainStage = WorkspaceStage & {
  units: OperationsUnit[];
  crossMarketUnits: OperationsUnit[];
};

export type OperationsBacklog = {
  download?: number;
  parse?: number;
  semantic?: number;
  total?: number;
};

export type OperationsBackgroundWorker = {
  key: string;
  label: string;
  status: WorkspaceStatus;
  serviceUnit: string;
  timerUnit: string | null;
  loadState?: string | null;
  lastResult?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  nextTriggerAt?: string | null;
  reason?: string | null;
  backlog?: OperationsBacklog | null;
};

export type OperationsSchedule = {
  unit: string;
  label: string;
  status: "active" | "inactive" | "unavailable";
  loadState?: string | null;
  lastTriggerAt?: string | null;
  nextTriggerAt?: string | null;
  reason?: string | null;
  automation: "automatic";
};

export type OperationsCenterData = {
  generated_at: string;
  errors?: WorkspacePartialError[];
  scope: "all" | "a_share" | "cn_qdii_etf" | "exceptions";
  truncated?: boolean;
  truncationReason?: "serialized_size_limit" | string | null;
  runtime: {
    status: OperationsRuntimeStatus;
    lastKnownAt?: string | null;
    reason?: string | null;
  };
  dailyFreshness: {
    asOfDate: string;
    status: "waiting" | WorkspaceStatus;
    lastCompleteDate?: string | null;
    completedTasks?: number;
    expectedTasks?: number;
  };
  mainChain: OperationsChainStage[];
  background: {
    status: OperationsRuntimeStatus;
    snapshotGeneratedAt?: string | null;
    backlog: OperationsBacklog;
    artifactWorkers: {
      status: OperationsRuntimeStatus;
      activeLeases: number;
      latestFinishedAt?: string | null;
    };
    localBackfill: {
      status: string;
      phase?: string | null;
      reason?: string | null;
      updatedAt?: string | null;
    };
  };
  backgroundWorkers: OperationsBackgroundWorker[];
  schedules: Record<"daily" | "weekly" | "monthly", OperationsSchedule[]>;
  recentRuns: {
    runId: string;
    market: string;
    strategyKey: string;
    strategyLabel: string;
    command: string;
    asOf?: string | null;
    status: string;
    startedAt: string;
    finishedAt: string;
    durationMs: number;
    errorSummary: string;
  }[];
  disk: {
    status: OperationsRuntimeStatus;
    usedRatio?: number | null;
    totalBytes?: number;
    freeBytes?: number;
  };
  interventions: {
    key: string;
    severity: "critical" | "warning" | "info" | string;
    title: string;
    evidence: string;
  }[];
};

export type MultiAgentResearchLatestRun = {
  runId: string;
  createdAt: string | null;
  status: string;
  market: string;
  instrument: {
    code: string;
    name: string;
  };
  model: string | null;
  degradedRoles: string[];
  digest: string;
  executionEffect: "none_research_only";
  reportPath: string;
};

export type MultiAgentResearchData = {
  schemaVersion: "multi-agent-research-dashboard-v1";
  status: "available" | "empty";
  latestRun: MultiAgentResearchLatestRun | null;
  universe: {
    status: "available" | "unavailable";
    asOf: string | null;
    aShare: {
      scopeCounts: Record<string, number>;
      uniqueInstruments?: number | null;
    };
    funds: {
      sourceCounts: Record<string, number>;
      overseasScopeCounts: Record<string, number>;
      classificationCounts?: Record<string, number>;
    };
  };
  executionEffect: "none_research_only";
};

export type ResearchUniverseKind = "a_share" | "exchange_fund" | "otc_fund";

export type ResearchUniverseRequest = {
  kind: ResearchUniverseKind;
  query: string;
  scope: string | null;
  page: number;
  pageSize: 20 | 50 | 100;
};

export type ResearchUniverseAShareRecord = {
  code: string;
  name: string;
  recordKind: "a_share_equity" | string;
  researchOnly: true;
  researchScopes: string[];
  membershipDate: string | null;
};

export type ResearchUniverseFundRecord = {
  code: string;
  name: string;
  recordKind: "fund" | string;
  researchOnly: true;
  fundType: string;
  benchmark: string;
  overseasScope: string | null;
  classificationStatus: string;
  tradability: "exchange_research_only" | "otc_non_tradable_research_only";
};

export type ResearchUniverseRecord =
  | ResearchUniverseAShareRecord
  | ResearchUniverseFundRecord;

export type ResearchUniversePage = {
  schemaVersion: "research-universe-browser-v1";
  status: "available" | "unavailable";
  asOf: string | null;
  kind: ResearchUniverseKind;
  query: string;
  scope: string | null;
  page: number;
  pageSize: 20 | 50 | 100;
  total: number;
  scopeOptions: string[];
  records: ResearchUniverseRecord[];
  executionEffect: "none_research_only";
};

export type ResearchUniverseInstrumentRequest = {
  kind: ResearchUniverseKind;
  code: string;
};

export type ResearchUniverseInstrumentDetail = {
  schemaVersion: "research-universe-instrument-v1";
  status: "available" | "unavailable";
  asOf: string | null;
  kind: ResearchUniverseKind;
  code: string;
  instrument: ResearchUniverseRecord | null;
  market: "a_share" | "cn_qdii_etf" | null;
  latest: (Candle & { changePct: number | null }) | null;
  candles: Candle[];
  metrics: InstrumentMetric[];
  warning: string | null;
  executionEffect: "none_research_only";
};
