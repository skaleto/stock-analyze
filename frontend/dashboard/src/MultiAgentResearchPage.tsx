import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpenText, Database, FileText, ShieldCheck } from "lucide-react";
import { fetchMultiAgentResearch, fetchResearchUniverse } from "./api";
import ResearchUniverseInstrumentDrawer from "./ResearchUniverseInstrumentDrawer";
import { WorkspaceStatusBadge } from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type {
  MultiAgentResearchData,
  ResearchUniverseFundRecord,
  ResearchUniverseKind,
  ResearchUniversePage,
  ResearchUniverseRecord,
  WorkspaceStatus,
} from "./workspaceTypes";

function count(value: number | null | undefined): string {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

function workspaceStatus(value: string): WorkspaceStatus {
  if (value === "available" || value === "complete") return "success";
  if (value === "completed_with_degradation") return "research";
  if (value === "empty") return "empty";
  return "unavailable";
}

const RESEARCH_UNIVERSE_TABS: Array<{ kind: ResearchUniverseKind; label: string }> = [
  { kind: "a_share", label: "A股" },
  { kind: "exchange_fund", label: "场内基金" },
  { kind: "otc_fund", label: "场外基金" },
];

function isFundRecord(record: ResearchUniverseRecord): record is ResearchUniverseFundRecord {
  return "fundType" in record;
}

function pageSummary(data: ResearchUniversePage): string {
  const pages = Math.max(1, Math.ceil(data.total / data.pageSize));
  return `第 ${data.page} 页 / 共 ${pages} 页 · 共 ${count(data.total)} 条`;
}

export function MultiAgentResearchPage({
  refreshToken,
}: {
  refreshToken: number;
}) {
  const loader = useCallback(
    (signal: AbortSignal) => fetchMultiAgentResearch(signal),
    [],
  );
  const resource = useWorkspaceResource<MultiAgentResearchData>(
    "multi-agent-research",
    true,
    loader,
  );
  const [kind, setKind] = useState<ResearchUniverseKind>("a_share");
  const [draftQuery, setDraftQuery] = useState("");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [selectedRecord, setSelectedRecord] = useState<ResearchUniverseRecord | null>(null);
  const drawerTriggerRef = useRef<HTMLElement | null>(null);
  const browserKey = `research-universe:${kind}:${query}:${scope ?? ""}:${page}:50`;
  const browserLoader = useCallback(
    (signal: AbortSignal) => fetchResearchUniverse({
      kind,
      query,
      scope,
      page,
      pageSize: 50,
    }, signal),
    [kind, page, query, scope],
  );
  const browser = useWorkspaceResource<ResearchUniversePage>(
    browserKey,
    true,
    browserLoader,
    { keepPreviousData: true },
  );
  const previousRefreshToken = useRef(refreshToken);

  useEffect(() => {
    if (previousRefreshToken.current === refreshToken) return;
    previousRefreshToken.current = refreshToken;
    resource.refresh();
    browser.refresh();
  }, [browser.refresh, refreshToken, resource.refresh]);

  const changeKind = useCallback((nextKind: ResearchUniverseKind) => {
    setKind(nextKind);
    setDraftQuery("");
    setQuery("");
    setScope(null);
    setPage(1);
    setSelectedRecord(null);
    drawerTriggerRef.current = null;
  }, []);

  const submitSearch = useCallback((event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setQuery(draftQuery.trim());
    setPage(1);
  }, [draftQuery]);

  const clearSearch = useCallback(() => {
    setDraftQuery("");
    setQuery("");
    setScope(null);
    setPage(1);
  }, []);

  const openInstrument = useCallback((record: ResearchUniverseRecord, trigger: HTMLElement) => {
    drawerTriggerRef.current = trigger;
    setSelectedRecord(record);
  }, []);

  const closeInstrument = useCallback(() => {
    const trigger = drawerTriggerRef.current;
    setSelectedRecord(null);
    drawerTriggerRef.current = null;
    window.requestAnimationFrame(() => trigger?.focus());
  }, []);

  if (resource.loading && !resource.data) {
    return (
      <div className="skeleton-grid" aria-label="多角色投研加载中">
        <div /><div /><div /><div />
      </div>
    );
  }
  if (!resource.data) {
    return (
      <div className="error-banner" role="alert">
        多角色投研不可用：{resource.error ?? "未知错误"}
      </div>
    );
  }

  const data = resource.data;
  const latest = data.latestRun;
  const scopeCounts = data.universe.aShare.scopeCounts;
  const sourceCounts = data.universe.funds.sourceCounts;
  const browserData = browser.data;
  const displayedKind = browserData?.kind ?? kind;
  const browserPages = browserData ? Math.max(1, Math.ceil(browserData.total / browserData.pageSize)) : 1;
  const canGoPrevious = Boolean(browserData && page > 1);
  const canGoNext = Boolean(browserData && page < browserPages);
  return (
    <section className="workspace-page multi-agent-research-page" aria-label="多角色投研">
      <header className="section-heading">
        <div>
          <h1>多角色投研</h1>
          <p>项目内点时证据 · 审计留痕 · 仅研究</p>
        </div>
        <WorkspaceStatusBadge status={workspaceStatus(data.status)} />
      </header>

      <div className="stale-banner" role="status">
        <ShieldCheck size={16} aria-hidden="true" />
        研究专用：不会创建订单、修改正式账户或触发模型注册。
      </div>

      <section className="workspace-detail-panel" aria-label="研究数据范围">
        <header>
          <div>
            <h2><Database size={17} aria-hidden="true" /> 研究数据范围</h2>
            <p>目录日期：{data.universe.asOf ?? "未生成"}</p>
          </div>
        </header>
        <div className="workspace-detail-body">
          <div className="metric-grid">
            <article className="metric-card">
              <span>CSI1000</span>
              <strong>{count(scopeCounts.csi1000)}</strong>
              <small>研究成员</small>
            </article>
            <article className="metric-card">
              <span>场内基金目录</span>
              <strong>{count(sourceCounts.exchange)}</strong>
              <small>只读研究候选</small>
            </article>
            <article className="metric-card">
              <span>场外基金目录</span>
              <strong>{count(sourceCounts.otc)}</strong>
              <small>非交易研究对照</small>
            </article>
          </div>
        </div>
      </section>

      <section className="workspace-detail-panel research-universe-panel" aria-label="研究目录浏览">
        <header>
          <div>
            <h2><Database size={17} aria-hidden="true" /> 研究目录浏览</h2>
            <p>服务端分页 · 仅读取已落盘目录快照 · 可按代码、名称与研究范围检索</p>
          </div>
          {kind === "otc_fund" ? <span className="research-universe-otc-label">非交易研究对照</span> : null}
        </header>
        <div className="workspace-detail-body research-universe-body">
          <div className="research-universe-tabs" role="tablist" aria-label="研究目录类型">
            {RESEARCH_UNIVERSE_TABS.map((tab) => (
              <button
                key={tab.kind}
                type="button"
                role="tab"
                aria-selected={kind === tab.kind}
                className={kind === tab.kind ? "active" : undefined}
                onClick={() => changeKind(tab.kind)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <form className="research-universe-controls" onSubmit={submitSearch}>
            <input
              type="search"
              aria-label="搜索研究目录"
              value={draftQuery}
              maxLength={80}
              placeholder={kind === "a_share" ? "搜索证券代码或名称" : "搜索基金代码或名称"}
              onChange={(event) => setDraftQuery(event.target.value)}
            />
            <select
              aria-label="研究范围"
              value={scope ?? ""}
              onChange={(event) => {
                setScope(event.target.value || null);
                setPage(1);
              }}
              disabled={!browserData || browserData.scopeOptions.length === 0}
            >
              <option value="">全部研究范围</option>
              {(browserData?.scopeOptions ?? []).map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
            <button className="research-universe-submit" type="submit">搜索</button>
            <button className="research-universe-clear" type="button" onClick={clearSearch} disabled={!draftQuery && !query && !scope}>
              清除
            </button>
          </form>

          {browser.loading && !browserData ? <p className="research-universe-state">研究目录加载中…</p> : null}
          {!browserData && browser.error ? <p className="research-universe-state error">研究目录读取失败：{browser.error}</p> : null}
          {browserData?.status === "unavailable" ? <p className="research-universe-state">研究目录快照暂不可用。</p> : null}
          {browserData?.status === "available" && browserData.total === 0 ? <p className="research-universe-state">没有匹配的研究目录记录。</p> : null}
          {browserData?.status === "available" && browserData.total > 0 ? (
            <>
              <div className="research-universe-table-wrap">
                <table className="research-universe-table" aria-label="研究目录结果">
                  <thead>
                    {displayedKind === "a_share" ? (
                      <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th>研究范围</th>
                        <th>成员日期</th>
                      </tr>
                    ) : (
                      <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th>基金类型</th>
                        <th>基准</th>
                        <th>跨境范围</th>
                        <th>分类</th>
                        <th>研究状态</th>
                      </tr>
                    )}
                  </thead>
                  <tbody>
                    {browserData.records.map((record) => (
                      <tr
                        key={record.code}
                        className="research-universe-row"
                        tabIndex={0}
                        title="点击查看投研详情；按 Enter 或空格键打开"
                        onClick={(event) => openInstrument(record, event.currentTarget)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            openInstrument(record, event.currentTarget);
                          }
                        }}
                      >
                        <td><strong>{record.code}</strong></td>
                        <td>{record.name || "—"}</td>
                        {isFundRecord(record) ? (
                          <>
                            <td>{record.fundType || "—"}</td>
                            <td>{record.benchmark || "—"}</td>
                            <td>{record.overseasScope ?? "未分类"}</td>
                            <td>{record.classificationStatus || "—"}</td>
                            <td>{record.tradability === "otc_non_tradable_research_only" ? "非交易研究对照" : "场内研究候选"}</td>
                          </>
                        ) : (
                          <>
                            <td>{record.researchScopes.join(" · ") || "未分类"}</td>
                            <td>{record.membershipDate ?? "未记录"}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="research-universe-pagination">
                <span>{pageSummary(browserData)}{browser.loading ? " · 正在更新" : ""}</span>
                <div>
                  <button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={!canGoPrevious}>上一页</button>
                  <button type="button" onClick={() => setPage((current) => current + 1)} disabled={!canGoNext}>下一页</button>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </section>

      {selectedRecord ? <ResearchUniverseInstrumentDrawer kind={kind} record={selectedRecord} onClose={closeInstrument} /> : null}

      <section className="workspace-detail-panel" aria-label="最新多角色投研">
        <header>
          <div>
            <h2><BookOpenText size={17} aria-hidden="true" /> 最新多角色投研</h2>
            <p>Dashboard 只读取已完成产物；运行须通过命令行显式发起。</p>
          </div>
          {latest ? <WorkspaceStatusBadge status={workspaceStatus(latest.status)} /> : null}
        </header>
        <div className="workspace-detail-body">
          {!latest ? (
            <p>尚无已完成投研。请先准备项目内研究快照，再显式运行多角色投研命令。</p>
          ) : (
            <>
              <div className="metric-grid">
                <article className="metric-card">
                  <span>研究对象</span>
                  <strong>{latest.instrument.name || latest.instrument.code}</strong>
                  <small>{latest.market} · {latest.instrument.code}</small>
                </article>
                <article className="metric-card">
                  <span>模型</span>
                  <strong>{latest.model ?? "未记录"}</strong>
                  <small>{latest.createdAt?.replace("T", " ") ?? "未记录"}</small>
                </article>
                <article className="metric-card">
                  <span>降级角色</span>
                  <strong>{latest.degradedRoles.length}</strong>
                  <small>{latest.degradedRoles.join("、") || "无"}</small>
                </article>
              </div>
              <pre className="workspace-code-block">{latest.digest || "未生成简报"}</pre>
              <a className="text-link" href={`/${latest.reportPath}`}>
                <FileText size={15} aria-hidden="true" /> 查看完整审计报告
              </a>
            </>
          )}
        </div>
      </section>
    </section>
  );
}
