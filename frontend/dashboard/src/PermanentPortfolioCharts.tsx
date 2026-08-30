import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";
import type {
  PermanentPortfolioSeriesPoint,
  PermanentPortfolioTrade,
} from "./types";

type MetricKey = "normalized_nav" | "drawdown" | "volatility_63d";
type StrategyId = "fixed" | "dynamic";
type BenchmarkSeries = {
  id: string;
  name: string;
  series: PermanentPortfolioSeriesPoint[];
};

type Props = {
  series: {
    fixed: PermanentPortfolioSeriesPoint[];
    dynamic: PermanentPortfolioSeriesPoint[];
  };
  benchmarks?: BenchmarkSeries[];
  trades?: Partial<Record<StrategyId, PermanentPortfolioTrade[]>>;
  stageBoundary?: {
    date?: string;
    before_label?: string;
    after_label?: string;
  };
};

const PLOT_LEFT = 24;
const PLOT_RIGHT = 976;
const DAY_MS = 24 * 60 * 60 * 1000;
const MIN_WINDOW_MS = 30 * DAY_MS;

function isoDate(value: string): string {
  const compact = value.split("-").join("").slice(0, 8);
  if (!/^\d{8}$/.test(compact)) {
    return value.slice(0, 10);
  }
  return `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`;
}

function subtractYears(value: string, years: number): string {
  const [year, month, day] = isoDate(value).split("-").map(Number);
  const date = new Date(Date.UTC(year - years, month - 1, day));
  if (date.getUTCMonth() !== month - 1) {
    date.setUTCDate(0);
  }
  return date.toISOString().slice(0, 10);
}

function filterSeries(
  values: PermanentPortfolioSeriesPoint[],
  startDate: string,
  endDate: string,
): PermanentPortfolioSeriesPoint[] {
  return values.filter((point) => {
    const date = isoDate(point.date);
    return date >= startDate && date <= endDate;
  });
}

function dateTime(value: string): number {
  return Date.parse(`${isoDate(value)}T00:00:00Z`);
}

function dateFromTime(value: number): string {
  return new Date(value).toISOString().slice(0, 10);
}

function clampTimeWindow(
  start: number,
  end: number,
  minimum: number,
  maximum: number,
): [number, number] {
  const fullSpan = Math.max(maximum - minimum, 0);
  const span = Math.min(Math.max(end - start, 0), fullSpan);
  const nextStart = Math.max(minimum, Math.min(start, maximum - span));
  return [nextStart, nextStart + span];
}

function xForDate(value: string, startDate: string, endDate: string): number {
  const start = dateTime(startDate);
  const range = Math.max(dateTime(endDate) - start, 1);
  const ratio = (dateTime(value) - start) / range;
  return PLOT_LEFT + ratio * (PLOT_RIGHT - PLOT_LEFT);
}

function points(
  values: PermanentPortfolioSeriesPoint[],
  key: MetricKey,
  minimum: number,
  maximum: number,
  startDate: string,
  endDate: string,
): string {
  const valid = values
    .map((point) => ({ date: point.date, value: point[key] }))
    .filter(
      (point): point is { date: string; value: number } => (
        typeof point.value === "number" && Number.isFinite(point.value)
      ),
    );
  const range = maximum - minimum || 1;
  return valid.map(({ date, value }) => {
    const x = xForDate(date, startDate, endDate);
    const y = 196 - ((value - minimum) / range) * 168;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function nearestPoint(
  values: PermanentPortfolioSeriesPoint[],
  date: string,
): PermanentPortfolioSeriesPoint | undefined {
  const target = dateTime(date);
  return values.reduce<PermanentPortfolioSeriesPoint | undefined>(
    (nearest, point) => (
      !nearest
      || Math.abs(dateTime(point.date) - target)
        < Math.abs(dateTime(nearest.date) - target)
        ? point
        : nearest
    ),
    undefined,
  );
}

function formatMetric(value: number | null | undefined, metric: MetricKey) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  if (metric === "normalized_nav") {
    return value.toFixed(4);
  }
  return `${(value * 100).toFixed(2)}%`;
}

function SeriesChart({
  title,
  ariaLabel,
  metric,
  fixed,
  dynamic,
  benchmarks = [],
  trades = {},
  stageBoundary,
  isPanning,
  onWheelWindow,
  onPointerDownWindow,
  onPointerMoveWindow,
  onPointerUpWindow,
  onResetWindow,
}: {
  title: string;
  ariaLabel: string;
  metric: MetricKey;
  fixed: PermanentPortfolioSeriesPoint[];
  dynamic: PermanentPortfolioSeriesPoint[];
  benchmarks?: BenchmarkSeries[];
  trades?: Props["trades"];
  stageBoundary?: Props["stageBoundary"];
  isPanning: boolean;
  onWheelWindow: (event: WheelEvent<SVGSVGElement>) => void;
  onPointerDownWindow: (event: PointerEvent<SVGSVGElement>) => void;
  onPointerMoveWindow: (event: PointerEvent<SVGSVGElement>) => void;
  onPointerUpWindow: (event: PointerEvent<SVGSVGElement>) => void;
  onResetWindow: () => void;
}) {
  const [hoveredDate, setHoveredDate] = useState<string | null>(null);
  const benchmarkPoints = metric === "normalized_nav"
    ? benchmarks.flatMap((benchmark) => benchmark.series)
    : [];
  const values = [...fixed, ...dynamic, ...benchmarkPoints]
    .map((point) => point[metric])
    .filter(
      (value): value is number => (
        typeof value === "number" && Number.isFinite(value)
      ),
    );
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 1;
  const firstDate = fixed[0]?.date ? isoDate(fixed[0].date) : "";
  const lastDate = fixed[fixed.length - 1]?.date
    ? isoDate(fixed[fixed.length - 1].date)
    : "";
  const boundaryDate = stageBoundary?.date
    ? isoDate(stageBoundary.date)
    : "";
  const showBoundary = Boolean(
    boundaryDate
    && boundaryDate >= firstDate
    && boundaryDate <= lastDate,
  );
  const boundaryX = showBoundary
    ? xForDate(boundaryDate, firstDate, lastDate)
    : null;
  const strategySeries: Record<StrategyId, PermanentPortfolioSeriesPoint[]> = {
    fixed,
    dynamic,
  };
  const markers = metric === "normalized_nav"
    ? (Object.entries(trades) as Array<
      [StrategyId, PermanentPortfolioTrade[] | undefined]
    >).flatMap(([strategy, strategyTrades]) => {
      const grouped = new Map<string, PermanentPortfolioTrade[]>();
      for (const trade of strategyTrades ?? []) {
        const date = trade.trade_date ? isoDate(trade.trade_date) : "";
        const side = trade.side?.toLowerCase();
        if (
          !date
          || (side !== "buy" && side !== "sell")
          || date < firstDate
          || date > lastDate
        ) {
          continue;
        }
        const key = `${date}:${side}`;
        grouped.set(key, [...(grouped.get(key) ?? []), trade]);
      }
      return [...grouped.entries()].flatMap(([key, markerTrades]) => {
        const [date, side] = key.split(":") as [string, "buy" | "sell"];
        const point = nearestPoint(strategySeries[strategy], date);
        const value = point?.[metric];
        if (typeof value !== "number") {
          return [];
        }
        const range = maximum - minimum || 1;
        return [{
          date,
          side,
          strategy,
          trades: markerTrades,
          x: xForDate(date, firstDate, lastDate),
          y: 196 - ((value - minimum) / range) * 168,
        }];
      });
    })
    : [];
  const hoveredFixed = hoveredDate ? nearestPoint(fixed, hoveredDate) : undefined;
  const hoveredDynamic = hoveredDate
    ? nearestPoint(dynamic, hoveredDate)
    : undefined;
  const hoveredBenchmarks = hoveredDate && metric === "normalized_nav"
    ? benchmarks.map((benchmark) => ({
      ...benchmark,
      point: nearestPoint(benchmark.series, hoveredDate),
    }))
    : [];
  const hoveredTrades = hoveredDate
    ? (Object.entries(trades) as Array<
      [StrategyId, PermanentPortfolioTrade[] | undefined]
    >).flatMap(([strategy, strategyTrades]) => (
      (strategyTrades ?? [])
        .filter((trade) => (
          trade.trade_date && isoDate(trade.trade_date) === hoveredDate
        ))
        .map((trade) => ({ ...trade, strategy }))
    ))
    : [];

  function handleMouseMove(event: MouseEvent<SVGSVGElement>) {
    if (!fixed.length) {
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0) {
      return;
    }
    const ratio = Math.max(
      0,
      Math.min(1, (event.clientX - bounds.left) / bounds.width),
    );
    const target = dateTime(firstDate)
      + ratio * (dateTime(lastDate) - dateTime(firstDate));
    const nearest = fixed.reduce((best, point) => (
      Math.abs(dateTime(point.date) - target)
        < Math.abs(dateTime(best.date) - target)
        ? point
        : best
    ));
    setHoveredDate(isoDate(nearest.date));
  }

  return (
    <section className="permanent-chart-panel">
      <div className="permanent-chart-heading">
        <h3>{title}</h3>
        <div aria-label={`${title}图例`}>
          <span className="permanent-legend fixed">固定</span>
          <span className="permanent-legend dynamic">动态</span>
          {metric === "normalized_nav" ? benchmarks.map((benchmark, index) => (
            <span
              key={benchmark.id}
              className={`permanent-legend benchmark benchmark-${index % 3}`}
            >
              {benchmark.name}
            </span>
          )) : null}
          {metric === "normalized_nav" ? (
            <>
              <span className="permanent-legend trade buy">买入</span>
              <span className="permanent-legend trade sell">卖出</span>
            </>
          ) : null}
        </div>
      </div>
      <svg
        className={`permanent-chart${isPanning ? " is-panning" : ""}`}
        viewBox="0 0 1000 220"
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        data-start-date={firstDate}
        data-end-date={lastDate}
        onWheel={onWheelWindow}
        onPointerDown={onPointerDownWindow}
        onPointerMove={onPointerMoveWindow}
        onPointerUp={onPointerUpWindow}
        onPointerCancel={onPointerUpWindow}
        onDoubleClick={onResetWindow}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredDate(null)}
      >
        <title>滚轮缩放，拖动平移，双击恢复全部历史</title>
        {boundaryX !== null ? (
          <>
            <rect
              x={boundaryX}
              y="28"
              width={PLOT_RIGHT - boundaryX}
              height="168"
              className="chart-stage-holdout"
            />
            <line
              x1={boundaryX}
              y1="28"
              x2={boundaryX}
              y2="196"
              className="chart-stage-boundary"
            />
            <text
              x={Math.max(PLOT_LEFT + 8, boundaryX - 8)}
              y="42"
              textAnchor="end"
              className="chart-stage-label"
            >
              {stageBoundary?.before_label ?? "开发期"}
            </text>
            <text
              x={Math.min(PLOT_RIGHT - 8, boundaryX + 8)}
              y="42"
              className="chart-stage-label holdout"
            >
              {stageBoundary?.after_label ?? "盲测期"}
            </text>
          </>
        ) : null}
        <line x1="24" y1="196" x2="976" y2="196" className="chart-axis" />
        <line x1="24" y1="28" x2="24" y2="196" className="chart-axis" />
        <polyline
          points={points(
            fixed,
            metric,
            minimum,
            maximum,
            firstDate,
            lastDate,
          )}
          className="chart-line chart-line-fixed"
        />
        <polyline
          points={points(
            dynamic,
            metric,
            minimum,
            maximum,
            firstDate,
            lastDate,
          )}
          className="chart-line chart-line-dynamic"
        />
        {metric === "normalized_nav" ? benchmarks.map((benchmark, index) => (
          <polyline
            key={benchmark.id}
            points={points(
              benchmark.series,
              metric,
              minimum,
              maximum,
              firstDate,
              lastDate,
            )}
            className={`chart-line chart-line-benchmark benchmark-${index % 3}`}
          />
        )) : null}
        {markers.map((marker) => (
          <circle
            key={`${marker.strategy}:${marker.date}:${marker.side}`}
            cx={marker.x}
            cy={marker.y}
            r="4.5"
            className={`chart-trade-marker ${marker.side}`}
            role="button"
            tabIndex={0}
            aria-label={`${marker.side === "buy" ? "买入" : "卖出"}调仓 ${
              marker.strategy === "fixed" ? "固定永久组合" : "动态永久组合"
            } ${marker.date}`}
            onMouseEnter={() => setHoveredDate(marker.date)}
            onMouseMove={(event) => {
              event.stopPropagation();
              setHoveredDate(marker.date);
            }}
            onFocus={() => setHoveredDate(marker.date)}
          />
        ))}
        <text x={PLOT_LEFT} y="214" className="chart-date-label">
          {firstDate}
        </text>
        <text
          x={PLOT_RIGHT}
          y="214"
          textAnchor="end"
          className="chart-date-label"
        >
          {lastDate}
        </text>
      </svg>
      {hoveredDate ? (
        <div className="permanent-chart-tooltip" role="tooltip">
          <strong>{hoveredDate}</strong>
          <span>固定永久组合 {formatMetric(hoveredFixed?.[metric], metric)}</span>
          <span>动态永久组合 {formatMetric(hoveredDynamic?.[metric], metric)}</span>
          {hoveredBenchmarks.map((benchmark) => (
            <span key={benchmark.id}>
              {benchmark.name} {formatMetric(
                benchmark.point?.normalized_nav,
                "normalized_nav",
              )}
            </span>
          ))}
          {hoveredTrades.map((trade, index) => (
            <span key={`${trade.strategy}:${trade.code}:${index}`}>
              {trade.side?.toLowerCase() === "buy" ? "买入" : "卖出"}{" "}
              {trade.code ?? trade.role ?? "—"}{" "}
              {trade.shares ?? "—"}份 @{" "}
              {typeof trade.price === "number"
                ? trade.price.toFixed(2)
                : "—"}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function PermanentPortfolioCharts({
  series,
  benchmarks = [],
  trades = {},
  stageBoundary,
}: Props) {
  const availableDates = useMemo(
    () => [...series.fixed, ...series.dynamic]
      .map((point) => isoDate(point.date))
      .filter(Boolean)
      .sort(),
    [series],
  );
  const minimumDate = availableDates[0] ?? "";
  const maximumDate = availableDates[availableDates.length - 1] ?? "";
  const [startDate, setStartDate] = useState(minimumDate);
  const [endDate, setEndDate] = useState(maximumDate);
  const [metric, setMetric] = useState<MetricKey>("normalized_nav");
  const [showBenchmarks, setShowBenchmarks] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const dragState = useRef<{
    pointerId: number;
    clientX: number;
    start: number;
    end: number;
    width: number;
  } | null>(null);

  useEffect(() => {
    setStartDate(minimumDate);
    setEndDate(maximumDate);
    setIsPanning(false);
    dragState.current = null;
  }, [maximumDate, minimumDate]);

  const fixed = useMemo(
    () => filterSeries(series.fixed, startDate, endDate),
    [endDate, series.fixed, startDate],
  );
  const dynamic = useMemo(
    () => filterSeries(series.dynamic, startDate, endDate),
    [endDate, series.dynamic, startDate],
  );
  const visibleBenchmarks = useMemo(
    () => (showBenchmarks ? benchmarks : []).map((benchmark) => ({
      ...benchmark,
      series: filterSeries(benchmark.series, startDate, endDate),
    })),
    [benchmarks, endDate, showBenchmarks, startDate],
  );
  const visibleTrades = useMemo(() => (
    Object.fromEntries(
      (Object.entries(trades) as Array<
        [StrategyId, PermanentPortfolioTrade[] | undefined]
      >).map(([strategy, strategyTrades]) => [
        strategy,
        (strategyTrades ?? []).filter((trade) => {
          const date = trade.trade_date ? isoDate(trade.trade_date) : "";
          return date >= startDate && date <= endDate;
        }),
      ]),
    ) as Partial<Record<StrategyId, PermanentPortfolioTrade[]>>
  ), [endDate, startDate, trades]);
  const visibleDates = [...fixed, ...dynamic]
    .map((point) => isoDate(point.date))
    .sort();
  const visibleStart = visibleDates[0] ?? startDate;
  const visibleEnd = visibleDates[visibleDates.length - 1] ?? endDate;

  if (!minimumDate || !maximumDate) {
    return (
      <div className="permanent-chart-empty">
        暂无可展示的历史序列
      </div>
    );
  }

  function updateWindow(start: number, end: number) {
    const [nextStart, nextEnd] = clampTimeWindow(
      start,
      end,
      dateTime(minimumDate),
      dateTime(maximumDate),
    );
    setStartDate(dateFromTime(nextStart));
    setEndDate(dateFromTime(nextEnd));
  }

  function handleWheel(event: WheelEvent<SVGSVGElement>) {
    event.preventDefault();
    const bounds = event.currentTarget.getBoundingClientRect();
    const width = bounds.width || 1000;
    const plotLeft = bounds.left + width * (PLOT_LEFT / 1000);
    const plotWidth = width * ((PLOT_RIGHT - PLOT_LEFT) / 1000);
    const ratio = Math.max(
      0,
      Math.min(1, (event.clientX - plotLeft) / plotWidth),
    );
    const start = dateTime(startDate);
    const end = dateTime(endDate);
    const fullSpan = dateTime(maximumDate) - dateTime(minimumDate);
    const currentSpan = Math.max(end - start, MIN_WINDOW_MS);
    const scale = event.deltaY < 0 ? 0.8 : 1.25;
    const nextSpan = Math.min(
      fullSpan,
      Math.max(Math.min(MIN_WINDOW_MS, fullSpan), currentSpan * scale),
    );
    const anchor = start + ratio * (end - start);
    updateWindow(
      anchor - ratio * nextSpan,
      anchor + (1 - ratio) * nextSpan,
    );
  }

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) {
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    dragState.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      start: dateTime(startDate),
      end: dateTime(endDate),
      width: bounds.width || 1000,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setIsPanning(true);
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    const drag = dragState.current;
    if (!drag || drag.pointerId !== event.pointerId) {
      return;
    }
    const shift = -(
      (event.clientX - drag.clientX)
      / drag.width
      * (drag.end - drag.start)
    );
    updateWindow(drag.start + shift, drag.end + shift);
  }

  function handlePointerUp(event: PointerEvent<SVGSVGElement>) {
    if (dragState.current?.pointerId !== event.pointerId) {
      return;
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragState.current = null;
    setIsPanning(false);
  }

  function resetWindow() {
    setStartDate(minimumDate);
    setEndDate(maximumDate);
  }

  function selectRange(years: number | null) {
    if (years === null) {
      resetWindow();
      return;
    }
    setStartDate([minimumDate, subtractYears(maximumDate, years)].sort()[1]);
    setEndDate(maximumDate);
  }

  const metricOptions: Array<{
    key: MetricKey;
    label: string;
    title: string;
    ariaLabel: string;
  }> = [
    {
      key: "normalized_nav",
      label: "净值走势",
      title: "归一化净值",
      ariaLabel: "永久组合净值对比图",
    },
    {
      key: "drawdown",
      label: "回撤路径",
      title: "回撤",
      ariaLabel: "永久组合回撤图",
    },
    {
      key: "volatility_63d",
      label: "63日波动",
      title: "63 日滚动波动",
      ariaLabel: "永久组合滚动波动图",
    },
  ];
  const selectedMetric = metricOptions.find((item) => item.key === metric)
    ?? metricOptions[0];
  const fullRange = startDate === minimumDate && endDate === maximumDate;
  const threeYearStart = [minimumDate, subtractYears(maximumDate, 3)].sort()[1];
  const oneYearStart = [minimumDate, subtractYears(maximumDate, 1)].sort()[1];

  return (
    <div className="permanent-chart-region">
      <div className="permanent-chart-controls">
        <div className="permanent-chart-metrics" aria-label="图表指标">
          {metricOptions.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={metric === option.key}
              onClick={() => setMetric(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="permanent-chart-ranges" aria-label="图表时间范围">
          <button
            type="button"
            aria-pressed={fullRange}
            onClick={() => selectRange(null)}
          >
            全部
          </button>
          <button
            type="button"
            aria-pressed={startDate === threeYearStart && endDate === maximumDate}
            onClick={() => selectRange(3)}
          >
            近3年
          </button>
          <button
            type="button"
            aria-pressed={startDate === oneYearStart && endDate === maximumDate}
            onClick={() => selectRange(1)}
          >
            近1年
          </button>
          {benchmarks.length > 0 && metric === "normalized_nav" ? (
            <button
              type="button"
              className="permanent-benchmark-toggle"
              aria-pressed={showBenchmarks}
              onClick={() => setShowBenchmarks((value) => !value)}
            >
              {showBenchmarks ? "隐藏基准" : "显示基准"}
            </button>
          ) : null}
        </div>
        <output className="permanent-chart-range" aria-live="polite">
          {visibleStart} 至 {visibleEnd}
        </output>
      </div>
      <SeriesChart
        title={selectedMetric.title}
        ariaLabel={selectedMetric.ariaLabel}
        metric={metric}
        fixed={fixed}
        dynamic={dynamic}
        benchmarks={visibleBenchmarks}
        trades={visibleTrades}
        stageBoundary={stageBoundary}
        isPanning={isPanning}
        onWheelWindow={handleWheel}
        onPointerDownWindow={handlePointerDown}
        onPointerMoveWindow={handlePointerMove}
        onPointerUpWindow={handlePointerUp}
        onResetWindow={resetWindow}
      />
    </div>
  );
}
