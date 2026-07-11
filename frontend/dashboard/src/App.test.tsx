import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

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
        }
      ],
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
  nav: {
    latest: {
      date: "2026-07-10",
      total_value: 1000000,
      total_value_display: "¥1.00M",
      return_display: "0.00%",
      benchmark_code: "513100.SH"
    },
    series: [
      { date: "2026-07-09", total_value: 999000, return: -0.001 },
      { date: "2026-07-10", total_value: 1000000, return: 0 }
    ],
    accounts: []
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
      return Promise.reject(new Error(`unexpected url: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("button", { name: "跨境ETF" })).toBeInTheDocument();
    expect(await screen.findByText("513100.SH")).toBeInTheDocument();
    expect(screen.getAllByText("run-weekly").length).toBeGreaterThan(0);

    const ordersPanel = screen.getByRole("region", { name: "目标订单" });
    expect(within(ordersPanel).getByText("纳指ETF")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "刷新 dashboard" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/dashboard/summary.json", expect.anything()));
  });

  it("opens an order row with Enter and closes the dialog with Escape", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      return Promise.resolve(
        url.includes("summary") ? jsonResponse(summaryPayload) : jsonResponse(detailPayload)
      );
    }));
    const user = userEvent.setup();
    render(<App />);

    const cell = await screen.findByText("纳指ETF");
    const row = cell.closest("tr");
    expect(row).not.toBeNull();
    row?.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("dialog", { name: "订单明细" })).toBeVisible();
    const closeButton = screen.getByRole("button", { name: "关闭明细" });
    expect(closeButton).toHaveFocus();
    await user.tab();
    expect(closeButton).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "订单明细" })).not.toBeInTheDocument();
    expect(row).toHaveFocus();
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
    expect(await screen.findByText("平安银行")).toBeInTheDocument();

    await act(async () => {
      oldDetail.resolve(jsonResponse(detailPayload));
      await oldDetail.promise;
      await Promise.resolve();
    });
    expect(screen.queryByText("纳指ETF")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "codex 策略工作台" })).toBeInTheDocument();
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

    expect(await screen.findByText("纳指ETF")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "A股" }));

    expect(await screen.findByText("Dashboard data source is unreadable: positions")).toBeInTheDocument();
    expect(screen.queryByText("纳指ETF")).not.toBeInTheDocument();
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

    expect(await screen.findByText("纳指ETF")).toBeInTheDocument();
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
