import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchModelResearch,
  fetchResearchUniverse,
  fetchSystemOverview,
} from "./api";

function unavailableIntelligence() {
  return {
    pipeline: {
      status: "unavailable",
      documents: 0,
      stages: {
        catalogued: 0,
        pdfReady: 0,
        parsed: 0,
        semanticCompleted: 0,
        canonicalEvents: 0,
      },
      backlog: { download: 0, parse: 0, semantic: 0, total: 0 },
      sources: [],
      artifacts: {},
    },
    extraction: {
      status: "unavailable",
      semanticRuns: {},
      decisions: {},
      latestBatch: null,
      contract: {},
    },
    factorSupply: {
      status: "unavailable",
      suppliedFactors: 0,
      modelEligibleFactors: [],
      factors: [],
      factorSets: [],
      modelEligible: false,
      lifecycleCounts: {},
      rows: 0,
    },
    modelImpact: {
      status: "unavailable",
      adopted: false,
      activeFactors: [],
      iterationFactors: [],
      qualifiedHorizons: 0,
      activation: "unchanged",
      reason: "情报证据暂不可用。",
      horizons: [],
    },
    decisions: {
      canonical: 0,
      no_event: 0,
      quarantined: 0,
      failed: 0,
    },
    recentEvents: [],
  };
}

function validSystemOverview() {
  return {
    generated_at: "2026-07-30T10:00:00+08:00",
    markets: [
      {
        market: "a_share",
        label: "A股",
        currency: "¥",
        agents: [],
        monthly: {},
      },
      {
        market: "cn_qdii_etf",
        label: "跨境ETF",
        currency: "¥",
        agents: [],
        monthly: {},
      },
    ],
    models: [
      {
        market: "a_share",
        market_label: "A股",
        iteration: {
          status: "unavailable",
          candidate: null,
          champion: null,
        },
      },
      {
        market: "cn_qdii_etf",
        market_label: "跨境ETF",
        iteration: {
          status: "unavailable",
          candidate: null,
          champion: null,
        },
      },
    ],
    strategy_model_usage: [],
    intelligence: unavailableIntelligence(),
    errors: [],
  };
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function validResearchUniversePage() {
  return {
    schemaVersion: "research-universe-browser-v1",
    status: "available",
    asOf: "20260822",
    kind: "exchange_fund",
    query: "纳斯",
    scope: "nasdaq_100",
    page: 2,
    pageSize: 50,
    total: 51,
    scopeOptions: ["nasdaq_100", "sp500"],
    records: [{
      code: "513100.SH",
      name: "纳斯达克100ETF",
      recordKind: "fund",
      researchOnly: true,
      fundType: "ETF",
      benchmark: "纳斯达克100指数",
      overseasScope: "nasdaq_100",
      classificationStatus: "name_benchmark_inferred",
      tradability: "exchange_research_only",
    }],
    executionEffect: "none_research_only",
  };
}

let systemOverviewFixture: unknown;
let modelResearchFixture: unknown;

async function currentBuilderPayload(): Promise<unknown> {
  if (systemOverviewFixture !== undefined) {
    return structuredClone(systemOverviewFixture);
  }
  // Node types are intentionally not a production dashboard dependency.
  // @ts-expect-error Vitest executes this integration fixture in Node.
  const { execFileSync } = await import("node:child_process");
  const script = [
    "import json",
    "from pathlib import Path",
    "from stock_analyze.dashboard_api import build_dashboard_system_overview_data",
    "payload = build_dashboard_system_overview_data(repo_root=Path.cwd())",
    "print(json.dumps(payload, ensure_ascii=False))",
  ].join("; ");
  const output = execFileSync("python3", ["-c", script], {
    cwd: "../..",
    encoding: "utf-8",
    maxBuffer: 1_000_000,
  });
  systemOverviewFixture = JSON.parse(output);
  return structuredClone(systemOverviewFixture);
}

async function currentModelResearchBuilderPayload(): Promise<unknown> {
  if (modelResearchFixture !== undefined) {
    return structuredClone(modelResearchFixture);
  }
  // Node types are intentionally not a production dashboard dependency.
  // @ts-expect-error Vitest executes this integration fixture in Node.
  const { execFileSync } = await import("node:child_process");
  const script = [
    "import json",
    "from pathlib import Path",
    "from stock_analyze.dashboard_workspace_api import build_dashboard_model_research_data",
    "payload = build_dashboard_model_research_data(repo_root=Path.cwd(), market='a_share')",
    "print(json.dumps(payload, ensure_ascii=False))",
  ].join("; ");
  const output = execFileSync("python3", ["-c", script], {
    cwd: "../..",
    encoding: "utf-8",
    maxBuffer: 1_000_000,
  });
  modelResearchFixture = JSON.parse(output);
  return structuredClone(modelResearchFixture);
}

function validResearchModel(index: number) {
  return {
    modelVersion: `A20-V${String(index).padStart(3, "0")}`,
    accountScope: "hs300",
    specId: `classic-mainline-${index}`,
    horizon: index,
    algorithmFamily: "boosting_ensemble",
    trainedAt: null,
    registeredAt: null,
    sampleSupport: 100,
    featureColumns: [],
    artifactRef: null,
    artifactStatus: "missing",
    gatePassed: false,
    gateReasons: [],
    shadowCycles: 0,
    shadowCyclesRemaining: 12,
    isChampion: false,
    candidateFeatureCount: 0,
    metrics: {},
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchSystemOverview", () => {
  it("accepts the current real builder payload", async () => {
    const payload = await currentBuilderPayload();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchSystemOverview()).resolves.toEqual(payload);
  }, 15_000);

  it("accepts a model impact report awaiting sufficient event support", async () => {
    const payload = validSystemOverview();
    payload.intelligence.modelImpact.status = "insufficient_support";
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchSystemOverview()).resolves.toEqual(payload);
  });

  it("strictly validates the real nested iteration contract and bounds", async () => {
    const payload = structuredClone(await currentBuilderPayload()) as any;
    payload.models[0].iteration.candidate ??= {
      market: "a_share",
      horizon: 20,
      model_version: "fixture-model",
      display_version: "A20-V001",
      status: "research",
      status_label: "研究候选",
      champion_model_version: null,
      shadow_cycles: 0,
      shadow_cycles_remaining: 12,
      registered_at: null,
      artifact: null,
    };
    payload.models[0].iteration.candidate.artifact = "models/a20-v001.json";
    payload.models[0].iteration.candidate.candidate_kind = "transparent_rule";
    payload.models[0].iteration.candidate.admission_grade = "exploratory";
    payload.models[0].iteration.candidate.source_campaign = "campaign-v1";
    payload.models[0].iteration.candidate.source_trial_id = "a-zz500-mom";
    payload.models[0].iteration.candidate.promotion_policy = "strict-forward-review-v1";
    const invalidArtifact = structuredClone(payload);
    invalidArtifact.models[0].iteration.candidate.artifact = 42;
    const invalidAdmissionGrade = structuredClone(payload);
    invalidAdmissionGrade.models[0].iteration.candidate.admission_grade = 42;
    const unknownCandidateField = structuredClone(payload);
    unknownCandidateField.models[0].iteration.candidate.debug = "leak";
    const oversizedSelection = structuredClone(payload);
    oversizedSelection.models[0].iteration.selected = Array.from(
      { length: 101 },
      () => ({
        code: "000001.SZ",
        name: "样本",
        score: 0.5,
        target_weight: 0.01,
        confidence: 0.7,
        p_up: 0.6,
        p_down: 0.4,
        expected_excess_return: 0.02,
        model_version: "A20-V001",
        reason: "测试",
      }),
    );
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(payload))
      .mockResolvedValueOnce(jsonResponse(invalidArtifact))
      .mockResolvedValueOnce(jsonResponse(invalidAdmissionGrade))
      .mockResolvedValueOnce(jsonResponse(unknownCandidateField))
      .mockResolvedValueOnce(jsonResponse(oversizedSelection));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchSystemOverview()).resolves.toEqual(payload);
    await expect(fetchSystemOverview()).rejects.toThrow(
      "models[0].iteration.candidate.artifact",
    );
    await expect(fetchSystemOverview()).rejects.toThrow(
      "models[0].iteration.candidate.admission_grade",
    );
    await expect(fetchSystemOverview()).rejects.toThrow(
      "models[0].iteration.candidate.debug",
    );
    await expect(fetchSystemOverview()).rejects.toThrow(
      "models[0].iteration.selected",
    );
  });

  it("rejects an empty object instead of trusting a type assertion", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}))));

    await expect(fetchSystemOverview()).rejects.toThrow(
      "Invalid system overview response",
    );
  });

  it("rejects a payload larger than 250KB by its UTF-8 byte length", async () => {
    const oversized = {
      ...validSystemOverview(),
      ignored: "中".repeat(84_000),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(oversized))),
    );

    await expect(fetchSystemOverview()).rejects.toThrow(
      "System overview response exceeds 250000 bytes",
    );
  });

  it("rejects duplicate market and model identities", async () => {
    const duplicateMarket = validSystemOverview();
    duplicateMarket.markets[1] = { ...duplicateMarket.markets[0] };
    const duplicateModel = validSystemOverview();
    duplicateModel.models[1] = { ...duplicateModel.models[0] };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(duplicateMarket))
      .mockResolvedValueOnce(jsonResponse(duplicateModel));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchSystemOverview()).rejects.toThrow(
      "markets[1] duplicate market",
    );
    await expect(fetchSystemOverview()).rejects.toThrow(
      "models[1] duplicate market",
    );
  });

  it("accepts a bounded partial payload with controlled lineage errors", async () => {
    const partial = {
      ...validSystemOverview(),
      strategy_model_usage: [],
      errors: [
        {
          code: "model_lineage_read_unavailable",
          section: "models",
          market: "a_share",
          message: "A股模型采用链暂不可用。",
        },
        {
          code: "strategy_model_usage_read_unavailable",
          section: "strategy_model_usage",
          message: "策略模型采用记录暂不可用。",
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(partial))));

    await expect(fetchSystemOverview()).resolves.toEqual(partial);
  });
});

describe("fetchModelResearch", () => {
  it("accepts the current real builder payload", async () => {
    const payload = await currentModelResearchBuilderPayload();
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchModelResearch("a_share")).resolves.toEqual(payload);
  }, 15_000);

  it("accepts the same model version and horizon in different account scopes", async () => {
    const payload = await currentModelResearchBuilderPayload() as any;
    const hs300 = {
      ...validResearchModel(20),
      modelVersion: "CLASSIC-V001",
      accountScope: "hs300",
    };
    const zz500 = {
      ...hs300,
      accountScope: "zz500",
    };
    payload.training = {
      ...payload.training,
      models: [hs300, zz500],
      archive: {
        total: 7,
        byStatus: { rejected: 5, research: 2 },
        recent: [{ ...hs300, modelVersion: "CLASSIC-V000" }],
      },
    };

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchModelResearch("a_share")).resolves.toEqual(payload);
  });

  it("rejects a duplicate model identity within one account scope", async () => {
    const payload = await currentModelResearchBuilderPayload() as any;
    const model = {
      ...validResearchModel(20),
      modelVersion: "CLASSIC-V001",
      accountScope: "hs300",
    };
    payload.training = {
      ...payload.training,
      models: [model, { ...model }],
    };

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchModelResearch("a_share")).rejects.toThrow(
      "Invalid model research response: training.models[1] duplicate accountScope,horizon,modelVersion",
    );
  });

  it("rejects malformed archive status counts", async () => {
    const payload = await currentModelResearchBuilderPayload() as any;
    payload.training = {
      ...payload.training,
      archive: {
        total: 1,
        byStatus: { rejected: "one" },
        recent: [],
      },
    };

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchModelResearch("a_share")).rejects.toThrow(
      "Invalid model research response: training.archive.byStatus.rejected",
    );
  });

  it("validates account-scoped transparent Shadow evidence", async () => {
    const valid = await currentModelResearchBuilderPayload() as any;
    valid.simulation.accounts = [{
      accountId: "zz500",
      scope: "zz500",
      benchmark: "000905",
      selectedCount: 50,
      candidateVersion: "rule-a-mom-v1",
      candidateLabel: "A_MOM_02",
      candidateKind: "transparent_rule",
      admissionGrade: "exploratory",
      participationStatus: "shadow_running",
      rebalanceFrequency: "monthly",
      rebalanceDue: true,
      targetRiskyExposure: 1,
      historicalNetReturn: 0.103,
      historicalNetExcessReturn: -0.042,
      historicalCostStressNetExcessReturn: -0.058,
      historicalMaxDrawdown: 0.233,
      historicalTargetFillRatio: 0.983,
    }];
    const invalidGrade = structuredClone(valid);
    invalidGrade.simulation.accounts[0].admissionGrade = 42;
    const invalidMetric = structuredClone(valid);
    invalidMetric.simulation.accounts[0].historicalNetReturn = "bad";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(valid))
      .mockResolvedValueOnce(jsonResponse(invalidGrade))
      .mockResolvedValueOnce(jsonResponse(invalidMetric));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchModelResearch("a_share")).resolves.toEqual(valid);
    await expect(fetchModelResearch("a_share")).rejects.toThrow(
      "Invalid model research response: simulation.accounts[0].admissionGrade",
    );
    await expect(fetchModelResearch("a_share")).rejects.toThrow(
      "Invalid model research response: simulation.accounts[0].historicalNetReturn",
    );
  });

  it("rejects a payload larger than 250KB by its UTF-8 byte length", async () => {
    const payload = {
      ...(await currentModelResearchBuilderPayload() as Record<string, unknown>),
      ignored: "中".repeat(84_000),
    };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchModelResearch("a_share")).rejects.toThrow(
      "Model research response exceeds 250000 bytes",
    );
  });

  it("rejects model research lists beyond their initial-response bounds", async () => {
    const base = await currentModelResearchBuilderPayload() as any;
    const cases: Array<{
      path: string;
      mutate: (payload: any) => void;
    }> = [
      {
        path: "errors exceeds 20",
        mutate: (payload) => {
          payload.errors = Array.from(
            { length: 21 },
            (_, index) => ({ resource: `resource-${index}`, reason: "unavailable" }),
          );
        },
      },
      {
        path: "stages exceeds 20",
        mutate: (payload) => {
          payload.stages = Array.from(
            { length: 21 },
            (_, index) => ({
              key: `stage-${index}`,
              label: `阶段 ${index}`,
              status: "success",
              primary: "完成",
              secondary: "有证据",
            }),
          );
        },
      },
      {
        path: "dataPreparation.sources exceeds 20",
        mutate: (payload) => {
          payload.dataPreparation.sources = Array.from(
            { length: 21 },
            (_, index) => ({
              source: `source-${index}`,
              status: "available",
              rows: index,
              failed: false,
            }),
          );
        },
      },
      {
        path: "dataPreparation.selectedFeatures exceeds 20",
        mutate: (payload) => {
          payload.dataPreparation.selectedFeatures = Array.from(
            { length: 21 },
            (_, index) => `feature-${index}`,
          );
        },
      },
      {
        path: "dataPreparation.unclassifiedFeatures exceeds 20",
        mutate: (payload) => {
          payload.dataPreparation.unclassifiedFeatures = Array.from(
            { length: 21 },
            (_, index) => `unclassified-${index}`,
          );
        },
      },
      {
        path: "dataPreparation.gaps exceeds 20",
        mutate: (payload) => {
          payload.dataPreparation.gaps = Array.from(
            { length: 21 },
            (_, index) => `gap-${index}`,
          );
        },
      },
      {
        path: "training.models exceeds 20",
        mutate: (payload) => {
          payload.training.models = Array.from(
            { length: 21 },
            (_, index) => validResearchModel(index),
          );
        },
      },
      {
        path: "validation.models exceeds 20",
        mutate: (payload) => {
          payload.validation.models = Array.from(
            { length: 21 },
            (_, index) => validResearchModel(index),
          );
        },
      },
      {
        path: "training.models[0].featureColumns exceeds 20",
        mutate: (payload) => {
          payload.training.models = [validResearchModel(1)];
          payload.training.models[0].featureColumns = Array.from(
            { length: 21 },
            (_, index) => `feature-${index}`,
          );
        },
      },
      {
        path: "training.models[0].gateReasons exceeds 20",
        mutate: (payload) => {
          payload.training.models = [validResearchModel(1)];
          payload.training.models[0].gateReasons = Array.from(
            { length: 21 },
            (_, index) => `reason-${index}`,
          );
        },
      },
      {
        path: "adoption.champions exceeds 20",
        mutate: (payload) => {
          payload.adoption.champions = Array.from(
            { length: 21 },
            (_, index) => ({
              modelVersion: `A20-V${index}`,
              horizon: index,
              activatedAt: null,
              artifactRef: null,
            }),
          );
        },
      },
      {
        path: "adoption.rollbackCandidates exceeds 5",
        mutate: (payload) => {
          payload.adoption.rollbackCandidates = Array.from(
            { length: 6 },
            (_, index) => ({
              modelVersion: `A20-V${index}`,
              displayVersion: `A20-V${index}`,
              outcome: "retired",
              endedAt: null,
            }),
          );
        },
      },
      {
        path: "adoption.strategyUsage exceeds 20",
        mutate: (payload) => {
          payload.adoption.strategyUsage = Array.from(
            { length: 21 },
            (_, index) => ({
              agent: `agent-${index}`,
              strategy_label: `策略 ${index}`,
              as_of: null,
              status: "active",
              applied_candidates: 0,
              candidate_coverage: 0,
              model_versions: {},
              fallback_reason: "",
              accounts: 1,
            }),
          );
        },
      },
    ];

    for (const testCase of cases) {
      const payload = structuredClone(base);
      testCase.mutate(payload);
      vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

      await expect(fetchModelResearch("a_share")).rejects.toThrow(
        `Invalid model research response: ${testCase.path}`,
      );
    }
  });
});

describe("fetchResearchUniverse", () => {
  it("encodes the complete scoped page request and accepts a bounded response", async () => {
    const payload = validResearchUniversePage();
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(payload)));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchResearchUniverse({
      kind: "exchange_fund",
      query: "纳斯",
      scope: "nasdaq_100",
      page: 2,
      pageSize: 50,
    })).resolves.toMatchObject({ kind: "exchange_fund", total: 51 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard/research-universe.json?kind=exchange_fund&query=%E7%BA%B3%E6%96%AF&scope=nasdaq_100&page=2&page_size=50",
      expect.objectContaining({ cache: "no-cache" }),
    );
  });

  it("rejects oversized records, execution effects, and fund rows without tradability", async () => {
    const oversized = validResearchUniversePage();
    oversized.pageSize = 100;
    oversized.records = Array.from({ length: 101 }, () => oversized.records[0]);
    const wrongEffect = validResearchUniversePage();
    wrongEffect.executionEffect = "orders_created";
    const missingTradability = validResearchUniversePage();
    delete (missingTradability.records[0] as { tradability?: string }).tradability;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(oversized))
      .mockResolvedValueOnce(jsonResponse(wrongEffect))
      .mockResolvedValueOnce(jsonResponse(missingTradability));
    vi.stubGlobal("fetch", fetchMock);

    const request = {
      kind: "exchange_fund" as const,
      query: "",
      scope: null,
      page: 1,
      pageSize: 100,
    };
    await expect(fetchResearchUniverse(request)).rejects.toThrow("records");
    await expect(fetchResearchUniverse(request)).rejects.toThrow("executionEffect");
    await expect(fetchResearchUniverse(request)).rejects.toThrow("tradability");
  });
});
