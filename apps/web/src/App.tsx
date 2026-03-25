import { ChangeEvent, FormEvent, KeyboardEvent, ReactNode, useEffect, useMemo, useState } from "react";
import {
  approvePlan,
  createTask,
  createTeamRun,
  exportDeliverable,
  getBoard,
  listAgents,
  listTeamRuns,
  rejectPlan,
  sendRequest,
  updateRunAgents,
  updateTask,
  uploadFile,
  deleteTeamRun,
} from "./api";
import type { FileUploadItem, OutputType, OversightMode, TeamBoardSnapshot, TeamTask, TeamRunSnapshot } from "./types";

const AUTO_REVIEW_PRESETS = [
  { label: "보수적 (2회)", value: 2 },
  { label: "균형 (4회)", value: 4 },
  { label: "집중 (6회)", value: 6 },
] as const;

const TASK_STATUS_COLUMNS = [
  { key: "todo",        label: "할 일",    colorClass: "col-todo" },
  { key: "in_progress", label: "진행 중",  colorClass: "col-in-progress" },
  { key: "review",      label: "검토 대기", colorClass: "col-review" },
  { key: "done",        label: "완료",     colorClass: "col-done" },
];

const AGENT_MAP: Record<string, { label: string; icon: string; initials: string }> = {
  planner: { label: "기획자",   icon: "📋", initials: "P" },
  writer:  { label: "작성자",   icon: "✍️", initials: "W" },
  critic:  { label: "비평가",   icon: "🧐", initials: "C" },
  qa:      { label: "품질 검증", icon: "🛡️", initials: "Q" },
  manager: { label: "매니저",   icon: "��‍💼", initials: "M" },
};

function statusLabel(status?: string) {
  if (!status) return "대기";
  const map: Record<string, string> = {
    in_progress: "진행 중", done: "완료", todo: "할 일",
    review: "검토 중", idle: "유휴", running: "실행 중",
    pending: "대기", approved: "승인됨", rejected: "반려됨",
    error: "오류",
  };
  return map[status] ?? status.replace(/_/g, " ");
}

function agentLabel(handle?: string | null) {
  if (!handle) return "시스템";
  return AGENT_MAP[handle]?.label || handle;
}
function agentIcon(handle?: string | null) {
  if (!handle) return "🤖";
  return AGENT_MAP[handle]?.icon || "🤖";
}
function agentInitials(handle?: string | null) {
  if (!handle) return "SY";
  return AGENT_MAP[handle]?.initials || handle.slice(0, 2).toUpperCase();
}

/** Returns the CSS class name for an agent handle */
function agentClass(handle?: string | null): string {
  if (!handle) return "system";
  const known = ["planner", "writer", "critic", "qa", "manager"];
  return known.includes(handle) ? handle : "system";
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g;
  let lastIndex = 0;
  let key = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(<code key={`inline-code-${key++}`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={`inline-strong-${key++}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("[")) {
      const labelMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
      if (labelMatch) {
        nodes.push(
          <a key={`inline-link-${key++}`} href={labelMatch[2]} target="_blank" rel="noreferrer">
            {labelMatch[1]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    } else {
      nodes.push(token);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

function MarkdownPreview({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push(
        <pre key={`code-${i}`} className="md-code-block">
          <code>{codeLines.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const headingText = headingMatch[2];
      const Tag = `h${Math.min(level, 6)}` as keyof JSX.IntrinsicElements;
      blocks.push(<Tag key={`heading-${i}`}>{renderInlineMarkdown(headingText)}</Tag>);
      i += 1;
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      const quoteLines: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push(<blockquote key={`quote-${i}`}>{renderInlineMarkdown(quoteLines.join(" "))}</blockquote>);
      continue;
    }

    if (/^[-*+]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*+]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${i}`}>
          {items.map((item, idx) => (
            <li key={`ul-${i}-${idx}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ol key={`ol-${i}`}>
          {items.map((item, idx) => (
            <li key={`ol-${i}-${idx}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const paragraphLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith("```") &&
      !/^#{1,6}\s+/.test(lines[i].trim()) &&
      !/^>\s?/.test(lines[i].trim()) &&
      !/^[-*+]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim())
    ) {
      paragraphLines.push(lines[i].trim());
      i += 1;
    }
    blocks.push(<p key={`p-${i}`}>{renderInlineMarkdown(paragraphLines.join(" "))}</p>);
  }

  return <div className="markdown-preview">{blocks}</div>;
}

export default function App() {
  const [runs, setRuns] = useState<TeamRunSnapshot[]>([]);
  const [activeRunId, setActiveRunId] = useState<string>("");
  const [board, setBoard] = useState<TeamBoardSnapshot | null>(null);
  const [agentHandles, setAgentHandles] = useState<string[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [outputType, setOutputType] = useState<OutputType>("pptx");
  const [oversightMode, setOversightMode] = useState<OversightMode>("auto");
  const [presetRounds, setPresetRounds] = useState<number>(4);
  const [files, setFiles] = useState<FileUploadItem[]>([]);
  const [composerText, setComposerText] = useState("");
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [error, setError] = useState("");
  const [sendingRequest, setSendingRequest] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [isPlanModalOpen, setIsPlanModalOpen] = useState(false);
  const [isFilesModalOpen, setIsFilesModalOpen] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [createTaskForm, setCreateTaskForm] = useState({ title: "", description: "" });
  const [isDarkMode, setIsDarkMode] = useState(() => {
    return localStorage.getItem("theme") === "dark" ||
      (!localStorage.getItem("theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
  });

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add("dark");
      localStorage.setItem("theme", "dark");
    } else {
      document.documentElement.classList.remove("dark");
      localStorage.setItem("theme", "light");
    }
  }, [isDarkMode]);

  useEffect(() => {
    void refreshAgents();
    void refreshRuns();
  }, []);

  useEffect(() => {
    if (!activeRunId) { setBoard(null); return; }
    let cancelled = false;
    const fetchBoard = async () => {
      try {
        const data = await getBoard(activeRunId);
        if (!cancelled) setBoard(data);
      } catch {
        if (!cancelled) setError("워크스페이스 동기화 실패");
      }
    };
    void fetchBoard();
    const interval = window.setInterval(() => { void fetchBoard(); }, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [activeRunId]);

  const selectedTask = useMemo<TeamTask | undefined>(
    () => board?.tasks?.find((task) => task.id === selectedTaskId),
    [board, selectedTaskId],
  );

  useEffect(() => {
    const agents = board?.run?.selected_agents ?? [];
    setSelectedAgents(agents);
  }, [board?.run?.id, board?.run?.selected_agents]);

  async function refreshAgents() {
    try {
      const data = await listAgents();
      setAgentHandles(data.agents.map((a) => a.handle));
    } catch { /* silent */ }
  }

  async function refreshRuns() {
    setLoadingRuns(true);
    try {
      const items = await listTeamRuns();
      setRuns(items);
      if (!activeRunId && items[0]) setActiveRunId(items[0].id);
    } catch {
      setError("목록 갱신 실패");
    } finally {
      setLoadingRuns(false);
    }
  }

  async function handleCreateRun() {
    setLoadingRuns(true);
    try {
      const snapshot = await createTeamRun({ requestedBy: "USER", oversightMode, outputType, autoReviewMaxRounds: presetRounds });
      setBoard(snapshot);
      setActiveRunId(snapshot.run.id);
      void refreshRuns();
    } catch {
      setError("워크스페이스 생성 실패");
    } finally {
      setLoadingRuns(false);
    }
  }

  async function handleDeleteSelected() {
    if (!selectedRunIds.length) return;
    if (!window.confirm("선택한 항목을 삭제하시겠습니까?")) return;
    try {
      for (const id of selectedRunIds) await deleteTeamRun(id);
      setSelectedRunIds([]);
      if (selectedRunIds.includes(activeRunId)) setActiveRunId("");
      void refreshRuns();
    } catch {
      setError("삭제 작업 중 오류 발생");
    }
  }

  async function handleSendRequest(e?: FormEvent) {
    if (e) e.preventDefault();
    if (!activeRunId || !composerText.trim() || sendingRequest) return;
    setSendingRequest(true);
    try {
      const snapshot = await sendRequest(activeRunId, {
        text: composerText.trim(), senderName: "USER",
        outputType, autoReviewMaxRounds: presetRounds,
        sourceFileIds: files.map((item) => item.id),
      });
      setBoard(snapshot);
      setComposerText("");
      setFiles([]);
    } catch {
      setError("메시지 전송 실패");
    } finally {
      setSendingRequest(false);
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      if (e.nativeEvent.isComposing) return;
      e.preventDefault();
      void handleSendRequest();
    }
  };

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const fileList = event.target.files;
    if (!fileList?.length) return;
    try {
      for (const file of Array.from(fileList)) {
        const item = await uploadFile(file);
        setFiles((prev) => [...prev, item]);
      }
    } catch {
      setError("파일 업로드 실패");
    } finally {
      event.target.value = "";
    }
  }

  async function handleExport(format: OutputType) {
    if (!activeRunId) return;
    try {
      const { download_path } = await exportDeliverable(activeRunId, format);
      window.location.href = download_path;
    } catch {
      setError("파일 내보내기 실패");
    }
  }

  async function handleTaskAction(action: string) {
    if (!selectedTaskId) return;
    setPlanLoading(true);
    try {
      const snapshot = await updateTask(selectedTaskId, { action });
      setBoard(snapshot);
      setSelectedTaskId(null);
    } catch {
      setError("작업 업데이트 실패");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleSaveAgents() {
    if (!activeRunId) return;
    setPlanLoading(true);
    try {
      const snapshot = await updateRunAgents(activeRunId, selectedAgents);
      setBoard(snapshot);
    } catch {
      setError("에이전트 설정 저장 실패");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handlePlanApprove() {
    if (!activeRunId) return;
    setPlanLoading(true);
    try {
      const snapshot = await approvePlan(activeRunId);
      setBoard(snapshot);
    } catch {
      setError("계획 승인 실패");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handlePlanReject() {
    if (!activeRunId) return;
    setPlanLoading(true);
    try {
      const snapshot = await rejectPlan(activeRunId);
      setBoard(snapshot);
    } catch {
      setError("계획 반려 실패");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleCreateTask(e: FormEvent) {
    e.preventDefault();
    if (!activeRunId || !createTaskForm.title.trim()) return;
    setPlanLoading(true);
    try {
      const snapshot = await createTask(activeRunId, {
        title: createTaskForm.title.trim(),
        description: createTaskForm.description.trim(),
        ownerHandle: "planner",
        artifactGoal: "",
        priority: 5,
        reviewRequired: false,
      });
      setBoard(snapshot);
      setCreateTaskForm({ title: "", description: "" });
    } catch {
      setError("작업 생성 실패");
    } finally {
      setPlanLoading(false);
    }
  }

  const latestActivities = useMemo(() => [...(board?.activity ?? [])].reverse(), [board?.activity]);
  const latestMessages   = useMemo(() => [...(board?.items   ?? [])].reverse(), [board?.items]);

  const taskColumns = useMemo(() => {
    const tasks = board?.tasks ?? [];
    return TASK_STATUS_COLUMNS.map((column) => ({
      ...column,
      tasks: tasks.filter((task) => {
        if (!task.status) return column.key === "todo";
        if (task.status === column.key) return true;
        if (column.key === "review" && task.review_state === "reviewed") return true;
        return false;
      }),
    }));
  }, [board?.tasks]);

  const activeAgents = useMemo(() => {
    const active = new Set<string>();
    board?.tasks?.forEach((t) => { if (t.status === "in_progress" && t.owner_handle) active.add(t.owner_handle); });
    return active;
  }, [board?.tasks]);

  const runStatus        = board?.run?.status      || "idle";
  const runPlanStatus    = board?.run?.plan_status  || "pending";
  const currentOutputType = board?.run?.output_type || outputType;

  // ── Render ─────────────────────────────────────────────────────
  return (
    <div className="app-container">
      {/* ── Sidebar ── */}
      <aside className="app-sidebar">
        <header className="sidebar-brand">
          <div className="brand-icon">D</div>
          <div className="brand-text">
            <h1>DocFlow AI</h1>
            <span>Workspace Console</span>
          </div>
          <button className="theme-switcher" onClick={() => setIsDarkMode(!isDarkMode)} title="테마 전환">
            {isDarkMode ? "☀️" : "🌙"}
          </button>
        </header>

        <nav className="sidebar-nav">
          {/* Config */}
          <div className="nav-group">
            <h2 className="nav-label">워크스페이스 설정</h2>
            <div className="config-card">
              <div className="config-field">
                <label>개입 모드</label>
                <select value={oversightMode} onChange={(e) => setOversightMode(e.target.value as OversightMode)}>
                  <option value="auto">자동 (Auto)</option>
                  <option value="manual">수동 (Manual)</option>
                </select>
              </div>
              <div className="config-field">
                <label>산출물 형식</label>
                <select value={outputType} onChange={(e) => setOutputType(e.target.value as OutputType)}>
                  <option value="pptx">PPTX 슬라이드</option>
                  <option value="docx">DOCX 문서</option>
                  <option value="xlsx">XLSX 데이터</option>
                </select>
              </div>
              <div className="config-field">
                <label>검토 강도</label>
                <select value={presetRounds} onChange={(e) => setPresetRounds(Number(e.target.value))}>
                  {AUTO_REVIEW_PRESETS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </div>
              <button className="action-button primary" onClick={handleCreateRun} disabled={loadingRuns}>
                {loadingRuns ? "생성 중…" : "+ 새 워크스페이스"}
              </button>
            </div>
          </div>

          {/* Agents */}
          <div className="nav-group">
            <h2 className="nav-label">참여 에이전트</h2>
            <div className="agent-selection-list">
              {agentHandles.map((handle) => (
                <label key={handle} className={`agent-checkbox-item ${activeAgents.has(handle) ? "agent-active" : ""}`}>
                  <input
                    type="checkbox"
                    checked={selectedAgents.includes(handle)}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...selectedAgents, handle]
                        : selectedAgents.filter((a) => a !== handle);
                      setSelectedAgents(Array.from(new Set(next)));
                    }}
                  />
                  <div className="agent-checkbox-content">
                    <span className={`agent-dot ${handle}`} />
                    <span className="agent-name">{agentLabel(handle)}</span>
                    {activeAgents.has(handle) && <div className="status-indicator" />}
                  </div>
                </label>
              ))}
              <button className="action-button ghost" onClick={handleSaveAgents} disabled={planLoading || !activeRunId}>
                설정 적용
              </button>
            </div>
          </div>

          {/* Workspace list */}
          <div className="nav-group flex-fill">
            <h2 className="nav-label flex-header">
              워크스페이스 목록
              <button className="delete-trigger" onClick={handleDeleteSelected} disabled={!selectedRunIds.length}>삭제</button>
            </h2>
            <div className="run-entry-list scrollable">
              {runs.length === 0 && <span style={{ fontSize: 11, color: "var(--sb-muted)", padding: "8px 6px" }}>워크스페이스가 없습니다</span>}
              {runs.map((run) => (
                <div
                  key={run.id}
                  className={`run-entry-card ${run.id === activeRunId ? "active" : ""}`}
                  onClick={() => setActiveRunId(run.id)}
                >
                  <input
                    type="checkbox"
                    checked={selectedRunIds.includes(run.id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...selectedRunIds, run.id]
                        : selectedRunIds.filter((id) => id !== run.id);
                      setSelectedRunIds(next);
                    }}
                  />
                  <div className="run-entry-info">
                    <strong className="run-title">{run.title || "무제 작업"}</strong>
                    <span className="run-status">{statusLabel(run.status)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </nav>
      </aside>

      {/* ── Main ── */}
      <main className="app-main">
        <header className="workspace-header">
          <div className="header-meta">
            <h2 className="workspace-title">{board?.run?.title || "워크스페이스를 선택하세요"}</h2>
            <p className="workspace-desc">{board?.run?.request_text || "실시간 에이전트 실행 상태를 모니터링합니다."}</p>
          </div>
          <div className="header-controls">
            <div className="status-pill-group">
              <span className={`status-pill ${runStatus}`}>
                {runStatus === "running" && <span className="pill-dot" />}
                {runStatus.toUpperCase()}
              </span>
              <span className={`status-pill ${runPlanStatus}`}>
                📋 {statusLabel(runPlanStatus)}
              </span>
              <span className="status-pill info">
                📄 {currentOutputType.toUpperCase()}
              </span>
            </div>
            <div className="button-row">
              <button className="control-button" onClick={() => setIsFilesModalOpen(true)}>
                📎 파일 {files.length > 0 && `(${files.length})`}
              </button>
              <button className="control-button primary" onClick={() => setIsPlanModalOpen(true)}>
                계획 관리
              </button>
            </div>
          </div>
        </header>

        {error && (
          <div className="alert-banner" style={{ margin: "12px 32px 0" }}>
            ⚠️ {error}
            <button
              onClick={() => setError("")}
              style={{ marginLeft: 12, background: "none", border: "none", cursor: "pointer", fontWeight: 700, color: "inherit" }}
            >
              ✕
            </button>
          </div>
        )}

        {/* Content */}
        {!board ? (
          <div className="welcome-screen">
            <div className="welcome-icon">🤖</div>
            <h3>DocFlow AI에 오신 것을 환영합니다</h3>
            <p>왼쪽 설정에서 새 워크스페이스를 만들거나 기존 워크스페이스를 선택하세요.</p>
          </div>
        ) : (
          <div className="workspace-content">
            <div className="content-layout-grid">
              {/* Left column */}
              <div className="main-viewport">
                {/* Output panel */}
                <section className="dashboard-panel output-panel">
                  <header className="panel-top">
                    <h3>최종 결과물</h3>
                    <div className="export-tools">
                      <button onClick={() => handleExport("pptx")} disabled={!board.deliverable || currentOutputType !== "pptx"}>PPTX</button>
                      <button onClick={() => handleExport("docx")} disabled={!board.deliverable || currentOutputType !== "docx"}>DOCX</button>
                      <button onClick={() => handleExport("xlsx")} disabled={!board.deliverable || currentOutputType !== "xlsx"}>XLSX</button>
                    </div>
                  </header>
                  <div className="panel-body deliverable-view">
                    {board.deliverable?.content
                      ? <MarkdownPreview content={board.deliverable.content} />
                      : (
                        <div className="empty-state">
                          <div className="empty-state-icon">📄</div>
                          <p>아직 생성된 결과물이 없습니다.<br />AI 팀에게 작업을 요청해보세요.</p>
                        </div>
                      )
                    }
                  </div>
                </section>

                {/* Kanban */}
                <section className="dashboard-panel board-panel">
                  <header className="panel-top">
                    <h3>작업 보드</h3>
                    <div className="agent-avatars">
                      {board.run?.selected_agents?.map((h) => (
                        <span key={h} className="agent-avatar-chip">{agentIcon(h)} {agentLabel(h)}</span>
                      ))}
                    </div>
                  </header>
                  <div className="kanban-board">
                    {taskColumns.map((col) => (
                      <div key={col.key} className="kanban-column">
                        <header className={`column-header ${col.colorClass}`}>
                          {col.label}
                          <span className="col-count">{col.tasks.length}</span>
                        </header>
                        <div className="column-cards">
                          {col.tasks.map((t) => (
                            <div
                              key={t.id}
                              className={`task-card-item agent-${agentClass(t.owner_handle)} ${t.id === selectedTaskId ? "active" : ""}`}
                              onClick={() => setSelectedTaskId(t.id)}
                            >
                              <h4 className="task-name">{t.title}</h4>
                              <div className="task-footer">
                                <span className={`agent-badge ${agentClass(t.owner_handle)}`}>
                                  {agentInitials(t.owner_handle)}
                                </span>
                                <span className="agent-tag">{agentLabel(t.owner_handle)}</span>
                              </div>
                            </div>
                          ))}
                          {!col.tasks.length && <div className="column-empty">비어 있음</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              </div>

              {/* Right column */}
              <aside className="side-viewport">
                {/* Activity timeline */}
                <section className="dashboard-panel logs-panel">
                  <header className="panel-top">
                    <h3>진행 현황</h3>
                    <span style={{ fontSize: 11, color: "var(--text-4)" }}>{latestActivities.length}건</span>
                  </header>
                  <div className="timeline-list">
                    {latestActivities.length === 0
                      ? <div className="empty-notice">진행 기록이 없습니다</div>
                      : latestActivities.map((a) => (
                        <div key={a.id} className="timeline-item">
                          <div className={`timeline-avatar ${agentClass(a.actor_handle)}`}>
                            {agentInitials(a.actor_handle)}
                          </div>
                          <div className="timeline-body">
                            <p className="item-summary">{a.summary}</p>
                            <footer className="item-footer">{agentLabel(a.actor_handle)} · {a.created_at}</footer>
                          </div>
                        </div>
                      ))
                    }
                  </div>
                </section>

                {/* Chat log */}
                <section className="dashboard-panel chat-panel">
                  <header className="panel-top">
                    <h3>대화 로그</h3>
                    <span style={{ fontSize: 11, color: "var(--text-4)" }}>{latestMessages.length}건</span>
                  </header>
                  <div className="chat-log-list">
                    {latestMessages.length === 0
                      ? <div className="empty-notice">대화 내용이 없습니다</div>
                      : latestMessages.map((m) => {
                        const isUser = m.speaker_role === "user";
                        const cls = isUser ? "user" : agentClass(m.speaker_role);
                        return (
                          <div key={m.id} className="chat-bubble-item">
                            <div className={`bubble-avatar ${cls}`}>
                              {isUser ? "U" : agentInitials(m.speaker_role)}
                            </div>
                            <div className="bubble-body">
                              <div className="bubble-header">
                                <strong className="bubble-speaker">
                                  {isUser ? "사용자" : agentLabel(m.speaker_role)}
                                </strong>
                                {m.created_at && <span className="bubble-time">{m.created_at}</span>}
                              </div>
                              <p className="bubble-text">{m.visible_message || m.raw_text}</p>
                            </div>
                          </div>
                        );
                      })
                    }
                  </div>
                </section>
              </aside>
            </div>
          </div>
        )}

        {/* Footer composer */}
        <footer className="workspace-footer">
          <div className="composer-wrap">
            {files.length > 0 && (
              <div className="file-pills">
                {files.map((f) => (
                  <span key={f.id} className="file-pill">📄 {f.original_name}</span>
                ))}
              </div>
            )}
            <div className="message-composer">
              <textarea
                value={composerText}
                onChange={(e) => setComposerText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="AI 팀에게 요청사항을 입력하세요… (Enter 전송 / Shift+Enter 줄바꿈)"
                rows={1}
              />
              <button
                className="send-button"
                onClick={() => void handleSendRequest()}
                disabled={sendingRequest || !composerText.trim() || !activeRunId}
              >
                {sendingRequest ? "전송 중…" : "전송"}
              </button>
            </div>
          </div>
        </footer>
      </main>

      {/* ── Task Detail Slide-over ── */}
      {selectedTaskId && selectedTask && (
        <div className="slide-panel-container">
          <div className="panel-overlay" onClick={() => setSelectedTaskId(null)} />
          <div className="slide-panel-content">
            <header className="slide-panel-header">
              <h2>{selectedTask.title}</h2>
              <button className="panel-close" onClick={() => setSelectedTaskId(null)}>✕</button>
            </header>
            <div className="slide-panel-body scrollable">
              {selectedTask.description && (
                <div className="detail-block">
                  <label>작업 설명</label>
                  <p>{selectedTask.description}</p>
                </div>
              )}
              <div className="detail-data-grid">
                <div className="grid-cell">
                  <label>담당자</label>
                  <span>
                    <span className={`agent-badge ${agentClass(selectedTask.owner_handle)}`} style={{ marginRight: 6 }}>
                      {agentInitials(selectedTask.owner_handle)}
                    </span>
                    {agentLabel(selectedTask.owner_handle)}
                  </span>
                </div>
                <div className="grid-cell"><label>상태</label><span>{statusLabel(selectedTask.status)}</span></div>
                <div className="grid-cell"><label>우선순위</label><span>{selectedTask.priority ?? "-"}</span></div>
                <div className="grid-cell"><label>리뷰 상태</label><span>{selectedTask.review_state || "-"}</span></div>
              </div>
              <div className="detail-actions-row">
                <button onClick={() => void handleTaskAction("rerun")}>재실행</button>
                <button onClick={() => void handleTaskAction("block")}>보류</button>
                <button onClick={() => void handleTaskAction("unblock")}>해제</button>
                <button className="success" onClick={() => void handleTaskAction("approve_review")}>리뷰 승인</button>
                <button className="danger"  onClick={() => void handleTaskAction("reject_review")}>리뷰 반려</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Files Modal ── */}
      {isFilesModalOpen && (
        <div className="global-modal-dim" onClick={() => setIsFilesModalOpen(false)}>
          <div className="global-modal-box sm" onClick={(e) => e.stopPropagation()}>
            <header className="modal-top">
              <h3>참고 파일 관리</h3>
              <button className="modal-close" onClick={() => setIsFilesModalOpen(false)}>✕</button>
            </header>
            <div className="modal-inner">
              <label className="upload-zone">
                <input type="file" multiple onChange={handleUpload} />
                ＋ 파일 추가하기
              </label>
              <div className="file-list-group">
                {files.length === 0
                  ? <p className="modal-empty">첨부된 파일이 없습니다.</p>
                  : files.map((f) => (
                    <div key={f.id} className="file-pill-modal">📄 {f.original_name}</div>
                  ))
                }
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Plan Modal ── */}
      {isPlanModalOpen && (
        <div className="global-modal-dim" onClick={() => setIsPlanModalOpen(false)}>
          <div className="global-modal-box lg" onClick={(e) => e.stopPropagation()}>
            <header className="modal-top">
              <h3>실행 계획 및 작업 제어</h3>
              <button className="modal-close" onClick={() => setIsPlanModalOpen(false)}>✕</button>
            </header>
            <div className="modal-inner plan-view">
              <section className="control-section">
                <h4>워크플로우 제어</h4>
                <div className="status-display">
                  현재 상태: <strong>{statusLabel(runPlanStatus)}</strong>
                </div>
                <div className="horizontal-actions">
                  <button className="action-btn success" onClick={() => void handlePlanApprove()} disabled={planLoading || !activeRunId}>
                    ✓ 승인
                  </button>
                  <button className="action-btn danger"  onClick={() => void handlePlanReject()}  disabled={planLoading || !activeRunId}>
                    ✕ 반려
                  </button>
                </div>
              </section>
              <section className="form-section">
                <h4>수동 작업 추가</h4>
                <form className="form-stack" onSubmit={(e) => void handleCreateTask(e)}>
                  <div className="form-item">
                    <label>제목</label>
                    <input
                      placeholder="작업 제목 입력…"
                      value={createTaskForm.title}
                      onChange={(e) => setCreateTaskForm({ ...createTaskForm, title: e.target.value })}
                    />
                  </div>
                  <div className="form-item">
                    <label>설명</label>
                    <textarea
                      placeholder="작업 설명 입력…"
                      value={createTaskForm.description}
                      onChange={(e) => setCreateTaskForm({ ...createTaskForm, description: e.target.value })}
                    />
                  </div>
                  <button type="submit" className="action-btn primary full" disabled={planLoading || !createTaskForm.title.trim()}>
                    작업 생성
                  </button>
                </form>
              </section>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
