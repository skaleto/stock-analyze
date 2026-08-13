import type { ReactNode } from "react";

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
  metrics: Record<string, number | string | boolean | null>;
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
      };
    }[];
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
  };
  validation: {
    passed: number;
    total: number;
    models: ModelResearchModel[];
    accounts?: ModelResearchAccountSummary[];
  };
  tabularResearch?: ModelResearchTabularEvidence;
  historicalComparison?: ModelResearchHistoricalComparison;
  simulation: {
    status: string;
    candidate: ModelResearchCandidate | null;
    account: ModelResearchSimulationAccount | null;
    accounts?: {
      accountId: string;
      scope: string;
      benchmark: string;
      selectedCount: number;
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
