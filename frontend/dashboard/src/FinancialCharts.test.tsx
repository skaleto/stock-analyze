import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chartMocks = vi.hoisted(() => {
  const setData = vi.fn();
  const applyPriceScaleOptions = vi.fn();
  const addSeries = vi.fn((_seriesType: { type?: string }, _options?: unknown) => ({
    setData,
    priceScale: () => ({ applyOptions: applyPriceScaleOptions }),
  }));
  const remove = vi.fn();
  const subscribeCrosshairMove = vi.fn();
  const fitContent = vi.fn();
  const setVisibleRange = vi.fn();
  const detachMarkers = vi.fn();
  const createSeriesMarkers = vi.fn((_series: unknown, _markers: unknown[]) => ({ detach: detachMarkers }));
  const createChart = vi.fn(() => ({
    addSeries,
    remove,
    subscribeCrosshairMove,
    timeScale: () => ({ fitContent, setVisibleRange }),
    applyOptions: vi.fn(),
  }));
  return { setData, addSeries, remove, subscribeCrosshairMove, fitContent, setVisibleRange, createChart, createSeriesMarkers, detachMarkers };
});

vi.mock("lightweight-charts", () => ({
  createChart: chartMocks.createChart,
  createSeriesMarkers: chartMocks.createSeriesMarkers,
  AreaSeries: { type: "area" },
  CandlestickSeries: { type: "candlestick" },
  HistogramSeries: { type: "histogram" },
  LineSeries: { type: "line" },
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
}));

import { CandlestickChart, PerformanceChart, StrategyComparisonChart } from "./FinancialCharts";


describe("financial charts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
  });

  it("renders portfolio and benchmark as interactive percentage series", () => {
    render(
      <PerformanceChart
        points={[
          { date: "2026-07-09", return: 0, benchmark_return: 0 },
          { date: "2026-07-10", return: 0.01, benchmark_return: 0.005 },
        ]}
        benchmarkLabel="组合基准"
      />
    );

    expect(chartMocks.createChart).toHaveBeenCalledTimes(1);
    expect(chartMocks.addSeries).toHaveBeenCalledTimes(2);
    expect(screen.getByText("组合净值")).toBeInTheDocument();
    expect(screen.getByText("组合基准")).toBeInTheDocument();
    expect(chartMocks.subscribeCrosshairMove).toHaveBeenCalledTimes(1);
  });

  it("initializes the readout when nav points arrive after the first render", () => {
    const { rerender } = render(
      <PerformanceChart points={[]} benchmarkLabel="组合基准" />
    );

    rerender(
      <PerformanceChart
        points={[{ date: "2026-07-10", return: 0, benchmark_return: null }]}
        benchmarkLabel="组合基准"
      />
    );

    expect(screen.getByText("2026-07-10")).toBeInTheDocument();
    expect(screen.getByText("0.00%")).toBeInTheDocument();
  });

  it("renders candlesticks, indicators, and releases the chart", () => {
    const { unmount } = render(
      <CandlestickChart
        candles={[
          { date: "2026-07-10", open: 2.1, high: 2.2, low: 2.0, close: 2.15, volume: 1000, amount: 2200 },
        ]}
      />
    );

    expect(chartMocks.addSeries).toHaveBeenCalledTimes(6);
    expect(chartMocks.setData).toHaveBeenCalled();
    unmount();
    expect(chartMocks.remove).toHaveBeenCalled();
  });

  it("shows the latest month by default and allows the full history", () => {
    render(
      <CandlestickChart
        candles={[
          { date: "2026-05-29", open: 2.0, high: 2.1, low: 1.9, close: 2.05 },
          { date: "2026-06-15", open: 2.1, high: 2.2, low: 2.0, close: 2.15 },
          { date: "2026-07-13", open: 2.2, high: 2.3, low: 2.1, close: 2.25 },
        ]}
      />
    );

    expect(screen.getByRole("button", { name: "1月" })).toHaveClass("active");
    expect(chartMocks.setVisibleRange).toHaveBeenLastCalledWith({
      from: "2026-06-15",
      to: "2026-07-13",
    });

    fireEvent.click(screen.getByRole("button", { name: "全部" }));
    expect(chartMocks.fitContent).toHaveBeenLastCalledWith();
  });

  it("renders selectable moving averages and volume from the full candle history", () => {
    const candles = Array.from({ length: 61 }, (_, index) => {
      const day = new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10);
      return { date: day, open: index + 1, high: index + 2, low: index, close: index + 1, volume: (index + 1) * 1000 };
    });
    render(<CandlestickChart candles={candles} />);

    for (const label of ["MA5", "MA10", "MA20", "MA60", "成交量"]) {
      expect(screen.getByRole("checkbox", { name: label })).toBeChecked();
    }
    const ma5Data = chartMocks.setData.mock.calls
      .map(([rows]) => rows)
      .find((rows) => Array.isArray(rows) && rows.length === 57);
    expect(ma5Data?.[0]).toEqual({ time: "2026-01-05", value: 3 });
    expect(ma5Data?.at(-1)?.value).toBe(59);

    fireEvent.click(screen.getByRole("checkbox", { name: "MA60" }));
    expect(screen.getByRole("checkbox", { name: "MA60" })).not.toBeChecked();

    const callCountBeforeVolumeToggle = chartMocks.addSeries.mock.calls.length;
    fireEvent.click(screen.getByRole("checkbox", { name: "成交量" }));
    expect(screen.getByRole("checkbox", { name: "成交量" })).not.toBeChecked();
    expect(
      chartMocks.addSeries.mock.calls
        .slice(callCountBeforeVolumeToggle)
        .some(([seriesType]) => seriesType?.type === "histogram")
    ).toBe(false);
  });

  it("marks all instrument trades covered by the visible candle history", () => {
    render(
      <CandlestickChart
        candles={[
          { date: "2026-07-10", open: 2.0, high: 2.1, low: 1.9, close: 2.05 },
          { date: "2026-07-14", open: 2.1, high: 2.3, low: 2.0, close: 2.2 },
        ]}
        trades={[
          { trade_date: "2026-07-10", side: "buy", shares: 100, price: 2.01, gross_amount: 201, commission: 0.1, slippage: 0.05 },
          { trade_date: "2026-07-14", side: "buy", shares: 200 },
          { trade_date: "2026-07-14", side: "sell", shares: 100 },
        ]}
        strategyLabel="趋势进攻"
      />
    );

    expect(chartMocks.createSeriesMarkers).toHaveBeenCalledTimes(1);
    expect(chartMocks.createSeriesMarkers.mock.calls[0]?.[1]).toEqual([
      {
        id: "trade:2026-07-10:buy",
        time: "2026-07-10",
        position: "belowBar",
        color: "#38bdf8",
        shape: "circle",
        size: 1.5,
      },
      {
        id: "trade:2026-07-14:buy",
        time: "2026-07-14",
        position: "belowBar",
        color: "#38bdf8",
        shape: "circle",
        size: 1.5,
      },
      {
        id: "trade:2026-07-14:sell",
        time: "2026-07-14",
        position: "aboveBar",
        color: "#f59e0b",
        shape: "circle",
        size: 1.5,
      },
    ]);
    expect(screen.getByText("历史成交 · 趋势进攻")).toBeInTheDocument();
    expect(screen.getByText("买入 2")).toBeInTheDocument();
    expect(screen.getByText("卖出 1")).toBeInTheDocument();

    act(() => {
      chartMocks.subscribeCrosshairMove.mock.calls[0]?.[0]({
        time: "2026-07-10",
        hoveredObjectId: "trade:2026-07-10:buy",
      });
    });
    expect(screen.getByText("趋势进攻 · 买入")).toBeInTheDocument();
    expect(screen.getByText("成交价 2.010")).toBeInTheDocument();
    expect(screen.getByText("份额 100")).toBeInTheDocument();
    expect(screen.getByText("金额 ¥201.00")).toBeInTheDocument();
    expect(screen.getByText("费用 ¥0.15")).toBeInTheDocument();
  });

  it("renders two strategy series and a benchmark with crosshair readout", () => {
    render(
      <StrategyComparisonChart
        points={[
          { date: "2026-07-10", claude: 0, codex: 0, benchmark: 0 },
          { date: "2026-07-14", claude: 0.02, codex: 0.04, benchmark: 0.01 },
        ]}
        strategies={{
          claude: { label: "稳健防守", color: "#d6a84b" },
          codex: { label: "趋势进攻", color: "#22d3ee" },
        }}
      />
    );

    expect(chartMocks.addSeries).toHaveBeenCalledTimes(3);
    expect(screen.getAllByText("稳健防守").length).toBeGreaterThan(0);
    expect(screen.getAllByText("趋势进攻").length).toBeGreaterThan(0);
    expect(screen.getByText("赛季基准")).toBeInTheDocument();
    expect(screen.getByText("2026-07-14")).toBeInTheDocument();
    expect(chartMocks.subscribeCrosshairMove).toHaveBeenCalledTimes(1);
  });

  it("explains why a new season does not have a visible trajectory yet", () => {
    render(
      <StrategyComparisonChart
        points={[{ date: "2026-07-11", claude: 0, codex: 0, benchmark: 0 }]}
        strategies={{
          claude: { label: "稳健防守", color: "#d6a84b" },
          codex: { label: "趋势进攻", color: "#22d3ee" },
        }}
      />
    );

    expect(screen.getByText("当前 1 个估值点，至少 2 个估值点后形成曲线")).toBeInTheDocument();
  });
});
