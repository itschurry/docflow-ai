import { FormEvent, useEffect, useMemo, useState } from "react";
import { createTeamRun, exportDeliverable, getBoard, listTeamRuns, sendRequest, uploadFile } from "./api";
import type { FileUploadItem, OutputType, OversightMode, TeamBoardSnapshot, TeamRun } from "./types";

const AUTO_REVIEW_PRESETS = [
  { label: "보수적 (1회)", value: 1 },
  { label: "균형 (2회)", value: 2 },
  { label: "집중 (4회)", value: 4 },
] as const;

function safeText(value?: string | null): string {
  return String(value || "").trim();
}

export default function App() {
  const [runs, setRuns] = useState<TeamRun[]>([]);
  const [activeRunId, setActiveRunId] = useState<string>("");
  const [board, setBoard] = useState<TeamBoardSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>("");

  const [senderName, setSenderName] = useState("ceo");
  const [oversightMode, setOversightMode] = useState<OversightMode>("auto");
  const [outputType, setOutputType] = useState<OutputType>("pptx");
  const [presetRounds, setPresetRounds] = useState<number>(2);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<FileUploadItem[]>([]);

  useEffect(() => {
    void refreshRuns();
  }, []);

  useEffect(() => {
    if (!activeRunId) return;
    const timer = window.setInterval(() => {
      void refreshBoard(activeRunId, false);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeRunId]);

  async function refreshRuns(): Promise<void> {
    try {
      setError("");
      const items = await listTeamRuns();
      setRuns(items);
      const next = activeRunId || items[0]?.id || "";
      if (next) {
        setActiveRunId(next);
        await refreshBoard(next, false);
      } else {
        setBoard(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "워크스페이스를 불러오지 못했습니다.");
    }
  }

  async function refreshBoard(runId: string, withLoading: boolean): Promise<void> {
    if (!runId) return;
    try {
      if (withLoading) setLoading(true);
      const data = await getBoard(runId);
      setBoard(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "작업 보드를 불러오지 못했습니다.");
    } finally {
      if (withLoading) setLoading(false);
    }
  }

  async function handleCreateRun(): Promise<void> {
    try {
      setLoading(true);
      const data = await createTeamRun({
        requestedBy: senderName || "ceo",
        oversightMode,
        outputType,
        autoReviewMaxRounds: presetRounds,
      });
      await refreshRuns();
      setActiveRunId(data.run.id);
      setBoard(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "새 워크스페이스를 만들지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function handleUpload(fileList: FileList | null): Promise<void> {
    if (!fileList || fileList.length === 0) return;
    try {
      setLoading(true);
      const uploaded: FileUploadItem[] = [];
      for (const file of Array.from(fileList)) {
        const item = await uploadFile(file);
        uploaded.push(item);
      }
      setFiles((prev) => [...uploaded, ...prev]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "파일 업로드에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSend(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!activeRunId || !safeText(text) || sending) return;
    try {
      setSending(true);
      const data = await sendRequest(activeRunId, {
        text: text.trim(),
        senderName: senderName || "ceo",
        outputType,
        autoReviewMaxRounds: presetRounds,
        sourceFileIds: files.map((item) => item.id),
      });
      setBoard(data);
      setText("");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "요청 전송에 실패했습니다.");
    } finally {
      setSending(false);
    }
  }

  async function handleExport(format: OutputType): Promise<void> {
    if (!activeRunId) return;
    try {
      const data = await exportDeliverable(activeRunId, format);
      window.location.href = data.download_path;
    } catch (err) {
      setError(err instanceof Error ? err.message : "결과물 다운로드에 실패했습니다.");
    }
  }

  const latestActivity = useMemo(() => {
    const items = board?.activity || [];
    return [...items].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  }, [board]);

  const chatMessages = useMemo(() => {
    const items = board?.items || [];
    return [...items].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
  }, [board]);

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>DocFlow AI</h1>
        <div className="panel">
          <label>발신자</label>
          <input value={senderName} onChange={(e) => setSenderName(e.target.value)} />

          <label>개입 모드</label>
          <select value={oversightMode} onChange={(e) => setOversightMode(e.target.value as OversightMode)}>
            <option value="auto">자동</option>
            <option value="manual">수동</option>
          </select>

          <label>문서 형식</label>
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

          <button onClick={() => void handleCreateRun()} disabled={loading}>
            새 워크스페이스
          </button>
          <button onClick={() => void refreshRuns()} disabled={loading}>
            새로고침
          </button>
        </div>

        <div className="panel">
          <label>참고 파일 업로드</label>
          <input type="file" multiple onChange={(e) => void handleUpload(e.target.files)} />
          <div className="file-list">
            {files.map((item) => (
              <div key={item.id} className="file-item">
                {item.original_name}
              </div>
            ))}
          </div>
        </div>

        <div className="panel">
          <label>워크스페이스</label>
          <div className="run-list">
            {runs.map((run) => (
              <button
                key={run.id}
                className={run.id === activeRunId ? "run-item active" : "run-item"}
                onClick={() => {
                  setActiveRunId(run.id);
                  void refreshBoard(run.id, true);
                }}
              >
                <span>{run.title || "Untitled"}</span>
                <small>{run.status}</small>
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="header">
          <div>
            <h2>{board?.run?.title || "Workspace"}</h2>
            <p>
              상태: <b>{board?.run?.status || "idle"}</b> | 계획: <b>{board?.run?.plan_status || "-"}</b> | 출력:{" "}
              <b>{board?.run?.output_type || outputType}</b>
            </p>
          </div>
          <div className="header-actions">
            <button onClick={() => void handleExport("pptx")}>PPTX 다운로드</button>
            <button onClick={() => void handleExport("docx")}>DOCX 다운로드</button>
            <button onClick={() => void handleExport("xlsx")}>XLSX 다운로드</button>
          </div>
        </header>

        {error ? <div className="error">{error}</div> : null}

        <section className="content-grid">
          <article className="card">
            <h3>진행 현황</h3>
            <div className="scroll-list">
              {latestActivity.length === 0 ? <div className="empty">활동 내역 없음</div> : null}
              {latestActivity.map((item, index) => (
                <div key={item.id} className="log-item">
                  <span className="idx">#{latestActivity.length - index}</span>
                  <span className="summary">{item.summary}</span>
                </div>
              ))}
            </div>
          </article>

          <article className="card">
            <h3>팀 메시지</h3>
            <div className="scroll-list">
              {chatMessages.length === 0 ? <div className="empty">대화 없음</div> : null}
              {chatMessages.map((item) => (
                <div key={item.id} className="chat-item">
                  <b>{item.speaker_role || "agent"}</b>
                  <p>{safeText(item.visible_message) || safeText(item.raw_text)}</p>
                </div>
              ))}
            </div>
          </article>

          <article className="card card-wide">
            <h3>최종 결과물</h3>
            <div className="deliverable">
              {safeText(board?.deliverable?.content) ? (
                <pre>{safeText(board?.deliverable?.content)}</pre>
              ) : (
                <div className="empty">아직 결과물이 없습니다.</div>
              )}
            </div>
          </article>
        </section>

        <form className="composer" onSubmit={(e) => void handleSend(e)}>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="요청을 입력하세요. 예: 청계천의 변화와 역사 발표자료 작성"
          />
          <button type="submit" disabled={!activeRunId || sending || !safeText(text)}>
            {sending ? "전송 중..." : "요청 전송"}
          </button>
        </form>
      </main>
    </div>
  );
}
