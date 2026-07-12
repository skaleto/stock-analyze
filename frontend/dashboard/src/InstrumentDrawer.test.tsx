import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import InstrumentDrawer from "./InstrumentDrawer";

const chartMocks = vi.hoisted(() => ({
  CandlestickChart: vi.fn((_props: unknown) => <div>K线图</div>),
}));

vi.mock("./FinancialCharts", () => ({
  CandlestickChart: chartMocks.CandlestickChart,
}));


describe("instrument drawer", () => {
  it("loads a security research view with Chinese indicator names", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({
      generated_at: "2026-07-11T12:00:00",
      market: "cn_qdii_etf",
      agent: "codex",
      instrument: { code: "513100.SH", name: "纳指ETF", exposure_group: "美国市场", theme: "纳斯达克100" },
      underlying: {
        index_key: "nasdaq_100",
        name: "纳斯达克100",
        as_of: "2026-06-30",
        source_url: "https://indexes.nasdaq.com/docs/FS_NDX.pdf",
        source_label: "Nasdaq official factsheet",
        constituents: [
          { symbol: "NVDA", name: "英伟达", sector: "信息技术", weight: 0.076 },
          { symbol: "AAPL", name: "苹果", sector: "信息技术", weight: 0.0667 },
        ],
        sector_weights: [],
      },
      latest: { date: "2026-07-10", open: 2.1, high: 2.2, low: 2.0, close: 2.15, change_pct: 0.01, volume: 1000, amount: 2200 },
      candles: [{ date: "2026-07-10", open: 2.1, high: 2.2, low: 2.0, close: 2.15, volume: 1000, amount: 2200 }],
      metrics: [{ key: "roe", label: "净资产收益率 ROE", explanation: "公司用股东投入的净资产创造利润的效率。", value: 0.15, format: "percent" }],
      related_trades: [{ trade_date: "2026-07-14", side: "buy", shares: 100 }],
      warning: null,
    }), { status: 200 }))));

    render(
      <InstrumentDrawer
        row={{ code: "513100.SH", name: "纳指ETF", gross_margin: 0.4 }}
        title="持仓"
        market="cn_qdii_etf"
        agent="codex"
        strategyLabel="趋势进攻"
        onClose={vi.fn()}
      />
    );

    expect(await screen.findByRole("dialog", { name: "证券详情" })).toBeVisible();
    expect(await screen.findByText("净资产收益率 ROE")).toBeInTheDocument();
    expect(screen.getByText("毛利率")).toBeInTheDocument();
    expect(screen.getByText("K线图")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "底层指数成分" })).toBeInTheDocument();
    expect(screen.getByText("英伟达")).toBeInTheDocument();
    expect(screen.getByText("7.60%")).toBeInTheDocument();
    expect(screen.getByText("数据日期 2026-06-30")).toBeInTheDocument();
    expect(screen.queryByText("gross_margin")).not.toBeInTheDocument();
    expect(chartMocks.CandlestickChart.mock.calls[0]?.[0]).toEqual(expect.objectContaining({
      trades: [{ trade_date: "2026-07-14", side: "buy", shares: 100 }],
      strategyLabel: "趋势进攻",
    }));
  });
});
