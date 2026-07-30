import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IntelligencePanel } from "./IntelligencePanel";
import type { MarketIntelligence } from "./types";

const {
  fetchIntelligence,
  fetchIntelligenceEvent,
  fetchIntelligenceDocument,
} = vi.hoisted(() => ({
  fetchIntelligence: vi.fn(),
  fetchIntelligenceEvent: vi.fn(),
  fetchIntelligenceDocument: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    fetchIntelligence,
    fetchIntelligenceEvent,
    fetchIntelligenceDocument,
  };
});

let intersectionCallback: IntersectionObserverCallback;

class TestIntersectionObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = "160px";
  readonly thresholds = [0];
  constructor(callback: IntersectionObserverCallback) {
    intersectionCallback = callback;
  }
  disconnect = vi.fn();
  observe = vi.fn();
  takeRecords = vi.fn(() => []);
  unobserve = vi.fn();
}

const summary = {
  generated_at: "2026-07-24T18:00:00+08:00",
  market: "a_share",
  agent: "codex",
  pipeline: {
    status: "available",
    documents: 42,
    artifacts: { queued: 2, downloaded: 35, parsed: 31, ocr_failed: 1 },
    stages: {
      catalogued: 42,
      pdfReady: 35,
      parsed: 31,
      semanticCompleted: 30,
      canonicalEvents: 18,
    },
    backlog: { download: 7, parse: 4, semantic: 1, total: 12 },
    artifactWorkers: {
      status: "available",
      activeLeases: 1,
      leasedDocuments: 10,
      completedDocuments: 128,
      downloadedDocuments: 144,
      parsedDocuments: 128,
      latestFinishedAt: "2026-07-24T17:40:00+08:00",
      stages: {
        download: { leased: 0, importing: 0, imported: 3, partial: 1, failed: 0, expired: 0 },
        parse: { leased: 1, importing: 0, imported: 12, partial: 2, failed: 0, expired: 1 },
      },
    },
    sources: [{
      source: "tushare_anns_d",
      documents: 42,
      latestPublishedAt: "2026-07-24T08:00:00+00:00",
      lastIngestedAt: "2026-07-24T18:00:00+00:00",
      freshnessStatus: "fresh",
      latestRunStatus: "succeeded",
      fetched: 42,
      inserted: 40,
      cursor: "20260724",
      cursorUpdatedAt: "2026-07-24T18:00:00+00:00",
    }],
  },
  extraction: {
    status: "available",
    semanticRuns: { succeeded: 22, no_event: 8, failed_terminal: 2 },
    decisions: { canonical: 18, no_event: 8, quarantined: 3, failed: 2 },
    latestBatch: {
      batchKey: "deepseek:2026-07-24",
      profileId: "a-share-announcement-v1",
      provider: "deepseek",
      model: "deepseek-v4-pro",
      promptVersion: "announcement-events-v1",
      schemaVersion: "announcement-semantic-v1",
      taxonomyVersion: "announcement-events-v1",
      parserVersion: "announcement-layout-v1",
      batchDate: "2026-07-24",
      startedAt: "2026-07-24T17:50:00+08:00",
      finishedAt: "2026-07-24T18:00:00+08:00",
      runs: 34,
      succeeded: 22,
      noEvent: 8,
      quarantined: 3,
      failed: 2,
      deferred: 2,
      remaining: 2,
      inputTokens: 3100,
      outputTokens: 930,
      costMicrounits: 28000,
      requestCount: 36,
      validationRepairs: 4,
      validationRepairFailures: 1,
      successRate: 30 / 34,
      qualityStatus: "partial",
    },
    contract: {
      profileId: "a-share-announcement-v1",
      promptVersion: "announcement-events-v1",
      schemaVersion: "announcement-semantic-v1",
      taxonomyVersion: "announcement-events-v1",
    },
  },
  factorSupply: {
    status: "complete",
    snapshotDate: "20260724",
    rows: 4200,
    reportName: "factor_validation_a_share_20260724.json",
    factorSet: "event-lite-v1",
    factorSets: [{
      name: "event-lite-v1",
      state: "research",
      features: ["event_net_strength_5d"],
    }],
    factors: [{
      name: "event_net_strength_5d",
      state: "observing",
      coverage: 0.42,
      activationRate: 0.18,
      dailyIcCount: 36,
      meanRankIc: 0.021,
      recommendation: "observe",
      gateReasons: [],
    }],
    lifecycleCounts: { observing: 1 },
    suppliedFactors: 1,
    modelEligible: false,
    modelEligibleFactors: [],
  },
  modelImpact: {
    status: "complete",
    asOf: "20260724",
    snapshotDate: "20260724",
    reportName: "model_incremental_effect_a_share_20260724.json",
    factorSet: "event-lite-v1",
    qualifiedHorizons: 1,
    activation: "unchanged",
    adopted: false,
    activeFactors: [],
    iterationFactors: [],
    reason: "情报因子仍处于观察或研究阶段，当前未进入正式模型。",
    horizons: [{
      horizon: "5",
      status: "complete",
      support: { rows: 4200, covered_ratio: 0.42, active_ratio: 0.18 },
      deltas: { macro_f1: 0.012 },
      baseMetrics: {},
      candidateMetrics: {},
    }],
  },
  decisions: { canonical: 18, no_event: 8, quarantined: 3, failed: 2 },
  rows: [
    {
      decision_id: "event-1",
      decision: "canonical",
      document_id: 1,
      event_type: "buyback",
      lifecycle: "announced",
      issuer_name: "晨星科技",
      issuer_code: "600001.SH",
      event_subject: "控股股东",
      title: "关于股份回购方案的公告",
      effective_at: "2026-07-24T08:00:00+00:00",
      direction: 0.7,
      materiality: 0.8,
      relevance: 0.9,
      novelty: 0.6,
      confidence: 0.86,
    },
    {
      decision_id: "run-no-event",
      decision: "no_event",
      document_id: 2,
      event_type: null,
      lifecycle: null,
      issuer_name: "海岳材料",
      issuer_code: "600002.SH",
      title: "日常经营情况公告",
      effective_at: "2026-07-24T07:00:00+00:00",
      reason: "例行披露，未识别到重大事件",
    },
    {
      decision_id: "candidate-q",
      decision: "quarantined",
      document_id: 3,
      event_type: "major_contract",
      lifecycle: "announced",
      issuer_name: "远川工业",
      issuer_code: "600003.SH",
      title: "重大合同公告",
      effective_at: "2026-07-24T06:00:00+00:00",
      reason: "证据引文无法与原文严格对齐",
    },
    {
      decision_id: "run-failed",
      decision: "failed",
      document_id: 4,
      event_type: null,
      lifecycle: null,
      issuer_name: "启元能源",
      issuer_code: "600004.SH",
      title: "临时公告",
      effective_at: "2026-07-24T05:00:00+00:00",
      reason: "模型输出未通过结构校验",
    },
  ],
};

const canonicalDetail = {
  generated_at: "2026-07-24T18:00:00+08:00",
  market: "a_share",
  agent: "codex",
  decision: "canonical",
  event: {
    event_id: "event-1",
    event_type: "buyback",
    lifecycle: "announced",
    effective_at: "2026-07-24T08:00:00+00:00",
  },
  issuer: { name: "晨星科技", code: "600001.SH", industry: "电子" },
  scores: {
    direction: 0.7,
    materiality: 0.8,
    relevance: 0.9,
    novelty: 0.6,
    confidence: 0.86,
  },
  versions: {
    model: "deepseek-v4-pro",
    prompt_version: "announcement-events-v1",
    schema_version: "announcement-semantic-v1",
    taxonomy_version: "announcement-events-v1",
    scoring_version: "event-score-v1",
  },
  evidence: [{
    evidence_id: "ev-1",
    page_number: 3,
    quote: "公司拟使用不低于一亿元资金回购股份。",
  }],
  facts: [{
    fact_name: "回购金额下限",
    raw_value: "1亿元",
    unit: "元",
    currency: "CNY",
  }],
  document: {
    document_id: 1,
    title: "关于股份回购方案的公告",
    source_url: "https://example.com/buyback.pdf",
    published_at: "2026-07-24T08:00:00+00:00",
  },
};

describe("IntelligencePanel", () => {
  beforeEach(() => {
    vi.stubGlobal("IntersectionObserver", TestIntersectionObserver);
    fetchIntelligence.mockResolvedValue(summary);
    fetchIntelligenceEvent.mockResolvedValue(canonicalDetail);
    fetchIntelligenceDocument.mockResolvedValue({
      document: canonicalDetail.document,
      artifacts: [],
      decisions: [],
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps the decision ledger while hiding overview bands in ledger mode", async () => {
    render(
      <IntelligencePanel
        intelligence={{
          market: "a_share",
          agent: "model_shadow",
        } as unknown as MarketIntelligence}
        eager
        mode="ledger"
      />,
    );

    expect(await screen.findByText("语义决策明细")).toBeInTheDocument();
    expect(screen.queryByText("情报链路总览")).not.toBeInTheDocument();
    expect(screen.queryByText("数据源新鲜度")).not.toBeInTheDocument();
    expect(screen.queryByText("最新语义批次")).not.toBeInTheDocument();
    expect(screen.queryByText("模型增量影响")).not.toBeInTheDocument();
  });

  it("loads only after visibility and opens a traceable decision drawer", async () => {
    const user = userEvent.setup();
    render(
      <IntelligencePanel
        intelligence={{
          market: "a_share",
          agent: "codex",
        } as unknown as MarketIntelligence}
      />,
    );

    expect(fetchIntelligence).not.toHaveBeenCalled();
    act(() => {
      intersectionCallback(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });

    await screen.findByText("晨星科技");
    expect(screen.getByText("控股股东")).toBeInTheDocument();
    expect(fetchIntelligence).toHaveBeenCalledWith(
      "a_share",
      "codex",
      expect.any(AbortSignal),
    );
    expect(screen.getByText("语料获取")).toBeInTheDocument();
    expect(screen.getByText("语义抽取")).toBeInTheDocument();
    expect(screen.getAllByText("因子供给").length).toBeGreaterThan(0);
    expect(screen.getByText("模型影响")).toBeInTheDocument();
    expect(screen.getAllByText("当前未入模").length).toBeGreaterThan(0);
    expect(screen.getByText("Tushare 全量公告")).toBeInTheDocument();
    expect(screen.getByText("本机历史计算节点")).toBeInTheDocument();
    expect(screen.getByText(/已解析 128 篇 · 已下载 144 篇/)).toBeInTheDocument();
    expect(screen.getByText("运行中 1 批 · 10 篇")).toBeInTheDocument();
    expect(screen.getByText("2026-07-25 02:00")).toBeInTheDocument();
    expect(screen.getAllByText("2026-07-24 16:00").length).toBeGreaterThan(0);
    expect(screen.getByText("deepseek-v4-pro")).toBeInTheDocument();
    expect(screen.getByText("a-share-announcement-v1")).toBeInTheDocument();
    expect(screen.getByText("4,030")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("统一抽取契约")).toBeInTheDocument();
    expect(screen.getByText(/部分有效/)).toBeInTheDocument();
    expect(screen.getByText(/校验纠正 4 · 未修复 1 · 剩余 2/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /晨星科技/ }));
    const dialog = await screen.findByRole("dialog", { name: "决策详情" });
    expect(within(dialog).getByText("股份回购")).toBeInTheDocument();
    expect(within(dialog).getByText("已公告")).toBeInTheDocument();
    expect(within(dialog).getByText("600001.SH")).toBeInTheDocument();
    expect(within(dialog).getByText("上涨倾向 70%")).toBeInTheDocument();
    expect(within(dialog).getByText("80%")).toBeInTheDocument();
    expect(within(dialog).getByText("90%")).toBeInTheDocument();
    expect(within(dialog).getByText("60%")).toBeInTheDocument();
    expect(within(dialog).getByText("86%")).toBeInTheDocument();
    expect(within(dialog).getByText("deepseek-v4-pro")).toBeInTheDocument();
    expect(within(dialog).getByText("第 3 页")).toBeInTheDocument();
    expect(within(dialog).getByText(/不低于一亿元资金回购/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "查看原始 PDF" })).toHaveAttribute(
      "href",
      "https://example.com/buyback.pdf",
    );
  });

  it("filters canonical, no-event, quarantined, and failed decisions", async () => {
    const user = userEvent.setup();
    render(
      <IntelligencePanel
        intelligence={{
          market: "a_share",
          agent: "codex",
        } as unknown as MarketIntelligence}
      />,
    );
    act(() => {
      intersectionCallback(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );
    });
    await screen.findByText("晨星科技");

    await user.click(screen.getByRole("button", { name: "无事件 8" }));
    expect(screen.getByText("海岳材料")).toBeInTheDocument();
    expect(screen.getByText(/例行披露/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "已隔离 3" }));
    expect(screen.getByText("远川工业")).toBeInTheDocument();
    expect(screen.getByText(/证据引文无法/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "失败 2" }));
    expect(screen.getByText("启元能源")).toBeInTheDocument();
    expect(screen.getByText(/结构校验/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "已确认 18" }));
    await waitFor(() => expect(screen.getByText("晨星科技")).toBeInTheDocument());
  });

  it("reloads the intelligence resource when the workspace refreshes", async () => {
    const intelligence = {
      market: "a_share",
      agent: "codex",
    } as unknown as MarketIntelligence;
    const { rerender } = render(
      <IntelligencePanel
        intelligence={intelligence}
        eager
        refreshToken={0}
      />,
    );

    await screen.findByText("Tushare 全量公告");
    expect(fetchIntelligence).toHaveBeenCalledTimes(1);

    rerender(
      <IntelligencePanel
        intelligence={intelligence}
        eager
        refreshToken={1}
      />,
    );

    await waitFor(() => expect(fetchIntelligence).toHaveBeenCalledTimes(2));
  });
});
