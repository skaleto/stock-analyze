import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const chartMocks = vi.hoisted(() => {
  const setData = vi.fn();
  const applyPriceScaleOptions = vi.fn();
  const addSeries = vi.fn(() => ({ setData, priceScale: () => ({ applyOptions: applyPriceScaleOptions }) }));
  const remove = vi.fn();
  const subscribeCrosshairMove = vi.fn();
  const fitContent = vi.fn();
  const detachMarkers = vi.fn();
  const createSeriesMarkers = vi.fn((_series: unknown, _markers: unknown[]) => ({ detach: detachMarkers }));
  const createChart = vi.fn(() => ({
    addSeries,
    remove,
    subscribeCrosshairMove,
    timeScale: () => ({ fitContent }),
    applyOptions: vi.fn(),
  }));
  return { setData, addSeries, remove, subscribeCrosshairMove, fitContent, createChart, createSeriesMarkers, detachMarkers };
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

  it("renders candlesticks and volume and releases the chart", () => {
    const { unmount } = render(
      <CandlestickChart
        candles={[
          { date: "2026-07-10", open: 2.1, high: 2.2, low: 2.0, close: 2.15, volume: 1000, amount: 2200 },
        ]}
      />
    );

    expect(chartMocks.addSeries).toHaveBeenCalledTimes(2);
    expect(chartMocks.setData).toHaveBeenCalled();
    unmount();
    expect(chartMocks.remove).toHaveBeenCalled();
  });

  it("marks all instrument trades covered by the visible candle history", () => {
    render(
      <CandlestickChart
        candles={[
          { date: "2026-07-10", open: 2.0, high: 2.1, low: 1.9, close: 2.05 },
          { date: "2026-07-14", open: 2.1, high: 2.3, low: 2.0, close: 2.2 },
        ]}
        trades={[
          { trade_date: "2026-07-10", side: "buy", shares: 100 },
          { trade_date: "2026-07-14", side: "buy", shares: 200 },
          { trade_date: "2026-07-14", side: "sell", shares: 100 },
        ]}
        strategyLabel="趋势进攻"
      />
    );

    expect(chartMocks.createSeriesMarkers).toHaveBeenCalledTimes(1);
    expect(chartMocks.createSeriesMarkers.mock.calls[0]?.[1]).toEqual([
      {
        time: "2026-07-10",
        position: "belowBar",
        color: "#ef4444",
        shape: "arrowUp",
        text: "趋势买",
      },
      {
        time: "2026-07-14",
        position: "belowBar",
        color: "#ef4444",
        shape: "arrowUp",
        text: "趋势买",
      },
      {
        time: "2026-07-14",
        position: "aboveBar",
        color: "#22c55e",
        shape: "arrowDown",
        text: "趋势卖",
      },
    ]);
    expect(screen.getByText("历史成交 · 趋势进攻")).toBeInTheDocument();
    expect(screen.getByText("买入 2")).toBeInTheDocument();
    expect(screen.getByText("卖出 1")).toBeInTheDocument();
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
