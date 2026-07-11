import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import type { Candle, NavPoint } from "./types";
import { formatMoney, formatPercent } from "./finance";

const chartLayout = {
  background: { type: ColorType.Solid, color: "#0d131d" },
  textColor: "#8391a3",
  fontFamily: '"SF Mono", "PingFang SC", monospace',
  fontSize: 11,
};

function observeChart(container: HTMLElement, chart: IChartApi): () => void {
  if (typeof ResizeObserver === "undefined") return () => undefined;
  const observer = new ResizeObserver(([entry]) => {
    const width = Math.floor(entry?.contentRect.width ?? container.clientWidth);
    if (width > 0) chart.applyOptions({ width });
  });
  observer.observe(container);
  return () => observer.disconnect();
}

export function PerformanceChart({
  points,
  benchmarkLabel,
}: {
  points: NavPoint[];
  benchmarkLabel: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<30 | 90 | 0>(90);
  const [hovered, setHovered] = useState<NavPoint | null>(points[points.length - 1] ?? null);
  const filtered = useMemo(() => (range === 0 ? points : points.slice(-range)), [points, range]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || filtered.length === 0) return undefined;
    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 320),
      height: 270,
      layout: chartLayout,
      grid: {
        vertLines: { color: "#182230" },
        horzLines: { color: "#182230" },
      },
      rightPriceScale: { borderColor: "#2a3748", scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: "#2a3748", timeVisible: false, rightOffset: 2 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { priceFormatter: (price: number) => `${price.toFixed(2)}%` },
    });
    const portfolio = chart.addSeries(LineSeries, {
      color: "#22d3ee",
      lineWidth: 2,
      title: "组合净值",
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const benchmark = chart.addSeries(LineSeries, {
      color: "#a3b2c4",
      lineWidth: 2,
      lineStyle: 2,
      title: benchmarkLabel,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    portfolio.setData(filtered
      .filter((point) => typeof point.return === "number")
      .map((point) => ({ time: point.date as Time, value: (point.return ?? 0) * 100 })));
    benchmark.setData(filtered
      .filter((point) => typeof point.benchmark_return === "number")
      .map((point) => ({ time: point.date as Time, value: (point.benchmark_return ?? 0) * 100 })));
    const pointByDate = new Map(filtered.map((point) => [point.date, point]));
    chart.subscribeCrosshairMove((parameter) => {
      const date = typeof parameter.time === "string" ? parameter.time : null;
      setHovered((date && pointByDate.get(date)) || filtered[filtered.length - 1] || null);
    });
    chart.timeScale().fitContent();
    const stopObserving = observeChart(container, chart);
    return () => {
      stopObserving();
      chart.remove();
    };
  }, [benchmarkLabel, filtered]);

  if (points.length === 0) {
    return <div className="chart-empty">净值序列将在首次每日估值后出现</div>;
  }

  const excess = hovered && typeof hovered.return === "number" && typeof hovered.benchmark_return === "number"
    ? hovered.return - hovered.benchmark_return
    : null;
  return (
    <div className="financial-chart performance-chart">
      <div className="chart-toolbar">
        <div className="chart-legend" aria-label="图例">
          <span><i className="legend-portfolio" />组合净值</span>
          <span><i className="legend-benchmark" />{benchmarkLabel}</span>
        </div>
        <div className="range-control" aria-label="净值时间范围">
          {([{ value: 30, label: "1月" }, { value: 90, label: "3月" }, { value: 0, label: "全部" }] as const).map((item) => (
            <button key={item.value} type="button" className={range === item.value ? "active" : ""} onClick={() => setRange(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-readout" aria-live="polite">
        <span>{hovered?.date ?? "-"}</span>
        <strong className={(hovered?.return ?? 0) >= 0 ? "positive" : "negative"}>{formatPercent(hovered?.return)}</strong>
        <span>基准 {formatPercent(hovered?.benchmark_return)}</span>
        <span>超额 <b className={(excess ?? 0) >= 0 ? "positive" : "negative"}>{formatPercent(excess)}</b></span>
      </div>
      <div ref={containerRef} className="chart-canvas" aria-label="组合净值与基准对比图" />
    </div>
  );
}

export function CandlestickChart({ candles }: { candles: Candle[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<Candle | null>(candles[candles.length - 1] ?? null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || candles.length === 0) return undefined;
    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 320),
      height: 340,
      layout: chartLayout,
      grid: {
        vertLines: { color: "#182230" },
        horzLines: { color: "#182230" },
      },
      rightPriceScale: { borderColor: "#2a3748", scaleMargins: { top: 0.08, bottom: 0.27 } },
      timeScale: { borderColor: "#2a3748", rightOffset: 3 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#ef4444",
      downColor: "#22c55e",
      borderUpColor: "#ef4444",
      borderDownColor: "#22c55e",
      wickUpColor: "#ef4444",
      wickDownColor: "#22c55e",
      priceLineVisible: false,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    priceSeries.setData(candles.map((candle) => ({
      time: candle.date as Time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })));
    volumeSeries.setData(candles.map((candle) => ({
      time: candle.date as Time,
      value: candle.volume ?? 0,
      color: candle.close >= candle.open ? "rgba(239,68,68,0.46)" : "rgba(34,197,94,0.46)",
    })));
    const candleByDate = new Map(candles.map((candle) => [candle.date, candle]));
    chart.subscribeCrosshairMove((parameter) => {
      const date = typeof parameter.time === "string" ? parameter.time : null;
      setHovered((date && candleByDate.get(date)) || candles[candles.length - 1] || null);
    });
    chart.timeScale().fitContent();
    const stopObserving = observeChart(container, chart);
    return () => {
      stopObserving();
      chart.remove();
    };
  }, [candles]);

  if (candles.length === 0) return <div className="chart-empty">暂无历史行情缓存</div>;
  const change = hovered ? hovered.close - hovered.open : 0;
  return (
    <div className="financial-chart candle-chart">
      <div className="ohlc-readout" aria-live="polite">
        <span>{hovered?.date}</span>
        <span>开 <b>{hovered?.open.toFixed(3)}</b></span>
        <span>高 <b>{hovered?.high.toFixed(3)}</b></span>
        <span>低 <b>{hovered?.low.toFixed(3)}</b></span>
        <span>收 <b className={change >= 0 ? "positive" : "negative"}>{hovered?.close.toFixed(3)}</b></span>
        <span>成交额 <b>{formatMoney(hovered?.amount)}</b></span>
      </div>
      <div ref={containerRef} className="chart-canvas candle-canvas" aria-label="日K线和成交量图" />
    </div>
  );
}
