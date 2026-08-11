# Lark System Overview Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 原地把 Stock Analyze 飞书总览改造成图文结合、可扫读、可下钻的完成态系统说明。

**Architecture:** `docs/lark/system-overview-docx.xml` 保存可维护的富文档源稿；飞书用户身份负责覆盖写入，ECS 应用身份只用于给当前用户补权限。四张 Mermaid 画板嵌入 XML，写入后通过 Docx 回读和画板导出验证。

**Tech Stack:** Lark Docx XML、Mermaid whiteboard、lark-cli、Feishu OpenAPI。

---

### Task 1: 修正文档权限

**Files:**
- Read: `/etc/stock-analyze/secrets.env` on ECS

- [ ] 使用 ECS 文档应用为当前登录用户 `ou_df55c9a9b72a583125985dec204fc54b` 授予原文档 `full_access`。
- [ ] 使用 `lark-cli docs +fetch --detail full` 验证当前用户可以读取原文档。

### Task 2: 编写富文档源稿

**Files:**
- Create: `docs/lark/system-overview-docx.xml`

- [ ] 将第一屏改写为系统定位、范围边界和阅读地图。
- [ ] 嵌入总体架构、每日流水线、模型生命周期和调度时间轴四张 Mermaid 画板。
- [ ] 将数据源、能力和 timer 转换为语义明确的表格与连贯段落。
- [ ] 保留风险边界、人工动作、运维入口和技术附录。

### Task 3: 原地覆盖并验证

**Files:**
- Update: Lark document `TM0ydzbbAouZPCxK4jZcA2aGnxd`

- [ ] 执行 `lark-cli docs +update --command overwrite --content @docs/lark/system-overview-docx.xml --as user`。
- [ ] 回读 outline，确认章节层级和四个图解章节。
- [ ] 回读 full，确认存在四个 `whiteboard` token、表格和关键风险说明。
- [ ] 导出画板预览并确认文件非空。
