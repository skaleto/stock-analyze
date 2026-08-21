import { useCallback, useEffect, useRef } from "react";
import { BookOpenText, Database, FileText, ShieldCheck } from "lucide-react";
import { fetchMultiAgentResearch } from "./api";
import { WorkspaceStatusBadge } from "./WorkspacePrimitives";
import { useWorkspaceResource } from "./useWorkspaceResource";
import type { MultiAgentResearchData, WorkspaceStatus } from "./workspaceTypes";

function count(value: number | null | undefined): string {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

function workspaceStatus(value: string): WorkspaceStatus {
  if (value === "available" || value === "complete") return "success";
  if (value === "completed_with_degradation") return "research";
  if (value === "empty") return "empty";
  return "unavailable";
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
  const previousRefreshToken = useRef(refreshToken);

  useEffect(() => {
    if (previousRefreshToken.current === refreshToken) return;
    previousRefreshToken.current = refreshToken;
    resource.refresh();
  }, [refreshToken, resource.refresh]);

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
