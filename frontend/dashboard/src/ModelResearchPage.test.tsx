import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelResearchPage } from "./ModelResearchPage";

const payload = {
  generated_at: "2026-07-30T13:00:00",
  market: "a_share",
  market_label: "A股",
  truncated: false,
  truncationReason: null,
  stages: [
    {
      key: "data",
      label: "数据准备",
      status: "success",
      primary: "48 个已选特征",
      secondary: "6 个来源状态",
    },
    {
      key: "training",
      label: "模型训练",
      status: "success",
      primary: "4 个研究版本",
      secondary: "16800 条样本支持",
    },
    {
      key: "validation",
      label: "测试验收",
      status: "research",
      primary: "0 / 4 通过",
      secondary: "7 个阻塞项",
    },
    {
      key: "simulation",
      label: "模拟运行",
      status: "running",
      primary: "A20-V005",
      secondary: "0 / 12 个观察周期",
    },
    {
      key: "adoption",
      label: "正式采用",
      status: "waiting_upstream",
      primary: "0 个 Champion",
      secondary: "0 个正式策略账户已采用",
    },
  ],
  dataPreparation: {
    sources: [
      { source: "market", status: "available", rows: 1000, failed: false },
    ],
    candidateFeatureCount: 72,
    selectedFeatureCount: 48,
    structuredFeatureCount: 46,
    intelligenceFeatureCount: 1,
    unclassifiedFeatureCount: 1,
    unclassifiedFeatures: ["future_feature_not_registered"],
    selectedFeatures: ["momentum_20", "event_net_strength_5d"],
    pointInTimeAudit: "passed",
    gaps: [],
  },
  training: {
    models: [
      {
        modelVersion: "A20-V005",
        horizon: 20,
        algorithmFamily: "boosting_ensemble",
        trainedAt: "2026-07-29T23:00:00",
        registeredAt: "2026-07-30T08:30:00+08:00",
        sampleSupport: 4200,
        featureColumns: ["momentum_20"],
        artifactRef: "data/research/models/a_share/20/run-A20-V005.joblib",
        artifactStatus: "available",
        gatePassed: false,
        gateReasons: ["rank_ic_below_floor"],
        shadowCycles: 0,
        shadowCyclesRemaining: 12,
        isChampion: false,
        pointInTimeAudit: true,
        candidateFeatureCount: 72,
        metrics: { rank_ic: 0.021 },
      },
    ],
  },
  validation: {
    passed: 0,
    total: 4,
    models: [
      {
        modelVersion: "A20-V005",
        horizon: 20,
        algorithmFamily: "boosting_ensemble",
        trainedAt: "2026-07-29T23:00:00",
        registeredAt: "2026-07-30T08:30:00+08:00",
        sampleSupport: 4200,
        featureColumns: ["momentum_20"],
        artifactRef: "data/research/models/a_share/20/run-A20-V005.joblib",
        artifactStatus: "available",
        gatePassed: false,
        gateReasons: ["rank_ic_below_floor"],
        shadowCycles: 0,
        shadowCyclesRemaining: 12,
        isChampion: false,
        pointInTimeAudit: true,
        candidateFeatureCount: 72,
        metrics: { rank_ic: 0.021 },
      },
    ],
  },
  simulation: {
    status: "available",
    candidate: {
      model_version: "A20-V005",
      display_version: "A20-V005",
      status: "shadow",
      status_label: "影子观察",
      selected_at: "2026-07-29T23:30:00",
      registered_at: "2026-07-30T08:30:00+08:00",
      shadow_cycles: 0,
      shadow_cycles_remaining: 12,
      horizon: 20,
    },
    account: {
      accountId: "model-shadow-a-share",
      accountLabel: "模型独立模拟账户",
      isolation: "完全隔离，不计入双策略竞赛",
      navRows: 14,
      portfolioRef: "data/model_shadow/a_share/state.json",
    },
    predictionAsOf: null,
    predictionStatus: "missing",
    cyclesCompleted: 0,
    cyclesRequired: 12,
    decision: {
      candidateRows: 31,
      eligibleRows: 0,
      selectedCount: 0,
      tradesExecuted: 0,
      pendingOrders: 0,
      cashOnly: true,
      cashReason: "probability_gate_not_met",
      diagnostics: null,
    },
  },
  adoption: {
    champions: [],
    rollbackCandidates: [],
    strategyUsage: [
      {
        agent: "codex",
        strategy_label: "稳健防守",
        as_of: "2026-07-30",
        status: "fallback",
        applied_candidates: 0,
        candidate_coverage: 0,
        model_versions: {},
        fallback_reason: "no_champion",
        accounts: 1,
      },
    ],
  },
};

function jsonResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 503,
    statusText: ok ? "OK" : "Service Unavailable",
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModelResearchPage", () => {
  it("drills into translated gate failures and requests the selected market", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByText("0 / 4 通过")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /测试验收/ }));
    const detail = screen.getByRole("region", { name: "测试验收详情" });
    expect(within(detail).getByText("A20-V005")).toBeInTheDocument();
    expect(within(detail).getByText(/Rank IC 未达到门槛/)).toHaveTextContent(
      "rank_ic_below_floor",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dashboard/model-research.json?market=a_share",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("shows zero selected securities and the evidenced cash reason", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模拟运行/ }));

    expect(screen.getByText("0 个入选")).toBeInTheDocument();
    expect(screen.getByText(/上涨概率未达到入选门槛/)).toHaveTextContent(
      "probability_gate_not_met",
    );
  });

  it("shows training time, artifact status, and artifact reference separately", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模型训练/ }));
    const detail = screen.getByRole("region", { name: "模型训练详情" });

    expect(within(detail).getByRole("columnheader", { name: "训练时间" }))
      .toBeInTheDocument();
    expect(within(detail).getByRole("columnheader", { name: "产物状态" }))
      .toBeInTheDocument();
    expect(within(detail).getByRole("columnheader", { name: "产物引用" }))
      .toBeInTheDocument();
    expect(within(detail).getByText("2026-07-29T23:00:00"))
      .toBeInTheDocument();
    expect(within(detail).getByText("available")).toBeInTheDocument();
    expect(
      within(detail).getByText(
        "data/research/models/a_share/20/run-A20-V005.joblib",
      ),
    ).toBeInTheDocument();
  });

  it("shows independent simulation account and execution evidence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模拟运行/ }));
    const detail = screen.getByRole("region", { name: "模拟运行详情" });

    expect(within(detail).getByText("模型独立模拟账户")).toBeInTheDocument();
    expect(within(detail).getByText("model-shadow-a-share")).toBeInTheDocument();
    expect(
      within(detail).getByText("完全隔离，不计入双策略竞赛"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("成交 0 笔")).toBeInTheDocument();
    expect(within(detail).getByText("待执行 0 笔")).toBeInTheDocument();
  });

  it("shows bounded Champion adoption evidence by model and horizon", async () => {
    const championPayload = {
      ...payload,
      adoption: {
        ...payload.adoption,
        champions: [
          {
            modelVersion: "A20-V004",
            horizon: 20,
            activatedAt: "2026-07-28T10:00:00+08:00",
            artifactRef: "data/research/models/a_share/20/run-A20-V004.joblib",
          },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(championPayload)),
    );
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("0 个 Champion");
    await user.click(screen.getByRole("button", { name: /正式采用/ }));
    const detail = screen.getByRole("region", { name: "正式采用详情" });

    expect(within(detail).getByText("A20-V004")).toBeInTheDocument();
    expect(within(detail).getByText("20 日")).toBeInTheDocument();
    expect(
      within(detail).getByText("2026-07-28T10:00:00+08:00"),
    ).toBeInTheDocument();
    expect(
      within(detail).getByText(
        "data/research/models/a_share/20/run-A20-V004.joblib",
      ),
    ).toBeInTheDocument();
  });

  it("states rule-driven adoption without a Champion and shows strategy evidence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("0 个 Champion");
    await user.click(screen.getByRole("button", { name: /正式采用/ }));

    expect(screen.getByText("正式策略仍由规则驱动")).toBeInTheDocument();
    expect(screen.getByText("稳健防守")).toBeInTheDocument();
    expect(screen.getByText("no_champion")).toBeInTheDocument();
  });

  it("shows source health and unclassified feature evidence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    const detail = await screen.findByRole("region", { name: "数据准备详情" });
    expect(within(detail).getByText("market")).toBeInTheDocument();
    expect(within(detail).getByText("46")).toBeInTheDocument();
    expect(within(detail).getByText("1 个未分类")).toBeInTheDocument();
    expect(
      within(detail).getByText("future_feature_not_registered"),
    ).toBeInTheDocument();
  });

  it("rejects a malformed stage status from a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({
        ...payload,
        stages: [{ ...payload.stages[0], status: "done" }],
      })),
    );

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid model research response: stages[0].status",
    );
  });

  it("rejects a successful response with missing core sections", async () => {
    const { training: _training, ...missingTraining } = payload;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(missingTraining)),
    );

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid model research response: training",
    );
  });

  it("handles loading and failed loading", async () => {
    let resolveFetch: ((response: Response) => void) | undefined;
    const pending = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending));
    const { rerender } = render(
      <ModelResearchPage market="a_share" refreshToken={0} />,
    );
    expect(screen.getByLabelText("模型研究加载中")).toBeInTheDocument();

    resolveFetch?.(jsonResponse(payload));
    expect(await screen.findByText("0 / 4 通过")).toBeInTheDocument();

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ message: "backend down" }, false)),
    );
    rerender(<ModelResearchPage market="cn_qdii_etf" refreshToken={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("backend down");
  });

  it("resets the selected stage when the market changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { rerender } = render(
      <ModelResearchPage market="a_share" refreshToken={0} />,
    );

    await screen.findByText("0 / 4 通过");
    await user.click(screen.getByRole("button", { name: /测试验收/ }));
    expect(
      screen.getByRole("region", { name: "测试验收详情" }),
    ).toBeInTheDocument();

    rerender(<ModelResearchPage market="cn_qdii_etf" refreshToken={0} />);
    await waitFor(() => {
      expect(
        screen.getByRole("region", { name: "数据准备详情" }),
      ).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/dashboard/model-research.json?market=cn_qdii_etf",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});
