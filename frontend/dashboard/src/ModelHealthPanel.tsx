import { Braces, CircleAlert } from "lucide-react";
import type { ModelHealth, RegimeSummary, SourceHealth } from "./types";

const number = (value?: number) => value == null ? "-" : value.toFixed(3);

export default function ModelHealthPanel({ health, regimes, sources = [] }: { health?: ModelHealth; regimes?: RegimeSummary; sources?: SourceHealth[] }) {
  const latest = health?.models?.[health.models.length - 1];
  const unavailable = sources.filter((source) => source.status === "source_unavailable").length;
  return (
    <section className="model-health terminal-section" aria-label="模型健康">
      <header className="compact-panel-heading"><div><Braces size={15} aria-hidden="true" /><h2>模型与数据</h2></div><span>{health?.status === "available" ? "已校准" : "未训练"}</span></header>
      <dl className="model-metrics">
        <div><dt>校准方式</dt><dd>{latest?.calibration_method ?? "-"}</dd></div>
        <div><dt>Log Loss</dt><dd>{number(latest?.metrics?.log_loss)}</dd></div>
        <div><dt>Brier</dt><dd>{number(latest?.metrics?.brier_score)}</dd></div>
        <div><dt>样本数</dt><dd>{latest?.sample_support ?? "-"}</dd></div>
      </dl>
      <div className="model-state-line"><span>市场状态</span><strong>{String(regimes?.current?.composite_regime ?? "暂无")}</strong></div>
      <div className="model-state-line"><span>影子观察周期</span><strong>{latest?.shadow_cycles ?? 0}/4{latest?.shadow_cycles_remaining ? `，还差 ${latest.shadow_cycles_remaining}` : ""}</strong></div>
      <div className="model-state-line"><span>未接入文本源</span><strong className={unavailable ? "warning" : ""}>{unavailable}</strong></div>
      {health?.status !== "available" ? <div className="model-note"><CircleAlert size={14} aria-hidden="true" />模型尚未完成训练或晋级</div> : null}
    </section>
  );
}
