import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./FinancialCharts", () => ({
  PerformanceChart: ({ benchmarkLabel }: { benchmarkLabel: string }) => <div>净值图 · {benchmarkLabel}</div>,
  StrategyComparisonChart: () => <div>双策略赛季净值图</div>,
  CandlestickChart: () => <div>K线图</div>,
}));

const comparisonPayload = {
  market: "cn_qdii_etf",
  season: {
    id: "dual_strategy_2026_s1",
    name: "双策略对抗 · 赛季1",
    effective_date: "2026-07-11",
    anchor_date: "2026-07-10",
  },
  strategies: {
    claude: {
      agent: "claude",
      label: "稳健防守",
      description: "价值质量、低波与低换手",
      color: "#d6a84b",
      strategy_id: "defensive_global_etf_v1",
      strategy_name: "稳健防守 · 低波均衡",
      holdings_source: "planned_orders",
      allocations: [{ label: "美国市场", value: 0.5, weight: 0.5 }],
      metrics: {
        season_return: 0.01, benchmark_return: 0.005, excess_return: 0.005,
        annualized_volatility: null, sharpe: null, max_drawdown: 0,
        cash_ratio: 1, turnover: 0, trading_cost: 0, cost_bps: null,
        position_count: 5, pending_order_count: 5, trade_count: 0,
      },
    },
    codex: {
      agent: "codex",
      label: "趋势进攻",
      description: "动量成长与主动换仓",
      color: "#22d3ee",
      strategy_id: "trend_global_etf_v1",
      strategy_name: "趋势进攻 · 全球动量",
      holdings_source: "planned_orders",
      allocations: [{ label: "美国市场", value: 0.7, weight: 0.7 }],
      metrics: {
        season_return: 0.02, benchmark_return: 0.005, excess_return: 0.015,
        annualized_volatility: null, sharpe: null, max_drawdown: 0,
        cash_ratio: 1, turnover: 0, trading_cost: 0, cost_bps: null,
        position_count: 10, pending_order_count: 10, trade_count: 0,
      },
    },
  },
  pair: {
    position_overlap: 0.43,
    underlying_index_overlap: 0.25,
    underlying_company_overlap: 0.38,
    weighted_company_overlap: 0.21,
    return_correlation: null,
    factor_distance: 0.65,
    factor_distance_floor: 0.45,
  },
  nav_series: [{ date: "2026-07-10", claude: 0, codex: 0, benchmark: 0 }],
  factor_rows: [{
    key: "momentum_20",
    label: "近20日动量",
    explanation: "观察近期趋势。",
    claude: { weight: 0.1, direction: "high" },
    codex: { weight: 0.4, direction: "high" },
  }],
};

const summaryPayload = {
  generated_at: "2026-07-10T01:00:00",
  markets: [
    {
      market: "cn_qdii_etf",
      label: "跨境ETF",
      currency: "¥",
      agents: [
        {
          agent: "codex",
          strategy: comparisonPayload.strategies.codex,
          nav: {
            latest: 1000000,
            latest_display: "¥1.00M",
            date: "2026-07-10",
            return: 0,
            return_display: "0.00%"
          },
          decision: {
            href: "/pro/cn_qdii_etf/codex.html",
            pending_orders: { total: 1, buy: 1, sell: 0 },
            weekly_report_href: "/cn_qdii_etf/codex/weekly_report.md"
          },
          tasks: {
            daily: { status: "missing" },
            weekly: { status: "success" }
          }
        },
        {
          agent: "claude",
          strategy: comparisonPayload.strategies.claude,
          nav: {
            latest: 1000000,
            latest_display: "¥1.00M",
            date: "2026-07-10",
            return: 0,
            return_display: "0.00%"
          },
          decision: {
            href: "/pro/cn_qdii_etf/claude.html",
            pending_orders: { total: 1, buy: 1, sell: 0 },
            weekly_report_href: "/cn_qdii_etf/claude/weekly_report.md"
          },
          tasks: {
            daily: { status: "missing" },
            weekly: { status: "success" }
          }
        },
      ],
      comparison: comparisonPayload,
      monthly: { status: "not_configured" }
    }
  ],
  sentiment: []
};

const detailPayload = {
  generated_at: "2026-07-10T01:00:02",
  market: "cn_qdii_etf",
  market_label: "跨境ETF",
  currency: "¥",
  agent: "codex",
  selection: {
    schema_version: 1,
    as_of: "2026-07-10",
    universe_hash: "shared-hash",
    scopes: {
      us_exposure: {
        stages: [
          { key: "catalog", label: "动态目录", count: 13 },
          { key: "portfolio_target", label: "目标持仓", count: 1 },
        ],
        rejections: [],
        selected: [],
      },
    },
  },
  lookthrough: {
    status: "partial",
    source: "planned_orders",
    profile_coverage: 1,
    company_weight_coverage: 0.4504,
    indexes: [{ index_key: "nasdaq_100", label: "纳斯达克100", weight: 1, profile_available: true }],
    countries: [{ label: "美国", weight: 1 }],
    sectors: [{ label: "信息技术", weight: 0.6851 }],
    companies: [{ symbol: "NVDA", name: "英伟达", sector: "信息技术", weight: 0.076 }],
    company_symbols: ["NVDA"],
    sources: [{ index_key: "nasdaq_100", name: "纳斯达克100", as_of: "2026-06-30", source_url: "https://example.com", source_label: "official" }],
    unsupported_indexes: [],
  },
  nav: {
    latest: {
      date: "2026-07-10",
      total_value: 1000000,
      total_value_display: "¥1.00M",
      return_display: "0.00%",
      benchmark_code: "513100.SH",
      benchmark_return: 0.004
    },
    series: [
      { date: "2026-07-09", total_value: 999000, return: -0.001, benchmark_return: -0.002 },
      { date: "2026-07-10", total_value: 1000000, return: 0, benchmark_return: 0.004 }
    ],
    accounts: [],
    benchmark_label: "纳斯达克100基准"
  },
  strategy: {
    agent: "codex",
    agent_label: "趋势进攻",
    strategy_id: "codex-etf",
    name: "趋势进攻 · 全球动量",
    factors: [
      { key: "momentum_20", label: "近20日涨跌", explanation: "观察近期趋势。", weight: 0.6, direction: "high", direction_label: "偏好高值" }
    ]
  },
  activity: {
    summary: { total: 1 },
    rows: [
      { date: "2026-07-13", code: "513100.SH", name: "纳指ETF", status: "planned", status_label: "计划买入", shares: 1000 }
    ]
  },
  orders: {
    summary: { total: 1, buy: 1, sell: 0 },
    rows: [
      {
        account_id: "us_exposure",
        code: "513100.SH",
        name: "纳指ETF",
        side: "buy",
        shares: 1000,
        target_value: 100000,
        score: 0.92,
        execute_after: "2026-07-13",
        reason: "momentum"
      }
    ]
  },
  positions: { summary: { total: 0, market_value_display: "¥0" }, rows: [] },
  trades: { summary: { total: 0 }, rows: [] },
  runs: {
    summary: { total: 1 },
    rows: [
      {
        run_id: "run-weekly-20260710T005635-8fmi",
        command: "run-weekly",
        as_of: "2026-07-10",
        started_at: "2026-07-10T00:56:35",
        duration_ms: 878,
        status: "success"
      }
    ]
  },
  weekly_report: {
    exists: true,
    href: "/cn_qdii_etf/codex/weekly_report.md",
    markdown: "# 跨境 ETF 周报\n\n生成 1 笔订单。"
  }
};

const instrumentPayload = {
  generated_at: "2026-07-10T01:00:03",
  market: "cn_qdii_etf",
  agent: "codex",
  instrument: { code: "513100.SH", name: "纳指ETF", exposure_group: "美国市场", theme: "纳斯达克100" },
  latest: null,
  candles: [],
  metrics: [],
  related_trades: [],
  warning: "暂无可用的历史行情缓存"
};

const aShareMarket = {
  market: "a_share",
  label: "A股",
  currency: "¥",
  agents: [
    {
      ...summaryPayload.markets[0].agents[0],
      agent: "codex"
    }
  ],
  monthly: { status: "ready" }
};

const multiMarketSummary = {
  ...summaryPayload,
  markets: [...summaryPayload.markets, aShareMarket]
};

const aShareDetail = {
  ...detailPayload,
  market: "a_share",
  market_label: "A股",
  nav: {
    ...detailPayload.nav,
    benchmark_label: "沪深300",
    latest: {
      ...detailPayload.nav.latest,
      benchmark_code: "000300.SH"
    }
  },
  orders: {
    summary: { total: 1, buy: 1, sell: 0 },
    rows: [
      {
        ...detailPayload.orders.rows[0],
        code: "000001.SZ",
        name: "平安银行"
      }
    ]
  },
  activity: {
    summary: { total: 1 },
    rows: [
      { date: "2026-07-13", code: "000001.SZ", name: "平安银行", status: "planned", status_label: "计划买入", shares: 1000 }
    ]
  }
};

const modelShadowDetail = {
  ...detailPayload,
  agent: "model_shadow",
  strategy: {
    agent: "model_shadow",
    agent_label: "模型迭代",
    strategy_id: "model_iteration_cn_qdii_etf_v1",
    name: "候选模型模拟验证 · 5日",
    factors: [
      { key: "expected_excess_return", label: "预期超额收益", explanation: "只接受正值。", weight: 0.45, direction: "high", direction_label: "偏好高值" }
    ]
  },
  model_iteration: {
    status: "available",
    label: "模型迭代",
    portfolio_label: "候选模型模拟组合",
    isolation: "完全隔离，不计入双策略竞赛",
    source_agent: "codex",
    as_of: "2026-07-17",
    prediction_as_of: "2026-07-17",
    horizon: 5,
    candidate: {
      market: "cn_qdii_etf",
      horizon: 5,
      model_version: "model-v3",
      display_version: "Q5-V003",
      status: "shadow",
      status_label: "模拟验证",
      champion_model_version: "model-v2",
      shadow_cycles: 2,
      shadow_cycles_remaining: 2,
    },
    champion: {
      market: "cn_qdii_etf",
      horizon: 5,
      model_version: "model-v2",
      display_version: "Q5-V002",
      status: "active",
      status_label: "正式使用",
      champion_model_version: "model-v2",
      shadow_cycles: 4,
      shadow_cycles_remaining: 0,
    },
    version_history: [{ model_version: "model-v1", display_version: "Q5-V001", outcome: "retired", ended_at: "2026-07-10" }],
    model_versions: ["model-v3"],
    eligible_rows: 21,
    selected_count: 5,
    cash_only: false,
    pending_orders: 5,
    trades_executed: 0,
  },
  runs: {
    summary: { total: 1 },
    rows: [
      {
        run_id: "run-model-iteration-20260717",
        command: "run-model-iteration",
        as_of: "2026-07-17",
        started_at: "2026-07-17T18:00:00",
        duration_ms: 1200,
        status: "success"
      }
    ]
  },
  weekly_report: { exists: false, href: null, markdown: "" },
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status });
}

function resourcePayload(url: string, detail: typeof detailPayload | typeof modelShadowDetail | typeof aShareDetail = detailPayload): unknown {
  if (url.includes("agent=model_shadow")) detail = modelShadowDetail;
  if (url.includes("/overview.json")) return {
    generated_at: detail.generated_at,
    market: detail.market,
    market_label: detail.market_label,
    currency: detail.currency,
    agent: detail.agent,
    strategy: detail.strategy,
    latest_nav: detail.nav.latest,
    model_iteration: "model_iteration" in detail ? detail.model_iteration : undefined,
    model_shadow: "model_iteration" in detail ? detail.model_iteration : undefined,
  };
  if (url.includes("/performance.json")) return {
    generated_at: detail.generated_at,
    market: detail.market,
    agent: detail.agent,
    nav: detail.nav,
  };
  if (url.includes("/portfolio.json")) return {
    generated_at: detail.generated_at,
    market: detail.market,
    agent: detail.agent,
    activity: detail.activity,
    orders: detail.orders,
    positions: detail.positions,
    trades: detail.trades,
  };
  if (url.includes("/predictions.json")) return {
    generated_at: detail.generated_at,
    market: detail.market,
    agent: detail.agent,
    prediction_summary: { status: "unavailable", horizons: [], rows: [] },
    alerts: [],
    regimes: { status: "unavailable", history: [], industries: [] },
    model_health: { status: "unavailable", models: [] },
    source_health: [],
  };
  if (url.includes("/research.json")) return {
    generated_at: detail.generated_at,
    market: detail.market,
    agent: detail.agent,
    selection: detail.selection,
    lookthrough: detail.lookthrough,
    research: {},
  };
  if (url.includes("/operations.json")) return {
    generated_at: detail.generated_at,
    market: detail.market,
    agent: detail.agent,
    runs: detail.runs,
    weekly_report: detail.weekly_report,
  };
  throw new Error(`unexpected resource url: ${url}`);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function setRoute(view: "compare" | "detail" | "model-iteration" | "model-shadow" = "detail", market = "cn_qdii_etf", strategy: "defensive" | "trend" = "trend") {
  const params = new URLSearchParams({ market, view });
  if (view === "detail") params.set("strategy", strategy);
  window.history.replaceState({ strategy }, "", `/?${params.toString()}`);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("Dashboard app", () => {
  it("opens the model iteration workbench with champion and challenger lifecycle", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    });
    vi.stubGlobal("fetch", fetchMock);
    setRoute("model-iteration");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "模型迭代工作台" })).toBeInTheDocument();
    const lifecycle = screen.getByRole("region", { name: "模型版本生命周期" });
    expect(within(lifecycle).getByText("当前正式版")).toBeInTheDocument();
    expect(within(lifecycle).getByText("当前验证版")).toBeInTheDocument();
    expect(within(lifecycle).getByText("Q5-V002")).toBeInTheDocument();
    expect(within(lifecycle).getByText("Q5-V003")).toBeInTheDocument();
    expect(within(lifecycle).getByText("验证进度 2 / 4")).toBeInTheDocument();
    expect(screen.getByText("候选模型模拟组合")).toBeInTheDocument();
    expect(screen.getByText("完全隔离，不计入双策略竞赛")).toBeInTheDocument();
    expect(screen.getByText("候选版本以预测形成模拟订单，正式策略仍只读取已晋级版本")).toBeInTheDocument();
    expect(screen.queryByText("上涨概率与可信度独立计算，研究态不会改变模拟订单")).not.toBeInTheDocument();
    expect(screen.getByText(/模型订单穿透/)).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "分析导航" });
    expect(within(navigation).getByRole("button", { name: "模型迭代" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByRole("button", { name: "单策略分析" })).toHaveAttribute("aria-expanded", "false");
    const params = new URLSearchParams(window.location.search);
    expect(params.get("view")).toBe("model-iteration");
    expect(params.has("agent")).toBe(false);
    expect(params.has("strategy")).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/overview.json?market=cn_qdii_etf&agent=model_shadow"))).toBe(true);
    expect(screen.queryByText("模型影子账户")).not.toBeInTheDocument();
  });

  it("canonicalizes the legacy model-shadow URL without exposing the internal agent", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    setRoute("model-shadow");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "模型迭代工作台" })).toBeInTheDocument();
    await waitFor(() => expect(new URLSearchParams(window.location.search).get("view")).toBe("model-iteration"));
    expect(new URLSearchParams(window.location.search).has("agent")).toBe(false);
  });

  it("opens in comparison mode without loading single-strategy resources", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    });
    vi.stubGlobal("fetch", fetchMock);
    setRoute("compare");

    render(<App />);

    expect(await screen.findByRole("region", { name: "双策略竞技场" })).toBeInTheDocument();
    expect(screen.getByText("双策略对抗 · 赛季1")).toBeInTheDocument();
    const analysisNavigation = screen.getByRole("navigation", { name: "分析导航" });
    expect(analysisNavigation.closest("aside")).toHaveClass("left-rail");
    expect(within(analysisNavigation).getByRole("button", { name: "策略对比" })).toHaveAttribute("aria-current", "page");
    expect(within(analysisNavigation).getByRole("button", { name: "单策略分析" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "账户总览" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "目标订单" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "策略对象" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "分析模式" })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).has("agent")).toBe(false);
    expect(new URLSearchParams(window.location.search).has("strategy")).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => /\/(overview|performance|portfolio|predictions|research|operations)\.json/.test(String(input)))).toBe(false);
  });

  it("enters a strategy detail from the comparison and keeps the context in the URL", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    });
    vi.stubGlobal("fetch", fetchMock);
    setRoute("compare");
    const user = userEvent.setup();

    render(<App />);
    await user.click(await screen.findByRole("button", { name: "查看趋势进攻明细" }));

    expect(await screen.findByRole("region", { name: "账户总览" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "目标订单" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "双策略竞技场" })).not.toBeInTheDocument();
    const analysisNavigation = screen.getByRole("navigation", { name: "分析导航" });
    const strategyNavigation = screen.getByRole("navigation", { name: "策略对象" });
    expect(analysisNavigation).toContainElement(strategyNavigation);
    expect(within(analysisNavigation).getByRole("button", { name: "单策略分析" })).toHaveAttribute("aria-expanded", "true");
    expect(within(strategyNavigation).getByRole("button", { name: "稳健防守" })).not.toHaveAttribute("aria-current");
    expect(within(strategyNavigation).getByRole("button", { name: "趋势进攻" })).toHaveAttribute("aria-current", "page");
    expect(new URLSearchParams(window.location.search).get("view")).toBe("detail");
    expect(new URLSearchParams(window.location.search).get("strategy")).toBe("trend");
    expect(new URLSearchParams(window.location.search).has("agent")).toBe(false);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/portfolio.json?market=cn_qdii_etf&agent=codex"))).toBe(true);
  });

  it("keeps the selected strategy while switching between analysis modes", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    });
    vi.stubGlobal("fetch", fetchMock);
    setRoute("compare", "cn_qdii_etf", "defensive");
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("region", { name: "双策略竞技场" });
    await user.click(screen.getByRole("button", { name: "单策略分析" }));

    expect(await screen.findByRole("heading", { name: "稳健防守 策略工作台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "稳健防守" })).toHaveAttribute("aria-current", "page");
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("agent=claude"))).toBe(true);

    await user.click(screen.getByRole("button", { name: "策略对比" }));
    await user.click(screen.getByRole("button", { name: "单策略分析" }));
    expect(await screen.findByRole("heading", { name: "稳健防守 策略工作台" })).toBeInTheDocument();
  });

  it("switches workbench tabs and restores the view from browser history state", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    setRoute("compare");
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("region", { name: "双策略竞技场" });
    await user.click(screen.getByRole("button", { name: "单策略分析" }));
    await user.click(await screen.findByRole("button", { name: "稳健防守" }));
    expect(await screen.findByRole("heading", { name: "稳健防守 策略工作台" })).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("view")).toBe("detail");
    expect(new URLSearchParams(window.location.search).get("strategy")).toBe("defensive");
    expect(new URLSearchParams(window.location.search).has("agent")).toBe(false);

    act(() => {
      window.history.pushState({}, "", "/?market=cn_qdii_etf&view=compare");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(await screen.findByRole("region", { name: "双策略竞技场" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "策略对比" })).toHaveAttribute("aria-current", "page");
  });

  it("normalizes an invalid browser history route before loading detail resources", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    setRoute("compare");

    render(<App />);
    await screen.findByRole("region", { name: "双策略竞技场" });

    act(() => {
      window.history.pushState({}, "", "/?market=unknown&view=detail&strategy=unknown&agent=unknown");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(await screen.findByRole("heading", { name: "趋势进攻 策略工作台" })).toBeInTheDocument();
    const params = new URLSearchParams(window.location.search);
    expect(params.get("market")).toBe("cn_qdii_etf");
    expect(params.get("view")).toBe("detail");
    expect(params.get("strategy")).toBe("trend");
    expect(params.has("agent")).toBe(false);
  });

  it("canonicalizes a legacy agent link to a public strategy link", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    window.history.replaceState({}, "", "/?market=cn_qdii_etf&view=detail&agent=claude");

    render(<App />);

    expect(await screen.findByRole("heading", { name: "稳健防守 策略工作台" })).toBeInTheDocument();
    const params = new URLSearchParams(window.location.search);
    expect(params.get("strategy")).toBe("defensive");
    expect(params.has("agent")).toBe(false);
  });

  it("removes legacy identity parameters from comparison links", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    window.history.replaceState({}, "", "/?market=cn_qdii_etf&view=compare&agent=codex");

    render(<App />);

    expect(await screen.findByRole("region", { name: "双策略竞技场" })).toBeInTheDocument();
    const params = new URLSearchParams(window.location.search);
    expect(params.has("strategy")).toBe(false);
    expect(params.has("agent")).toBe(false);
  });

  it("does not add browser history entries when the active mode or strategy is clicked again", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    setRoute("detail");
    const pushState = vi.spyOn(window.history, "pushState");
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("region", { name: "账户总览" });
    pushState.mockClear();
    await user.click(screen.getByRole("button", { name: "单策略分析" }));
    await user.click(screen.getByRole("button", { name: "趋势进攻" }));

    expect(pushState).not.toHaveBeenCalled();
  });

  it("loads independent dashboard resources without the legacy detail endpoint", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/dashboard/summary.json")) {
        return Promise.resolve(new Response(JSON.stringify(summaryPayload), { status: 200 }));
      }
      if (url.includes("/api/dashboard/instrument.json")) {
        return Promise.resolve(jsonResponse(instrumentPayload));
      }
      expect(init?.cache).toBe("no-cache");
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    });
    vi.stubGlobal("fetch", fetchMock);
    setRoute("detail");

    render(<App />);

    expect(await screen.findByRole("button", { name: "跨境ETF" })).toBeInTheDocument();
    expect((await screen.findAllByText("513100.SH")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("run-weekly").length).toBeGreaterThan(0);
    expect(screen.getByText("周度复盘")).toBeInTheDocument();
    expect(screen.queryByText("周度调仓")).not.toBeInTheDocument();

    const ordersPanel = screen.getByRole("region", { name: "目标订单" });
    expect(within(ordersPanel).getByText("纳指ETF")).toBeInTheDocument();
    expect(screen.getAllByText("趋势进攻 · 全球动量").length).toBeGreaterThan(0);
    expect(screen.getAllByText("纳斯达克100基准").length).toBeGreaterThan(0);
    expect(screen.queryByText("周报摘录")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "ETF候选与底层暴露" })).toBeInTheDocument();
    expect(screen.getByText("shared-hash")).toBeInTheDocument();

    expect(screen.queryByRole("region", { name: "双策略竞技场" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "趋势进攻" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByText(/Claude|Codex/i)).not.toBeInTheDocument();

    const portfolio = screen.getByRole("region", { name: "持仓组合" });
    expect(portfolio.compareDocumentPosition(ordersPanel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "刷新 dashboard" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dashboard/summary.json", expect.anything()));
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/detail.json"))).toBe(false);
    for (const resource of ["overview", "performance", "portfolio", "predictions", "research", "operations"]) {
      expect(fetchMock.mock.calls.some(([input]) => String(input).includes(`/${resource}.json`))).toBe(true);
    }
  });

  it("opens an order row with Enter and closes the dialog with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      if (url.includes("instrument")) return Promise.resolve(jsonResponse(instrumentPayload));
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    const user = userEvent.setup();
    setRoute("detail");
    render(<App />);

    const ordersPanel = await screen.findByRole("region", { name: "目标订单" });
    const cell = within(ordersPanel).getByText("纳指ETF");
    const row = cell.closest("tr");
    expect(row).not.toBeNull();
    row?.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("dialog", { name: "证券详情" })).toBeVisible();
    const closeButton = screen.getByRole("button", { name: "关闭明细" });
    expect(closeButton).toHaveFocus();
    await user.tab();
    expect(closeButton).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "证券详情" })).not.toBeInTheDocument();
    await waitFor(() => expect(row).toHaveFocus());
  });

  it("ignores an older resource response after the market changes", async () => {
    const oldPortfolio = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(multiMarketSummary));
      if (url.includes("market=cn_qdii_etf") && url.includes("portfolio")) return oldPortfolio.promise;
      if (url.includes("market=cn_qdii_etf")) return Promise.resolve(jsonResponse(resourcePayload(url)));
      if (url.includes("market=a_share")) return Promise.resolve(jsonResponse(resourcePayload(url, aShareDetail)));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    }));
    const user = userEvent.setup();
    setRoute("detail");
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "A股" }));
    expect((await screen.findAllByText("平安银行")).length).toBeGreaterThan(0);

    await act(async () => {
      oldPortfolio.resolve(jsonResponse(resourcePayload("/portfolio.json", detailPayload)));
      await oldPortfolio.promise;
      await Promise.resolve();
    });
    expect(screen.queryAllByText("纳指ETF")).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "趋势进攻 策略工作台" })).toBeInTheDocument();
    expect(screen.getAllByText("A股").length).toBeGreaterThan(0);
  });

  it("renders settled resources while predictions are still pending", async () => {
    const pendingPredictions = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      if (url.includes("predictions")) return pendingPredictions.promise;
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));

    setRoute("detail");
    render(<App />);

    expect((await screen.findAllByText("513100.SH")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("run-weekly").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "持仓组合" })).toBeInTheDocument();
  });

  it("isolates a failed portfolio resource after the market changes", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(multiMarketSummary));
      if (url.includes("market=cn_qdii_etf")) return Promise.resolve(jsonResponse(resourcePayload(url)));
      if (url.includes("market=a_share") && url.includes("portfolio")) {
        return Promise.resolve(jsonResponse({
          error: "dashboard_data_invalid",
          message: "Dashboard data source is unreadable: positions"
        }, 500));
      }
      if (url.includes("market=a_share")) return Promise.resolve(jsonResponse(resourcePayload(url, aShareDetail)));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    }));
    const user = userEvent.setup();
    setRoute("detail");
    render(<App />);

    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "A股" }));

    expect(await screen.findByText(/持仓交易：Dashboard data source is unreadable: positions/)).toBeInTheDocument();
    expect(screen.queryAllByText("纳指ETF")).toHaveLength(0);
    expect(screen.getByText("净值图 · 沪深300")).toBeInTheDocument();
  });

  it("keeps a summary error when a concurrent resource refresh succeeds", async () => {
    const refreshedPortfolio = deferred<Response>();
    let summaryCalls = 0;
    let portfolioCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) {
        summaryCalls += 1;
        if (summaryCalls === 1) return Promise.resolve(jsonResponse(summaryPayload));
        return Promise.resolve(jsonResponse({
          error: "dashboard_api_failed",
          message: "Summary refresh failed"
        }, 500));
      }
      if (url.includes("portfolio")) {
        portfolioCalls += 1;
        if (portfolioCalls > 1) return refreshedPortfolio.promise;
      }
      return Promise.resolve(jsonResponse(resourcePayload(url)));
    }));
    const user = userEvent.setup();
    setRoute("detail");
    render(<App />);

    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "刷新 dashboard" }));
    expect(await screen.findByText("Summary refresh failed")).toBeInTheDocument();

    await act(async () => {
      refreshedPortfolio.resolve(jsonResponse(resourcePayload("/portfolio.json", detailPayload)));
      await refreshedPortfolio.promise;
      await Promise.resolve();
    });

    expect(screen.getByText("Summary refresh failed")).toBeInTheDocument();
  });
});
