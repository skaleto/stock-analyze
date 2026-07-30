import type { ReactElement } from "react";
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

function expectRenderError(ui: ReactElement, message: string) {
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    expect(() => render(ui)).toThrowError(message);
  } finally {
    consoleError.mockRestore();
  }
}

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
      name: /数据准备 成功 6 个来源 72 个特征/,
    });
    const trainingStage = within(flow).getByRole("button", {
      name: /模型训练 研究中 4 个版本 最近训练 07-30/,
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
      name: /模型训练 研究中 4 个版本 最近训练 07-30/,
    });
    expect(within(trainingStage).getByText("研究中")).toHaveClass(
      "status-research",
    );
    expect(within(trainingStage).queryByText("失败")).not.toBeInTheDocument();
  });

  it("rejects duplicate stage keys", () => {
    expectRenderError(
      <StageFlow
        ariaLabel="模型研究进度"
        selectedKey="data"
        onSelect={vi.fn()}
        stages={[
          ...stages,
          { ...stages[0], label: "重复的数据准备" },
        ]}
      />,
      'StageFlow received duplicate stage key "data".',
    );
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

  it("normalizes explicit-zone timestamps to Asia/Shanghai", () => {
    const { rerender } = render(
      <DetailPanel
        title="模型训练"
        status="running"
        updatedAt="2026-07-30T09:30:00Z"
      >
        <p>训练详情</p>
      </DetailPanel>,
    );
    expect(screen.getByText("2026-07-30 17:30:00")).toHaveAttribute(
      "datetime",
      "2026-07-30T09:30:00Z",
    );

    rerender(
      <DetailPanel
        title="模型训练"
        status="running"
        updatedAt="2026-07-30T18:30:00+09:00"
      >
        <p>训练详情</p>
      </DetailPanel>,
    );
    expect(screen.getByText("2026-07-30 17:30:00")).toHaveAttribute(
      "datetime",
      "2026-07-30T18:30:00+09:00",
    );
  });

  it("shows malformed timestamp text unchanged", () => {
    render(
      <DetailPanel
        title="模型训练"
        status="running"
        updatedAt="brokenTtimestamp"
      >
        <p>训练详情</p>
      </DetailPanel>,
    );

    expect(screen.getByText("brokenTtimestamp")).toBeInTheDocument();
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

  it("rejects an empty column definition", () => {
    expectRenderError(
      <BoundedTable<number>
        rows={[]}
        columns={[]}
        rowKey={(row) => String(row)}
        emptyLabel="暂无记录"
      />,
      "BoundedTable requires at least one column.",
    );
  });

  it("rejects duplicate column keys", () => {
    expectRenderError(
      <BoundedTable
        rows={[1]}
        columns={[
          ...columns,
          { key: "index", label: "重复序号", render: String },
        ]}
        rowKey={(row) => String(row)}
        emptyLabel="暂无记录"
      />,
      'BoundedTable received duplicate column key "index".',
    );
  });

  it("rejects duplicate row keys within the rendered bound", () => {
    expectRenderError(
      <BoundedTable
        rows={[1, 2]}
        columns={columns}
        rowKey={() => "same-row"}
        emptyLabel="暂无记录"
      />,
      'BoundedTable received duplicate row key "same-row".',
    );
  });
});
