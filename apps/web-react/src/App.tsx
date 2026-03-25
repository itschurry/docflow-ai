import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
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
  { label: "보수적 (1회)", value: 1 },
  { label: "균형 (2회)", value: 2 },
  { label: "집중 (4회)", value: 4 },
] as const;

const TASK_STATUS_COLUMNS = [
  { key: "todo", label: "할 일" },
  { key: "in_progress", label: "진행 중" },
  { key: "review", label: "검토 대기" },
  { key: "done", label: "완료" },
];

function statusLabel(status?: string) {
  if (!status) return "알 수 없음";
  if (status === "in_progress") return "진행 중";
  if (status === "done") return "완료";
  if (status === "todo") return "할 일";
  if (status === "review") return "검토 대기";
  return status.replace(/_/g, " ");
}

export default function App() {
  const [runs, setRuns] = useState<TeamRunSnapshot[]>([]);
  const [activeRunId, setActiveRunId] = useState<string>("");
  const [board, setBoard] = useState<TeamBoardSnapshot | null>(null);
  const [agentHandles, setAgentHandles] = useState<string[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [senderName, setSenderName] = useState("ceo");
  const [outputType, setOutputType] = useState<OutputType>("pptx");
  const [oversightMode, setOversightMode] = useState<OversightMode>("auto");
  const [presetRounds, setPresetRounds] = useState<number>(2);
  const [files, setFiles] = useState<FileUploadItem[]>([]);
  const [composerText, setComposerText] = useState("");
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [error, setError] = useState("");
  const [sendingRequest, setSendingRequest] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [isPlanModalOpen, setIsPlanModalOpen] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);

  const [createTaskForm, setCreateTaskForm] = useState({
    title: "",
    description: "",
    ownerHandle: "writer",
    artifactGoal: "draft",
    priority: 50,
    reviewRequired: false,
  });

  useEffect(() => {
    void refreshAgents();
    void refreshRuns();
  }, []);

  useEffect(() => {
    if (!activeRunId) {
      setBoard(null);
      return;
    }
    let cancelled = false;
    const fetchBoard = async () => {
      try {
        const data = await getBoard(activeRunId);
        if (!cancelled) {
          setBoard(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "워크스페이스를 불러오지 못했습니다.");
        }
      }
    };
    void fetchBoard();
    const interval = window.setInterval(() => {
      void fetchBoard();
    }, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeRunId]);

  const selectedTask = useMemo<TeamTask | undefined>(() => {
    return board?.tasks?.find((task) => task.id === selectedTaskId);
  }, [board, selectedTaskId]);

  useEffect(() => {
    const agents = board?.run?.selected_agents ?? [];
    setSelectedAgents(agents);
  }, [board?.run?.id, board?.run?.selected_agents]);

  async function refreshAgents() {
    try {
      const data = await listAgents();
      const handles = data.agents.map((agent) => agent.handle);
      setAgentHandles(handles);
      if (handles.length && !handles.includes(createTaskForm.ownerHandle)) {
        setCreateTaskForm((prev) => ({ ...prev, ownerHandle: handles[0] }));
      }
    } catch {
      // keep UI usable without blocking on agent list
    }
  }

  async function refreshRuns() {
    setLoadingRuns(true);
    try {
      const items = await listTeamRuns();
      setRuns(items);
      const next = activeRunId || items[0]?.id || "";
      if (next) {
        setActiveRunId(next);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "워크스페이스를 불러오지 못했습니다.");
    } finally {
      setLoadingRuns(false);
    }
  }

  async function handleCreateRun() {
    setLoadingRuns(true);
    try {
      const boardSnapshot = await createTeamRun({
        requestedBy: senderName || "ceo",
        oversightMode,
        outputType,
        autoReviewMaxRounds: presetRounds,
      });
      setBoard(boardSnapshot);
      setActiveRunId(boardSnapshot.run.id);
      setError("");
      void refreshRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : "워크스페이스 생성에 실패했습니다.");
    } finally {
      setLoadingRuns(false);
    }
  }

  async function handleDeleteSelected() {
    if (!selectedRunIds.length) return;
    if (!window.confirm(`선택한 ${selectedRunIds.length}개의 워크스페이스를 삭제하시겠습니까?`)) return;
    setLoadingRuns(true);
    try {
      for (const id of selectedRunIds) {
        await deleteTeamRun(id);
      }
      setSelectedRunIds([]);
      if (selectedRunIds.includes(activeRunId)) {
        setActiveRunId("");
      }
      void refreshRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : "워크스페이스 삭제에 실패했습니다.");
    } finally {
      setLoadingRuns(false);
    }
  }

  async function handleDeleteAll() {
    if (!runs.length) return;
    if (!window.confirm("모든 워크스페이스를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) return;
    setLoadingRuns(true);
    try {
      for (const run of runs) {
        await deleteTeamRun(run.id);
      }
      setSelectedRunIds([]);
      setActiveRunId("");
      void refreshRuns();
    } catch (err) {
      setError(err instanceof Error ? err.message : "모든 워크스페이스 삭제에 실패했습니다.");
    } finally {
      setLoadingRuns(false);
    }
  }

  async function handleSendRequest(e: FormEvent) {
    e.preventDefault();
    if (!activeRunId || !composerText.trim() || sendingRequest) {
      return;
    }
    setSendingRequest(true);
    try {
      const boardSnapshot = await sendRequest(activeRunId, {
        text: composerText.trim(),
        senderName: senderName || "ceo",
        outputType,
        autoReviewMaxRounds: presetRounds,
        sourceFileIds: files.map((item) => item.id),
      });
      setBoard(boardSnapshot);
      setComposerText("");
      setFiles([]);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청 전송에 실패했습니다.");
    } finally {
      setSendingRequest(false);
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const fileList = event.target.files;
    if (!fileList || fileList.length === 0) {
      return;
    }
    try {
      const uploaded: FileUploadItem[] = [];
      for (const file of Array.from(fileList)) {
        const item = await uploadFile(file);
        uploaded.push(item);
      }
      setFiles((prev) => [...uploaded, ...prev]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "파일 업로드에 실패했습니다.");
    } finally {
      event.target.value = "";
    }
  }

  async function handleExport(format: OutputType) {
    if (!activeRunId) return;
    try {
      const { download_path } = await exportDeliverable(activeRunId, format);
      window.location.href = download_path;
    } catch (err) {
      setError(err instanceof Error ? err.message : "결과물 다운로드에 실패했습니다.");
    }
  }

  async function handlePlanApprove() {
    if (!activeRunId) return;
    setPlanLoading(true);
    try {
      const snapshot = await approvePlan(activeRunId);
      setBoard(snapshot);
      setError("");
      setIsPlanModalOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "계획 승인에 실패했습니다.");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handlePlanReject() {
    if (!activeRunId) return;
    const reason = window.prompt("반려 사유를 입력하세요.") || "";
    if (!reason.trim()) return;
    setPlanLoading(true);
    try {
      const snapshot = await rejectPlan(activeRunId, reason);
      setBoard(snapshot);
      setError("");
      setIsPlanModalOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "계획 반려에 실패했습니다.");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleTaskAction(action: string) {
    if (!selectedTaskId) return;
    setPlanLoading(true);
    try {
      const snapshot = await updateTask(selectedTaskId, { action });
      setBoard(snapshot);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "작업 처리를 실패했습니다.");
    } finally {
      setPlanLoading(false);
      setSelectedTaskId(null);
    }
  }

  async function handleSaveAgents() {
    if (!activeRunId) return;
    setPlanLoading(true);
    try {
      const snapshot = await updateRunAgents(activeRunId, selectedAgents);
      setBoard(snapshot);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "에이전트 저장에 실패했습니다.");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleCreateTask(e: FormEvent) {
    e.preventDefault();
    if (!activeRunId) return;
    if (!createTaskForm.title.trim() || !createTaskForm.description.trim()) {
      setError("작업 제목과 설명을 입력해 주세요.");
      return;
    }
    setPlanLoading(true);
    try {
      const snapshot = await createTask(activeRunId, {
        title: createTaskForm.title.trim(),
        description: createTaskForm.description.trim(),
        ownerHandle: createTaskForm.ownerHandle,
        artifactGoal: createTaskForm.artifactGoal,
        priority: createTaskForm.priority,
        reviewRequired: createTaskForm.reviewRequired,
      });
      setBoard(snapshot);
      setCreateTaskForm((prev) => ({ ...prev, title: "", description: "" }));
      setError("");
      setIsPlanModalOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "작업 생성에 실패했습니다.");
    } finally {
      setPlanLoading(false);
    }
  }

  const activityList = board?.activity ?? [];
  const messageList = board?.items ?? [];
  const taskColumns = useMemo(() => {
    const tasks = board?.tasks ?? [];
    return TASK_STATUS_COLUMNS.map((column) => ({
      ...column,
      tasks: tasks.filter((task) => {
        if (!task.status) return column.key === "todo";
        if (task.status === column.key) return true;
        if (column.key === "review" && task.review_state === "reviewed") return true;
        if (column.key === "done" && task.status === "done") return true;
        return false;
      }),
    }));
  }, [board]);

  const runStatus = board?.run?.status || "대기 중";
  const runPlanStatus = board?.run?.plan_status || "대기 중";
  const taskCount = board?.tasks?.length || 0;
  const doneCount = (board?.tasks || []).filter((task) => task.status === "done").length;

  return (
    <div className="layout">
      <aside className="side">
        <div className="brand">
          <div className="dot">D</div>
          <div className="brand-copy">
            <b>DocFlow AI</b>
            <br />
            <span>에이전트 워크스페이스</span>
          </div>
        </div>

        <div className="side-section">
          <div className="side-label">워크스페이스 설정</div>
          <div className="card">
            <label>발신자</label>
            <input className="side-input" value={senderName} onChange={(e) => setSenderName(e.target.value)} />
            <label>개입 모드</label>
            <select className="side-input" value={oversightMode} onChange={(e) => setOversightMode(e.target.value as OversightMode)}>
              <option value="auto">자동</option>
              <option value="manual">수동</option>
            </select>
            <label>산출물 형식</label>
            <select className="side-input" value={outputType} onChange={(e) => setOutputType(e.target.value as OutputType)}>
              <option value="pptx">PPTX 슬라이드</option>
              <option value="docx">DOCX 문서</option>
              <option value="xlsx">XLSX 표</option>
            </select>
            <label>자동 검토 횟수</label>
            <select className="side-input" value={presetRounds} onChange={(e) => setPresetRounds(Number(e.target.value))}>
              {AUTO_REVIEW_PRESETS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <button className="side-btn primary" onClick={handleCreateRun} disabled={loadingRuns}>
              새 워크스페이스
            </button>
          </div>
        </div>

        <div className="side-section">
          <div className="side-label">참참여 에이전트</div>
          <div className="card">
            <div className="agents">
              {agentHandles.map((handle) => (
                <label key={handle} className="agent-chip">
                  <input
                    type="checkbox"
                    checked={selectedAgents.includes(handle)}
                    onChange={(e) => {
                      setSelectedAgents((prev) => {
                        if (e.target.checked) {
                          return Array.from(new Set([...prev, handle]));
                        }
                        return prev.filter((item) => item !== handle);
                      });
                    }}
                  />
                  <span>{handle}</span>
                </label>
              ))}
            </div>
            <button className="side-btn" onClick={handleSaveAgents} disabled={planLoading || !activeRunId}>
              에이전트 적용
            </button>
          </div>
        </div>

        <div className="side-section run-list-section">
          <div className="side-label">워크스페이스 목록</div>
          <div className="run-list-actions">
            <button className="text-btn danger" onClick={handleDeleteSelected} disabled={!selectedRunIds.length}>선택 삭제</button>
            <button className="text-btn danger" onClick={handleDeleteAll} disabled={!runs.length}>전체 삭제</button>
          </div>
          <div className="run-list scrollable">
            {runs.map((run) => (
              <div
                key={run.id}
                className={`run-item ${run.id === activeRunId ? "active" : ""}`}
                onClick={() => setActiveRunId(run.id)}
              >
                <input
                  type="checkbox"
                  className="run-checkbox"
                  checked={selectedRunIds.includes(run.id)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    setSelectedRunIds(prev =>
                      e.target.checked ? [...prev, run.id] : prev.filter(id => id !== run.id)
                    );
                  }}
                />
                <div className="run-info">
                  <div className="run-title-text">{run.title || "제목 없음"}</div>
                  <small className="run-status-text">{statusLabel(run.status)}</small>
                </div>
              </div>
            ))}
            {runs.length === 0 ? <p className="empty-state-side">워크스페이스가 없습니다.</p> : null}
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="top">
          <div className="header-left">
            <h1 className="title">{board?.run?.title || "워크스페이스"}</h1>
            <p className="subtitle">AI 팀 실행 상태를 실시간으로 확인하고 문서 결과물을 관리합니다.</p>
          </div>
          <div className="header-right">
            <div className="ws-badges">
              <span className={`ws-badge status-${runStatus === 'running' || runStatus === 'active' ? 'running' : 'active'}`}>{runStatus}</span>
              <span className="ws-badge label">📋 {runPlanStatus}</span>
              <span className="ws-badge label">📄 {board?.run?.output_type || outputType}</span>
              <span className="ws-badge label">✅ {doneCount}/{taskCount}</span>
            </div>
            <button className="header-btn primary" onClick={() => setIsPlanModalOpen(true)}>계획 및 작업 관리</button>
          </div>
        </header>

        <div className="workspace-grid">
          <div className="workspace-main-column">
            <article className="section-panel deliverable-panel">
              <div className="panel-header-top">
                <h3>최종 결과물</h3>
                <div className="export-actions">
                  <button className="export-btn" onClick={() => handleExport("pptx")} disabled={!board?.deliverable}>PPTX</button>
                  <button className="export-btn" onClick={() => handleExport("docx")} disabled={!board?.deliverable}>DOCX</button>
                  <button className="export-btn" onClick={() => handleExport("xlsx")} disabled={!board?.deliverable}>XLSX</button>
                </div>
              </div>
              <div className="deliverable-body-container">
                {board?.deliverable?.content ? (
                  <pre className="deliverable-pre">{board.deliverable.content}</pre>
                ) : (
                  <div className="empty-state-main">아직 결과물이 없습니다.</div>
                )}
              </div>
            </article>

            <article className="section-panel board-panel-refined">
              <div className="panel-header-top">
                <h3>작업 보드</h3>
                <small className="panel-meta">{board?.run?.selected_agents?.join(", ") || "다수 에이전트"}</small>
              </div>
              <div className="board-box">
                <div className="board-columns">
                  {taskColumns.map((column) => (
                    <div key={column.key} className="board-col">
                      <h4>{column.label}</h4>
                      <div className="board-cards scrollable">
                        {column.tasks.length === 0 ? (
                          <div className="task-empty-text">비어 있음</div>
                        ) : (
                          column.tasks.map((task) => (
                            <div
                              key={task.id}
                              className={`task-card ${task.id === selectedTaskId ? "active" : ""}`}
                              onClick={() => setSelectedTaskId(task.id)}
                            >
                              <div className="task-title">{task.title}</div>
                              <div className="task-meta">{task.owner_handle || "미할당"} · {statusLabel(task.status)}</div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </article>
          </div>

          <div className="workspace-side-column">
            <article className="section-panel log-panel">
              <div className="panel-header-top">
                <h3>진행 현황</h3>
              </div>
              <div className="log-list scrollable">
                {activityList.length === 0 ? (
                  <p className="empty-state-main">활동 이력이 없습니다.</p>
                ) : (
                  activityList.map((item, index) => (
                    <div key={item.id} className="log-item">
                      <div className="log-line">
                        <span className="log-index">#{activityList.length - index}</span>
                        <span className="log-summary">{item.summary}</span>
                      </div>
                      <div className="log-meta">{item.actor_handle || "시스템"} · {item.created_at}</div>
                    </div>
                  ))
                )}
              </div>
            </article>

            <article className="section-panel log-panel">
              <div className="panel-header-top">
                <h3>대화 로그</h3>
              </div>
              <div className="log-list scrollable">
                {messageList.length === 0 ? (
                  <p className="empty-state-main">대화 내용이 없습니다.</p>
                ) : (
                  messageList.map((message, index) => (
                    <div key={message.id} className="log-item">
                      <div className="log-line">
                        <span className="log-index">#{messageList.length - index}</span>
                        <strong className="log-speaker">{message.speaker_role || "사용자"}</strong>
                      </div>
                      <p className="log-text">{message.visible_message || message.raw_text || "내용 없음"}</p>
                      <div className="log-meta">{message.created_at}</div>
                    </div>
                  ))
                )}
              </div>
            </article>

            <article className="section-panel source-files-panel">
              <div className="panel-header-top">
                <h3>참고 파일</h3>
              </div>
              <div className="source-files-box">
                <input type="file" multiple onChange={handleUpload} className="file-input-hidden" id="file-upload" />
                <label htmlFor="file-upload" className="file-upload-label">파일 업로드</label>
                <div className="file-list-main scrollable">
                  {files.map((file) => (
                    <div key={file.id} className="file-item-main">{file.original_name}</div>
                  ))}
                  {files.length === 0 && <div className="task-empty-text">첨부된 파일이 없습니다.</div>}
                </div>
              </div>
            </article>
          </div>
        </div>

        <footer className="footer">
          <form className="composer-refined" onSubmit={handleSendRequest}>
            <textarea
              className="composer-input"
              value={composerText}
              onChange={(e) => setComposerText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSendRequest(e as unknown as FormEvent);
                }
              }}
              placeholder="AI 팀에게 요청하세요... (Enter: 전송 / Shift+Enter: 줄바꿈)"
            />
            <button className="composer-btn" type="submit" disabled={!composerText.trim() || sendingRequest}>
              {sendingRequest ? "전송 중…" : "전송"}
            </button>
          </form>
        </footer>
      </main>

      {/* Task Detail Backdrop & Slide-over */}
      {selectedTaskId && selectedTask && (
        <>
          <div className="task-detail-backdrop" onClick={() => setSelectedTaskId(null)} />
          <div className={`task-detail-box ${selectedTaskId ? "open" : ""}`}>
            <div className="detail-header">
              <h2>{selectedTask.title}</h2>
              <button className="detail-close-btn" onClick={() => setSelectedTaskId(null)}>✕</button>
            </div>
            <div className="detail-content scrollable">
              <div className="detail-section">
                <div className="detail-label">설명</div>
                <p className="detail-text">{selectedTask.description}</p>
              </div>
              <div className="detail-info-grid">
                <div className="info-item">
                  <div className="info-label">담당</div>
                  <div className="info-value">{selectedTask.owner_handle || "없음"}</div>
                </div>
                <div className="info-item">
                  <div className="info-label">상태</div>
                  <div className="info-value">{statusLabel(selectedTask.status)}</div>
                </div>
                <div className="info-item">
                  <div className="info-label">우선순위</div>
                  <div className="info-value">{selectedTask.priority ?? "-"}</div>
                </div>
                <div className="info-item">
                  <div className="info-label">리뷰 상태</div>
                  <div className="info-value">{selectedTask.review_state || "-"}</div>
                </div>
              </div>
              <div className="detail-actions">
                <button className="action-btn" onClick={() => handleTaskAction("rerun")}>재실행</button>
                <button className="action-btn" onClick={() => handleTaskAction("block")}>진행 보류</button>
                <button className="action-btn" onClick={() => handleTaskAction("unblock")}>보류 해제</button>
                <button className="action-btn success" onClick={() => handleTaskAction("approve_review")}>리뷰 승인</button>
                <button className="action-btn danger" onClick={() => handleTaskAction("reject_review")}>리뷰 반려</button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Plan & Task Creation Modal (Full Overlay) */}
      {isPlanModalOpen && (
        <div className="modal-overlay-refined" onClick={() => setIsPlanModalOpen(false)}>
          <div className="modal-content-refined" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header-refined">
              <h3>계획 및 작업 관리</h3>
              <button className="modal-close-btn" onClick={() => setIsPlanModalOpen(false)}>✕</button>
            </div>
            <div className="modal-body-refined scrollable">
              <div className="modal-grid-refined">
                <section className="modal-section panel-light">
                  <h3>계획 제어</h3>
                  <div className="plan-status-box">
                    상태: <strong className="status-highlight">{runPlanStatus}</strong>
                  </div>
                  <div className="plan-btn-group">
                    <button className="modal-btn success" onClick={handlePlanApprove} disabled={planLoading || !board}>승인</button>
                    <button className="modal-btn danger" onClick={handlePlanReject} disabled={planLoading || !board}>반려</button>
                  </div>
                </section>

                <section className="modal-section panel-light">
                  <h3>작업 생성</h3>
                  <form className="create-task-form" onSubmit={handleCreateTask}>
                    <label>제목</label>
                    <input
                      className="modal-input"
                      value={createTaskForm.title}
                      onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, title: e.target.value }))}
                    />
                    <label>설명</label>
                    <textarea
                      className="modal-input"
                      value={createTaskForm.description}
                      onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, description: e.target.value }))}
                    />
                    <div className="form-row">
                      <div className="form-group">
                        <label>담당</label>
                        <select
                          className="modal-input"
                          value={createTaskForm.ownerHandle}
                          onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, ownerHandle: e.target.value }))}
                        >
                          {(selectedAgents.length ? selectedAgents : agentHandles).map((handle) => (
                            <option key={handle} value={handle}>
                              {handle}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="form-group">
                        <label>우선순위</label>
                        <input
                          className="modal-input"
                          type="number"
                          value={createTaskForm.priority}
                          onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, priority: Number(e.target.value) }))}
                        />
                      </div>
                    </div>
                    <label>산출물 목표</label>
                    <select
                      className="modal-input"
                      value={createTaskForm.artifactGoal}
                      onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, artifactGoal: e.target.value }))}
                    >
                      <option value="draft">초안 (draft)</option>
                      <option value="review_notes">리뷰 노트 (review_notes)</option>
                      <option value="final">최종본 (final)</option>
                    </select>
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={createTaskForm.reviewRequired}
                        onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, reviewRequired: e.target.checked }))}
                      />
                      <span>리뷰 필요</span>
                    </label>
                    <button type="submit" disabled={planLoading || !activeRunId} className="modal-btn primary">
                      작업 생성
                    </button>
                  </form>
                </section>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
