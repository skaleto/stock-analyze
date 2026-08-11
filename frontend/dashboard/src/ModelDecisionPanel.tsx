import { CirclePause, Filter, ScanSearch } from "lucide-react";
import type { ModelIterationStatus } from "./types";

const REGIME_LABELS: Record<string, string> = {
  risk_on: "风险偏好",
  risk_off: "风险规避",
  mixed: "多空交错",
  unknown: "状态未知",
};

function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export default function ModelDecisionPanel({ status }: { status?: ModelIterationStatus | null }) {
  const diagnostics = status?.decision_diagnostics;
  const cashOnly = diagnostics?.outcome === "cash" || status?.cash_only === true;
  const summary = diagnostics?.summary ?? status?.cash_reason ?? "等待候选模型生成本期决策";
  const funnel = diagnostics?.funnel ?? [];
  const nearMisses = diagnostics?.near_misses ?? [];
  const regime = REGIME_LABELS[diagnostics?.regime ?? "unknown"] ?? diagnostics?.regime ?? "状态未知";

  return (
    <section className="model-decision-panel" role="region" aria-label="本期模型决策">
      <header className="model-decision-heading">
        <div className="model-decision-title">
          <span><Filter size={15} aria-hidden="true" /></span>
          <div>
            <strong>本期决策</strong>
            <p>{status?.display_version ?? status?.candidate?.display_version ?? "候选版本"} · {status?.prediction_as_of ?? status?.as_of ?? "等待数据日"}</p>
          </div>
        </div>
        <div className="model-decision-outcome">
          <span className={cashOnly ? "cash" : "selected"}><CirclePause size={13} aria-hidden="true" />{cashOnly ? "持币观察" : "生成模拟订单"}</span>
          <small>{regime}</small>
        </div>
      </header>

      <div className="model-decision-body">
        <div className="decision-summary">
          <span>DECISION</span>
          <p>{summary}</p>
        </div>

        {funnel.length ? (
          <div className="decision-funnel" role="list" aria-label="模型筛选漏斗">
            {funnel.map((stage, index) => (
              <div key={stage.key} role="listitem" className={stage.count === 0 ? "stopped" : ""}>
                <small>{stage.label}</small>
                <strong>{stage.count}</strong>
                {index < funnel.length - 1 ? <i aria-hidden="true" /> : null}
              </div>
            ))}
          </div>
        ) : (
          <div className="decision-diagnostics-pending">本次运行尚未记录筛选明细</div>
        )}

        <div className="near-miss-section">
          <div className="near-miss-heading">
            <span><ScanSearch size={14} aria-hidden="true" />最接近入选</span>
            <small>按预期超额、概率差和置信度排序</small>
          </div>
          {nearMisses.length ? (
            <div className="near-miss-list">
              {nearMisses.map((row) => (
                <article key={row.code} className="near-miss-row">
                  <div className="near-miss-security"><strong>{row.name || row.code}</strong><small>{row.code}</small></div>
                  <div className="near-miss-probabilities"><span>上涨 {percent(row.p_up)}</span><span>下跌 {percent(row.p_down)}</span><span>可信度 {percent(row.confidence)}</span></div>
                  <div className="near-miss-alpha"><small>预期超额</small><strong>{percent(row.expected_excess_return, 2)}</strong></div>
                  <div className="near-miss-rules">{row.failed_rules.map((rule) => <span key={rule}>{rule}</span>)}</div>
                </article>
              ))}
            </div>
          ) : (
            <div className="decision-diagnostics-pending">没有需要解释的近失候选</div>
          )}
        </div>
      </div>
    </section>
  );
}
