import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchSystemOverview } from "./api";

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

async function currentBuilderPayload(): Promise<unknown> {
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
  return JSON.parse(output);
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
  });

  it("strictly validates the real nested iteration contract and bounds", async () => {
    const payload = structuredClone(await currentBuilderPayload()) as any;
    payload.models[0].iteration.candidate.artifact = "models/a20-v001.json";
    const invalidArtifact = structuredClone(payload);
    invalidArtifact.models[0].iteration.candidate.artifact = 42;
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
      .mockResolvedValueOnce(jsonResponse(unknownCandidateField))
      .mockResolvedValueOnce(jsonResponse(oversizedSelection));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchSystemOverview()).resolves.toEqual(payload);
    await expect(fetchSystemOverview()).rejects.toThrow(
      "models[0].iteration.candidate.artifact",
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
