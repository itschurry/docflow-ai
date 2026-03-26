import { useEffect, useRef, useState } from "react";
import { getJobDetail, getJobSteps } from "./api";
import type { JobDetail, JobStep } from "./types";

const STEP_LABEL: Record<string, string> = {
  parse_reference_docs: "참조 문서 파싱",
  generate_report_outline: "보고서 개요 생성",
  generate_report_draft: "보고서 초안 작성",
  review_report: "품질 검토",
  generate_slide_outline: "슬라이드 개요 생성",
  generate_slide_body: "슬라이드 본문 생성",
  extract_budget_items: "예산 항목 추출",
  run_budget_rules: "예산 규칙 적용",
  generate_xlsx: "Excel 파일 생성",
  generate_ppt: "프레젠테이션 생성",
};

const STATUS_CONFIG: Record<string, { icon: string; label: string; cls: string }> = {
  pending:   { icon: "○", label: "대기",     cls: "step-pending"   },
  running:   { icon: "◐", label: "실행 중",  cls: "step-running"   },
  completed: { icon: "●", label: "완료",     cls: "step-completed" },
  failed:    { icon: "✕", label: "실패",     cls: "step-failed"    },
};

const JOB_STATUS_LABEL: Record<string, string> = {
  QUEUED:           "대기 중",
  RUNNING:          "실행 중",
  REVIEW_REQUIRED:  "검토 대기",
  COMPLETED:        "완료",
  FAILED:           "실패",
  CANCELLED:        "취소됨",
};

function elapsed(start: string | null, end: string | null): string {
  if (!start) return "";
  const a = new Date(start).getTime();
  const b = end ? new Date(end).getTime() : Date.now();
  const sec = Math.round((b - a) / 1000);
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

interface Props {
  jobId: string;
  onClose?: () => void;
}

export default function JobTimeline({ jobId, onClose }: Props) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [steps, setSteps] = useState<JobStep[]>([]);
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED", "REVIEW_REQUIRED"]);

  async function refresh() {
    try {
      const [d, s] = await Promise.all([getJobDetail(jobId), getJobSteps(jobId)]);
      setJob(d);
      setSteps(s);
      if (terminal.has(d.status) && intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void refresh();
    intervalRef.current = setInterval(() => void refresh(), 2000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [jobId]);

  if (error) return <div className="job-timeline-error">오류: {error}</div>;
  if (!job) return <div className="job-timeline-loading">불러오는 중…</div>;

  const statusCfg = STATUS_CONFIG[job.status?.toLowerCase()] ?? STATUS_CONFIG.pending;

  return (
    <div className="job-timeline">
      <div className="job-timeline-header">
        <div className="job-timeline-title">
          <span className="job-type-badge">{job.job_type}</span>
          <span className={`job-status-badge job-status-${job.status.toLowerCase()}`}>
            {JOB_STATUS_LABEL[job.status] ?? job.status}
          </span>
        </div>
        {onClose && (
          <button className="job-timeline-close" onClick={onClose}>✕</button>
        )}
      </div>

      <div className="job-timeline-request">{job.request_text}</div>

      {/* Progress bar */}
      <div className="job-progress-wrap">
        <div
          className="job-progress-bar"
          style={{ width: `${job.progress}%` }}
          data-status={job.status.toLowerCase()}
        />
        <span className="job-progress-label">{job.progress}%</span>
      </div>

      {/* Step list */}
      <ol className="step-list">
        {steps.map((step, idx) => {
          const cfg = STATUS_CONFIG[step.status] ?? STATUS_CONFIG.pending;
          const isExpanded = expandedStep === step.id;
          return (
            <li key={step.id} className={`step-item ${cfg.cls}`}>
              <div className="step-row" onClick={() => setExpandedStep(isExpanded ? null : step.id)}>
                <span className="step-index">{idx + 1}</span>
                <span className="step-icon">{cfg.icon}</span>
                <span className="step-name">
                  {STEP_LABEL[step.step_name] ?? step.step_name.replace(/_/g, " ")}
                </span>
                <span className="step-badge">{cfg.label}</span>
                {step.started_at && (
                  <span className="step-elapsed">
                    {elapsed(step.started_at, step.finished_at)}
                  </span>
                )}
                {(step.output || step.error) && (
                  <span className="step-expand-icon">{isExpanded ? "▲" : "▼"}</span>
                )}
              </div>
              {isExpanded && (
                <div className="step-detail">
                  {step.error && (
                    <div className="step-error">⚠ {step.error}</div>
                  )}
                  {step.output && Object.keys(step.output).length > 0 && (
                    <pre className="step-output">
                      {JSON.stringify(step.output, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </li>
          );
        })}
        {steps.length === 0 && (
          <li className="step-empty">아직 실행된 스텝이 없습니다.</li>
        )}
      </ol>
    </div>
  );
}
