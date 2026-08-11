import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SystemOverviewPanel from "./SystemOverviewPanel";

const { fetchSystemOverview } = vi.hoisted(() => ({
  fetchSystemOverview: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, fetchSystemOverview };
});

const summary = {
  generated_at: "2026-07-29T22:00:00+08:00",
  sentiment: [],
  markets: [
    {
      market: "a_share",
      label: "A股",
      currency: "¥",
      agents: [
        {
          agent: "claude",
          strategy: { label: "稳健防守", strategy_name: "价值质量" },
          nav: { latest_display: "¥1.01M", return_display: "+1.0%" },
          tasks: { daily: { status: "success" }, weekly: { status: "success" } },
        },
        {
          agent: "codex",
          strategy: { label: "趋势进攻", strategy_name: "动量成长" },
          nav: { latest_display: "¥0.99M", return_display: "-1.0%" },
          tasks: { daily: { status: "success" }, weekly: { status: "success" } },
        },
      ],
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
};

const system = {
  generated_at: "2026-07-29T22:00:00+08:00",
  markets: summary.markets,
  models: [
    {
      market: "a_share",
      market_label: "A股",
      iteration: {
        status: "complete",
        display_version: "A20-V005",
        champion: null,
        candidate: { display_version: "A20-V005", status_label: "模拟验证" },
      },
    },
    {
      market: "cn_qdii_etf",
      market_label: "跨境ETF",
      iteration: {
        status: "complete",
        display_version: "Q5-V004",
        champion: null,
        candidate: { display_version: "Q5-V004", status_label: "模拟验证" },
      },
    },
  ],
  strategy_model_usage: [
    {
      market: "a_share",
      agent: "claude",
      strategy_label: "稳健防守",
      as_of: "2026-07-29",
      status: "rule_only",
      applied_candidates: 0,
      candidate_coverage: 0,
      model_versions: {},
      fallback_reason: "prediction_artifact_missing",
      accounts: 1,
    },
    {
      market: "a_share",
      agent: "codex",
      strategy_label: "趋势进攻",
      as_of: "2026-07-29",
      status: "rule_only",
      applied_candidates: 0,
      candidate_coverage: 0,
      model_versions: {},
      fallback_reason: "prediction_artifact_missing",
      accounts: 1,
    },
  ],
  intelligence: {
    pipeline: {
      status: "available",
      documents: 584499,
      stages: {
        catalogued: 583690,
        pdfReady: 14773,
        parsed: 6082,
        semanticCompleted: 34,
        canonicalEvents: 11,
      },
      backlog: { download: 568917, parse: 8691, semantic: 6044, total: 583652 },
      sources: [],
      artifacts: {},
    },
    extraction: {
      status: "available",
      semanticRuns: { succeeded: 42, no_event: 25, failed_terminal: 37 },
      decisions: { canonical: 11, no_event: 25, quarantined: 48, failed: 37 },
      latestBatch: {
        model: "deepseek-v4-pro",
        runs: 3,
        succeeded: 0,
        noEvent: 1,
        quarantined: 0,
        failed: 2,
        qualityStatus: "degraded",
      },
      contract: {},
    },
    factorSupply: {
      status: "complete",
      suppliedFactors: 8,
      modelEligibleFactors: [],
      factors: [],
      factorSets: [],
      modelEligible: false,
      lifecycleCounts: {},
      rows: 0,
    },
    modelImpact: {
      status: "complete",
      adopted: false,
      activeFactors: [],
      iterationFactors: [],
      qualifiedHorizons: 0,
      activation: "unchanged",
      reason: "情报因子仍处于观察或研究阶段，当前未进入正式模型。",
      horizons: [],
    },
    decisions: { canonical: 11, no_event: 25, quarantined: 48, failed: 37 },
    recentEvents: [],
  },
  errors: [],
};

describe("SystemOverviewPanel", () => {
  afterEach(() => vi.clearAllMocks());

  it("shows the real disconnected state and supports drill-down", async () => {
    const onNavigate = vi.fn();
    fetchSystemOverview.mockResolvedValue(system);
    render(
      <SystemOverviewPanel
        onNavigate={onNavigate}
      />,
    );

    expect(
      await screen.findByRole("region", { name: "决策闭环总览" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("正式策略当前完全由经典规则驱动"),
    ).toBeInTheDocument();
    expect(await screen.findByText("A20-V005")).toBeInTheDocument();
    expect(screen.getByText("Q5-V004")).toBeInTheDocument();
    expect(
      screen.getByText("情报因子仍处于观察或研究阶段，当前未进入正式模型。"),
    ).toBeInTheDocument();
    expect(screen.getByText("境内上市跨境ETF")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "查看模型迭代" }));
    await waitFor(() => {
      expect(onNavigate).toHaveBeenCalledWith(
        { view: "model-research" },
      );
    });
    expect(JSON.stringify(onNavigate.mock.calls)).not.toMatch(
      /claude|codex|model_shadow/,
    );
  });

  it("keeps partial data visible and shows controlled read failures", async () => {
    fetchSystemOverview.mockResolvedValue({
      ...system,
      strategy_model_usage: [],
      errors: [
        {
          code: "strategy_model_usage_read_unavailable",
          section: "strategy_model_usage",
          message: "策略模型采用记录暂不可用。",
        },
      ],
    });

    render(<SystemOverviewPanel onNavigate={vi.fn()} />);

    expect(await screen.findByText("A20-V005")).toBeInTheDocument();
    expect(
      screen.getByText("策略模型采用记录暂不可用。"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "决策闭环总览" }),
    ).toBeInTheDocument();
  });
});
