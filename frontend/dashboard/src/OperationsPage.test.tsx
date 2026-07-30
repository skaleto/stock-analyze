import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchOperationsCenter } from "./api";
import { OperationsPage } from "./OperationsPage";
import type { OperationsCenterData } from "./workspaceTypes";

function payload(
  overrides: Partial<OperationsCenterData> = {},
): OperationsCenterData {
  return {
    generated_at: "2026-07-30T13:30:00+08:00",
    scope: "a_share",
    runtime: {
      status: "available",
      lastKnownAt: "2026-07-30T13:30:00+08:00",
      reason: null,
    },
    dailyFreshness: {
      asOfDate: "2026-07-30",
      status: "waiting",
    },
    mainChain: [
      {
        key: "intelligence",
        label: "情报增量",
        status: "success",
        primary: "1 / 1 个任务完成",
        secondary: "下次 16:30",
        units: [{
          unit: "stock-analyze-intelligence.service",
          status: "success",
          activeState: "inactive",
          subState: "dead",
          result: "success",
          exitStatus: 0,
          startedAt: "2026-07-30T13:00:00+08:00",
          finishedAt: "2026-07-30T13:01:00+08:00",
        }],
        crossMarketUnits: [],
      },
      {
        key: "market_snapshot",
        label: "行情与研究快照",
        status: "waiting_schedule",
        primary: "0 / 1 个任务完成",
        secondary: "下次 18:30",
        units: [{
          unit: "stock-analyze-market-data.service",
          status: "waiting_schedule",
        }],
        crossMarketUnits: [],
      },
      {
        key: "research",
        label: "特征、预测与评估",
        status: "waiting_upstream",
        primary: "0 / 1 个任务完成",
        secondary: "等待上游",
        units: [{
          unit: "stock-analyze-research.service",
          status: "waiting_upstream",
        }],
        crossMarketUnits: [],
      },
      {
        key: "simulation",
        label: "正式策略模拟",
        status: "running",
        primary: "1 / 2 个任务完成",
        secondary: "正在执行",
        units: [{
          unit: "stock-analyze-claude-daily.service",
          status: "running",
          activeState: "active",
        }],
        crossMarketUnits: [{
          unit: "stock-analyze-model-iteration.service",
          status: "failed",
          result: "exit-code",
          reason: "cross_market_service_result_not_attributable_to_single_market",
        }],
      },
      {
        key: "publish",
        label: "Dashboard 聚合与通知",
        status: "skipped",
        primary: "1 / 2 个任务完成",
        secondary: "本次无需执行",
        units: [{
          unit: "stock-analyze-daily-summary.service",
          status: "skipped",
          exitStatus: 75,
        }],
        crossMarketUnits: [],
      },
    ],
    background: {
      status: "available",
      snapshotGeneratedAt: "2026-07-30T13:29:00+08:00",
      backlog: {
        download: 100,
        parse: 20,
        semantic: 10,
        total: 130,
      },
      artifactWorkers: {
        status: "available",
        activeLeases: 2,
        latestFinishedAt: "2026-07-30T13:20:00+08:00",
      },
    },
    backgroundWorkers: [
      {
        key: "artifact_backfill",
        label: "PDF 下载与解析回填",
        status: "running",
        serviceUnit: "stock-analyze-intelligence-artifact-backfill.service",
        timerUnit: "stock-analyze-intelligence-artifact-backfill.timer",
        lastResult: "success",
        startedAt: "2026-07-30T13:20:00+08:00",
        finishedAt: null,
        nextTriggerAt: "2026-07-30T13:40:00+08:00",
        backlog: { download: 100, parse: 20, semantic: 10, total: 130 },
      },
      {
        key: "reconcile",
        label: "情报对账",
        status: "success",
        serviceUnit: "stock-analyze-intelligence-reconcile.service",
        timerUnit: "stock-analyze-intelligence-reconcile.timer",
        lastResult: "success",
        backlog: null,
      },
      {
        key: "semantic",
        label: "LLM 语义抽取",
        status: "waiting_schedule",
        serviceUnit: "stock-analyze-intelligence-semantic.service",
        timerUnit: "stock-analyze-intelligence-semantic.timer",
        backlog: { semantic: 10 },
      },
    ],
    schedules: {
      daily: [
        ["stock-analyze-daily-summary.timer", "每日运行摘要"],
        ["stock-analyze-ifind-source-audit.timer", "iFinD 数据源审计"],
        [
          "stock-analyze-intelligence-artifact-backfill.timer",
          "PDF 下载解析回填",
        ],
        ["stock-analyze-intelligence-reconcile.timer", "情报对账"],
        ["stock-analyze-intelligence-semantic.timer", "LLM 语义抽取"],
        ["stock-analyze-intelligence.timer", "情报增量采集"],
        ["stock-analyze-market-data.timer", "行情与研究日链"],
      ].map(([unit, label]) => ({
        unit,
        label,
        status: "active" as const,
        lastTriggerAt: "2026-07-29T18:30:00+08:00",
        nextTriggerAt: "2026-07-30T18:30:00+08:00",
        automation: "automatic" as const,
      })),
      weekly: [
        [
          "stock-analyze-claude-cn-qdii-etf-weekly.timer",
          "跨境ETF稳健防守周度复盘",
        ],
        [
          "stock-analyze-codex-cn-qdii-etf-weekly.timer",
          "跨境ETF趋势进攻周度复盘",
        ],
        ["stock-analyze-qdii-research.timer", "跨境ETF周度研究"],
        ["stock-analyze-weekly-summary.timer", "每周运行摘要"],
        ["stock-analyze-weekly-trigger.timer", "A股周度复盘"],
      ].map(([unit, label]) => ({
        unit,
        label,
        status: "active" as const,
        lastTriggerAt: "2026-07-26T10:00:00+08:00",
        nextTriggerAt: "2026-08-02T10:00:00+08:00",
        automation: "automatic" as const,
      })),
      monthly: [
        ["stock-analyze-model-training.timer", "月度模型训练"],
        ["stock-analyze-monthly-review.timer", "月度策略复盘"],
        ["stock-analyze-monthly-summary.timer", "每月运行摘要"],
      ].map(([unit, label]) => ({
        unit,
        label,
        status: "active" as const,
        lastTriggerAt: "2026-07-01T10:00:00+08:00",
        nextTriggerAt: "2026-08-01T10:00:00+08:00",
        automation: "automatic" as const,
      })),
    },
    recentRuns: [{
      runId: "daily-1",
      market: "a_share",
      strategyKey: "trend",
      strategyLabel: "趋势进攻",
      command: "run-daily",
      asOf: "2026-07-29",
      status: "success",
      startedAt: "2026-07-29T18:40:00+08:00",
      finishedAt: "2026-07-29T18:41:00+08:00",
      durationMs: 60_000,
      errorSummary: "",
    }],
    disk: {
      status: "available",
      usedRatio: 0.52,
      totalBytes: 1000,
      freeBytes: 480,
    },
    interventions: [],
    ...overrides,
  };
}

function backendMinimalTruncatedPayload(): Record<string, unknown> {
  const source = payload();
  return {
    generated_at: source.generated_at,
    scope: source.scope,
    runtime: {
      status: source.runtime.status,
      lastKnownAt: source.runtime.lastKnownAt,
      reason: "详".repeat(1_000),
    },
    dailyFreshness: source.dailyFreshness,
    mainChain: source.mainChain.map((row) => ({
      key: row.key,
      label: row.label,
      status: row.status,
      primary: row.primary,
      secondary: row.secondary,
    })),
    background: {
      status: source.background.status,
      snapshotGeneratedAt: source.background.snapshotGeneratedAt,
      backlog: source.background.backlog,
    },
    backgroundWorkers: source.backgroundWorkers.map((row) => ({
      key: row.key,
      label: row.label,
      status: row.status,
      loadState: row.loadState ?? "loaded",
      reason: null,
    })),
    schedules: Object.fromEntries(
      Object.entries(source.schedules).map(([cadence, rows]) => [
        cadence,
        rows.map((row) => ({
          unit: row.unit,
          label: row.label,
          status: row.status,
          loadState: row.loadState ?? "loaded",
          reason: null,
        })),
      ]),
    ),
    recentRuns: [],
    disk: {
      status: source.disk.status,
      usedRatio: source.disk.usedRatio,
    },
    interventions: [],
    truncated: true,
    truncationReason: "serialized_size_limit",
  };
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("fetchOperationsCenter", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("validates the fixed operations contract and preserves typed partial data", async () => {
    const partial = payload({
      runtime: {
        status: "unavailable",
        lastKnownAt: "2026-07-30T13:20:00+08:00",
        reason: "runtime_status_unavailable",
      },
      mainChain: payload().mainChain.map((row) => ({
        ...row,
        status: "unavailable",
      })),
      background: {
        status: "unavailable",
        snapshotGeneratedAt: null,
        backlog: { download: 0, parse: 0, semantic: 0, total: 0 },
        artifactWorkers: {
          status: "unavailable",
          activeLeases: 0,
          latestFinishedAt: null,
        },
      },
      backgroundWorkers: [],
    });
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(partial));

    await expect(fetchOperationsCenter("a_share")).resolves.toMatchObject({
      runtime: { status: "unavailable" },
      background: { status: "unavailable" },
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/dashboard/operations-center.json?scope=a_share",
      expect.objectContaining({ cache: "no-cache" }),
    );
  });

  it("normalizes the backend minimal truncated payload without weakening full responses", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(backendMinimalTruncatedPayload()),
    );
    const result = await fetchOperationsCenter("a_share");
    expect(result.runtime.reason).toHaveLength(1_000);
    expect(result.mainChain[0].units).toEqual([]);
    expect(result.mainChain[0].crossMarketUnits).toEqual([]);
    expect(result.background.artifactWorkers).toEqual({
      status: "unavailable",
      activeLeases: 0,
      latestFinishedAt: null,
    });
    expect(result.backgroundWorkers[0]).toMatchObject({
      serviceUnit: "",
      timerUnit: "",
      backlog: null,
    });
    expect(result.schedules.daily[0]).toMatchObject({
      automation: "automatic",
      lastTriggerAt: null,
      nextTriggerAt: null,
    });
    expect(result.disk).toMatchObject({
      status: "available",
      usedRatio: 0.52,
    });

    const malformedFull = payload();
    delete (malformedFull.mainChain[0] as Partial<
      OperationsCenterData["mainChain"][number]
    >).units;
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(malformedFull));
    await expect(fetchOperationsCenter("a_share")).rejects.toThrow(
      /Invalid operations center response/,
    );
  });

  it.each([
    ["duplicate main key", () => {
      const value = payload();
      value.mainChain[1].key = value.mainChain[0].key;
      return value;
    }],
    ["unknown main key", () => {
      const value = payload();
      value.mainChain[0].key = "surprise";
      return value;
    }],
    ["duplicate worker key", () => {
      const value = payload();
      value.backgroundWorkers.push({ ...value.backgroundWorkers[0] });
      return value;
    }],
    ["unknown schedule key", () => ({
      ...payload(),
      schedules: { ...payload().schedules, quarterly: [] },
    })],
    ["malformed available runtime", () => ({
      ...payload(),
      runtime: { status: "available" },
    })],
  ])("rejects %s", async (_label, build) => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(build()));
    await expect(fetchOperationsCenter("all")).rejects.toThrow(
      /Invalid operations center response/,
    );
  });

  it("rejects a response over the measured 250KB UTF-8 limit", async () => {
    const value = payload();
    value.recentRuns[0].errorSummary = "错".repeat(90_000);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(value));
    await expect(fetchOperationsCenter("all")).rejects.toThrow(
      /exceeds 250000 bytes/,
    );
  });

  it("rejects overlong error text and inconsistent load states", async () => {
    const overlong = payload();
    overlong.recentRuns[0].errorSummary = "x".repeat(201);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(overlong));
    await expect(fetchOperationsCenter("all")).rejects.toThrow(
      /Invalid operations center response/,
    );

    const missingUnit = payload();
    missingUnit.mainChain[0].units[0] = {
      ...missingUnit.mainChain[0].units[0],
      loadState: "not-found",
      status: "waiting_schedule",
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(missingUnit));
    await expect(fetchOperationsCenter("all")).rejects.toThrow(
      /Invalid operations center response/,
    );

    const duplicateIdentity = payload();
    duplicateIdentity.mainChain[0].crossMarketUnits = [{
      ...duplicateIdentity.mainChain[0].units[0],
    }];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(duplicateIdentity));
    await expect(fetchOperationsCenter("all")).rejects.toThrow(
      /duplicate unit identity/,
    );
  });
});

describe("OperationsPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(payload())));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows Chinese task states and drill-down without service controls", async () => {
    const user = userEvent.setup();
    render(<OperationsPage scope="a_share" refreshToken={0} />);

    expect((await screen.findAllByText("等待计划时间")).length)
      .toBeGreaterThan(0);
    expect(screen.getAllByText("等待上游").length).toBeGreaterThan(0);
    expect(screen.getAllByText("运行中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已跳过").length).toBeGreaterThan(0);
    expect(screen.queryByText(
      /waiting_schedule|waiting_upstream|running|skipped/,
    )).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /正式策略模拟/ }));
    expect(screen.getByText("稳健防守")).toBeInTheDocument();
    expect(screen.getByText("跨市场候选模型证据")).toBeInTheDocument();
    expect(screen.getByText(/不能归属到单一市场/)).toBeInTheDocument();
    expect(screen.queryByRole("button", {
      name: /^(启动|停止|重跑|执行|立即执行)$/,
    })).not.toBeInTheDocument();

    const exposed = [
      document.body.textContent ?? "",
      ...Array.from(document.querySelectorAll("[title], [aria-label]"))
        .flatMap((element) => [
          element.getAttribute("title") ?? "",
          element.getAttribute("aria-label") ?? "",
        ]),
    ].join("\n");
    expect(exposed).not.toMatch(/claude|codex|model_shadow/i);
  });

  it("separates backlog, freshness, and worker state", async () => {
    render(<OperationsPage scope="a_share" refreshToken={0} />);
    await screen.findByText("后台队列");
    expect(screen.getByText("待下载")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("活跃租约")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("最近完成")).toBeInTheDocument();
  });

  it("uses accessible daily, weekly, and monthly schedule tabs", async () => {
    const user = userEvent.setup();
    render(<OperationsPage scope="a_share" refreshToken={0} />);
    await screen.findByText("周期计划");
    const tabs = screen.getByRole("tablist", { name: "周期计划" });
    const daily = within(tabs).getByRole("tab", { name: "每日" });
    const weekly = within(tabs).getByRole("tab", { name: "每周" });
    expect(daily).toHaveAttribute("aria-selected", "true");

    await user.click(weekly);
    expect(weekly).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("A股周度复盘")).toBeInTheDocument();
    expect(screen.getAllByText("自动执行").length).toBeGreaterThan(0);

    await user.click(within(tabs).getByRole("tab", { name: "每月" }));
    expect(screen.getByText("月度模型训练")).toBeInTheDocument();
  });

  it("supports standard roving keyboard navigation across schedule tabs", async () => {
    const user = userEvent.setup();
    render(<OperationsPage scope="a_share" refreshToken={0} />);
    await screen.findByText("周期计划");
    const tabs = screen.getByRole("tablist", { name: "周期计划" });
    const daily = within(tabs).getByRole("tab", { name: "每日" });
    const weekly = within(tabs).getByRole("tab", { name: "每周" });
    const monthly = within(tabs).getByRole("tab", { name: "每月" });

    daily.focus();
    await user.keyboard("{ArrowRight}");
    expect(weekly).toHaveFocus();
    expect(weekly).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{End}");
    expect(monthly).toHaveFocus();
    expect(monthly).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(daily).toHaveFocus();
    expect(daily).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}");
    expect(monthly).toHaveFocus();
    expect(monthly).toHaveAttribute("aria-selected", "true");
  });

  it("shows recent runs, empty interventions, and exception scope", async () => {
    const { rerender } = render(
      <OperationsPage scope="a_share" refreshToken={0} />,
    );
    expect(await screen.findByText("最近运行")).toBeInTheDocument();
    expect(screen.getByText("当前无需人工介入")).toBeInTheDocument();
    expect(screen.getAllByText("成功").length).toBeGreaterThan(0);

    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload({
      scope: "exceptions",
      mainChain: [{
        ...payload().mainChain[0],
        status: "failed",
        primary: "1 个异常",
      }],
      interventions: [{
        key: "disk_capacity",
        severity: "critical",
        title: "磁盘使用率超过 85%",
        evidence: "89.0%",
      }],
    })));
    rerender(<OperationsPage scope="exceptions" refreshToken={0} />);
    expect(await screen.findByText((_content, element) => (
      element?.tagName === "P"
      && element.textContent?.includes("仅看异常") === true
    ))).toBeInTheDocument();
    expect(screen.getByText(/严重 · 磁盘使用率/)).toBeInTheDocument();
    expect(screen.getByText("89.0%")).toBeInTheDocument();
  });

  it("accepts unavailable runtime as a stale partial view", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload({
      runtime: {
        status: "unavailable",
        lastKnownAt: "2026-07-30T13:20:00+08:00",
        reason: "runtime_status_unavailable",
      },
      mainChain: payload().mainChain.map((row) => ({
        ...row,
        status: "unavailable",
      })),
    })));
    render(<OperationsPage scope="a_share" refreshToken={0} />);
    expect(await screen.findByText(/运行时状态不可用/)).toBeInTheDocument();
    expect(screen.getByText(/最后已知快照/)).toBeInTheDocument();
    expect(screen.getAllByText("状态不可用").length).toBeGreaterThan(0);
  });

  it("loads once on a non-zero initial refresh token and once per later change", async () => {
    const { rerender } = render(
      <OperationsPage scope="a_share" refreshToken={7} />,
    );
    await screen.findByText("今日主任务链");
    expect(fetch).toHaveBeenCalledTimes(1);

    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(payload()));
    rerender(<OperationsPage scope="a_share" refreshToken={8} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  });

  it("aborts the old scope, resets selection, and avoids duplicate scope requests", async () => {
    const signals: AbortSignal[] = [];
    vi.mocked(fetch).mockImplementation((input, init) => {
      signals.push(init?.signal as AbortSignal);
      const scope = String(input).includes("cn_qdii_etf")
        ? "cn_qdii_etf"
        : "a_share";
      return Promise.resolve(jsonResponse(payload({ scope })));
    });
    const user = userEvent.setup();
    const { rerender } = render(
      <OperationsPage scope="a_share" refreshToken={0} />,
    );
    await screen.findByText("今日主任务链");
    await user.click(screen.getByRole("button", { name: /正式策略模拟/ }));

    rerender(<OperationsPage scope="cn_qdii_etf" refreshToken={0} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(signals[0].aborted).toBe(true);
    expect(screen.getByRole("button", { name: /情报增量/ }))
      .toHaveAttribute("aria-pressed", "true");
  });

  it("renders error summaries as bounded text rather than HTML", async () => {
    const value = payload();
    value.recentRuns[0].status = "failed";
    value.recentRuns[0].errorSummary = "<img src=x onerror=alert(1)>";
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(value));
    render(<OperationsPage scope="a_share" refreshToken={0} />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>"))
      .toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders not-found, masked, and truncated states without calling them waiting", async () => {
    const value = payload({
      truncated: true,
      truncationReason: "serialized_size_limit",
      mainChain: [{
        ...payload().mainChain[0],
        status: "unavailable",
        units: [{
          unit: "stock-analyze-intelligence.service",
          status: "unavailable",
          loadState: "not-found",
        }],
      }],
      backgroundWorkers: [],
      schedules: { daily: [], weekly: [], monthly: [] },
    });
    value.mainChain[0].crossMarketUnits = [{
      unit: "stock-analyze-model-iteration.service",
      status: "unavailable",
      loadState: "masked",
      reason: "cross_market_service_result_not_attributable_to_single_market",
    }];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(value));

    render(<OperationsPage scope="a_share" refreshToken={0} />);
    expect(await screen.findByText(/响应内容已裁剪/)).toBeInTheDocument();
    expect(screen.getByText("未安装")).toBeInTheDocument();
    expect(screen.getByText("已屏蔽")).toBeInTheDocument();
    expect(screen.queryByText("等待计划时间")).not.toBeInTheDocument();
    expect(screen.getByText("后台明细已裁剪")).toBeInTheDocument();
    expect(screen.getByText("周期计划明细已裁剪")).toBeInTheDocument();
  });

  it("renders the backend minimal truncated payload instead of a page error", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse(backendMinimalTruncatedPayload()),
    );
    render(<OperationsPage scope="a_share" refreshToken={0} />);
    expect(await screen.findByText(/响应内容已裁剪/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "今日主任务链" }))
      .toBeInTheDocument();
    expect(screen.getAllByText("任务明细已裁剪或尚无本日执行记录").length)
      .toBeGreaterThan(0);
  });

  it("does not double refresh when scope and token change together", async () => {
    const signals: AbortSignal[] = [];
    vi.mocked(fetch).mockImplementation((input, init) => {
      signals.push(init?.signal as AbortSignal);
      const scope = String(input).includes("cn_qdii_etf")
        ? "cn_qdii_etf"
        : "a_share";
      return Promise.resolve(jsonResponse(payload({ scope })));
    });
    const { rerender } = render(
      <OperationsPage scope="a_share" refreshToken={2} />,
    );
    await screen.findByText("今日主任务链");

    rerender(
      <OperationsPage scope="cn_qdii_etf" refreshToken={3} />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(signals).toHaveLength(2);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });
});
