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
} from "./api";
import type { FileUploadItem, OutputType, OversightMode, TeamBoardSnapshot, TeamTask, TeamRunSnapshot } from "./types";

const AUTO_REVIEW_PRESETS = [
  { label: "보수적 (1회)", value: 1 },
  { label: "균형 (2회)", value: 2 },
  { label: "집중 (4회)", value: 4 },
] as const;

const TASK_STATUS_COLUMNS = [
  { key: "todo", label: "Todo" },
  { key: "in_progress", label: "In Progress" },
  { key: "review", label: "Review" },
  { key: "done", label: "Done" },
];

function statusLabel(status?: string) {
  if (!status) return "unknown";
  if (status === "in_progress") return "In Progress";
  if (status === "done") return "Done";
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "워크스페이스 생성에 실패했습니다.");
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "계획 승인에 실패했습니다.");
    } finally {
      setPlanLoading(false);
    }
  }

  async function handlePlanReject() {
    if (!activeRunId) return;
    const reason = window.prompt("반려 사유를 입력하세요.") || "";
    setPlanLoading(true);
    try {
      const snapshot = await rejectPlan(activeRunId, reason);
      setBoard(snapshot);
      setError("");
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

  const runStatus = board?.run?.status || "idle";
  const runPlanStatus = board?.run?.plan_status || "pending";
  const taskCount = board?.tasks?.length || 0;
  const doneCount = (board?.tasks || []).filter((task) => task.status === "done").length;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-mark">DF</div>
          <div>
            <strong>DocFlow AI</strong>
            <small>Agent Workspace Console</small>
          </div>
        </div>
        <div className="panel">
          <h3 className="panel-title">Workspace Controls</h3>
          <label>발신자</label>
          <input value={senderName} onChange={(e) => setSenderName(e.target.value)} />
          <label>개입 모드</label>
          <select value={oversightMode} onChange={(e) => setOversightMode(e.target.value as OversightMode)}>
            <option value="auto">자동</option>
            <option value="manual">수동</option>
          </select>
          <label>산출물 형식</label>
          <select value={outputType} onChange={(e) => setOutputType(e.target.value as OutputType)}>
            <option value="pptx">PPTX</option>
            <option value="docx">DOCX</option>
            <option value="xlsx">XLSX</option>
          </select>
          <label>자동 검토 프리셋</label>
          <select value={presetRounds} onChange={(e) => setPresetRounds(Number(e.target.value))}>
            {AUTO_REVIEW_PRESETS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <button onClick={handleCreateRun} disabled={loadingRuns}>
            새 워크스페이스
          </button>
          <button onClick={refreshRuns} disabled={loadingRuns}>
            워크스페이스 목록 갱신
          </button>
        </div>

        <div className="panel">
          <h3 className="panel-title">Source Files</h3>
          <label>참고 파일 업로드</label>
          <input type="file" multiple onChange={handleUpload} />
          <div className="file-list">
            {files.map((file) => (
              <div key={file.id}>{file.original_name}</div>
            ))}
          </div>
        </div>

        <div className="panel">
          <h3 className="panel-title">Team Members</h3>
          <label>에이전트 선택</label>
          <div className="agent-picker">
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
          <button onClick={handleSaveAgents} disabled={planLoading || !activeRunId}>
            에이전트 적용
          </button>
        </div>

        <div className="panel run-list">
          <h3 className="panel-title">Runs</h3>
          <label>워크스페이스</label>
          {runs.map((run) => (
            <button
              key={run.id}
              className={run.id === activeRunId ? "run-item active" : "run-item"}
              onClick={() => setActiveRunId(run.id)}
            >
              <div>{run.title || "Untitled"}</div>
              <small>{run.status}</small>
            </button>
          ))}
          {runs.length === 0 ? <p className="empty-state">생성된 워크스페이스가 없습니다.</p> : null}
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-header">
          <div className="header-main">
            <h2>{board?.run?.title || "워크스페이스"}</h2>
            <p className="header-subtitle">AI 팀 실행 상태를 실시간으로 확인하고 문서 결과물을 관리합니다.</p>
            <div className="status-row">
              <span className="status-pill">Status: {runStatus}</span>
              <span className="status-pill">Plan: {runPlanStatus}</span>
              <span className="status-pill">Output: {board?.run?.output_type || outputType}</span>
              <span className="status-pill">Tasks: {doneCount}/{taskCount}</span>
            </div>
          </div>
          <div className="header-actions">
            <button onClick={() => handleExport("pptx")}>PPTX</button>
            <button onClick={() => handleExport("docx")}>DOCX</button>
            <button onClick={() => handleExport("xlsx")}>XLSX</button>
          </div>
        </header>

        {error ? <div className="error-banner">{error}</div> : null}

        <section className="workspace-grid">
          <article className="panel activity-panel">
            <h3>진행 현황</h3>
            <div className="scroll-area">
              {activityList.length === 0 ? (
                <p className="empty-state">아직 활동 이력이 없습니다.</p>
              ) : (
                activityList.map((item, index) => (
                  <div key={item.id} className="activity-item">
                    <span className="activity-index">#{activityList.length - index}</span>
                    <div>
                      <p>{item.summary}</p>
                      <small>{item.actor_handle || "system"} · {item.created_at}</small>
                    </div>
                  </div>
                ))
              )}
            </div>
          </article>

          <article className="panel message-panel">
            <h3>대화 로그</h3>
            <div className="scroll-area">
              {messageList.length === 0 ? (
                <p className="empty-state">대화 내용이 없습니다.</p>
              ) : (
                messageList.map((message) => (
                  <div key={message.id} className="message-item">
                    <strong>{message.speaker_role || "user"}</strong>
                    <p>{message.visible_message || message.raw_text || "내용 없음"}</p>
                  </div>
                ))
              )}
            </div>
          </article>

          <article className="panel board-panel">
            <div className="panel-header">
              <h3>작업 보드</h3>
              <div className="run-meta">
                {board?.run?.selected_agents?.join(", ") || "다수 에이전트"}
              </div>
            </div>
            <div className="board-columns">
              {taskColumns.map((column) => (
                <div key={column.key} className="board-column">
                  <h4>{column.label}</h4>
                  <div className="column-body">
                    {column.tasks.length === 0 ? (
                      <p className="empty-state">비어 있음</p>
                    ) : (
                      column.tasks.map((task) => (
                        <button
                          key={task.id}
                          className={task.id === selectedTaskId ? "task-card active" : "task-card"}
                          onClick={() => setSelectedTaskId(task.id)}
                        >
                          <div className="task-title">{task.title}</div>
                          <small>{task.owner_handle || "unassigned"}</small>
                          <span className="task-status">{statusLabel(task.status)}</span>
                        </button>
                      ))
                    )}
                  </div>
                </div>
              ))}
            </div>
          </article>

          <article className="panel deliverable-panel">
            <h3>최종 결과물</h3>
            <div className="deliverable-body">
              {board?.deliverable?.content ? (
                <pre>{board.deliverable.content}</pre>
              ) : (
                <p className="empty-state">아직 결과물이 없습니다.</p>
              )}
            </div>
            <div className="deliverable-caption">
              산출물은 Markdown 미리보기 기준이며, 아래 버튼으로 형식별 파일을 내려받을 수 있습니다.
            </div>
            <div className="deliverable-meta">
              <button onClick={() => handleExport("pptx")} disabled={!board?.deliverable}>
                PPTX 다운로드
              </button>
              <button onClick={() => handleExport("docx")} disabled={!board?.deliverable}>
                DOCX 다운로드
              </button>
              <button onClick={() => handleExport("xlsx")} disabled={!board?.deliverable}>
                XLSX 다운로드
              </button>
            </div>
          </article>
        </section>

        <section className="detail-row">
          <div className="task-detail">
            {selectedTask ? (
              <>
                <h3>{selectedTask.title}</h3>
                <p>{selectedTask.description}</p>
                <dl>
                  <div>
                    <dt>담당</dt>
                    <dd>{selectedTask.owner_handle || "없음"}</dd>
                  </div>
                  <div>
                    <dt>상태</dt>
                    <dd>{statusLabel(selectedTask.status)}</dd>
                  </div>
                  <div>
                    <dt>우선순위</dt>
                    <dd>{selectedTask.priority ?? "-"}</dd>
                  </div>
                  <div>
                    <dt>리뷰 상태</dt>
                    <dd>{selectedTask.review_state || "-"}</dd>
                  </div>
                </dl>
                <div className="task-actions">
                  <button onClick={() => handleTaskAction("rerun")}>Rerun</button>
                  <button onClick={() => handleTaskAction("block")}>Block</button>
                  <button onClick={() => handleTaskAction("unblock")}>Unblock</button>
                  <button onClick={() => handleTaskAction("approve_review")}>Approve Review</button>
                  <button onClick={() => handleTaskAction("reject_review")}>Reject Review</button>
                </div>
              </>
            ) : (
              <p className="empty-state">작업을 선택하면 세부 정보를 보여줍니다.</p>
            )}
          </div>

          <div className="plan-detail panel stack-panel">
            <section>
              <h3>계획 제어</h3>
              <p>
                실행 계획 상태: <strong>{board?.run?.plan_status || "pending"}</strong>
              </p>
              <div className="plan-actions">
                <button onClick={handlePlanApprove} disabled={planLoading || !board}>
                  승인
                </button>
                <button onClick={handlePlanReject} disabled={planLoading || !board}>
                  반려
                </button>
              </div>
            </section>

            <section className="create-task-panel">
              <h3>작업 생성</h3>
              <form onSubmit={handleCreateTask}>
                <label>제목</label>
                <input
                  value={createTaskForm.title}
                  onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, title: e.target.value }))}
                />
                <label>설명</label>
                <textarea
                  value={createTaskForm.description}
                  onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, description: e.target.value }))}
                />
                <label>담당</label>
                <select
                  value={createTaskForm.ownerHandle}
                  onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, ownerHandle: e.target.value }))}
                >
                  {(selectedAgents.length ? selectedAgents : agentHandles).map((handle) => (
                    <option key={handle} value={handle}>
                      {handle}
                    </option>
                  ))}
                </select>
                <label>산출물 목표</label>
                <select
                  value={createTaskForm.artifactGoal}
                  onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, artifactGoal: e.target.value }))}
                >
                  <option value="draft">draft</option>
                  <option value="review_notes">review_notes</option>
                  <option value="final">final</option>
                  <option value="decision">decision</option>
                  <option value="brief">brief</option>
                </select>
                <label>우선순위</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={createTaskForm.priority}
                  onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, priority: Number(e.target.value) }))}
                />
                <label className="inline-check">
                  <input
                    type="checkbox"
                    checked={createTaskForm.reviewRequired}
                    onChange={(e) => setCreateTaskForm((prev) => ({ ...prev, reviewRequired: e.target.checked }))}
                  />
                  <span>리뷰 필요</span>
                </label>
                <button type="submit" disabled={planLoading || !activeRunId}>
                  작업 생성
                </button>
              </form>
            </section>
          </div>
        </section>

        <form className="composer" onSubmit={handleSendRequest}>
          <textarea
            value={composerText}
            onChange={(e) => setComposerText(e.target.value)}
            placeholder="요청을 입력하세요..."
          />
          <button type="submit" disabled={!composerText.trim() || sendingRequest}>
            {sendingRequest ? "전송중…" : "요청 전송"}
          </button>
        </form>
      </main>
    </div>
  );
}
