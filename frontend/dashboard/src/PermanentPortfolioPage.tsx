import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  ChevronDown,
  Database,
  ShieldCheck,
  WalletCards,
} from "lucide-react";
import { fetchPermanentPortfolio } from "./api";
import { PermanentPortfolioCharts } from "./PermanentPortfolioCharts";
import type {
  PermanentPortfolioData,
  PermanentPortfolioMetricSet,
  PermanentPortfolioResult,
} from "./types";

type Stage = "historical" | "forward";
type StrategyId = "fixed" | "dynamic";

const stageLabels: Record<Stage, string> = {
  historical: "历史数据回测",
  forward: "前向纸面期",
};

const portfolioLabels: Record<string, string> = {
  fixed: "固定永久组合",
  dynamic: "动态永久组合",
  equity_buy_hold: "沪深300买入持有",
  equal_weight_buy_hold: "四资产等权买入持有",
  cash_buy_hold: "现金ETF买入持有",
};

const roleLabels: Record<string, string> = {
  equity: "股票",
  bond: "长期国债",
  cash: "现金ETF",
  gold: "黄金",
};

const strategyCopy: Record<StrategyId, {
  eyebrow: string;
  summary: string;
  cadence: string;
}> = {
  fixed: {
    eyebrow: "防守核心",
    summary: "四类资产长期均衡，仅在权重越过 15% / 35% 阈值时再平衡。",
    cadence: "每日收盘检查阈值",
  },
  dynamic: {
    eyebrow: "趋势增强",
    summary: "保留四资产分散基础，按月用双周期动量把权重倾斜至强势资产。",
    cadence: "每月末检查动量排名",
  },
};

function percent(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(2)}%`
    : "—";
}

function ratio(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "—";
}

function money(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `¥${value.toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`
    : "—";
}

function quantity(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-CN")
    : "—";
}

function formatDate(value: string | null | undefined): string {
  const compact = String(value ?? "").split("-").join("").slice(0, 8);
  return /^\d{8}$/.test(compact)
    ? `${compact.slice(0, 4)}-${compact.slice(4, 6)}-${compact.slice(6, 8)}`
    : value || "—";
}

function latestCompleteStage(data: PermanentPortfolioData): Stage {
  return ["complete", "available"].includes(data.windows.forward.status)
    ? "forward"
    : "historical";
}

function latestNav(portfolio: PermanentPortfolioResult | undefined) {
  const values = portfolio?.nav ?? [];
  return values[values.length - 1];
}

function latestTargets(portfolio: PermanentPortfolioResult | undefined) {
  const signalDate = (portfolio?.targets ?? []).reduce(
    (latest, target) => String(target.signal_date ?? "") > latest
      ? String(target.signal_date)
      : latest,
    "",
  );
  return {
    signalDate,
    values: new Map(
      (portfolio?.targets ?? [])
        .filter((target) => String(target.signal_date ?? "") === signalDate)
        .map((target) => [target.role ?? "", target.target_weight]),
    ),
  };
}

function MetricsTable({ portfolios }: {
  portfolios: Record<string, PermanentPortfolioResult>;
}) {
  return (
    <div className="permanent-table-wrap">
      <table className="permanent-table" aria-label="完整策略指标对比">
        <thead>
          <tr>
            <th>组合</th>
            <th>累计收益</th>
            <th>年化收益</th>
            <th>年化波动</th>
            <th>最大回撤</th>
            <th>现金超额 Sharpe</th>
            <th>Calmar</th>
            <th>年化换手</th>
            <th>累计成本</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(portfolios).map(([key, portfolio]) => {
            const metrics = portfolio.metrics ?? {};
            return (
              <tr key={key} className={key === "fixed" ? "is-primary" : ""}>
                <th>{portfolioLabels[key] ?? key}</th>
                <td>{percent(metrics.cumulative_return)}</td>
                <td>{percent(metrics.annualized_return)}</td>
                <td>{percent(metrics.annualized_volatility)}</td>
                <td>{percent(metrics.max_drawdown)}</td>
                <td>{ratio(metrics.sharpe_vs_cash)}</td>
                <td>{ratio(metrics.calmar)}</td>
                <td>{percent(metrics.annualized_turnover)}</td>
                <td>{money(metrics.total_cost)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StrategyHero({
  strategy,
  portfolio,
  startDate,
  endDate,
}: {
  strategy: StrategyId;
  portfolio: PermanentPortfolioResult;
  startDate?: string;
  endDate?: string;
}) {
  const metrics: PermanentPortfolioMetricSet = portfolio.metrics ?? {};
  return (
    <section className="permanent-hero">
      <div className="permanent-hero-copy">
        <span className="permanent-kicker">{strategyCopy[strategy].eyebrow}</span>
        <h2>{portfolioLabels[strategy]}</h2>
        <p>{strategyCopy[strategy].summary}</p>
        <div className="permanent-period">
          <CalendarClock size={14} aria-hidden="true" />
          {formatDate(startDate)} — {formatDate(endDate)}
        </div>
      </div>
      <div
        className="permanent-kpi-grid"
        aria-label={`${portfolioLabels[strategy]}核心指标`}
      >
        <article>
          <span>年化收益</span>
          <strong>{percent(metrics.annualized_return)}</strong>
          <small>累计 {percent(metrics.cumulative_return)}</small>
        </article>
        <article>
          <span>最大回撤</span>
          <strong className="risk">{percent(metrics.max_drawdown)}</strong>
          <small>越接近 0 风险越低</small>
        </article>
        <article>
          <span>年化波动</span>
          <strong>{percent(metrics.annualized_volatility)}</strong>
          <small>收益路径稳定度</small>
        </article>
        <article>
          <span>现金超额 Sharpe</span>
          <strong>{ratio(metrics.sharpe_vs_cash)}</strong>
          <small>相对现金ETF</small>
        </article>
      </div>
    </section>
  );
}

function AllocationPanel({
  strategy,
  portfolio,
  assets,
}: {
  strategy: StrategyId;
  portfolio: PermanentPortfolioResult;
  assets: PermanentPortfolioData["assets"];
}) {
  const nav = latestNav(portfolio);
  const targets = latestTargets(portfolio);
  const names = new Map(assets.map((asset) => [asset.code, asset.name]));
  const pending = portfolio.pending ?? [];
  const rows = portfolio.positions ?? [];

  return (
    <section className="permanent-card permanent-allocation-card">
      <div className="permanent-card-heading">
        <div>
          <span>当前账本</span>
          <h3>资产配置与下一步</h3>
        </div>
        <span className={`permanent-action-status ${pending.length ? "pending" : "idle"}`}>
          {pending.length ? "待执行调仓" : "无需调仓"}
        </span>
      </div>
      <div className="permanent-account-strip">
        <div><span>账户总值</span><strong>{money(nav?.total_value)}</strong></div>
        <div><span>可用现金</span><strong>{money(nav?.cash)}</strong></div>
        <div><span>账本日期</span><strong>{formatDate(nav?.date)}</strong></div>
        <div>
          <span>检查节奏</span>
          <strong>{strategyCopy[strategy].cadence}</strong>
        </div>
      </div>
      <div className="permanent-allocation-list">
        {rows.map((position) => {
          const actualWeight = typeof position.market_value === "number"
            && typeof nav?.total_value === "number"
            && nav.total_value > 0
            ? position.market_value / nav.total_value
            : null;
          const targetWeight = targets.values.get(position.role ?? "");
          return (
            <article key={position.code} className="permanent-allocation-row">
              <div className="permanent-asset-identity">
                <span className={`permanent-asset-dot ${position.role ?? ""}`} />
                <div>
                  <strong>{roleLabels[position.role ?? ""] ?? position.role}</strong>
                  <small>{names.get(position.code ?? "") ?? position.code}</small>
                </div>
              </div>
              <div className="permanent-allocation-bar" aria-hidden="true">
                <span style={{ width: percent(actualWeight) }} />
                {typeof targetWeight === "number" ? (
                  <i style={{ left: percent(targetWeight) }} />
                ) : null}
              </div>
              <div className="permanent-weight-pair">
                <strong>{percent(actualWeight)}</strong>
                <span>目标 {percent(targetWeight)}</span>
              </div>
              <div className="permanent-position-detail">
                <strong>{money(position.market_value)}</strong>
                <span><b>{quantity(position.shares)}</b> 份 · {position.code}</span>
              </div>
            </article>
          );
        })}
      </div>
      <p className="permanent-execution-note">
        <ArrowRight size={14} aria-hidden="true" />
        信号在收盘后生成，下一交易日开盘按 100 份整手成交；图中实线为当前权重，刻度为目标权重。
      </p>
    </section>
  );
}

function RecentTrades({
  strategy,
  portfolio,
}: {
  strategy: StrategyId;
  portfolio: PermanentPortfolioResult;
}) {
  const trades = [...(portfolio.trades ?? [])]
    .sort((left, right) => String(right.trade_date ?? "")
      .localeCompare(String(left.trade_date ?? "")))
    .slice(0, 12);
  return (
    <section className="permanent-card permanent-trades-card">
      <div className="permanent-card-heading">
        <div>
          <span>执行记录</span>
          <h3>最近调仓</h3>
        </div>
        <span>仅显示 {portfolioLabels[strategy]}</span>
      </div>
      <div className="permanent-table-wrap">
        <table className="permanent-table permanent-trade-table">
          <thead>
            <tr><th>日期</th><th>资产</th><th>方向</th><th>份额</th><th>成交价</th><th>成本</th></tr>
          </thead>
          <tbody>
            {trades.map((trade, index) => {
              const side = String(trade.side ?? "").toLowerCase();
              return (
                <tr key={`${trade.trade_date}-${trade.code}-${index}`}>
                  <td>{formatDate(trade.trade_date)}</td>
                  <td><strong>{trade.code ?? "—"}</strong><span>{roleLabels[trade.role ?? ""] ?? trade.role}</span></td>
                  <td><span className={`permanent-side ${side}`}>{side === "buy" ? "买入" : "卖出"}</span></td>
                  <td>{quantity(trade.shares)}</td>
                  <td>{money(trade.price)}</td>
                  <td>{money(trade.commission)}</td>
                </tr>
              );
            })}
            {trades.length === 0 ? <tr><td colSpan={6}>暂无调仓记录</td></tr> : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function PermanentPortfolioPage({ refreshToken }: { refreshToken: number }) {
  const [stage, setStage] = useState<Stage>("historical");
  const [strategy, setStrategy] = useState<StrategyId>("fixed");
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [data, setData] = useState<PermanentPortfolioData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    fetchPermanentPortfolio(controller.signal)
      .then((payload) => {
        setData(payload);
        setStage(latestCompleteStage(payload));
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      });
    return () => controller.abort();
  }, [refreshToken]);

  const selected = data?.windows[stage];
  const portfolios = selected?.portfolios ?? {};
  const selectedPortfolio = portfolios[strategy];
  const chartSeries = useMemo(() => ({
    fixed: portfolios.fixed?.series ?? [],
    dynamic: portfolios.dynamic?.series ?? [],
  }), [portfolios]);
  const chartBenchmarks = useMemo(() => (
    (data?.benchmarks ?? []).map((benchmark) => ({
      ...benchmark,
      series: portfolios[benchmark.id]?.series ?? [],
    }))
  ), [data?.benchmarks, portfolios]);
  const chartTrades = useMemo(() => ({
    fixed: portfolios.fixed?.trades ?? [],
    dynamic: portfolios.dynamic?.trades ?? [],
  }), [portfolios]);

  if (error) {
    return (
      <div className="workspace-page permanent-portfolio-page">
        <div className="error-banner"><AlertTriangle size={17} aria-hidden="true" />{error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="workspace-page permanent-portfolio-page skeleton-grid" aria-label="永久组合加载中">
        <div /><div /><div /><div />
      </div>
    );
  }

  return (
    <div className="workspace-page permanent-portfolio-page">
      <header className="permanent-toolbar">
        <div className="permanent-stage-control" aria-label="研究阶段">
          {(Object.keys(stageLabels) as Stage[]).map((value) => (
            <button
              key={value}
              type="button"
              className={stage === value ? "active" : ""}
              aria-pressed={stage === value}
              onClick={() => setStage(value)}
            >
              {stageLabels[value]}
            </button>
          ))}
        </div>
        <div className="permanent-study-status">
          <ShieldCheck size={15} aria-hidden="true" />
          {data.study.status === "holdout_complete" ? "开发与盲测已封存" : data.study.status}
        </div>
      </header>

      {selected?.status === "unavailable" || !selectedPortfolio ? (
        <section className="permanent-sealed">
          <Database size={22} aria-hidden="true" />
          <div><h2>该阶段尚无可用产物</h2><p>最近一次完整运行结束后自动显示。</p></div>
        </section>
      ) : (
        <>
          <nav className="permanent-strategy-switcher" aria-label="组合策略">
            {(["fixed", "dynamic"] as StrategyId[]).map((value) => {
              const metrics = portfolios[value]?.metrics ?? {};
              return (
                <button
                  key={value}
                  type="button"
                  className={strategy === value ? "active" : ""}
                  aria-pressed={strategy === value}
                  aria-label={`查看${portfolioLabels[value]}`}
                  onClick={() => setStrategy(value)}
                >
                  <span>{strategyCopy[value].eyebrow}</span>
                  <strong>{portfolioLabels[value]}</strong>
                  <small>年化 {percent(metrics.annualized_return)} · 回撤 {percent(metrics.max_drawdown)}</small>
                </button>
              );
            })}
          </nav>

          <StrategyHero
            strategy={strategy}
            portfolio={selectedPortfolio}
            startDate={selected?.start_date}
            endDate={selected?.end_date}
          />

          <section className="permanent-card permanent-chart-card">
            <div className="permanent-card-heading">
              <div><span>历史路径</span><h3>收益与风险如何演变</h3></div>
              <span>普通滚动页面 · Ctrl/⌘ + 滚轮缩放 · 拖动平移 · 双击复位</span>
            </div>
            <PermanentPortfolioCharts
              series={chartSeries}
              benchmarks={chartBenchmarks}
              trades={chartTrades}
              stageBoundary={stage === "historical" ? selected?.stage_boundaries?.[0] : undefined}
            />
          </section>

          <div className="permanent-detail-grid">
            <AllocationPanel strategy={strategy} portfolio={selectedPortfolio} assets={data.assets} />
            <RecentTrades strategy={strategy} portfolio={selectedPortfolio} />
          </div>

          <section className="permanent-comparison">
            <button
              type="button"
              className="permanent-comparison-toggle"
              aria-expanded={comparisonOpen}
              aria-label={comparisonOpen ? "收起完整策略对比" : "展开完整策略对比"}
              onClick={() => setComparisonOpen((value) => !value)}
            >
              <span><WalletCards size={16} aria-hidden="true" />完整策略与基准对比</span>
              <span>{comparisonOpen ? "收起" : "展开完整策略对比"}<ChevronDown size={16} aria-hidden="true" /></span>
            </button>
            {comparisonOpen ? <MetricsTable portfolios={portfolios} /> : null}
          </section>
        </>
      )}

      <details className="permanent-evidence">
        <summary>研究证据与校验哈希</summary>
        <div>
          <span>状态<strong>{data.study.status}</strong></span>
          <span>规则<strong>{data.study.contractSha256 ?? "—"}</strong></span>
          <span>开发<strong>{data.study.developmentSha256 ?? "—"}</strong></span>
          <span>盲测<strong>{data.study.holdoutSha256 ?? "—"}</strong></span>
        </div>
      </details>
    </div>
  );
}
