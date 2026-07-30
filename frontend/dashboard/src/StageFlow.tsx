import { ArrowRight } from "lucide-react";
import { WorkspaceStatusBadge } from "./WorkspacePrimitives";
import type { WorkspaceStage } from "./workspaceTypes";

export function StageFlow({
  stages,
  selectedKey,
  ariaLabel,
  onSelect,
}: {
  stages: WorkspaceStage[];
  selectedKey: string;
  ariaLabel: string;
  onSelect: (key: string) => void;
}) {
  const stageKeys = new Set<string>();
  for (const stage of stages) {
    if (stageKeys.has(stage.key)) {
      throw new Error(`StageFlow received duplicate stage key "${stage.key}".`);
    }
    stageKeys.add(stage.key);
  }

  return (
    <div className="stage-flow" role="group" aria-label={ariaLabel}>
      {stages.map((stage, index) => (
        <div className="stage-flow-item" key={stage.key}>
          <button
            type="button"
            className={selectedKey === stage.key
              ? "stage-node active"
              : "stage-node"}
            aria-pressed={selectedKey === stage.key}
            onClick={() => onSelect(stage.key)}
          >
            <span className="stage-index">
              {String(index + 1).padStart(2, "0")}
            </span>
            <strong>{stage.label}</strong>
            <WorkspaceStatusBadge status={stage.status} />
            <b>{stage.primary}</b>
            <small>{stage.secondary}</small>
          </button>
          {index < stages.length - 1 ? (
            <ArrowRight
              className="stage-link"
              size={17}
              aria-hidden="true"
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}
