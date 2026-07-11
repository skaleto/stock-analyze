import type { CSSProperties } from "react";
import { ArrowUpRight, GitCompareArrows, ShieldCheck, Split } from "lucide-react";
import { StrategyComparisonChart } from "./FinancialCharts";
import { formatMoney, formatPercent } from "./finance";
import type { StrategyComparison, StrategyComparisonSide } from "./types";

const strategyOrder = ["claude", "codex"] as const;

function formatRatio(value: number | null, digits = 2): string {
  return value == null ? "数据积累中" : value.toFixed(digits);
}

function sourceLabel(source: string): string {
  return source === "positions" ? "来自当前持仓" : "来自目标订单";
}

function MetricValue({ value, format, currency }: { value: number | null; format: "percent" | "number" | "money"; currency: string; }) {
  if (value == null) return <span className="comparison-pending">数据积累中</span>;
  if (format === "percent") return <>{formatPercent(value)}</>;
  if (format === "money") return <>{formatMoney(value, currency)}</>;
  return <>{value.toFixed(2)}</>;
}

function StrategyHeader({
  side,
  active,
  onSelect,
}: {
  side: StrategyComparisonSide;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`strategy-column-head ${active ? "active" : ""}`}
      style={{ "--strategy-color": side.color } as CSSProperties}
      onClick={onSelect}
      aria-label={`查看${side.label}明细`}
    >
      <span className="strategy-signal" />
      <span><b>{side.label}</b><small>{side.strategy_name || side.description}</small></span>
      <ArrowUpRight size={16} aria-hidden="true" />
    </button>
  );
}

export default function CompetitionPanel({
  comparison,
  activeAgent,
  currency,
  onSelectAgent,
}: {
  comparison: StrategyComparison | null | undefined;
  activeAgent: string;
  currency: string;
  onSelectAgent: (agent: string) => void;
}) {
  if (!comparison) {
    return (
      <section className="competition-panel terminal-section" role="region" aria-label="双策略竞技场">
        <div className="competition-empty">双策略比较数据正在生成</div>
      </section>
    );
  }

  const metricRows = [
    { label: "赛季收益", key: "season_return", format: "percent" },
    { label: "超额收益", key: "excess_return", format: "percent" },
    { label: "年化波动", key: "annualized_volatility", format: "percent" },
    { label: "Sharpe", key: "sharpe", format: "number" },
    { label: "最大回撤", key: "max_drawdown", format: "percent" },
    { label: "现金占比", key: "cash_ratio", format: "percent" },
    { label: "赛季换手", key: "turnover", format: "percent" },
    { label: "交易成本", key: "trading_cost", format: "money" },
  ] as const;

  return (
    <section className="competition-panel terminal-section" role="region" aria-label="双策略竞技场">
      <header className="competition-heading">
        <div>
          <span className="section-kicker"><GitCompareArrows size={14} aria-hidden="true" />STRATEGY ARENA</span>
          <h2>{comparison.season.name}</h2>
          <p>接管生效 {comparison.season.effective_date} · 净值锚点 {comparison.season.anchor_date || "等待首次估值"}</p>
        </div>
        <div className="divergence-strip" aria-label="策略差异指标">
          <div><Split size={15} aria-hidden="true" /><span>持仓重合度</span><strong>{comparison.pair.position_overlap == null ? "数据积累中" : formatPercent(comparison.pair.position_overlap)}</strong></div>
          <div><GitCompareArrows size={15} aria-hidden="true" /><span>收益相关性</span><strong>{formatRatio(comparison.pair.return_correlation)}</strong></div>
          <div><ShieldCheck size={15} aria-hidden="true" /><span>因子差异度</span><strong>{formatRatio(comparison.pair.factor_distance)}</strong></div>
        </div>
      </header>

      <div className="strategy-head-grid">
        <div className="strategy-head-spacer">比较维度</div>
        {strategyOrder.map((agent) => (
          <StrategyHeader
            key={agent}
            side={comparison.strategies[agent]}
            active={activeAgent === agent}
            onSelect={() => onSelectAgent(agent)}
          />
        ))}
      </div>

      <div className="competition-chart-block">
        <StrategyComparisonChart
          points={comparison.nav_series}
          strategies={{
            claude: comparison.strategies.claude,
            codex: comparison.strategies.codex,
          }}
        />
      </div>

      <div className="comparison-matrix" role="table" aria-label="策略指标矩阵">
        {metricRows.map((row) => (
          <div className="comparison-metric-row" role="row" key={row.key}>
            <span role="rowheader">{row.label}</span>
            {strategyOrder.map((agent) => (
              <strong role="cell" key={agent} className={row.key.includes("return") ? "metric-emphasis" : ""}>
                <MetricValue
                  value={comparison.strategies[agent].metrics[row.key]}
                  format={row.format}
                  currency={currency}
                />
                {row.key === "trading_cost" && comparison.strategies[agent].metrics.cost_bps != null
                  ? <small>{comparison.strategies[agent].metrics.cost_bps?.toFixed(1)} bps</small>
                  : null}
              </strong>
            ))}
          </div>
        ))}
      </div>

      <div className="comparison-detail-grid">
        <section className="factor-comparison" aria-labelledby="factor-comparison-title">
          <header><h3 id="factor-comparison-title">因子配置差异</h3><p>条形长度表示当前策略权重</p></header>
          <div className="factor-comparison-list">
            {comparison.factor_rows.map((factor) => (
              <div className="dual-factor-row" key={factor.key} title={factor.explanation}>
                <div><b>{factor.label}</b><small>{factor.explanation}</small></div>
                {strategyOrder.map((agent) => {
                  const side = comparison.strategies[agent];
                  const config = factor[agent];
                  return (
                    <div className="dual-factor-value" key={agent}>
                      <span><i style={{ width: `${Math.min(config.weight * 100, 100)}%`, background: side.color }} /></span>
                      <strong>{formatPercent(config.weight)}</strong>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </section>

        <section className="allocation-comparison" aria-labelledby="allocation-comparison-title">
          <header><h3 id="allocation-comparison-title">市场与板块暴露</h3><p>当前持仓为空时使用待执行买单</p></header>
          <div className="allocation-columns">
            {strategyOrder.map((agent) => {
              const side = comparison.strategies[agent];
              return (
                <div key={agent}>
                  <div className="allocation-column-head"><b>{side.label}</b><span>{sourceLabel(side.holdings_source)}</span></div>
                  <div className="allocation-list">
                    {side.allocations.length === 0 ? <p className="comparison-pending">暂无配置</p> : side.allocations.map((allocation) => (
                      <div key={allocation.label}>
                        <span><b>{allocation.label}</b><small>{formatPercent(allocation.weight)}</small></span>
                        <i><em style={{ width: `${Math.min((allocation.weight ?? 0) * 100, 100)}%`, background: side.color }} /></i>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </section>
  );
}
