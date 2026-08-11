import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDataIntelligence } from "./api";
import { DataIntelligencePage } from "./DataIntelligencePage";
import type { DataIntelligenceData } from "./workspaceTypes";

const { fetchSystemOverview } = vi.hoisted(() => ({
  fetchSystemOverview: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, fetchSystemOverview };
});

vi.mock("./IntelligencePanel", () => ({
  IntelligencePanel: ({ mode }: { mode: string }) => (
    <div>情报证据模式：{mode}</div>
  ),
}));

function responsePayload(): DataIntelligenceData {
  const cell = (
    status: string,
    formalFactors: string[] = [],
    researchFeatures: string[] = [],
    formalEvidence: string[] = [],
    researchEvidence: string[] = [],
  ) => ({
    status,
    count: formalFactors.length + researchFeatures.length,
    countSemantics: "formal_plus_research_namespace_items",
    features: [...new Set([...formalFactors, ...researchFeatures])],
    evidence: [...formalEvidence, ...researchEvidence],
    formalCount: formalFactors.length,
    formalFactors,
    researchCount: researchFeatures.length,
    researchFeatures,
    evidenceByNamespace: {
      formal: formalEvidence,
      research: researchEvidence,
    },
  });
  const structured = cell(
    "used",
    ["momentum_20"],
    ["momentum_20", "pb"],
    ["strategy_overlay"],
    ["model_feature_manifest:20:A20-V005"],
  );
  const intelligence = cell(
    "used",
    [],
    ["event_net_strength_5d"],
    [],
    ["model_feature_manifest:20:A20-V005"],
  );
  return {
    generated_at: "2026-07-30T13:00:00+08:00",
    market: "a_share",
    market_label: "A股",
    structured: {
      stages: [
        {
          key: "sources",
          label: "行情与财务",
          status: "success",
          primary: "6 个数据源",
          secondary: "47 个模型特征 · 10 个策略因子",
        },
        {
          key: "quality",
          label: "清洗与质量",
          status: "success",
          primary: "4 / 4 个模型通过点时审计",
          secondary: "点时证据来自模型元数据",
        },
        {
          key: "traditional",
          label: "传统量化因子",
          status: "success",
          primary: "10 个正式策略 · 47 个研究模型",
          secondary: "10 个策略可用 · 72 个研究定义",
        },
      ],
      sources: [{
        source: "adjusted_ohlcv",
        researchFeatureCount: 32,
        selectedModelFeatureCount: 20,
        strategyFactorCount: 3,
        activeStrategyFactorCount: 2,
        status: "used",
        useLocations: ["研究模型 A20-V005 (20日)", "稳健防守", "趋势进攻"],
      }],
      coverage: {
        status: "available",
        rangeStart: "20230711",
        rangeEnd: "20260729",
        latestTradeDate: "20260729",
        snapshotAsOf: "20260730",
        latestSnapshot: "data/research/features/a_share/20260729.parquet",
        snapshotCount: 620,
        inspectedSnapshots: 2,
        readableSnapshots: 2,
      },
      factorGroups: [{
        family: "technical",
        definedFeatureCount: 32,
        selectedFeatureCount: 20,
      }],
      selectedFeatures: ["momentum_20", "pb"],
      formalFactorNamespace: {
        definedFactorCount: 10,
        activeFactorCount: 2,
        activeFactors: ["momentum_20", "pb"],
      },
      researchFeatureNamespace: {
        definedFeatureCount: 72,
        selectedFeatures: ["momentum_20", "pb"],
      },
      quality: {
        status: "available",
        modelCount: 4,
        pointInTimeAuditedModels: 4,
        pointInTimeFailedModels: 0,
        missingRateStatus: "not_recorded",
        outlierStatus: "not_recorded",
      },
    },
    intelligence: {
      stages: [
        {
          key: "documents",
          label: "公告与政策",
          status: "success",
          primary: "584598 篇目录",
          secondary: "2 个来源",
        },
        {
          key: "artifacts",
          label: "下载与解析",
          status: "running",
          primary: "6888 篇已解析",
          secondary: "577710 篇积压",
        },
        {
          key: "semantic",
          label: "语义事件",
          status: "success",
          primary: "12 个标准事件",
          secondary: "1 个失败",
        },
        {
          key: "intelligence_factors",
          label: "情报因子",
          status: "research",
          primary: "1 个已计算",
          secondary: "0 个可入模",
        },
      ],
      featureNamespace: {
        definedFeatureCount: 8,
        selectedFeatureCount: 1,
        selectedFeatures: ["event_net_strength_5d"],
      },
      pipeline: {
        status: "available",
        documents: 584598,
        artifacts: {},
        stages: {
          catalogued: 584598,
          pdfReady: 23243,
          parsed: 6888,
          semanticCompleted: 35,
          canonicalEvents: 12,
        },
        backlog: {
          download: 561355,
          parse: 16355,
          semantic: 6853,
          total: 584563,
        },
        artifactWorkers: {
          status: "available",
          activeLeases: 0,
          leasedDocuments: 0,
          completedDocuments: 6888,
          downloadedDocuments: 23243,
          parsedDocuments: 6888,
          stages: {
            download: {
              leased: 0,
              importing: 0,
              imported: 10,
              partial: 0,
              failed: 0,
              expired: 0,
            },
            parse: {
              leased: 0,
              importing: 0,
              imported: 10,
              partial: 0,
              failed: 0,
              expired: 0,
            },
          },
        },
        sources: [{
          source: "tushare_anns_d",
          documents: 584598,
          latestPublishedAt: "2026-07-29T08:00:00+00:00",
          lastIngestedAt: "2026-07-30T10:00:00+00:00",
          freshnessStatus: "fresh",
          latestRunStatus: "succeeded",
          fetched: 100,
          inserted: 70,
          cursor: "20260729",
        }],
      },
      extraction: {
        status: "available",
        semanticRuns: { succeeded: 12, no_event: 20, failed_terminal: 1 },
        decisions: { canonical: 12, no_event: 20, quarantined: 2, failed: 1 },
        latestBatch: null,
        contract: { profileId: "a-share-announcement-v1" },
      },
      factorSupply: {
        status: "available",
        rows: 3000,
        factorSets: [],
        factors: [{
          name: "event_net_strength_5d",
          state: "observing",
          coverage: 0.42,
          activationRate: 0.18,
          meanRankIc: 0.021,
          gateReasons: [],
          recommendation: "observe",
        }],
        lifecycleCounts: { observing: 1 },
        suppliedFactors: 1,
        modelEligible: false,
        modelEligibleFactors: [],
      },
      modelImpact: {
        status: "available",
        qualifiedHorizons: 0,
        activation: "unchanged",
        adopted: false,
        activeFactors: [],
        iterationFactors: [],
        reason: "no_factor_passed_gate",
        horizons: [],
      },
      decisions: { canonical: 12, no_event: 20, quarantined: 2, failed: 1 },
    },
    usageMatrix: [
      {
        consumerKey: "defensive",
        consumerLabel: "稳健防守",
        structuredData: structured,
        traditionalFactors: structured,
        intelligenceFactors: intelligence,
        impact: "规则与正式模型共同决策",
      },
      {
        consumerKey: "trend",
        consumerLabel: "趋势进攻",
        structuredData: structured,
        traditionalFactors: structured,
        intelligenceFactors: intelligence,
        impact: "规则驱动",
      },
      {
        consumerKey: "research_model",
        consumerLabel: "研究模型",
        structuredData: structured,
        traditionalFactors: structured,
        intelligenceFactors: intelligence,
        impact: "3 个训练特征，1 个来自情报",
      },
      {
        consumerKey: "candidate_simulation",
        consumerLabel: "候选模拟",
        structuredData: structured,
        traditionalFactors: structured,
        intelligenceFactors: intelligence,
        impact: "本期 0 个入选，0 笔成交",
      },
    ],
  };
}

function jsonResponse(payload: unknown, contentLength?: number): Response {
  const body = JSON.stringify(payload);
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    headers: {
      get: vi.fn((name: string) => (
        name.toLowerCase() === "content-length"
          ? String(contentLength ?? new TextEncoder().encode(body).byteLength)
          : null
      )),
    },
    json: vi.fn().mockResolvedValue(payload),
    text: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe("DataIntelligencePage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(responsePayload())),
    );
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows the shared pipeline once and demand-loads market evidence", async () => {
    fetchSystemOverview.mockResolvedValue({
      generated_at: "2026-08-08T18:00:00+08:00",
      markets: [
        { market: "a_share", label: "A股", agents: [], monthly: {} },
        {
          market: "cn_qdii_etf",
          label: "跨境ETF",
          agents: [],
          monthly: {},
        },
      ],
      models: [],
      strategy_model_usage: [
        {
          market: "a_share",
          agent: "codex",
          strategy_label: "趋势进攻",
          status: "rule_only",
          applied_candidates: 0,
          candidate_coverage: 0,
          model_versions: {},
          fallback_reason: "no_champion",
          accounts: 1,
        },
      ],
      intelligence: {
        pipeline: {
          status: "running",
          stages: {
            catalogued: 584598,
            pdfReady: 18020,
            parsed: 7201,
            semanticCompleted: 102,
            canonicalEvents: 41,
          },
          backlog: { download: 10, parse: 20, semantic: 30, total: 60 },
        },
        extraction: { latestBatch: { model: "deepseek", runs: 20 } },
        factorSupply: {
          status: "research",
          suppliedFactors: 8,
          modelEligibleFactors: ["event_net_strength_5d"],
          modelEligible: true,
        },
        modelImpact: {
          status: "research",
          adopted: false,
          reason: "等待增量检验",
        },
        decisions: { canonical: 41, failed: 2 },
        recentEvents: [],
      },
      errors: [],
    });
    const onFocusMarket = vi.fn();

    render(
      <DataIntelligencePage
        refreshToken={0}
        onFocusMarket={onFocusMarket}
      />,
    );

    expect(await screen.findByText("584,598")).toBeInTheDocument();
    expect(screen.getByText("18,020")).toBeInTheDocument();
    expect(screen.getByText("7,201")).toBeInTheDocument();
    expect(screen.getAllByText("41").length).toBeGreaterThan(0);
    expect(fetchSystemOverview).toHaveBeenCalledTimes(1);
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", {
      name: "查看A股数据证据",
    }));
    expect(onFocusMarket).toHaveBeenCalledWith("a_share");
  });

  it("drills into actual node data and keeps formal factors separate from research features", async () => {
    const user = userEvent.setup();
    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByRole("heading", {
      name: "结构化数据",
    })).toBeInTheDocument();
    expect(screen.getByText("文本情报")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /行情与财务/ }));
    expect(screen.getByText("复权行情数据")).toBeInTheDocument();
    expect(screen.getByText(/开盘、最高、最低、收盘/)).toBeInTheDocument();
    expect(screen.getByText("adjusted_ohlcv")).toBeInTheDocument();
    expect(screen.getAllByText("2026-07-29").length).toBeGreaterThan(0);
    expect(screen.getByText("快照日期")).toBeInTheDocument();
    expect(screen.getByText("2026-07-30")).toBeInTheDocument();
    expect(screen.getByText("研究模型 A20-V005 (20日)、稳健防守、趋势进攻")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /传统量化因子/ }));
    expect(screen.getByText("技术面特征")).toBeInTheDocument();
    expect(screen.getByText("正式策略因子")).toBeInTheDocument();
    expect(screen.getByText("研究模型特征")).toBeInTheDocument();
    expect(screen.getByText("2 / 10")).toBeInTheDocument();
    expect(screen.getByText("2 / 72")).toBeInTheDocument();

    const matrix = screen.getByRole("table", { name: "实际使用去向" });
    expect(within(matrix).getByText("研究模型")).toBeInTheDocument();
    expect(within(matrix).getByText("3 个训练特征，1 个来自情报")).toBeInTheDocument();
    expect(within(matrix).getAllByText(/正式 1 · 研究 2/).length).toBeGreaterThan(0);
  });

  it("lazy-mounts the evidence ledger only after semantic drill-down", async () => {
    const user = userEvent.setup();
    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    await screen.findByText("文本情报");
    expect(screen.queryByText("情报证据模式：ledger")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /语义事件/ }));
    expect(screen.getByText("情报证据模式：ledger")).toBeInTheDocument();
  });

  it("does not double-fetch a non-zero initial refresh token", async () => {
    const { rerender } = render(
      <DataIntelligencePage market="a_share" refreshToken={7} />,
    );
    await screen.findByRole("heading", { name: "结构化数据" });
    expect(fetch).toHaveBeenCalledTimes(1);
    rerender(<DataIntelligencePage market="a_share" refreshToken={7} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    rerender(<DataIntelligencePage market="a_share" refreshToken={8} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/claude|codex|model_shadow/i)).not.toBeInTheDocument();
  });

  it("preserves structured data when a truncated intelligence lane is partial", async () => {
    const payload = responsePayload() as unknown as Record<string, unknown>;
    payload.truncated = true;
    payload.truncationReason = "serialized_size_limit";
    const intelligence = payload.intelligence as Record<string, unknown>;
    intelligence.truncated = true;
    intelligence.truncationReasons = ["node_budget_exhausted"];
    intelligence.pipeline = { status: "partial" };
    intelligence.extraction = { status: "unavailable" };
    intelligence.factorSupply = { status: "unavailable" };
    intelligence.modelImpact = { status: "unavailable" };
    intelligence.decisions = {};
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));
    const user = userEvent.setup();

    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByText("adjusted_ohlcv")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /下载与解析/ }));
    expect(screen.getByText("公告目录")).toBeInTheDocument();
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    expect(screen.getByText("状态不可用")).toBeInTheDocument();
    expect(screen.getByText("内容超过安全展示上限，已安全截断")).toBeInTheDocument();
    expect(screen.queryByText("serialized_size_limit")).not.toBeInTheDocument();
  });

  it("drops an incomplete trailing source from an explicitly truncated lane", async () => {
    const payload = responsePayload() as unknown as Record<string, unknown>;
    const intelligence = payload.intelligence as Record<string, unknown>;
    const pipeline = intelligence.pipeline as Record<string, unknown>;
    intelligence.truncated = true;
    intelligence.truncationReasons = ["node_budget_exhausted"];
    delete pipeline.status;
    pipeline.sources = [
      ...pipeline.sources as unknown[],
      { documents: 212, fetched: 0 },
    ];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    await expect(fetchDataIntelligence("a_share")).resolves.toMatchObject({
      intelligence: {
        pipeline: {
          sources: [expect.objectContaining({ source: "tushare_anns_d" })],
        },
      },
    });
  });

  it("shows a partial-status banner without hiding the structured lane", async () => {
    const value = responsePayload();
    value.errors = [{ resource: "intelligence", reason: "unavailable" }];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(value));

    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByText(/部分状态不可用/)).toHaveTextContent(
      "intelligence",
    );
    expect(screen.getByRole("heading", { name: "结构化数据" }))
      .toBeInTheDocument();
    expect(screen.getByText("adjusted_ohlcv")).toBeInTheDocument();
  });

  it("keeps explicitly available sections strict when the lane is truncated", async () => {
    const payload = responsePayload() as unknown as Record<string, unknown>;
    payload.truncated = true;
    const intelligence = payload.intelligence as Record<string, unknown>;
    intelligence.truncated = true;
    intelligence.pipeline = {
      ...(intelligence.pipeline as Record<string, unknown>),
      status: "available",
      documents: "malformed",
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    await expect(fetchDataIntelligence("a_share")).rejects.toThrow(
      "Invalid data intelligence response: intelligence.pipeline.documents",
    );
  });

  it("accepts the bounded production semantic batch manifest", async () => {
    const payload = responsePayload();
    payload.intelligence.extraction.latestBatch = {
      batchDate: "2026-08-08",
      batchKey: "semantic-20260808-001",
      costMicrounits: 1200,
      deferred: 3,
      failed: 1,
      finishedAt: "2026-08-08T03:12:00+00:00",
      inputTokens: 24000,
      model: "deepseek-chat",
      noEvent: 8,
      outputTokens: 4000,
      parserVersion: "v1",
      profileId: "a-share-announcement-v1",
      promptVersion: "v1",
      provider: "deepseek",
      qualityStatus: "accepted",
      quarantined: 2,
      remaining: 40,
      requestCount: 12,
      runs: 12,
      schemaVersion: "v1",
      startedAt: "2026-08-08T03:00:00+00:00",
      succeeded: 11,
      successRate: 0.9167,
      taxonomyVersion: "v4",
      validationRepairFailures: 0,
      validationRepairs: 1,
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    await expect(fetchDataIntelligence("a_share")).resolves.toMatchObject({
      intelligence: {
        extraction: {
          latestBatch: { batchKey: "semantic-20260808-001" },
        },
      },
    });
  });

  it("uses a safe Chinese fallback for unknown truncation reasons", async () => {
    const payload = responsePayload();
    payload.truncated = true;
    payload.truncationReason = "future_truncation_code";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByText("内容过多，已安全截断")).toBeInTheDocument();
    expect(screen.queryByText("future_truncation_code")).not.toBeInTheDocument();
  });

  it("renders operational states and recommendations in Chinese", async () => {
    const payload = responsePayload();
    payload.structured.sources[0].status = "declared";
    payload.intelligence.pipeline.sources[0].freshnessStatus = "stale";
    payload.intelligence.pipeline.artifactWorkers.status = "unavailable";
    payload.usageMatrix[0].lineageStatus = "active";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));
    const user = userEvent.setup();

    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByText("已声明")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /清洗与质量/ }));
    expect(screen.getAllByText("未记录").length).toBeGreaterThanOrEqual(2);
    await user.click(screen.getByRole("button", { name: /公告与政策/ }));
    expect(screen.getByText("已过期")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /下载与解析/ }));
    expect(screen.getByText("状态不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /情报因子/ }));
    expect(screen.getByText("观察中")).toBeInTheDocument();
    expect(screen.getByText("继续观察")).toBeInTheDocument();
    expect(screen.getByText(/正式启用/)).toBeInTheDocument();
    expect(screen.queryByText(
      /declared|not_recorded|stale|unavailable|observing|observe|active/,
    )).not.toBeInTheDocument();
  });

  it("uses a safe Chinese fallback for unknown states", async () => {
    const payload = responsePayload();
    payload.structured.sources[0].status = "future_state";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByText("未知状态")).toBeInTheDocument();
    expect(screen.queryByText("future_state")).not.toBeInTheDocument();
  });

  it("keeps active lineage visible when its feature manifest is unavailable", async () => {
    const payload = responsePayload();
    const defensive = payload.usageMatrix[0];
    defensive.traditionalFactors = {
      ...defensive.traditionalFactors,
      status: "unavailable",
      count: 0,
      features: [],
      evidence: [],
      formalCount: 0,
      formalFactors: [],
      researchCount: 0,
      researchFeatures: [],
      evidenceByNamespace: { formal: [], research: [] },
      researchStatus: "unavailable",
      missingManifestEvidence: ["missing_manifest:20:RETIRED-MANIFEST"],
    };
    defensive.modelAdoption = {
      status: "active",
      modelCount: 1,
      resolvableManifestCount: 0,
      missingManifestCount: 1,
      models: [{
        horizon: 20,
        modelVersion: "RETIRED-MANIFEST",
        manifestStatus: "unavailable",
        evidence: "decision_lineage:20:RETIRED-MANIFEST",
        missingManifestEvidence: "missing_manifest:20:RETIRED-MANIFEST",
      }],
    };
    defensive.impact = "正式决策采用 1 个模型版本";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    render(<DataIntelligencePage market="a_share" refreshToken={0} />);

    expect(await screen.findByText(
      "正式决策采用 1 个模型版本 · 1 个模型清单缺失",
    )).toBeInTheDocument();
    expect(screen.queryByText("本期规则驱动")).not.toBeInTheDocument();
  });

  it.each([
    ["duplicate structured stage keys", (payload: ReturnType<typeof responsePayload>) => {
      payload.structured.stages[1].key = "sources";
    }],
    ["duplicate intelligence stage keys", (payload: ReturnType<typeof responsePayload>) => {
      payload.intelligence.stages[1].key = "documents";
    }],
    ["duplicate consumer keys", (payload: ReturnType<typeof responsePayload>) => {
      payload.usageMatrix[1].consumerKey = "defensive";
    }],
    ["over-limit feature lists", (payload: ReturnType<typeof responsePayload>) => {
      payload.structured.researchFeatureNamespace.selectedFeatures =
        Array.from({ length: 21 }, (_, index) => `feature_${index}`);
    }],
  ])("rejects malformed 200 responses with %s", async (_label, mutate) => {
    const payload = responsePayload();
    mutate(payload);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    await expect(fetchDataIntelligence("a_share")).rejects.toThrow(
      "Invalid data intelligence response",
    );
  });

  it("accepts a bounded body when Content-Length is forged too large", async () => {
    const response = jsonResponse(responsePayload(), 250_001);
    vi.mocked(fetch).mockResolvedValueOnce(response);

    await expect(fetchDataIntelligence("a_share")).resolves.toMatchObject({
      market: "a_share",
    });
    expect(response.text).toHaveBeenCalledTimes(1);
    expect(response.json).not.toHaveBeenCalled();
  });

  it("rejects oversized nested prose even when transport metadata lies", async () => {
    const payload = responsePayload() as unknown as Record<string, unknown>;
    const intelligence = payload.intelligence as Record<string, unknown>;
    const pipeline = intelligence.pipeline as Record<string, unknown>;
    pipeline.artifactWorkers = {
      status: "available",
      diagnostic: {
        raw: "x".repeat(260_000),
      },
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload, 100));

    await expect(fetchDataIntelligence("a_share")).rejects.toThrow(
      "Data intelligence response exceeds 250000 bytes",
    );
  });

  it("rejects an oversized body when Content-Length is missing", async () => {
    const payload = responsePayload() as unknown as Record<string, unknown>;
    payload.padding = "x".repeat(260_000);
    const response = jsonResponse(payload);
    vi.mocked(response.headers.get).mockReturnValue(null);
    vi.mocked(fetch).mockResolvedValueOnce(response);

    await expect(fetchDataIntelligence("a_share")).rejects.toThrow(
      "Data intelligence response exceeds 250000 bytes",
    );
  });

  it("rejects deeply nested diagnostic prose below the transport limit", async () => {
    const payload = responsePayload() as unknown as Record<string, unknown>;
    const intelligence = payload.intelligence as Record<string, unknown>;
    const extraction = intelligence.extraction as Record<string, unknown>;
    extraction.semanticRuns = {
      succeeded: 12,
      diagnostic: {
        raw: "x".repeat(5_000),
      },
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    await expect(fetchDataIntelligence("a_share")).rejects.toThrow(
      "Invalid data intelligence response: intelligence.extraction.semanticRuns",
    );
  });

  it("rejects four unique but non-canonical usage consumer keys", async () => {
    const payload = responsePayload();
    payload.usageMatrix[0].consumerKey = "defensive_alias";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload));

    await expect(fetchDataIntelligence("a_share")).rejects.toThrow(
      "usageMatrix consumerKey set",
    );
  });
});
