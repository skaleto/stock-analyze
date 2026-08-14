import {
  act,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyWorkspacePage } from "./StrategyWorkspacePage";

vi.mock("./CompetitionPanel", () => ({
  default: ({
    onSelectAgent,
  }: {
    onSelectAgent: (agent: string) => void;
  }) => (
    <section aria-label="策略对比">
      <button type="button" onClick={() => onSelectAgent("claude")}>
        查看稳健防守
      </button>
    </section>
  ),
}));

vi.mock("./FinancialCharts", () => ({
  PerformanceChart: () => <div>净值图</div>,
  CandlestickChart: () => <div>K线图</div>,
  StrategyComparisonChart: () => <div>双策略赛季净值图</div>,
}));

const summary = {
  generated_at: "2026-07-30T09:00:00+08:00",
  markets: [{
    market: "cn_qdii_etf",
    label: "跨境ETF",
    currency: "¥",
    agents: [
      {
        agent: "claude",
        strategy: {
          agent: "claude",
          label: "稳健防守",
          strategy_name: "低波均衡",
          color: "#d6a84b",
        },
        nav: {
          latest: 1_000_000,
          latest_display: "¥1.00M",
          date: "2026-07-30",
          return: 0,
          return_display: "0.00%",
        },
        tasks: { daily: { status: "success" }, weekly: { status: "success" } },
      },
      {
        agent: "codex",
        strategy: {
          agent: "codex",
          label: "趋势进攻",
          strategy_name: "全球动量",
          color: "#22d3ee",
        },
        nav: {
          latest: 1_010_000,
          latest_display: "¥1.01M",
          date: "2026-07-30",
          return: 0.01,
          return_display: "1.00%",
        },
        tasks: { daily: { status: "success" }, weekly: { status: "success" } },
      },
    ],
    comparison: {
      market: "cn_qdii_etf",
      strategies: {},
      pair: {},
      nav_series: [],
      factor_rows: [],
    },
    monthly: {},
  }],
  sentiment: [],
};

const base = {
  generated_at: "2026-07-30T09:00:01+08:00",
  market: "cn_qdii_etf",
  agent: "codex",
};

function response(payload: unknown): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(payload)));
}

function errorResponse(message: string): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify({ message }), {
    status: 500,
    statusText: "Internal Server Error",
  }));
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function payloadFor(url: string): unknown {
  if (url.includes("/summary.json")) return summary;
  if (url.includes("/overview.json")) {
    return {
      ...base,
      market_label: "跨境ETF",
      currency: "¥",
      strategy: {
        agent: "codex",
        agent_label: "趋势进攻",
        strategy_id: "trend-v1",
        name: "全球动量",
        factors: [],
      },
      latest_nav: {
        date: "2026-07-30",
        total_value: 1_010_000,
        total_value_display: "¥1.01M",
        return: 0.01,
        return_display: "1.00%",
        benchmark_return: 0.005,
      },
    };
  }
  if (url.includes("/performance.json")) {
    return {
      ...base,
      nav: {
        latest: {
          date: "2026-07-30",
          total_value: 1_010_000,
          total_value_display: "¥1.01M",
          return: 0.01,
          return_display: "1.00%",
          benchmark_return: 0.005,
        },
        series: [],
        accounts: [],
        benchmark_label: "跨境ETF组合基准",
      },
    };
  }
  if (url.includes("/portfolio.json")) {
    return {
      ...base,
      activity: { summary: { total: 0 }, rows: [] },
      orders: {
        summary: { total: 1, buy: 1, sell: 0 },
        rows: [{
          account_id: "us_exposure",
          code: "513100.SH",
          name: "纳指ETF",
          side: "buy",
          shares: 100,
          target_value: 100_000,
        }],
      },
      positions: { summary: { total: 0, market_value_display: "¥0" }, rows: [] },
      trades: { summary: { total: 0 }, rows: [] },
    };
  }
  if (url.includes("/predictions.json")) {
    return {
      ...base,
      prediction_summary: { status: "unavailable", horizons: [], rows: [] },
      alerts: [],
      regimes: { status: "unavailable", history: [], industries: [] },
      model_health: { status: "unavailable", models: [] },
      source_health: [],
    };
  }
  if (url.includes("/research.json")) {
    return { ...base, selection: undefined, lookthrough: undefined, research: {} };
  }
  if (url.includes("/operations.json")) {
    return {
      ...base,
      runs: { summary: { total: 0 }, rows: [] },
      weekly_report: { exists: false, href: null, markdown: "" },
    };
  }
  if (url.includes("/governance.json")) {
    return {
      ...base,
      action_state: { status: "healthy", items: [] },
      lineage: {
        status: "available",
        database_integrity: "ok",
        counts: {},
        decision_runs: [],
        decision_funnel: {
          evaluated: 0,
          eligible: 0,
          selected: 0,
          rejection_counts: {},
        },
        candidates: [],
        allocations: [],
        orders: [],
        fills: [],
        attributions: [],
        experiments: [],
      },
      risk: { status: "available", portfolios: [] },
      attribution: { status: "available", rows: [] },
      drift: {},
      experiments: [],
      intelligence_evidence: {},
      distinctness: {},
    };
  }
  if (url.includes("/instrument.json")) {
    return {
      ...base,
      instrument: {
        code: "513100.SH",
        name: "纳指ETF",
        exposure_group: "全球市场",
      },
      latest: null,
      candles: [],
      related_trades: [],
      metrics: [],
      predictions: [],
      event_evidence: [],
    };
  }
  throw new Error(`unexpected ${url}`);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("StrategyWorkspacePage", () => {
  it("renders the existing portfolio section before performance and target orders", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    )));

    render(
      <StrategyWorkspacePage
        market="cn_qdii_etf"
        mode="detail"
        strategy="trend"
        search=""
        onSelectStrategy={vi.fn()}
        refreshToken={0}
      />,
    );

    const portfolio = await screen.findByRole("region", { name: "当前持仓" });
    const performance = screen.getByRole("region", { name: "净值与基准" });
    const orders = screen.getByRole("region", { name: "目标订单" });
    expect(
      portfolio.compareDocumentPosition(performance)
      & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      performance.compareDocumentPosition(orders)
      & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("maps the public strategy once at the detail request boundary", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    ));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrategyWorkspacePage
        market="cn_qdii_etf"
        mode="detail"
        strategy="trend"
        search=""
        onSelectStrategy={vi.fn()}
        refreshToken={0}
      />,
    );
    await screen.findByRole("region", { name: "当前持仓" });

    await waitFor(() => {
      const detailUrls = fetchMock.mock.calls
        .map(([input]) => String(input))
        .filter((url) => !url.includes("/summary.json"));
      expect(detailUrls).toHaveLength(7);
      expect(detailUrls.every((url) => url.includes("agent=codex"))).toBe(true);
    });
  });

  it("does not double load when first mounted with a non-zero refresh token", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    ));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrategyWorkspacePage
        market="cn_qdii_etf"
        mode="detail"
        strategy="trend"
        search=""
        onSelectStrategy={vi.fn()}
        refreshToken={3}
      />,
    );
    await screen.findByRole("region", { name: "当前持仓" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));
  });

  it("keeps comparison on the bounded summary request", async () => {
    const onSelectStrategy = vi.fn();
    const fetchMock = vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    ));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrategyWorkspacePage
        market="cn_qdii_etf"
        mode="compare"
        search=""
        onSelectStrategy={onSelectStrategy}
        refreshToken={0}
      />,
    );

    await screen.findByRole("region", { name: "策略对比" });
    await userEvent.click(
      screen.getByRole("button", { name: "查看稳健防守" }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/summary.json");
    expect(onSelectStrategy).toHaveBeenCalledWith("defensive");
  });

  it("does not render the prior strategy while a new strategy loads", async () => {
    let activeAgent = "codex";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("agent=claude")) activeAgent = "claude";
      const payload = payloadFor(url);
      if (url.includes("/portfolio.json") && activeAgent === "claude") {
        const portfolio = payload as {
          orders: { rows: { name: string; code: string }[] };
        };
        portfolio.orders.rows[0].name = "防守ETF";
        portfolio.orders.rows[0].code = "513500.SH";
      }
      return response(payload);
    });
    vi.stubGlobal("fetch", fetchMock);

    const props = {
      market: "cn_qdii_etf" as const,
      mode: "detail" as const,
      search: "",
      onSelectStrategy: vi.fn(),
      refreshToken: 0,
    };
    const { rerender } = render(
      <StrategyWorkspacePage {...props} strategy="trend" />,
    );
    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);

    rerender(
      <StrategyWorkspacePage {...props} strategy="defensive" />,
    );

    expect((await screen.findAllByText("防守ETF")).length).toBeGreaterThan(0);
    expect(screen.queryAllByText("纳指ETF")).toHaveLength(0);
  });

  it("does not double load when market and token change together", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    ));
    vi.stubGlobal("fetch", fetchMock);
    const props = {
      mode: "detail" as const,
      strategy: "trend" as const,
      search: "",
      onSelectStrategy: vi.fn(),
    };
    const { rerender } = render(
      <StrategyWorkspacePage
        {...props}
        market="cn_qdii_etf"
        refreshToken={0}
      />,
    );
    await screen.findByRole("region", { name: "当前持仓" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));

    rerender(
      <StrategyWorkspacePage
        {...props}
        market="a_share"
        refreshToken={1}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(16));
  });

  it("does not refresh summary or duplicate detail when strategy and token change together", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    ));
    vi.stubGlobal("fetch", fetchMock);
    const props = {
      market: "cn_qdii_etf" as const,
      mode: "detail" as const,
      search: "",
      onSelectStrategy: vi.fn(),
    };
    const { rerender } = render(
      <StrategyWorkspacePage
        {...props}
        strategy="trend"
        refreshToken={0}
      />,
    );
    await screen.findByRole("region", { name: "当前持仓" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));

    rerender(
      <StrategyWorkspacePage
        {...props}
        strategy="defensive"
        refreshToken={1}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(15));
    const summaryCalls = fetchMock.mock.calls.filter(([input]) => (
      String(input).includes("/summary.json")
    ));
    expect(summaryCalls).toHaveLength(1);
  });

  it("opens an order from the keyboard, traps focus, and restores the row", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => (
      response(payloadFor(String(input)))
    )));
    const user = userEvent.setup();

    render(
      <StrategyWorkspacePage
        market="cn_qdii_etf"
        mode="detail"
        strategy="trend"
        search=""
        onSelectStrategy={vi.fn()}
        refreshToken={0}
      />,
    );

    const ordersPanel = await screen.findByRole("region", {
      name: "目标订单",
    });
    const row = within(ordersPanel).getByText("纳指ETF").closest("tr");
    expect(row).not.toBeNull();
    row?.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("dialog", { name: "证券详情" })).toBeVisible();
    const close = screen.getByRole("button", { name: "关闭明细" });
    expect(close).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: "证券详情" }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(row).toHaveFocus());
  });

  it("ignores a late portfolio response after the market changes", async () => {
    const oldPortfolio = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (
        url.includes("market=cn_qdii_etf")
        && url.includes("/portfolio.json")
      ) {
        return oldPortfolio.promise;
      }
      const payload = payloadFor(url);
      if (url.includes("market=a_share") && url.includes("/portfolio.json")) {
        const portfolio = structuredClone(payload) as {
          orders: { rows: { name: string; code: string }[] };
        };
        portfolio.orders.rows[0].name = "平安银行";
        portfolio.orders.rows[0].code = "000001.SZ";
        return response(portfolio);
      }
      return response(payload);
    });
    vi.stubGlobal("fetch", fetchMock);
    const props = {
      mode: "detail" as const,
      strategy: "trend" as const,
      search: "",
      onSelectStrategy: vi.fn(),
      refreshToken: 0,
    };
    const { rerender } = render(
      <StrategyWorkspacePage {...props} market="cn_qdii_etf" />,
    );

    rerender(<StrategyWorkspacePage {...props} market="a_share" />);
    expect((await screen.findAllByText("平安银行")).length).toBeGreaterThan(0);

    await act(async () => {
      oldPortfolio.resolve(new Response(JSON.stringify(
        payloadFor("/portfolio.json"),
      )));
      await oldPortfolio.promise;
      await Promise.resolve();
    });
    expect(screen.queryAllByText("纳指ETF")).toHaveLength(0);
    expect(screen.getAllByText("平安银行").length).toBeGreaterThan(0);
  });

  it("keeps settled resources visible when one resource fails", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/portfolio.json")) {
        return errorResponse("持仓源暂不可读");
      }
      return response(payloadFor(url));
    }));

    render(
      <StrategyWorkspacePage
        market="cn_qdii_etf"
        mode="detail"
        strategy="trend"
        search=""
        onSelectStrategy={vi.fn()}
        refreshToken={0}
      />,
    );

    expect(
      await screen.findByText("持仓交易：持仓源暂不可读"),
    ).toBeInTheDocument();
    expect(screen.getByText("净值图")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "账户总览" }),
    ).toBeInTheDocument();
  });

  it("does not erase a summary refresh error when detail refreshes finish", async () => {
    const refreshedPortfolio = deferred<Response>();
    let summaryCalls = 0;
    let portfolioCalls = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/summary.json")) {
        summaryCalls += 1;
        if (summaryCalls > 1) return errorResponse("摘要刷新失败");
      }
      if (url.includes("/portfolio.json")) {
        portfolioCalls += 1;
        if (portfolioCalls > 1) return refreshedPortfolio.promise;
      }
      return response(payloadFor(url));
    }));
    const props = {
      market: "cn_qdii_etf" as const,
      mode: "detail" as const,
      strategy: "trend" as const,
      search: "",
      onSelectStrategy: vi.fn(),
    };
    const { rerender } = render(
      <StrategyWorkspacePage {...props} refreshToken={0} />,
    );
    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);

    rerender(<StrategyWorkspacePage {...props} refreshToken={1} />);
    expect(await screen.findByText("摘要刷新失败")).toBeInTheDocument();

    await act(async () => {
      refreshedPortfolio.resolve(new Response(JSON.stringify(
        payloadFor("/portfolio.json"),
      )));
      await refreshedPortfolio.promise;
      await Promise.resolve();
    });
    expect(screen.getByText("摘要刷新失败")).toBeInTheDocument();
    expect((await screen.findAllByText("纳指ETF")).length).toBeGreaterThan(0);
  });
});
