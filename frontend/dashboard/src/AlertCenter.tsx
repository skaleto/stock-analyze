import { BellRing, CircleAlert, DatabaseZap, TrendingUp } from "lucide-react";
import { useState } from "react";
import type { PredictionAlert } from "./types";

const filters = [
  { key: "all", label: "全部" },
  { key: "opportunity", label: "机会" },
  { key: "downside", label: "风险" },
  { key: "data", label: "数据" },
  { key: "model", label: "模型" },
] as const;

export default function AlertCenter({ alerts = [] }: { alerts?: PredictionAlert[] }) {
  const [filter, setFilter] = useState<(typeof filters)[number]["key"]>("all");
  const visible = filter === "all" ? alerts : alerts.filter((alert) => alert.type === filter);
  return (
    <section className="alert-center terminal-section" aria-label="预测预警">
      <header className="compact-panel-heading"><div><BellRing size={15} aria-hidden="true" /><h2>预警中心</h2></div><strong>{visible.length}</strong></header>
      <div className="alert-filters" role="group" aria-label="预警类型">
        {filters.map((item) => <button key={item.key} type="button" aria-pressed={filter === item.key} onClick={() => setFilter(item.key)}>{item.label}</button>)}
      </div>
      <div className="alert-list">
        {visible.length === 0 ? <div className="compact-empty">当前没有符合条件的预警</div> : visible.slice(0, 8).map((alert) => {
          const Icon = alert.type === "opportunity" ? TrendingUp : alert.type === "data" ? DatabaseZap : CircleAlert;
          return <article key={alert.id} className={`alert-row alert-${alert.type}`}><Icon size={16} aria-hidden="true" /><div><strong>{alert.title}</strong><p>{alert.detail}</p></div><small>{alert.horizon ? `${alert.horizon}日` : alert.severity}</small></article>;
        })}
      </div>
    </section>
  );
}
