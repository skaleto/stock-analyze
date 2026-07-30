import type {
  DashboardDetail,
  DashboardGovernance,
  DashboardOperations,
  DashboardOverview,
  DashboardPerformance,
  DashboardPortfolio,
  DashboardPredictions,
  DashboardResearch,
  DashboardSummary,
  IntelligenceDocumentDetail,
  IntelligenceEventDetail,
  IntelligenceSummary,
  InstrumentDetail,
  SystemOverviewData,
} from "./types";
import type {
  DataIntelligenceData,
  ModelResearchData,
} from "./workspaceTypes";

const workspaceStatuses = new Set([
  "success",
  "running",
  "waiting_schedule",
  "waiting_upstream",
  "failed",
  "skipped",
  "research",
  "empty",
  "unavailable",
]);

function modelResearchError(path: string): never {
  throw new Error(`Invalid model research response: ${path}`);
}

function objectAt(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    modelResearchError(path);
  }
  return value as Record<string, unknown>;
}

function arrayAt(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) modelResearchError(path);
  return value;
}

function stringAt(value: unknown, path: string): string {
  if (typeof value !== "string") modelResearchError(path);
  return value;
}

function numberAt(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    modelResearchError(path);
  }
  return value;
}

function booleanAt(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") modelResearchError(path);
  return value;
}

function optionalString(value: unknown, path: string): void {
  if (value !== undefined && value !== null && typeof value !== "string") {
    modelResearchError(path);
  }
}

function stringArray(value: unknown, path: string): void {
  arrayAt(value, path).forEach((item, index) => {
    stringAt(item, `${path}[${index}]`);
  });
}

function rejectDuplicateIdentity(
  seen: Set<string>,
  identity: unknown[],
  path: string,
  fields: string,
): void {
  const key = JSON.stringify(identity);
  if (seen.has(key)) modelResearchError(`${path} duplicate ${fields}`);
  seen.add(key);
}

function validateModel(value: unknown, path: string): void {
  const model = objectAt(value, path);
  stringAt(model.modelVersion, `${path}.modelVersion`);
  numberAt(model.horizon, `${path}.horizon`);
  stringAt(model.algorithmFamily, `${path}.algorithmFamily`);
  optionalString(model.trainedAt, `${path}.trainedAt`);
  optionalString(model.registeredAt, `${path}.registeredAt`);
  numberAt(model.sampleSupport, `${path}.sampleSupport`);
  stringArray(model.featureColumns, `${path}.featureColumns`);
  optionalString(model.artifactRef, `${path}.artifactRef`);
  stringAt(model.artifactStatus, `${path}.artifactStatus`);
  booleanAt(model.gatePassed, `${path}.gatePassed`);
  stringArray(model.gateReasons, `${path}.gateReasons`);
  numberAt(model.shadowCycles, `${path}.shadowCycles`);
  numberAt(model.shadowCyclesRemaining, `${path}.shadowCyclesRemaining`);
  booleanAt(model.isChampion, `${path}.isChampion`);
  numberAt(model.candidateFeatureCount, `${path}.candidateFeatureCount`);
  objectAt(model.metrics, `${path}.metrics`);
}

function validateModelResearch(value: unknown): ModelResearchData {
  const data = objectAt(value, "root");
  stringAt(data.generated_at, "generated_at");
  stringAt(data.market, "market");
  stringAt(data.market_label, "market_label");

  const stageKeys = new Set<string>();
  arrayAt(data.stages, "stages").forEach((item, index) => {
    const path = `stages[${index}]`;
    const stage = objectAt(item, path);
    const key = stringAt(stage.key, `${path}.key`);
    rejectDuplicateIdentity(stageKeys, [key], `${path}.key`, "key");
    stringAt(stage.label, `${path}.label`);
    const status = stringAt(stage.status, `${path}.status`);
    if (!workspaceStatuses.has(status)) modelResearchError(`${path}.status`);
    stringAt(stage.primary, `${path}.primary`);
    stringAt(stage.secondary, `${path}.secondary`);
  });

  const preparation = objectAt(data.dataPreparation, "dataPreparation");
  const sourceIdentities = new Set<string>();
  arrayAt(preparation.sources, "dataPreparation.sources").forEach(
    (item, index) => {
      const path = `dataPreparation.sources[${index}]`;
      const source = objectAt(item, path);
      stringAt(source.source, `${path}.source`);
      stringAt(source.status, `${path}.status`);
      if (source.rows !== undefined) numberAt(source.rows, `${path}.rows`);
      if (source.failed !== undefined) {
        booleanAt(source.failed, `${path}.failed`);
      }
      optionalString(source.as_of, `${path}.as_of`);
      optionalString(source.error, `${path}.error`);
      rejectDuplicateIdentity(
        sourceIdentities,
        [
          source.source,
          source.status,
          source.rows ?? "",
          source.as_of ?? "",
          source.error ?? "",
        ],
        path,
        "source,status,rows,as_of,error",
      );
    },
  );
  [
    "candidateFeatureCount",
    "selectedFeatureCount",
    "structuredFeatureCount",
    "intelligenceFeatureCount",
  ].forEach((key) => numberAt(preparation[key], `dataPreparation.${key}`));
  if (preparation.unclassifiedFeatureCount !== undefined) {
    numberAt(
      preparation.unclassifiedFeatureCount,
      "dataPreparation.unclassifiedFeatureCount",
    );
  }
  if (preparation.unclassifiedFeatures !== undefined) {
    stringArray(
      preparation.unclassifiedFeatures,
      "dataPreparation.unclassifiedFeatures",
    );
  }
  stringAt(preparation.pointInTimeAudit, "dataPreparation.pointInTimeAudit");

  const training = objectAt(data.training, "training");
  const trainingIdentities = new Set<string>();
  arrayAt(training.models, "training.models").forEach((model, index) => {
    const path = `training.models[${index}]`;
    validateModel(model, path);
    const row = model as Record<string, unknown>;
    rejectDuplicateIdentity(
      trainingIdentities,
      [row.horizon, row.modelVersion],
      path,
      "horizon,modelVersion",
    );
  });
  const validation = objectAt(data.validation, "validation");
  numberAt(validation.passed, "validation.passed");
  numberAt(validation.total, "validation.total");
  const validationIdentities = new Set<string>();
  arrayAt(validation.models, "validation.models").forEach((model, index) => {
    const path = `validation.models[${index}]`;
    validateModel(model, path);
    const row = model as Record<string, unknown>;
    rejectDuplicateIdentity(
      validationIdentities,
      [row.horizon, row.modelVersion],
      path,
      "horizon,modelVersion",
    );
  });

  const simulation = objectAt(data.simulation, "simulation");
  stringAt(simulation.status, "simulation.status");
  if (simulation.candidate !== null) {
    const candidate = objectAt(simulation.candidate, "simulation.candidate");
    optionalString(
      candidate.display_version,
      "simulation.candidate.display_version",
    );
  }
  if (simulation.account !== null) {
    const account = objectAt(simulation.account, "simulation.account");
    stringAt(account.accountId, "simulation.account.accountId");
    stringAt(account.accountLabel, "simulation.account.accountLabel");
    stringAt(account.isolation, "simulation.account.isolation");
  }
  optionalString(simulation.predictionAsOf, "simulation.predictionAsOf");
  stringAt(simulation.predictionStatus, "simulation.predictionStatus");
  numberAt(simulation.cyclesCompleted, "simulation.cyclesCompleted");
  numberAt(simulation.cyclesRequired, "simulation.cyclesRequired");
  const decision = objectAt(simulation.decision, "simulation.decision");
  [
    "candidateRows",
    "eligibleRows",
    "selectedCount",
    "tradesExecuted",
    "pendingOrders",
  ].forEach((key) => numberAt(decision[key], `simulation.decision.${key}`));
  booleanAt(decision.cashOnly, "simulation.decision.cashOnly");
  optionalString(decision.cashReason, "simulation.decision.cashReason");

  const adoption = objectAt(data.adoption, "adoption");
  const championIdentities = new Set<string>();
  arrayAt(adoption.champions, "adoption.champions").forEach((item, index) => {
    const path = `adoption.champions[${index}]`;
    const champion = objectAt(item, path);
    stringAt(champion.modelVersion, `${path}.modelVersion`);
    numberAt(champion.horizon, `${path}.horizon`);
    optionalString(champion.activatedAt, `${path}.activatedAt`);
    optionalString(champion.artifactRef, `${path}.artifactRef`);
    rejectDuplicateIdentity(
      championIdentities,
      [champion.horizon, champion.modelVersion],
      path,
      "horizon,modelVersion",
    );
  });
  arrayAt(adoption.rollbackCandidates, "adoption.rollbackCandidates").forEach(
    (item, index) => {
      const path = `adoption.rollbackCandidates[${index}]`;
      const rollback = objectAt(item, path);
      stringAt(rollback.displayVersion, `${path}.displayVersion`);
    },
  );
  const strategyAgents = new Set<string>();
  arrayAt(adoption.strategyUsage, "adoption.strategyUsage").forEach(
    (item, index) => {
      const path = `adoption.strategyUsage[${index}]`;
      const usage = objectAt(item, path);
      stringAt(usage.agent, `${path}.agent`);
      stringAt(usage.strategy_label, `${path}.strategy_label`);
      optionalString(usage.as_of, `${path}.as_of`);
      stringAt(usage.status, `${path}.status`);
      numberAt(usage.candidate_coverage, `${path}.candidate_coverage`);
      const versions = objectAt(usage.model_versions, `${path}.model_versions`);
      Object.entries(versions).forEach(([key, version]) => {
        stringAt(version, `${path}.model_versions.${key}`);
      });
      stringAt(usage.fallback_reason, `${path}.fallback_reason`);
      rejectDuplicateIdentity(
        strategyAgents,
        [usage.agent],
        path,
        "agent",
      );
    },
  );
  return data as unknown as ModelResearchData;
}

const DATA_INTELLIGENCE_LIST_LIMIT = 20;
const DATA_INTELLIGENCE_STRING_LIMIT = 1_000;
const DATA_INTELLIGENCE_DEPTH_LIMIT = 8;
const DATA_INTELLIGENCE_NODE_LIMIT = 512;
const DATA_INTELLIGENCE_RESPONSE_LIMIT = 250_000;
const DATA_INTELLIGENCE_CONSUMERS = new Set([
  "defensive",
  "trend",
  "research_model",
  "candidate_simulation",
]);

function dataIntelligenceError(path: string): never {
  throw new Error(`Invalid data intelligence response: ${path}`);
}

function dataObject(
  value: unknown,
  path: string,
): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    dataIntelligenceError(path);
  }
  return value as Record<string, unknown>;
}

function dataArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) dataIntelligenceError(path);
  if (value.length > DATA_INTELLIGENCE_LIST_LIMIT) {
    dataIntelligenceError(`${path} exceeds ${DATA_INTELLIGENCE_LIST_LIMIT}`);
  }
  return value;
}

function dataString(value: unknown, path: string): string {
  if (
    typeof value !== "string"
    || value.length > DATA_INTELLIGENCE_STRING_LIMIT
  ) {
    dataIntelligenceError(path);
  }
  return value;
}

function dataNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    dataIntelligenceError(path);
  }
  return value;
}

function dataBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") dataIntelligenceError(path);
  return value;
}

function dataOptionalString(value: unknown, path: string): void {
  if (value !== undefined && value !== null && typeof value !== "string") {
    dataIntelligenceError(path);
  }
}

function dataStringList(value: unknown, path: string): string[] {
  return dataArray(value, path).map((item, index) => (
    dataString(item, `${path}[${index}]`)
  ));
}

function dataOptionalNumber(value: unknown, path: string): void {
  if (value !== undefined && value !== null) dataNumber(value, path);
}

function validateBoundedNested(value: unknown, path: string): void {
  const budget = { nodes: 0 };

  function visit(item: unknown, itemPath: string, depth: number): void {
    budget.nodes += 1;
    if (budget.nodes > DATA_INTELLIGENCE_NODE_LIMIT) {
      dataIntelligenceError(`${path} node limit`);
    }
    if (depth > DATA_INTELLIGENCE_DEPTH_LIMIT) {
      dataIntelligenceError(`${path} depth limit`);
    }
    if (item === null || typeof item === "boolean") return;
    if (typeof item === "number") {
      if (!Number.isFinite(item)) dataIntelligenceError(itemPath);
      return;
    }
    if (typeof item === "string") {
      dataString(item, itemPath);
      return;
    }
    if (Array.isArray(item)) {
      if (item.length > DATA_INTELLIGENCE_LIST_LIMIT) {
        dataIntelligenceError(`${itemPath} exceeds ${DATA_INTELLIGENCE_LIST_LIMIT}`);
      }
      item.forEach((child, index) => {
        visit(child, `${itemPath}[${index}]`, depth + 1);
      });
      return;
    }
    if (typeof item === "object") {
      const entries = Object.entries(item);
      if (entries.length > DATA_INTELLIGENCE_LIST_LIMIT) {
        dataIntelligenceError(`${itemPath} key limit`);
      }
      entries.forEach(([key, child]) => {
        if (key.length > 128) dataIntelligenceError(`${itemPath} key length`);
        visit(child, `${itemPath}.${key}`, depth + 1);
      });
      return;
    }
    dataIntelligenceError(itemPath);
  }

  visit(value, path, 0);
}

function validateCountRecord(value: unknown, path: string): void {
  validateBoundedNested(value, path);
  const record = dataObject(value, path);
  Object.entries(record).forEach(([key, count]) => {
    dataNumber(count, `${path}.${key}`);
  });
}

function validateDecisionCounts(value: unknown, path: string): void {
  validateCountRecord(value, path);
  const decisions = dataObject(value, path);
  const expected = new Set(["canonical", "no_event", "quarantined", "failed"]);
  if (
    Object.keys(decisions).length !== expected.size
    || Object.keys(decisions).some((key) => !expected.has(key))
  ) {
    dataIntelligenceError(`${path} decision keys`);
  }
}

function validateMetricRecord(value: unknown, path: string): void {
  validateBoundedNested(value, path);
  const metrics = dataObject(value, path);
  Object.entries(metrics).forEach(([key, metric]) => {
    if (
      metric !== null
      && (typeof metric !== "number" || !Number.isFinite(metric))
    ) {
      dataIntelligenceError(`${path}.${key}`);
    }
  });
}

function looseObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function safeCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

function safeStatus(value: unknown, fallback = "unavailable"): string {
  return typeof value === "string" && value ? value : fallback;
}

function normalizeDecisionCounts(value: unknown): Record<string, number> {
  const current = looseObject(value) ?? {};
  return {
    canonical: safeCount(current.canonical),
    no_event: safeCount(current.no_event),
    quarantined: safeCount(current.quarantined),
    failed: safeCount(current.failed),
  };
}

function normalizeDataIntelligence(value: unknown): unknown {
  const root = looseObject(value);
  const intelligence = looseObject(root?.intelligence);
  if (!root || !intelligence) return value;

  const laneDegraded = root.truncated === true || intelligence.truncated === true;
  const shouldNormalize = (section: unknown): boolean => {
    const current = looseObject(section);
    return laneDegraded
      || current?.status === "partial"
      || current?.status === "unavailable";
  };
  const normalized = { ...intelligence };

  if (shouldNormalize(intelligence.pipeline)) {
    const pipeline = looseObject(intelligence.pipeline) ?? {};
    const stages = looseObject(pipeline.stages) ?? {};
    const backlog = looseObject(pipeline.backlog) ?? {};
    const workers = looseObject(pipeline.artifactWorkers) ?? {};
    const workerStages = looseObject(workers.stages) ?? {};
    const workerStage = (key: string) => {
      const stage = looseObject(workerStages[key]) ?? {};
      return {
        leased: safeCount(stage.leased),
        importing: safeCount(stage.importing),
        imported: safeCount(stage.imported),
        partial: safeCount(stage.partial),
        failed: safeCount(stage.failed),
        expired: safeCount(stage.expired),
      };
    };
    normalized.pipeline = {
      ...pipeline,
      status: safeStatus(pipeline.status),
      documents: safeCount(pipeline.documents),
      artifacts: looseObject(pipeline.artifacts) ?? {},
      stages: {
        catalogued: safeCount(stages.catalogued),
        pdfReady: safeCount(stages.pdfReady),
        parsed: safeCount(stages.parsed),
        semanticCompleted: safeCount(stages.semanticCompleted),
        canonicalEvents: safeCount(stages.canonicalEvents),
      },
      backlog: {
        download: safeCount(backlog.download),
        parse: safeCount(backlog.parse),
        semantic: safeCount(backlog.semantic),
        total: safeCount(backlog.total),
      },
      artifactWorkers: {
        ...workers,
        status: safeStatus(workers.status),
        activeLeases: safeCount(workers.activeLeases),
        leasedDocuments: safeCount(workers.leasedDocuments),
        completedDocuments: safeCount(workers.completedDocuments),
        downloadedDocuments: safeCount(workers.downloadedDocuments),
        parsedDocuments: safeCount(workers.parsedDocuments),
        stages: {
          download: workerStage("download"),
          parse: workerStage("parse"),
        },
      },
      sources: Array.isArray(pipeline.sources) ? pipeline.sources : [],
    };
  }

  if (shouldNormalize(intelligence.extraction)) {
    const extraction = looseObject(intelligence.extraction) ?? {};
    const contract = looseObject(extraction.contract) ?? {};
    normalized.extraction = {
      ...extraction,
      status: safeStatus(extraction.status),
      semanticRuns: looseObject(extraction.semanticRuns) ?? {},
      decisions: normalizeDecisionCounts(extraction.decisions),
      latestBatch: looseObject(extraction.latestBatch),
      contract: {
        ...contract,
        profileId: typeof contract.profileId === "string"
          ? contract.profileId
          : "",
      },
    };
  }

  if (shouldNormalize(intelligence.factorSupply)) {
    const supply = looseObject(intelligence.factorSupply) ?? {};
    normalized.factorSupply = {
      ...supply,
      status: safeStatus(supply.status),
      rows: safeCount(supply.rows),
      factorSets: Array.isArray(supply.factorSets) ? supply.factorSets : [],
      factors: Array.isArray(supply.factors) ? supply.factors : [],
      lifecycleCounts: looseObject(supply.lifecycleCounts) ?? {},
      suppliedFactors: safeCount(supply.suppliedFactors),
      modelEligible: supply.modelEligible === true,
      modelEligibleFactors: Array.isArray(supply.modelEligibleFactors)
        ? supply.modelEligibleFactors
        : [],
    };
  }

  if (shouldNormalize(intelligence.modelImpact)) {
    const impact = looseObject(intelligence.modelImpact) ?? {};
    normalized.modelImpact = {
      ...impact,
      status: safeStatus(impact.status),
      qualifiedHorizons: safeCount(impact.qualifiedHorizons),
      activation: typeof impact.activation === "string"
        ? impact.activation
        : "unavailable",
      adopted: impact.adopted === true,
      activeFactors: Array.isArray(impact.activeFactors)
        ? impact.activeFactors
        : [],
      iterationFactors: Array.isArray(impact.iterationFactors)
        ? impact.iterationFactors
        : [],
      reason: typeof impact.reason === "string" ? impact.reason : "",
      horizons: Array.isArray(impact.horizons) ? impact.horizons : [],
    };
  }

  if (
    laneDegraded
    || !looseObject(intelligence.decisions)
  ) {
    normalized.decisions = normalizeDecisionCounts(intelligence.decisions);
  }

  return { ...root, intelligence: normalized };
}

function validateWorkspaceStages(value: unknown, path: string): void {
  const keys = new Set<string>();
  dataArray(value, path).forEach((item, index) => {
    const itemPath = `${path}[${index}]`;
    const stage = dataObject(item, itemPath);
    const key = dataString(stage.key, `${itemPath}.key`);
    if (keys.has(key)) dataIntelligenceError(`${itemPath}.key duplicate key`);
    keys.add(key);
    dataString(stage.label, `${itemPath}.label`);
    const status = dataString(stage.status, `${itemPath}.status`);
    if (!workspaceStatuses.has(status)) {
      dataIntelligenceError(`${itemPath}.status`);
    }
    dataString(stage.primary, `${itemPath}.primary`);
    dataString(stage.secondary, `${itemPath}.secondary`);
  });
}

function validateUsageCell(value: unknown, path: string): void {
  const cell = dataObject(value, path);
  dataString(cell.status, `${path}.status`);
  dataNumber(cell.count, `${path}.count`);
  dataString(cell.countSemantics, `${path}.countSemantics`);
  dataStringList(cell.features, `${path}.features`);
  dataStringList(cell.evidence, `${path}.evidence`);
  const formalCount = dataNumber(cell.formalCount, `${path}.formalCount`);
  const formalFactors = dataStringList(
    cell.formalFactors,
    `${path}.formalFactors`,
  );
  dataOptionalString(cell.formalStatus, `${path}.formalStatus`);
  const researchCount = dataNumber(
    cell.researchCount,
    `${path}.researchCount`,
  );
  const researchFeatures = dataStringList(
    cell.researchFeatures,
    `${path}.researchFeatures`,
  );
  dataOptionalString(cell.researchStatus, `${path}.researchStatus`);
  if (cell.missingManifestEvidence !== undefined) {
    dataStringList(
      cell.missingManifestEvidence,
      `${path}.missingManifestEvidence`,
    );
  }
  if (formalFactors.length > formalCount || researchFeatures.length > researchCount) {
    dataIntelligenceError(`${path} namespace count`);
  }
  if (cell.count !== formalCount + researchCount) {
    dataIntelligenceError(`${path}.count`);
  }
  const evidence = dataObject(
    cell.evidenceByNamespace,
    `${path}.evidenceByNamespace`,
  );
  dataStringList(evidence.formal, `${path}.evidenceByNamespace.formal`);
  dataStringList(evidence.research, `${path}.evidenceByNamespace.research`);
  if (cell.missingManifest !== undefined) {
    dataBoolean(cell.missingManifest, `${path}.missingManifest`);
  }
  dataOptionalString(cell.lineageStatus, `${path}.lineageStatus`);
}

function validateDataIntelligence(value: unknown): DataIntelligenceData {
  const data = dataObject(normalizeDataIntelligence(value), "root");
  dataString(data.generated_at, "generated_at");
  dataString(data.market, "market");
  dataString(data.market_label, "market_label");
  if (data.truncated !== undefined) {
    dataBoolean(data.truncated, "truncated");
  }
  dataOptionalString(data.truncationReason, "truncationReason");

  const structured = dataObject(data.structured, "structured");
  validateWorkspaceStages(structured.stages, "structured.stages");
  const sourceKeys = new Set<string>();
  dataArray(structured.sources, "structured.sources").forEach((item, index) => {
    const path = `structured.sources[${index}]`;
    const source = dataObject(item, path);
    const sourceKey = dataString(source.source, `${path}.source`);
    if (sourceKeys.has(sourceKey)) {
      dataIntelligenceError(`${path}.source duplicate source`);
    }
    sourceKeys.add(sourceKey);
    [
      "researchFeatureCount",
      "selectedModelFeatureCount",
      "strategyFactorCount",
      "activeStrategyFactorCount",
    ].forEach((key) => dataNumber(source[key], `${path}.${key}`));
    dataString(source.status, `${path}.status`);
    dataStringList(source.useLocations, `${path}.useLocations`);
  });

  const coverage = dataObject(structured.coverage, "structured.coverage");
  dataString(coverage.status, "structured.coverage.status");
  [
    "rangeStart",
    "rangeEnd",
    "latestTradeDate",
    "snapshotAsOf",
    "latestSnapshot",
  ].forEach((key) => (
    dataOptionalString(coverage[key], `structured.coverage.${key}`)
  ));
  [
    "snapshotCount",
    "inspectedSnapshots",
    "readableSnapshots",
    "datedSnapshots",
  ].forEach(
    (key) => dataOptionalNumber(coverage[key], `structured.coverage.${key}`),
  );

  const familyKeys = new Set<string>();
  dataArray(structured.factorGroups, "structured.factorGroups").forEach(
    (item, index) => {
      const path = `structured.factorGroups[${index}]`;
      const group = dataObject(item, path);
      const family = dataString(group.family, `${path}.family`);
      if (familyKeys.has(family)) {
        dataIntelligenceError(`${path}.family duplicate family`);
      }
      familyKeys.add(family);
      dataNumber(group.definedFeatureCount, `${path}.definedFeatureCount`);
      dataNumber(group.selectedFeatureCount, `${path}.selectedFeatureCount`);
    },
  );
  dataStringList(structured.selectedFeatures, "structured.selectedFeatures");
  const formal = dataObject(
    structured.formalFactorNamespace,
    "structured.formalFactorNamespace",
  );
  dataNumber(
    formal.definedFactorCount,
    "structured.formalFactorNamespace.definedFactorCount",
  );
  dataNumber(
    formal.activeFactorCount,
    "structured.formalFactorNamespace.activeFactorCount",
  );
  dataStringList(
    formal.activeFactors,
    "structured.formalFactorNamespace.activeFactors",
  );
  const research = dataObject(
    structured.researchFeatureNamespace,
    "structured.researchFeatureNamespace",
  );
  dataNumber(
    research.definedFeatureCount,
    "structured.researchFeatureNamespace.definedFeatureCount",
  );
  dataStringList(
    research.selectedFeatures,
    "structured.researchFeatureNamespace.selectedFeatures",
  );
  const quality = dataObject(structured.quality, "structured.quality");
  dataString(quality.status, "structured.quality.status");
  [
    "modelCount",
    "pointInTimeAuditedModels",
    "pointInTimeFailedModels",
  ].forEach((key) => dataNumber(quality[key], `structured.quality.${key}`));
  dataString(quality.missingRateStatus, "structured.quality.missingRateStatus");
  dataString(quality.outlierStatus, "structured.quality.outlierStatus");

  const intelligence = dataObject(data.intelligence, "intelligence");
  validateWorkspaceStages(intelligence.stages, "intelligence.stages");
  if (intelligence.truncated !== undefined) {
    dataBoolean(intelligence.truncated, "intelligence.truncated");
  }
  if (intelligence.truncationReasons !== undefined) {
    dataStringList(
      intelligence.truncationReasons,
      "intelligence.truncationReasons",
    );
  }
  const intelligenceNamespace = dataObject(
    intelligence.featureNamespace,
    "intelligence.featureNamespace",
  );
  dataNumber(
    intelligenceNamespace.definedFeatureCount,
    "intelligence.featureNamespace.definedFeatureCount",
  );
  dataNumber(
    intelligenceNamespace.selectedFeatureCount,
    "intelligence.featureNamespace.selectedFeatureCount",
  );
  dataStringList(
    intelligenceNamespace.selectedFeatures,
    "intelligence.featureNamespace.selectedFeatures",
  );

  const pipeline = dataObject(intelligence.pipeline, "intelligence.pipeline");
  dataString(pipeline.status, "intelligence.pipeline.status");
  dataNumber(pipeline.documents, "intelligence.pipeline.documents");
  const pipelineStages = dataObject(
    pipeline.stages,
    "intelligence.pipeline.stages",
  );
  [
    "catalogued",
    "pdfReady",
    "parsed",
    "semanticCompleted",
    "canonicalEvents",
  ].forEach((key) => (
    dataNumber(pipelineStages[key], `intelligence.pipeline.stages.${key}`)
  ));
  const backlog = dataObject(pipeline.backlog, "intelligence.pipeline.backlog");
  ["download", "parse", "semantic", "total"].forEach((key) => (
    dataNumber(backlog[key], `intelligence.pipeline.backlog.${key}`)
  ));
  const sourceIdentities = new Set<string>();
  dataArray(pipeline.sources, "intelligence.pipeline.sources").forEach(
    (item, index) => {
      const path = `intelligence.pipeline.sources[${index}]`;
      const source = dataObject(item, path);
      const identity = dataString(source.source, `${path}.source`);
      if (sourceIdentities.has(identity)) {
        dataIntelligenceError(`${path}.source duplicate source`);
      }
      sourceIdentities.add(identity);
      dataString(source.freshnessStatus, `${path}.freshnessStatus`);
      dataOptionalString(source.latestPublishedAt, `${path}.latestPublishedAt`);
      dataOptionalString(source.lastIngestedAt, `${path}.lastIngestedAt`);
      dataOptionalString(source.cursor, `${path}.cursor`);
    },
  );
  validateBoundedNested(
    pipeline.artifactWorkers,
    "intelligence.pipeline.artifactWorkers",
  );
  const artifactWorkers = dataObject(
    pipeline.artifactWorkers,
    "intelligence.pipeline.artifactWorkers",
  );
  dataString(
    artifactWorkers.status,
    "intelligence.pipeline.artifactWorkers.status",
  );

  const extraction = dataObject(
    intelligence.extraction,
    "intelligence.extraction",
  );
  dataString(extraction.status, "intelligence.extraction.status");
  validateCountRecord(
    extraction.semanticRuns,
    "intelligence.extraction.semanticRuns",
  );
  validateDecisionCounts(
    extraction.decisions,
    "intelligence.extraction.decisions",
  );
  if (extraction.latestBatch !== null) {
    validateBoundedNested(
      extraction.latestBatch,
      "intelligence.extraction.latestBatch",
    );
    const latestBatch = dataObject(
      extraction.latestBatch,
      "intelligence.extraction.latestBatch",
    );
    [
      "batchKey",
      "profileId",
      "provider",
      "model",
      "promptVersion",
      "schemaVersion",
      "taxonomyVersion",
      "parserVersion",
      "batchDate",
      "qualityStatus",
    ].forEach((key) => (
      dataString(
        latestBatch[key],
        `intelligence.extraction.latestBatch.${key}`,
      )
    ));
  }
  validateBoundedNested(
    extraction.contract,
    "intelligence.extraction.contract",
  );
  const contract = dataObject(
    extraction.contract,
    "intelligence.extraction.contract",
  );
  dataString(contract.profileId, "intelligence.extraction.contract.profileId");

  const supply = dataObject(
    intelligence.factorSupply,
    "intelligence.factorSupply",
  );
  dataString(supply.status, "intelligence.factorSupply.status");
  dataNumber(supply.suppliedFactors, "intelligence.factorSupply.suppliedFactors");
  dataBoolean(supply.modelEligible, "intelligence.factorSupply.modelEligible");
  dataStringList(
    supply.modelEligibleFactors,
    "intelligence.factorSupply.modelEligibleFactors",
  );
  const factorNames = new Set<string>();
  dataArray(supply.factors, "intelligence.factorSupply.factors").forEach(
    (item, index) => {
      const path = `intelligence.factorSupply.factors[${index}]`;
      const factor = dataObject(item, path);
      const name = dataString(factor.name, `${path}.name`);
      if (factorNames.has(name)) {
        dataIntelligenceError(`${path}.name duplicate factor`);
      }
      factorNames.add(name);
      dataString(factor.state, `${path}.state`);
      dataOptionalNumber(factor.coverage, `${path}.coverage`);
      dataOptionalNumber(factor.activationRate, `${path}.activationRate`);
      if (
        factor.meanRankIc !== undefined
        && factor.meanRankIc !== null
        && (
          typeof factor.meanRankIc !== "number"
          || !Number.isFinite(factor.meanRankIc)
        )
      ) {
        dataIntelligenceError(`${path}.meanRankIc`);
      }
      dataOptionalString(factor.recommendation, `${path}.recommendation`);
    },
  );

  const impact = dataObject(
    intelligence.modelImpact,
    "intelligence.modelImpact",
  );
  dataString(impact.status, "intelligence.modelImpact.status");
  dataNumber(
    impact.qualifiedHorizons,
    "intelligence.modelImpact.qualifiedHorizons",
  );
  dataString(impact.activation, "intelligence.modelImpact.activation");
  dataBoolean(impact.adopted, "intelligence.modelImpact.adopted");
  dataStringList(impact.activeFactors, "intelligence.modelImpact.activeFactors");
  dataStringList(
    impact.iterationFactors,
    "intelligence.modelImpact.iterationFactors",
  );
  dataString(impact.reason, "intelligence.modelImpact.reason");
  validateBoundedNested(
    impact.horizons,
    "intelligence.modelImpact.horizons",
  );
  dataArray(impact.horizons, "intelligence.modelImpact.horizons").forEach(
    (item, index) => {
      const path = `intelligence.modelImpact.horizons[${index}]`;
      const horizon = dataObject(item, path);
      dataString(horizon.horizon, `${path}.horizon`);
      dataString(horizon.status, `${path}.status`);
      dataOptionalString(horizon.reason, `${path}.reason`);
      validateMetricRecord(horizon.support, `${path}.support`);
      validateMetricRecord(horizon.deltas, `${path}.deltas`);
      validateMetricRecord(horizon.baseMetrics, `${path}.baseMetrics`);
      validateMetricRecord(
        horizon.candidateMetrics,
        `${path}.candidateMetrics`,
      );
    },
  );
  validateDecisionCounts(intelligence.decisions, "intelligence.decisions");

  const consumerKeys = new Set<string>();
  const usage = dataArray(data.usageMatrix, "usageMatrix");
  if (usage.length !== 4) dataIntelligenceError("usageMatrix expected 4 rows");
  usage.forEach((item, index) => {
    const path = `usageMatrix[${index}]`;
    const row = dataObject(item, path);
    const key = dataString(row.consumerKey, `${path}.consumerKey`);
    if (consumerKeys.has(key)) {
      dataIntelligenceError(`${path}.consumerKey duplicate key`);
    }
    consumerKeys.add(key);
    dataString(row.consumerLabel, `${path}.consumerLabel`);
    validateUsageCell(row.structuredData, `${path}.structuredData`);
    validateUsageCell(row.traditionalFactors, `${path}.traditionalFactors`);
    validateUsageCell(row.intelligenceFactors, `${path}.intelligenceFactors`);
    if (row.modelAdoption !== undefined) {
      const adoption = dataObject(row.modelAdoption, `${path}.modelAdoption`);
      dataString(adoption.status, `${path}.modelAdoption.status`);
      [
        "modelCount",
        "resolvableManifestCount",
        "missingManifestCount",
      ].forEach((key) => (
        dataNumber(adoption[key], `${path}.modelAdoption.${key}`)
      ));
      const modelIdentities = new Set<string>();
      dataArray(adoption.models, `${path}.modelAdoption.models`).forEach(
        (modelItem, modelIndex) => {
          const modelPath = `${path}.modelAdoption.models[${modelIndex}]`;
          const model = dataObject(modelItem, modelPath);
          const horizon = dataNumber(model.horizon, `${modelPath}.horizon`);
          const version = dataString(
            model.modelVersion,
            `${modelPath}.modelVersion`,
          );
          const identity = `${horizon}:${version}`;
          if (modelIdentities.has(identity)) {
            dataIntelligenceError(`${modelPath} duplicate horizon,modelVersion`);
          }
          modelIdentities.add(identity);
          dataString(model.manifestStatus, `${modelPath}.manifestStatus`);
          dataString(model.evidence, `${modelPath}.evidence`);
          dataOptionalString(
            model.missingManifestEvidence,
            `${modelPath}.missingManifestEvidence`,
          );
        },
      );
    }
    dataString(row.impact, `${path}.impact`);
    dataOptionalString(row.lineageStatus, `${path}.lineageStatus`);
    if (row.missingManifest !== undefined) {
      dataBoolean(row.missingManifest, `${path}.missingManifest`);
    }
  });
  if (
    consumerKeys.size !== DATA_INTELLIGENCE_CONSUMERS.size
    || [...consumerKeys].some((key) => !DATA_INTELLIGENCE_CONSUMERS.has(key))
  ) {
    dataIntelligenceError("usageMatrix consumerKey set");
  }
  return data as unknown as DataIntelligenceData;
}

async function fetchJson<T>(
  url: string,
  signal?: AbortSignal,
  maxResponseBytes?: number,
): Promise<T> {
  const response = await fetch(url, { cache: "no-cache", signal });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`.trim();
    try {
      const payload = await response.json() as { message?: unknown };
      if (typeof payload.message === "string" && payload.message.trim()) {
        message = payload.message.trim();
      }
    } catch {
      // Keep the HTTP fallback when the error body is not JSON.
    }
    throw new Error(message);
  }
  if (maxResponseBytes !== undefined) {
    const body = await response.text();
    if (new TextEncoder().encode(body).byteLength > maxResponseBytes) {
      throw new Error(
        `Data intelligence response exceeds ${maxResponseBytes} bytes`,
      );
    }
    try {
      return JSON.parse(body) as T;
    } catch {
      throw new Error("Invalid JSON response");
    }
  }
  return response.json() as Promise<T>;
}

export function fetchSummary(signal?: AbortSignal): Promise<DashboardSummary> {
  return fetchJson<DashboardSummary>("/api/dashboard/summary.json", signal);
}

export function fetchSystemOverview(signal?: AbortSignal): Promise<SystemOverviewData> {
  return fetchJson<SystemOverviewData>("/api/dashboard/system-overview.json", signal);
}

export function fetchDetail(
  market: string,
  agent: string,
  signal?: AbortSignal
): Promise<DashboardDetail> {
  const params = new URLSearchParams({ market, agent });
  return fetchJson<DashboardDetail>(`/api/dashboard/detail.json?${params.toString()}`, signal);
}

function resourceUrl(resource: string, market: string, agent: string, extra?: Record<string, string>): string {
  const params = new URLSearchParams({ market, agent, ...extra });
  return `/api/dashboard/${resource}.json?${params.toString()}`;
}

export function fetchOverview(market: string, agent: string, signal?: AbortSignal): Promise<DashboardOverview> {
  return fetchJson(resourceUrl("overview", market, agent), signal);
}

export function fetchPerformance(market: string, agent: string, signal?: AbortSignal): Promise<DashboardPerformance> {
  return fetchJson(resourceUrl("performance", market, agent), signal);
}

export function fetchPortfolio(market: string, agent: string, signal?: AbortSignal): Promise<DashboardPortfolio> {
  return fetchJson(resourceUrl("portfolio", market, agent), signal);
}

export function fetchPredictions(market: string, agent: string, signal?: AbortSignal): Promise<DashboardPredictions> {
  return fetchJson(resourceUrl("predictions", market, agent, { limit_per_horizon: "12" }), signal);
}

export function fetchResearch(market: string, agent: string, signal?: AbortSignal): Promise<DashboardResearch> {
  return fetchJson(resourceUrl("research", market, agent), signal);
}

export function fetchOperations(market: string, agent: string, signal?: AbortSignal): Promise<DashboardOperations> {
  return fetchJson(resourceUrl("operations", market, agent), signal);
}

export function fetchGovernance(market: string, agent: string, signal?: AbortSignal): Promise<DashboardGovernance> {
  return fetchJson(resourceUrl("governance", market, agent), signal);
}

export function fetchIntelligence(
  market: string,
  agent: string,
  signal?: AbortSignal,
): Promise<IntelligenceSummary> {
  return fetchJson(resourceUrl("intelligence", market, agent), signal);
}

export function fetchIntelligenceEvent(
  market: string,
  agent: string,
  eventId: string,
  signal?: AbortSignal,
): Promise<IntelligenceEventDetail> {
  return fetchJson(
    resourceUrl("intelligence-event", market, agent, { event_id: eventId }),
    signal,
  );
}

export function fetchIntelligenceDocument(
  market: string,
  agent: string,
  documentId: number,
  signal?: AbortSignal,
): Promise<IntelligenceDocumentDetail> {
  return fetchJson(
    resourceUrl("intelligence-document", market, agent, {
      document_id: String(documentId),
    }),
    signal,
  );
}

export function fetchInstrument(
  market: string,
  agent: string,
  code: string,
  signal?: AbortSignal
): Promise<InstrumentDetail> {
  const params = new URLSearchParams({ market, agent, code });
  return fetchJson<InstrumentDetail>(`/api/dashboard/instrument.json?${params.toString()}`, signal);
}

export function fetchModelResearch(
  market: string,
  signal?: AbortSignal,
): Promise<ModelResearchData> {
  const params = new URLSearchParams({ market });
  return fetchJson<unknown>(
    `/api/dashboard/model-research.json?${params.toString()}`,
    signal,
  ).then(validateModelResearch);
}

export function fetchDataIntelligence(
  market: string,
  signal?: AbortSignal,
): Promise<DataIntelligenceData> {
  const params = new URLSearchParams({ market });
  return fetchJson<unknown>(
    `/api/dashboard/data-intelligence.json?${params.toString()}`,
    signal,
    DATA_INTELLIGENCE_RESPONSE_LIMIT,
  ).then(validateDataIntelligence);
}
