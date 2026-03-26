import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
  listKnowledgeFiles,
  getFileChunks,
  deleteKnowledgeFile,
} from "./api";
import JobTimeline from "./JobTimeline";
import type { FileUploadItem, OutputType, OversightMode, ReviewMode, TeamBoardSnapshot, TeamTask, TeamRunSnapshot, KnowledgeFile, ChunkItem, ReferenceMode, StyleMode, StyleStrength } from "./types";

const REVIEW_MODE_OPTIONS: { label: string; value: ReviewMode }[] = [
  { label: "Fast — 빠른 기본 검토", value: "fast" },
  { label: "Balanced — 기본 + 구조 검토", value: "balanced" },
  { label: "Deep — 비평/검증 포함 심층 검토", value: "deep" },
  { label: "Aggressive — 강한 비판/재검토 중심", value: "aggressive" },
];

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
  manager: { label: "매니저",   icon: "👔", initials: "M" },
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

function MarkdownPreview({ content }: { content: string }) {
  return (
    <div className="markdown-preview">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">{children}</a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default function App() {
  const [runs, setRuns] = useState<TeamRunSnapshot[]>([]);
  const [activeRunId, setActiveRunId] = useState<string>("");
  const [board, setBoard] = useState<TeamBoardSnapshot | null>(null);
  const [agentHandles, setAgentHandles] = useState<string[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [oversightMode, setOversightMode] = useState<OversightMode>("auto");
  const [reviewMode, setReviewMode] = useState<ReviewMode>("balanced");
  // composerOutputType: per-request output format (shown in composer, not workspace creation)
  const [composerOutputType, setComposerOutputType] = useState<OutputType>("pptx");
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

  // TASK_05 - Knowledge & RAG UI state
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [isKnowledgeOpen, setIsKnowledgeOpen] = useState(true);
  const [isKnowledgeDrawerOpen, setIsKnowledgeDrawerOpen] = useState(false);
  const [expandedFileId, setExpandedFileId] = useState<string | null>(null);
  const [fileChunks, setFileChunks] = useState<Record<string, ChunkItem[]>>({});
  const [referenceMode, setReferenceMode] = useState<ReferenceMode>("auto");
  const [styleMode, setStyleMode] = useState<StyleMode>("default");
  const [styleStrength, setStyleStrength] = useState<StyleStrength>("medium");
  const [selectedKnowledgeIds, setSelectedKnowledgeIds] = useState<string[]>([]);

  // Job Monitor state
  const [isJobMonitorOpen, setIsJobMonitorOpen] = useState(false);
  const [monitorJobId, setMonitorJobId] = useState("");
  const [activeMonitorJobId, setActiveMonitorJobId] = useState<string | null>(null);

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
    void refreshKnowledge();
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
    // Sync output type from workspace when switching boards
    if (board?.run?.output_type) {
      setComposerOutputType(board.run.output_type as OutputType);
    }
    // Sync sidebar title/status from latest board poll
    if (board?.run) {
      setRuns((prev) => prev.map((r) =>
        r.id === board.run.id
          ? { ...r, title: board.run.title || r.title, status: board.run.status }
          : r,
      ));
    }
  }, [board?.run?.id, board?.run?.selected_agents, board?.run?.status, board?.run?.title]);

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

  async function refreshKnowledge() {
    setKnowledgeLoading(true);
    try {
      const items = await listKnowledgeFiles();
      setKnowledgeFiles(items);
    } catch {
      // silent - knowledge endpoint may be empty
    } finally {
      setKnowledgeLoading(false);
    }
  }

  async function handleToggleChunks(fileId: string) {
    if (expandedFileId === fileId) {
      setExpandedFileId(null);
      return;
    }
    setExpandedFileId(fileId);
    if (!fileChunks[fileId]) {
      try {
        const chunks = await getFileChunks(fileId);
        setFileChunks((prev) => ({ ...prev, [fileId]: chunks }));
      } catch {
        // silent
      }
    }
  }

  async function handleDeleteKnowledgeFile(fileId: string) {
    if (!window.confirm("이 파일을 지식 라이브러리에서 삭제하시겠습니까?")) return;
    try {
      await deleteKnowledgeFile(fileId);
      setKnowledgeFiles((prev) => prev.filter((f) => f.id !== fileId));
      setSelectedKnowledgeIds((prev) => prev.filter((id) => id !== fileId));
      if (expandedFileId === fileId) setExpandedFileId(null);
    } catch {
      setError("파일 삭제 실패");
    }
  }

  async function handleCreateRun() {
    setLoadingRuns(true);
    try {
      // output_type is now set per-request in the composer; workspace creation uses backend default
      const snapshot = await createTeamRun({ requestedBy: "USER", oversightMode, outputType: composerOutputType, reviewMode });
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
    // Optimistic: mark the run as queued in sidebar immediately
    setRuns((prev) => prev.map((r) =>
      r.id === activeRunId ? { ...r, status: "queued" } : r,
    ));
    try {
      const sourceIds = referenceMode === "selected"
        ? selectedKnowledgeIds
        : referenceMode === "all"
          ? knowledgeFiles.map((f) => f.id)
          : files.map((item) => item.id);
      const snapshot = await sendRequest(activeRunId, {
        text: composerText.trim(), senderName: "USER",
        outputType: composerOutputType, reviewMode,
        sourceFileIds: sourceIds,
        referenceMode,
        styleMode,
        styleStrength,
      });
      setBoard(snapshot);
      setComposerText("");
      setFiles([]);
      // Sync sidebar title and status immediately from response
      setRuns((prev) => prev.map((r) =>
        r.id === activeRunId
          ? { ...r, title: snapshot.run.title || r.title, status: snapshot.run.status }
          : r,
      ));
      // Full refresh to catch any server-side changes
      void refreshRuns();
    } catch {
      setError("메시지 전송 실패");
      // Revert optimistic update on failure
      setRuns((prev) => prev.map((r) =>
        r.id === activeRunId ? { ...r, status: "idle" } : r,
      ));
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
      void refreshKnowledge();
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
  const currentOutputType = (board?.run?.output_type || composerOutputType) as OutputType;
  const isRunActive = ["queued", "running", "active", "planning"].includes(runStatus);

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
                <label>Review Mode</label>
                <select value={reviewMode} onChange={(e) => setReviewMode(e.target.value as ReviewMode)}>
                  {REVIEW_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
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

          {/* Knowledge Library */}
          <div className="nav-group">
            <h2 className="nav-label">
              지식 라이브러리
              <div className="nav-label-actions">
                <label className="icon-btn upload-icon-btn" title="파일 업로드">
                  <input type="file" multiple style={{ display: "none" }} onChange={handleUpload} />
                  ＋
                </label>
                <button
                  className="icon-btn"
                  onClick={() => { setIsKnowledgeOpen(!isKnowledgeOpen); if (!isKnowledgeOpen) void refreshKnowledge(); }}
                  title={isKnowledgeOpen ? "접기" : "펼치기"}
                >
                  {knowledgeLoading ? "⟳" : isKnowledgeOpen ? "▲" : "▼"}
                </button>
              </div>
            </h2>
            {isKnowledgeOpen && (
              <div className="knowledge-list">
                {knowledgeFiles.length === 0 ? (
                  <div className="knowledge-empty-state">
                    <p className="empty-notice">업로드된 파일 없음</p>
                    <label className="knowledge-upload-cta">
                      <input type="file" multiple style={{ display: "none" }} onChange={handleUpload} />
                      📎 파일 업로드
                    </label>
                  </div>
                ) : (
                  knowledgeFiles.map((kf) => (
                    <div key={kf.id} className={`knowledge-card ${selectedKnowledgeIds.includes(kf.id) ? "selected" : ""}`}>
                      <div className="knowledge-card-header" onClick={() => {
                        const next = selectedKnowledgeIds.includes(kf.id)
                          ? selectedKnowledgeIds.filter((id) => id !== kf.id)
                          : [...selectedKnowledgeIds, kf.id];
                        setSelectedKnowledgeIds(next);
                      }}>
                        <span className={`index-badge ${kf.index_status}`}>
                          {kf.index_status === "indexed" ? "✓" : kf.index_status === "failed" ? "✗" : "○"}
                        </span>
                        <span className="knowledge-name">{kf.original_name}</span>
                        <span className="chunk-count">{kf.chunk_count}청크</span>
                        <button
                          className="icon-btn sm"
                          onClick={(e) => { e.stopPropagation(); void handleToggleChunks(kf.id); }}
                          title="근거 문단 보기"
                        >
                          {expandedFileId === kf.id ? "▲" : "▼"}
                        </button>
                        <button
                          className="icon-btn sm danger"
                          onClick={(e) => { e.stopPropagation(); void handleDeleteKnowledgeFile(kf.id); }}
                          title="삭제"
                        >
                          ✕
                        </button>
                      </div>
                      {expandedFileId === kf.id && (
                        <div className="chunk-preview-list">
                          {(fileChunks[kf.id] ?? []).map((chunk) => (
                            <div key={chunk.id} className="chunk-preview-item">
                              <span className="chunk-section">{chunk.section || `청크 ${chunk.chunk_index + 1}`}</span>
                              <p className="chunk-text">{chunk.content}</p>
                            </div>
                          ))}
                          {(fileChunks[kf.id] ?? []).length === 0 && (
                            <p className="empty-notice">청크 없음</p>
                          )}
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>

          {/* RAG & Style Config */}
          <div className="nav-group">
            <h2 className="nav-label">참고·문체 설정</h2>
            <div className="config-card">
              <div className="config-field">
                <label>참고 모드</label>
                <select value={referenceMode} onChange={(e) => setReferenceMode(e.target.value as ReferenceMode)}>
                  <option value="auto">자동 추천</option>
                  <option value="all">전체 라이브러리</option>
                  <option value="selected">선택한 자료만</option>
                </select>
              </div>
              <div className="config-field">
                <label>문체 모드</label>
                <select value={styleMode} onChange={(e) => setStyleMode(e.target.value as StyleMode)}>
                  <option value="default">기본</option>
                  <option value="formal">격식체</option>
                  <option value="concise">간결체</option>
                  <option value="friendly">친근체</option>
                </select>
              </div>
              {styleMode !== "default" && (
                <div className="config-field">
                  <label>문체 강도</label>
                  <select value={styleStrength} onChange={(e) => setStyleStrength(e.target.value as StyleStrength)}>
                    <option value="low">약하게</option>
                    <option value="medium">보통</option>
                    <option value="high">강하게</option>
                  </select>
                </div>
              )}
              {referenceMode === "selected" && selectedKnowledgeIds.length > 0 && (
                <p className="config-hint">{selectedKnowledgeIds.length}개 파일 선택됨</p>
              )}
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
                {(runStatus === "running" || runStatus === "queued") && <span className="pill-dot" />}
                {runStatus === "queued" ? "⏳ 대기 중" :
                 runStatus === "running" ? "▶ 실행 중" :
                 runStatus === "planning" ? "📋 기획 중" :
                 runStatus === "active" ? "🔄 처리 중" :
                 runStatus === "done" ? "✓ 완료" :
                 runStatus === "blocked" ? "⛔ 차단됨" :
                 runStatus === "awaiting_review" ? "👁 검토 대기" :
                 runStatus.toUpperCase()}
              </span>
              <span className={`status-pill ${runPlanStatus}`}>
                📋 {statusLabel(runPlanStatus)}
              </span>
              <span className="status-pill info">
                📄 {currentOutputType.toUpperCase()}
              </span>
            </div>
            <div className="button-row">
              <button className="control-button knowledge-btn" onClick={() => { setIsKnowledgeDrawerOpen(true); void refreshKnowledge(); }}>
                📚 지식 라이브러리 {knowledgeFiles.length > 0 && <span className="kb-count-badge">{knowledgeFiles.length}</span>}
              </button>
              <button className="control-button" onClick={() => setIsFilesModalOpen(true)}>
                📎 파일 {files.length > 0 && `(${files.length})`}
              </button>
              <button className="control-button" onClick={() => setIsJobMonitorOpen(true)} title="Job 진행 상태 모니터링">
                📊 Job 모니터
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
                      {(["pptx","docx","xlsx","txt","md"] as OutputType[]).map((fmt) => (
                        <button
                          key={fmt}
                          onClick={() => handleExport(fmt)}
                          disabled={!board.deliverable || currentOutputType !== fmt}
                          className={currentOutputType === fmt ? "export-btn-active" : ""}
                        >
                          {fmt.toUpperCase()}
                        </button>
                      ))}
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

                  {/* RAG Sources Panel - always visible */}
                  <div className="rag-sources-panel">
                    <header className="sources-header">
                      <span className="sources-title">📎 참고 자료</span>
                      <div className="sources-header-right">
                        {board?.run?.rag_config?.reference_mode && (
                          <span className="rag-mode-badge">
                            {board.run.rag_config.reference_mode === "auto" ? "자동" :
                             board.run.rag_config.reference_mode === "all" ? "전체" : "선택"}
                          </span>
                        )}
                        <button
                          className="icon-btn sm"
                          onClick={() => { setIsKnowledgeDrawerOpen(true); void refreshKnowledge(); }}
                          title="지식 라이브러리 열기"
                        >
                          📚
                        </button>
                      </div>
                    </header>
                    {board?.source_files && board.source_files.length > 0 ? (
                      <div className="sources-list">
                        {board.source_files.map((sf) => (
                          <div key={sf.id} className="source-chip">
                            <span className={`index-badge sm ${sf.index_status ?? "not_indexed"}`}>
                              {sf.index_status === "indexed" ? "✓" : sf.index_status === "failed" ? "✗" : "○"}
                            </span>
                            <span className="source-name">{sf.original_name}</span>
                            {sf.chunk_count !== undefined && sf.chunk_count > 0 && (
                              <span className="source-chunks">{sf.chunk_count}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="sources-empty">
                        <span>이 요청에 사용된 참고 자료가 없습니다.</span>
                        <button
                          className="sources-empty-btn"
                          onClick={() => { setIsKnowledgeDrawerOpen(true); void refreshKnowledge(); }}
                        >
                          라이브러리 관리 →
                        </button>
                      </div>
                    )}
                    {board?.run?.rag_config?.style_mode && board.run.rag_config.style_mode !== "default" && (
                      <div className="style-badge-row">
                        <span className="style-badge">
                          문체: {board.run.rag_config.style_mode} · {board.run.rag_config.style_strength ?? "medium"}
                        </span>
                      </div>
                    )}
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
            {/* Running status guard banner */}
            {isRunActive && (
              <div className="composer-running-banner">
                <span className="running-banner-dot" />
                <span>
                  {runStatus === "queued" ? "⏳ 요청이 대기열에 등록되었습니다…" :
                   runStatus === "running" ? "▶ AI 팀이 작업 중입니다…" :
                   runStatus === "planning" ? "📋 PM이 작업을 분석하고 있습니다…" :
                   "🔄 처리 중…"}
                </span>
              </div>
            )}
            {/* Reference & Style mode indicator bar */}
            <div className="composer-mode-bar">
              <div className="mode-indicators">
                {/* Output format selector (per-request) */}
                <span className="mode-chip output-type-chip" title="산출물 형식">
                  📄
                  <select
                    className="output-type-select"
                    value={composerOutputType}
                    onChange={(e) => setComposerOutputType(e.target.value as OutputType)}
                    title="산출물 형식 선택"
                  >
                    <option value="pptx">PPTX 슬라이드</option>
                    <option value="docx">DOCX 문서</option>
                    <option value="xlsx">XLSX 데이터</option>
                    <option value="txt">TXT 텍스트</option>
                    <option value="md">MD 마크다운</option>
                  </select>
                </span>
                <span className="mode-chip" title="참고 모드">
                  🔖 {referenceMode === "auto" ? "자동 참고" : referenceMode === "all" ? "전체 라이브러리" : `선택 참고 (${selectedKnowledgeIds.length}개)`}
                </span>
                <span className="mode-chip" title="문체 모드">
                  ✒️ {styleMode === "default" ? "기본 문체" : `${styleMode} · ${styleStrength}`}
                </span>
                {knowledgeFiles.length > 0 && (
                  <span className="mode-chip kb-chip" title="지식 라이브러리">
                    📚 {knowledgeFiles.length}개 파일
                  </span>
                )}
              </div>
              <button
                className="mode-settings-btn"
                onClick={() => { setIsKnowledgeDrawerOpen(true); void refreshKnowledge(); }}
                title="지식 라이브러리 및 참고 설정"
              >
                ⚙️ 라이브러리 설정
              </button>
            </div>
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

      {/* ── Knowledge Library Drawer ── */}
      {isKnowledgeDrawerOpen && (
        <div className="knowledge-drawer-container">
          <div className="panel-overlay" onClick={() => setIsKnowledgeDrawerOpen(false)} />
          <div className="knowledge-drawer">
            <header className="knowledge-drawer-header">
              <div className="knowledge-drawer-title">
                <span className="drawer-icon">📚</span>
                <h2>지식 라이브러리</h2>
                {knowledgeLoading && <span className="loading-spin">⟳</span>}
              </div>
              <div className="knowledge-drawer-actions">
                <button className="icon-btn" onClick={() => void refreshKnowledge()} title="새로고침">⟳</button>
                <button className="panel-close" onClick={() => setIsKnowledgeDrawerOpen(false)}>✕</button>
              </div>
            </header>

            <div className="knowledge-drawer-body scrollable">
              {/* Upload area */}
              <section className="kd-section">
                <h3 className="kd-section-title">문서 업로드</h3>
                <label className="kd-upload-zone">
                  <input type="file" multiple style={{ display: "none" }} onChange={(e) => { handleUpload(e); }} />
                  <div className="kd-upload-icon">📂</div>
                  <p className="kd-upload-label">파일을 클릭하거나 드래그하여 업로드</p>
                  <p className="kd-upload-hint">PDF, DOCX, TXT, XLSX 등 지원</p>
                </label>
              </section>

              {/* Reference & Style settings */}
              <section className="kd-section">
                <h3 className="kd-section-title">참고·문체 설정</h3>
                <div className="kd-config-grid">
                  <div className="kd-config-item">
                    <label>참고 모드</label>
                    <select value={referenceMode} onChange={(e) => setReferenceMode(e.target.value as ReferenceMode)}>
                      <option value="auto">자동 추천</option>
                      <option value="all">전체 라이브러리</option>
                      <option value="selected">선택한 자료만</option>
                    </select>
                    <span className="kd-config-hint">
                      {referenceMode === "auto" && "AI가 관련 자료를 자동으로 선택합니다"}
                      {referenceMode === "all" && "라이브러리의 모든 파일을 참고합니다"}
                      {referenceMode === "selected" && `체크된 ${selectedKnowledgeIds.length}개 파일만 참고합니다`}
                    </span>
                  </div>
                  <div className="kd-config-item">
                    <label>문체 모드</label>
                    <select value={styleMode} onChange={(e) => setStyleMode(e.target.value as StyleMode)}>
                      <option value="default">기본</option>
                      <option value="formal">격식체</option>
                      <option value="concise">간결체</option>
                      <option value="friendly">친근체</option>
                    </select>
                  </div>
                  {styleMode !== "default" && (
                    <div className="kd-config-item">
                      <label>문체 강도</label>
                      <select value={styleStrength} onChange={(e) => setStyleStrength(e.target.value as StyleStrength)}>
                        <option value="low">약하게</option>
                        <option value="medium">보통</option>
                        <option value="high">강하게</option>
                      </select>
                    </div>
                  )}
                </div>
              </section>

              {/* File list */}
              <section className="kd-section">
                <h3 className="kd-section-title">
                  파일 목록
                  <span className="kd-file-count">{knowledgeFiles.length}개</span>
                </h3>
                {knowledgeFiles.length === 0 ? (
                  <div className="kd-empty-state">
                    <div className="kd-empty-icon">📭</div>
                    <p>업로드된 파일이 없습니다.</p>
                    <p className="kd-empty-hint">위 업로드 영역에서 파일을 추가하세요.</p>
                  </div>
                ) : (
                  <div className="kd-file-list">
                    {/* Select all / deselect all */}
                    {referenceMode === "selected" && (
                      <div className="kd-select-bar">
                        <button className="kd-select-all-btn" onClick={() => setSelectedKnowledgeIds(knowledgeFiles.map((f) => f.id))}>
                          전체 선택
                        </button>
                        <button className="kd-select-all-btn" onClick={() => setSelectedKnowledgeIds([])}>
                          전체 해제
                        </button>
                        <span className="kd-selected-count">{selectedKnowledgeIds.length}개 선택됨</span>
                      </div>
                    )}
                    {knowledgeFiles.map((kf) => (
                      <div key={kf.id} className={`kd-file-card ${selectedKnowledgeIds.includes(kf.id) ? "selected" : ""}`}>
                        <div className="kd-file-row" onClick={() => {
                          if (referenceMode !== "selected") return;
                          const next = selectedKnowledgeIds.includes(kf.id)
                            ? selectedKnowledgeIds.filter((id) => id !== kf.id)
                            : [...selectedKnowledgeIds, kf.id];
                          setSelectedKnowledgeIds(next);
                        }}>
                          {referenceMode === "selected" && (
                            <input
                              type="checkbox"
                              className="kd-file-checkbox"
                              checked={selectedKnowledgeIds.includes(kf.id)}
                              onChange={() => {}}
                            />
                          )}
                          <div className={`kd-status-badge ${kf.index_status}`}>
                            {kf.index_status === "indexed" ? "✓ 인덱싱됨" :
                             kf.index_status === "failed" ? "✗ 실패" : "○ 대기"}
                          </div>
                          <div className="kd-file-info">
                            <span className="kd-file-name">{kf.original_name}</span>
                            <div className="kd-file-meta">
                              <span>{kf.chunk_count}개 청크</span>
                              {kf.document_type && <span>· {kf.document_type}</span>}
                              {kf.created_at && <span>· {new Date(kf.created_at).toLocaleDateString("ko-KR")}</span>}
                            </div>
                            {kf.document_summary && (
                              <p className="kd-file-summary">{kf.document_summary}</p>
                            )}
                          </div>
                          <div className="kd-file-actions">
                            <button
                              className="kd-action-btn"
                              onClick={(e) => { e.stopPropagation(); void handleToggleChunks(kf.id); }}
                              title="청크 미리보기"
                            >
                              {expandedFileId === kf.id ? "▲" : "▼"} 청크
                            </button>
                            <button
                              className="kd-action-btn danger"
                              onClick={(e) => { e.stopPropagation(); void handleDeleteKnowledgeFile(kf.id); }}
                              title="삭제"
                            >
                              🗑️ 삭제
                            </button>
                          </div>
                        </div>
                        {/* Chunk preview */}
                        {expandedFileId === kf.id && (
                          <div className="kd-chunk-list">
                            {(fileChunks[kf.id] ?? []).length === 0 ? (
                              <p className="kd-chunk-empty">청크 없음</p>
                            ) : (
                              (fileChunks[kf.id] ?? []).map((chunk) => (
                                <div key={chunk.id} className="kd-chunk-item">
                                  <div className="kd-chunk-header">
                                    <span className={`kd-chunk-status ${chunk.index_status}`}>
                                      {chunk.index_status === "indexed" ? "✓" : chunk.index_status === "failed" ? "✗" : "○"}
                                    </span>
                                    <span className="kd-chunk-section">
                                      {chunk.section || `청크 ${chunk.chunk_index + 1}`}
                                    </span>
                                  </div>
                                  <p className="kd-chunk-text">{chunk.content}</p>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>

            <footer className="knowledge-drawer-footer">
              <button className="action-button primary" onClick={() => setIsKnowledgeDrawerOpen(false)}>
                설정 완료
              </button>
            </footer>
          </div>
        </div>
      )}

      {/* ── Job Monitor Modal ── */}
      {isJobMonitorOpen && (
        <div className="modal-overlay" onClick={() => setIsJobMonitorOpen(false)}>
          <div className="modal-content job-monitor-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3>📊 Job 모니터</h3>
              <button className="panel-close" onClick={() => setIsJobMonitorOpen(false)}>✕</button>
            </header>
            <div className="job-monitor-input-row">
              <input
                className="job-id-input"
                type="text"
                placeholder="Job ID를 입력하세요 (UUID)"
                value={monitorJobId}
                onChange={(e) => setMonitorJobId(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && monitorJobId.trim()) {
                    setActiveMonitorJobId(monitorJobId.trim());
                  }
                }}
              />
              <button
                className="action-button primary"
                onClick={() => { if (monitorJobId.trim()) setActiveMonitorJobId(monitorJobId.trim()); }}
                disabled={!monitorJobId.trim()}
              >
                조회
              </button>
            </div>
            {activeMonitorJobId && (
              <JobTimeline
                key={activeMonitorJobId}
                jobId={activeMonitorJobId}
                onClose={() => setActiveMonitorJobId(null)}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
