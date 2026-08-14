import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ModelResearchPage } from "./ModelResearchPage";

const { fetchSystemOverview } = vi.hoisted(() => ({
  fetchSystemOverview: vi.fn(),
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, fetchSystemOverview };
});

vi.mock("./OperationsPage", () => ({
  OperationsPage: () => <div>运行中心占位</div>,
}));

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
        accountScope: "hs300",
        specId: "h20_momentum_anchor_quality_residual_ridge_v2",
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
        diagnosticNetExcessReturn: 0.041,
        netExcessReturn: 0.018,
        calibrationStatus: "available",
        capitalUtilization: 0.92,
        baselineComparison: {
          transparent_baseline: { net_excess_return: 0.025 },
          candidate_increment: { net_excess_return_delta: -0.007 },
        },
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
        accountScope: "hs300",
        specId: "h20_momentum_anchor_quality_residual_ridge_v2",
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
        diagnosticNetExcessReturn: 0.041,
        netExcessReturn: 0.018,
        calibrationStatus: "available",
        capitalUtilization: 0.92,
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
      modelEligibleRows: 3,
      eligibleRows: 0,
      scopeRejectedRows: 3,
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
  fetchSystemOverview.mockReset();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("ModelResearchPage", () => {
  it("shows both markets before loading any market detail", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    fetchSystemOverview.mockResolvedValue({
      generated_at: "2026-08-08T18:00:00+08:00",
      markets: [],
      models: [
        {
          market: "a_share",
          market_label: "A股",
          iteration: {
            status: "running",
            candidate: {
              display_version: "A20-V005",
              status_label: "影子观察",
              shadow_cycles: 4,
              shadow_cycles_remaining: 8,
            },
            champion: null,
          },
        },
        {
          market: "cn_qdii_etf",
          market_label: "跨境ETF",
          iteration: {
            status: "complete",
            candidate: {
              display_version: "Q5-V004",
              status_label: "等待验收",
              shadow_cycles: 12,
              shadow_cycles_remaining: 0,
            },
            champion: { display_version: "Q5-V003" },
          },
        },
      ],
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
        {
          market: "cn_qdii_etf",
          agent: "codex",
          strategy_label: "趋势进攻",
          status: "active",
          applied_candidates: 3,
          candidate_coverage: 0.3,
          model_versions: { "5": "Q5-V003" },
          fallback_reason: "",
          accounts: 1,
        },
      ],
      intelligence: {},
      errors: [],
    });
    const onFocusMarket = vi.fn();

    render(
      <ModelResearchPage
        refreshToken={0}
        onFocusMarket={onFocusMarket}
      />,
    );

    expect(await screen.findByText("A20-V005")).toBeInTheDocument();
    expect(screen.getByText("Q5-V004")).toBeInTheDocument();
    expect(screen.getByText("Q5-V003")).toBeInTheDocument();
    expect(fetchSystemOverview).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", {
      name: "查看A股模型详情",
    }));
    expect(onFocusMarket).toHaveBeenCalledWith("a_share");
  });

  it("distinguishes a completed rejection from unavailable runtime data", async () => {
    vi.stubGlobal("fetch", vi.fn());
    fetchSystemOverview.mockResolvedValue({
      generated_at: "2026-08-14T09:30:00+08:00",
      markets: [],
      models: [
        {
          market: "a_share",
          market_label: "A股",
          iteration: {
            status: "no_candidate",
            as_of: "2026-08-13",
            candidate: null,
            champion: null,
          },
        },
      ],
      strategy_model_usage: [],
      intelligence: {},
      errors: [],
    });

    render(<ModelResearchPage refreshToken={0} />);

    expect(
      await screen.findByText("本轮无合格候选"),
    ).toBeInTheDocument();
    expect(screen.getByText("2026-08-13")).toBeInTheDocument();
    expect(screen.getByText("最新主线未通过验收")).toBeInTheDocument();
    expect(screen.queryByText("状态不可用")).not.toBeInTheDocument();
  });

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

  it("shows the sealed campaign decision without implying formal activation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      ...payload,
      strategyCampaign: {
        status: "complete",
        campaignId: "strategy-recovery-20260814-v1",
        manifestHash: "manifest-hash",
        completedAt: "2026-08-14T18:00:00",
        formalStrategyActivated: false,
        scopes: [{
          accountScope: "hs300",
          status: "baseline_only",
          selectedRuleSpecId: "A_MOM_01",
          selectedIncrementalSpecId: null,
          bestDiagnosticSpecId: null,
          diagnosticOnly: false,
          reasons: ["ml_no_proven_increment"],
          transparentTrialCount: 6,
          incrementalTrialCount: 2,
          netReturn: 0.08,
          benchmarkReturn: 0.03,
          netExcessReturn: 0.04,
          sharpe: 0.9,
          maxDrawdown: 0.1,
          targetFillRatio: 0.99,
          costStressNetExcessReturn: 0.02,
          deflatedSharpeProbability: 0.97,
          probabilityOfBacktestOverfit: 0.25,
          pairedBootstrapProbability: 0.82,
          attribution: { status: "reconciled" },
          folds: [],
          regimes: {},
        }, {
          accountScope: "zz500",
          status: "falsified",
          selectedRuleSpecId: null,
          selectedIncrementalSpecId: null,
          bestDiagnosticSpecId: "A_MOM_02",
          diagnosticOnly: true,
          reasons: ["no_transparent_candidate_passed_gates_1_2"],
          transparentTrialCount: 6,
          incrementalTrialCount: 0,
          netReturn: 0.31,
          benchmarkReturn: 0.32,
          netExcessReturn: -0.0035,
          sharpe: 0.99,
          maxDrawdown: 0.19,
          targetFillRatio: 0.99,
          costStressNetExcessReturn: -0.02,
          deflatedSharpeProbability: 0.04,
          probabilityOfBacktestOverfit: 0,
          pairedBootstrapProbability: null,
          attribution: { status: "reconciled" },
          folds: [],
          regimes: {},
        }],
      },
    })));

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByText("封闭策略验证")).toBeInTheDocument();
    expect(screen.getByText("仅规则基线")).toBeInTheDocument();
    expect(screen.getByText("A_MOM_01")).toBeInTheDocument();
    expect(screen.getByText("A_MOM_02（仅诊断）")).toBeInTheDocument();
    expect(screen.getByText("未接入正式策略")).toBeInTheDocument();
  });

  it("shows formal rules and candidate models in one historical window", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({
        ...payload,
        historicalComparison: {
          status: "complete",
          evidenceType: "historical_diagnostic",
          asOf: "20260813",
          horizon: 20,
          scopes: [{
            accountScope: "hs300",
            finalWindow: ["20260105", "20260731"],
            evaluationDateCount: 140,
            winner: {
              participantId: "model:a20-v1",
              name: "A20",
              netExcessReturn: 0.03,
            },
            participants: [
              {
                participantId: "rule:defensive",
                participantType: "formal_rule",
                name: "稳健防守",
                status: "historical_replay",
                metrics: {
                  netReturn: 0.04,
                  benchmarkReturn: 0.02,
                  netExcessReturn: 0.02,
                  cashPositionEffectTotal: -0.006,
                  securitySelectionReturnTotal: 0.027,
                  executionCostEffectTotal: -0.001,
                  maxDrawdown: 0.03,
                  informationRatio: 0.4,
                  annualTurnover: 2,
                },
              },
              {
                participantId: "model:a20-v1",
                participantType: "candidate_model",
                name: "A20",
                status: "research",
                metrics: {
                  netReturn: 0.05,
                  benchmarkReturn: 0.02,
                  netExcessReturn: 0.03,
                  cashPositionEffectTotal: -0.004,
                  securitySelectionReturnTotal: 0.035,
                  executionCostEffectTotal: -0.001,
                  maxDrawdown: 0.02,
                  informationRatio: 0.6,
                  annualTurnover: 1,
                },
              },
            ],
          }],
        },
      })),
    );
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("0 / 4 通过");
    await user.click(screen.getByRole("button", { name: /测试验收/ }));
    const comparison = within(
      screen.getByRole("region", { name: "测试验收详情" }),
    ).getByRole("region", { name: "同窗历史对比" });

    expect(within(comparison).getByText("稳健防守")).toBeInTheDocument();
    expect(within(comparison).getByText("A20 · 当前最佳")).toBeInTheDocument();
    expect(within(comparison).getByText("正式规则")).toBeInTheDocument();
    expect(within(comparison).getByText("候选模型")).toBeInTheDocument();
    expect(within(comparison).getByRole("columnheader", { name: "现金仓位贡献" }))
      .toBeInTheDocument();
    expect(within(comparison).getByRole("columnheader", { name: "选股贡献" }))
      .toBeInTheDocument();
    expect(within(comparison).getByRole("columnheader", { name: "交易成本贡献" }))
      .toBeInTheDocument();
    expect(within(comparison).getByText("-0.60%")).toBeInTheDocument();
    expect(within(comparison).getByText("3.50%")).toBeInTheDocument();
    expect(within(comparison).getAllByText("2.00%")).toHaveLength(4);
  });

  it("shows the best tabular candidate without implying formal adoption", async () => {
    const tabularResearch = {
      status: "available",
      formalStrategyWeight: 0,
      formalOrderSource: false,
      latest: null,
      best: {
        status: "research",
        protocolVersion: "regime-aware-tabular-alpha-v2",
        configHash: "e7960d4206b5a0c7",
        accountScope: "zz500",
        asOf: "20260807",
        estimator: "lightgbm_regression",
        target: "residualized_cross_sectional_rank_v1",
        selectedFeatureCount: 88,
        developmentStart: "20180102",
        developmentEnd: "20250106",
        oosStart: "20201027",
        oosEnd: "20250106",
        formalOrderSource: false,
        registryMutated: false,
        metrics: {
          rankIc: 0.0955,
          icir: 0.5173,
          rawRankIc: 0.0183,
          rawIcir: 0.2103,
          portfolioCagr: 0.051,
          benchmarkCagr: -0.0266,
          netExcessReturn: 0.0797,
          maxDrawdown: 0.1701,
          activeMaxDrawdown: 0.1818,
          annualTurnover: 3.57,
          capitalUtilization: 0.9994,
          portfolioSharpe: 0.2628,
          informationRatio: 0.4889,
          deflatedSharpeProbability: 0.4407,
          probabilityOfBacktestOverfit: 0.4286,
        },
        gate: {
          passed: false,
          reasons: [
            "top_tail",
            "active_max_drawdown",
            "deflated_sharpe_probability",
          ],
          checks: { rank_ic: true, top_tail: false },
          positiveFolds: 3,
          bucketSpearman: 0.9,
        },
        buckets: [
          { bucket: 1, meanExcessReturn: -0.0039, observations: 99_988 },
          { bucket: 2, meanExcessReturn: -0.0027, observations: 100_431 },
          { bucket: 3, meanExcessReturn: 0.0029, observations: 100_378 },
          { bucket: 4, meanExcessReturn: 0.0067, observations: 100_431 },
          { bucket: 5, meanExcessReturn: 0.0059, observations: 100_634 },
        ],
      },
      experiments: [],
      forwardObservation: {
        status: "observing",
        lifecycleStatus: "forward_observation",
        modelId: "TABULAR-E7960D4206B5A0C7-FWD1",
        configHash: "e7960d4206b5a0c7",
        accountScope: "zz500",
        horizon: 20,
        observationStart: "20260810",
        latestPredictionDate: "20260810",
        observationDays: 1,
        predictionRows: 500,
        latestCandidates: 500,
        latestSelected: 50,
        maturedEvidence: {
          status: "waiting_for_horizon",
          maturedRows: 0,
          maturedDays: 0,
          latestLabelEnd: null,
          rankIc: null,
          icir: null,
          rawRankIc: null,
          rawIcir: null,
          topBottomSpread: null,
          buckets: [],
        },
        portfolio: {
          status: "waiting_for_next_open",
          periods: 0,
          rebalancePeriods: 0,
          trades: 0,
          netReturn: null,
          benchmarkReturn: null,
          netExcessReturn: null,
          maxDrawdown: null,
          activeMaxDrawdown: null,
          informationRatio: null,
          annualTurnover: null,
          capitalUtilization: null,
          executionCostBps: null,
        },
        drift: {
          status: "normal",
          medianFeatureCoverage: 0.9828,
          medianOutOfRangeRatio: 0.031,
        },
        promotion: {
          status: "evidence_pending",
          passedChecks: 1,
          totalChecks: 7,
          checks: [
            { key: "observation_days", passed: false },
            { key: "feature_drift", passed: true },
          ],
          automaticPromotion: false,
        },
        formalStrategyWeight: 0,
        formalOrderSource: false,
        updatedAt: "2026-08-11T02:30:00+00:00",
      },
      closure: {
        status: "research_blocked",
        asOf: "20260810",
        decision: "retain_research_baseline",
        bestConfigHash: "e7960d4206b5a0c7",
        officialImmutableTrials: 14,
        diagnosticExperiments: 29,
        passedChecks: 9,
        totalChecks: 12,
        formalStrategyWeight: 0,
        blockers: [
          {
            code: "top_tail",
            measured: -0.0008,
            required: 0,
            evidence: "score_bucket_spread",
          },
          {
            code: "active_drawdown",
            measured: 0.1818,
            required: 0.12,
            evidence: "exact_cost_walk_forward",
          },
          {
            code: "multiplicity_confidence",
            measured: 0.4407,
            required: 0.95,
            evidence: "deflated_sharpe_probability",
          },
        ],
        nextRunConditions: [
          {
            code: "historical_information_coverage",
            measured: 0.0005,
            required: 0.55,
            evidence: "moneyflow_and_events",
          },
          {
            code: "untouched_lockbox",
            measured: 0,
            required: 1,
            evidence: "observed_final_already_opened",
          },
        ],
      },
    };
    const calibratedLatest = {
      ...tabularResearch.best,
      configHash: "dd0dabd7b01c2d57",
      metrics: {
        ...tabularResearch.best.metrics,
        netExcessReturn: 0.0283,
        activeMaxDrawdown: 0.3126,
        capitalUtilization: 0.0444,
        deflatedSharpeProbability: 0.2004,
      },
      gate: {
        ...tabularResearch.best.gate,
        reasons: [
          "top_tail",
          "active_max_drawdown",
          "capital_utilization",
          "deflated_sharpe_probability",
        ],
      },
      calibration: {
        enabled: true,
        foldCount: 3,
        economicPredictionCoverage: 1,
        positiveLowerBoundCoverage: 0.1492,
        uncertaintyBpsP50: 89.05,
        uncertaintyBpsP90: 144.84,
        optimizerTrackingErrorP50: 0.0699,
        optimizerTrackingErrorP90: 0.1123,
        noTradeReasons: [
          { reason: "scheduled_rebalance_not_due", count: 18_009 },
          { reason: "insufficient_net_edge", count: 1_713 },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({
        ...payload,
        tabularResearch: {
          ...tabularResearch,
          latest: calibratedLatest,
          experiments: [calibratedLatest],
        },
      })),
    );
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("0 / 4 通过");
    await user.click(screen.getByRole("button", { name: /测试验收/ }));
    const detail = screen.getByRole("region", { name: "测试验收详情" });

    expect(within(detail).getByText("经典表格模型")).toBeInTheDocument();
    expect(
      within(detail).getByRole("region", { name: "历史经典表格模型归档" }),
    ).toBeInTheDocument();
    expect(within(detail).getByText("历史最佳试验")).toBeInTheDocument();
    expect(within(detail).queryByText("当前最佳研究候选")).not.toBeInTheDocument();
    expect(within(detail).getByText("前瞻研究观察")).toBeInTheDocument();
    expect(within(detail).getByText("1 / 60")).toBeInTheDocument();
    expect(within(detail).getByText("0 / 12")).toBeInTheDocument();
    expect(within(detail).getByText("98.28%")).toBeInTheDocument();
    expect(within(detail).getByText("未接入正式策略")).toBeInTheDocument();
    expect(within(detail).getByText("本轮自主优化结论")).toBeInTheDocument();
    expect(within(detail).getByText("9 / 12")).toBeInTheDocument();
    expect(within(detail).getByText("14")).toBeInTheDocument();
    expect(within(detail).getByText("29")).toBeInTheDocument();
    expect(within(detail).getByText("主动回撤超限")).toBeInTheDocument();
    expect(within(detail).getByText("下一轮数据条件")).toBeInTheDocument();
    expect(within(detail).getByText("资金流历史覆盖")).toBeInTheDocument();
    expect(within(detail).getByText("多次试验后可信度不足")).toBeInTheDocument();
    expect(within(detail).getByText("新的前瞻验证窗口")).toBeInTheDocument();
    expect(within(detail).getAllByText("7.97%")).toHaveLength(2);
    expect(within(detail).getAllByText("正式策略权重")).toHaveLength(2);
    expect(within(detail).getAllByText("0.00%")).toHaveLength(2);
    expect(within(detail).getByText(/最高分组未稳定优于次高分组/))
      .toHaveTextContent("top_tail");
    expect(within(detail).getByText("最近试验诊断")).toBeInTheDocument();
    expect(within(detail).getByText("4.44%")).toBeInTheDocument();
    expect(within(detail).getByText("14.92%")).toBeInTheDocument();
    expect(within(detail).getByText("89.05 bp")).toBeInTheDocument();
    expect(within(detail).getByText("成本与置信度过滤")).toBeInTheDocument();
    expect(within(detail).getByText("1,713")).toBeInTheDocument();
    const comparison = within(detail).getByRole("region", {
      name: "最佳与最近试验对比",
    });
    expect(within(comparison).getByText("历史最佳")).toBeInTheDocument();
    expect(within(comparison).getByText("最近试验")).toBeInTheDocument();
    expect(within(comparison).getByText("20.04%")).toBeInTheDocument();
  });

  it("shows zero selected securities and the evidenced cash reason", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模拟运行/ }));

    const detail = screen.getByRole("region", { name: "模拟运行详情" });
    expect(screen.getByText("0 个入选")).toBeInTheDocument();
    expect(within(detail).getByText("范围外剔除")).toBeInTheDocument();
    expect(within(detail).getAllByText("3")).toHaveLength(2);
    expect(screen.getByText(/上涨概率未达到入选门槛/)).toHaveTextContent(
      "probability_gate_not_met",
    );
  });

  it("shows training time, translated artifact status, and artifact reference separately", async () => {
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
    expect(within(detail).getByRole("columnheader", { name: "资金利用率" }))
      .toBeInTheDocument();
    expect(within(detail).getByText("2026-07-29T23:00:00"))
      .toBeInTheDocument();
    expect(within(detail).getByText("可用")).toBeInTheDocument();
    expect(within(detail).queryByText("available")).not.toBeInTheDocument();
    expect(
      within(detail).getByText(
        "data/research/models/a_share/20/run-A20-V005.joblib",
      ),
    ).toBeInTheDocument();
  });

  it("separates ranking diagnostics from the deployable mainline portfolio", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload)));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模型训练/ }));
    const mainline = within(
      screen.getByRole("region", { name: "模型训练详情" }),
    ).getByRole("region", { name: "当前经典主线" });

    expect(within(mainline).getByRole("columnheader", {
      name: "排名诊断组合 · 净超额",
    })).toBeInTheDocument();
    expect(within(mainline).getByRole("columnheader", {
      name: "可部署组合 · 净超额",
    })).toBeInTheDocument();
    expect(within(mainline).getByRole("columnheader", {
      name: "透明基线 · 净超额",
    })).toBeInTheDocument();
    expect(within(mainline).getByRole("columnheader", {
      name: "机器学习增量",
    })).toBeInTheDocument();
    expect(within(mainline).getByRole("columnheader", { name: "校准状态" }))
      .toBeInTheDocument();
    expect(within(mainline).getByText("4.10%")).toBeInTheDocument();
    expect(within(mainline).getByText("1.80%")).toBeInTheDocument();
    expect(within(mainline).getByText("2.50%")).toBeInTheDocument();
    expect(within(mainline).getByText("-0.70%")).toBeInTheDocument();
    expect(within(mainline).getByText("校准可用")).toBeInTheDocument();
    expect(within(mainline).getByText("92.00%")).toBeInTheDocument();
  });

  it("keeps old model runs in a collapsed history archive", async () => {
    const archivedModel = {
      ...payload.training.models[0],
      modelVersion: "A3-V001",
      specId: "legacy-h3",
      horizon: 3,
      lifecycleStatus: "rejected",
      trainedAt: "2026-06-01T20:00:00",
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      ...payload,
      training: {
        ...payload.training,
        archive: {
          total: 17,
          byStatus: { rejected: 14, research: 3 },
          recent: [archivedModel],
        },
      },
    })));
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模型训练/ }));
    const detail = screen.getByRole("region", { name: "模型训练详情" });
    const archiveToggle = within(detail).getByText("历史归档");
    const archiveDetails = archiveToggle.closest("details");

    expect(archiveToggle).toBeInTheDocument();
    expect(within(detail).getByText("17 个版本")).toBeInTheDocument();
    expect(archiveDetails).not.toHaveAttribute("open");

    await user.click(archiveToggle);

    expect(archiveDetails).toHaveAttribute("open");
    const archive = within(detail).getByRole("region", { name: "历史归档明细" });
    expect(within(archive).getByText("A3-V001")).toBeInTheDocument();
    expect(within(archive).getByText("未通过验收 14")).toBeInTheDocument();
    expect(within(archive).getByText("研究中 3")).toBeInTheDocument();
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
    expect(within(detail).getByText("预测产物").parentElement)
      .toHaveTextContent("缺失 · -");
    expect(within(detail).queryByText("missing")).not.toBeInTheDocument();
  });

  it("shows scoped accounts, exact replay baselines, and formal attribution", async () => {
    const evidencePayload = {
      ...payload,
      simulation: {
        ...payload.simulation,
        accounts: [
          {
            accountId: "hs300",
            scope: "hs300",
            benchmark: "000300",
            selectedCount: 3,
            totalValue: 503000,
          },
          {
            accountId: "zz500",
            scope: "zz500",
            benchmark: "000905",
            selectedCount: 2,
            totalValue: 498000,
          },
        ],
        evaluation: {
          status: "available",
          modelVersion: "A20-V005",
          simulatorVersion: "paper-parity-daily-v1",
          grossReturn: 0.085,
          netReturn: 0.08,
          benchmarkReturn: 0.03,
          netExcessReturn: 0.05,
          maxDrawdown: 0.08,
          annualTurnover: 3.2,
          capitalUtilization: 0.91,
          cashRatio: 0.09,
          rebalanceFrequency: "monthly",
          scheduledRebalancePeriods: 24,
          sharpe: 0.9,
          executionCost: 125,
          executionCostBps: 11.2,
          impactBpsP50: 6.8,
          impactBpsP90: 9.3,
          impactCappedNotionalRatio: 0,
          missingLiquidityNotionalRatio: 0,
          executionEvidenceStatus: "available",
          executionPolicyVersion: "cost-aware-aim-v1",
          edgeCalibrationVersion: "clustered-date-mean-se-v2",
          allocationContract: "core-plus-tilt-v1",
          modelTiltCap: 0.2,
          decisionCount: 120,
          tradeAllowedCount: 18,
          noTradeCount: 102,
          noTradeReasonCounts: {
            insufficient_net_edge: 70,
            rank_buffer_hold: 32,
          },
          validTrialCount: 5,
          baselineComparison: {
            momentum_20: { net_excess_return: 0.01 },
            no_trade: { net_excess_return: 0 },
          },
          accountMetrics: {},
        },
      },
      attribution: {
        status: "available",
        formalModelApplied: false,
        completeCount: 0,
        totalCount: 1,
        rows: [
          {
            asOf: "2026-08-07",
            strategyId: "trend-v2",
            accountId: "hs300",
            status: "partial",
            modelPolicyStatus: "rule_only",
            modelVersions: {},
            netPnl: -120,
            modelSelectionPnl: 0,
            explainedRatio: 0.97,
            residualRatio: 0.03,
            positiveDrivers: [],
            negativeDrivers: [],
            unavailableInputs: ["factor_attribution"],
          },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(evidencePayload)),
    );
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    await screen.findByText("A20-V005");
    await user.click(screen.getByRole("button", { name: /模拟运行/ }));
    const simulation = screen.getByRole("region", { name: "模拟运行详情" });
    expect(within(simulation).getAllByText("hs300")).toHaveLength(2);
    expect(within(simulation).getAllByText("zz500")).toHaveLength(2);
    expect(within(simulation).getByText("5.00%")).toBeInTheDocument();
    expect(within(simulation).getByText("8.50%")).toBeInTheDocument();
    expect(within(simulation).getByText("11.20 bp")).toBeInTheDocument();
    expect(within(simulation).getByText("9.30 bp")).toBeInTheDocument();
    expect(within(simulation).getByText("91.00%")).toBeInTheDocument();
    expect(within(simulation).getByText("9.00%")).toBeInTheDocument();
    expect(within(simulation).getByText("每月")).toBeInTheDocument();
    expect(within(simulation).getByText("24")).toBeInTheDocument();
    expect(within(simulation).getByText("均值误差校准 v2")).toBeInTheDocument();
    expect(within(simulation).getByText("规则核心 + 模型倾斜")).toBeInTheDocument();
    expect(within(simulation).getByText("20.00%")).toBeInTheDocument();
    expect(within(simulation).getByText("18 / 120")).toBeInTheDocument();
    expect(within(simulation).getByText("净收益不足以覆盖成本与不确定性")).toBeInTheDocument();
    expect(within(simulation).getByText("20日动量")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /正式采用/ }));
    const adoption = screen.getByRole("region", { name: "正式采用详情" });
    expect(within(adoption).getAllByText("未采用")).toHaveLength(2);
    expect(within(adoption).getByText("trend-v2 · hs300")).toBeInTheDocument();
    expect(within(adoption).getByText("规则策略")).toBeInTheDocument();
    expect(within(adoption).getByText("97.00%")).toBeInTheDocument();
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
    expect(screen.getByText("规则策略兜底")).toBeInTheDocument();
    expect(screen.getByText("尚无可用的 Champion 模型")).toBeInTheDocument();
    expect(screen.queryByText("fallback")).not.toBeInTheDocument();
    expect(screen.queryByText("no_champion")).not.toBeInTheDocument();
  });

  it("shows translated audit and source health states", async () => {
    const sourceStatusPayload = {
      ...payload,
      dataPreparation: {
        ...payload.dataPreparation,
        sources: [
          { source: "market", status: "available", rows: 1000, failed: false },
          {
            source: "news",
            status: "source_unavailable",
            rows: 0,
            failed: true,
          },
          { source: "finance", status: "failed", rows: 0, failed: true },
          {
            source: "audit",
            status: "not_recorded",
            rows: 0,
            failed: false,
          },
          { source: "events", status: "empty", rows: 0, failed: false },
          {
            source: "policy",
            status: "unavailable",
            rows: 0,
            failed: true,
          },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(sourceStatusPayload)),
    );
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    const detail = await screen.findByRole("region", { name: "数据准备详情" });
    expect(within(detail).getByText("行情价格数据")).toBeInTheDocument();
    expect(within(detail).getByText("market")).toBeInTheDocument();
    expect(within(detail).getByText("46")).toBeInTheDocument();
    expect(within(detail).getByText("1 个未分类")).toBeInTheDocument();
    expect(within(detail).getByText("已通过")).toBeInTheDocument();
    expect(within(detail).getByText("可用")).toBeInTheDocument();
    expect(within(detail).getByText("数据源不可用")).toBeInTheDocument();
    expect(within(detail).getByText("失败")).toBeInTheDocument();
    expect(within(detail).getByText("未记录")).toBeInTheDocument();
    expect(within(detail).getByText("暂无数据")).toBeInTheDocument();
    expect(within(detail).getByText("状态不可用")).toBeInTheDocument();
    expect(
      within(detail).getByText("future_feature_not_registered"),
    ).toBeInTheDocument();
    expect(within(detail).getByText("未收录特征")).toBeInTheDocument();
    expect(detail).not.toHaveTextContent(
      /\b(?:available|source_unavailable|failed|not_recorded|empty|unavailable|passed)\b/,
    );
  });

  it("safely labels unknown internal statuses across model details", async () => {
    const unknownStatusPayload = {
      ...payload,
      dataPreparation: {
        ...payload.dataPreparation,
        pointInTimeAudit: "future_audit_state",
        sources: [
          {
            source: "market",
            status: "new_provider_state",
            rows: 1000,
            failed: false,
          },
        ],
      },
      training: {
        models: [
          {
            ...payload.training.models[0],
            artifactStatus: "artifact_uploaded_v2",
          },
        ],
      },
      simulation: {
        ...payload.simulation,
        predictionStatus: "prediction_queued_v2",
      },
      adoption: {
        ...payload.adoption,
        strategyUsage: [
          {
            ...payload.adoption.strategyUsage[0],
            status: "strategy_linked_v2",
            fallback_reason: "future_fallback_reason",
          },
        ],
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(unknownStatusPayload)),
    );
    const user = userEvent.setup();
    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    const dataDetail = await screen.findByRole("region", {
      name: "数据准备详情",
    });
    expect(within(dataDetail).getAllByText("未知状态")).toHaveLength(2);
    expect(dataDetail).not.toHaveTextContent(
      /future_audit_state|new_provider_state/,
    );

    await user.click(screen.getByRole("button", { name: /模型训练/ }));
    const trainingDetail = screen.getByRole("region", {
      name: "模型训练详情",
    });
    expect(within(trainingDetail).getByText("未知状态")).toBeInTheDocument();
    expect(trainingDetail).not.toHaveTextContent("artifact_uploaded_v2");

    await user.click(screen.getByRole("button", { name: /模拟运行/ }));
    const simulationDetail = screen.getByRole("region", {
      name: "模拟运行详情",
    });
    expect(within(simulationDetail).getByText("预测产物").parentElement)
      .toHaveTextContent("未知状态 · -");
    expect(simulationDetail).not.toHaveTextContent("prediction_queued_v2");

    await user.click(screen.getByRole("button", { name: /正式采用/ }));
    const adoptionDetail = screen.getByRole("region", {
      name: "正式采用详情",
    });
    expect(within(adoptionDetail).getByText("未知状态")).toBeInTheDocument();
    expect(within(adoptionDetail).getByText("原因待系统补充"))
      .toBeInTheDocument();
    expect(adoptionDetail).not.toHaveTextContent(
      /strategy_linked_v2|future_fallback_reason/,
    );
  });

  it("shows a partial-status banner without hiding valid model sections", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      ...payload,
      errors: [{ resource: "source_health", reason: "unavailable" }],
    })));
    const user = userEvent.setup();

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByText(/部分状态不可用/)).toHaveTextContent(
      "source_health",
    );
    await user.click(screen.getByRole("button", { name: /模型训练/ }));
    expect(
      screen.getByRole("region", { name: "模型训练详情" }),
    ).toHaveTextContent("A20-V005");
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

  it("rejects duplicate stage identities from a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({
        ...payload,
        stages: [...payload.stages, { ...payload.stages[0] }],
      })),
    );

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid model research response: stages[5].key duplicate",
    );
  });

  it("rejects duplicate model table rows from a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({
        ...payload,
        training: {
          models: [...payload.training.models, { ...payload.training.models[0] }],
        },
      })),
    );

    render(<ModelResearchPage market="a_share" refreshToken={0} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid model research response: training.models[1] duplicate accountScope,horizon,modelVersion",
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

  it("loads once when first mounted with a non-zero refresh token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    render(<ModelResearchPage market="a_share" refreshToken={4} />);

    await screen.findByText("0 / 4 通过");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes only when the token changes within the same market", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(
      <ModelResearchPage market="a_share" refreshToken={1} />,
    );
    await screen.findByText("0 / 4 通过");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    rerender(<ModelResearchPage market="a_share" refreshToken={2} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("loads a changed market once when market and token change together", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(
      <ModelResearchPage market="a_share" refreshToken={0} />,
    );
    await screen.findByText("0 / 4 通过");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    rerender(
      <ModelResearchPage market="cn_qdii_etf" refreshToken={1} />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/dashboard/model-research.json?market=cn_qdii_etf",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("loads once when entering model research after refreshing another workspace", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);
    fetchSystemOverview.mockResolvedValue({
      generated_at: "2026-08-08T18:00:00+08:00",
      markets: [],
      models: [],
      strategy_model_usage: [],
      intelligence: {},
      errors: [],
    });
    window.history.replaceState(
      {},
      "",
      "/app.html?view=operations&scope=all",
    );
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "刷新 dashboard" }));
    await user.click(screen.getByRole("button", { name: "模型研究" }));

    expect(await screen.findByText("跨市场模型进度")).toBeInTheDocument();
    expect(fetchSystemOverview).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
