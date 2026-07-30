import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StageFlow } from "./StageFlow";
import {
  BoundedTable,
  DetailPanel,
  WorkspaceStatusBadge,
} from "./WorkspacePrimitives";
import type { WorkspaceStatus } from "./workspaceTypes";

const stages = [
  {
    key: "data",
    label: "数据准备",
    status: "success" as const,
    primary: "6 个来源",
    secondary: "72 个特征",
  },
  {
    key: "training",
    label: "模型训练",
    status: "research" as const,
    primary: "4 个版本",
    secondary: "最近训练 07-30",
  },
];

describe("StageFlow", () => {
  it("selects a numbered stage and exposes the active stage", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <StageFlow
        ariaLabel="模型研究进度"
        selectedKey="training"
        onSelect={onSelect}
        stages={stages}
      />,
    );

    const flow = screen.getByRole("group", { name: "模型研究进度" });
    const dataStage = within(flow).getByRole("button", {
      name: /数据准备 6 个来源/,
    });
    const trainingStage = within(flow).getByRole("button", {
      name: /模型训练 4 个版本/,
    });
    expect(dataStage).toHaveAttribute("aria-pressed", "false");
    expect(trainingStage).toHaveAttribute("aria-pressed", "true");
    expect(trainingStage).toHaveClass("active");
    expect(within(dataStage).getByText("01")).toBeInTheDocument();
    expect(within(trainingStage).getByText("02")).toBeInTheDocument();
    expect(flow.querySelectorAll(".stage-link")).toHaveLength(1);

    await user.click(dataStage);
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith("data");
  });

  it("presents research as research rather than failure", () => {
    render(
      <StageFlow
        ariaLabel="模型研究进度"
        selectedKey="training"
        onSelect={vi.fn()}
        stages={stages}
      />,
    );

    const trainingStage = screen.getByRole("button", {
      name: /模型训练 4 个版本/,
    });
    expect(within(trainingStage).getByText("研究中")).toHaveClass(
      "status-research",
    );
    expect(within(trainingStage).queryByText("失败")).not.toBeInTheDocument();
  });
});

describe("WorkspaceStatusBadge", () => {
  const statuses: Array<[WorkspaceStatus, string]> = [
    ["success", "成功"],
    ["running", "运行中"],
    ["waiting_schedule", "等待计划时间"],
    ["waiting_upstream", "等待上游"],
    ["failed", "失败"],
    ["skipped", "已跳过"],
    ["research", "研究中"],
    ["empty", "暂无数据"],
    ["unavailable", "状态不可用"],
  ];

  it.each(statuses)("renders %s with its Chinese status and an icon", (
    status,
    label,
  ) => {
    const { container } = render(<WorkspaceStatusBadge status={status} />);

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });
});

describe("DetailPanel", () => {
  it("labels the detail region and exposes its optional close action", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <DetailPanel
        title="模型训练"
        status="running"
        updatedAt="2026-07-30T17:30:00"
        onClose={onClose}
      >
        <p>训练详情</p>
      </DetailPanel>,
    );

    const panel = screen.getByRole("region", { name: "模型训练详情" });
    expect(within(panel).getByText("运行中")).toBeInTheDocument();
    expect(within(panel).getByText("2026-07-30 17:30:00")).toBeInTheDocument();
    await user.click(
      within(panel).getByRole("button", { name: "关闭模型训练详情" }),
    );
    expect(onClose).toHaveBeenCalledOnce();

    rerender(
      <DetailPanel title="模型训练" status="empty">
        <p>暂无训练详情</p>
      </DetailPanel>,
    );
    expect(
      screen.queryByRole("button", { name: "关闭模型训练详情" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("暂无数据")).toBeInTheDocument();
  });
});

describe("BoundedTable", () => {
  const columns = [
    { key: "index", label: "序号", render: (row: number) => String(row) },
  ];

  it("renders at most the first 20 rows", () => {
    render(
      <BoundedTable
        rows={Array.from({ length: 25 }, (_, index) => index + 1)}
        columns={columns}
        rowKey={(row) => String(row)}
        emptyLabel="暂无记录"
      />,
    );

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(21);
    expect(within(table).getByRole("cell", { name: "20" })).toBeInTheDocument();
    expect(
      within(table).queryByRole("cell", { name: "21" }),
    ).not.toBeInTheDocument();
  });

  it("renders one spanning empty row when no data is available", () => {
    render(
      <BoundedTable
        rows={[] as number[]}
        columns={[
          ...columns,
          { key: "state", label: "状态", render: () => "成功" },
        ]}
        rowKey={(row) => String(row)}
        emptyLabel="暂无记录"
      />,
    );

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByRole("cell", { name: "暂无记录" }))
      .toHaveAttribute("colspan", "2");
  });
});
