import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, BookOpen, LoaderCircle, X } from "lucide-react";
import { fetchResearchUniverseInstrument } from "./api";
import { CandlestickChart } from "./FinancialCharts";
import { formatFieldValue } from "./finance";
import type {
  ResearchUniverseInstrumentDetail,
  ResearchUniverseKind,
  ResearchUniverseRecord,
} from "./workspaceTypes";

function metadataEntries(record: ResearchUniverseRecord): Array<[string, string]> {
  if ("fundType" in record) {
    return [
      ["基金类型", record.fundType || "—"],
      ["基准", record.benchmark || "—"],
      ["跨境范围", record.overseasScope ?? "未分类"],
      ["分类", record.classificationStatus || "—"],
      ["研究状态", record.tradability === "otc_non_tradable_research_only" ? "非交易研究对照" : "场内研究候选"],
    ];
  }
  return [
    ["研究范围", record.researchScopes.join(" · ") || "未分类"],
    ["成员日期", record.membershipDate ?? "未记录"],
  ];
}

function drawerName(record: ResearchUniverseRecord): string {
  return record.name || record.code;
}

export default function ResearchUniverseInstrumentDrawer({
  kind,
  record,
  onClose,
}: {
  kind: ResearchUniverseKind;
  record: ResearchUniverseRecord;
  onClose: () => void;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [detail, setDetail] = useState<ResearchUniverseInstrumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const name = drawerName(record);
  const metadata = useMemo(() => metadataEntries(record), [record]);

  useEffect(() => {
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      ) ?? []);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setDetail(null);
    fetchResearchUniverseInstrument({ kind, code: record.code }, controller.signal)
      .then(setDetail)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "投研详情加载失败");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [kind, record.code]);

  const latest = detail?.latest;
  return (
    <>
      <button className="drawer-backdrop" type="button" aria-label="关闭遮罩" onClick={onClose} />
      <aside ref={drawerRef} className="instrument-drawer research-universe-instrument-drawer" role="dialog" aria-modal="true" aria-label={`${name}投研详情`}>
        <header className="instrument-head">
          <div>
            <span>研究目录 · 只读详情</span>
            <h2>{name}</h2>
            <p>{record.code}</p>
          </div>
          <button ref={closeRef} className="icon-button" type="button" onClick={onClose} aria-label="关闭投研详情"><X size={19} aria-hidden="true" /></button>
        </header>
        <div className="instrument-body">
          {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={22} aria-hidden="true" />加载已落盘行情与指标</div> : null}
          {error ? <div className="drawer-error"><AlertCircle size={18} aria-hidden="true" />{error}</div> : null}
          {detail?.warning ? <div className="drawer-warning"><AlertCircle size={18} aria-hidden="true" />{detail.warning}</div> : null}
          {detail?.status === "unavailable" ? <p className="research-universe-state">{detail.warning ?? "投研详情暂不可用。"}</p> : null}
          {detail?.status === "available" ? (
            <>
              <section className="instrument-chart-block">
                <div className="drawer-section-title"><BookOpen size={15} aria-hidden="true" /><h3>K线行情</h3></div>
                {latest ? (
                  <div className="instrument-quote">
                    <span>最新收盘</span>
                    <strong>{latest.close.toFixed(3)}</strong>
                    <b className={(latest.changePct ?? 0) >= 0 ? "positive" : "negative"}>{formatFieldValue("momentum_20", latest.changePct)}</b>
                    <small>{latest.date}</small>
                  </div>
                ) : null}
                <CandlestickChart candles={detail.candles} showTradeMarkers={false} />
              </section>
              {detail.metrics.length ? (
                <section className="research-metrics">
                  <div className="drawer-section-title"><BookOpen size={15} aria-hidden="true" /><h3>关键指标</h3></div>
                  <div>
                    {detail.metrics.map((metric) => (
                      <article key={metric.key}>
                        <span>{metric.label}</span>
                        <strong>{metric.format === "money" ? formatFieldValue("avg_amount_20", metric.value) : formatFieldValue(metric.key, metric.value)}</strong>
                        <p>{metric.explanation}</p>
                      </article>
                    ))}
                  </div>
                </section>
              ) : null}
            </>
          ) : null}
          <section className="record-fields">
            <div className="drawer-section-title"><BookOpen size={15} aria-hidden="true" /><h3>目录元数据</h3></div>
            <dl>
              {metadata.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
      </aside>
    </>
  );
}
