# K 线与 MACD 联动窗格 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 K 线和 MACD 放入同一个 `lightweight-charts` 实例的上下 pane，共享时间轴、缩放、拖动和十字光标读数。

**Architecture:** `CandlestickChart` 继续拥有唯一图表实例，价格、均线和成交量位于 pane 0，MACD 柱、DIF 和 DEA 位于 pane 1。十字光标回调以日期同时查找 `Candle` 和 `MacdPoint`，一次更新价格与指标读数。

**Tech Stack:** React 18, TypeScript, lightweight-charts 5.2, Vitest, Testing Library, Vite

---

## 文件结构

- Modify: `frontend/dashboard/src/FinancialCharts.tsx` - 合并图表实例、创建 MACD pane、统一 hover 读数。
- Modify: `frontend/dashboard/src/FinancialCharts.test.tsx` - 模拟 pane API 并验证单实例、pane 索引、时间范围和同日读数。
- Modify: `frontend/dashboard/src/styles.css` - 调整联动图高度与紧凑指标读数布局，删除独立 MACD canvas 样式。

### Task 1: 用失败测试锁定联动合约

**Files:**
- Modify: `frontend/dashboard/src/FinancialCharts.test.tsx`
- Test: `frontend/dashboard/src/FinancialCharts.test.tsx`

- [ ] **Step 1: 扩展图表模拟 API**

在 `chartMocks` 中增加 pane 与可见区间 API，并让 `timeScale()` 返回同一个可断言对象：

```tsx
const setStretchFactor = vi.fn();
const subscribeVisibleTimeRangeChange = vi.fn();
const unsubscribeVisibleTimeRangeChange = vi.fn();
const timeScaleApi = {
  fitContent,
  setVisibleRange,
  subscribeVisibleTimeRangeChange,
  unsubscribeVisibleTimeRangeChange,
};
const panes = vi.fn(() => [
  { setStretchFactor: vi.fn() },
  { setStretchFactor },
]);
const createChart = vi.fn(() => ({
  addSeries,
  remove,
  subscribeCrosshairMove,
  timeScale: () => timeScaleApi,
  panes,
  applyOptions: vi.fn(),
}));
```

- [ ] **Step 2: 添加单图表双 pane 失败测试**

渲染带有三根 K 线的 `CandlestickChart`，断言只创建一个 chart，并且 MACD 的三个 series 通过 `addSeries(..., ..., 1)` 进入 pane 1：

```tsx
expect(chartMocks.createChart).toHaveBeenCalledTimes(1);
const macdPaneCalls = chartMocks.addSeries.mock.calls.filter(([, , paneIndex]) => paneIndex === 1);
expect(macdPaneCalls).toHaveLength(3);
expect(chartMocks.panes).toHaveBeenCalled();
expect(chartMocks.setStretchFactor).toHaveBeenCalled();
expect(screen.getByLabelText("日K线、成交量与MACD联动图")).toBeInTheDocument();
```

- [ ] **Step 3: 添加同日 hover 读数失败测试**

触发图表唯一的 crosshair 回调并定位第一个交易日，验证 OHLC 和 MACD 读数同时切换：

```tsx
act(() => {
  chartMocks.subscribeCrosshairMove.mock.calls[0]?.[0]({ time: "2026-07-10" });
});
expect(screen.getByText("2026-07-10")).toBeInTheDocument();
expect(screen.getByText((_, node) => node?.textContent === "DIF 0.0000")).toBeInTheDocument();
expect(screen.getByText((_, node) => node?.textContent === "DEA 0.0000")).toBeInTheDocument();
expect(screen.getByText((_, node) => node?.textContent === "MACD柱 0.0000")).toBeInTheDocument();
```

- [ ] **Step 4: 运行定向测试并确认正确失败**

Run:

```bash
cd frontend/dashboard
npm test -- FinancialCharts.test.tsx
```

Expected: FAIL，失败原因是当前仍创建独立 `MacdPanel` chart，而且 MACD series 没有 pane index 1。

### Task 2: 实现原生 pane 与统一读数

**Files:**
- Modify: `frontend/dashboard/src/FinancialCharts.tsx:468-760`
- Modify: `frontend/dashboard/src/styles.css:838-842`
- Test: `frontend/dashboard/src/FinancialCharts.test.tsx`

- [ ] **Step 1: 删除独立 `MacdPanel` chart**

删除 `MacdPanel` 中的 `createChart` 和 `.macd-canvas`，在 `CandlestickChart` 中增加当前 MACD 点状态：

```tsx
const [hoveredMacd, setHoveredMacd] = useState<MacdPoint | null>(macdPoints[macdPoints.length - 1] ?? null);

useEffect(() => {
  setHovered(candles[candles.length - 1] ?? null);
  setHoveredMacd(macdPoints[macdPoints.length - 1] ?? null);
}, [candles, macdPoints]);
```

- [ ] **Step 2: 在主 chart 的 pane 1 创建 MACD series**

仅在 `visibleIndicators.macd` 为 true 时添加三个 series：

```tsx
if (visibleIndicators.macd) {
  const histogram = chart.addSeries(HistogramSeries, {
    priceLineVisible: false,
    lastValueVisible: false,
    priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
  }, 1);
  const dif = chart.addSeries(LineSeries, {
    color: "#facc15",
    lineWidth: 1,
    title: "DIF",
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  }, 1);
  const dea = chart.addSeries(LineSeries, {
    color: "#60a5fa",
    lineWidth: 1,
    title: "DEA",
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  }, 1);
  histogram.setData(macdPoints.map((point) => ({
    time: point.date as Time,
    value: point.histogram,
    color: point.histogram >= 0 ? "rgba(239,68,68,0.58)" : "rgba(34,197,94,0.58)",
  })));
  dif.setData(macdPoints.map((point) => ({ time: point.date as Time, value: point.dif })));
  dea.setData(macdPoints.map((point) => ({ time: point.date as Time, value: point.dea })));
  chart.panes()[0]?.setStretchFactor(3);
  chart.panes()[1]?.setStretchFactor(1.15);
}
```

- [ ] **Step 3: 让时间范围仅作用于唯一 chart**

使用 `chartRef` 和 `lastVisibleRangeRef` 保存用户拖动后的可见区间。时间按钮变化时只调用唯一 time scale：

```tsx
const chartRef = useRef<IChartApi | null>(null);
const lastVisibleRangeRef = useRef<{ from: Time; to: Time } | null>(null);

const rememberVisibleRange = (nextRange: { from: Time; to: Time } | null) => {
  if (nextRange) lastVisibleRangeRef.current = nextRange;
};
chart.timeScale().subscribeVisibleTimeRangeChange(rememberVisibleRange);

useEffect(() => {
  const chart = chartRef.current;
  if (!chart) return;
  if (visibleRange) chart.timeScale().setVisibleRange(visibleRange);
  else chart.timeScale().fitContent();
}, [visibleRange]);
```

在 chart 因指标开关重建时，优先恢复 `lastVisibleRangeRef.current`；在证券 K 线数据键变化时清空该 ref，并使用当前预设范围。清理阶段取消 time-range 订阅。

- [ ] **Step 4: 统一 crosshair 回调与读数**

增加 `macdByDate`，在已有 callback 中同时更新两类数据：

```tsx
const macdByDate = new Map(macdPoints.map((point) => [point.date, point]));
chart.subscribeCrosshairMove((parameter) => {
  const date = typeof parameter.time === "string" ? parameter.time : null;
  setHovered((date && candleByDate.get(date)) || candles[candles.length - 1] || null);
  setHoveredMacd((date && macdByDate.get(date)) || macdPoints[macdPoints.length - 1] || null);
  const objectId = parameter.hoveredInfo?.objectId ?? parameter.hoveredObjectId;
  setHoveredTrade(typeof objectId === "string" ? markerBundle.details.get(objectId) ?? null : null);
});
```

将 MACD 读数放到统一 chart 上方：

```tsx
{visibleIndicators.macd ? (
  <div className="macd-readout" aria-live="polite">
    <strong>MACD (12, 26, 9)</strong>
    <span>DIF <b>{hoveredMacd?.dif.toFixed(4) ?? "-"}</b></span>
    <span>DEA <b>{hoveredMacd?.dea.toFixed(4) ?? "-"}</b></span>
    <span>MACD柱 <b>{hoveredMacd?.histogram.toFixed(4) ?? "-"}</b></span>
  </div>
) : null}
<div
  ref={containerRef}
  className={`chart-canvas candle-canvas${visibleIndicators.macd ? " with-macd" : ""}`}
  aria-label={visibleIndicators.macd ? "日K线、成交量与MACD联动图" : "日K线和成交量图"}
/>
```

- [ ] **Step 5: 更新样式**

删除 `.macd-panel` 和 `.macd-canvas`，改为联动图高度：

```css
.candle-canvas { height: 340px; }
.candle-canvas.with-macd { height: 502px; }
.macd-readout {
  min-height: 28px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  color: var(--muted);
  font-family: "SF Mono", "JetBrains Mono", Menlo, monospace;
  font-size: 10px;
}
```

同时将 `createChart` 的 `height` 设为 `visibleIndicators.macd ? 502 : 340`。

- [ ] **Step 6: 运行定向测试并修正类型错误**

Run:

```bash
cd frontend/dashboard
npm test -- FinancialCharts.test.tsx
npm run build
```

Expected: `FinancialCharts.test.tsx` 全部 PASS，TypeScript 和 Vite build 退出码为 0。

### Task 3: 回归与真实页面验证

**Files:**
- Verify: `frontend/dashboard/src/FinancialCharts.tsx`
- Verify: `frontend/dashboard/src/styles.css`

- [ ] **Step 1: 运行完整前端测试**

Run:

```bash
cd frontend/dashboard
npm test
npm run build
```

Expected: Vitest 0 failed，TypeScript/Vite build 退出码 0。

- [ ] **Step 2: 启动本地 Dashboard**

Run:

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 18767
```

Expected: `http://127.0.0.1:18767/app.html` 可访问，启动命令保持运行直到浏览器验证结束。

- [ ] **Step 3: 在桌面端验证联动**

使用 1440x900 视口打开任一有历史行情的证券详情，验证：

```text
1. 页面中只有一个联动图表 canvas 容器。
2. K 线与 MACD 的纵向网格与日期一致。
3. 拖动或滚轮缩放后上下窗格同时变化。
4. 十字光标跨两个窗格，OHLC 与 DIF/DEA/MACD柱读数为同一日。
5. 关闭再打开 MACD 后仍保留当前时间区间。
6. 浏览器控制台无新错误。
```

- [ ] **Step 4: 在移动端验证布局**

使用 390x844 视口验证上下 pane 未横向溢出，MACD 读数可换行，时间范围按钮、指标开关和证券抽屉仍可操作。

- [ ] **Step 5: 检查最终差异**

Run:

```bash
git diff --check
git diff -- frontend/dashboard/src/FinancialCharts.tsx frontend/dashboard/src/FinancialCharts.test.tsx frontend/dashboard/src/styles.css
```

Expected: `git diff --check` 无输出；差异只包含本规格需要的 pane 联动、读数、测试和样式改动，并保留这些文件中原有的未提交内容。
