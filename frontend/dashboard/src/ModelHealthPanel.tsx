import { Braces, CircleAlert } from "lucide-react";
import type { ModelHealth, RegimeSummary, SourceHealth } from "./types";

const number = (value?: number) => value == null ? "-" : value.toFixed(3);
const percent = (value?: number | null) => value == null ? "-" : `${(value * 100).toFixed(1)}%`;
const metricNumber = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : undefined;
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
  shadow_cycles: "验证周期",
};

const stateLabel = (status?: string) => status === "active" ? "正式使用" : status === "shadow" ? "模拟验证" : "研究候选";
const regimeLabels: Record<string, string> = {
  risk_on: "风险偏好", risk_off: "风险规避", mixed: "多空交错", unknown: "数据不足",
  up: "上行", flat: "震荡", down: "下行", high: "高波动", normal: "常态", low: "低波动",
  expanding: "扩张", neutral: "中性", contracting: "收缩", recovery: "复苏",
  expansion: "扩张", slowdown: "放缓", contraction: "收缩",
};
const regimeLabel = (value: unknown) => regimeLabels[String(value ?? "unknown")] ?? String(value ?? "数据不足");

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
    : models.some((model) => model.status === "shadow") ? "模拟验证" : models.length ? "研究候选" : "未训练";
  const missingEvidence = Array.from(new Set(models.flatMap((model) => model.gate_reasons ?? [])))
    .map((reason) => gateLabels[reason] ?? reason);
  const current = regimes?.current ?? {};
  const dimensions = [
    ["趋势", current.trend_regime], ["波动", current.volatility_regime],
    ["流动性", current.liquidity_regime], ["宏观", current.macro_regime],
    ["全球", current.global_risk_regime],
  ];
  const industries = (regimes?.industries ?? []).slice(0, 6);
  const diagnosticModel = models.find((model) => model.horizon === 5) ?? models[0];
  const reliability = Array.isArray(diagnosticModel?.metrics?.reliability_curve)
    ? diagnosticModel.metrics.reliability_curve.filter((row): row is { class: string; bin: number; count: number; predicted: number; observed: number } => {
      if (typeof row !== "object" || row == null) return false;
      const point = row as Record<string, unknown>;
      return point.class === "up" && typeof point.predicted === "number" && typeof point.observed === "number";
    })
    : [];
  const accuracy = health?.accuracy;
  const diagnostics = health?.prediction_diagnostics;
  return (
    <section className="model-health terminal-section" aria-label="模型健康">
      <header className="compact-panel-heading"><div><Braces size={15} aria-hidden="true" /><h2>模型与数据</h2></div><span>{overallState}</span></header>
      {models.length ? (
        <div className="model-health-table" role="table" aria-label="各周期模型质量">
          <div className="model-health-head" role="row"><span>周期</span><span>状态</span><span>Log Loss</span><span>Brier</span><span>验证</span></div>
          {models.map((model) => (
            <div className="model-health-row" role="row" key={`${model.horizon}-${model.model_version}`}>
              <strong>{model.horizon}日</strong>
              <span className={`model-status model-status-${model.status ?? "research"}`}>{stateLabel(model.status)}</span>
              <MetricCell value={metricNumber(model.metrics?.log_loss)} baseline={LOG_LOSS_BASELINE} label="Log Loss" />
              <MetricCell value={metricNumber(model.metrics?.brier_score)} baseline={BRIER_BASELINE} label="Brier" />
              <b>{model.shadow_cycles ?? 0}/4</b>
            </div>
          ))}
        </div>
      ) : <div className="model-note"><CircleAlert size={14} aria-hidden="true" />模型尚未完成训练或晋级</div>}
      {missingEvidence.length ? <div className="model-gate-note">待补证据：{missingEvidence.join("、")}</div> : null}
      {(accuracy?.evaluated ?? 0) > 0 || (diagnostics?.invalidated ?? 0) > 0 ? <div className="model-live-diagnostics" aria-label="预测兑现与漂移">
        <span><small>兑现样本</small><b>{accuracy?.evaluated ?? 0}</b></span>
        <span><small>命中率</small><b>{percent(accuracy?.hit_rate)}</b></span>
        <span><small>兑现 Brier</small><b>{number(accuracy?.mean_brier_score ?? undefined)}</b></span>
        <span><small>最大 PSI</small><b>{number(diagnostics?.max_psi)}</b></span>
        <span><small>当前失效</small><b>{diagnostics?.invalidated ?? 0}</b></span>
      </div> : null}
      {reliability.length ? <div className="model-reliability">
        <div className="model-reliability-heading"><span>上行概率校准</span><small>预测 / 实际</small></div>
        <div className="reliability-bars" aria-label={`${diagnosticModel?.horizon ?? "-"}日上行概率校准曲线`}>
          {reliability.map((point) => <span key={`${point.bin}-${point.predicted}`} title={`预测 ${percent(point.predicted)}，实际 ${percent(point.observed)}，样本 ${point.count}`}>
            <i style={{ height: `${Math.max(2, Math.min(100, point.observed * 100))}%` }} />
            <b style={{ bottom: `${Math.max(0, Math.min(100, point.predicted * 100))}%` }} />
          </span>)}
        </div>
      </div> : null}
      <div className="model-context-line"><span>市场状态 <strong>{regimeLabel(current.composite_regime)}</strong></span><span className={unavailable ? "warning" : ""}>未接入文本源 {unavailable}</span></div>
      <div className="regime-dimensions" aria-label="市场五维状态">
        {dimensions.map(([label, value]) => <span key={String(label)}><small>{String(label)}</small><b>{regimeLabel(value)}</b></span>)}
      </div>
      {industries.length ? <div className="industry-regime-strip" aria-label="行业状态">
        {industries.map((row) => <span key={String(row.scope)}><small>{String(row.scope ?? "").replace("industry:", "")}</small><b>{regimeLabel(row.composite_regime)}</b></span>)}
      </div> : null}
    </section>
  );
}
