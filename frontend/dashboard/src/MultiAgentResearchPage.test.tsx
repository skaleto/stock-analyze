import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MultiAgentResearchPage } from "./MultiAgentResearchPage";
import { workspaceQueryClient } from "./queryClient";

vi.mock("./FinancialCharts", () => ({
  CandlestickChart: () => <div>K线图</div>,
}));

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

function summary() {
  return {
    schemaVersion: "multi-agent-research-dashboard-v1",
    status: "available",
    latestRun: {
      runId: "run-1",
      createdAt: "2026-08-22T01:02:03+00:00",
      status: "completed_with_degradation",
      market: "a_share",
      instrument: { code: "000001.SZ", name: "平安银行" },
      model: "test-model",
      degradedRoles: ["news"],
      digest: "# 简报\n\n仅研究",
      executionEffect: "none_research_only",
      reportPath: "reports/research/multi_agent/a_share/000001.SZ/run-1/full_report.md",
    },
    universe: {
      status: "available",
      asOf: "20260822",
      aShare: { scopeCounts: { csi1000: 1000 }, uniqueInstruments: 1000 },
      funds: {
        sourceCounts: { exchange: 2188, otc: 15000 },
        overseasScopeCounts: { nasdaq_100: 6 },
        classificationCounts: { name_benchmark_inferred: 6 },
      },
    },
    executionEffect: "none_research_only",
  };
}

function universePage(
  kind: "a_share" | "exchange_fund" | "otc_fund",
  {
    query = "",
    scope = null,
    page = 1,
    total = 2,
    status = "available",
  }: {
    query?: string;
    scope?: string | null;
    page?: number;
    total?: number;
    status?: "available" | "unavailable";
  } = {},
) {
  const aShare = {
    code: "000001.SZ",
    name: "平安银行",
    recordKind: "a_share_equity",
    researchOnly: true,
    researchScopes: ["csi1000"],
    membershipDate: "20260731",
  };
  const fund = {
    code: kind === "otc_fund" ? "000834.OF" : "513100.SH",
    name: "纳斯达克100ETF",
    recordKind: "fund",
    researchOnly: true,
    fundType: "ETF",
    benchmark: "纳斯达克100指数",
    overseasScope: "nasdaq_100",
    classificationStatus: "name_benchmark_inferred",
    tradability: kind === "otc_fund"
      ? "otc_non_tradable_research_only"
      : "exchange_research_only",
  };
  return {
    schemaVersion: "research-universe-browser-v1",
    status,
    asOf: status === "available" ? "20260822" : null,
    kind,
    query,
    scope,
    page,
    pageSize: 50,
    total: status === "available" ? total : 0,
    scopeOptions: kind === "a_share" ? ["csi1000", "hs300"] : ["nasdaq_100", "sp500"],
    records: status === "available" && total > 0 ? [kind === "a_share" ? aShare : fund] : [],
    executionEffect: "none_research_only",
  };
}

function universeInstrument(kind: "a_share" | "exchange_fund" | "otc_fund") {
  const aShare = kind === "a_share";
  return {
    schemaVersion: "research-universe-instrument-v1",
    status: "available",
    asOf: "20260822",
    kind,
    code: aShare ? "000001.SZ" : "513100.SH",
    instrument: aShare
      ? {
        code: "000001.SZ",
        name: "平安银行",
        recordKind: "a_share_equity",
        researchOnly: true,
        researchScopes: ["csi1000"],
        membershipDate: "20260731",
      }
      : {
        code: "513100.SH",
        name: "纳斯达克100ETF",
        recordKind: "fund",
        researchOnly: true,
        fundType: "ETF",
        benchmark: "纳斯达克100指数",
        overseasScope: "nasdaq_100",
        classificationStatus: "name_benchmark_inferred",
        tradability: "exchange_research_only",
      },
    market: aShare ? "a_share" : "cn_qdii_etf",
    latest: {
      date: "2026-08-22",
      open: 10,
      high: 11,
      low: 9,
      close: 10.5,
      volume: 1000,
      amount: 10000,
      changePct: 0.02,
    },
    candles: [
      { date: "2026-08-21", open: 9, high: 10, low: 8, close: 9.5, volume: 900, amount: 9000 },
      { date: "2026-08-22", open: 10, high: 11, low: 9, close: 10.5, volume: 1000, amount: 10000 },
    ],
    metrics: [{
      key: "momentum_20",
      label: "20日动量",
      explanation: "近20个交易日价格变化。",
      value: 0.12,
      format: "percent",
    }],
    warning: null,
    executionEffect: "none_research_only",
  };
}

function mockFetch() {
  return vi.fn((url: string) => {
    if (url === "/api/dashboard/multi-agent-research.json") {
      return Promise.resolve(response(summary()));
    }
    if (url.includes("/api/dashboard/research-universe-instrument.json")) {
      const params = new URL(url, "http://dashboard.local").searchParams;
      return Promise.resolve(response(universeInstrument(params.get("kind") as "a_share" | "exchange_fund" | "otc_fund")));
    }
    const params = new URL(url, "http://dashboard.local").searchParams;
    const kind = params.get("kind") as "a_share" | "exchange_fund" | "otc_fund";
    const query = params.get("query") ?? "";
    const page = Number(params.get("page") ?? "1");
    if (query === "不可用") {
      return Promise.resolve(response(universePage(kind, { query, status: "unavailable" })));
    }
    return Promise.resolve(response(universePage(kind, {
      query,
      scope: params.get("scope"),
      page,
      total: query === "无匹配" ? 0 : kind === "exchange_fund" && query === "纳斯" ? 60 : kind === "otc_fund" ? 1 : 2,
    })));
  });
}

afterEach(() => {
  workspaceQueryClient.clear();
  vi.unstubAllGlobals();
});

describe("MultiAgentResearchPage", () => {
  it("shows completed artifacts and the default A-share research directory without a run control", async () => {
    vi.stubGlobal("fetch", mockFetch());

    render(<MultiAgentResearchPage refreshToken={0} />);

    await waitFor(() => expect(screen.getAllByText("平安银行").length).toBeGreaterThan(0));
    expect(await screen.findByRole("table", { name: "研究目录结果" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "A股" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("CSI1000")).toBeInTheDocument();
    expect(screen.getByText("场外基金目录")).toBeInTheDocument();
    expect(screen.getAllByText(/仅研究/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /运行|生成|刷新目录|交易/ })).not.toBeInTheDocument();
  });

  it("switches to exchange funds, submits a search, and shows the paginated result", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MultiAgentResearchPage refreshToken={0} />);

    await screen.findByRole("table", { name: "研究目录结果" });
    await user.click(screen.getByRole("tab", { name: "场内基金" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("kind=exchange_fund"),
      expect.anything(),
    ));

    await user.type(screen.getByRole("searchbox", { name: "搜索研究目录" }), "纳斯");
    await user.click(screen.getByRole("button", { name: "搜索" }));

    expect(await screen.findByText("纳斯达克100ETF")).toBeInTheDocument();
    expect(screen.getByText("第 1 页 / 共 2 页 · 共 60 条")).toBeInTheDocument();
  });

  it("opens a read-only research detail drawer when an A-share catalog row is clicked", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MultiAgentResearchPage refreshToken={0} />);

    await screen.findByRole("table", { name: "研究目录结果" });
    await user.click(screen.getByRole("row", { name: /000001\.SZ.*平安银行.*csi1000.*20260731/i }));

    expect(await screen.findByRole("dialog", { name: "平安银行投研详情" })).toBeInTheDocument();
    expect(screen.getByText("K线行情")).toBeInTheDocument();
    expect(screen.getByText("20日动量")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/dashboard/research-universe-instrument.json?kind=a_share&code=000001.SZ"),
      expect.anything(),
    );
    expect(screen.queryByText("相关交易")).not.toBeInTheDocument();
  });

  it("opens a research detail drawer when a focused catalog row receives Enter", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const user = userEvent.setup();
    render(<MultiAgentResearchPage refreshToken={0} />);

    await screen.findByRole("table", { name: "研究目录结果" });
    const row = screen.getByRole("row", { name: /000001\.SZ.*平安银行.*csi1000.*20260731/i });
    row.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("dialog", { name: "平安银行投研详情" })).toBeInTheDocument();
  });

  it("opens a research detail drawer when a focused catalog row receives Space", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const user = userEvent.setup();
    render(<MultiAgentResearchPage refreshToken={0} />);

    await screen.findByRole("table", { name: "研究目录结果" });
    const row = screen.getByRole("row", { name: /000001\.SZ.*平安银行.*csi1000.*20260731/i });
    row.focus();
    await user.keyboard(" ");

    expect(await screen.findByRole("dialog", { name: "平安银行投研详情" })).toBeInTheDocument();
  });

  it("resets criteria on tabs and separates no-result, unavailable, and OTC comparison states", async () => {
    vi.stubGlobal("fetch", mockFetch());
    const user = userEvent.setup();
    render(<MultiAgentResearchPage refreshToken={0} />);

    await screen.findByRole("table", { name: "研究目录结果" });
    const search = screen.getByRole("searchbox", { name: "搜索研究目录" });
    await user.type(search, "无匹配");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("没有匹配的研究目录记录。")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "场外基金" }));
    expect(search).toHaveValue("");
    expect((await screen.findAllByText("非交易研究对照")).length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();

    await user.type(search, "不可用");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("研究目录快照暂不可用。")).toBeInTheDocument();
  });
});
