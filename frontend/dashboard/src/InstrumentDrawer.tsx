import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, BookOpen, LoaderCircle, X } from "lucide-react";
import { fetchInstrument } from "./api";
import { CandlestickChart } from "./FinancialCharts";
import { fieldMeta, formatFieldValue, visibleRowEntries } from "./finance";
import type { InstrumentDetail, OrderRow } from "./types";

export default function InstrumentDrawer({
  row,
  title,
  market,
  agent,
  onClose,
}: {
  row: OrderRow;
  title: string;
  market: string;
  agent: string;
  onClose: () => void;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [detail, setDetail] = useState<InstrumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(row.code));

  useEffect(() => {
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
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
    if (!row.code) {
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchInstrument(market, agent, row.code, controller.signal)
      .then((payload) => setDetail(payload))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "个股行情加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [agent, market, row.code]);

  const displayEntries = useMemo(() => visibleRowEntries(row)
    .filter(([key]) => !["code", "name", "exposure_group", "theme"].includes(key)), [row]);
  const instrument = detail?.instrument;
  const dialogName = row.code ? "证券详情" : `${title}明细`;
  return (
    <>
      <button className="drawer-backdrop" type="button" aria-label="关闭明细" onClick={onClose} />
      <aside ref={drawerRef} className="instrument-drawer" role="dialog" aria-modal="true" aria-label={dialogName}>
        <header className="instrument-head">
          <div>
            <span>{title} · {instrument?.exposure_group || row.exposure_group || row.industry || "模拟账户"}</span>
            <h2>{instrument?.name || row.name || row.code || row.run_id || title}</h2>
            <p>{row.code}{instrument?.theme || row.theme ? ` · ${instrument?.theme || row.theme}` : ""}</p>
          </div>
          <button ref={closeRef} className="icon-button" type="button" onClick={onClose} aria-label="关闭明细"><X size={19} aria-hidden="true" /></button>
        </header>
        <div className="instrument-body">
          {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={22} aria-hidden="true" />加载真实历史行情</div> : null}
          {error ? <div className="drawer-error"><AlertCircle size={18} aria-hidden="true" />{error}</div> : null}
          {detail?.warning ? <div className="drawer-warning"><AlertCircle size={18} aria-hidden="true" />{detail.warning}</div> : null}
          {detail ? (
            <section className="instrument-chart-block">
              <div className="instrument-quote">
                <span>最新收盘</span>
                <strong>{detail.latest?.close?.toFixed(3) ?? "-"}</strong>
                <b className={(detail.latest?.change_pct ?? 0) >= 0 ? "positive" : "negative"}>{formatFieldValue("momentum_20", detail.latest?.change_pct)}</b>
                <small>{detail.latest?.date ?? "-"}</small>
              </div>
              <CandlestickChart candles={detail.candles} />
            </section>
          ) : null}
          {detail?.metrics.length ? (
            <section className="research-metrics">
              <div className="drawer-section-title"><BookOpen size={15} aria-hidden="true" /><h3>关键指标</h3></div>
              <div>
                {detail.metrics.map((metric) => (
                  <article key={metric.key}>
                    <span>{metric.label}</span>
                    <strong>{metric.format === "percent" ? formatFieldValue(metric.key, metric.value) : metric.format === "money" ? formatFieldValue("avg_amount_20", metric.value) : formatFieldValue(metric.key, metric.value)}</strong>
                    <p>{metric.explanation}</p>
                  </article>
                ))}
              </div>
            </section>
          ) : null}
          <section className="record-fields">
            <div className="drawer-section-title"><BookOpen size={15} aria-hidden="true" /><h3>{title}数据</h3></div>
            <dl>
              {displayEntries.map(([key, value]) => {
                const metadata = fieldMeta(key);
                return (
                  <div key={key} title={metadata.explanation}>
                    <dt>{metadata.label}</dt>
                    <dd>{formatFieldValue(key, value)}</dd>
                    <p>{metadata.explanation}</p>
                  </div>
                );
              })}
            </dl>
          </section>
        </div>
      </aside>
    </>
  );
}
