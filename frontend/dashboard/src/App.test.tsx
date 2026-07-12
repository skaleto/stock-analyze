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

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Dashboard app", () => {
  it("loads summary and detail data for the selected market agent pair", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/dashboard/summary.json")) {
        return Promise.resolve(new Response(JSON.stringify(summaryPayload), { status: 200 }));
      }
      if (url.includes("/api/dashboard/detail.json")) {
        return Promise.resolve(new Response(JSON.stringify(detailPayload), { status: 200 }));
      }
      if (url.includes("/api/dashboard/instrument.json")) {
        return Promise.resolve(jsonResponse(instrumentPayload));
      }
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("button", { name: "跨境ETF" })).toBeInTheDocument();
    expect((await screen.findAllByText("513100.SH")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("run-weekly").length).toBeGreaterThan(0);

    const ordersPanel = screen.getByRole("region", { name: "目标订单" });
    expect(within(ordersPanel).getByText("纳指ETF")).toBeInTheDocument();
    expect(screen.getAllByText("趋势进攻 · 全球动量").length).toBeGreaterThan(0);
    expect(screen.getAllByText("纳斯达克100基准").length).toBeGreaterThan(0);
    expect(screen.queryByText("周报摘录")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "ETF候选与底层暴露" })).toBeInTheDocument();
    expect(screen.getByText("shared-hash")).toBeInTheDocument();

    const arena = screen.getByRole("region", { name: "双策略竞技场" });
    const accountOverview = screen.getByRole("region", { name: "账户总览" });
    expect(arena.compareDocumentPosition(accountOverview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("双策略对抗 · 赛季1")).toBeInTheDocument();
    expect(screen.queryByText(/Claude|Codex/i)).not.toBeInTheDocument();

    const portfolio = screen.getByRole("region", { name: "持仓组合" });
    expect(portfolio.compareDocumentPosition(ordersPanel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "刷新 dashboard" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dashboard/summary.json", expect.anything()));
  });

  it("opens an order row with Enter and closes the dialog with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(summaryPayload));
      if (url.includes("instrument")) return Promise.resolve(jsonResponse(instrumentPayload));
      return Promise.resolve(jsonResponse(detailPayload));
    }));
    const user = userEvent.setup();
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

  it("ignores an older detail response after the market changes", async () => {
    const oldDetail = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(multiMarketSummary));
      if (url.includes("market=cn_qdii_etf")) return oldDetail.promise;
      if (url.includes("market=a_share")) return Promise.resolve(jsonResponse(aShareDetail));
      return Promise.reject(new Error(`unexpected url: ${url}`));
    }));
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "A股" }));
    expect((await screen.findAllByText("平安银行")).length).toBeGreaterThan(0);

    await act(async () => {
      oldDetail.resolve(jsonResponse(detailPayload));
      await oldDetail.promise;
      await Promise.resolve();
    });
    expect(screen.queryAllByText("纳指ETF")).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "趋势进攻 策略工作台" })).toBeInTheDocument();
    expect(screen.getAllByText("A股").length).toBeGreaterThan(0);
  });

  it("clears old detail when the new selection fails", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("summary")) return Promise.resolve(jsonResponse(multiMarketSummary));
      if (url.includes("market=cn_qdii_etf")) return Promise.resolve(jsonResponse(detailPayload));
      if (url.includes("market=a_share")) {
        return Promise.resolve(jsonResponse({
          error: "dashboard_data_invalid",
          message: "Dashboard data source is unreadable: positions"
        }, 500));
      }
      return Promise.reject(new Error(`unexpected url: ${url}`));
    }));
    const user = userEvent.setup();
    render(<App />);

    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "A股" }));

    expect(await screen.findByText("Dashboard data source is unreadable: positions")).toBeInTheDocument();
    expect(screen.queryAllByText("纳指ETF")).toHaveLength(0);
  });

  it("keeps a summary error when a concurrent detail refresh succeeds", async () => {
    const refreshedDetail = deferred<Response>();
    let summaryCalls = 0;
    let detailCalls = 0;
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
      detailCalls += 1;
      if (detailCalls === 1) return Promise.resolve(jsonResponse(detailPayload));
      return refreshedDetail.promise;
    }));
    const user = userEvent.setup();
    render(<App />);

    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "刷新 dashboard" }));
    expect(await screen.findByText("Summary refresh failed")).toBeInTheDocument();

    await act(async () => {
      refreshedDetail.resolve(jsonResponse(detailPayload));
      await refreshedDetail.promise;
      await Promise.resolve();
    });

    expect(screen.getByText("Summary refresh failed")).toBeInTheDocument();
  });
});
