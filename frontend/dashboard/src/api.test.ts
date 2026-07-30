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

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchSystemOverview", () => {
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
