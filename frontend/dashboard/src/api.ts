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
  OperationsCenterData,
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

const SYSTEM_OVERVIEW_RESPONSE_LIMIT = 250_000;
const systemMarkets = new Set(["a_share", "cn_qdii_etf"]);
const systemAgents = new Set(["claude", "codex"]);
const systemUsageStatuses = new Set([
  "active",
  "rule_only",
  "not_recorded",
]);
const systemSectionStatuses = new Set([
  "available",
  "unavailable",
  "empty",
  "partial",
  "complete",
  "degraded",
  "healthy",
  "research",
]);
const systemIterationStatuses = new Set([
  "available",
  "unavailable",
  "complete",
  "no_candidate",
  "not_started",
  "prediction_missing",
  "active",
  "shadow",
  "research",
  "candidate",
  "pending",
]);
const systemLifecycleStatuses = new Set([
  "research",
  "shadow",
  "active",
  "retired",
]);
const systemErrorContracts = {
  market_summary_read_unavailable: {
    section: "markets",
    message: "市场概览暂不可用。",
  },
  model_lineage_read_unavailable: {
    section: "models",
    message: null,
  },
  strategy_model_usage_read_unavailable: {
    section: "strategy_model_usage",
    message: "策略模型采用记录暂不可用。",
  },
  intelligence_read_unavailable: {
    section: "intelligence",
    message: "情报链路暂不可用。",
  },
} as const;

function systemOverviewError(path: string): never {
  throw new Error(`Invalid system overview response: ${path}`);
}

function systemObject(
  value: unknown,
  path: string,
): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    systemOverviewError(path);
  }
  return value as Record<string, unknown>;
}

function systemKeys(
  value: unknown,
  path: string,
  required: readonly string[],
  allowed: readonly string[],
): Record<string, unknown> {
  const object = systemObject(value, path);
  const allowedKeys = new Set(allowed);
  for (const key of Object.keys(object)) {
    if (!allowedKeys.has(key)) systemOverviewError(`${path}.${key}`);
  }
  for (const key of required) {
    if (!Object.prototype.hasOwnProperty.call(object, key)) {
      systemOverviewError(`${path}.${key}`);
    }
  }
  return object;
}

function systemArray(
  value: unknown,
  path: string,
  limit: number,
): unknown[] {
  if (!Array.isArray(value) || value.length > limit) {
    systemOverviewError(path);
  }
  return value;
}

function systemString(value: unknown, path: string): string {
  if (typeof value !== "string") systemOverviewError(path);
  return value;
}

function systemOptionalString(value: unknown, path: string): void {
  if (value !== undefined && value !== null && typeof value !== "string") {
    systemOverviewError(path);
  }
}

function systemNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    systemOverviewError(path);
  }
  return value;
}

function systemOptionalNumber(value: unknown, path: string): void {
  if (value !== undefined && value !== null) systemNumber(value, path);
}

function systemInteger(
  value: unknown,
  path: string,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  const number = systemNumber(value, path);
  if (!Number.isInteger(number) || number < 0 || number > maximum) {
    systemOverviewError(path);
  }
  return number;
}

function systemBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") systemOverviewError(path);
  return value;
}

function validateSystemStringMap(
  value: unknown,
  path: string,
  limit: number,
): void {
  const object = systemObject(value, path);
  if (Object.keys(object).length > limit) systemOverviewError(path);
  Object.entries(object).forEach(([key, item]) => {
    if (!key || typeof item !== "string") {
      systemOverviewError(`${path}.${key}`);
    }
  });
}

function validateSystemNumberMap(
  value: unknown,
  path: string,
  limit: number,
): void {
  const object = systemObject(value, path);
  if (Object.keys(object).length > limit) systemOverviewError(path);
  Object.entries(object).forEach(([key, item]) => {
    systemNumber(item, `${path}.${key}`);
  });
}

function validateSystemTask(value: unknown, path: string): void {
  const task = systemKeys(
    value,
    path,
    [],
    ["status", "started_at", "finished_at", "error_summary"],
  );
  systemOptionalString(task.status, `${path}.status`);
  systemOptionalString(task.started_at, `${path}.started_at`);
  systemOptionalString(task.finished_at, `${path}.finished_at`);
  systemOptionalString(task.error_summary, `${path}.error_summary`);
}

function validateSystemMarket(value: unknown, path: string): string {
  const market = systemKeys(
    value,
    path,
    ["market", "label", "currency", "agents", "monthly"],
    ["market", "label", "currency", "agents", "comparison", "monthly"],
  );
  const marketKey = systemString(market.market, `${path}.market`);
  if (!systemMarkets.has(marketKey)) systemOverviewError(`${path}.market`);
  systemString(market.label, `${path}.label`);
  systemString(market.currency, `${path}.currency`);
  if (market.comparison !== undefined && market.comparison !== null) {
    systemObject(market.comparison, `${path}.comparison`);
  }
  systemKeys(
    market.monthly,
    `${path}.monthly`,
    [],
    ["status", "href", "label"],
  );
  const seenAgents = new Set<string>();
  systemArray(market.agents, `${path}.agents`, 2).forEach((item, index) => {
    const agentPath = `${path}.agents[${index}]`;
    const agent = systemKeys(
      item,
      agentPath,
      ["agent", "nav", "decision", "tasks"],
      ["agent", "strategy", "nav", "decision", "tasks"],
    );
    const agentKey = systemString(agent.agent, `${agentPath}.agent`);
    if (!systemAgents.has(agentKey)) {
      systemOverviewError(`${agentPath}.agent`);
    }
    if (seenAgents.has(agentKey)) {
      systemOverviewError(`${agentPath} duplicate agent`);
    }
    seenAgents.add(agentKey);
    if (agent.strategy !== undefined) {
      const strategy = systemKeys(
        agent.strategy,
        `${agentPath}.strategy`,
        ["agent", "label"],
        [
          "agent",
          "label",
          "description",
          "color",
          "strategy_id",
          "strategy_name",
          "holdings_source",
          "allocations",
          "lookthrough",
          "research",
          "metrics",
        ],
      );
      const strategyAgent = systemString(
        strategy.agent,
        `${agentPath}.strategy.agent`,
      );
      if (strategyAgent !== agentKey) {
        systemOverviewError(`${agentPath}.strategy.agent`);
      }
      systemString(strategy.label, `${agentPath}.strategy.label`);
      systemOptionalString(
        strategy.strategy_id,
        `${agentPath}.strategy.strategy_id`,
      );
      systemOptionalString(
        strategy.strategy_name,
        `${agentPath}.strategy.strategy_name`,
      );
    }
    const nav = systemKeys(
      agent.nav,
      `${agentPath}.nav`,
      ["latest", "latest_display", "date", "return", "return_display"],
      ["latest", "latest_display", "date", "return", "return_display"],
    );
    systemOptionalNumber(nav.latest, `${agentPath}.nav.latest`);
    systemString(nav.latest_display, `${agentPath}.nav.latest_display`);
    systemOptionalString(nav.date, `${agentPath}.nav.date`);
    systemOptionalNumber(nav.return, `${agentPath}.nav.return`);
    systemString(nav.return_display, `${agentPath}.nav.return_display`);
    const decision = systemKeys(
      agent.decision,
      `${agentPath}.decision`,
      ["href", "pending_orders", "weekly_report_href"],
      ["href", "pending_orders", "weekly_report_href"],
    );
    systemString(decision.href, `${agentPath}.decision.href`);
    systemOptionalString(
      decision.weekly_report_href,
      `${agentPath}.decision.weekly_report_href`,
    );
    const orders = systemKeys(
      decision.pending_orders,
      `${agentPath}.decision.pending_orders`,
      ["total", "buy", "sell"],
      ["total", "buy", "sell"],
    );
    ["total", "buy", "sell"].forEach((key) => (
      systemNumber(
        orders[key],
        `${agentPath}.decision.pending_orders.${key}`,
      )
    ));
    const tasks = systemKeys(
      agent.tasks,
      `${agentPath}.tasks`,
      ["daily", "weekly"],
      ["daily", "weekly"],
    );
    validateSystemTask(tasks.daily, `${agentPath}.tasks.daily`);
    validateSystemTask(tasks.weekly, `${agentPath}.tasks.weekly`);
  });
  return marketKey;
}

const iterationKeys = [
  "schema_version",
  "market",
  "account_id",
  "status",
  "label",
  "portfolio_label",
  "isolation",
  "source_agent",
  "source_type",
  "as_of",
  "prediction_as_of",
  "horizon",
  "model_version",
  "display_version",
  "model_versions",
  "decision_key",
  "decision_changed",
  "candidate_rows",
  "eligible_rows",
  "selected_count",
  "invalidated_rows",
  "minimum_confidence",
  "cash_only",
  "cash_reason",
  "decision_diagnostics",
  "trades_executed",
  "pending_orders",
  "nav_rows",
  "selected",
  "run_id",
  "updated_at",
  "lifecycle_status",
  "lifecycle_status_label",
  "champion_model_version",
  "shadow_cycles",
  "shadow_cycles_remaining",
  "prediction_path",
  "portfolio_path",
  "candidate",
  "champion",
  "version_history",
] as const;

function validateSystemModelVersion(
  value: unknown,
  path: string,
  expectedMarket: string,
): void {
  if (value === null) return;
  const version = systemKeys(
    value,
    path,
    [
      "market",
      "horizon",
      "model_version",
      "display_version",
      "status",
      "status_label",
      "champion_model_version",
      "shadow_cycles",
      "shadow_cycles_remaining",
      "registered_at",
      "artifact",
    ],
    [
      "market",
      "horizon",
      "model_version",
      "display_version",
      "status",
      "status_label",
      "champion_model_version",
      "shadow_cycles",
      "shadow_cycles_remaining",
      "registered_at",
      "artifact",
      "selected_at",
      "outcome",
      "ended_at",
    ],
  );
  const market = systemString(version.market, `${path}.market`);
  if (market !== expectedMarket || !systemMarkets.has(market)) {
    systemOverviewError(`${path}.market`);
  }
  const status = systemString(version.status, `${path}.status`);
  if (!systemLifecycleStatuses.has(status)) {
    systemOverviewError(`${path}.status`);
  }
  [
    "model_version",
    "display_version",
    "status_label",
    "champion_model_version",
    "registered_at",
    "selected_at",
    "outcome",
    "ended_at",
  ].forEach((key) => systemOptionalString(version[key], `${path}.${key}`));
  ["horizon", "shadow_cycles", "shadow_cycles_remaining"].forEach(
    (key) => systemInteger(version[key], `${path}.${key}`),
  );
  systemOptionalString(version.artifact, `${path}.artifact`);
}

function validateSystemSelected(value: unknown, path: string): void {
  systemArray(value, path, 100).forEach((item, index) => {
    const itemPath = `${path}[${index}]`;
    const selected = systemKeys(
      item,
      itemPath,
      [
        "code",
        "name",
        "score",
        "target_weight",
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
        "model_version",
        "reason",
      ],
      [
        "code",
        "name",
        "score",
        "target_weight",
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
        "model_version",
        "reason",
      ],
    );
    ["code", "name", "model_version", "reason"].forEach((key) => (
      systemOptionalString(selected[key], `${itemPath}.${key}`)
    ));
    [
      "score",
      "target_weight",
      "confidence",
      "p_up",
      "p_down",
      "expected_excess_return",
    ].forEach((key) => (
      systemOptionalNumber(selected[key], `${itemPath}.${key}`)
    ));
  });
}

function validateSystemDecisionDiagnostics(
  value: unknown,
  path: string,
): void {
  const diagnostics = systemKeys(
    value,
    path,
    ["outcome", "summary", "regime", "funnel", "near_misses"],
    ["outcome", "summary", "regime", "funnel", "near_misses"],
  );
  const outcome = systemString(diagnostics.outcome, `${path}.outcome`);
  if (outcome !== "cash" && outcome !== "selected") {
    systemOverviewError(`${path}.outcome`);
  }
  systemString(diagnostics.summary, `${path}.summary`);
  systemString(diagnostics.regime, `${path}.regime`);
  systemArray(diagnostics.funnel, `${path}.funnel`, 20)
    .forEach((item, index) => {
      const itemPath = `${path}.funnel[${index}]`;
      const stage = systemKeys(
        item,
        itemPath,
        ["key", "label", "count"],
        ["key", "label", "count"],
      );
      systemString(stage.key, `${itemPath}.key`);
      systemString(stage.label, `${itemPath}.label`);
      systemInteger(stage.count, `${itemPath}.count`);
    });
  systemArray(diagnostics.near_misses, `${path}.near_misses`, 50)
    .forEach((item, index) => {
      const itemPath = `${path}.near_misses[${index}]`;
      const nearMiss = systemKeys(
        item,
        itemPath,
        [
          "code",
          "confidence",
          "p_up",
          "p_down",
          "expected_excess_return",
          "failed_rules",
        ],
        [
          "code",
          "name",
          "confidence",
          "p_up",
          "p_down",
          "expected_excess_return",
          "failed_rules",
        ],
      );
      systemString(nearMiss.code, `${itemPath}.code`);
      systemOptionalString(nearMiss.name, `${itemPath}.name`);
      [
        "confidence",
        "p_up",
        "p_down",
        "expected_excess_return",
      ].forEach((key) => (
        systemNumber(nearMiss[key], `${itemPath}.${key}`)
      ));
      systemArray(nearMiss.failed_rules, `${itemPath}.failed_rules`, 20)
        .forEach((rule, ruleIndex) => (
          systemString(rule, `${itemPath}.failed_rules[${ruleIndex}]`)
        ));
    });
}

function validateSystemIteration(
  value: unknown,
  path: string,
  expectedMarket: string,
): void {
  const iteration = systemKeys(value, path, ["status"], iterationKeys);
  const status = systemString(iteration.status, `${path}.status`);
  if (!systemIterationStatuses.has(status)) {
    systemOverviewError(`${path}.status`);
  }
  for (const key of [
    "account_id",
    "label",
    "portfolio_label",
    "isolation",
    "source_agent",
    "source_type",
    "as_of",
    "prediction_as_of",
    "model_version",
    "display_version",
    "decision_key",
    "cash_reason",
    "run_id",
    "updated_at",
    "lifecycle_status_label",
    "champion_model_version",
    "prediction_path",
    "portfolio_path",
  ]) {
    systemOptionalString(iteration[key], `${path}.${key}`);
  }
  if (
    iteration.account_id !== undefined
    && iteration.account_id !== "model_shadow"
  ) {
    systemOverviewError(`${path}.account_id`);
  }
  if (iteration.market !== undefined) {
    const market = systemString(iteration.market, `${path}.market`);
    if (market !== expectedMarket || !systemMarkets.has(market)) {
      systemOverviewError(`${path}.market`);
    }
  }
  if (iteration.lifecycle_status !== undefined) {
    const lifecycleStatus = systemString(
      iteration.lifecycle_status,
      `${path}.lifecycle_status`,
    );
    if (!systemLifecycleStatuses.has(lifecycleStatus)) {
      systemOverviewError(`${path}.lifecycle_status`);
    }
  }
  for (const key of [
    "schema_version",
    "horizon",
    "candidate_rows",
    "eligible_rows",
    "selected_count",
    "invalidated_rows",
    "trades_executed",
    "pending_orders",
    "nav_rows",
    "shadow_cycles",
    "shadow_cycles_remaining",
  ]) {
    if (iteration[key] !== undefined) {
      systemInteger(
        iteration[key],
        `${path}.${key}`,
        key === "schema_version" ? 10 : Number.MAX_SAFE_INTEGER,
      );
    }
  }
  systemOptionalNumber(
    iteration.minimum_confidence,
    `${path}.minimum_confidence`,
  );
  for (const key of ["decision_changed", "cash_only"]) {
    if (iteration[key] !== undefined) {
      systemBoolean(iteration[key], `${path}.${key}`);
    }
  }
  if (iteration.model_versions !== undefined) {
    const seenVersions = new Set<string>();
    systemArray(iteration.model_versions, `${path}.model_versions`, 20)
      .forEach((item, index) => {
        const version = systemString(
          item,
          `${path}.model_versions[${index}]`,
        );
        if (seenVersions.has(version)) {
          systemOverviewError(`${path}.model_versions[${index}] duplicate`);
        }
        seenVersions.add(version);
      });
  }
  if (iteration.selected !== undefined) {
    validateSystemSelected(iteration.selected, `${path}.selected`);
    if (
      iteration.selected_count !== undefined
      && (iteration.selected as unknown[]).length !== iteration.selected_count
    ) {
      systemOverviewError(`${path}.selected_count`);
    }
  }
  validateSystemModelVersion(
    iteration.candidate,
    `${path}.candidate`,
    expectedMarket,
  );
  validateSystemModelVersion(
    iteration.champion,
    `${path}.champion`,
    expectedMarket,
  );
  if (iteration.version_history !== undefined) {
    systemArray(iteration.version_history, `${path}.version_history`, 50)
      .forEach((item, index) => (
        validateSystemModelVersion(
          item,
          `${path}.version_history[${index}]`,
          expectedMarket,
        )
      ));
  }
  if (
    iteration.decision_diagnostics !== undefined
    && iteration.decision_diagnostics !== null
  ) {
    validateSystemDecisionDiagnostics(
      iteration.decision_diagnostics,
      `${path}.decision_diagnostics`,
    );
  }
}

function validateSystemIntelligence(value: unknown): void {
  const intelligence = systemKeys(
    value,
    "intelligence",
    [
      "pipeline",
      "extraction",
      "factorSupply",
      "modelImpact",
      "decisions",
      "recentEvents",
    ],
    [
      "pipeline",
      "extraction",
      "factorSupply",
      "modelImpact",
      "decisions",
      "recentEvents",
    ],
  );
  const pipeline = systemKeys(
    intelligence.pipeline,
    "intelligence.pipeline",
    ["status", "documents", "artifacts", "stages", "backlog", "sources"],
    [
      "status",
      "documents",
      "artifacts",
      "stages",
      "backlog",
      "sources",
      "artifactWorkers",
      "snapshotGeneratedAt",
    ],
  );
  const pipelineStatus = systemString(
    pipeline.status,
    "intelligence.pipeline.status",
  );
  if (!systemSectionStatuses.has(pipelineStatus)) {
    systemOverviewError("intelligence.pipeline.status");
  }
  systemNumber(pipeline.documents, "intelligence.pipeline.documents");
  validateSystemNumberMap(
    pipeline.artifacts,
    "intelligence.pipeline.artifacts",
    32,
  );
  const stages = systemKeys(
    pipeline.stages,
    "intelligence.pipeline.stages",
    [
      "catalogued",
      "pdfReady",
      "parsed",
      "semanticCompleted",
      "canonicalEvents",
    ],
    [
      "catalogued",
      "pdfReady",
      "parsed",
      "semanticCompleted",
      "canonicalEvents",
    ],
  );
  [
    "catalogued",
    "pdfReady",
    "parsed",
    "semanticCompleted",
    "canonicalEvents",
  ].forEach((key) => (
    systemNumber(stages[key], `intelligence.pipeline.stages.${key}`)
  ));
  const backlog = systemKeys(
    pipeline.backlog,
    "intelligence.pipeline.backlog",
    ["download", "parse", "semantic", "total"],
    ["download", "parse", "semantic", "total"],
  );
  ["download", "parse", "semantic", "total"].forEach((key) => (
    systemNumber(backlog[key], `intelligence.pipeline.backlog.${key}`)
  ));
  systemArray(pipeline.sources, "intelligence.pipeline.sources", 16)
    .forEach((item, index) => {
      const path = `intelligence.pipeline.sources[${index}]`;
      const source = systemKeys(
        item,
        path,
        [
          "source",
          "documents",
          "freshnessStatus",
          "latestRunStatus",
          "fetched",
          "inserted",
        ],
        [
          "source",
          "documents",
          "latestPublishedAt",
          "lastIngestedAt",
          "freshnessStatus",
          "latestRunStatus",
          "fetched",
          "inserted",
          "error",
          "cursor",
          "cursorUpdatedAt",
        ],
      );
      systemString(source.source, `${path}.source`);
      ["documents", "fetched", "inserted"].forEach((key) => (
        systemNumber(source[key], `${path}.${key}`)
      ));
      systemString(source.freshnessStatus, `${path}.freshnessStatus`);
      systemString(source.latestRunStatus, `${path}.latestRunStatus`);
    });
  if (pipeline.artifactWorkers !== undefined) {
    systemObject(
      pipeline.artifactWorkers,
      "intelligence.pipeline.artifactWorkers",
    );
  }
  systemOptionalString(
    pipeline.snapshotGeneratedAt,
    "intelligence.pipeline.snapshotGeneratedAt",
  );

  const extraction = systemKeys(
    intelligence.extraction,
    "intelligence.extraction",
    ["status", "semanticRuns", "decisions", "latestBatch", "contract"],
    ["status", "semanticRuns", "decisions", "latestBatch", "contract"],
  );
  const extractionStatus = systemString(
    extraction.status,
    "intelligence.extraction.status",
  );
  if (!systemSectionStatuses.has(extractionStatus)) {
    systemOverviewError("intelligence.extraction.status");
  }
  validateSystemNumberMap(
    extraction.semanticRuns,
    "intelligence.extraction.semanticRuns",
    16,
  );
  validateSystemNumberMap(
    extraction.decisions,
    "intelligence.extraction.decisions",
    8,
  );
  if (extraction.latestBatch !== null) {
    systemObject(
      extraction.latestBatch,
      "intelligence.extraction.latestBatch",
    );
  }
  systemObject(extraction.contract, "intelligence.extraction.contract");

  const factorSupply = systemKeys(
    intelligence.factorSupply,
    "intelligence.factorSupply",
    [
      "status",
      "rows",
      "factorSets",
      "factors",
      "lifecycleCounts",
      "suppliedFactors",
      "modelEligible",
      "modelEligibleFactors",
    ],
    [
      "status",
      "snapshotDate",
      "rows",
      "reportName",
      "factorSet",
      "factorSets",
      "factors",
      "lifecycleCounts",
      "suppliedFactors",
      "modelEligible",
      "modelEligibleFactors",
    ],
  );
  const factorStatus = systemString(
    factorSupply.status,
    "intelligence.factorSupply.status",
  );
  if (!systemSectionStatuses.has(factorStatus)) {
    systemOverviewError("intelligence.factorSupply.status");
  }
  systemNumber(factorSupply.rows, "intelligence.factorSupply.rows");
  systemNumber(
    factorSupply.suppliedFactors,
    "intelligence.factorSupply.suppliedFactors",
  );
  systemBoolean(
    factorSupply.modelEligible,
    "intelligence.factorSupply.modelEligible",
  );
  systemArray(
    factorSupply.modelEligibleFactors,
    "intelligence.factorSupply.modelEligibleFactors",
    100,
  ).forEach((item, index) => (
    systemString(
      item,
      `intelligence.factorSupply.modelEligibleFactors[${index}]`,
    )
  ));
  systemArray(
    factorSupply.factorSets,
    "intelligence.factorSupply.factorSets",
    32,
  ).forEach((item, index) => (
    systemObject(item, `intelligence.factorSupply.factorSets[${index}]`)
  ));
  systemArray(
    factorSupply.factors,
    "intelligence.factorSupply.factors",
    100,
  ).forEach((item, index) => (
    systemObject(item, `intelligence.factorSupply.factors[${index}]`)
  ));
  validateSystemNumberMap(
    factorSupply.lifecycleCounts,
    "intelligence.factorSupply.lifecycleCounts",
    32,
  );

  const modelImpact = systemKeys(
    intelligence.modelImpact,
    "intelligence.modelImpact",
    [
      "status",
      "qualifiedHorizons",
      "activation",
      "adopted",
      "activeFactors",
      "iterationFactors",
      "reason",
      "horizons",
    ],
    [
      "status",
      "asOf",
      "snapshotDate",
      "reportName",
      "factorSet",
      "qualifiedHorizons",
      "activation",
      "adopted",
      "activeFactors",
      "iterationFactors",
      "reason",
      "horizons",
    ],
  );
  const impactStatus = systemString(
    modelImpact.status,
    "intelligence.modelImpact.status",
  );
  if (!systemSectionStatuses.has(impactStatus)) {
    systemOverviewError("intelligence.modelImpact.status");
  }
  systemNumber(
    modelImpact.qualifiedHorizons,
    "intelligence.modelImpact.qualifiedHorizons",
  );
  systemString(modelImpact.activation, "intelligence.modelImpact.activation");
  systemBoolean(modelImpact.adopted, "intelligence.modelImpact.adopted");
  systemString(modelImpact.reason, "intelligence.modelImpact.reason");
  ["activeFactors", "iterationFactors"].forEach((key) => (
    systemArray(modelImpact[key], `intelligence.modelImpact.${key}`, 100)
      .forEach((item, index) => (
        systemString(item, `intelligence.modelImpact.${key}[${index}]`)
      ))
  ));
  systemArray(modelImpact.horizons, "intelligence.modelImpact.horizons", 20)
    .forEach((item, index) => (
      systemObject(item, `intelligence.modelImpact.horizons[${index}]`)
    ));

  const decisions = systemKeys(
    intelligence.decisions,
    "intelligence.decisions",
    ["canonical", "no_event", "quarantined", "failed"],
    ["canonical", "no_event", "quarantined", "failed"],
  );
  ["canonical", "no_event", "quarantined", "failed"].forEach((key) => (
    systemNumber(decisions[key], `intelligence.decisions.${key}`)
  ));
  systemArray(
    intelligence.recentEvents,
    "intelligence.recentEvents",
    5,
  ).forEach((item, index) => {
    const path = `intelligence.recentEvents[${index}]`;
    const event = systemKeys(
      item,
      path,
      [
        "decision_id",
        "decision",
        "document_id",
        "issuer_name",
        "issuer_code",
        "title",
        "effective_at",
      ],
      [
        "decision_id",
        "decision",
        "document_id",
        "event_type",
        "lifecycle",
        "issuer_name",
        "issuer_code",
        "event_subject",
        "title",
        "effective_at",
        "direction",
        "materiality",
        "relevance",
        "novelty",
        "confidence",
        "reason",
      ],
    );
    systemString(event.decision_id, `${path}.decision_id`);
    const decision = systemString(event.decision, `${path}.decision`);
    if (!["canonical", "no_event", "quarantined", "failed"].includes(decision)) {
      systemOverviewError(`${path}.decision`);
    }
    systemNumber(event.document_id, `${path}.document_id`);
    ["issuer_name", "issuer_code", "title", "effective_at"].forEach((key) => (
      systemString(event[key], `${path}.${key}`)
    ));
  });
}

function validateSystemOverview(value: unknown): SystemOverviewData {
  const data = systemKeys(
    value,
    "root",
    [
      "generated_at",
      "markets",
      "models",
      "strategy_model_usage",
      "intelligence",
      "errors",
    ],
    [
      "generated_at",
      "markets",
      "models",
      "strategy_model_usage",
      "intelligence",
      "errors",
    ],
  );
  systemString(data.generated_at, "generated_at");
  const seenMarkets = new Set<string>();
  systemArray(data.markets, "markets", 2).forEach((item, index) => {
    const market = validateSystemMarket(item, `markets[${index}]`);
    if (seenMarkets.has(market)) {
      systemOverviewError(`markets[${index}] duplicate market`);
    }
    seenMarkets.add(market);
  });
  const seenModels = new Set<string>();
  systemArray(data.models, "models", 2).forEach((item, index) => {
    const path = `models[${index}]`;
    const model = systemKeys(
      item,
      path,
      ["market", "market_label", "iteration"],
      ["market", "market_label", "iteration"],
    );
    const market = systemString(model.market, `${path}.market`);
    if (!systemMarkets.has(market)) systemOverviewError(`${path}.market`);
    if (seenModels.has(market)) {
      systemOverviewError(`${path} duplicate market`);
    }
    seenModels.add(market);
    systemString(model.market_label, `${path}.market_label`);
    validateSystemIteration(model.iteration, `${path}.iteration`, market);
  });
  const seenUsage = new Set<string>();
  systemArray(data.strategy_model_usage, "strategy_model_usage", 4)
    .forEach((item, index) => {
      const path = `strategy_model_usage[${index}]`;
      const usage = systemKeys(
        item,
        path,
        [
          "market",
          "agent",
          "strategy_label",
          "status",
          "applied_candidates",
          "candidate_coverage",
          "model_versions",
          "fallback_reason",
          "accounts",
        ],
        [
          "market",
          "agent",
          "strategy_label",
          "as_of",
          "status",
          "applied_candidates",
          "candidate_coverage",
          "model_versions",
          "fallback_reason",
          "accounts",
        ],
      );
      const market = systemString(usage.market, `${path}.market`);
      const agent = systemString(usage.agent, `${path}.agent`);
      if (!systemMarkets.has(market)) systemOverviewError(`${path}.market`);
      if (!systemAgents.has(agent)) systemOverviewError(`${path}.agent`);
      const identity = `${market}:${agent}`;
      if (seenUsage.has(identity)) {
        systemOverviewError(`${path} duplicate market,agent`);
      }
      seenUsage.add(identity);
      systemString(usage.strategy_label, `${path}.strategy_label`);
      systemOptionalString(usage.as_of, `${path}.as_of`);
      const status = systemString(usage.status, `${path}.status`);
      if (!systemUsageStatuses.has(status)) {
        systemOverviewError(`${path}.status`);
      }
      systemNumber(usage.applied_candidates, `${path}.applied_candidates`);
      systemNumber(usage.candidate_coverage, `${path}.candidate_coverage`);
      validateSystemStringMap(usage.model_versions, `${path}.model_versions`, 20);
      systemString(usage.fallback_reason, `${path}.fallback_reason`);
      systemNumber(usage.accounts, `${path}.accounts`);
    });
  validateSystemIntelligence(data.intelligence);
  systemArray(data.errors, "errors", 8).forEach((item, index) => {
    const path = `errors[${index}]`;
    const error = systemKeys(
      item,
      path,
      ["code", "section", "message"],
      ["code", "section", "market", "message"],
    );
    const code = systemString(error.code, `${path}.code`);
    if (!Object.prototype.hasOwnProperty.call(systemErrorContracts, code)) {
      systemOverviewError(`${path}.code`);
    }
    const contract = systemErrorContracts[
      code as keyof typeof systemErrorContracts
    ];
    const section = systemString(error.section, `${path}.section`);
    if (section !== contract.section) {
      systemOverviewError(`${path}.section`);
    }
    const message = systemString(error.message, `${path}.message`);
    if (code === "model_lineage_read_unavailable") {
      const market = systemString(error.market, `${path}.market`);
      if (!systemMarkets.has(market)) systemOverviewError(`${path}.market`);
      const expected = market === "a_share"
        ? "A股模型采用链暂不可用。"
        : "跨境ETF模型采用链暂不可用。";
      if (message !== expected) systemOverviewError(`${path}.message`);
    } else {
      if (error.market !== undefined) systemOverviewError(`${path}.market`);
      if (message !== contract.message) {
        systemOverviewError(`${path}.message`);
      }
    }
  });
  return data as SystemOverviewData;
}

const MODEL_RESEARCH_LIST_LIMIT = 20;
const MODEL_RESEARCH_ROLLBACK_LIMIT = 5;
const MODEL_RESEARCH_RESPONSE_LIMIT = 250_000;

function modelResearchError(path: string): never {
  throw new Error(`Invalid model research response: ${path}`);
}

function objectAt(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    modelResearchError(path);
  }
  return value as Record<string, unknown>;
}

function arrayAt(
  value: unknown,
  path: string,
  limit = MODEL_RESEARCH_LIST_LIMIT,
): unknown[] {
  if (!Array.isArray(value)) modelResearchError(path);
  if (value.length > limit) modelResearchError(`${path} exceeds ${limit}`);
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
  if (data.errors !== undefined) arrayAt(data.errors, "errors");

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
  stringArray(
    preparation.selectedFeatures,
    "dataPreparation.selectedFeatures",
  );
  stringArray(preparation.gaps, "dataPreparation.gaps");
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
    optionalString(account.accountId, "simulation.account.accountId");
    stringAt(account.accountLabel, "simulation.account.accountLabel");
    stringAt(account.isolation, "simulation.account.isolation");
    numberAt(account.navRows, "simulation.account.navRows");
    optionalString(account.portfolioRef, "simulation.account.portfolioRef");
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
  arrayAt(
    adoption.rollbackCandidates,
    "adoption.rollbackCandidates",
    MODEL_RESEARCH_ROLLBACK_LIMIT,
  ).forEach((item, index) => {
    const path = `adoption.rollbackCandidates[${index}]`;
    const rollback = objectAt(item, path);
    stringAt(rollback.displayVersion, `${path}.displayVersion`);
  });
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
    if (current?.status === "partial" || current?.status === "unavailable") {
      return true;
    }
    return current?.status === undefined && laneDegraded;
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
  responseName = "Data intelligence",
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
    if (typeof response.text !== "function") {
      const payload = await response.json() as T;
      const body = JSON.stringify(payload);
      if (new TextEncoder().encode(body).byteLength > maxResponseBytes) {
        throw new Error(
          `${responseName} response exceeds ${maxResponseBytes} bytes`,
        );
      }
      return payload;
    }
    const body = await response.text();
    if (new TextEncoder().encode(body).byteLength > maxResponseBytes) {
      throw new Error(
        `${responseName} response exceeds ${maxResponseBytes} bytes`,
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
  return fetchJson<unknown>(
    "/api/dashboard/system-overview.json",
    signal,
    SYSTEM_OVERVIEW_RESPONSE_LIMIT,
    "System overview",
  ).then(validateSystemOverview);
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
    MODEL_RESEARCH_RESPONSE_LIMIT,
    "Model research",
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

const OPERATIONS_RESPONSE_LIMIT = 250_000;
const OPERATIONS_LIST_LIMIT = 20;
const OPERATIONS_TEXT_LIMIT = 1_000;
const OPERATIONS_SCOPES = new Set([
  "all",
  "a_share",
  "cn_qdii_etf",
  "exceptions",
]);
const OPERATIONS_MAIN_KEYS = new Set([
  "intelligence",
  "market_snapshot",
  "research",
  "simulation",
  "publish",
]);
const OPERATIONS_BACKGROUND_KEYS = new Set([
  "artifact_backfill",
  "reconcile",
  "semantic",
]);
const OPERATIONS_SCHEDULE_UNITS: Record<string, Set<string>> = {
  daily: new Set([
    "stock-analyze-daily-summary.timer",
    "stock-analyze-ifind-source-audit.timer",
    "stock-analyze-intelligence-artifact-backfill.timer",
    "stock-analyze-intelligence-reconcile.timer",
    "stock-analyze-intelligence-semantic.timer",
    "stock-analyze-intelligence.timer",
    "stock-analyze-market-data.timer",
  ]),
  weekly: new Set([
    "stock-analyze-claude-cn-qdii-etf-weekly.timer",
    "stock-analyze-codex-cn-qdii-etf-weekly.timer",
    "stock-analyze-qdii-research.timer",
    "stock-analyze-weekly-summary.timer",
    "stock-analyze-weekly-trigger.timer",
  ]),
  monthly: new Set([
    "stock-analyze-model-training.timer",
    "stock-analyze-monthly-review.timer",
    "stock-analyze-monthly-summary.timer",
  ]),
};
const OPERATIONS_WORKSPACE_STATUSES = new Set([
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

function operationsError(path: string): never {
  throw new Error(`Invalid operations center response: ${path}`);
}

function operationsObject(
  value: unknown,
  path: string,
): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    operationsError(path);
  }
  return value as Record<string, unknown>;
}

function operationsArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value) || value.length > OPERATIONS_LIST_LIMIT) {
    operationsError(path);
  }
  return value;
}

function operationsString(
  value: unknown,
  path: string,
  max = OPERATIONS_TEXT_LIMIT,
): string {
  if (typeof value !== "string" || value.length > max) {
    operationsError(path);
  }
  return value;
}

function operationsOptionalString(value: unknown, path: string): void {
  if (value !== undefined && value !== null) {
    operationsString(value, path);
  }
}

function operationsNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    operationsError(path);
  }
  return value;
}

function operationsStatus(value: unknown, path: string): string {
  const status = operationsString(value, path, 40);
  if (!OPERATIONS_WORKSPACE_STATUSES.has(status)) operationsError(path);
  return status;
}

function operationsUniqueRows(
  value: unknown,
  path: string,
  keyName: string,
): Record<string, unknown>[] {
  const seen = new Set<string>();
  return operationsArray(value, path).map((item, index) => {
    const rowPath = `${path}[${index}]`;
    const row = operationsObject(item, rowPath);
    const key = operationsString(row[keyName], `${rowPath}.${keyName}`);
    if (seen.has(key)) operationsError(`${rowPath}.${keyName} duplicate key`);
    seen.add(key);
    return row;
  });
}

function operationsExactKeys(
  rows: Record<string, unknown>[],
  expected: Set<string>,
  path: string,
  keyName: string,
  allowSubset: boolean,
): void {
  const actual = new Set(rows.map((row) => String(row[keyName])));
  for (const key of actual) {
    if (!expected.has(key)) operationsError(`${path} unknown key ${key}`);
  }
  if (!allowSubset) {
    for (const key of expected) {
      if (!actual.has(key)) operationsError(`${path} missing key ${key}`);
    }
  }
}

function validateOperationsUnit(
  value: unknown,
  path: string,
): void {
  const unit = operationsObject(value, path);
  operationsString(unit.unit, `${path}.unit`);
  const status = operationsStatus(unit.status, `${path}.status`);
  operationsOptionalString(unit.loadState, `${path}.loadState`);
  operationsOptionalString(unit.activeState, `${path}.activeState`);
  operationsOptionalString(unit.subState, `${path}.subState`);
  operationsOptionalString(unit.result, `${path}.result`);
  operationsOptionalString(unit.startedAt, `${path}.startedAt`);
  operationsOptionalString(unit.finishedAt, `${path}.finishedAt`);
  operationsOptionalString(unit.reason, `${path}.reason`);
  if (
    unit.exitStatus !== undefined
    && unit.exitStatus !== null
    && (typeof unit.exitStatus !== "number"
      || !Number.isInteger(unit.exitStatus))
  ) {
    operationsError(`${path}.exitStatus`);
  }
  if (
    (unit.loadState === "not-found" || unit.loadState === "masked")
    && status !== "unavailable"
  ) {
    operationsError(`${path}.status`);
  }
}

function validateOperationsBacklog(
  value: unknown,
  path: string,
): void {
  const backlog = operationsObject(value, path);
  for (const [key, count] of Object.entries(backlog)) {
    if (!["download", "parse", "semantic", "total"].includes(key)) {
      operationsError(`${path}.${key}`);
    }
    operationsNumber(count, `${path}.${key}`);
  }
}

function normalizeTruncatedOperations(value: unknown): unknown {
  if (
    value === null
    || typeof value !== "object"
    || Array.isArray(value)
  ) {
    return value;
  }
  const root = value as Record<string, unknown>;
  if (root.truncated !== true) return value;

  const normalizeRows = (
    rows: unknown,
    normalize: (row: Record<string, unknown>) => Record<string, unknown>,
  ): unknown => {
    if (!Array.isArray(rows)) return rows;
    return rows.map((item) => (
      item !== null && typeof item === "object" && !Array.isArray(item)
        ? normalize(item as Record<string, unknown>)
        : item
    ));
  };
  const normalizeBacklog = (backlog: unknown): unknown => {
    if (backlog === undefined || backlog === null) {
      return { download: 0, parse: 0, semantic: 0, total: 0 };
    }
    return backlog;
  };

  const background: Record<string, unknown> | null = (
    root.background !== null
    && typeof root.background === "object"
    && !Array.isArray(root.background)
  ) ? root.background as Record<string, unknown> : null;
  const schedules: Record<string, unknown> | null = (
    root.schedules !== null
    && typeof root.schedules === "object"
    && !Array.isArray(root.schedules)
  ) ? root.schedules as Record<string, unknown> : null;
  const disk: Record<string, unknown> | null = (
    root.disk !== null
    && typeof root.disk === "object"
    && !Array.isArray(root.disk)
  ) ? root.disk as Record<string, unknown> : null;

  return {
    ...root,
    mainChain: normalizeRows(root.mainChain, (row) => ({
      ...row,
      units: row.units === undefined ? [] : row.units,
      crossMarketUnits: row.crossMarketUnits === undefined
        ? []
        : row.crossMarketUnits,
    })),
    background: background ? {
      ...background,
      backlog: normalizeBacklog(background.backlog),
      artifactWorkers: background.artifactWorkers === undefined ? {
        status: "unavailable",
        activeLeases: 0,
        latestFinishedAt: null,
      } : background.artifactWorkers,
    } : root.background,
    backgroundWorkers: normalizeRows(root.backgroundWorkers, (row) => ({
      ...row,
      serviceUnit: row.serviceUnit === undefined ? "" : row.serviceUnit,
      timerUnit: row.timerUnit === undefined ? "" : row.timerUnit,
      backlog: row.backlog === undefined ? null : row.backlog,
    })),
    schedules: schedules
      ? Object.fromEntries(
        Object.entries(schedules).map(([cadence, rows]) => [
          cadence,
          normalizeRows(rows, (row) => ({
            ...row,
            automation: row.automation === undefined
              ? "automatic"
              : row.automation,
            lastTriggerAt: row.lastTriggerAt === undefined
              ? null
              : row.lastTriggerAt,
            nextTriggerAt: row.nextTriggerAt === undefined
              ? null
              : row.nextTriggerAt,
          })),
        ]),
      )
      : root.schedules,
    disk: disk ? {
      ...disk,
      status: disk.status === undefined ? "unavailable" : disk.status,
      usedRatio: disk.usedRatio === undefined ? null : disk.usedRatio,
    } : root.disk,
  };
}

function validateOperationsCenter(value: unknown): OperationsCenterData {
  const data = operationsObject(normalizeTruncatedOperations(value), "root");
  operationsString(data.generated_at, "generated_at");
  const scope = operationsString(data.scope, "scope");
  if (!OPERATIONS_SCOPES.has(scope)) operationsError("scope");
  const truncated = data.truncated === true;
  if (data.truncated !== undefined && typeof data.truncated !== "boolean") {
    operationsError("truncated");
  }
  operationsOptionalString(data.truncationReason, "truncationReason");
  if (truncated && data.truncationReason !== "serialized_size_limit") {
    operationsError("truncationReason");
  }

  const runtime = operationsObject(data.runtime, "runtime");
  const runtimeStatus = operationsString(runtime.status, "runtime.status");
  if (!["available", "unavailable"].includes(runtimeStatus)) {
    operationsError("runtime.status");
  }
  if (
    runtimeStatus === "available"
    && (!Object.prototype.hasOwnProperty.call(runtime, "lastKnownAt")
      || !Object.prototype.hasOwnProperty.call(runtime, "reason"))
  ) {
    operationsError("runtime available fields");
  }
  operationsOptionalString(runtime.lastKnownAt, "runtime.lastKnownAt");
  operationsOptionalString(runtime.reason, "runtime.reason");

  const freshness = operationsObject(data.dailyFreshness, "dailyFreshness");
  operationsString(freshness.asOfDate, "dailyFreshness.asOfDate");
  const freshnessStatus = operationsString(
    freshness.status,
    "dailyFreshness.status",
  );
  if (
    freshnessStatus !== "waiting"
    && !OPERATIONS_WORKSPACE_STATUSES.has(freshnessStatus)
  ) {
    operationsError("dailyFreshness.status");
  }

  const chain = operationsUniqueRows(data.mainChain, "mainChain", "key");
  operationsExactKeys(
    chain,
    OPERATIONS_MAIN_KEYS,
    "mainChain",
    "key",
    truncated || scope === "exceptions",
  );
  for (const [index, row] of chain.entries()) {
    const path = `mainChain[${index}]`;
    operationsString(row.label, `${path}.label`);
    operationsStatus(row.status, `${path}.status`);
    operationsString(row.primary, `${path}.primary`);
    operationsString(row.secondary, `${path}.secondary`);
    const units = operationsUniqueRows(row.units, `${path}.units`, "unit");
    units.forEach((unit, unitIndex) => (
      validateOperationsUnit(unit, `${path}.units[${unitIndex}]`)
    ));
    const crossMarket = operationsUniqueRows(
      row.crossMarketUnits,
      `${path}.crossMarketUnits`,
      "unit",
    );
    const unitIdentities = new Set(
      units.map((unit) => String(unit.unit)),
    );
    for (const unit of crossMarket) {
      if (unitIdentities.has(String(unit.unit))) {
        operationsError(`${path} duplicate unit identity`);
      }
    }
    crossMarket.forEach((unit, unitIndex) => (
      validateOperationsUnit(
        unit,
        `${path}.crossMarketUnits[${unitIndex}]`,
      )
    ));
  }

  const background = operationsObject(data.background, "background");
  const backgroundStatus = operationsString(
    background.status,
    "background.status",
  );
  if (!["available", "unavailable"].includes(backgroundStatus)) {
    operationsError("background.status");
  }
  operationsOptionalString(
    background.snapshotGeneratedAt,
    "background.snapshotGeneratedAt",
  );
  validateOperationsBacklog(background.backlog, "background.backlog");
  const artifacts = operationsObject(
    background.artifactWorkers,
    "background.artifactWorkers",
  );
  if (!["available", "unavailable"].includes(
    operationsString(artifacts.status, "background.artifactWorkers.status"),
  )) {
    operationsError("background.artifactWorkers.status");
  }
  operationsNumber(
    artifacts.activeLeases,
    "background.artifactWorkers.activeLeases",
  );
  operationsOptionalString(
    artifacts.latestFinishedAt,
    "background.artifactWorkers.latestFinishedAt",
  );

  const workers = operationsUniqueRows(
    data.backgroundWorkers,
    "backgroundWorkers",
    "key",
  );
  operationsExactKeys(
    workers,
    OPERATIONS_BACKGROUND_KEYS,
    "backgroundWorkers",
    "key",
    truncated || scope === "exceptions" || runtimeStatus === "unavailable",
  );
  for (const [index, worker] of workers.entries()) {
    const path = `backgroundWorkers[${index}]`;
    operationsString(worker.label, `${path}.label`);
    operationsStatus(worker.status, `${path}.status`);
    operationsString(worker.serviceUnit, `${path}.serviceUnit`);
    operationsString(worker.timerUnit, `${path}.timerUnit`);
    operationsOptionalString(worker.loadState, `${path}.loadState`);
    operationsOptionalString(worker.lastResult, `${path}.lastResult`);
    operationsOptionalString(worker.startedAt, `${path}.startedAt`);
    operationsOptionalString(worker.finishedAt, `${path}.finishedAt`);
    operationsOptionalString(worker.nextTriggerAt, `${path}.nextTriggerAt`);
    operationsOptionalString(worker.reason, `${path}.reason`);
    if (worker.backlog !== undefined && worker.backlog !== null) {
      validateOperationsBacklog(worker.backlog, `${path}.backlog`);
    }
  }

  const schedules = operationsObject(data.schedules, "schedules");
  const cadenceKeys = Object.keys(schedules);
  if (
    cadenceKeys.length !== 3
    || cadenceKeys.some((key) => !["daily", "weekly", "monthly"].includes(key))
  ) {
    operationsError("schedules");
  }
  for (const cadence of ["daily", "weekly", "monthly"]) {
    const rows = operationsUniqueRows(
      schedules[cadence],
      `schedules.${cadence}`,
      "unit",
    );
    operationsExactKeys(
      rows,
      OPERATIONS_SCHEDULE_UNITS[cadence],
      `schedules.${cadence}`,
      "unit",
      truncated,
    );
    for (const [index, row] of rows.entries()) {
      const path = `schedules.${cadence}[${index}]`;
      operationsString(row.label, `${path}.label`);
      if (!["active", "inactive", "unavailable"].includes(
        operationsString(row.status, `${path}.status`),
      )) {
        operationsError(`${path}.status`);
      }
      operationsOptionalString(row.loadState, `${path}.loadState`);
      operationsOptionalString(row.lastTriggerAt, `${path}.lastTriggerAt`);
      operationsOptionalString(row.nextTriggerAt, `${path}.nextTriggerAt`);
      operationsOptionalString(row.reason, `${path}.reason`);
      if (row.automation !== "automatic") {
        operationsError(`${path}.automation`);
      }
    }
  }

  const recentRuns = operationsUniqueRows(
    data.recentRuns,
    "recentRuns",
    "runId",
  );
  for (const [index, row] of recentRuns.entries()) {
    const path = `recentRuns[${index}]`;
    for (const key of [
      "market",
      "strategyKey",
      "strategyLabel",
      "command",
      "status",
      "startedAt",
      "finishedAt",
    ]) {
      operationsString(row[key], `${path}.${key}`);
    }
    operationsString(
      row.errorSummary,
      `${path}.errorSummary`,
      200,
    );
    operationsOptionalString(row.asOf, `${path}.asOf`);
    operationsNumber(row.durationMs, `${path}.durationMs`);
  }

  const disk = operationsObject(data.disk, "disk");
  if (!["available", "unavailable"].includes(
    operationsString(disk.status, "disk.status"),
  )) {
    operationsError("disk.status");
  }
  for (const key of ["usedRatio", "totalBytes", "freeBytes"]) {
    if (disk[key] !== undefined && disk[key] !== null) {
      operationsNumber(disk[key], `disk.${key}`);
    }
  }

  const interventions = operationsUniqueRows(
    data.interventions,
    "interventions",
    "key",
  );
  for (const [index, row] of interventions.entries()) {
    const path = `interventions[${index}]`;
    operationsString(row.severity, `${path}.severity`);
    operationsString(row.title, `${path}.title`);
    operationsString(row.evidence, `${path}.evidence`);
  }
  return data as unknown as OperationsCenterData;
}

async function fetchOperationsPayload(
  url: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(url, { cache: "no-cache", signal });
  if (!response.ok) {
    throw new Error(
      `${response.status} ${response.statusText}`.trim()
      || "Operations center request failed",
    );
  }
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > OPERATIONS_RESPONSE_LIMIT) {
    throw new Error(
      `Operations center response exceeds ${OPERATIONS_RESPONSE_LIMIT} bytes`,
    );
  }
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error("Invalid operations center response: JSON");
  }
}

export function fetchOperationsCenter(
  scope: string,
  signal?: AbortSignal,
): Promise<OperationsCenterData> {
  const params = new URLSearchParams({ scope });
  return fetchOperationsPayload(
    `/api/dashboard/operations-center.json?${params.toString()}`,
    signal,
  ).then(validateOperationsCenter);
}
