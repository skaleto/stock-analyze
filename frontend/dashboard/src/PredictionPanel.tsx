import { Activity, CircleAlert, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import type { PredictionSummary } from "./types";

const percent = (value?: number | null) => value == null ? "-" : `${(value * 100).toFixed(1)}%`;

export default function PredictionPanel({ summary }: { summary?: PredictionSummary }) {
  const horizons = summary?.horizons ?? [];
  const preferred = horizons.includes(5) ? 5 : horizons[0] ?? 5;
  const [horizon, setHorizon] = useState(preferred);
  const rows = useMemo(
    () => (summary?.rows ?? []).filter((row) => row.horizon === horizon).sort((a, b) => b.confidence - a.confidence),
    [horizon, summary?.rows],
  );

  return (
    <section className="prediction-panel terminal-section" aria-label="概率预测">
      <header className="prediction-heading">
        <div>
          <span className="section-kicker"><Activity size={14} aria-hidden="true" />PREDICTION RESEARCH</span>
          <h2>概率预测</h2>
          <p>上涨概率与可信度独立计算，研究态不会改变模拟订单</p>
        </div>
        <span className={`research-status ${summary?.status === "available" ? "available" : "unavailable"}`}>
          {summary?.status === "available" ? <ShieldCheck size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}
          {summary?.status === "available" ? `数据日 ${summary.as_of ?? "-"}` : "研究中"}
        </span>
      </header>
      {summary?.status !== "available" ? (
        <div className="prediction-empty">预测研究尚无可用数据</div>
      ) : (
        <>
          <div className="horizon-switch" role="group" aria-label="预测周期">
            {horizons.map((value) => (
              <button key={value} type="button" aria-pressed={horizon === value} className={horizon === value ? "active" : ""} onClick={() => setHorizon(value)}>
                {value}日
              </button>
            ))}
          </div>
          <div className="prediction-rows">
            {rows.length === 0 ? <div className="prediction-empty">该周期暂无预测</div> : rows.slice(0, 8).map((row) => (
              <article key={`${row.code}-${row.horizon}`} className="prediction-row">
                <div className="prediction-security">
                  <strong>{row.name || row.code}</strong><span>{row.code}</span>
                  <small className={row.active_status === "active" ? "active" : "research"}>{row.active_status === "active" ? "已激活" : "研究中"}</small>
                </div>
                <div className="probability-stack">
                  <div className="probability-labels"><span>上涨概率 <b>{percent(row.p_up)}</b></span><span>震荡 {percent(row.p_flat)}</span><span>下跌 {percent(row.p_down)}</span></div>
                  <div className="probability-track" aria-label={`${row.code}概率分布`}>
                    <i className="probability-up" style={{ width: `${row.p_up * 100}%` }} />
                    <i className="probability-flat" style={{ width: `${row.p_flat * 100}%` }} />
                    <i className="probability-down" style={{ width: `${row.p_down * 100}%` }} />
                  </div>
                  <div className="confidence-row"><span>可信度</span><div><i style={{ width: `${row.confidence * 100}%` }} /></div><b>{percent(row.confidence)}</b></div>
                </div>
                <div className="prediction-return"><span>预期超额</span><strong>{percent(row.expected_excess_return)}</strong><small>{row.regime || "状态未知"}</small></div>
                <div className="prediction-evidence">
                  <p>{row.reasons?.[0] ?? "等待证据积累"}</p>
                  <small>{row.invalidation?.[0] ?? "暂无失效条件"}</small>
                </div>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
