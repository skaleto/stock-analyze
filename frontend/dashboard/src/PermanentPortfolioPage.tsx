import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  Database,
  WalletCards,
} from "lucide-react";
import { fetchPermanentPortfolio } from "./api";
import { PermanentPortfolioCharts } from "./PermanentPortfolioCharts";
import type {
  PermanentPortfolioData,
  PermanentPortfolioMetricSet,
} from "./types";

type Stage = "historical" | "forward";

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

function latestCompleteStage(data: PermanentPortfolioData): Stage {
  if (["complete", "available"].includes(data.windows.forward.status)) {
    return "forward";
  }
  return "historical";
}

function MetricsTable({
  portfolios,
}: {
  portfolios: NonNullable<
    PermanentPortfolioData["windows"]["historical"]["portfolios"]
  >;
}) {
  const rows = Object.entries(portfolios);
  return (
    <div className="permanent-table-wrap">
      <table className="permanent-table">
        <thead>
          <tr>
            <th>组合</th>
            <th>累计收益</th>
            <th>年化收益</th>
            <th>年化波动</th>
            <th>最大回撤</th>
            <th>Sharpe</th>
            <th>Calmar</th>
            <th>换手</th>
            <th>成本</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([key, portfolio]) => {
            const metrics: PermanentPortfolioMetricSet = (
              portfolio.metrics ?? {}
            );
            return (
              <tr key={key}>
                <th>{portfolioLabels[key] ?? key}</th>
                <td>{percent(metrics.cumulative_return)}</td>
                <td>{percent(metrics.annualized_return)}</td>
                <td>{percent(metrics.annualized_volatility)}</td>
                <td>{percent(metrics.max_drawdown)}</td>
                <td>{ratio(metrics.sharpe_vs_cash)}</td>
                <td>{ratio(metrics.calmar)}</td>
                <td>{percent(metrics.annualized_turnover)}</td>
                <td>{typeof metrics.total_cost === "number"
                  ? `¥${metrics.total_cost.toFixed(2)}`
                  : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function HoldingsTable({
  portfolios,
  assets,
}: {
  portfolios: NonNullable<
    PermanentPortfolioData["windows"]["historical"]["portfolios"]
  >;
  assets: PermanentPortfolioData["assets"];
}) {
  const names = new Map(assets.map((asset) => [asset.code, asset.name]));
  const strategies = ["fixed", "dynamic"].flatMap((strategy) => {
    const portfolio = portfolios[strategy];
    const navRows = portfolio?.nav ?? [];
    const nav = navRows[navRows.length - 1];
    if (!portfolio || !nav) return [];
    const latestSignal = (portfolio.targets ?? []).reduce(
      (latest, target) => (
        String(target.signal_date ?? "") > latest ? String(target.signal_date) : latest
      ),
      "",
    );
    const targets = new Map(
      (portfolio.targets ?? [])
        .filter((target) => String(target.signal_date ?? "") === latestSignal)
        .map((target) => [target.role, target.target_weight]),
    );
    return [{
      strategy,
      portfolio,
      nav,
      latestSignal,
      targets,
    }];
  });

  return (
    <>
      <div className="permanent-execution-grid">
        {strategies.map(({ strategy, portfolio, nav, latestSignal }) => {
          const pending = portfolio.pending ?? [];
          return (
            <div className="permanent-execution-row" key={strategy}>
              <div>
                <strong>{portfolioLabels[strategy]}</strong>
                <span>账本日期 {nav.date ?? "—"}</span>
              </div>
              <div>
                <span>账户总值</span>
                <strong>{money(nav.total_value)}</strong>
              </div>
              <div>
                <span>可用现金</span>
                <strong>{money(nav.cash)}</strong>
              </div>
              <div>
                <span>下一动作</span>
                <strong>
                  {pending.length > 0
                    ? "下一交易日 09:30 执行"
                    : strategy === "fixed"
                      ? "每日收盘检查阈值"
                      : "下月末收盘生成信号"}
                </strong>
                {pending.length > 0 ? <span>信号日 {latestSignal}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
      <div className="permanent-table-wrap">
        <table className="permanent-table permanent-holdings-table">
          <thead>
            <tr>
              <th>组合</th>
              <th>资产</th>
              <th>代码</th>
              <th>持有份额</th>
              <th>参考价</th>
              <th>市值</th>
              <th>实际权重</th>
              <th>最新目标</th>
            </tr>
          </thead>
          <tbody>
            {strategies.flatMap(({ strategy, portfolio, nav, targets }) => {
              const rows = (portfolio.positions ?? []).map((position) => {
                const marketValue = position.market_value;
                const actualWeight = (
                  typeof marketValue === "number"
                  && typeof nav.total_value === "number"
                  && nav.total_value > 0
                ) ? marketValue / nav.total_value : null;
                return (
                  <tr key={`${strategy}-${position.code}`}>
                    <th>{portfolioLabels[strategy]}</th>
                    <td>{roleLabels[position.role ?? ""] ?? position.role ?? "—"}</td>
                    <td>
                      <strong>{position.code ?? "—"}</strong>
                      <span className="permanent-instrument-name">
                        {names.get(position.code ?? "") ?? ""}
                      </span>
                    </td>
                    <td>{quantity(position.shares)}</td>
                    <td>{money(position.last_price)}</td>
                    <td>{money(marketValue)}</td>
                    <td>{percent(actualWeight)}</td>
                    <td>{percent(targets.get(position.role))}</td>
                  </tr>
                );
              });
              rows.push(
                <tr key={`${strategy}-account-cash`}>
                  <th>{portfolioLabels[strategy]}</th>
                  <td>账户现金</td>
                  <td>—</td>
                  <td>—</td>
                  <td>—</td>
                  <td>{money(nav.cash)}</td>
                  <td>{percent(
                    typeof nav.cash === "number"
                    && typeof nav.total_value === "number"
                    && nav.total_value > 0
                      ? nav.cash / nav.total_value
                      : null,
                  )}</td>
                  <td>—</td>
                </tr>,
              );
              return rows;
            })}
            {strategies.length === 0 ? (
              <tr><td colSpan={8}>暂无账本持仓</td></tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PermanentPortfolioPage({
  refreshToken,
}: {
  refreshToken: number;
}) {
  const [stage, setStage] = useState<Stage>("historical");
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
  const trades = useMemo<Array<Record<string, unknown> & { portfolio: string }>>(() => (
    Object.entries(portfolios).flatMap(([portfolio, value]) => (
      (value.trades ?? []).map((trade) => ({ portfolio, ...trade }))
    )).slice(-100)
  ), [portfolios]);

  if (error) {
    return (
      <div className="workspace-page permanent-portfolio-page">
        <div className="error-banner">
          <AlertTriangle size={17} aria-hidden="true" />
          {error}
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div
        className="workspace-page permanent-portfolio-page skeleton-grid"
        aria-label="永久组合加载中"
      >
        <div /><div /><div /><div />
      </div>
    );
  }

  return (
    <div className="workspace-page permanent-portfolio-page">
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

      {selected?.status === "unavailable" ? (
        <section className="permanent-sealed">
          <Database size={22} aria-hidden="true" />
          <div>
            <h2>该阶段尚无可用产物</h2>
            <p>最近一次完整运行结束后自动显示。</p>
          </div>
        </section>
      ) : (
        <>
          <section className="permanent-section">
            <div className="section-heading">
              <div>
                <p>模拟执行账本</p>
                <h2>
                  <WalletCards size={18} aria-hidden="true" />
                  {stage === "historical" ? "历史期末持仓与目标" : "当前持仓与目标"}
                </h2>
              </div>
              <span>
                <CalendarClock size={14} aria-hidden="true" />
                信号收盘后生成，下一交易日开盘执行
              </span>
            </div>
            <HoldingsTable portfolios={portfolios} assets={data.assets} />
          </section>

          <section className="permanent-section">
            <div className="section-heading">
              <div>
                <p>{stageLabels[stage]}</p>
                <h2>收益与波动</h2>
              </div>
              <span>{selected?.start_date ?? "—"} 至 {selected?.end_date ?? "—"}</span>
            </div>
            <MetricsTable portfolios={portfolios} />
          </section>

          <section className="permanent-section">
            <div className="section-heading">
              <div>
                <p>风险路径</p>
                <h2>净值、回撤与滚动波动</h2>
              </div>
            </div>
            <PermanentPortfolioCharts
              series={chartSeries}
              benchmarks={chartBenchmarks}
              trades={chartTrades}
              stageBoundary={stage === "historical"
                ? selected?.stage_boundaries?.[0]
                : undefined}
            />
          </section>

          <section className="permanent-section">
            <div className="section-heading">
              <div>
                <p>执行证据</p>
                <h2>最近调仓</h2>
              </div>
            </div>
            <div className="permanent-table-wrap">
              <table className="permanent-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>组合</th>
                    <th>资产</th>
                    <th>方向</th>
                    <th>份额</th>
                    <th>成交价</th>
                    <th>成本</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((trade, index) => (
                    <tr key={`${String(trade.trade_date)}-${index}`}>
                      <td>{String(trade.trade_date ?? "—")}</td>
                      <td>{portfolioLabels[trade.portfolio] ?? trade.portfolio}</td>
                      <td>{String(trade.code ?? trade.role ?? "—")}</td>
                      <td>{String(trade.side ?? "—")}</td>
                      <td>{String(trade.shares ?? "—")}</td>
                      <td>{String(trade.price ?? "—")}</td>
                      <td>{String(trade.commission ?? "—")}</td>
                    </tr>
                  ))}
                  {trades.length === 0 ? (
                    <tr><td colSpan={7}>暂无调仓记录</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <section className="permanent-evidence">
        <span>状态 {data.study.status}</span>
        <span>规则 {data.study.contractSha256 ?? "—"}</span>
        <span>开发 {data.study.developmentSha256 ?? "—"}</span>
        <span>盲测 {data.study.holdoutSha256 ?? "—"}</span>
      </section>
    </div>
  );
}
