import { Activity, ArrowDownRight, ArrowUpRight, CalendarDays, Layers3, Radio, Target } from "lucide-react";
import type { OrderRow, StrategyProfile } from "./types";
import { formatFieldValue, formatMoney, formatPercent, formatStrategyReason, sideLabel } from "./finance";

type SelectHandler = (row: OrderRow, title: string, trigger: HTMLElement) => void;

function rowValue(row: OrderRow, planned: boolean): number {
  const candidate = planned ? row.target_value : row.market_value;
  return typeof candidate === "number" && Number.isFinite(candidate) ? candidate : 0;
}

export function PortfolioSection({
  positions,
  planned,
  currency,
  onSelect,
}: {
  positions: OrderRow[];
  planned: OrderRow[];
  currency: string;
  onSelect: SelectHandler;
}) {
  const isPlanned = positions.length === 0;
  const rows = isPlanned ? planned.filter((row) => row.side !== "sell") : positions;
  const total = rows.reduce((sum, row) => sum + rowValue(row, isPlanned), 0);
  const groups = Array.from(rows.reduce((map, row) => {
    const label = String(row.exposure_group || row.industry || "未分类");
    const existing = map.get(label) ?? [];
    existing.push(row);
    map.set(label, existing);
    return map;
  }, new Map<string, OrderRow[]>())).sort((left, right) => {
    const leftValue = left[1].reduce((sum, row) => sum + rowValue(row, isPlanned), 0);
    const rightValue = right[1].reduce((sum, row) => sum + rowValue(row, isPlanned), 0);
    return rightValue - leftValue;
  });

  return (
    <section className="portfolio-section terminal-section" role="region" aria-label="持仓组合">
      <header className="section-heading">
        <div>
          <span className="section-kicker"><Layers3 size={14} aria-hidden="true" />PORTFOLIO</span>
          <h2>{isPlanned ? "计划持仓" : "当前持仓"}</h2>
          <p>{isPlanned ? "等待下一交易日模拟成交" : "按底层市场与行业分组，点击证券查看行情"}</p>
        </div>
        <div className="section-stat">
          <span>{isPlanned ? "计划金额" : "持仓市值"}</span>
          <strong>{formatMoney(total, currency)}</strong>
        </div>
      </header>
      {groups.length === 0 ? (
        <div className="terminal-empty">当前没有持仓或待执行买入计划</div>
      ) : (
        <div className="exposure-groups">
          {groups.map(([group, groupRows]) => {
            const groupValue = groupRows.reduce((sum, row) => sum + rowValue(row, isPlanned), 0);
            return (
              <section className="exposure-group" key={group}>
                <header>
                  <div>
                    <h3>{group}</h3>
                    <span>{groupRows.length} 只证券</span>
                  </div>
                  <div className="exposure-weight">
                    <strong>{formatPercent(total > 0 ? groupValue / total : 0)}</strong>
                    <span>{formatMoney(groupValue, currency)}</span>
                  </div>
                </header>
                <div className="security-list">
                  {groupRows.map((row) => {
                    const pnl = typeof row.unrealized_pnl === "number" ? row.unrealized_pnl : null;
                    return (
                      <button
                        key={`${row.account_id || "account"}-${row.code}`}
                        type="button"
                        className="security-row"
                        onClick={(event) => onSelect(row, isPlanned ? "计划持仓" : "持仓", event.currentTarget)}
                      >
                        <span className="security-identity">
                          <b>{row.name || row.code}</b>
                          <small><span>{row.code}</span><span aria-hidden="true"> · </span><span>{row.theme || row.industry || "未分类"}</span></small>
                        </span>
                        <span className="security-quantity">
                          <small>{formatFieldValue("shares", row.shares)} 份</small>
                          <b>{formatMoney(rowValue(row, isPlanned), currency)}</b>
                        </span>
                        <span className={`security-pnl ${(pnl ?? 0) >= 0 ? "positive" : "negative"}`}>
                          {pnl === null ? <Target size={17} aria-hidden="true" /> : pnl >= 0 ? <ArrowUpRight size={17} aria-hidden="true" /> : <ArrowDownRight size={17} aria-hidden="true" />}
                          {pnl === null ? "待成交" : formatMoney(pnl, currency)}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </section>
  );
}

function shortDate(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})/);
  return match ? `${match[1]}月${match[2]}日` : value;
}

export function TradeTimeline({ events, onSelect }: { events: OrderRow[]; onSelect: SelectHandler }) {
  const grouped = Array.from(events.reduce((map, event) => {
    const date = String(event.date || event.trade_date || event.execute_after || "日期未知");
    const current = map.get(date) ?? [];
    current.push(event);
    map.set(date, current);
    return map;
  }, new Map<string, OrderRow[]>()));
  return (
    <section className="timeline-section terminal-section" role="region" aria-label="交易时间线">
      <header className="section-heading">
        <div>
          <span className="section-kicker"><CalendarDays size={14} aria-hidden="true" />ACTIVITY</span>
          <h2>交易时间线</h2>
          <p>把已经成交和等待执行的动作放回具体日期</p>
        </div>
        <div className="section-stat"><span>记录</span><strong>{events.length}</strong></div>
      </header>
      {grouped.length === 0 ? <div className="terminal-empty">暂无成交或计划订单</div> : (
        <div className="timeline-list">
          {grouped.map(([date, dateEvents]) => (
            <section className="timeline-day" key={date}>
              <time dateTime={date}>{shortDate(date)}</time>
              <div>
                {dateEvents.map((event, index) => (
                  <button key={`${event.code}-${event.status}-${index}`} type="button" onClick={(click) => onSelect(event, "交易", click.currentTarget)}>
                    <span className={`timeline-marker ${event.status === "completed" ? "completed" : "planned"}`}><Radio size={12} aria-hidden="true" /></span>
                    <span className="timeline-copy">
                      <b>{event.status_label || `${event.status === "completed" ? "已" : "计划"}${sideLabel(String(event.side || ""))}`}</b>
                      <span>{event.name || event.code}</span>
                      <small>{event.code} · {formatFieldValue("shares", event.shares)} 份 · {formatStrategyReason(event.reason)}</small>
                    </span>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

export function StrategyBrief({ strategy, reportHref }: { strategy: StrategyProfile; reportHref?: string | null }) {
  return (
    <section className="strategy-section terminal-section" role="region" aria-label="策略说明">
      <header className="section-heading">
        <div>
          <span className="section-kicker"><Activity size={14} aria-hidden="true" />STRATEGY LOGIC</span>
          <h2>{strategy.name}</h2>
          <p>这里解释当前策略如何选证券和调仓；市场账户用于区分资金与持仓范围</p>
        </div>
        {reportHref ? <a className="text-link" href={reportHref} target="_blank" rel="noreferrer">查看完整周报</a> : null}
      </header>
      <div className="factor-grid">
        {strategy.factors.length === 0 ? <div className="terminal-empty">策略因子将在配置加载后显示</div> : strategy.factors.map((factor) => (
          <article className="factor-item" key={factor.key}>
            <header><b>{factor.label}</b><strong>{formatPercent(factor.weight)}</strong></header>
            <div className="factor-track"><span style={{ width: `${Math.max(3, factor.weight * 100)}%` }} /></div>
            <p>{factor.direction_label} · {factor.explanation}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export function RuntimeHistory({ rows, onSelect }: { rows: OrderRow[]; onSelect: SelectHandler }) {
  return (
    <details className="runtime-section terminal-section">
      <summary><span><Activity size={15} aria-hidden="true" />系统运行记录</span><small>最近 {rows.length} 次</small></summary>
      <div className="runtime-list">
        {rows.map((row) => (
          <button key={String(row.run_id)} type="button" onClick={(event) => onSelect(row, "运行记录", event.currentTarget)}>
            <span><b>{row.command}</b><small>{String(row.started_at || "-").replace("T", " ")}</small></span>
            <span className={row.status === "success" ? "positive" : "negative"}>{row.status || "未知"}</span>
          </button>
        ))}
      </div>
    </details>
  );
}
