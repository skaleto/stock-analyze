import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { Candle, NavPoint, OrderRow, StrategyComparisonPoint } from "./types";
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
    setHovered(filtered[filtered.length - 1] ?? null);
  }, [filtered]);

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

export function StrategyComparisonChart({
  points,
  strategies,
}: {
  points: StrategyComparisonPoint[];
  strategies: {
    claude: { label: string; color: string };
    codex: { label: string; color: string };
  };
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<StrategyComparisonPoint | null>(points[points.length - 1] ?? null);

  useEffect(() => {
    setHovered(points[points.length - 1] ?? null);
  }, [points]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || points.length === 0) return undefined;
    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 320),
      height: 286,
      layout: chartLayout,
      grid: {
        vertLines: { color: "#182230" },
        horzLines: { color: "#182230" },
      },
      rightPriceScale: { borderColor: "#2a3748", scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: "#2a3748", rightOffset: 2 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { priceFormatter: (price: number) => `${price.toFixed(2)}%` },
    });
    const defensive = chart.addSeries(LineSeries, {
      color: strategies.claude.color,
      lineWidth: 2,
      title: strategies.claude.label,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const trend = chart.addSeries(LineSeries, {
      color: strategies.codex.color,
      lineWidth: 2,
      title: strategies.codex.label,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    const benchmark = chart.addSeries(LineSeries, {
      color: "#91a0b2",
      lineWidth: 1,
      lineStyle: 2,
      title: "赛季基准",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    defensive.setData(points
      .filter((point) => typeof point.claude === "number")
      .map((point) => ({ time: point.date as Time, value: (point.claude ?? 0) * 100 })));
    trend.setData(points
      .filter((point) => typeof point.codex === "number")
      .map((point) => ({ time: point.date as Time, value: (point.codex ?? 0) * 100 })));
    benchmark.setData(points
      .filter((point) => typeof point.benchmark === "number")
      .map((point) => ({ time: point.date as Time, value: (point.benchmark ?? 0) * 100 })));
    const pointByDate = new Map(points.map((point) => [point.date, point]));
    chart.subscribeCrosshairMove((parameter) => {
      const date = typeof parameter.time === "string" ? parameter.time : null;
      setHovered((date && pointByDate.get(date)) || points[points.length - 1] || null);
    });
    chart.timeScale().fitContent();
    const stopObserving = observeChart(container, chart);
    return () => {
      stopObserving();
      chart.remove();
    };
  }, [points, strategies.claude.color, strategies.claude.label, strategies.codex.color, strategies.codex.label]);

  if (points.length === 0) {
    return <div className="chart-empty">赛季净值将在首次估值后出现</div>;
  }

  return (
    <div className="financial-chart strategy-comparison-chart">
      <div className="chart-legend strategy-chart-legend" aria-label="双策略图例">
        <span><i style={{ background: strategies.claude.color }} />{strategies.claude.label}</span>
        <span><i style={{ background: strategies.codex.color }} />{strategies.codex.label}</span>
        <span><i className="legend-benchmark" />赛季基准</span>
      </div>
      <div className="chart-readout strategy-readout" aria-live="polite">
        <span>{hovered?.date ?? "-"}</span>
        <span>{strategies.claude.label} <b>{formatPercent(hovered?.claude)}</b></span>
        <span>{strategies.codex.label} <b>{formatPercent(hovered?.codex)}</b></span>
        <span>基准 <b>{formatPercent(hovered?.benchmark)}</b></span>
      </div>
      {points.length < 2 ? (
        <div className="chart-sparse-note">当前 1 个估值点，至少 2 个估值点后形成曲线</div>
      ) : null}
      <div ref={containerRef} className="chart-canvas strategy-chart-canvas" aria-label="双策略赛季净值对比图" />
    </div>
  );
}

const BUY_SIDES = new Set(["buy", "cover"]);
const SELL_SIDES = new Set(["sell", "short"]);
const BUY_MARKER_COLOR = "#38bdf8";
const SELL_MARKER_COLOR = "#f59e0b";

type TradeMarkerDetail = {
  id: string;
  time: string;
  kind: "buy" | "sell";
  strategyLabel: string;
  count: number;
  shares: number;
  averagePrice: number | null;
  grossAmount: number;
  fees: number;
};

type CandleRange = 30 | 90 | 365 | 0;

const MOVING_AVERAGES = [
  { key: "ma5", label: "MA5", period: 5, color: "#a78bfa" },
  { key: "ma10", label: "MA10", period: 10, color: "#2dd4bf" },
  { key: "ma20", label: "MA20", period: 20, color: "#f472b6" },
  { key: "ma60", label: "MA60", period: 60, color: "#94a3b8" },
] as const;

type MovingAverageKey = typeof MOVING_AVERAGES[number]["key"];
type IndicatorKey = MovingAverageKey | "volume";
type IndicatorVisibility = Record<IndicatorKey, boolean>;

const DEFAULT_INDICATORS: IndicatorVisibility = {
  ma5: true,
  ma10: true,
  ma20: true,
  ma60: true,
  volume: true,
};

function movingAverageData(candles: Candle[], period: number): { time: Time; value: number }[] {
  let rollingTotal = 0;
  const points: { time: Time; value: number }[] = [];
  candles.forEach((candle, index) => {
    rollingTotal += candle.close;
    if (index >= period) rollingTotal -= candles[index - period].close;
    if (index >= period - 1) {
      points.push({ time: candle.date as Time, value: rollingTotal / period });
    }
  });
  return points;
}

function formatVolume(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function candleVisibleRange(candles: Candle[], range: CandleRange): { from: Time; to: Time } | null {
  if (range === 0 || candles.length === 0) return null;
  const last = candles[candles.length - 1];
  const threshold = new Date(`${last.date}T00:00:00Z`);
  threshold.setUTCDate(threshold.getUTCDate() - range);
  const thresholdDate = threshold.toISOString().slice(0, 10);
  const first = candles.find((candle) => candle.date >= thresholdDate) ?? candles[0];
  return { from: first.date as Time, to: last.date as Time };
}

function visibleCandleTrades(candles: Candle[], trades: OrderRow[]): OrderRow[] {
  const candleDates = new Set(candles.map((candle) => candle.date));
  return trades.filter((trade) => {
    const tradeDate = String(trade.trade_date ?? trade.date ?? "").slice(0, 10);
    const side = String(trade.side ?? "").toLowerCase();
    return Boolean(
      tradeDate
      && candleDates.has(tradeDate)
      && (BUY_SIDES.has(side) || SELL_SIDES.has(side))
    );
  });
}

function tradeNumber(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function buildTradeMarkerBundle(trades: OrderRow[], strategyLabel: string): {
  markers: SeriesMarker<Time>[];
  details: Map<string, TradeMarkerDetail>;
} {
  const grouped = new Map<string, { time: string; kind: "buy" | "sell"; rows: OrderRow[] }>();
  for (const trade of trades) {
    const time = String(trade.trade_date ?? trade.date ?? "").slice(0, 10);
    const side = String(trade.side ?? "").toLowerCase();
    const kind = BUY_SIDES.has(side) ? "buy" : "sell";
    const key = `${time}:${kind}`;
    const existing = grouped.get(key);
    grouped.set(key, { time, kind, rows: [...(existing?.rows ?? []), trade] });
  }
  const details = new Map<string, TradeMarkerDetail>();
  const markers = Array.from(grouped.values())
    .sort((left, right) => left.time.localeCompare(right.time) || left.kind.localeCompare(right.kind))
    .map(({ time, kind, rows }) => {
      const id = `trade:${time}:${kind}`;
      const shares = rows.reduce((total, row) => total + Math.abs(tradeNumber(row.shares)), 0);
      const weightedPrice = rows.reduce(
        (total, row) => total + Math.abs(tradeNumber(row.shares)) * tradeNumber(row.price),
        0,
      );
      const grossAmount = rows.reduce((total, row) => total + Math.abs(tradeNumber(row.gross_amount)), 0);
      const fees = rows.reduce(
        (total, row) => total
          + Math.abs(tradeNumber(row.commission))
          + Math.abs(tradeNumber(row.stamp_tax))
          + Math.abs(tradeNumber(row.slippage)),
        0,
      );
      details.set(id, {
        id,
        time,
        kind,
        strategyLabel,
        count: rows.length,
        shares,
        averagePrice: shares > 0 ? weightedPrice / shares : null,
        grossAmount,
        fees,
      });
      return {
        id,
        time: time as Time,
        position: (kind === "buy" ? "belowBar" : "aboveBar") as "belowBar" | "aboveBar",
        color: kind === "buy" ? BUY_MARKER_COLOR : SELL_MARKER_COLOR,
        shape: "circle" as const,
        size: 1.5,
      };
    });
  return { markers, details };
}

export function CandlestickChart({
  candles,
  trades = [],
  strategyLabel = "当前策略",
}: {
  candles: Candle[];
  trades?: OrderRow[];
  strategyLabel?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState<CandleRange>(30);
  const [visibleIndicators, setVisibleIndicators] = useState<IndicatorVisibility>(DEFAULT_INDICATORS);
  const [hovered, setHovered] = useState<Candle | null>(candles[candles.length - 1] ?? null);
  const [hoveredTrade, setHoveredTrade] = useState<TradeMarkerDetail | null>(null);
  const visibleTrades = useMemo(
    () => visibleCandleTrades(candles, trades),
    [candles, trades],
  );
  const markerBundle = useMemo(
    () => buildTradeMarkerBundle(visibleTrades, strategyLabel),
    [strategyLabel, visibleTrades],
  );
  const visibleRange = useMemo(() => candleVisibleRange(candles, range), [candles, range]);
  const buyCount = visibleTrades.filter((trade) => BUY_SIDES.has(String(trade.side ?? "").toLowerCase())).length;
  const sellCount = visibleTrades.length - buyCount;

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
    priceSeries.setData(candles.map((candle) => ({
      time: candle.date as Time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    })));
    if (visibleIndicators.volume) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        lastValueVisible: false,
        priceLineVisible: false,
      });
      volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
      volumeSeries.setData(candles.map((candle) => ({
        time: candle.date as Time,
        value: candle.volume ?? 0,
        color: candle.close >= candle.open ? "rgba(239,68,68,0.46)" : "rgba(34,197,94,0.46)",
      })));
    }
    for (const average of MOVING_AVERAGES) {
      if (!visibleIndicators[average.key]) continue;
      const series = chart.addSeries(LineSeries, {
        color: average.color,
        lineWidth: 1,
        title: average.label,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      series.setData(movingAverageData(candles, average.period));
    }
    const markerPlugin = markerBundle.markers.length ? createSeriesMarkers(priceSeries, markerBundle.markers) : null;
    const candleByDate = new Map(candles.map((candle) => [candle.date, candle]));
    chart.subscribeCrosshairMove((parameter) => {
      const date = typeof parameter.time === "string" ? parameter.time : null;
      setHovered((date && candleByDate.get(date)) || candles[candles.length - 1] || null);
      const objectId = parameter.hoveredInfo?.objectId ?? parameter.hoveredObjectId;
      setHoveredTrade(typeof objectId === "string" ? markerBundle.details.get(objectId) ?? null : null);
    });
    if (visibleRange) chart.timeScale().setVisibleRange(visibleRange);
    else chart.timeScale().fitContent();
    const stopObserving = observeChart(container, chart);
    return () => {
      stopObserving();
      markerPlugin?.detach();
      chart.remove();
    };
  }, [candles, markerBundle, visibleIndicators, visibleRange]);

  if (candles.length === 0) return <div className="chart-empty">暂无历史行情缓存</div>;
  const change = hovered ? hovered.close - hovered.open : 0;
  return (
    <div className="financial-chart candle-chart">
      <div className="chart-toolbar candle-toolbar">
        <div className="trade-marker-summary" aria-label="当前策略成交标记">
          <strong>历史成交 · {strategyLabel}</strong>
          {visibleTrades.length ? (
            <>
              <span className="trade-marker-buy">买入 {buyCount}</span>
              <span className="trade-marker-sell">卖出 {sellCount}</span>
            </>
          ) : (
            <span>暂无成交，执行后自动标注</span>
          )}
        </div>
        <div className="range-control" aria-label="K线时间范围">
          {([
            { value: 30, label: "1月" },
            { value: 90, label: "3月" },
            { value: 365, label: "1年" },
            { value: 0, label: "全部" },
          ] as const).map((item) => (
            <button key={item.value} type="button" className={range === item.value ? "active" : ""} onClick={() => setRange(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <div className="indicator-controls" aria-label="技术指标">
        {MOVING_AVERAGES.map((average) => (
          <label key={average.key} className="indicator-toggle">
            <input
              type="checkbox"
              checked={visibleIndicators[average.key]}
              onChange={() => setVisibleIndicators((current) => ({
                ...current,
                [average.key]: !current[average.key],
              }))}
            />
            <i style={{ background: average.color }} aria-hidden="true" />
            <span>{average.label}</span>
          </label>
        ))}
        <label className="indicator-toggle">
          <input
            type="checkbox"
            checked={visibleIndicators.volume}
            onChange={() => setVisibleIndicators((current) => ({
              ...current,
              volume: !current.volume,
            }))}
          />
          <i className="indicator-volume" aria-hidden="true" />
          <span>成交量</span>
        </label>
      </div>
      <div className="ohlc-readout" aria-live="polite">
        <span>{hovered?.date}</span>
        <span>开 <b>{hovered?.open.toFixed(3)}</b></span>
        <span>高 <b>{hovered?.high.toFixed(3)}</b></span>
        <span>低 <b>{hovered?.low.toFixed(3)}</b></span>
        <span>收 <b className={change >= 0 ? "positive" : "negative"}>{hovered?.close.toFixed(3)}</b></span>
        {visibleIndicators.volume ? <span>量 <b>{formatVolume(hovered?.volume)}</b></span> : null}
        <span>成交额 <b>{formatMoney(hovered?.amount)}</b></span>
      </div>
      {hoveredTrade ? (
        <div className={`trade-marker-tooltip trade-marker-tooltip-${hoveredTrade.kind}`} role="status" aria-label="成交标记详情">
          <header>
            <i aria-hidden="true" />
            <strong>{hoveredTrade.strategyLabel} · {hoveredTrade.kind === "buy" ? "买入" : "卖出"}</strong>
            <time>{hoveredTrade.time}</time>
          </header>
          <div>
            <span>{hoveredTrade.count} 笔</span>
            <span>成交价 {hoveredTrade.averagePrice?.toFixed(3) ?? "-"}</span>
            <span>份额 {hoveredTrade.shares.toLocaleString("zh-CN")}</span>
            <span>金额 {formatMoney(hoveredTrade.grossAmount)}</span>
            <span>费用 {formatMoney(hoveredTrade.fees)}</span>
          </div>
        </div>
      ) : null}
      <div ref={containerRef} className="chart-canvas candle-canvas" aria-label="日K线和成交量图" />
    </div>
  );
}
