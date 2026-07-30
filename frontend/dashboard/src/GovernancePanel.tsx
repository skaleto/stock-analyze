import { useMemo, useState } from "react";
import {
  Activity,
  Beaker,
  GitBranch,
  Radar,
  Scale,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import type { DashboardGovernance } from "./types";

type Tab = "decision" | "risk" | "attribution" | "evidence";

const tabs: { key: Tab; label: string; icon: typeof Activity }[] = [
  { key: "decision", label: "决策链路", icon: GitBranch },
  { key: "risk", label: "风险压力", icon: ShieldCheck },
  { key: "attribution", label: "收益归因", icon: Scale },
  { key: "evidence", label: "模型与情报", icon: Beaker },
];

const lineageLabels: Record<string, string> = {
  decision_runs: "决策",
  candidate_evaluations: "候选",
  target_allocations: "权重",
  orders: "订单",
  fills: "成交",
  pnl_attributions: "归因",
  experiment_trials: "实验",
};

const rejectionLabels: Record<string, string> = {
  active_fund_event_block: "公告硬阻断",
  liquidity_below_floor: "流动性不足",
  listing_too_recent: "上市时间过短",
  abnormal_premium: "溢价异常",
  fund_size_below_floor: "基金规模不足",
  peer_tracking_error_high: "跟踪偏差过高",
  management_fee_high: "管理费过高",
  candidate_cap: "候选上限",
  insufficient_factor_coverage: "因子覆盖不足",
};

function numeric(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function percent(value: unknown): string {
  const parsed = numeric(value);
  return parsed == null ? "数据不足" : `${parsed >= 0 ? "+" : ""}${(parsed * 100).toFixed(2)}%`;
}

function fixed(value: unknown, digits = 3): string {
  const parsed = numeric(value);
  return parsed == null ? "数据不足" : parsed.toFixed(digits);
}

function text(value: unknown, fallback = "-"): string {
  return value == null || value === "" ? fallback : String(value);
}

function breachLabel(value: unknown): string {
  if (value && typeof value === "object") {
    const row = value as Record<string, unknown>;
    return text(row.reason, text(row.metric, "未达标"));
  }
  return text(value, "未达标");
}

function DecisionView({ data }: { data: DashboardGovernance }) {
  const funnel = data.lineage.decision_funnel;
  const stages = [
    { label: "已评估", value: funnel?.evaluated ?? 0 },
    { label: "通过硬门槛", value: funnel?.eligible ?? 0 },
    { label: "进入组合", value: funnel?.selected ?? data.lineage.allocations.length },
    { label: "生成订单", value: data.lineage.orders.length },
    { label: "已成交", value: data.lineage.fills.length },
  ];
  const maximum = Math.max(...stages.map((stage) => stage.value), 1);
  return (
    <div className="governance-view">
      <div className="decision-funnel" aria-label="决策漏斗">
        {stages.map((stage) => (
          <div key={stage.label}>
            <span>{stage.label}</span>
            <strong>{stage.value}</strong>
            <i style={{ width: `${Math.max(4, stage.value / maximum * 100)}%` }} />
          </div>
        ))}
      </div>
      <div className="lineage-strip" aria-label="历史审计记录数">
        {Object.entries(lineageLabels).map(([key, label]) => (
          <div key={key}><span>{label}</span><strong>{data.lineage.counts[key] ?? 0}</strong></div>
        ))}
      </div>
      <div className="governance-split">
        <section>
          <h3>最近候选</h3>
          <div className="governance-table-wrap">
            <table className="governance-table">
              <thead><tr><th>证券</th><th>评分</th><th>状态</th><th>原因</th></tr></thead>
              <tbody>
                {data.lineage.candidates.length === 0 ? (
                  <tr><td colSpan={4}>等待下一次正式决策写入</td></tr>
                ) : data.lineage.candidates.slice(0, 10).map((row, index) => (
                  <tr key={`${text(row.security_code)}-${index}`}>
                    <td><b>{text(row.name, text(row.security_code))}</b><small>{text(row.security_code)}</small></td>
                    <td>{fixed(row.rank_score)}</td>
                    <td>{row.selected ? "已入选" : row.eligible ? "可选未入选" : "已淘汰"}</td>
                    <td>{rejectionLabels[text(row.rejection_reason, "")] ?? text(row.rejection_reason, row.eligible ? "组合约束" : "数据不足")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section>
          <h3>淘汰分布</h3>
          <div className="reason-list">
            {Object.entries(funnel?.rejection_counts ?? {}).length === 0 ? (
              <p className="governance-empty">本轮没有可定位的硬门槛淘汰记录</p>
            ) : Object.entries(funnel?.rejection_counts ?? {})
              .sort((left, right) => right[1] - left[1])
              .map(([reason, count]) => (
                <div key={reason}><span>{rejectionLabels[reason] ?? reason}</span><strong>{count}</strong></div>
              ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function RiskView({ data }: { data: DashboardGovernance }) {
  return (
    <div className="governance-view risk-view">
      {data.risk.portfolios.length === 0 ? (
        <p className="governance-empty">等待下一次组合优化生成风险快照</p>
      ) : data.risk.portfolios.map((portfolio, index) => {
        const stresses = (portfolio.stress_losses ?? {}) as Record<string, unknown>;
        const contributions = Object.entries(
          (portfolio.risk_contributions ?? {}) as Record<string, unknown>,
        ).sort((left, right) => Math.abs(numeric(right[1]) ?? 0) - Math.abs(numeric(left[1]) ?? 0));
        const constraints = Array.isArray(portfolio.binding_constraints)
          ? portfolio.binding_constraints.map(String)
          : [];
        return (
          <section className="risk-portfolio" key={`${text(portfolio.account_id, text(portfolio.scope))}-${index}`}>
            <header><div><h3>{text(portfolio.account_id, text(portfolio.scope, `组合 ${index + 1}`))}</h3><p>{portfolio.fallback_reason ? `降级原因：${text(portfolio.fallback_reason)}` : "联合优化正常完成"}</p></div><span>{percent(portfolio.cash_weight)} 现金</span></header>
            <div className="risk-metrics">
              <div><span>预期 Alpha</span><strong>{percent(portfolio.expected_alpha)}</strong></div>
              <div><span>年化波动</span><strong>{percent(portfolio.volatility)}</strong></div>
              <div><span>跟踪误差</span><strong>{percent(portfolio.tracking_error)}</strong></div>
              <div><span>换手率</span><strong>{percent(portfolio.turnover)}</strong></div>
              <div><span>预期成本</span><strong>{percent(portfolio.expected_cost)}</strong></div>
            </div>
            <div className="governance-split">
              <div>
                <h4>压力损失</h4>
                <div className="stress-list">
                  {Object.entries(stresses).map(([name, value]) => {
                    const amount = numeric(value) ?? 0;
                    return <div key={name}><span>{name}</span><i><b style={{ width: `${Math.min(100, Math.abs(amount) * 500)}%` }} /></i><strong>{percent(amount)}</strong></div>;
                  })}
                </div>
              </div>
              <div>
                <h4>主要风险贡献</h4>
                <div className="reason-list">
                  {contributions.slice(0, 6).map(([code, value]) => <div key={code}><span>{code}</span><strong>{percent(value)}</strong></div>)}
                </div>
                <p className="constraint-line">约束命中：{constraints.length ? constraints.join("、") : "无"}</p>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );
}

function AttributionView({ data }: { data: DashboardGovernance }) {
  const summary = data.attribution.rows.find((row) => row.security_code === "__PORTFOLIO__")
    ?? data.attribution.rows[0];
  const components = summary ? [
    ["市场", summary.market_pnl],
    ["行业", summary.industry_pnl],
    ["Alpha", summary.alpha_pnl],
    ["现金", summary.cash_pnl],
    ["成本", summary.cost_pnl],
    ["约束", summary.constraint_pnl],
    ["残差", summary.residual_pnl],
  ] : [];
  return (
    <div className="governance-view attribution-view">
      {!summary ? <p className="governance-empty">至少需要两个净值日，下一轮运行后生成首份归因</p> : (
        <>
          <div className="attribution-head">
            <div><span>归因日期</span><strong>{text(summary.as_of)}</strong></div>
            <div><span>净损益</span><strong>{fixed(summary.net_pnl, 2)}</strong></div>
            <div><span>对账差额</span><strong>{fixed(summary.reconciliation_delta, 6)}</strong></div>
            <div><span>证据状态</span><strong>{text(summary.status) === "complete" ? "完整" : "部分可用"}</strong></div>
          </div>
          <div className="attribution-waterfall" aria-label="收益归因分项">
            {components.map(([label, raw]) => {
              const value = numeric(raw) ?? 0;
              return <div key={String(label)} className={value >= 0 ? "positive" : "negative"}><span>{text(label)}</span><i style={{ height: `${Math.max(6, Math.min(100, Math.abs(value) / Math.max(Math.abs(numeric(summary.net_pnl) ?? 1), 1) * 100))}%` }} /><strong>{value >= 0 ? "+" : ""}{value.toFixed(2)}</strong></div>;
            })}
          </div>
          <p className="constraint-line">未具备的细分证据：{Array.isArray(summary.unavailable_inputs) && summary.unavailable_inputs.length ? summary.unavailable_inputs.join("、") : "无"}</p>
        </>
      )}
    </div>
  );
}

function EvidenceView({ data }: { data: DashboardGovernance }) {
  const validation = data.intelligence_evidence.factor_validation ?? {};
  const factors = (validation.factors ?? {}) as Record<string, Record<string, unknown>>;
  const driftRows = Object.entries(data.drift);
  const breaches = Array.isArray(data.distinctness.breaches)
    ? data.distinctness.breaches.map(breachLabel)
    : [];
  return (
    <div className="governance-view">
      <div className="evidence-grid">
        <section>
          <h3>模型漂移</h3>
          <div className="evidence-list">
            {driftRows.length === 0 ? <p className="governance-empty">尚无漂移观测</p> : driftRows.map(([horizon, row]) => (
              <div key={horizon}><span>{horizon} 日</span><strong>{text(row.status, "观察中")}</strong><small>{Array.isArray(row.breaches) && row.breaches.length ? row.breaches.join("、") : "未触发阈值"}</small></div>
            ))}
          </div>
        </section>
        <section>
          <h3>策略差异度</h3>
          <div className="distinctness-score"><Radar size={22} aria-hidden="true" /><strong>{percent(data.distinctness.distinctness_score)}</strong><span>{text(data.distinctness.status, "数据积累中")}</span></div>
          <p className="constraint-line">{breaches.length ? `未达标：${breaches.join("、")}` : "持仓、收益、决策和因子风格联合检查"}</p>
        </section>
      </div>
      <div className="governance-split">
        <section>
          <h3>情报因子证据</h3>
          <div className="evidence-list">
            {Object.keys(factors).length === 0 ? <p className="governance-empty">尚未形成可晋级的情报因子证据</p> : Object.entries(factors).slice(0, 8).map(([name, row]) => (
              <div key={name}><span>{name}</span><strong>{row.recommendation === "model_iteration" ? "可进入模型迭代" : "继续观察"}</strong><small>覆盖 {percent(row.coverage)} · IC {fixed(row.mean_rank_ic)} · 稳定 {percent(row.ic_sign_stability)}</small></div>
            ))}
          </div>
        </section>
        <section>
          <h3>最近实验</h3>
          <div className="evidence-list">
            {data.experiments.length === 0 ? <p className="governance-empty">尚无同口径实验记录</p> : data.experiments.slice(0, 6).map((row, index) => (
              <div key={`${text(row.trial_id)}-${index}`}><span>{text(row.model_version, text(row.trial_id))}</span><strong>{text(row.horizon)} 日</strong><small>Rank IC {fixed(row.rank_ic)} · Sharpe {fixed(row.sharpe)}</small></div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

export default function GovernancePanel({ data }: { data?: DashboardGovernance }) {
  const [tab, setTab] = useState<Tab>("decision");
  const actionState = data?.action_state;
  const statusLabel = !actionState
    ? "证据加载中"
    : actionState.status === "critical"
    ? "存在阻断"
    : actionState.status === "warning"
      ? "需要观察"
      : "运行正常";
  const visibleActions = useMemo(() => actionState?.items.slice(0, 3) ?? [], [actionState]);
  return (
    <section className="governance-panel terminal-section" role="region" aria-label="决策与风控">
      <header className="section-heading governance-heading">
        <div><span className="section-kicker"><ShieldCheck size={14} aria-hidden="true" />GOVERNANCE</span><h2>决策与风控</h2><p>从候选淘汰到收益归因，逐层核对本轮决策证据</p></div>
        <div className={`governance-state governance-state-${actionState?.status ?? "unknown"}`}>{actionState?.status === "critical" ? <ShieldAlert size={16} aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}<span>{statusLabel}</span></div>
      </header>
      {visibleActions.length ? (
        <div className="governance-actions">
          {visibleActions.map((item, index) => <div className={`governance-action action-${item.severity}`} key={`${item.title}-${index}`}><strong>{item.title}</strong><span>{item.detail}</span></div>)}
        </div>
      ) : null}
      <div className="governance-tabs" role="tablist" aria-label="决策与风控维度">
        {tabs.map(({ key, label, icon: Icon }) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}><Icon size={15} aria-hidden="true" />{label}</button>)}
      </div>
      {!data ? <p className="governance-empty">决策证据正在加载</p> : tab === "decision" ? <DecisionView data={data} /> : tab === "risk" ? <RiskView data={data} /> : tab === "attribution" ? <AttributionView data={data} /> : <EvidenceView data={data} />}
    </section>
  );
}
