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
import type { ModelResearchData } from "./workspaceTypes";

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

  arrayAt(data.stages, "stages").forEach((item, index) => {
    const path = `stages[${index}]`;
    const stage = objectAt(item, path);
    stringAt(stage.key, `${path}.key`);
    stringAt(stage.label, `${path}.label`);
    const status = stringAt(stage.status, `${path}.status`);
    if (!workspaceStatuses.has(status)) modelResearchError(`${path}.status`);
    stringAt(stage.primary, `${path}.primary`);
    stringAt(stage.secondary, `${path}.secondary`);
  });

  const preparation = objectAt(data.dataPreparation, "dataPreparation");
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
  arrayAt(training.models, "training.models").forEach((model, index) => {
    validateModel(model, `training.models[${index}]`);
  });
  const validation = objectAt(data.validation, "validation");
  numberAt(validation.passed, "validation.passed");
  numberAt(validation.total, "validation.total");
  arrayAt(validation.models, "validation.models").forEach((model, index) => {
    validateModel(model, `validation.models[${index}]`);
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
  arrayAt(adoption.champions, "adoption.champions").forEach((item, index) => {
    const path = `adoption.champions[${index}]`;
    const champion = objectAt(item, path);
    stringAt(champion.modelVersion, `${path}.modelVersion`);
    numberAt(champion.horizon, `${path}.horizon`);
    optionalString(champion.activatedAt, `${path}.activatedAt`);
    optionalString(champion.artifactRef, `${path}.artifactRef`);
  });
  arrayAt(adoption.rollbackCandidates, "adoption.rollbackCandidates").forEach(
    (item, index) => {
      const path = `adoption.rollbackCandidates[${index}]`;
      const rollback = objectAt(item, path);
      stringAt(rollback.displayVersion, `${path}.displayVersion`);
    },
  );
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
    },
  );
  return data as unknown as ModelResearchData;
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
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
