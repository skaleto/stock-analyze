import { Braces, CircleAlert } from "lucide-react";
import type { ModelHealth, RegimeSummary, SourceHealth } from "./types";

const number = (value?: number) => value == null ? "-" : value.toFixed(3);
const LOG_LOSS_BASELINE = Math.log(3);
const BRIER_BASELINE = 2 / 3;
const gateLabels: Record<string, string> = {
  coverage: "数据覆盖",
  point_in_time_audit: "时点审计",
  oos_predictions: "样本数量",
  rank_ic: "排名IC",
  icir: "IC稳定性",
  brier_improvement: "概率校准",
  hit_rate_uplift: "命中提升",
  auc: "区分能力",
  net_excess_return: "扣费超额",
  max_drawdown: "回撤控制",
  annual_turnover: "换手控制",
  ablation_stability: "模型稳定性",
  shadow_cycles: "影子周期",
};

const stateLabel = (status?: string) => status === "active" ? "已激活" : status === "shadow" ? "影子验证" : "研究中";

function MetricCell({ value, baseline, label }: { value?: number; baseline: number; label: string }) {
  const ratio = value == null ? 0 : Math.min(100, Math.max(0, value / baseline * 100));
  return (
    <div className="model-score" aria-label={`${label} ${number(value)}，随机基线 ${number(baseline)}`}>
      <span>{number(value)}</span><small>基线 {number(baseline)}</small>
      <div><i style={{ width: `${ratio}%` }} /></div>
    </div>
  );
}

export default function ModelHealthPanel({ health, regimes, sources = [] }: { health?: ModelHealth; regimes?: RegimeSummary; sources?: SourceHealth[] }) {
  const models = [...(health?.models ?? [])].sort((a, b) => (a.horizon ?? 0) - (b.horizon ?? 0));
  const unavailable = sources.filter((source) => source.status === "source_unavailable").length;
  const overallState = models.some((model) => model.status === "active")
    ? "已激活"
    : models.some((model) => model.status === "shadow") ? "影子验证" : models.length ? "研究中" : "未训练";
  const missingEvidence = Array.from(new Set(models.flatMap((model) => model.gate_reasons ?? [])))
    .map((reason) => gateLabels[reason] ?? reason);
  return (
    <section className="model-health terminal-section" aria-label="模型健康">
      <header className="compact-panel-heading"><div><Braces size={15} aria-hidden="true" /><h2>模型与数据</h2></div><span>{overallState}</span></header>
      {models.length ? (
        <div className="model-health-table" role="table" aria-label="各周期模型质量">
          <div className="model-health-head" role="row"><span>周期</span><span>状态</span><span>Log Loss</span><span>Brier</span><span>影子</span></div>
          {models.map((model) => (
            <div className="model-health-row" role="row" key={`${model.horizon}-${model.model_version}`}>
              <strong>{model.horizon}日</strong>
              <span className={`model-status model-status-${model.status ?? "research"}`}>{stateLabel(model.status)}</span>
              <MetricCell value={model.metrics?.log_loss} baseline={LOG_LOSS_BASELINE} label="Log Loss" />
              <MetricCell value={model.metrics?.brier_score} baseline={BRIER_BASELINE} label="Brier" />
              <b>{model.shadow_cycles ?? 0}/4</b>
            </div>
          ))}
        </div>
      ) : <div className="model-note"><CircleAlert size={14} aria-hidden="true" />模型尚未完成训练或晋级</div>}
      {missingEvidence.length ? <div className="model-gate-note">待补证据：{missingEvidence.join("、")}</div> : null}
      <div className="model-context-line"><span>市场状态 <strong>{String(regimes?.current?.composite_regime ?? "暂无")}</strong></span><span className={unavailable ? "warning" : ""}>未接入文本源 {unavailable}</span></div>
    </section>
  );
}
