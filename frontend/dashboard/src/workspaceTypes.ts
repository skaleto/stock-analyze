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

export type BoundedColumn<T> = {
  key: string;
  label: string;
  render: (row: T) => string;
};

export type ModelResearchModel = {
  modelVersion: string;
  horizon: number;
  algorithmFamily: string;
  trainedAt?: string | null;
  registeredAt?: string | null;
  sampleSupport: number;
  featureColumns: string[];
  artifactRef?: string | null;
  artifactStatus: string;
  gatePassed: boolean;
  gateReasons: string[];
  shadowCycles: number;
  shadowCyclesRemaining: number;
  isChampion: boolean;
  pointInTimeAudit?: boolean | null;
  candidateFeatureCount: number;
  metrics: Record<string, number | string | boolean | null>;
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
  accountId: string;
  accountLabel: string;
  isolation: string;
  navRows: number;
  portfolioRef: string;
};

export type ModelResearchData = {
  generated_at: string;
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
  training: { models: ModelResearchModel[] };
  validation: {
    passed: number;
    total: number;
    models: ModelResearchModel[];
  };
  simulation: {
    status: string;
    candidate: ModelResearchCandidate | null;
    account: ModelResearchSimulationAccount | null;
    predictionAsOf?: string | null;
    predictionStatus: string;
    cyclesCompleted: number;
    cyclesRequired: number;
    decision: {
      candidateRows: number;
      eligibleRows: number;
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
