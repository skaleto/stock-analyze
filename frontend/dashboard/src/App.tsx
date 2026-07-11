import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowDownUp,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  Clock3,
  ExternalLink,
  FileText,
  Gauge,
  Layers3,
  RefreshCcw,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  X,
} from "lucide-react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { fetchDetail, fetchSummary } from "./api";
import type { DashboardDetail, DashboardSummary, MarketSummary, OrderRow, SummaryAgent } from "./types";

const preferredMarket = "cn_qdii_etf";
const preferredAgent = "codex";

function formatNumber(value: unknown, digits = 2): string {
  if (value === null || value === undefined || value === "") return "-";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (Math.abs(number) >= 1000000) return `${(number / 1000000).toFixed(2)}M`;
  if (Math.abs(number) >= 1000) return `${(number / 1000).toFixed(1)}K`;
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function formatPercent(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${(number * 100).toFixed(2)}%`;
}

function chooseDefault(summary: DashboardSummary): { market: string; agent: string } {
  const preferred = summary.markets
    .find((market) => market.market === preferredMarket)
    ?.agents.find((agent) => agent.agent === preferredAgent);
  if (preferred) return { market: preferredMarket, agent: preferredAgent };
  const firstMarket = summary.markets.find((market) => market.agents.length > 0);
  return {
    market: firstMarket?.market ?? preferredMarket,
    agent: firstMarket?.agents[0]?.agent ?? preferredAgent,
  };
}

function agentFromSummary(summary: DashboardSummary | null, marketId: string, agentId: string): SummaryAgent | null {
  return summary?.markets.find((market) => market.market === marketId)?.agents.find((agent) => agent.agent === agentId) ?? null;
}

function marketFromSummary(summary: DashboardSummary | null, marketId: string): MarketSummary | null {
  return summary?.markets.find((market) => market.market === marketId) ?? null;
}

function statusTone(status?: string): "ok" | "warn" | "muted" {
  if (status === "success") return "ok";
  if (status === "failed") return "warn";
  return "muted";
}

function StatusBadge({ status }: { status?: string }) {
  const tone = statusTone(status);
  const Icon = tone === "ok" ? CheckCircle2 : tone === "warn" ? ShieldAlert : Clock3;
  return (
    <span className={`status status-${tone}`}>
      <Icon size={14} aria-hidden="true" />
      {status || "missing"}
    </span>
  );
}

function MetricTile({
  label,
  value,
  helper,
  icon: Icon,
  tone = "neutral",
}: {
  label: string;
  value: string;
  helper?: string;
  icon: typeof Activity;
  tone?: "neutral" | "positive" | "warning";
}) {
  return (
    <section className={`metric-tile metric-${tone}`}>
      <div className="metric-icon">
        <Icon size={18} aria-hidden="true" />
      </div>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        {helper ? <span>{helper}</span> : null}
      </div>
    </section>
  );
}

function Sparkline({ points }: { points: { date: string; total_value?: number | null }[] }) {
  const values = points.map((point) => point.total_value).filter((value): value is number => typeof value === "number");
  if (values.length < 2) {
    return (
      <div className="sparkline-empty">
        <BarChart3 size={28} aria-hidden="true" />
        <span>净值序列不足</span>
      </div>
    );
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 760;
  const height = 180;
  const coords = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 28) - 14;
    return [x, y] as const;
  });
  const path = coords.map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const area = `${path} L ${width} ${height} L 0 ${height} Z`;
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="净值曲线">
        <path className="chart-area" d={area} />
        <path className="chart-line" d={path} />
        {coords.map(([x, y], index) => (
          <circle key={`${x}-${index}`} className="chart-dot" cx={x} cy={y} r={index === coords.length - 1 ? 4 : 2.4} />
        ))}
      </svg>
      <div className="chart-axis">
        <span>{points[0]?.date}</span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
    </div>
  );
}

const columnHelper = createColumnHelper<OrderRow>();

function DataTable({
  title,
  rows,
  columns,
  empty,
  onSelect,
  search,
  detailTitle,
}: {
  title: string;
  rows: OrderRow[];
  columns: { key: string; label: string; numeric?: boolean; percent?: boolean }[];
  empty: string;
  onSelect: (row: OrderRow, title: string, trigger: HTMLElement) => void;
  search?: string;
  detailTitle?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const tableColumns = useMemo(
    () =>
      columns.map((column) =>
        columnHelper.accessor((row) => row[column.key], {
          id: column.key,
          header: column.label,
          cell: (info) => {
            const value = info.getValue();
            if (column.percent) return formatPercent(value);
            if (column.numeric) return formatNumber(value);
            return value === null || value === undefined || value === "" ? "-" : String(value);
          },
        })
      ),
    [columns]
  );
  const table = useReactTable({
    data: rows,
    columns: tableColumns,
    state: {
      sorting,
      globalFilter: search ?? "",
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  return (
    <section className="table-panel" aria-label={title} role="region">
      <div className="panel-title">
        <h2>{title}</h2>
        <span>{table.getRowModel().rows.length} rows</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id}>
                    <button type="button" onClick={header.column.getToggleSortingHandler()} aria-label={`排序 ${String(header.column.columnDef.header)}`}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      <ArrowDownUp size={13} aria-hidden="true" />
                    </button>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td className="empty-cell" colSpan={columns.length}>
                  {empty}
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={(event) => onSelect(row.original, detailTitle ?? title, event.currentTarget)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect(row.original, detailTitle ?? title, event.currentTarget);
                    }
                  }}
                  tabIndex={0}
                  aria-haspopup="dialog"
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function DetailDrawer({ row, title, onClose }: { row: OrderRow | null; title: string; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!row) return undefined;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const focusable = Array.from(
          drawerRef.current?.querySelectorAll<HTMLElement>(
            'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'
          ) ?? []
        );
        if (focusable.length === 0) {
          event.preventDefault();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [row, onClose]);

  if (!row) return null;
  return (
    <aside ref={drawerRef} className="drawer drawer-open" role="dialog" aria-modal="true" aria-label={`${title}明细`}>
      <div className="drawer-head">
        <div>
          <p>明细</p>
          <strong>{row.code || row.run_id || row.name || "未选择"}</strong>
        </div>
        <button ref={closeButtonRef} className="icon-button" type="button" onClick={onClose} aria-label="关闭明细">
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <dl>
        {Object.entries(row).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{value === null || value === undefined || value === "" ? "-" : String(value)}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  const lines = markdown.split("\n").filter(Boolean).slice(0, 16);
  if (lines.length === 0) return <p className="muted-copy">暂无周报。</p>;
  return (
    <div className="markdown-preview">
      {lines.map((line, index) => {
        if (line.startsWith("#")) {
          return <strong key={`${line}-${index}`}>{line.replace(/^#+\s*/, "")}</strong>;
        }
        return <p key={`${line}-${index}`}>{line}</p>;
      })}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="skeleton-grid" aria-label="加载中">
      {Array.from({ length: 7 }).map((_, index) => (
        <div key={index} />
      ))}
    </div>
  );
}

export default function App() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [detail, setDetail] = useState<DashboardDetail | null>(null);
  const [selectedMarket, setSelectedMarket] = useState(preferredMarket);
  const [selectedAgent, setSelectedAgent] = useState(preferredAgent);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedRow, setSelectedRow] = useState<OrderRow | null>(null);
  const [selectedRowTitle, setSelectedRowTitle] = useState("明细");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const selectionRef = useRef({ market: selectedMarket, agent: selectedAgent });
  const summaryAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const summaryRequestIdRef = useRef(0);
  const detailRequestIdRef = useRef(0);
  const drawerTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    selectionRef.current = { market: selectedMarket, agent: selectedAgent };
  }, [selectedMarket, selectedAgent]);

  const loadSummary = useCallback(async () => {
    summaryAbortRef.current?.abort();
    const controller = new AbortController();
    summaryAbortRef.current = controller;
    const requestId = ++summaryRequestIdRef.current;
    const payload = await fetchSummary(controller.signal);
    if (requestId !== summaryRequestIdRef.current) return;
    setSummary(payload);
    setSummaryError(null);
    const current = selectionRef.current;
    const currentMarket = payload.markets.find((market) => market.market === current.market);
    const currentAgent = currentMarket?.agents.find((agent) => agent.agent === current.agent);
    if (!currentMarket || !currentAgent) {
      const next = chooseDefault(payload);
      setSelectedMarket(next.market);
      setSelectedAgent(next.agent);
    }
  }, []);

  const loadDetail = useCallback(async (market: string, agent: string) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    const requestId = ++detailRequestIdRef.current;
    setDetailLoading(true);
    setDetail((current) => (
      current?.market === market && current?.agent === agent ? current : null
    ));
    try {
      const payload = await fetchDetail(market, agent, controller.signal);
      if (requestId !== detailRequestIdRef.current) return;
      setDetail(payload);
      setDetailError(null);
    } catch (err) {
      if (requestId !== detailRequestIdRef.current) return;
      if (err instanceof Error && err.name === "AbortError") return;
      setDetailError(err instanceof Error ? err.message : String(err));
    } finally {
      if (requestId === detailRequestIdRef.current) setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    setSummaryError(null);
    loadSummary()
      .catch((err: Error) => {
        if (err.name !== "AbortError") setSummaryError(err.message);
      })
      .finally(() => setLoading(false));
  }, [loadSummary]);

  useEffect(() => {
    void loadDetail(selectedMarket, selectedAgent);
  }, [loadDetail, selectedMarket, selectedAgent]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => {
      loadSummary().catch((err: Error) => {
        if (err.name !== "AbortError") setSummaryError(err.message);
      });
      void loadDetail(selectedMarket, selectedAgent);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loadDetail, loadSummary, selectedMarket, selectedAgent]);

  useEffect(() => () => {
    summaryAbortRef.current?.abort();
    detailAbortRef.current?.abort();
  }, []);

  const selectedMarketSummary = marketFromSummary(summary, selectedMarket);
  const selectedAgentSummary = agentFromSummary(summary, selectedMarket, selectedAgent);
  const markets = summary?.markets ?? [];
  const agentOptions = selectedMarketSummary?.agents ?? [];
  const activeDetail = detail?.market === selectedMarket && detail?.agent === selectedAgent ? detail : null;
  const error = detailError ?? summaryError;

  const refresh = async () => {
    setLoading(true);
    setSummaryError(null);
    setDetailError(null);
    try {
      await Promise.all([
        loadSummary(),
        loadDetail(selectedMarket, selectedAgent),
      ]);
    } catch (err) {
      if (!(err instanceof Error) || err.name !== "AbortError") {
        setSummaryError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  };

  const changeSelection = (market: string, agent: string) => {
    detailAbortRef.current?.abort();
    detailRequestIdRef.current += 1;
    setDetail(null);
    setSelectedRow(null);
    setDetailError(null);
    setSelectedMarket(market);
    setSelectedAgent(agent);
  };

  const openDrawer = (row: OrderRow, title: string, trigger: HTMLElement) => {
    drawerTriggerRef.current = trigger;
    setSelectedRowTitle(title);
    setSelectedRow(row);
  };

  const closeDrawer = useCallback(() => {
    setSelectedRow(null);
    drawerTriggerRef.current?.focus();
    drawerTriggerRef.current = null;
  }, []);

  const orders = activeDetail?.orders.rows ?? [];
  const positions = activeDetail?.positions.rows ?? [];
  const trades = activeDetail?.trades.rows ?? [];
  const runs = activeDetail?.runs.rows ?? [];

  return (
    <main className="app-shell">
      <aside className="left-rail">
        <div className="brand-lockup">
          <span><Gauge size={18} aria-hidden="true" /></span>
          <div>
            <strong>Stock Analyze</strong>
            <p>QDII ETF Workbench</p>
          </div>
        </div>

        <div className="control-group">
          <label>市场</label>
          <div className="segmented">
            {markets.map((market) => (
              <button
                key={market.market}
                type="button"
                className={market.market === selectedMarket ? "active" : ""}
                onClick={() => changeSelection(
                  market.market,
                  market.agents[0]?.agent ?? preferredAgent,
                )}
              >
                {market.label}
              </button>
            ))}
          </div>
        </div>

        <div className="control-group">
          <label>Agent</label>
          <div className="segmented">
            {agentOptions.map((agent) => (
              <button
                key={agent.agent}
                type="button"
                className={agent.agent === selectedAgent ? "active" : ""}
                onClick={() => changeSelection(selectedMarket, agent.agent)}
              >
                {agent.agent}
              </button>
            ))}
          </div>
        </div>

        <div className="status-stack">
          <div>
            <span>日任务</span>
            <StatusBadge status={selectedAgentSummary?.tasks.daily.status} />
          </div>
          <div>
            <span>周任务</span>
            <StatusBadge status={selectedAgentSummary?.tasks.weekly.status} />
          </div>
        </div>

        <button className="ghost-button" type="button" onClick={() => setAutoRefresh((value) => !value)}>
          <Activity size={16} aria-hidden="true" />
          {autoRefresh ? "自动刷新开" : "自动刷新关"}
        </button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p>{selectedMarketSummary?.label ?? activeDetail?.market_label ?? selectedMarket}</p>
            <h1>{selectedAgent} 策略工作台</h1>
          </div>
          <div className="topbar-actions">
            <div className="search-box">
              <Search size={16} aria-hidden="true" />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索代码、账户、原因" aria-label="搜索表格" />
            </div>
            <button className="icon-text-button" type="button" onClick={refresh} aria-label="刷新 dashboard">
              <RefreshCcw size={16} aria-hidden="true" />
              刷新
            </button>
          </div>
        </header>

        {error ? (
          <div className="error-banner">
            <ShieldAlert size={18} aria-hidden="true" />
            {error}
          </div>
        ) : null}

        {loading && !summary ? <Skeleton /> : null}

        <section className="metric-strip">
          <MetricTile
            label="净值"
            value={activeDetail?.nav.latest?.total_value_display ?? selectedAgentSummary?.nav.latest_display ?? "-"}
            helper={activeDetail?.nav.latest?.date ?? selectedAgentSummary?.nav.date ?? "无日期"}
            icon={BarChart3}
          />
          <MetricTile
            label="累计收益"
            value={activeDetail?.nav.latest?.return_display ?? selectedAgentSummary?.nav.return_display ?? "-"}
            helper={activeDetail?.nav.latest?.benchmark_codes?.length
              ? `基准 ${activeDetail.nav.latest.benchmark_codes.join(" / ")}`
              : activeDetail?.nav.latest?.benchmark_code
                ? `基准 ${activeDetail.nav.latest.benchmark_code}`
                : "等待基准"}
            icon={Gauge}
            tone={(activeDetail?.nav.latest?.return ?? selectedAgentSummary?.nav.return ?? 0) >= 0 ? "positive" : "warning"}
          />
          <MetricTile
            label="目标订单"
            value={String(activeDetail?.orders.summary.total ?? selectedAgentSummary?.decision.pending_orders.total ?? 0)}
            helper={`买 ${activeDetail?.orders.summary.buy ?? 0} / 卖 ${activeDetail?.orders.summary.sell ?? 0}`}
            icon={Layers3}
          />
          <MetricTile
            label="最近运行"
            value={runs[0]?.command ? String(runs[0].command) : selectedAgentSummary?.tasks.weekly.status ?? "-"}
            helper={runs[0]?.started_at ? String(runs[0].started_at).slice(0, 19).replace("T", " ") : "等待 run ledger"}
            icon={Clock3}
          />
        </section>

        <section className="chart-section">
          <div className="panel-title">
            <h2>净值轨迹</h2>
            <span>{detailLoading ? "更新中" : `更新 ${activeDetail?.generated_at ?? summary?.generated_at ?? "-"}`}</span>
          </div>
          <Sparkline points={activeDetail?.nav.series ?? []} />
        </section>

        <section className="two-column">
          <DataTable
            title="目标订单"
            rows={orders}
            search={search}
            empty="当前没有 pending order。"
            onSelect={openDrawer}
            detailTitle="订单"
            columns={[
              { key: "code", label: "代码" },
              { key: "name", label: "名称" },
              { key: "side", label: "方向" },
              { key: "shares", label: "股数", numeric: true },
              { key: "target_value", label: "目标金额", numeric: true },
              { key: "score", label: "分数", numeric: true },
              { key: "execute_after", label: "执行日" },
            ]}
          />
          <section className="report-panel">
            <div className="panel-title">
              <h2>周报摘录</h2>
              {activeDetail?.weekly_report.href ? (
                <a href={activeDetail.weekly_report.href} target="_blank" rel="noreferrer">
                  <ExternalLink size={14} aria-hidden="true" />
                  打开
                </a>
              ) : null}
            </div>
            <MarkdownPreview markdown={activeDetail?.weekly_report.markdown ?? ""} />
          </section>
        </section>

        <section className="tab-band">
          <details open>
            <summary>
              <SlidersHorizontal size={16} aria-hidden="true" />
              持仓
              <ChevronDown size={16} aria-hidden="true" />
            </summary>
            <DataTable
              title="持仓明细"
              rows={positions}
              search={search}
              empty="暂无持仓。周一订单执行后这里会出现仓位。"
              onSelect={openDrawer}
              detailTitle="持仓"
              columns={[
                { key: "code", label: "代码" },
                { key: "name", label: "名称" },
                { key: "industry", label: "暴露" },
                { key: "shares", label: "股数", numeric: true },
                { key: "last_price", label: "价格", numeric: true },
                { key: "market_value", label: "市值", numeric: true },
                { key: "unrealized_pnl", label: "浮盈亏", numeric: true },
              ]}
            />
          </details>
          <details>
            <summary>
              <FileText size={16} aria-hidden="true" />
              成交
              <ChevronDown size={16} aria-hidden="true" />
            </summary>
            <DataTable
              title="成交记录"
              rows={trades}
              search={search}
              empty="暂无成交。"
              onSelect={openDrawer}
              detailTitle="成交"
              columns={[
                { key: "trade_date", label: "日期" },
                { key: "code", label: "代码" },
                { key: "side", label: "方向" },
                { key: "shares", label: "股数", numeric: true },
                { key: "price", label: "价格", numeric: true },
                { key: "net_amount", label: "净额", numeric: true },
                { key: "reason", label: "原因" },
              ]}
            />
          </details>
          <details>
            <summary>
              <Clock3 size={16} aria-hidden="true" />
              Run Ledger
              <ChevronDown size={16} aria-hidden="true" />
            </summary>
            <DataTable
              title="运行记录"
              rows={runs}
              search={search}
              empty="暂无运行记录。"
              onSelect={openDrawer}
              detailTitle="运行"
              columns={[
                { key: "command", label: "命令" },
                { key: "status", label: "状态" },
                { key: "as_of", label: "日期" },
                { key: "duration_ms", label: "耗时 ms", numeric: true },
                { key: "started_at", label: "开始" },
                { key: "run_id", label: "run_id" },
              ]}
            />
          </details>
        </section>
      </section>

      <DetailDrawer row={selectedRow} title={selectedRowTitle} onClose={closeDrawer} />
    </main>
  );
}
