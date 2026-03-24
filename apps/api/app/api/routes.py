from pathlib import Path
import threading
import hashlib
import hmac
import json
import re
import shutil
import uuid
from uuid import UUID

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.state_machine import JobStatus
from app.core.time_utils import now_utc
from app import conversation_models
from app.models import FileModel, JobModel, ProjectModel, PromptLogModel, TaskModel
from app.schemas.plan import PlanResult
from app.schemas.request_response import (
    ArtifactSummary,
    CreateJobRequest,
    CreateJobResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    CreateOpsApiKeyRequest,
    CreateOpsApiKeyResponse,
    JobDetailResponse,
    DeadLetterItem,
    DeadLetterListResponse,
    DeadLetterReplayRequest,
    DeadLetterReplayResponse,
    JobHistoryItem,
    ProjectJobsResponse,
    PromptLogSummary,
    ReplayAuditItem,
    ReplayAuditListResponse,
    TaskSummary,
    UploadFileResponse,
)
from app.services.anthropic_skills import AnthropicSkillsDocumentGenerator
from app.services.anthropic_skills import anthropic_skills_available
from app.services.anthropic_skills import default_document_provider
from app.services.document_ir import build_slides_ir
from app.services.document_ir import build_sheet_ir_from_outline
from app.services.document_ir import build_word_ir_from_markdown
from app.services.document_ir import extract_text_from_ir
from app.services.document_ir import parse_document_to_ir
from app.services.document_ir import render_ir_to_docx_bytes
from app.services.document_ir import render_ir_to_pptx_bytes
from app.services.document_ir import render_ir_to_xlsx_bytes
from app.services.document_ir import summarize_document_ir
from app.services.job_dispatcher import dispatch_job
from app.services.llm_router import get_llm_provider
from app.services.planner_agent import PlannerAgent
from app.adapters.telegram.handlers import process_update
from app.adapters.telegram.dispatcher import DispatchResult
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_agent_run,
    serialize_artifact,
    serialize_conversation,
    serialize_message,
    serialize_team_activity,
    serialize_team_dependency,
    serialize_team_message,
    serialize_team_run,
    serialize_team_session,
    serialize_team_task,
)
from app.orchestrator.engine import orchestrator
from app.team_runtime.service import TeamRunService

router = APIRouter()

AUTO_REVIEW_MAX_ROUNDS = 2
TEAM_EXPORT_PROJECT_NAME = "Agent Team Exports"
WEB_UPLOAD_PROJECT_NAME = "Web Workspace Uploads"
AUTO_REVIEW_REJECT_KEYWORDS = (
    "반려",
    "재작성",
    "재작업",
    "수정",
    "보강",
    "누락",
    "부족",
    "오류",
    "불명확",
    "출처",
)
TEAM_ARTIFACT_STATUS_PHRASES = (
    "작성 중",
    "대기 중",
    "준비 중",
    "제출 필요",
    "초안 대기",
    "검토 준비 완료",
    "다음 실행",
    "검토 예정",
)
PRESENTATION_REQUEST_KEYWORDS = (
    "발표",
    "발표자료",
    "발표 자료",
    "ppt",
    "pptx",
    "슬라이드",
    "presentation",
    "deck",
)
SHEET_REQUEST_KEYWORDS = (
    "xlsx",
    "excel",
    "시트",
    "sheet",
    "표",
    "예산표",
    "스프레드시트",
)
OUTPUT_TYPE_PRESET_MAP = {
    "docx": "docx_brief_team",
    "xlsx": "xlsx_analysis_team",
    "pptx": "presentation_team",
}


@router.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _normalize_oversight_mode(value: object | None) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized not in {"manual", "auto"}:
        raise HTTPException(status_code=400, detail="oversight_mode must be manual or auto")
    return normalized


def _normalize_output_type(value: object | None) -> str:
    normalized = str(value or "docx").strip().lower()
    if normalized not in {"docx", "xlsx", "pptx"}:
        raise HTTPException(status_code=400, detail="output_type must be docx, xlsx, or pptx")
    return normalized


def _infer_output_type_from_request_text(request_text: str | None) -> str:
    lowered = str(request_text or "").strip().lower()
    if any(keyword in lowered for keyword in PRESENTATION_REQUEST_KEYWORDS):
        return "pptx"
    if any(keyword in lowered for keyword in SHEET_REQUEST_KEYWORDS):
        return "xlsx"
    return "docx"


def _infer_task_kind(artifact_goal: str) -> str:
    goal = str(artifact_goal or "").strip().lower()
    if goal == "review_notes":
        return "review"
    if goal == "final":
        return "final"
    if goal in {"brief", "decision"}:
        return "plan"
    return "draft"


def _slugify_filename(value: str, default: str = "docflow") -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "-", (value or "").strip()).strip("-_.")
    return slug[:80] or default


def _ensure_team_export_project(db: Session) -> ProjectModel:
    project = (
        db.execute(
            select(ProjectModel).where(ProjectModel.name == TEAM_EXPORT_PROJECT_NAME).limit(1)
        ).scalar_one_or_none()
    )
    if project:
        return project
    project = ProjectModel(
        name=TEAM_EXPORT_PROJECT_NAME,
        description="Internal project for team run exports",
    )
    db.add(project)
    db.flush()
    return project


def _ensure_web_upload_project(db: Session) -> ProjectModel:
    project = (
        db.execute(
            select(ProjectModel).where(ProjectModel.name == WEB_UPLOAD_PROJECT_NAME).limit(1)
        ).scalar_one_or_none()
    )
    if project:
        return project
    project = ProjectModel(
        name=WEB_UPLOAD_PROJECT_NAME,
        description="Internal project for workspace uploads",
    )
    db.add(project)
    db.flush()
    return project


def _persist_team_export_file(
    db: Session,
    *,
    run: conversation_models.TeamRunModel,
    filename: str,
    content: bytes,
    mime_type: str,
    extracted_text: str = "",
) -> FileModel:
    project = _ensure_team_export_project(db)
    output_dir = Path(settings.upload_dir) / str(project.id) / "generated" / "team_runs" / str(run.id)
    output_dir.mkdir(parents=True, exist_ok=True)
    stored_path = output_dir / filename
    stored_path.write_bytes(content)
    file_ir = parse_document_to_ir(str(stored_path), mime_type)
    document_type = str(file_ir.get("document_type") or "")
    document_summary = summarize_document_ir(file_ir)
    file_row = FileModel(
        project_id=project.id,
        job_id=None,
        original_name=filename,
        stored_path=str(stored_path),
        mime_type=mime_type,
        size=stored_path.stat().st_size,
        source_type="generated",
        extracted_text=extracted_text,
        document_type=document_type,
        document_summary=document_summary,
        created_at=now_utc(),
    )
    db.add(file_row)
    db.flush()
    return file_row


def _file_analysis_payload(file_row: FileModel) -> dict:
    file_ir = parse_document_to_ir(file_row.stored_path, file_row.mime_type)
    summary = summarize_document_ir(file_ir)
    return {
        "document_type": str(file_ir.get("document_type") or file_row.document_type or ""),
        "document_summary": summary or file_row.document_summary or "",
        "document_ir": file_ir,
    }


def _collect_source_files(
    db: Session,
    source_file_ids: list[str] | list[UUID] | None,
) -> tuple[list[FileModel], str]:
    ids = [str(item) for item in (source_file_ids or []) if str(item).strip()]
    if not ids:
        return [], ""
    rows: list[FileModel] = []
    summaries: list[str] = []
    for item in ids:
        try:
            file_id = UUID(str(item))
        except ValueError:
            continue
        file_row = db.get(FileModel, file_id)
        if not file_row:
            continue
        rows.append(file_row)
        analysis = _file_analysis_payload(file_row)
        summaries.append(
            f"[{file_row.original_name}] {analysis['document_summary']}\n{extract_text_from_ir(analysis['document_ir'])[:1200].strip()}"
        )
    return rows, "\n\n".join(item for item in summaries if item).strip()


def _build_deliverable_ir(
    *,
    title: str,
    content: str,
    run: conversation_models.TeamRunModel | None = None,
) -> dict:
    preset = _run_workflow_preset(run)
    output_type = str(getattr(run, "output_type", "") or "").strip().lower()
    if preset == "presentation_team":
        structured = _build_structured_deliverable(title, content)
        return build_slides_ir(title, structured.get("slide_outline") or [], sources=structured.get("sources") or [])
    if output_type == "xlsx":
        rows = [["섹션", "내용"]]
        current_section = ""
        for raw in str(content or "").splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.startswith("## "):
                current_section = stripped[3:].strip()
                continue
            if stripped.startswith(("- ", "* ")):
                rows.append([current_section or "요약", stripped[2:].strip()])
            else:
                rows.append([current_section or "요약", stripped])
        return build_sheet_ir_from_outline(title, rows, sheet_name="summary")
    return build_word_ir_from_markdown(title, content)


def _has_active_ops_keys(db: Session) -> bool:
    from app.models import OpsApiKeyModel

    row = db.execute(
        select(OpsApiKeyModel.id).where(
            OpsApiKeyModel.is_active.is_(True)).limit(1)
    ).first()
    return row is not None


def _authorize_ops_request(
    db: Session,
    x_ops_token: str | None,
    x_ops_key_id: str | None,
    x_ops_key_secret: str | None,
) -> str:
    from app.models import OpsApiKeyModel

    expected = settings.ops_api_token.strip()
    has_keys = _has_active_ops_keys(db)

    if expected and x_ops_token and hmac.compare_digest(x_ops_token, expected):
        return "legacy-token"

    if x_ops_key_id and x_ops_key_secret:
        key = db.execute(
            select(OpsApiKeyModel).where(
                OpsApiKeyModel.key_id == x_ops_key_id,
                OpsApiKeyModel.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if key and hmac.compare_digest(key.secret_hash, _secret_hash(x_ops_key_secret)):
            key.last_used_at = now_utc()
            db.add(key)
            db.commit()
            return f"apikey:{key.key_id}"

    if not expected and not has_keys:
        return "anonymous"

    raise HTTPException(status_code=401, detail="Invalid ops credentials")


def _replay_marker_path(file_name: str) -> Path:
    return Path(settings.dead_letter_dir) / "replayed" / f"{file_name}.done.json"


def _append_replay_audit(
    *,
    action: str,
    file_name: str,
    job_id: str,
    actor: str,
    result: str,
) -> None:
    dead_letter_dir = Path(settings.dead_letter_dir)
    dead_letter_dir.mkdir(parents=True, exist_ok=True)
    audit_path = dead_letter_dir / "replay_audit.jsonl"
    record = {
        "at": now_utc().isoformat(),
        "action": action,
        "file_name": file_name,
        "job_id": job_id,
        "actor": actor,
        "result": result,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/projects", response_model=CreateProjectResponse)
def create_project(payload: CreateProjectRequest, db: Session = Depends(get_db)) -> CreateProjectResponse:
    project = ProjectModel(name=payload.name, description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)

    return CreateProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
    )


@router.get("/api/projects/{project_id}/jobs", response_model=ProjectJobsResponse)
def list_project_jobs(project_id: UUID, db: Session = Depends(get_db)) -> ProjectJobsResponse:
    project = db.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    rows = db.execute(
        select(JobModel)
        .where(JobModel.project_id == project_id)
        .order_by(JobModel.created_at.desc())
    ).scalars().all()

    return ProjectJobsResponse(
        project_id=project_id,
        jobs=[
            JobHistoryItem(
                id=item.id,
                job_type=item.job_type,
                status=item.status,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in rows
        ],
    )


@router.post("/api/projects/{project_id}/files", response_model=UploadFileResponse)
def upload_file(
    project_id: UUID,
    uploaded_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadFileResponse:
    project = db.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    filename = uploaded_file.filename or "uploaded.bin"

    project_dir = Path(settings.upload_dir) / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)
    stored_path = project_dir / filename

    with stored_path.open("wb") as f:
        shutil.copyfileobj(uploaded_file.file, f)

    size = stored_path.stat().st_size
    file_ir = parse_document_to_ir(str(stored_path), uploaded_file.content_type)
    extracted_text = extract_text_from_ir(file_ir)
    document_type = str(file_ir.get("document_type") or "")
    document_summary = summarize_document_ir(file_ir)

    file_row = FileModel(
        project_id=project_id,
        job_id=None,
        original_name=filename,
        stored_path=str(stored_path),
        mime_type=uploaded_file.content_type or "application/octet-stream",
        size=size,
        source_type="upload",
        extracted_text=extracted_text,
        document_type=document_type,
        document_summary=document_summary,
    )
    db.add(file_row)
    db.commit()
    db.refresh(file_row)

    return UploadFileResponse(
        id=file_row.id,
        project_id=file_row.project_id,
        original_name=file_row.original_name,
        mime_type=file_row.mime_type,
        size=file_row.size,
        source_type=file_row.source_type,
        document_type=document_type,
        document_summary=document_summary,
        document_ir=file_ir,
        created_at=file_row.created_at,
    )


@router.post("/web/files", response_model=UploadFileResponse)
def upload_web_file(
    uploaded_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadFileResponse:
    project = _ensure_web_upload_project(db)
    return upload_file(project.id, uploaded_file, db)


@router.post("/api/projects/{project_id}/jobs", response_model=CreateJobResponse)
async def create_job(
    project_id: UUID,
    payload: CreateJobRequest,
    async_dispatch: bool = Query(
        False, description="Return immediately and dispatch in background thread"),
    db: Session = Depends(get_db),
) -> CreateJobResponse:
    project = db.get(ProjectModel, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    planner = PlannerAgent(provider=get_llm_provider())
    plan: PlanResult = await planner.plan(payload.request, payload.output_types)
    now = now_utc()

    job = JobModel(
        project_id=project_id,
        job_type=plan.job_type,
        request_text=payload.request,
        status=JobStatus.QUEUED,
        created_by="api_user",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()

    for task_type in plan.tasks:
        db.add(
            TaskModel(
                job_id=job.id,
                task_type=task_type,
                status="PENDING",
                input_payload_json={},
                output_payload_json={},
            )
        )

    db.commit()

    # Keep legacy synchronous behavior by default (tests rely on this).
    # For web clients, async_dispatch=true avoids request timeout in inline mode.
    if async_dispatch and settings.execution_backend == "inline":
        threading.Thread(target=dispatch_job, args=(
            job.id,), daemon=True).start()
    else:
        dispatch_job(job.id)

    db.refresh(job)

    return CreateJobResponse(
        job_id=job.id,
        project_id=job.project_id,
        status=job.status,
        job_type=job.job_type,
    )


@router.get("/api/jobs/{job_id}", response_model=JobDetailResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)) -> JobDetailResponse:
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetailResponse(
        id=job.id,
        project_id=job.project_id,
        job_type=job.job_type,
        request_text=job.request_text,
        status=job.status,
        created_by=job.created_by,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/api/jobs/{job_id}/artifacts")
def get_job_artifacts(job_id: UUID, db: Session = Depends(get_db)) -> dict:
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    artifacts = db.execute(select(FileModel).where(
        FileModel.job_id == job_id)).scalars().all()
    tasks = db.execute(select(TaskModel).where(
        TaskModel.job_id == job_id)).scalars().all()

    return {
        "job_id": str(job_id),
        "artifacts": [
            ArtifactSummary(
                id=item.id,
                original_name=item.original_name,
                stored_path=item.stored_path,
                source_type=item.source_type,
                document_type=item.document_type or "",
                document_summary=item.document_summary or "",
            ).model_dump()
            for item in artifacts
        ],
        "tasks": [
            TaskSummary(id=task.id, task_type=task.task_type,
                        status=task.status).model_dump()
            for task in tasks
        ],
    }


@router.get("/api/files/{file_id}/analysis")
def get_file_analysis(file_id: UUID, db: Session = Depends(get_db)) -> dict:
    file_row = db.get(FileModel, file_id)
    if not file_row:
        raise HTTPException(status_code=404, detail="File not found")
    analysis = _file_analysis_payload(file_row)
    return {
        "file": {
            "id": str(file_row.id),
            "project_id": str(file_row.project_id),
            "original_name": file_row.original_name,
            "mime_type": file_row.mime_type,
            "size": file_row.size,
            "source_type": file_row.source_type,
            "document_type": analysis["document_type"],
            "document_summary": analysis["document_summary"],
            "created_at": file_row.created_at.isoformat(),
        },
        "document_ir": analysis["document_ir"],
        "extracted_text": file_row.extracted_text or extract_text_from_ir(analysis["document_ir"]),
    }


@router.get("/api/jobs/{job_id}/status/stream")
async def stream_job_status(job_id: UUID):
    """SSE(Server-Sent Events) 엔드포인트 — 작업 완료/실패 시 스트림 종료."""
    check_db = SessionLocal()
    try:
        job = check_db.get(JobModel, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
    finally:
        check_db.close()

    async def _event_generator():
        terminal_statuses = {JobStatus.COMPLETED,
                             JobStatus.FAILED, JobStatus.CANCELLED}
        while True:
            loop_db = SessionLocal()
            try:
                current_job = loop_db.get(JobModel, job_id)
                tasks = loop_db.execute(
                    select(TaskModel).where(TaskModel.job_id == job_id)
                ).scalars().all()
            finally:
                loop_db.close()

            event_data = json.dumps({
                "job_id": str(job_id),
                "status": current_job.status if current_job else "unknown",
                "tasks": [
                    {"id": str(t.id), "task_type": t.task_type,
                     "status": t.status}
                    for t in tasks
                ],
            })
            yield f"data: {event_data}\n\n"

            if current_job and JobStatus(current_job.status) in terminal_statuses:
                break
            await asyncio.sleep(2)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: UUID, db: Session = Depends(get_db)) -> dict:
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    current = JobStatus(job.status)
    if current not in {JobStatus.FAILED, JobStatus.CANCELLED}:
        raise HTTPException(
            status_code=400, detail="Retry only allowed from FAILED/CANCELLED")

    job.status = JobStatus.QUEUED
    job.updated_at = now_utc()
    db.add(job)
    db.commit()
    dispatch_job(job_id)
    db.refresh(job)
    return {"job_id": str(job_id), "status": job.status}


@router.get("/api/jobs/{job_id}/prompt-logs")
def get_job_prompt_logs(job_id: UUID, db: Session = Depends(get_db)) -> dict:
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    task_ids = db.execute(select(TaskModel.id).where(
        TaskModel.job_id == job_id)).scalars().all()
    if not task_ids:
        return {"job_id": str(job_id), "logs": []}

    logs = db.execute(
        select(PromptLogModel)
        .where(PromptLogModel.task_id.in_(task_ids))
        .order_by(PromptLogModel.created_at.desc())
    ).scalars().all()

    return {
        "job_id": str(job_id),
        "logs": [
            PromptLogSummary(
                id=item.id,
                task_id=item.task_id,
                provider=item.provider,
                model=item.model,
                created_at=item.created_at,
            ).model_dump()
            for item in logs
        ],
    }


@router.get("/api/files/{file_id}/download")
def download_file(file_id: UUID, db: Session = Depends(get_db)) -> FileResponse:
    file_row = db.get(FileModel, file_id)
    if not file_row:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(file_row.stored_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file not found")

    return FileResponse(
        path=str(file_path),
        media_type=file_row.mime_type,
        filename=file_row.original_name,
    )


@router.get("/api/ops/dead-letters", response_model=DeadLetterListResponse)
def list_dead_letters(
    limit: int = 20,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> DeadLetterListResponse:
    _authorize_ops_request(db, x_ops_token, x_ops_key_id, x_ops_key_secret)

    dead_letter_dir = Path(settings.dead_letter_dir)
    if not dead_letter_dir.exists():
        return DeadLetterListResponse(items=[])

    files = sorted(dead_letter_dir.glob("job_*.json"), reverse=True)
    items: list[DeadLetterItem] = []
    safe_limit = max(1, min(limit, 200))

    for file_path in files[:safe_limit]:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            items.append(
                DeadLetterItem(
                    file_name=file_path.name,
                    path=str(file_path),
                    job_id=str(payload.get("job_id", "")),
                    reason=str(payload.get("reason", "")),
                    retries=int(payload.get("retries", 0)),
                    created_at=str(payload.get("created_at", "")),
                )
            )
        except Exception:
            continue

    return DeadLetterListResponse(items=items)


@router.post("/api/ops/dead-letters/replay", response_model=DeadLetterReplayResponse)
def replay_dead_letter(
    payload: DeadLetterReplayRequest,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> DeadLetterReplayResponse:
    actor = _authorize_ops_request(
        db, x_ops_token, x_ops_key_id, x_ops_key_secret)

    # Prevent path traversal by accepting only plain filenames.
    file_name = Path(payload.file_name).name
    file_path = Path(settings.dead_letter_dir) / file_name

    if not file_path.exists():
        raise HTTPException(
            status_code=404, detail="Dead letter file not found")

    data = json.loads(file_path.read_text(encoding="utf-8"))
    job_id = str(data.get("job_id", ""))
    replay_marker = _replay_marker_path(file_name)
    replayed_before = replay_marker.exists()

    if not payload.requeue:
        preview_message = "Set requeue=true to replay this dead-letter job."
        if replayed_before:
            preview_message = "This dead-letter file was already replayed once. Set force_requeue=true to replay again."

        _append_replay_audit(
            action="preview",
            file_name=file_name,
            job_id=job_id,
            actor=actor,
            result="ok",
        )
        return DeadLetterReplayResponse(
            file_name=file_name,
            job_id=job_id,
            requeued=False,
            deleted=False,
            status="PREVIEW",
            message=preview_message,
        )

    if replayed_before and not payload.force_requeue:
        _append_replay_audit(
            action="requeue",
            file_name=file_name,
            job_id=job_id,
            actor=actor,
            result="blocked_duplicate",
        )
        raise HTTPException(
            status_code=409,
            detail="Dead letter already replayed; set force_requeue=true to override",
        )

    try:
        job_uuid = UUID(job_id)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="Invalid job_id in dead letter") from exc

    job = db.get(JobModel, job_uuid)
    if not job:
        raise HTTPException(status_code=404, detail="Target job not found")

    job.status = JobStatus.QUEUED
    job.updated_at = now_utc()
    db.add(job)
    db.commit()

    dispatch_job(job_uuid)
    db.refresh(job)

    replay_marker.parent.mkdir(parents=True, exist_ok=True)
    replay_marker.write_text(
        json.dumps(
            {
                "file_name": file_name,
                "job_id": job_id,
                "replayed_at": now_utc().isoformat(),
                "actor": actor,
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    _append_replay_audit(
        action="requeue",
        file_name=file_name,
        job_id=job_id,
        actor=actor,
        result="dispatched",
    )

    deleted = False
    if payload.delete_on_success:
        file_path.unlink(missing_ok=True)
        deleted = True

    return DeadLetterReplayResponse(
        file_name=file_name,
        job_id=job_id,
        requeued=True,
        deleted=deleted,
        status=job.status,
        message="Dead-letter replay dispatched.",
    )


@router.post("/api/ops/api-keys", response_model=CreateOpsApiKeyResponse)
def create_ops_api_key(
    payload: CreateOpsApiKeyRequest,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> CreateOpsApiKeyResponse:
    from app.models import OpsApiKeyModel

    actor = _authorize_ops_request(
        db, x_ops_token, x_ops_key_id, x_ops_key_secret)
    if actor == "anonymous":
        raise HTTPException(
            status_code=403,
            detail="Configure OPS_API_TOKEN first to bootstrap API keys",
        )

    existing = db.execute(
        select(OpsApiKeyModel).where(OpsApiKeyModel.key_id == payload.key_id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="key_id already exists")

    row = OpsApiKeyModel(
        key_id=payload.key_id,
        secret_hash=_secret_hash(payload.key_secret),
        role=payload.role,
        is_active=True,
    )
    db.add(row)
    db.commit()

    return CreateOpsApiKeyResponse(
        key_id=row.key_id,
        role=row.role,
        is_active=row.is_active,
    )


@router.get("/api/ops/replay-audit", response_model=ReplayAuditListResponse)
def list_replay_audit(
    limit: int = 50,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> ReplayAuditListResponse:
    _authorize_ops_request(db, x_ops_token, x_ops_key_id, x_ops_key_secret)

    audit_path = Path(settings.dead_letter_dir) / "replay_audit.jsonl"
    if not audit_path.exists():
        return ReplayAuditListResponse(items=[])

    safe_limit = max(1, min(limit, 500))
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    selected = lines[-safe_limit:]

    items: list[ReplayAuditItem] = []
    for line in reversed(selected):
        try:
            payload = json.loads(line)
            items.append(
                ReplayAuditItem(
                    at=str(payload.get("at", "")),
                    action=str(payload.get("action", "")),
                    file_name=str(payload.get("file_name", "")),
                    job_id=str(payload.get("job_id", "")),
                    actor=str(payload.get("actor", "")),
                    result=str(payload.get("result", "")),
                )
            )
        except Exception:
            continue

    return ReplayAuditListResponse(items=items)


# ── Telegram Webhook ──────────────────────────────────────────────────────────

@router.post("/telegram/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Receive Telegram webhook updates."""
    secret = settings.telegram_webhook_secret
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    background_tasks.add_task(process_update, update, db)
    return {"ok": True}


@router.post("/telegram/setup-webhook", status_code=200)
async def setup_telegram_webhook(
    _: str = Depends(lambda: None),
    db: Session = Depends(get_db),
):
    """Register the webhook URL with Telegram (call once after deploy)."""
    from app.adapters.telegram.bot import bot as tg_bot
    if not settings.telegram_webhook_url:
        raise HTTPException(status_code=400, detail="TELEGRAM_WEBHOOK_URL not configured")
    ok = await tg_bot.set_webhook(
        settings.telegram_webhook_url,
        settings.telegram_webhook_secret,
    )
    return {"ok": ok, "webhook_url": settings.telegram_webhook_url}


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return serialize_conversation(conv)


@router.get("/conversations/{conversation_id}/messages")
def list_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = svc.list_messages(conversation_id, limit=limit)
    return {"items": [serialize_message(m) for m in messages]}


@router.get("/conversations/{conversation_id}/runs")
def list_conversation_runs(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    runs = svc.list_agent_runs(conversation_id)
    return {"items": [serialize_agent_run(r) for r in runs]}


@router.post("/conversations/{conversation_id}/stop", status_code=200)
def stop_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    svc.update_conversation_status(conversation_id, "idle")
    db.commit()
    return {"ok": True, "status": "idle"}


class _WebDispatcher:
    """Dispatcher adapter that persists rendered agent turns without Telegram send."""

    def __init__(self, base_dispatcher):
        self._base = base_dispatcher
        self._registry = base_dispatcher._registry

    def resolve_identity(self, role: str) -> str:
        return self._base.resolve_identity(role)

    async def dispatch(
        self,
        role: str,
        chat_id: str | int,
        body: str,
        next_role: str | None = None,
        include_handoff_hint: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> DispatchResult:
        rendered = self._base.build_message(
            role,
            body,
            next_role,
            include_handoff_hint=include_handoff_hint,
        )
        return DispatchResult(
            identity=self.resolve_identity(role),
            telegram_message_id=None,
            rendered_text=rendered,
        )

    async def dispatch_status(
        self,
        identity: str,
        chat_id: str | int,
        text: str,
        message_thread_id: int | None = None,
    ) -> int | None:
        return None


@router.get("/web/chats")
def list_web_chats(db: Session = Depends(get_db)):
    rows = (
        db.query(conversation_models.ConversationModel)
        .filter(conversation_models.ConversationModel.platform == "web")
        .order_by(conversation_models.ConversationModel.updated_at.desc())
        .limit(50)
        .all()
    )
    return {"items": [serialize_conversation(c) for c in rows]}


@router.get("/web/agents")
def list_web_agents():
    return {"agents": orchestrator.list_agents_info()}


@router.get("/web/team-runs")
def list_web_team_runs(db: Session = Depends(get_db)):
    svc = TeamRunService(db)
    rows = svc.list_runs(limit=50)
    return {"items": [serialize_team_run(run) for run in rows]}


@router.post("/web/team-runs", status_code=201)
def create_web_team_run(
    payload: dict,
    db: Session = Depends(get_db),
):
    title = str(payload.get("title") or "Web Team Run").strip()[:120]
    requested_by = str(payload.get("requested_by") or "web_user").strip() or "web_user"
    mode = "team-autonomous"
    oversight_mode = _normalize_oversight_mode(payload.get("oversight_mode"))
    output_type = _normalize_output_type(payload.get("output_type"))
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    raw_selected = payload.get("selected_agents")
    if not isinstance(raw_selected, list):
        raw_selected = ["planner", "writer", "critic", "manager"]
    raw_source_file_ids = payload.get("source_file_ids")
    if not isinstance(raw_source_file_ids, list):
        raw_source_file_ids = []
    selected = _normalize_web_selected_agents(
        selected=raw_selected,
        valid_handles=valid,
        mode=mode,
    )
    source_files, source_ir_summary = _collect_source_files(db, raw_source_file_ids)

    conv = conversation_models.ConversationModel(
        platform="web",
        chat_id=f"team-web:{uuid.uuid4().hex}",
        topic_id=None,
        title=title or "Web Team Run",
        mode=mode,
        autonomy_level=mode,
        selected_agents=selected,
        status="idle",
    )
    db.add(conv)
    db.flush()

    team_svc = TeamRunService(db)
    run = team_svc.create_run(
        conversation_id=conv.id,
        title=title or "Web Team Run",
        mode=mode,
        oversight_mode=oversight_mode,
        requested_by=requested_by,
        output_type=output_type,
        document_provider=default_document_provider(),
        selected_agents=selected,
        source_file_ids=[str(item.id) for item in source_files],
        source_ir_summary=source_ir_summary,
        status="idle",
    )
    _ensure_team_run_sessions(team_svc, run)
    team_svc.create_activity(
        team_run_id=run.id,
        event_type="run_created",
        actor_handle="planner",
        summary="PM이 새 팀 실행을 준비했습니다.",
    )
    db.commit()
    db.refresh(run)
    return _build_team_board_snapshot(db, run)


@router.get("/web/team-runs/{team_run_id}", status_code=200)
def get_web_team_run(
    team_run_id: UUID,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    return serialize_team_run(run)


@router.get("/web/team-runs/{team_run_id}/board", status_code=200)
def get_web_team_run_board(
    team_run_id: UUID,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    return _build_team_board_snapshot(db, run)


@router.get("/web/team-runs/{team_run_id}/activity", status_code=200)
def get_web_team_run_activity(
    team_run_id: UUID,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    return {"items": [serialize_team_activity(event) for event in svc.list_activity(team_run_id, limit=120)]}


@router.get("/web/team-runs/{team_run_id}/sessions", status_code=200)
def get_web_team_run_sessions(
    team_run_id: UUID,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    _ensure_team_run_sessions(svc, run)
    db.commit()
    return {"items": [serialize_team_session(item) for item in svc.list_sessions(team_run_id)]}


@router.post("/web/team-runs/{team_run_id}/sessions/spawn", status_code=201)
def spawn_web_team_run_session(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    handle = str(payload.get("handle") or "").strip().lower()
    if not handle:
        raise HTTPException(status_code=400, detail="handle is required")
    if run.selected_agents and handle not in set(run.selected_agents):
        raise HTTPException(status_code=400, detail="handle must be in selected_agents")
    role = str(payload.get("role") or "worker").strip().lower() or "worker"
    display_name = str(payload.get("display_name") or handle).strip() or handle
    session = svc.create_session(
        team_run_id=run.id,
        handle=handle,
        role=role,
        display_name=display_name,
    )
    svc.create_activity(
        team_run_id=run.id,
        event_type="session_spawned",
        actor_handle="planner",
        target_handle=handle,
        summary=f"planner가 {handle} 세션을 추가로 생성했습니다.",
        payload={"session_id": str(session.id)},
    )
    db.commit()
    return {"item": serialize_team_session(session)}


@router.get("/web/team-runs/{team_run_id}/messages", status_code=200)
def get_web_team_run_messages(
    team_run_id: UUID,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    return {"items": [serialize_team_message(item) for item in svc.list_inbox_messages(team_run_id, limit=200)]}


@router.post("/web/team-runs/{team_run_id}/messages", status_code=201)
def create_web_team_run_message(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    from_session_id = _coerce_optional_uuid(payload.get("from_session_id"))
    to_session_id = _coerce_optional_uuid(payload.get("to_session_id"))
    related_task_id = _coerce_optional_uuid(payload.get("related_task_id"))
    for session_id in (from_session_id, to_session_id):
        if not session_id:
            continue
        session = svc.get_session(session_id)
        if not session or session.team_run_id != run.id:
            raise HTTPException(status_code=400, detail="session must belong to the same run")
    item = svc.create_inbox_message(
        team_run_id=run.id,
        from_session_id=from_session_id,
        to_session_id=to_session_id,
        related_task_id=related_task_id,
        message_type=str(payload.get("message_type") or "direct").strip().lower() or "direct",
        subject=str(payload.get("subject") or "").strip(),
        content=content,
    )
    db.commit()
    return {"item": serialize_team_message(item)}


@router.post("/web/tasks/{task_id}/claim", status_code=200)
def claim_web_team_task(
    task_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    task = team_svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    session_id = _coerce_optional_uuid(payload.get("session_id"))
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    session = team_svc.get_session(session_id)
    if not session or session.team_run_id != task.team_run_id:
        raise HTTPException(status_code=400, detail="session_id must belong to the same run")
    if task.claim_status != "open" or task.status != "todo":
        raise HTTPException(status_code=400, detail="task is not claimable")
    team_svc.claim_task(task_id=task.id, session_id=session.id)
    team_svc.create_activity(
        team_run_id=task.team_run_id,
        task_id=task.id,
        event_type="task_claimed",
        actor_handle=session.handle,
        summary=f"{session.handle} 세션이 '{task.title}' 작업을 선점했습니다.",
        payload={"session_id": str(session.id)},
    )
    db.commit()
    run = team_svc.get_run(task.team_run_id)
    if not run:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, run)


@router.post("/web/tasks/{task_id}/release", status_code=200)
def release_web_team_task_claim(
    task_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    task = team_svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.claim_status != "claimed":
        raise HTTPException(status_code=400, detail="task is not claimed")
    claimed_session = team_svc.get_session(task.claimed_by_session_id) if task.claimed_by_session_id else None
    team_svc.release_task_claim(task.id)
    team_svc.create_activity(
        team_run_id=task.team_run_id,
        task_id=task.id,
        event_type="task_released",
        actor_handle=claimed_session.handle if claimed_session else None,
        summary=f"'{task.title}' 작업 선점이 해제되었습니다.",
    )
    db.commit()
    run = team_svc.get_run(task.team_run_id)
    if not run:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, run)


@router.post("/web/team-runs/{team_run_id}/tasks", status_code=201)
async def create_web_team_task(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    run = team_svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")

    actor_handle = str(payload.get("actor_handle") or "planner").strip().lower() or "planner"
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    owner_handle = str(payload.get("owner_handle") or "").strip().lower()
    artifact_goal = str(payload.get("artifact_goal") or "draft").strip().lower()
    priority = int(payload.get("priority") or 50)
    review_required = bool(payload.get("review_required", False))
    parent_task_id = _coerce_optional_uuid(payload.get("parent_task_id"))
    dependency_ids = _coerce_uuid_list(payload.get("depends_on_task_ids"))

    if not title or not description or not owner_handle:
        raise HTTPException(status_code=400, detail="title, description, owner_handle are required")
    _validate_task_owner(run, owner_handle)
    _validate_artifact_goal(artifact_goal)
    if parent_task_id:
        parent_task = team_svc.get_task(parent_task_id)
        if not parent_task or parent_task.team_run_id != run.id:
            raise HTTPException(status_code=400, detail="parent_task_id must belong to the same run")

    task = team_svc.create_task(
        team_run_id=run.id,
        title=title,
        description=description,
        owner_handle=owner_handle,
        artifact_goal=artifact_goal,
        created_by_handle=actor_handle,
        status="todo",
        priority=priority,
        parent_task_id=parent_task_id,
        review_required=review_required,
        task_kind=_infer_task_kind(artifact_goal),
    )
    _validate_dependency_ids(team_svc, run.id, task.id, dependency_ids)
    if _creates_dependency_cycle(team_svc, run.id, task.id, dependency_ids):
        raise HTTPException(status_code=400, detail="dependency cycle detected")
    team_svc.replace_dependencies(team_task_id=task.id, depends_on_task_ids=dependency_ids)
    team_svc.create_activity(
        team_run_id=run.id,
        task_id=task.id,
        event_type="task_split" if parent_task_id else "task_created",
        actor_handle=actor_handle,
        target_handle=owner_handle,
        summary=(
            f"{actor_handle}가 '{title}' 하위 작업을 만들고 {owner_handle}에게 배정했습니다."
            if parent_task_id
            else f"{actor_handle}가 새 작업 '{title}'을 만들고 {owner_handle}에게 배정했습니다."
        ),
        payload={"depends_on_task_ids": [str(item) for item in dependency_ids]},
    )
    _create_team_inbox_message(
        team_svc,
        run,
        from_handle=actor_handle,
        to_handle=owner_handle,
        related_task_id=task.id,
        message_type="task_assignment",
        subject="새 작업 배정",
        content=f"'{title}' 작업이 배정되었습니다. 목표 산출물은 {artifact_goal}입니다.",
    )

    if _task_is_ready(team_svc, run.id, task.id, db):
        team_svc.update_run(run.id, status="active")
        db.commit()
        await _run_team_scheduler(db, run.id)
    else:
        _refresh_team_run_status(db, team_svc, run.id)
        db.commit()

    refreshed_run = team_svc.get_run(run.id)
    if not refreshed_run:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, refreshed_run)


@router.get("/web/tasks/{task_id}", status_code=200)
def get_web_team_task_detail(
    task_id: UUID,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    task = team_svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    run = team_svc.get_run(task.team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")

    return _build_team_task_detail(db, task, run=run)


@router.put("/web/team-runs/{team_run_id}/agents", status_code=200)
def update_web_team_run_agents(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    svc = TeamRunService(db)
    run = svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    selected = payload.get("selected_agents")
    if not isinstance(selected, list):
        raise HTTPException(status_code=400, detail="selected_agents must be a list")
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    normalized = _normalize_web_selected_agents(
        selected=selected,
        valid_handles=valid,
        mode=run.mode,
    )
    svc.update_run(team_run_id, selected_agents=normalized)
    if run.conversation_id:
        conv = db.get(conversation_models.ConversationModel, run.conversation_id)
        if conv:
            conv.selected_agents = normalized
            conv.updated_at = now_utc()
    db.commit()
    db.refresh(run)
    return {"ok": True, "run": serialize_team_run(run)}


@router.post("/web/team-runs/{team_run_id}/requests", status_code=202)
async def send_web_team_run_request(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    text = str(payload.get("text") or "").strip()
    sender_name = str(payload.get("sender_name") or "web_user").strip() or "web_user"
    output_type = payload.get("output_type")
    raw_source_file_ids = payload.get("source_file_ids")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    team_svc = TeamRunService(db)
    conv_svc = ConversationService(db)
    run = team_svc.get_run(team_run_id)
    if not run or not run.conversation_id:
        raise HTTPException(status_code=404, detail="Team run not found")
    if not isinstance(raw_source_file_ids, list):
        raw_source_file_ids = list(run.source_file_ids or [])
    if output_type is not None:
        run.output_type = _normalize_output_type(output_type)
    elif not str(run.request_text or "").strip():
        run.output_type = _infer_output_type_from_request_text(text)

    conv = db.get(conversation_models.ConversationModel, run.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Linked conversation not found")
    _ensure_team_run_sessions(team_svc, run)

    selected = _normalize_web_selected_agents(
        selected=list(run.selected_agents or conv.selected_agents or []),
        valid_handles={a["handle"] for a in orchestrator.list_agents_info()},
        mode=run.mode,
    )
    run.selected_agents = selected
    conv.selected_agents = selected
    conv.updated_at = now_utc()
    source_files, source_ir_summary = _collect_source_files(
        db,
        raw_source_file_ids or list(run.source_file_ids or []),
    )
    if source_files or raw_source_file_ids:
        run.source_file_ids = [str(item.id) for item in source_files]
        run.source_ir_summary = source_ir_summary
    planning_request = text
    if source_ir_summary:
        planning_request = (
            f"{text}\n\n"
            "참고 문서 요약:\n"
            f"{source_ir_summary}"
        ).strip()

    user_participant = conv_svc.get_or_create_participant(
        conversation_id=conv.id,
        handle=sender_name,
        type="user",
        display_name=sender_name,
    )
    conv_svc.create_message(
        conversation_id=conv.id,
        raw_text=text,
        message_type="user",
        participant_id=user_participant.id,
        is_agent_message=False,
    )

    existing_tasks = team_svc.list_tasks(run.id)
    if existing_tasks:
        followup = team_svc.create_task(
            team_run_id=run.id,
            title="후속 요청 반영",
            description=text,
            owner_handle="planner",
            artifact_goal="decision",
            created_by_handle=sender_name,
            review_required=False,
            priority=95,
            task_kind="plan",
        )
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=followup.id,
            event_type="task_created",
            actor_handle=sender_name,
            target_handle="planner",
            summary="사용자가 후속 요청을 남겼고 PM이 반영 작업을 추가했습니다.",
        )
        _create_team_inbox_message(
            team_svc,
            run,
            from_handle=None,
            to_handle="planner",
            related_task_id=followup.id,
            message_type="task_assignment",
            subject="후속 요청 반영",
            content=(
                f"사용자 후속 요청: {text}\n\n참고 문서 요약:\n{source_ir_summary}"
                if source_ir_summary
                else f"사용자 후속 요청: {text}"
            ),
        )
        team_svc.update_run(run.id, status="active", plan_status="approved")
        db.commit()
        await _run_team_scheduler(db, run.id)
        db.refresh(run)
        return _build_team_board_snapshot(db, run)

    team_svc.update_run(
        run.id,
        request_text=text,
        output_type=run.output_type,
        document_provider=default_document_provider(),
        status="planning",
        plan_status="pending",
    )
    brief, task_defs = await _decompose_team_request(planning_request, selected, output_type=run.output_type)

    planner_task = team_svc.create_task(
        team_run_id=run.id,
        title="요청 정리 및 작업 구조화",
        description="PM이 요청을 해석하고 팀 작업 구조를 확정합니다.",
        owner_handle="planner",
        artifact_goal="brief",
        created_by_handle="planner",
        review_required=False,
        status="done",
        claim_status="done",
        priority=10,
        task_kind="plan",
    )
    brief_artifact = conv_svc.create_or_replace_artifact(
        conversation_id=conv.id,
        task_id=planner_task.id,
        artifact_type="brief",
        content=brief,
        created_by_handle="planner",
    )
    team_svc.create_activity(
        team_run_id=run.id,
        event_type="task_completed",
        actor_handle="planner",
        task_id=planner_task.id,
        summary="PM이 요청을 정리하고 작업 보드를 생성했습니다.",
        payload={"artifact_id": str(brief_artifact.id)},
    )

    created_tasks: dict[str, conversation_models.TeamTaskModel] = {planner_task.title: planner_task}
    for task_def in task_defs:
        if task_def["owner_handle"] == "planner":
            continue
        task = team_svc.create_task(
            team_run_id=run.id,
            title=task_def["title"],
            description=task_def["description"],
            owner_handle=task_def["owner_handle"],
            artifact_goal=task_def["artifact_goal"],
            created_by_handle="planner",
            review_required=bool(task_def.get("review_required", False)),
            status=task_def.get("status", "todo"),
            claim_status="open" if task_def.get("status", "todo") == "todo" else "done",
            priority=int(task_def.get("priority", 50)),
            task_kind=_infer_task_kind(task_def["artifact_goal"]),
        )
        created_tasks[task.title] = task
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=task.id,
            event_type="task_created",
            actor_handle="planner",
            target_handle=task.owner_handle,
            summary=f"PM이 {task.owner_handle}에게 '{task.title}' 작업을 배정했습니다.",
        )
        _create_team_inbox_message(
            team_svc,
            run,
            from_handle="planner",
            to_handle=task.owner_handle,
            related_task_id=task.id,
            message_type="task_assignment",
            subject=task.title,
            content=f"새 작업이 배정되었습니다.\n- 설명: {task.description}\n- 목표 산출물: {task.artifact_goal}",
        )

    for task_def in task_defs:
        if task_def["owner_handle"] == "planner":
            continue
        current_task = created_tasks.get(task_def["title"])
        if not current_task:
            continue
        for dep_title in task_def.get("depends_on_titles") or []:
            dep_task = created_tasks.get(dep_title)
            if dep_task:
                team_svc.add_dependency(team_task_id=current_task.id, depends_on_task_id=dep_task.id)

    if run.oversight_mode == "manual":
        team_svc.update_run(run.id, status="awaiting_plan_approval", plan_status="awaiting_approval")
        team_svc.create_activity(
            team_run_id=run.id,
            event_type="plan_submitted",
            actor_handle="planner",
            summary="PM이 실행 계획을 제출했고 승인 대기 상태로 전환했습니다.",
            payload={"artifact_id": str(brief_artifact.id)},
        )
        _create_team_inbox_message(
            team_svc,
            run,
            from_handle="planner",
            to_handle="manager",
            message_type="plan_approval",
            subject="실행 계획 승인 요청",
            content="PM이 실행 계획을 제출했습니다. 승인 또는 반려를 결정해 주세요.",
        )
        db.commit()
        db.refresh(run)
        return _build_team_board_snapshot(db, run)

    team_svc.update_run(run.id, status="active", plan_status="approved")
    team_svc.create_activity(
        team_run_id=run.id,
        event_type="plan_approved",
        actor_handle="manager",
        summary="자동 모드에서 실행 계획이 승인되어 팀 실행을 시작합니다.",
        payload={"artifact_id": str(brief_artifact.id)},
    )
    db.commit()
    await _run_team_scheduler(db, run.id)
    db.refresh(run)
    return _build_team_board_snapshot(db, run)


@router.post("/web/team-runs/{team_run_id}/plan/approve", status_code=200)
async def approve_web_team_run_plan(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    run = team_svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    if run.plan_status not in {"awaiting_approval", "pending"}:
        raise HTTPException(status_code=400, detail="plan is not awaiting approval")
    actor_handle = str(payload.get("actor_handle") or "manager").strip().lower() or "manager"
    team_svc.update_run(run.id, plan_status="approved", status="active")
    team_svc.create_activity(
        team_run_id=run.id,
        event_type="plan_approved",
        actor_handle=actor_handle,
        summary=f"{actor_handle}가 실행 계획을 승인했습니다.",
    )
    _create_team_inbox_message(
        team_svc,
        run,
        from_handle=actor_handle,
        to_handle="planner",
        message_type="plan_feedback",
        subject="실행 계획 승인",
        content="실행 계획이 승인되었습니다. 후속 작업을 진행하세요.",
    )
    db.commit()
    await _run_team_scheduler(db, run.id)
    refreshed = team_svc.get_run(run.id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, refreshed)


@router.post("/web/team-runs/{team_run_id}/plan/reject", status_code=200)
def reject_web_team_run_plan(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    run = team_svc.get_run(team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")
    if run.plan_status not in {"awaiting_approval", "pending"}:
        raise HTTPException(status_code=400, detail="plan is not awaiting approval")
    actor_handle = str(payload.get("actor_handle") or "manager").strip().lower() or "manager"
    reason = str(payload.get("reason") or "").strip()
    planner_session = team_svc.find_session_by_handle(run.id, "planner")
    team_svc.update_run(run.id, plan_status="rejected", status="blocked")
    team_svc.create_activity(
        team_run_id=run.id,
        event_type="plan_rejected",
        actor_handle=actor_handle,
        target_handle="planner",
        summary=f"{actor_handle}가 실행 계획을 반려했습니다.",
        payload={"reason": reason} if reason else None,
    )
    if planner_session:
        team_svc.create_inbox_message(
            team_run_id=run.id,
            from_session_id=None,
            to_session_id=planner_session.id,
            message_type="plan_feedback",
            subject="실행 계획 반려",
            content=reason or "계획을 다시 조정해 주세요.",
        )
    db.commit()
    refreshed = team_svc.get_run(run.id)
    if not refreshed:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, refreshed)


@router.patch("/web/tasks/{task_id}", status_code=200)
async def update_web_team_task(
    task_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    task = team_svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    run = team_svc.get_run(task.team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")

    actor_handle = str(payload.get("actor_handle") or "planner").strip().lower() or "planner"
    selected_handles = set(run.selected_agents or [])
    owner_changed = False
    previous_owner = task.owner_handle
    previous_status = task.status
    material_change = False
    fields = {}
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title must not be empty")
        fields["title"] = title
        material_change = material_change or title != task.title
    if "description" in payload:
        description = str(payload.get("description") or "").strip()
        if not description:
            raise HTTPException(status_code=400, detail="description must not be empty")
        fields["description"] = description
        material_change = material_change or description != task.description
    if "artifact_goal" in payload:
        artifact_goal = str(payload.get("artifact_goal") or task.artifact_goal).strip().lower()
        _validate_artifact_goal(artifact_goal)
        fields["artifact_goal"] = artifact_goal
        material_change = material_change or artifact_goal != task.artifact_goal
    if "priority" in payload:
        fields["priority"] = int(payload.get("priority") or task.priority)
    if "review_required" in payload:
        review_required = bool(payload.get("review_required"))
        fields["review_required"] = review_required
        material_change = material_change or review_required != task.review_required
    if "status" in payload:
        fields["status"] = str(payload.get("status") or task.status).strip().lower()
    if "owner_handle" in payload:
        next_owner = str(payload.get("owner_handle") or task.owner_handle).strip().lower()
        if next_owner and selected_handles and next_owner not in selected_handles:
            raise HTTPException(status_code=400, detail="owner_handle must be in selected_agents")
        fields["owner_handle"] = next_owner
        owner_changed = next_owner != previous_owner
    action = str(payload.get("action") or "").strip().lower()

    updated = team_svc.update_task(task_id, **fields) if fields else task
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")

    if owner_changed and updated.claim_status == "claimed":
        team_svc.release_task_claim(updated.id, reset_status="open")
        updated = team_svc.update_task(updated.id, status="todo") or updated

    if owner_changed:
        team_svc.create_activity(
            team_run_id=updated.team_run_id,
            task_id=updated.id,
            event_type="task_assigned",
            actor_handle=actor_handle,
            target_handle=updated.owner_handle,
            summary=f"{actor_handle}가 '{updated.title}' 작업 담당자를 {previous_owner}에서 {updated.owner_handle}로 변경했습니다.",
        )
        _create_team_inbox_message(
            team_svc,
            run,
            from_handle=actor_handle,
            to_handle=updated.owner_handle,
            related_task_id=updated.id,
            message_type="task_assignment",
            subject="작업 재배정",
            content=f"'{updated.title}' 작업이 {updated.owner_handle}에게 재배정되었습니다.",
        )

    if "status" in fields and updated.claim_status == "claimed":
        reset_claim = {
            "todo": "open",
            "blocked": "blocked",
            "done": "done",
            "canceled": "open",
        }.get(updated.status)
        if reset_claim:
            team_svc.release_task_claim(updated.id, reset_status=reset_claim)

    if action in {"rerun", "unblock"}:
        reopened = _reset_team_task_branch(
            team_svc=team_svc,
            run_id=updated.team_run_id,
            task_id=updated.id,
            include_descendants=action == "rerun",
        )
        if action == "rerun":
            _supersede_reopened_task_artifacts(db, run=run, tasks=reopened)
        summary = (
            f"{actor_handle}가 '{updated.title}' 작업을 다시 실행하도록 요청했습니다."
            if action == "rerun"
            else f"{actor_handle}가 '{updated.title}' 작업의 차단을 해제했습니다."
        )
        if len(reopened) > 1:
            summary += f" 후속 작업 {len(reopened) - 1}개도 함께 다시 열었습니다."
        team_svc.create_activity(
            team_run_id=updated.team_run_id,
            task_id=updated.id,
            event_type="task_reopened" if action == "rerun" else "task_unblocked",
            actor_handle=actor_handle,
            target_handle=updated.owner_handle,
            summary=summary,
            payload={"reopened_task_ids": [str(item.id) for item in reopened]},
        )
        team_svc.update_run(updated.team_run_id, status="active")
        db.commit()
        await _run_team_scheduler(db, updated.team_run_id)
        refreshed_run = team_svc.get_run(updated.team_run_id)
        if not refreshed_run:
            raise HTTPException(status_code=500, detail="Failed to refresh team run")
        return _build_team_board_snapshot(db, refreshed_run)

    if action in {"approve_review", "reject_review"}:
        if not _task_has_review_notes(db, run, updated.id):
            raise HTTPException(status_code=400, detail="Task does not have review notes")

        if action == "approve_review":
            team_svc.create_activity(
                team_run_id=updated.team_run_id,
                task_id=updated.id,
                event_type="review_approved",
                actor_handle=actor_handle,
                target_handle=updated.owner_handle,
                summary=f"{actor_handle}가 '{updated.title}' 검토를 승인했습니다.",
            )
            final_task = _find_final_task(team_svc, run.id)
            if final_task:
                _create_team_inbox_message(
                    team_svc,
                    run,
                    from_handle=actor_handle,
                    to_handle=final_task.owner_handle,
                    related_task_id=final_task.id,
                    message_type="review_feedback",
                    subject="검토 승인",
                    content=f"'{updated.title}' 검토가 승인되었습니다. 최종 산출물을 정리하세요.",
                )
            team_svc.update_run(updated.team_run_id, status="active")
            db.commit()
            await _run_team_scheduler(db, updated.team_run_id)
            refreshed_run = team_svc.get_run(updated.team_run_id)
            if not refreshed_run:
                raise HTTPException(status_code=500, detail="Failed to refresh team run")
            return _build_team_board_snapshot(db, refreshed_run)

        reopened = _reopen_review_branch(
            team_svc=team_svc,
            run_id=run.id,
            review_task_id=updated.id,
        )
        _supersede_reopened_task_artifacts(db, run=run, tasks=reopened)
        team_svc.create_activity(
            team_run_id=updated.team_run_id,
            task_id=updated.id,
            event_type="review_rejected",
            actor_handle=actor_handle,
            target_handle=updated.owner_handle,
            summary=(
                f"{actor_handle}가 '{updated.title}' 검토를 반려했고 "
                f"재작업 대상 {len(reopened)}개를 다시 열었습니다."
            ),
            payload={"reopened_task_ids": [str(item.id) for item in reopened]},
        )
        for reopened_task in reopened:
            if reopened_task.id == updated.id:
                continue
            _create_team_inbox_message(
                team_svc,
                run,
                from_handle=actor_handle,
                to_handle=reopened_task.owner_handle,
                related_task_id=reopened_task.id,
                message_type="review_feedback",
                subject="검토 반려로 재작업 필요",
                content=f"'{updated.title}' 검토가 반려되었습니다. '{reopened_task.title}' 작업을 보강해 주세요.",
            )
        team_svc.update_run(updated.team_run_id, status="active")
        db.commit()
        await _run_team_scheduler(db, updated.team_run_id)
        refreshed_run = team_svc.get_run(updated.team_run_id)
        if not refreshed_run:
            raise HTTPException(status_code=500, detail="Failed to refresh team run")
        return _build_team_board_snapshot(db, refreshed_run)

    if material_change and previous_status == "done":
        reopened = _reset_team_task_branch(
            team_svc=team_svc,
            run_id=updated.team_run_id,
            task_id=updated.id,
            include_descendants=True,
        )
        _supersede_reopened_task_artifacts(db, run=run, tasks=reopened)
        team_svc.create_activity(
            team_run_id=updated.team_run_id,
            task_id=updated.id,
            event_type="task_updated",
            actor_handle=actor_handle,
            target_handle=updated.owner_handle,
            summary=f"{actor_handle}가 '{updated.title}' 작업 정의를 수정했습니다.",
        )
        team_svc.create_activity(
            team_run_id=updated.team_run_id,
            task_id=updated.id,
            event_type="task_reopened",
            actor_handle=actor_handle,
            target_handle=updated.owner_handle,
            summary=f"{actor_handle}가 '{updated.title}' 작업을 다시 열고 후속 작업 {max(len(reopened) - 1, 0)}개를 함께 재개했습니다.",
            payload={"reopened_task_ids": [str(item.id) for item in reopened]},
        )
        if _task_is_ready(team_svc, run.id, updated.id, db):
            team_svc.update_run(updated.team_run_id, status="active")
            db.commit()
            await _run_team_scheduler(db, updated.team_run_id)
        else:
            _refresh_team_run_status(db, team_svc, updated.team_run_id)
            db.commit()
        refreshed_run = team_svc.get_run(updated.team_run_id)
        if not refreshed_run:
            raise HTTPException(status_code=500, detail="Failed to refresh team run")
        return _build_team_board_snapshot(db, refreshed_run)

    event_type = {
        "in_progress": "task_started",
        "blocked": "task_blocked",
        "review": "review_requested",
        "done": "task_completed",
        "canceled": "task_canceled",
    }.get(updated.status, "task_updated")
    summary = {
        "in_progress": f"{updated.owner_handle}가 '{updated.title}' 작업을 시작했습니다.",
        "blocked": f"{updated.owner_handle}가 '{updated.title}' 작업을 차단 상태로 전환했습니다.",
        "review": f"{updated.owner_handle}가 '{updated.title}' 작업의 검토를 요청했습니다.",
        "done": f"{updated.owner_handle}가 '{updated.title}' 작업을 완료했습니다.",
        "canceled": f"{updated.owner_handle}가 '{updated.title}' 작업을 취소했습니다.",
    }.get(updated.status, f"'{updated.title}' 작업 상태가 변경되었습니다.")
    team_svc.create_activity(
        team_run_id=updated.team_run_id,
        task_id=updated.id,
        event_type=event_type,
        actor_handle=updated.owner_handle,
        summary=summary,
    )

    _refresh_team_run_status(db, team_svc, updated.team_run_id)

    db.commit()
    refreshed_run = team_svc.get_run(updated.team_run_id)
    if not refreshed_run:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, refreshed_run)


@router.put("/web/tasks/{task_id}/dependencies", status_code=200)
async def update_web_team_task_dependencies(
    task_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    task = team_svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    run = team_svc.get_run(task.team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")

    actor_handle = str(payload.get("actor_handle") or "planner").strip().lower() or "planner"
    dependency_ids = _coerce_uuid_list(payload.get("depends_on_task_ids"))
    _validate_dependency_ids(team_svc, run.id, task.id, dependency_ids)
    if _creates_dependency_cycle(team_svc, run.id, task.id, dependency_ids):
        raise HTTPException(status_code=400, detail="dependency cycle detected")

    previous_status = task.status
    team_svc.replace_dependencies(team_task_id=task.id, depends_on_task_ids=dependency_ids)
    team_svc.create_activity(
        team_run_id=run.id,
        task_id=task.id,
        event_type="task_dependency_updated",
        actor_handle=actor_handle,
        target_handle=task.owner_handle,
        summary=f"{actor_handle}가 '{task.title}' 선행 작업 구성을 변경했습니다.",
        payload={"depends_on_task_ids": [str(item) for item in dependency_ids]},
    )

    if previous_status == "done":
        reopened = _reset_team_task_branch(
            team_svc=team_svc,
            run_id=run.id,
            task_id=task.id,
            include_descendants=True,
        )
        _supersede_reopened_task_artifacts(db, run=run, tasks=reopened)
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=task.id,
            event_type="task_reopened",
            actor_handle=actor_handle,
            target_handle=task.owner_handle,
            summary=f"{actor_handle}가 '{task.title}' 작업을 다시 열고 후속 작업 {max(len(reopened) - 1, 0)}개를 함께 재개했습니다.",
            payload={"reopened_task_ids": [str(item.id) for item in reopened]},
        )

    if _task_is_ready(team_svc, run.id, task.id, db):
        team_svc.update_run(run.id, status="active")
        db.commit()
        await _run_team_scheduler(db, run.id)
    else:
        _refresh_team_run_status(db, team_svc, run.id)
        db.commit()

    refreshed_run = team_svc.get_run(run.id)
    if not refreshed_run:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, refreshed_run)


def _artifact_payload(artifact: conversation_models.ConversationArtifactModel) -> dict:
    return {
        "artifact_type": artifact.artifact_type,
        "version": artifact.version,
        "status": artifact.status,
        "created_by_handle": artifact.created_by_handle,
        "created_at": artifact.created_at.isoformat(),
        "content": artifact.content,
    }


def _latest_active_task_artifact(
    db: Session,
    *,
    conversation_id: UUID,
    task_ids: list[uuid.UUID],
    artifact_type: str,
) -> conversation_models.ConversationArtifactModel | None:
    if not task_ids:
        return None
    return (
        db.query(conversation_models.ConversationArtifactModel)
        .filter(
            conversation_models.ConversationArtifactModel.conversation_id == conversation_id,
            conversation_models.ConversationArtifactModel.task_id.in_(task_ids),
            conversation_models.ConversationArtifactModel.artifact_type == artifact_type,
            conversation_models.ConversationArtifactModel.status.in_(("active", "final")),
        )
        .order_by(
            conversation_models.ConversationArtifactModel.version.desc(),
            conversation_models.ConversationArtifactModel.created_at.desc(),
        )
        .first()
    )


def _extract_web_deliverable(
    db: Session,
    conversation_id: UUID,
    run: conversation_models.TeamRunModel | None = None,
) -> dict | None:
    svc = ConversationService(db)
    workflow_preset = _run_workflow_preset(run) if run else None

    def to_payload(artifact: conversation_models.ConversationArtifactModel) -> dict:
        payload = _artifact_payload(artifact)
        if workflow_preset == "presentation_team" and payload.get("content"):
            payload["raw_content"] = payload["content"]
            payload["content"] = _presentation_user_visible_markdown(
                run.title or "최종 발표자료",
                str(payload["content"] or "").strip(),
            )
        if payload.get("content"):
            payload["structured_ir"] = _build_deliverable_ir(
                title=run.title if run else "Deliverable",
                content=str(payload["content"] or "").strip(),
                run=run,
            )
        return payload

    if run:
        team_svc = TeamRunService(db)
        tasks = team_svc.list_tasks(run.id)
        final_task_ids = [task.id for task in tasks if task.artifact_goal == "final" and task.status == "done"]
        if final_task_ids:
            artifact = _latest_active_task_artifact(
                db,
                conversation_id=conversation_id,
                task_ids=final_task_ids,
                artifact_type="final",
            )
            if artifact and artifact.content.strip():
                return to_payload(artifact)
        decision_task_ids = [task.id for task in tasks if task.artifact_goal == "decision" and task.status == "done"]
        if decision_task_ids:
            artifact = _latest_active_task_artifact(
                db,
                conversation_id=conversation_id,
                task_ids=decision_task_ids,
                artifact_type="decision",
            )
            if artifact and artifact.content.strip():
                return to_payload(artifact)
        brief_task_ids = [task.id for task in tasks if task.artifact_goal == "brief" and task.status == "done"]
        if brief_task_ids and run.plan_status != "approved":
            artifact = _latest_active_task_artifact(
                db,
                conversation_id=conversation_id,
                task_ids=brief_task_ids,
                artifact_type="brief",
            )
            if artifact and artifact.content.strip():
                return to_payload(artifact)
        for owner_group in (("writer",), ("planner", "manager", "critic", "reviewer")):
            draft_task_ids = [
                task.id
                for task in tasks
                if task.artifact_goal == "draft"
                and task.status == "done"
                and task.owner_handle in owner_group
            ]
            if not draft_task_ids:
                continue
            artifact = _latest_active_task_artifact(
                db,
                conversation_id=conversation_id,
                task_ids=draft_task_ids,
                artifact_type="draft",
            )
            if artifact and artifact.content.strip():
                return to_payload(artifact)

    for artifact_type in ("final", "decision", "brief", "draft"):
        artifact = svc.get_latest_artifact(conversation_id, artifact_type)
        if artifact and artifact.content.strip():
            return to_payload(artifact)

    rows = (
        db.query(conversation_models.MessageModel)
        .filter(
            conversation_models.MessageModel.conversation_id == conversation_id,
            conversation_models.MessageModel.message_type == "agent",
        )
        .order_by(conversation_models.MessageModel.created_at.desc())
        .limit(120)
        .all()
    )
    if not rows:
        return None

    def body_of(msg) -> str:
        return (msg.visible_message or msg.raw_text or "").strip()

    priorities = ("manager", "writer", "planner", "critic", "reviewer")
    for role in priorities:
        for msg in rows:
            if (msg.speaker_role or "") != role:
                continue
            body = body_of(msg)
            if len(body) >= 80:
                return {
                    "speaker_role": msg.speaker_role,
                    "speaker_identity": msg.speaker_identity,
                    "speaker_bot_username": msg.speaker_bot_username,
                    "created_at": msg.created_at.isoformat(),
                    "content": body,
                }

    for msg in rows:
        body = body_of(msg)
        if body:
            return {
                "speaker_role": msg.speaker_role,
                "speaker_identity": msg.speaker_identity,
                "speaker_bot_username": msg.speaker_bot_username,
                "created_at": msg.created_at.isoformat(),
                "content": body,
            }
    return None


def _extract_sources_from_text(body_text: str) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for raw in body_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if line.startswith("- "):
            candidate = line[2:].strip()
        elif line.startswith("* "):
            candidate = line[2:].strip()
        else:
            candidate = line
        if "http://" in lowered or "https://" in lowered or lowered.startswith("출처") or lowered.startswith("sources"):
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                sources.append(candidate)
    return sources[:10]


def _build_ppt_slides_from_text(title: str, body_text: str) -> list[dict]:
    sections: list[dict] = []
    current_title = "핵심 내용"
    current_bullets: list[str] = []
    skip_section = False
    meta_headings = (
        "자동 검토 종료 상태",
        "현재 판단",
        "남은 리스크",
        "최신 초안",
        "최신 검토 메모",
        "검토 상태",
    )

    def flush() -> None:
        nonlocal current_title, current_bullets
        bullets = [line.strip() for line in current_bullets if line.strip()]
        if bullets:
            sections.append({"title": current_title[:80], "bullets": bullets[:6]})
        current_bullets = []

    for raw in body_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            flush()
            heading = line[3:].strip() or "핵심 내용"
            lowered = heading.lower()
            skip_section = lowered.startswith(("요청", "참고 출처", "sources", "검토", "작성 메모")) or heading.startswith(meta_headings)
            current_title = heading
            continue
        if line.startswith("### "):
            heading = line[4:].strip() or "세부 내용"
            lowered = heading.lower()
            if lowered.startswith(("참고 출처", "sources", "검토", "작성 메모")) or heading.startswith(meta_headings):
                skip_section = True
                continue
            flush()
            current_title = heading
            skip_section = False
            continue
        if skip_section:
            continue
        if line.startswith("발표자용 검토 반영 요약:"):
            continue
        if line.startswith("- ") or line.startswith("* "):
            current_bullets.append(line[2:].strip())
            continue
        current_bullets.append(line)
    flush()

    if not sections:
        paragraphs = [line.strip() for line in body_text.splitlines() if line.strip()]
        grouped = [paragraphs[i:i + 4] for i in range(0, len(paragraphs), 4)]
        for idx, chunk in enumerate(grouped[:5], start=1):
            sections.append({"title": f"핵심 내용 {idx}", "bullets": chunk[:6]})

    if len(sections) > 6:
        sections = sections[:6]
    return sections or [{"title": "핵심 내용", "bullets": [body_text[:180] or "내용 없음"]}]


def _build_structured_deliverable(title: str, body_text: str) -> dict:
    slides = _build_ppt_slides_from_text(title, body_text)

    def is_meta_bullet(raw_line: str) -> bool:
        lowered = str(raw_line or "").strip().lower()
        return lowered.startswith(
            (
                "발표 포인트:",
                "발표자 메모:",
                "speaker notes:",
                "출처:",
                "참고 출처",
                "sources:",
                "검증 메모:",
                "검토 메모:",
                "작성 메모:",
                "현재 판단:",
                "남은 리스크:",
                "상태:",
                "자동 반려 횟수:",
            )
        )

    def normalize_slide_title(raw_title: str) -> str:
        text = str(raw_title or "핵심 내용").strip() or "핵심 내용"
        match = re.match(r"^슬라이드\s*\d+\.\s*(.+)$", text)
        if match:
            return match.group(1).strip() or text
        return text

    slide_outline = [
        {
            "title": normalize_slide_title(item.get("title", "핵심 내용")),
            "bullets": [
                str(line).strip()
                for line in item.get("bullets", [])
                if str(line).strip()
                and not is_meta_bullet(str(line))
            ],
            "speaker_notes": " ".join(
                [
                    str(line).split(":", 1)[1].strip()
                    for line in item.get("bullets", [])
                    if str(line).strip().lower().startswith(("발표 포인트:", "발표자 메모:", "speaker notes:"))
                ][:2]
            )
            or " ".join(
                [
                    str(line).strip()
                    for line in item.get("bullets", [])
                    if str(line).strip()
                    and not is_meta_bullet(str(line))
                ][:2]
            ),
        }
        for item in slides
    ]
    return {
        "title": title,
        "audience": "일반 청중",
        "slide_outline": slide_outline,
        "speaker_notes": [item["speaker_notes"] for item in slide_outline if item["speaker_notes"]],
        "sources": _extract_sources_from_text(body_text),
    }


def _structured_deliverable_to_markdown(structured: dict, fallback_text: str) -> str:
    title = str(structured.get("title") or "Deliverable").strip()
    slide_outline = structured.get("slide_outline") or []
    lines = [f"# {title}", ""]
    if slide_outline:
        lines.extend(["## Slide Outline", ""])
        for idx, slide in enumerate(slide_outline, start=1):
            lines.append(f"## 슬라이드 {idx}. {str(slide.get('title') or '핵심 내용').strip()}")
            for bullet in slide.get("bullets") or []:
                if str(bullet).strip():
                    lines.append(f"- {str(bullet).strip()}")
            note = str(slide.get("speaker_notes") or "").strip()
            if note:
                lines.append(f"- 발표 포인트: {note}")
            lines.append("")
    else:
        lines.extend([fallback_text.strip(), ""])
    sources = [str(item).strip() for item in structured.get("sources") or [] if str(item).strip()]
    if sources:
        lines.extend(["## 참고 출처", ""])
        lines.extend([f"- {source}" for source in sources])
        lines.append("")
    return "\n".join(lines).strip() or fallback_text.strip()


def _normalize_presentation_final_content(
    *,
    run: conversation_models.TeamRunModel,
    content: str,
    review_bodies: list[str],
    appendix_sections: list[tuple[str, list[str]]] | None = None,
) -> str:
    structured = _build_structured_deliverable(run.title or "최종 발표자료", content)
    markdown = _structured_deliverable_to_markdown(structured, content)
    title = run.title or "최종 발표자료"
    markdown_lines = markdown.splitlines()
    if markdown_lines and markdown_lines[0].strip() == f"# {title}":
        markdown_lines = markdown_lines[1:]
        while markdown_lines and not markdown_lines[0].strip():
            markdown_lines = markdown_lines[1:]
    markdown_body = "\n".join(markdown_lines).strip()
    review_lines = []
    if review_bodies:
        for raw in "\n".join(review_bodies[-1:]).splitlines():
            line = raw.strip(" -")
            if line:
                review_lines.append(line)
            if len(review_lines) >= 3:
                break
    blocks = [(section_title, [str(line).strip() for line in lines if str(line).strip()]) for section_title, lines in (appendix_sections or [])]
    if review_lines:
        blocks.append(("검토 메모", review_lines))

    output_lines = [
        f"# {title}",
        "",
        "## 요청",
        str(run.request_text or "").strip() or "- 요청 없음",
        "",
        markdown_body,
        "",
    ]
    for section_title, lines in blocks:
        if not lines:
            continue
        output_lines.extend([f"## {section_title}", ""])
        output_lines.extend([f"- {line}" for line in lines])
        output_lines.append("")
    return "\n".join(output_lines).strip()


def _select_presentation_primary_body(draft_bodies: list[str]) -> str:
    candidates = [str(body).strip() for body in draft_bodies if str(body).strip()]
    if not candidates:
        return ""

    def score(text: str, index: int) -> tuple[int, int, int]:
        lowered = text.lower()
        value = 0
        if "## 슬라이드" in text or "슬라이드 1" in text:
            value += 5
        if "발표 포인트" in text or "발표자 메모" in text:
            value += 5
        if "## slide outline" in lowered:
            value += 3
        if "검증 메모" in text:
            value -= 6
        if "## 좋은 점" in text or "## 문제점" in text:
            value -= 6
        if "자동 검토 종료 상태" in text:
            value -= 8
        return value, len(text), index

    indexed = list(enumerate(candidates))
    best_index, best_body = max(indexed, key=lambda item: score(item[1], item[0]))
    return best_body


def _presentation_user_visible_markdown(title: str, content: str) -> str:
    structured = _build_structured_deliverable(title, content)
    return _structured_deliverable_to_markdown(structured, content)


@router.post("/web/team-runs/{team_run_id}/exports", status_code=200)
def export_web_team_run_deliverable(
    team_run_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    team_svc = TeamRunService(db)
    run = team_svc.get_run(team_run_id)
    if not run or not run.conversation_id:
        raise HTTPException(status_code=404, detail="Team run not found")

    export_format = _normalize_output_type(payload.get("format") or run.output_type)
    if export_format not in {"docx", "pptx", "xlsx"}:
        raise HTTPException(status_code=400, detail="format must be docx, xlsx, or pptx")
    if export_format != run.output_type:
        raise HTTPException(status_code=400, detail=f"format must match run output_type ({run.output_type})")

    deliverable = _extract_web_deliverable(db, run.conversation_id, run=run)
    if not deliverable or not str(deliverable.get("content") or "").strip():
        raise HTTPException(status_code=400, detail="No deliverable available to export")

    title = str(run.title or "DocFlow Team Deliverable").strip()
    content = str(deliverable.get("content") or "").strip()
    filename_base = _slugify_filename(title, default="team-deliverable")
    structured = _build_structured_deliverable(title, content)
    document_ir = deliverable.get("structured_ir") or _build_deliverable_ir(title=title, content=content, run=run)
    extracted_text = extract_text_from_ir(document_ir) or _structured_deliverable_to_markdown(structured, content)
    generated_provider = "internal_fallback"

    if anthropic_skills_available():
        try:
            generated = AnthropicSkillsDocumentGenerator().generate(
                output_type=export_format,
                title=title,
                request_text=run.request_text,
                content=content,
                structured_ir=document_ir,
                source_ir_summary=run.source_ir_summary,
            )
            file_bytes = generated["content"]
            filename = str(generated.get("filename") or f"{filename_base}.{export_format}")
            mime_type = str(generated.get("mime_type") or "")
            generated_provider = "claude_skills"
        except Exception:
            if not settings.anthropic_skills_allow_fallback:
                raise HTTPException(status_code=502, detail="Claude Skills export failed")
            generated_provider = "internal_fallback"
        else:
            run.document_provider = generated_provider
            db.flush()

    if generated_provider == "internal_fallback":
        if export_format == "docx":
            file_bytes = render_ir_to_docx_bytes(document_ir)
            filename = f"{filename_base}.docx"
            mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif export_format == "xlsx":
            file_bytes = render_ir_to_xlsx_bytes(document_ir)
            filename = f"{filename_base}.xlsx"
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            file_bytes = render_ir_to_pptx_bytes(document_ir)
            filename = f"{filename_base}.pptx"
            mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        run.document_provider = generated_provider
        db.flush()

    file_row = _persist_team_export_file(
        db,
        run=run,
        filename=filename,
        content=file_bytes,
        mime_type=mime_type,
        extracted_text=extracted_text,
    )
    db.commit()
    db.refresh(file_row)
    return {
        "file": {
            "id": str(file_row.id),
            "original_name": file_row.original_name,
            "mime_type": file_row.mime_type,
            "size": file_row.size,
            "source_type": file_row.source_type,
            "created_at": file_row.created_at.isoformat(),
        },
        "download_path": f"/api/files/{file_row.id}/download",
        "format": export_format,
        "provider": generated_provider,
    }


def _compact_progress_text(text: str | None, max_chars: int = 90) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return "작업 진행 중"
    if len(compact) > max_chars:
        return compact[: max_chars - 1] + "…"
    return compact


def _looks_like_jsonish_text(text: str | None) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return stripped.startswith("{") or stripped.startswith("[") or stripped.startswith("```json")


def _normalize_text_block(text: str | None) -> str:
    return " ".join(str(text or "").split()).strip()


def _is_status_only_artifact(
    *,
    task: conversation_models.TeamTaskModel,
    content: str | None,
) -> bool:
    compact = _normalize_text_block(content)
    if not compact:
        return True
    lowered = compact.lower()
    if _looks_like_jsonish_text(compact):
        return True
    if compact == task.title:
        return True
    if any(phrase in compact for phrase in TEAM_ARTIFACT_STATUS_PHRASES):
        lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
        if len(lines) <= 12:
            return True
    if "원요청:" in compact and any(phrase in compact for phrase in TEAM_ARTIFACT_STATUS_PHRASES):
        return True
    if lowered in {"draft", "review_notes", "final", "brief", "decision"}:
        return True
    return False


def _best_effort_result_content(
    *,
    task: conversation_models.TeamTaskModel,
    result: object | None,
) -> str:
    if result is None:
        return ""
    candidates = [
        str(getattr(result, "text", "") or "").strip(),
        str(getattr(result, "visible_message", "") or "").strip(),
    ]
    for candidate in candidates:
        if not candidate or _looks_like_jsonish_text(candidate):
            continue
        if not _is_status_only_artifact(task=task, content=candidate):
            return candidate
    return ""


def _task_execution_contract(
    *,
    run: conversation_models.TeamRunModel,
    task: conversation_models.TeamTaskModel,
) -> str:
    preset = _run_workflow_preset(run)
    shared = (
        "중요:\n"
        "- `visible_message`는 진행 상태를 1문장으로만 쓰세요.\n"
        "- 실제 결과물 본문은 반드시 `artifact_update.content`에 넣으세요.\n"
        "- `artifact_update.content`에 '작성 중', '대기 중', '준비 중' 같은 상태 문구를 쓰면 실패로 처리됩니다.\n"
        "- 원요청과 직접 관련된 실제 내용만 작성하세요. 메타 설명, TODO, 대기 상태 금지.\n"
    )
    if task.artifact_goal == "draft":
        if preset == "presentation_team":
            return (
                f"{shared}"
                "이번 작업은 발표자료용 실제 슬라이드 초안 작성입니다.\n"
                "- 청중은 정책 담당자이며, 발표 시간은 약 5분입니다.\n"
                "- `## 슬라이드 1`, `## 슬라이드 2` 형식으로 정확히 5~6개 슬라이드를 구성하세요.\n"
                "- 각 슬라이드마다 2~4개의 핵심 bullet과 `발표 포인트:` 1줄을 포함하세요.\n"
                "- 슬라이드 제목은 짧고 결정적으로 쓰세요. 추상적 문구보다 정책 판단 문장을 우선하세요.\n"
                "- 확인되지 않은 숫자나 기관명은 단정하지 말고 정성 표현 또는 `검증 필요`로 처리하세요.\n"
                "- 마지막에 `## 참고 출처` 섹션을 포함하고, 출처가 없으면 `출처 확인 필요` 항목을 적으세요.\n"
                f"- 반드시 원요청 '{run.request_text}'에 직접 답하는 발표 내용만 작성하세요.\n"
            )
        if preset == "xlsx_analysis_team":
            return (
                f"{shared}"
                "이번 작업은 실제 엑셀 시트 초안 작성입니다.\n"
                "- `## 시트: 이름` 형식으로 시트별 구조를 나누세요.\n"
                "- 각 시트마다 첫 줄은 헤더 후보, 이후 줄은 행 데이터 또는 요약 표 형태로 작성하세요.\n"
                "- 숫자·단위·기간은 열 기준이 모호하지 않게 쓰세요.\n"
                f"- 반드시 원요청 '{run.request_text}'에 직접 답하는 표 구조를 작성하세요.\n"
            )
        if preset == "docx_brief_team":
            return (
                f"{shared}"
                "이번 작업은 실제 문서 초안 작성입니다.\n"
                "- `## 섹션명` 형식으로 3~6개 섹션을 구성하세요.\n"
                "- 핵심 bullet, 짧은 문단, 필요 시 간단한 표를 포함해 재구성 가능한 문서로 쓰세요.\n"
                f"- 반드시 원요청 '{run.request_text}'에 직접 답하는 문서를 작성하세요.\n"
            )
        return (
            f"{shared}"
            "이번 작업은 실제 초안 작성입니다.\n"
            "- 제목/섹션/본문이 있는 실질적인 초안을 작성하세요.\n"
            "- 발표자료라면 슬라이드별 핵심 메시지와 발표 포인트를 포함하세요.\n"
            f"- 반드시 원요청 '{run.request_text}'에 직접 답하는 내용을 작성하세요.\n"
        )
    if task.artifact_goal == "review_notes":
        if preset == "presentation_team":
            return (
                f"{shared}"
                "이번 작업은 발표자료 품질 검토입니다.\n"
                "- 반드시 `## 좋은 점`, `## 문제점`, `## 수정 제안` 3개 섹션으로 작성하세요.\n"
                "- 문제점에는 최소 3개 항목을 적고, 특히 출처/수치 신뢰성, 정책 실행 가능성, 슬라이드 흐름을 점검하세요.\n"
                "- 발표자 질문을 받을 때 취약한 지점을 구체적으로 지적하세요.\n"
                "- 단순히 '좋다/부족하다'가 아니라 어떤 슬라이드를 어떻게 바꿔야 하는지 적으세요.\n"
            )
        return (
            f"{shared}"
            "이번 작업은 실제 검토 메모 작성입니다.\n"
            "- 좋은 점 1개 이상, 문제점 2개 이상, 개선 제안 1개 이상을 포함하세요.\n"
            "- 초안이 부족하면 왜 부족한지 구체적으로 지적하세요.\n"
            "- 단순히 '초안 대기'만 쓰면 실패입니다.\n"
        )
    if task.artifact_goal == "final":
        if preset == "presentation_team":
            return (
                f"{shared}"
                "이번 작업은 실제 최종 발표자료 작성입니다.\n"
                "- 작성 담당 초안과 검토 담당 피드백을 반영해, 사용자에게 바로 전달 가능한 최종 발표자료를 작성하세요.\n"
                "- `## 슬라이드 1` 형식으로 정확히 5~6개 슬라이드를 완성하세요.\n"
                "- 각 슬라이드에 제목, 2~4개 bullet, `발표 포인트:` 1줄을 포함하세요.\n"
                "- critic의 문제점을 반영해 출처 없는 숫자, 근거 없는 단정, 과한 장식 표현을 제거하세요.\n"
                "- 마지막에 `## 참고 출처`를 넣고, 출처가 불완전하면 `출처 확인 필요`를 명시하세요.\n"
                "- raw critique를 그대로 복붙하지 말고, 발표 자료 본문과 발표자 메모만 남기세요.\n"
            )
        if preset == "xlsx_analysis_team":
            return (
                f"{shared}"
                "이번 작업은 실제 최종 Excel 문서 정리입니다.\n"
                "- 최종 결과는 시트 구조가 명확한 워크북 형태로 정리하세요.\n"
                "- `## 시트: 이름` 형식으로 시트별 목적, 헤더, 행 데이터를 정리하세요.\n"
                "- 검토 메모는 본문에 섞지 말고 실제 표 구조만 남기세요.\n"
            )
        if preset == "docx_brief_team":
            return (
                f"{shared}"
                "이번 작업은 실제 최종 Word 문서 정리입니다.\n"
                "- 완결된 제목, 섹션, 본문, 필요 시 표를 포함한 보고용 문서로 마감하세요.\n"
                "- 검토 메모를 복붙하지 말고 최종 본문만 남기세요.\n"
            )
        return (
            f"{shared}"
            "이번 작업은 실제 최종 결과물 작성입니다.\n"
            "- 최종 결론, 핵심 본문, 남은 리스크 또는 다음 액션을 포함하세요.\n"
            "- review_notes를 반영해 사용자에게 바로 전달 가능한 완성본을 작성하세요.\n"
        )
    if task.artifact_goal in {"brief", "decision"}:
        return (
            f"{shared}"
            "이번 작업은 실제 의사결정/브리프 문서 작성입니다.\n"
            "- 요청 해석, 작업 기준, 다음 단계가 명확히 드러나야 합니다.\n"
        )
    return shared


def _required_workflow_agents(mode: str | None) -> list[str]:
    normalized = (mode or "").strip().lower()
    if normalized in {"autonomous", "autonomous-lite", "team-autonomous"}:
        return ["planner", "writer", "critic", "manager"]
    return ["planner"]


def _normalize_web_selected_agents(
    *,
    selected: list[str] | None,
    valid_handles: set[str],
    mode: str | None,
) -> list[str]:
    normalized = [str(handle).strip().lower() for handle in (selected or []) if str(handle).strip()]
    normalized = [handle for handle in normalized if handle in valid_handles]
    for required in _required_workflow_agents(mode):
        if required in valid_handles and required not in normalized:
            normalized.append(required)
    if not normalized:
        return sorted(valid_handles)
    return normalized


def _default_session_specs(selected_agents: list[str]) -> list[dict]:
    info_by_handle = {
        item["handle"]: item
        for item in orchestrator.list_agents_info()
    }
    specs = []
    for handle in selected_agents:
        info = info_by_handle.get(handle, {})
        specs.append(
            {
                "handle": handle,
                "display_name": str(info.get("display_name") or handle),
                "role": "leader" if handle == "planner" else "worker",
            }
        )
    if not any(spec["handle"] == "planner" for spec in specs):
        specs.insert(
            0,
            {
                "handle": "planner",
                "display_name": "planner",
                "role": "leader",
            },
        )
    return specs


def _ensure_team_run_sessions(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
) -> list[conversation_models.TeamMemberSessionModel]:
    existing = team_svc.list_sessions(run.id)
    if existing:
        return existing
    sessions = []
    for spec in _default_session_specs(list(run.selected_agents or [])):
        sessions.append(
            team_svc.create_session(
                team_run_id=run.id,
                handle=spec["handle"],
                role=spec["role"],
                display_name=spec["display_name"],
            )
        )
    return sessions


def _ensure_session_for_handle(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
    handle: str,
) -> conversation_models.TeamMemberSessionModel:
    session = team_svc.find_session_by_handle(run.id, handle)
    if session:
        return session
    info_by_handle = {item["handle"]: item for item in orchestrator.list_agents_info()}
    info = info_by_handle.get(handle, {})
    return team_svc.create_session(
        team_run_id=run.id,
        handle=handle,
        role="leader" if handle == "planner" else "worker",
        display_name=str(info.get("display_name") or handle),
    )


def _create_team_inbox_message(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
    *,
    to_handle: str,
    content: str,
    subject: str = "",
    message_type: str = "direct",
    from_handle: str | None = None,
    related_task_id: uuid.UUID | None = None,
) -> conversation_models.TeamInboxMessageModel | None:
    if not content.strip():
        return None
    from_session = _ensure_session_for_handle(team_svc, run, from_handle) if from_handle else None
    to_session = _ensure_session_for_handle(team_svc, run, to_handle)
    return team_svc.create_inbox_message(
        team_run_id=run.id,
        from_session_id=from_session.id if from_session else None,
        to_session_id=to_session.id if to_session else None,
        related_task_id=related_task_id,
        message_type=message_type,
        subject=subject,
        content=content.strip(),
    )


def _infer_team_workflow_preset(request_text: str | None, output_type: str | None = None) -> str:
    normalized = str(output_type or "").strip().lower()
    if not normalized:
        normalized = _infer_output_type_from_request_text(request_text)
    if normalized in OUTPUT_TYPE_PRESET_MAP:
        return OUTPUT_TYPE_PRESET_MAP[normalized]
    return "docx_brief_team"


def _run_workflow_preset(run: object | None) -> str:
    if not run:
        return "docx_brief_team"
    request_text = getattr(run, "request_text", "") or getattr(run, "title", "")
    output_type = getattr(run, "output_type", None)
    return _infer_team_workflow_preset(request_text, output_type)


def _presentation_team_tasks(selected_agents: list[str]) -> list[dict]:
    tasks = [
        {
            "title": "요청 정리 및 작업 구조화",
            "description": (
                "발표 목적, 청중, 발표 길이, 핵심 메시지, 포함/제외 범위, 수치 표현 주의사항을 정리하고 "
                "슬라이드 개수와 흐름 기준을 확정합니다."
            ),
            "owner_handle": "planner",
            "artifact_goal": "brief",
            "depends_on_titles": [],
            "review_required": False,
            "status": "done",
            "priority": 10,
        },
        {
            "title": "슬라이드 초안 작성",
            "description": (
                "정책 담당자 또는 경영진 보고를 전제로 5장 내외의 슬라이드 초안을 작성합니다. "
                "각 슬라이드는 제목, 2~4개 bullet, 발표자 메모를 포함합니다."
            ),
            "owner_handle": "writer",
            "artifact_goal": "draft",
            "depends_on_titles": ["요청 정리 및 작업 구조화"],
            "review_required": True,
            "status": "todo",
            "priority": 30,
        },
        {
            "title": "슬라이드 흐름 및 메시지 검토",
            "description": (
                "슬라이드 흐름, 메시지 선명도, 근거 표현, 청중 적합성, 실행 가능성을 검토하고 수정 의견을 남깁니다."
            ),
            "owner_handle": "critic",
            "artifact_goal": "review_notes",
            "depends_on_titles": ["슬라이드 초안 작성"],
            "review_required": False,
            "status": "todo",
            "priority": 65,
        },
        {
            "title": "최종 발표자료 정리",
            "description": (
                "검토 의견을 반영해 최종 발표자료 구조를 확정합니다. 최종 산출물은 Claude Skills 기반 PPTX 생성에 바로 사용됩니다."
            ),
            "owner_handle": "manager",
            "artifact_goal": "final",
            "depends_on_titles": ["슬라이드 흐름 및 메시지 검토"],
            "review_required": False,
            "status": "todo",
            "priority": 90,
        },
    ]
    return [task for task in tasks if task["owner_handle"] in set(selected_agents) | {"planner"}]


def _docx_team_tasks(selected_agents: list[str]) -> list[dict]:
    tasks = [
        {
            "title": "요청 정리 및 작업 구조화",
            "description": "사용자 요청을 실행 가능한 작업 단위로 정리하고 기준을 확정합니다.",
            "owner_handle": "planner",
            "artifact_goal": "brief",
            "depends_on_titles": [],
            "review_required": False,
            "status": "done",
            "priority": 10,
        },
        {
            "title": "문서 초안 작성",
            "description": "요청과 첨부 문서를 반영해 보고용 본문 초안을 작성합니다. 제목, 섹션, 핵심 bullet 또는 표 구조를 포함합니다.",
            "owner_handle": "writer",
            "artifact_goal": "draft",
            "depends_on_titles": ["요청 정리 및 작업 구조화"],
            "review_required": True,
            "status": "todo",
            "priority": 30,
        },
        {
            "title": "문서 구조 및 품질 검토",
            "description": "문서 구조, 논리 흐름, 누락, 근거 표현, 재구성 완성도를 검토합니다.",
            "owner_handle": "critic",
            "artifact_goal": "review_notes",
            "depends_on_titles": ["문서 초안 작성"],
            "review_required": False,
            "status": "todo",
            "priority": 60,
        },
        {
            "title": "최종 Word 문서 정리",
            "description": "검토 내용을 반영해 최종 문서 구조를 확정합니다. 최종 산출물은 Claude Skills 기반 DOCX 생성에 바로 사용됩니다.",
            "owner_handle": "manager",
            "artifact_goal": "final",
            "depends_on_titles": ["문서 구조 및 품질 검토"],
            "review_required": False,
            "status": "todo",
            "priority": 90,
        },
    ]
    return [task for task in tasks if task["owner_handle"] in set(selected_agents) | {"planner"}]


def _xlsx_team_tasks(selected_agents: list[str]) -> list[dict]:
    tasks = [
        {
            "title": "요청 정리 및 시트 구조화",
            "description": "필요한 시트, 컬럼, 요약 지표, 계산 구조를 정리합니다.",
            "owner_handle": "planner",
            "artifact_goal": "brief",
            "depends_on_titles": [],
            "review_required": False,
            "status": "done",
            "priority": 10,
        },
        {
            "title": "시트 초안 작성",
            "description": "요청과 첨부 문서를 바탕으로 시트별 표 구조와 데이터 초안을 작성합니다.",
            "owner_handle": "writer",
            "artifact_goal": "draft",
            "depends_on_titles": ["요청 정리 및 시트 구조화"],
            "review_required": True,
            "status": "todo",
            "priority": 30,
        },
        {
            "title": "시트 구조 및 계산 검토",
            "description": "시트 구조, 표 일관성, 요약 지표, 누락 항목을 검토합니다.",
            "owner_handle": "critic",
            "artifact_goal": "review_notes",
            "depends_on_titles": ["시트 초안 작성"],
            "review_required": False,
            "status": "todo",
            "priority": 60,
        },
        {
            "title": "최종 Excel 문서 정리",
            "description": "검토 내용을 반영해 최종 워크북 구조를 확정합니다. 최종 산출물은 Claude Skills 기반 XLSX 생성에 바로 사용됩니다.",
            "owner_handle": "manager",
            "artifact_goal": "final",
            "depends_on_titles": ["시트 구조 및 계산 검토"],
            "review_required": False,
            "status": "todo",
            "priority": 90,
        },
    ]
    return [task for task in tasks if task["owner_handle"] in set(selected_agents) | {"planner"}]


def _default_team_tasks(
    request_text: str,
    selected_agents: list[str],
    *,
    output_type: str = "docx",
) -> list[dict]:
    preset = _infer_team_workflow_preset(request_text, output_type)
    if preset == "presentation_team":
        return _presentation_team_tasks(selected_agents)
    if preset == "xlsx_analysis_team":
        return _xlsx_team_tasks(selected_agents)
    return _docx_team_tasks(selected_agents)


def _normalize_team_tasks(tasks: list[dict], selected_agents: list[str]) -> list[dict]:
    if not tasks:
        return []
    normalized = [dict(task) for task in tasks]
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for task in normalized:
        title = str(task.get("title") or "").strip()
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        task["depends_on_titles"] = [
            str(dep).strip()
            for dep in task.get("depends_on_titles") or []
            if str(dep).strip() and str(dep).strip() != title
        ]
        deduped.append(task)
    normalized = deduped

    planner_title = next(
        (str(task["title"]) for task in normalized if task.get("owner_handle") == "planner"),
        "",
    )
    for task in normalized:
        if task.get("owner_handle") != "planner" and planner_title and not task.get("depends_on_titles"):
            task["depends_on_titles"] = [planner_title]

    review_source_titles = [
        str(task["title"])
        for task in normalized
        if task.get("owner_handle") == "writer"
        and task.get("artifact_goal") in {"draft", "decision"}
    ]
    decision_titles = [
        str(task["title"])
        for task in normalized
        if task.get("owner_handle") == "manager"
        and task.get("artifact_goal") == "decision"
    ]
    critic_titles = [
        str(task["title"])
        for task in normalized
        if task.get("owner_handle") == "critic"
    ]
    for task in normalized:
        if task.get("owner_handle") != "critic":
            continue
        deps = [str(dep).strip() for dep in task.get("depends_on_titles") or [] if str(dep).strip()]
        merged = deps + [title for title in review_source_titles if title not in deps and title != task.get("title")]
        task["artifact_goal"] = "review_notes"
        task["depends_on_titles"] = merged

    final_deps = critic_titles or decision_titles or review_source_titles
    final_task = next(
        (
            task
            for task in normalized
            if task.get("owner_handle") == "manager" and task.get("artifact_goal") == "final"
        ),
        None,
    )
    if final_task:
        deps = [str(dep).strip() for dep in final_task.get("depends_on_titles") or [] if str(dep).strip()]
        merged = deps + [title for title in final_deps if title not in deps and title != final_task.get("title")]
        final_task["depends_on_titles"] = merged
        final_task["review_required"] = False
    elif "manager" in set(selected_agents):
        normalized.append(
            {
                "title": "최종 결과물 정리",
                "description": "모든 작업과 검토 결과를 종합해 사용자에게 전달할 최종 결과물을 작성합니다.",
                "owner_handle": "manager",
                "artifact_goal": "final",
                "depends_on_titles": final_deps,
                "review_required": False,
                "status": "todo",
                "priority": max(int(task.get("priority") or 0) for task in normalized) + 20,
            }
        )
    return normalized


def _coerce_team_tasks(
    raw: object,
    selected_agents: list[str],
) -> list[dict]:
    if not isinstance(raw, list):
        return []
    allowed = set(selected_agents or [])
    allowed.add("planner")
    tasks: list[dict] = []
    seen_titles: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        owner_handle = str(item.get("owner_handle") or "").strip().lower()
        artifact_goal = str(item.get("artifact_goal") or "").strip().lower()
        if not title or not description or owner_handle not in allowed:
            continue
        if title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        depends_on_titles = [
            str(dep).strip()
            for dep in item.get("depends_on_titles") or []
            if str(dep).strip()
        ]
        if owner_handle == "critic":
            artifact_goal = "review_notes"
        elif owner_handle == "manager" and artifact_goal not in {"brief", "draft", "review_notes", "decision", "final"}:
            artifact_goal = "decision"
        elif owner_handle == "planner" and artifact_goal not in {"brief", "draft", "review_notes", "decision", "final"}:
            artifact_goal = "brief"
        elif artifact_goal not in {"brief", "draft", "review_notes", "decision", "final"}:
            artifact_goal = "draft"
        tasks.append(
            {
                "title": title[:200],
                "description": description[:2000],
                "owner_handle": owner_handle,
                "artifact_goal": artifact_goal or "draft",
                "depends_on_titles": depends_on_titles,
                "review_required": bool(item.get("review_required", False)),
                "status": "done" if owner_handle == "planner" else "todo",
                "priority": int(item.get("priority") or (10 + len(tasks) * 20)),
            }
        )
    return _normalize_team_tasks(tasks, selected_agents)


async def _decompose_team_request(
    request_text: str,
    selected_agents: list[str],
    *,
    output_type: str = "docx",
) -> tuple[str, list[dict]]:
    orchestrator._ensure_loaded()
    planner = orchestrator._agents.get("planner")
    preset = _infer_team_workflow_preset(request_text, output_type)
    if not planner:
        brief = f"요청 요약: {request_text[:500]}"
        return brief, _default_team_tasks(request_text, selected_agents, output_type=output_type)

    prompt = (
        "다음 요청을 실무형 팀 작업으로 분해하세요.\n\n"
        f"요청:\n{request_text}\n\n"
        f"허용 담당자: {', '.join(selected_agents)}\n"
        f"워크플로 프리셋: {preset}\n"
        "작업은 3~6개로 제한하고, title/description/owner_handle/artifact_goal/depends_on_titles/review_required를 채우세요."
    )
    schema = {
        "type": "object",
        "properties": {
            "brief": {"type": "string"},
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "owner_handle": {"type": "string"},
                        "artifact_goal": {"type": "string"},
                        "depends_on_titles": {"type": "array", "items": {"type": "string"}},
                        "review_required": {"type": "boolean"},
                        "priority": {"type": "integer"},
                    },
                },
            },
        },
    }
    brief = ""
    tasks: list[dict] = []
    try:
        structured = await planner._provider.generate_structured(prompt, schema)
        if isinstance(structured, dict):
            brief = str(structured.get("brief") or "").strip()
            tasks = _coerce_team_tasks(structured.get("tasks"), selected_agents)
    except Exception:
        brief = ""
        tasks = []

    if not brief:
        brief = f"요청 요약: {request_text[:500]}"
    if preset == "presentation_team":
        tasks = _presentation_team_tasks(selected_agents)
    elif not tasks:
        tasks = _default_team_tasks(request_text, selected_agents, output_type=output_type)
    return brief, _normalize_team_tasks(tasks, selected_agents)


def _review_snapshot(
    task: conversation_models.TeamTaskModel,
    task_artifacts: list[conversation_models.ConversationArtifactModel],
    task_events: list[conversation_models.TeamActivityEventModel],
) -> dict:
    latest_review_artifact = next(
        (artifact for artifact in reversed(task_artifacts) if artifact.artifact_type == "review_notes"),
        None,
    )
    latest_review_event = next(
        (
            event
            for event in reversed(task_events)
            if event.event_type in {"review_approved", "review_rejected"}
        ),
        None,
    )
    has_review_notes = latest_review_artifact is not None
    review_state = "not_required"
    if latest_review_event and (
        not latest_review_artifact
        or latest_review_event.created_at >= latest_review_artifact.created_at
    ):
        review_state = "approved" if latest_review_event.event_type == "review_approved" else "rejected"
    elif has_review_notes:
        review_state = "reviewed"
    elif task.review_required:
        review_state = "required"
    return {
        "has_review_notes": has_review_notes,
        "review_state": review_state,
        "latest_review_artifact": latest_review_artifact,
        "latest_review_event": latest_review_event,
    }


def _is_review_gate_task(task: conversation_models.TeamTaskModel) -> bool:
    return task.artifact_goal == "review_notes"


def _dependency_review_satisfied(
    *,
    run: conversation_models.TeamRunModel,
    dependency_task: conversation_models.TeamTaskModel,
    task_artifacts: list[conversation_models.ConversationArtifactModel],
    task_events: list[conversation_models.TeamActivityEventModel],
) -> bool:
    if not _is_review_gate_task(dependency_task):
        return True
    snapshot = _review_snapshot(dependency_task, task_artifacts, task_events)
    if run.oversight_mode == "manual":
        return snapshot["review_state"] == "approved"
    return snapshot["review_state"] in {"approved", "rejected"}


def _eligible_ready_tasks(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
    artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]],
    activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]],
) -> list[conversation_models.TeamTaskModel]:
    tasks = team_svc.list_tasks(run.id)
    tasks_by_id = {str(task.id): task for task in tasks}
    ready: list[conversation_models.TeamTaskModel] = []
    for task in team_svc.ready_tasks(run.id):
        dep_ids = [
            str(dep.depends_on_task_id)
            for dep in team_svc.list_dependencies(run.id)
            if dep.team_task_id == task.id
        ]
        if all(
            _dependency_review_satisfied(
                run=run,
                dependency_task=tasks_by_id[dep_id],
                task_artifacts=artifacts_by_task.get(dep_id, []),
                task_events=activity_by_task.get(dep_id, []),
            )
            for dep_id in dep_ids
            if dep_id in tasks_by_id
        ):
            ready.append(task)
    return ready


def _select_idle_session_for_task(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
    task: conversation_models.TeamTaskModel,
) -> conversation_models.TeamMemberSessionModel | None:
    sessions = [
        session
        for session in team_svc.list_sessions_by_handle(run.id, task.owner_handle)
        if session.status == "idle" and not session.current_task_id
    ]
    if sessions:
        return sessions[0]
    return None


def _claim_ready_tasks_for_idle_sessions(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
    ready_tasks: list[conversation_models.TeamTaskModel],
) -> list[tuple[conversation_models.TeamTaskModel, conversation_models.TeamMemberSessionModel]]:
    claimed: list[tuple[conversation_models.TeamTaskModel, conversation_models.TeamMemberSessionModel]] = []
    used_session_ids: set[uuid.UUID] = set()
    for task in ready_tasks:
        session = _select_idle_session_for_task(team_svc, run, task)
        if not session or session.id in used_session_ids:
            continue
        claimed_task = team_svc.claim_task(task_id=task.id, session_id=session.id)
        if not claimed_task:
            continue
        used_session_ids.add(session.id)
        claimed.append((claimed_task, session))
    return claimed


def _session_inbox_context(
    team_svc: TeamRunService,
    run: conversation_models.TeamRunModel,
    session: conversation_models.TeamMemberSessionModel | None,
) -> tuple[str, list[uuid.UUID]]:
    if not session:
        return "", []
    inbox_items = team_svc.list_session_inbox_messages(
        team_run_id=run.id,
        session_id=session.id,
        include_read=False,
        limit=8,
    )
    if not inbox_items:
        inbox_items = team_svc.list_session_inbox_messages(
            team_run_id=run.id,
            session_id=session.id,
            include_read=True,
            limit=5,
        )
    if not inbox_items:
        return "", []
    lines = []
    ids: list[uuid.UUID] = []
    for item in inbox_items:
        ids.append(item.id)
        lines.append(
            f"- [{item.message_type}] {item.subject or 'message'}: {item.content.strip()[:500]}"
        )
    return "세션 inbox:\n" + "\n".join(lines), ids


def _build_team_board_snapshot(db: Session, run: conversation_models.TeamRunModel) -> dict:
    team_svc = TeamRunService(db)
    conv_svc = ConversationService(db)
    sessions = team_svc.list_sessions(run.id)
    session_by_id = {str(session.id): session for session in sessions}
    inbox_messages = team_svc.list_inbox_messages(run.id, limit=120)
    tasks = team_svc.list_tasks(run.id)
    dependencies = team_svc.list_dependencies(run.id)
    deps_by_task: dict[str, list[str]] = {}
    for dep in dependencies:
        deps_by_task.setdefault(str(dep.team_task_id), []).append(str(dep.depends_on_task_id))
    tasks_by_id = {str(task.id): task for task in tasks}
    done_ids = {str(task.id) for task in tasks if task.status == "done"}
    conv = conv_svc.get_conversation(run.conversation_id) if run.conversation_id else None
    conversation_messages = conv_svc.list_messages(run.conversation_id, limit=200) if run.conversation_id else []
    artifacts = (
        list(reversed(conv_svc.list_artifacts(run.conversation_id, limit=80)))
        if run.conversation_id
        else []
    )
    artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]] = {}
    for artifact in artifacts:
        if artifact.task_id:
            artifacts_by_task.setdefault(str(artifact.task_id), []).append(artifact)
    activity = team_svc.list_activity(run.id, limit=120)
    activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]] = {}
    for event in activity:
        if event.task_id:
            activity_by_task.setdefault(str(event.task_id), []).append(event)
    auto_review_rounds_used = 0
    for task in tasks:
        if task.artifact_goal != "review_notes":
            continue
        rounds = sum(
            1
            for event in activity_by_task.get(str(task.id), [])
            if event.event_type == "review_rejected"
        )
        auto_review_rounds_used = max(auto_review_rounds_used, rounds)
    serialized_tasks = []
    for task in tasks:
        serialized_tasks.append(
            _serialize_team_task_snapshot(
                run=run,
                task=task,
                deps_by_task=deps_by_task,
                tasks_by_id=tasks_by_id,
                done_ids=done_ids,
                session_by_id=session_by_id,
                artifacts_by_task=artifacts_by_task,
                activity_by_task=activity_by_task,
            )
        )
    deliverable = _extract_web_deliverable(db, run.conversation_id, run=run) if run.conversation_id else None
    source_files, _ = _collect_source_files(db, list(run.source_file_ids or []))
    payload = {
        "run": serialize_team_run(run),
        "conversation": serialize_conversation(conv) if conv else None,
        "items": [serialize_message(msg) for msg in conversation_messages],
        "tasks": serialized_tasks,
        "dependencies": [serialize_team_dependency(dep) for dep in dependencies],
        "activity": [serialize_team_activity(event) for event in activity],
        "sessions": [serialize_team_session(session) for session in sessions],
        "messages": [
            {
                **serialize_team_message(item),
                "from_handle": session_by_id.get(str(item.from_session_id)).handle if item.from_session_id and str(item.from_session_id) in session_by_id else None,
                "to_handle": session_by_id.get(str(item.to_session_id)).handle if item.to_session_id and str(item.to_session_id) in session_by_id else None,
            }
            for item in inbox_messages
        ],
        "artifacts": [serialize_artifact(artifact) for artifact in artifacts],
        "deliverable": deliverable,
        "source_files": [
            {
                "id": str(file_row.id),
                "original_name": file_row.original_name,
                "mime_type": file_row.mime_type,
                "document_type": analysis["document_type"],
                "document_summary": analysis["document_summary"],
                "document_ir": analysis["document_ir"],
            }
            for file_row in source_files
            for analysis in [_file_analysis_payload(file_row)]
        ],
    }
    leader_session = next((session for session in sessions if session.role == "leader"), None)
    payload["run"]["leader_session_id"] = str(leader_session.id) if leader_session else None
    payload["run"]["active_sessions"] = len([session for session in sessions if session.status != "offline"])
    payload["run"]["workflow_preset"] = _run_workflow_preset(run)
    payload["run"]["auto_review_max_rounds"] = AUTO_REVIEW_MAX_ROUNDS
    payload["run"]["auto_review_rounds_used"] = auto_review_rounds_used
    return payload


def _serialize_team_task_snapshot(
    *,
    run: conversation_models.TeamRunModel,
    task: conversation_models.TeamTaskModel,
    deps_by_task: dict[str, list[str]],
    tasks_by_id: dict[str, conversation_models.TeamTaskModel],
    done_ids: set[str],
    session_by_id: dict[str, conversation_models.TeamMemberSessionModel],
    artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]],
    activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]],
) -> dict:
    item = serialize_team_task(task)
    task_id = str(task.id)
    dep_ids = deps_by_task.get(task_id, [])
    task_artifacts = artifacts_by_task.get(task_id, [])
    task_events = activity_by_task.get(task_id, [])
    latest_artifact = task_artifacts[-1] if task_artifacts else None
    latest_event = task_events[-1] if task_events else None
    review = _review_snapshot(task, task_artifacts, task_events)

    item["depends_on_task_ids"] = dep_ids
    item["depends_on_titles"] = [
        tasks_by_id[dep_id].title
        for dep_id in dep_ids
        if dep_id in tasks_by_id
    ]
    item["ready"] = task.status == "todo" and task.claim_status == "open" and set(dep_ids).issubset(done_ids) and all(
        _dependency_review_satisfied(
            run=run,
            dependency_task=tasks_by_id[dep_id],
            task_artifacts=artifacts_by_task.get(dep_id, []),
            task_events=activity_by_task.get(dep_id, []),
        )
        for dep_id in dep_ids
        if dep_id in tasks_by_id
    )
    item["artifact_count"] = len(task_artifacts)
    item["latest_artifact_type"] = latest_artifact.artifact_type if latest_artifact else None
    item["latest_artifact_created_at"] = latest_artifact.created_at.isoformat() if latest_artifact else None
    item["latest_activity_type"] = latest_event.event_type if latest_event else None
    item["latest_activity_at"] = latest_event.created_at.isoformat() if latest_event else None
    item["claimed_by_handle"] = (
        session_by_id.get(str(task.claimed_by_session_id)).handle
        if task.claimed_by_session_id and str(task.claimed_by_session_id) in session_by_id
        else None
    )
    item["has_review_notes"] = bool(review["has_review_notes"])
    item["review_state"] = str(review["review_state"])
    return item


def _build_team_task_detail(
    db: Session,
    task: conversation_models.TeamTaskModel,
    *,
    run: conversation_models.TeamRunModel | None = None,
) -> dict:
    team_svc = TeamRunService(db)
    conv_svc = ConversationService(db)
    run = run or team_svc.get_run(task.team_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Team run not found")

    tasks = team_svc.list_tasks(run.id)
    sessions = team_svc.list_sessions(run.id)
    session_by_id = {str(session.id): session for session in sessions}
    tasks_by_id = {str(item.id): item for item in tasks}
    dependencies = team_svc.list_dependencies(run.id)
    deps_by_task: dict[str, list[str]] = {}
    for dep in dependencies:
        deps_by_task.setdefault(str(dep.team_task_id), []).append(str(dep.depends_on_task_id))
    done_ids = {str(item.id) for item in tasks if item.status == "done"}
    conv = conv_svc.get_conversation(run.conversation_id) if run.conversation_id else None
    all_artifacts = (
        list(reversed(conv_svc.list_artifacts(run.conversation_id, limit=120)))
        if run.conversation_id
        else []
    )
    artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]] = {}
    for artifact in all_artifacts:
        if artifact.task_id:
            artifacts_by_task.setdefault(str(artifact.task_id), []).append(artifact)
    activity = team_svc.list_activity(run.id, limit=200)
    activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]] = {}
    for event in activity:
        if event.task_id:
            activity_by_task.setdefault(str(event.task_id), []).append(event)

    task_snapshot = _serialize_team_task_snapshot(
        run=run,
        task=task,
        deps_by_task=deps_by_task,
        tasks_by_id=tasks_by_id,
        done_ids=done_ids,
        session_by_id=session_by_id,
        artifacts_by_task=artifacts_by_task,
        activity_by_task=activity_by_task,
    )
    dep_ids = task_snapshot["depends_on_task_ids"]
    dependency_items = [
        serialize_team_task(tasks_by_id[dep_id])
        for dep_id in dep_ids
        if dep_id in tasks_by_id
    ]
    return {
        "run": serialize_team_run(run),
        "conversation": serialize_conversation(conv) if conv else None,
        "task": task_snapshot,
        "dependencies": dependency_items,
        "available_dependencies": [
            serialize_team_task(item)
            for item in tasks
            if item.id != task.id
        ],
        "activity": [
            serialize_team_activity(event)
            for event in activity_by_task.get(str(task.id), [])
        ],
        "sessions": [serialize_team_session(session) for session in sessions],
        "artifacts": [
            serialize_artifact(artifact)
            for artifact in artifacts_by_task.get(str(task.id), [])
        ],
    }


def _fallback_task_artifact_content(
    *,
    task: conversation_models.TeamTaskModel,
    run: conversation_models.TeamRunModel,
    result: object | None,
    draft_bodies: list[str],
    review_bodies: list[str],
) -> str:
    visible = ""
    preset = _run_workflow_preset(run)
    if result is not None:
        visible = str(getattr(result, "visible_message", "") or "").strip()
    substantive = _best_effort_result_content(task=task, result=result)
    if task.artifact_goal == "brief":
        return (
            f"요청 개요\n"
            f"- 제목: {run.title}\n"
            f"- 요청자: {run.requested_by}\n"
            f"- 요청: {run.request_text}\n\n"
            f"작업 메모\n{substantive or visible or task.description}"
        ).strip()
    if task.artifact_goal == "review_notes":
        base = substantive or visible or task.description
        return f"검토 메모\n- 작업: {task.title}\n- 담당: {task.owner_handle}\n- 메모: {base}"
    if task.artifact_goal == "final":
        draft_text = "\n\n".join(draft_bodies) if draft_bodies else "- 초안 없음"
        review_text = "\n\n".join(review_bodies) if review_bodies else "- 검토 메모 없음"
        if preset == "presentation_team":
            slide_body = draft_text if draft_bodies else (substantive or visible or task.description)
            structured = _build_structured_deliverable(run.title or "최종 발표자료", slide_body)
            markdown = _structured_deliverable_to_markdown(structured, slide_body)
            return (
                f"# {run.title or '최종 발표자료'}\n\n"
                f"## 요청\n{run.request_text}\n\n"
                f"{markdown}\n\n"
                f"## 검토 요약\n{review_text}\n"
            ).strip()
        return (
            f"# {run.title or '최종 결과물'}\n\n"
            f"요청\n{run.request_text}\n\n"
            f"핵심 결과\n{draft_text}\n\n"
            f"검토 요약\n{review_text}\n"
        ).strip()
    if substantive:
        return substantive
    return (
        f"{task.title}\n\n"
        f"{visible or task.description}\n\n"
        f"원요청: {run.request_text}"
    ).strip()


def _latest_rework_feedback_for_task(
    *,
    team_svc: TeamRunService,
    run_id: uuid.UUID,
    task_id: uuid.UUID,
) -> dict | None:
    task_id_str = str(task_id)
    for event in reversed(team_svc.list_activity(run_id, limit=240)):
        if event.event_type != "review_rejected":
            continue
        payload = event.payload or {}
        reopened_ids = [str(item) for item in payload.get("reopened_task_ids") or []]
        if task_id_str not in reopened_ids:
            continue
        summary = str(payload.get("summary") or "").strip()
        risk_summary = str(payload.get("risk_summary") or "").strip()
        if not summary and not risk_summary:
            summary = str(event.summary or "").strip()
        return {
            "summary": summary,
            "risk_summary": risk_summary,
            "created_at": event.created_at.isoformat(),
        }
    return None


async def _execute_team_task(
    db: Session,
    run: conversation_models.TeamRunModel,
    task: conversation_models.TeamTaskModel,
    *,
    session: conversation_models.TeamMemberSessionModel | None = None,
) -> tuple[conversation_models.ConversationArtifactModel | None, str | None]:
    orchestrator._ensure_loaded()
    conv_svc = ConversationService(db)
    team_svc = TeamRunService(db)
    conv = conv_svc.get_conversation(run.conversation_id) if run.conversation_id else None
    if not conv:
        return None, "linked conversation missing"

    agent = orchestrator._agents.get(task.owner_handle)
    if not agent:
        return None, f"agent not found: {task.owner_handle}"

    participant = conv_svc.get_or_create_participant(
        conversation_id=conv.id,
        handle=agent.handle,
        type="agent",
        display_name=agent.display_name,
        provider=agent.config.provider,
        model=agent.config.model,
    )
    latest_artifacts = list(reversed(conv_svc.list_artifacts(conv.id, limit=12)))
    artifact_lines = []
    draft_bodies: list[str] = []
    review_bodies: list[str] = []
    for artifact in latest_artifacts:
        body = artifact.content.strip()
        if not body:
            continue
        artifact_lines.append(f"[{artifact.artifact_type}] {body[:800]}")
        if artifact.artifact_type == "draft":
            draft_bodies.append(body)
        if artifact.artifact_type == "review_notes":
            review_bodies.append(body)
    rework_feedback = _latest_rework_feedback_for_task(
        team_svc=team_svc,
        run_id=run.id,
        task_id=task.id,
    )
    claimed_session = session
    if not claimed_session and task.claimed_by_session_id:
        claimed_session = team_svc.get_session(task.claimed_by_session_id)
    inbox_context, consumed_inbox_ids = _session_inbox_context(team_svc, run, claimed_session)
    execution_contract = _task_execution_contract(run=run, task=task)
    rework_clause = (
        "최근 자동 반려 피드백을 반드시 반영하세요.\n"
        f"- 핵심 사유: {rework_feedback.get('summary')}\n"
        f"- 보강 포인트: {rework_feedback.get('risk_summary')}\n\n"
        if rework_feedback
        else ""
    )
    context = "\n\n".join(
        part
        for part in (
            f"팀 실행 제목: {run.title}",
            f"원요청: {run.request_text}",
            f"참고 문서 요약:\n{run.source_ir_summary}" if run.source_ir_summary else "",
            f"현재 작업: {task.title}",
            f"작업 설명: {task.description}",
            (
                "최근 자동 반려 피드백:\n"
                f"- 핵심 사유: {rework_feedback.get('summary') or '없음'}\n"
                f"- 보강 포인트: {rework_feedback.get('risk_summary') or '없음'}"
            )
            if rework_feedback
            else "",
            execution_contract,
            (
                f"세션 정보:\n- handle: {claimed_session.handle}\n- role: {claimed_session.role}\n- 최근 요약: {claimed_session.context_window_summary or '없음'}"
            )
            if claimed_session
            else "",
            inbox_context,
            "기존 작업공간:\n" + "\n\n".join(artifact_lines) if artifact_lines else "",
        )
        if part
    )
    user_request = (
        "\n".join(
            part
            for part in (
                "팀 작업을 수행하세요.",
                f"- 작업명: {task.title}",
                f"- 담당자: {task.owner_handle}",
                f"- 목표 산출물: {task.artifact_goal}",
                f"- 작업 설명: {task.description}",
                f"- 원요청: {run.request_text}",
                f"- 참고 문서 요약:\n{run.source_ir_summary}" if run.source_ir_summary else "",
                "",
                inbox_context if inbox_context else "",
                rework_clause.strip() if rework_clause else "",
                execution_contract,
            )
            if part is not None
        )
    )
    agent_run = conv_svc.create_agent_run(
        conversation_id=conv.id,
        agent_handle=task.owner_handle,
        provider=agent.config.provider,
        model=agent.config.model,
        speaker_identity=task.owner_handle,
        input_context_snapshot=context,
    )
    conv_svc.start_agent_run(agent_run.id)
    db.commit()

    try:
        result = await agent.run(user_request=user_request, context=context)
    except Exception as exc:
        conv_svc.finish_agent_run(
            agent_run.id,
            output="",
            input_snapshot=user_request,
            input_context_snapshot=context,
            error=str(exc),
        )
        db.commit()
        return None, str(exc)

    def build_artifact_payload(agent_result: object) -> tuple[str, str]:
        artifact_update = getattr(agent_result, "artifact_update", None) or {}
        artifact_type = str(artifact_update.get("type") or task.artifact_goal or "draft").strip().lower()
        if _is_review_gate_task(task):
            artifact_type = "review_notes"
        artifact_content = str(artifact_update.get("content") or "").strip()
        if not artifact_content:
            artifact_content = _fallback_task_artifact_content(
                task=task,
                run=run,
                result=agent_result,
                draft_bodies=draft_bodies,
                review_bodies=review_bodies,
            )
        return artifact_type, artifact_content

    artifact_type, artifact_content = build_artifact_payload(result)
    if (
        task.artifact_goal == "final"
        and _run_workflow_preset(run) == "presentation_team"
        and artifact_content.strip()
    ):
        artifact_content = _normalize_presentation_final_content(
            run=run,
            content=artifact_content,
            review_bodies=review_bodies,
        )
    if _is_status_only_artifact(task=task, content=artifact_content):
        repair_request = (
            f"{user_request}\n\n"
            "방금 출력은 상태 문구에 그쳐 실패했습니다. "
            "이번에는 `artifact_update.content`에 실제 결과물 본문만 다시 작성하세요. "
            "진행 상태, 대기 안내, 메타 설명은 금지입니다."
        )
        repair_context = (
            f"{context}\n\n"
            "직전 출력이 '작성 중/대기 중/준비 중' 같은 상태 문구로만 구성되어 실패했습니다. "
            "실제 본문 초안/검토/최종본을 충분히 작성하세요."
        )
        try:
            result = await agent.run(user_request=repair_request, context=repair_context)
            artifact_type, artifact_content = build_artifact_payload(result)
            if (
                task.artifact_goal == "final"
                and _run_workflow_preset(run) == "presentation_team"
                and artifact_content.strip()
            ):
                artifact_content = _normalize_presentation_final_content(
                    run=run,
                    content=artifact_content,
                    review_bodies=review_bodies,
                )
        except Exception as exc:
            conv_svc.finish_agent_run(
                agent_run.id,
                output="",
                input_snapshot=repair_request,
                input_context_snapshot=repair_context,
                error=str(exc),
            )
            db.commit()
            return None, str(exc)
    if _is_status_only_artifact(task=task, content=artifact_content):
        conv_svc.finish_agent_run(
            agent_run.id,
            output=str(getattr(result, "text", "") or getattr(result, "visible_message", "") or ""),
            input_snapshot=user_request,
            input_context_snapshot=context,
            error="non-substantive artifact generated",
        )
        db.commit()
        return None, "non-substantive artifact generated"
    artifact = conv_svc.create_or_replace_artifact(
        conversation_id=conv.id,
        task_id=task.id,
        source_run_id=agent_run.id,
        artifact_type=artifact_type,
        content=artifact_content,
        created_by_handle=task.owner_handle,
        replace_latest=True,
    )
    visible_message = str(result.visible_message or f"{task.title} 작업을 완료했습니다.").strip()
    conv_svc.create_message(
        conversation_id=conv.id,
        raw_text=visible_message,
        rendered_text=visible_message,
        message_type="agent",
        participant_id=participant.id,
        visible_message=visible_message,
        speaker_role=task.owner_handle,
        speaker_identity=task.owner_handle,
        task_status=f"{task.title} 완료",
        done=True,
        needs_user_input=False,
        is_progress_turn=True,
        is_agent_message=True,
    )
    conv_svc.finish_agent_run(
        agent_run.id,
        output=str(result.text or visible_message),
        input_snapshot=user_request,
        input_context_snapshot=context,
        suggested_next_agent=None,
        approved_next_agent=None,
        handoff_reason="team_task_execution",
        validation_result={"task_id": str(task.id), "artifact_type": artifact.artifact_type},
        fallback_applied=False,
        progress_detected=True,
        termination_reason="task_completed",
    )
    if claimed_session:
        team_svc.update_session(
            claimed_session.id,
            context_window_summary=(
                f"{task.title} -> {artifact.artifact_type}: "
                f"{artifact.content.strip().replace(chr(10), ' ')[:240]}"
            ),
        )
        if consumed_inbox_ids:
            team_svc.mark_inbox_messages_read(consumed_inbox_ids)
            team_svc.update_session(
                claimed_session.id,
                inbox_cursor=claimed_session.inbox_cursor + len(consumed_inbox_ids),
            )
    if artifact.artifact_type == "final":
        team_svc.update_run(run.id, final_artifact_id=artifact.id)
        conv.export_ready = True
        conv.done = True
        conv.status = "idle"
        conv.updated_at = now_utc()
    db.commit()
    return artifact, None


def _review_rejection_count(
    team_svc: TeamRunService,
    run_id: uuid.UUID,
    review_task_id: uuid.UUID,
) -> int:
    return sum(
        1
        for event in team_svc.list_activity(run_id, limit=240)
        if event.task_id == review_task_id and event.event_type == "review_rejected"
    )


def _find_final_task(
    team_svc: TeamRunService,
    run_id: uuid.UUID,
) -> conversation_models.TeamTaskModel | None:
    tasks = team_svc.list_tasks(run_id)
    for task in tasks:
        if task.artifact_goal == "final":
            return task
    for task in tasks:
        if task.owner_handle == "manager":
            return task
    return None


async def _manager_auto_review_decision(
    db: Session,
    run: conversation_models.TeamRunModel,
    review_task: conversation_models.TeamTaskModel,
    review_artifact: conversation_models.ConversationArtifactModel,
) -> dict:
    orchestrator._ensure_loaded()
    conv_svc = ConversationService(db)
    artifacts = list(reversed(conv_svc.list_artifacts(run.conversation_id, limit=40))) if run.conversation_id else []
    draft_bodies = [artifact.content.strip() for artifact in artifacts if artifact.artifact_type == "draft" and artifact.content.strip()]
    review_bodies = [artifact.content.strip() for artifact in artifacts if artifact.artifact_type == "review_notes" and artifact.content.strip()]
    manager = orchestrator._agents.get("manager")
    prompt = (
        "당신은 PM(manager)입니다. critic의 review_notes를 보고 승인/반려를 결정하세요.\n\n"
        f"팀 실행 제목: {run.title}\n"
        f"원요청: {run.request_text}\n"
        f"검토 작업: {review_task.title}\n\n"
        f"최신 초안:\n{chr(10).join(draft_bodies[-2:]) if draft_bodies else '- 초안 없음'}\n\n"
        f"최신 검토 메모:\n{review_artifact.content.strip()}\n\n"
        "반드시 approve 또는 reject 중 하나를 선택하고, 한두 문장 요약과 남은 리스크를 적으세요."
    )
    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string"},
            "summary": {"type": "string"},
            "risk_summary": {"type": "string"},
        },
    }
    structured: dict = {}
    if manager and getattr(manager, "_provider", None):
        try:
            structured = await manager._provider.generate_structured(prompt, schema)  # type: ignore[attr-defined]
        except Exception:
            structured = {}

    raw = " ".join(
        part
        for part in (
            str(structured.get("decision") or "").strip(),
            str(structured.get("summary") or "").strip(),
            str(structured.get("risk_summary") or "").strip(),
            str(structured.get("raw") or "").strip(),
            review_artifact.content.strip(),
        )
        if part
    ).lower()
    decision = str(structured.get("decision") or "").strip().lower()
    fallback_used = decision not in {"approve", "reject"}
    if decision not in {"approve", "reject"}:
        decision = "reject" if any(keyword in raw for keyword in AUTO_REVIEW_REJECT_KEYWORDS) else "approve"
    summary = str(structured.get("summary") or "").strip()
    if not summary:
        summary = (
            "자동 검토 결과, 추가 보강이 필요해 반려합니다."
            if decision == "reject"
            else "자동 검토 결과, 현재 품질로 승인 가능합니다."
        )
    risk_summary = str(structured.get("risk_summary") or "").strip()
    if not risk_summary and decision == "reject":
        risk_summary = "critic review_notes 기준으로 근거/표현 보강이 더 필요합니다."
    elif not risk_summary:
        risk_summary = "현재 자동 검토 기준에서 치명적 리스크는 발견되지 않았습니다."
    return {
        "decision": decision,
        "summary": summary,
        "risk_summary": risk_summary,
        "fallback_used": fallback_used,
        "review_bodies": review_bodies,
        "draft_bodies": draft_bodies,
    }


def _build_done_with_risks_content(
    *,
    run: conversation_models.TeamRunModel,
    draft_bodies: list[str],
    review_bodies: list[str],
    summary: str,
    risk_summary: str,
    rounds_used: int,
) -> str:
    draft_text = "\n\n".join(draft_bodies[-2:]) if draft_bodies else "- 초안 없음"
    review_text = "\n\n".join(review_bodies[-2:]) if review_bodies else "- 검토 메모 없음"
    if _run_workflow_preset(run) == "presentation_team":
        primary_draft = _select_presentation_primary_body(draft_bodies)
        fallback_body = primary_draft or (
            "## 슬라이드 1: 자동 검토 마감\n"
            "- 자동 검토가 리스크 포함 상태로 종료되었습니다.\n"
            f"- 현재 판단: {summary}\n"
            f"- 남은 리스크: {risk_summary}\n"
            "- 발표 포인트: 본 발표안은 추가 근거 보강이 필요한 상태입니다.\n"
        )
        return _normalize_presentation_final_content(
            run=run,
            content=fallback_body,
            review_bodies=review_bodies,
            appendix_sections=[
                (
                    "작성 메모",
                    [
                        "상태: done_with_risks",
                        f"자동 반려 횟수: {rounds_used}",
                        f"현재 판단: {summary}",
                        f"남은 리스크: {risk_summary}",
                    ],
                )
            ],
        )
    return (
        f"# {run.title or '최종 결과물'}\n\n"
        "## 자동 검토 종료 상태\n"
        f"- 상태: done_with_risks\n"
        f"- 자동 반려 횟수: {rounds_used}\n"
        f"- 요청: {run.request_text}\n\n"
        "## 현재 판단\n"
        f"{summary}\n\n"
        "## 남은 리스크\n"
        f"{risk_summary}\n\n"
        "## 최신 초안\n"
        f"{draft_text}\n\n"
        "## 최신 검토 메모\n"
        f"{review_text}\n"
    ).strip()


def _publish_done_with_risks(
    db: Session,
    run: conversation_models.TeamRunModel,
    review_task: conversation_models.TeamTaskModel,
    *,
    summary: str,
    risk_summary: str,
    draft_bodies: list[str],
    review_bodies: list[str],
    rounds_used: int,
) -> conversation_models.ConversationArtifactModel | None:
    conv_svc = ConversationService(db)
    team_svc = TeamRunService(db)
    conv = conv_svc.get_conversation(run.conversation_id) if run.conversation_id else None
    if not conv:
        return None
    participant = conv_svc.get_or_create_participant(
        conversation_id=conv.id,
        handle="manager",
        type="agent",
        display_name="manager",
    )
    final_task = _find_final_task(team_svc, run.id)
    content = _build_done_with_risks_content(
        run=run,
        draft_bodies=draft_bodies,
        review_bodies=review_bodies,
        summary=summary,
        risk_summary=risk_summary,
        rounds_used=rounds_used,
    )
    artifact = conv_svc.create_or_replace_artifact(
        conversation_id=conv.id,
        task_id=final_task.id if final_task else review_task.id,
        artifact_type="final",
        content=content,
        created_by_handle="manager",
        replace_latest=True,
    )
    conv_svc.create_message(
        conversation_id=conv.id,
        raw_text="자동 검토 한도에 도달해 리스크 포함 최종본으로 마감합니다.",
        rendered_text="자동 검토 한도에 도달해 리스크 포함 최종본으로 마감합니다.",
        message_type="agent",
        participant_id=participant.id,
        visible_message="자동 검토 한도에 도달해 리스크 포함 최종본으로 마감합니다.",
        speaker_role="manager",
        speaker_identity="manager",
        task_status="리스크 포함 최종본 마감",
        done=True,
        needs_user_input=False,
        is_progress_turn=True,
        is_agent_message=True,
    )
    if final_task:
        team_svc.update_task(final_task.id, status="done")
    team_svc.update_run(run.id, final_artifact_id=artifact.id, status="done_with_risks")
    conv.export_ready = True
    conv.done = True
    conv.status = "idle"
    conv.updated_at = now_utc()
    return artifact


async def _maybe_auto_review_task(
    db: Session,
    run: conversation_models.TeamRunModel,
    task: conversation_models.TeamTaskModel,
    artifact: conversation_models.ConversationArtifactModel | None,
) -> None:
    if run.oversight_mode != "auto" or not artifact or artifact.artifact_type != "review_notes":
        return
    team_svc = TeamRunService(db)
    team_svc.create_activity(
        team_run_id=run.id,
        task_id=task.id,
        event_type="auto_review_started",
        actor_handle="manager",
        target_handle=task.owner_handle,
        summary=f"manager가 '{task.title}' 자동 검토 판정을 시작했습니다.",
    )
    db.commit()

    decision = await _manager_auto_review_decision(db, run, task, artifact)
    if decision["fallback_used"]:
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=task.id,
            event_type="auto_review_fallback",
            actor_handle="manager",
            target_handle=task.owner_handle,
            summary=f"manager가 '{task.title}' 자동 검토를 fallback 규칙으로 판정했습니다.",
            payload={"decision": decision["decision"]},
        )
    if decision["decision"] == "approve":
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=task.id,
            event_type="review_approved",
            actor_handle="manager",
            target_handle=task.owner_handle,
            summary=f"manager가 '{task.title}' 검토를 자동 승인했습니다.",
            payload={"summary": decision["summary"]},
        )
        final_task = _find_final_task(team_svc, run.id)
        if final_task:
            _create_team_inbox_message(
                team_svc,
                run,
                from_handle="manager",
                to_handle=final_task.owner_handle,
                related_task_id=final_task.id,
                message_type="review_feedback",
                subject="자동 검토 승인",
                content=f"'{task.title}' 검토가 자동 승인되었습니다. 최종 산출물을 정리하세요.",
            )
        db.commit()
        return

    rounds_used = _review_rejection_count(team_svc, run.id, task.id) + 1
    if rounds_used > AUTO_REVIEW_MAX_ROUNDS:
        final_artifact = _publish_done_with_risks(
            db,
            run,
            task,
            summary=decision["summary"],
            risk_summary=decision["risk_summary"],
            draft_bodies=decision["draft_bodies"],
            review_bodies=decision["review_bodies"],
            rounds_used=rounds_used - 1,
        )
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=task.id,
            event_type="final_published",
            actor_handle="manager",
            target_handle=task.owner_handle,
            summary=f"자동 검토가 {AUTO_REVIEW_MAX_ROUNDS}회 재작업 후 종료되어 리스크 포함 최종본으로 마감됐습니다.",
            payload={"artifact_id": str(final_artifact.id)} if final_artifact else None,
        )
        db.commit()
        return

    reopened = _reopen_review_branch(
        team_svc=team_svc,
        run_id=run.id,
        review_task_id=task.id,
    )
    _supersede_reopened_task_artifacts(db, run=run, tasks=reopened)
    team_svc.create_activity(
        team_run_id=run.id,
        task_id=task.id,
        event_type="review_rejected",
        actor_handle="manager",
        target_handle=task.owner_handle,
        summary=(
            f"manager가 '{task.title}' 검토를 자동 반려했고 "
            f"재작업 대상 {len(reopened)}개를 다시 열었습니다."
        ),
        payload={
            "reopened_task_ids": [str(item.id) for item in reopened],
            "round": rounds_used,
            "summary": decision["summary"],
            "risk_summary": decision["risk_summary"],
        },
    )
    for reopened_task in reopened:
        if reopened_task.id == task.id:
            continue
        _create_team_inbox_message(
            team_svc,
            run,
            from_handle="manager",
            to_handle=reopened_task.owner_handle,
            related_task_id=reopened_task.id,
            message_type="review_feedback",
            subject="자동 검토 반려",
            content=(
                f"자동 검토에서 재작업이 필요합니다.\n"
                f"- 핵심 사유: {decision['summary']}\n"
                f"- 보강 포인트: {decision['risk_summary']}"
            ),
        )
    team_svc.update_run(run.id, status="active")
    db.commit()


async def _run_team_scheduler(
    db: Session,
    team_run_id: uuid.UUID,
) -> conversation_models.TeamRunModel | None:
    team_svc = TeamRunService(db)
    run = team_svc.get_run(team_run_id)
    if not run:
        return None
    if run.plan_status != "approved":
        return run

    team_svc.update_run(run.id, status="running")
    db.commit()

    while True:
        run = team_svc.get_run(team_run_id)
        if not run:
            return None
        conv_svc = ConversationService(db)
        artifacts = (
            list(reversed(conv_svc.list_artifacts(run.conversation_id, limit=120)))
            if run.conversation_id
            else []
        )
        artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]] = {}
        for artifact in artifacts:
            if artifact.task_id:
                artifacts_by_task.setdefault(str(artifact.task_id), []).append(artifact)
        activity = team_svc.list_activity(run.id, limit=240)
        activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]] = {}
        for event in activity:
            if event.task_id:
                activity_by_task.setdefault(str(event.task_id), []).append(event)
        ready = _eligible_ready_tasks(team_svc, run, artifacts_by_task, activity_by_task)
        if not ready:
            break
        claimed_pairs = _claim_ready_tasks_for_idle_sessions(team_svc, run, ready)
        if not claimed_pairs:
            for task in ready:
                _ensure_session_for_handle(team_svc, run, task.owner_handle)
            claimed_pairs = _claim_ready_tasks_for_idle_sessions(team_svc, run, ready)
        if not claimed_pairs:
            break
        for task, session in claimed_pairs:
            team_svc.update_task(task.id, status="in_progress")
            team_svc.create_activity(
                team_run_id=run.id,
                task_id=task.id,
                event_type="task_claimed",
                actor_handle=session.handle,
                target_handle=task.owner_handle,
                summary=f"{session.handle} 세션이 ready 상태의 '{task.title}' 작업을 선점했습니다.",
                payload={"session_id": str(session.id)},
            )
            team_svc.create_activity(
                team_run_id=run.id,
                task_id=task.id,
                event_type="task_started",
                actor_handle=session.handle,
                summary=f"{session.handle} 세션이 '{task.title}' 작업을 시작했습니다.",
            )
            db.commit()

            artifact, error = await _execute_team_task(db, run, task, session=session)
            if error:
                team_svc.release_task_claim(task.id, reset_status="blocked")
                team_svc.update_task(task.id, status="blocked")
                team_svc.update_run(run.id, status="blocked")
                team_svc.create_activity(
                    team_run_id=run.id,
                    task_id=task.id,
                    event_type="task_blocked",
                    actor_handle=session.handle,
                    summary=f"{session.handle} 세션이 '{task.title}' 작업 중 오류로 중단되었습니다: {error}",
                )
                _create_team_inbox_message(
                    team_svc,
                    run,
                    from_handle=session.handle,
                    to_handle="planner",
                    related_task_id=task.id,
                    message_type="task_status",
                    subject="작업 차단",
                    content=f"'{task.title}' 작업이 오류로 차단되었습니다: {error}",
                )
                db.commit()
                return team_svc.get_run(team_run_id)

            team_svc.release_task_claim(task.id, reset_status="done")
            completed = team_svc.update_task(task.id, status="done")
            summary = f"{session.handle} 세션이 '{task.title}' 작업을 완료했습니다."
            if artifact and artifact.artifact_type == "review_notes":
                summary = f"{session.handle} 세션이 '{task.title}' 검토를 완료했습니다."
            if artifact and artifact.artifact_type == "final":
                summary = f"{session.handle} 세션이 최종 결과물을 정리했습니다."
            team_svc.create_activity(
                team_run_id=run.id,
                task_id=task.id,
                event_type="final_published" if artifact and artifact.artifact_type == "final" else "task_completed",
                actor_handle=session.handle,
                summary=summary,
                payload={"artifact_id": str(artifact.id)} if artifact else None,
            )
            if completed:
                db.commit()
            await _maybe_auto_review_task(db, run, task, artifact)

    run = team_svc.get_run(team_run_id)
    if not run:
        return None
    _refresh_team_run_status(db, team_svc, run.id)
    db.commit()
    return team_svc.get_run(team_run_id)


def _refresh_team_run_status(db: Session, team_svc: TeamRunService, team_run_id: uuid.UUID) -> None:
    run = team_svc.get_run(team_run_id)
    if not run:
        return
    if run.plan_status == "awaiting_approval":
        team_svc.update_run(run.id, status="awaiting_plan_approval")
        return
    if run.plan_status == "rejected":
        team_svc.update_run(run.id, status="blocked")
        return
    tasks = team_svc.list_tasks(run.id)
    conv_svc = ConversationService(db)
    artifacts = (
        list(reversed(conv_svc.list_artifacts(run.conversation_id, limit=120)))
        if run.conversation_id
        else []
    )
    artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]] = {}
    for artifact in artifacts:
        if artifact.task_id:
            artifacts_by_task.setdefault(str(artifact.task_id), []).append(artifact)
    activity = team_svc.list_activity(run.id, limit=240)
    activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]] = {}
    for event in activity:
        if event.task_id:
            activity_by_task.setdefault(str(event.task_id), []).append(event)
    ready = _eligible_ready_tasks(team_svc, run, artifacts_by_task, activity_by_task)
    if run.status == "done_with_risks":
        return
    if tasks and all(task.status == "done" for task in tasks):
        team_svc.update_run(run.id, status="done")
    elif any(task.status == "blocked" for task in tasks):
        team_svc.update_run(run.id, status="blocked")
    elif run.oversight_mode == "manual" and any(
        _review_snapshot(
            task,
            artifacts_by_task.get(str(task.id), []),
            activity_by_task.get(str(task.id), []),
        )["review_state"] == "reviewed"
        for task in tasks
        if _is_review_gate_task(task)
    ) and not ready:
        team_svc.update_run(run.id, status="awaiting_review")
    else:
        team_svc.update_run(run.id, status="active")


def _coerce_optional_uuid(value: object | None) -> uuid.UUID | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _coerce_uuid_list(value: object | None) -> list[uuid.UUID]:
    if not value:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="depends_on_task_ids must be a list")
    result = []
    for item in value:
        result.append(_coerce_optional_uuid(item))
    return [item for item in result if item is not None]


def _validate_task_owner(run: conversation_models.TeamRunModel, owner_handle: str) -> None:
    if run.selected_agents and owner_handle not in set(run.selected_agents):
        raise HTTPException(status_code=400, detail="owner_handle must be in selected_agents")


def _validate_artifact_goal(artifact_goal: str) -> None:
    if artifact_goal not in {"brief", "draft", "review_notes", "decision", "final"}:
        raise HTTPException(status_code=400, detail="invalid artifact_goal")


def _validate_dependency_ids(
    team_svc: TeamRunService,
    run_id: uuid.UUID,
    task_id: uuid.UUID,
    dependency_ids: list[uuid.UUID],
) -> None:
    tasks = {task.id: task for task in team_svc.list_tasks(run_id)}
    for dependency_id in dependency_ids:
        if dependency_id == task_id:
            raise HTTPException(status_code=400, detail="task cannot depend on itself")
        if dependency_id not in tasks:
            raise HTTPException(status_code=400, detail="depends_on_task_ids must belong to the same run")


def _creates_dependency_cycle(
    team_svc: TeamRunService,
    run_id: uuid.UUID,
    task_id: uuid.UUID,
    dependency_ids: list[uuid.UUID],
) -> bool:
    deps_by_task: dict[uuid.UUID, set[uuid.UUID]] = {}
    for dep in team_svc.list_dependencies(run_id):
        if dep.team_task_id == task_id:
            continue
        deps_by_task.setdefault(dep.team_task_id, set()).add(dep.depends_on_task_id)
    deps_by_task[task_id] = set(dependency_ids)

    seen: set[uuid.UUID] = set()
    queue = list(dependency_ids)
    while queue:
        current = queue.pop(0)
        if current == task_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        queue.extend(item for item in deps_by_task.get(current, set()) if item not in seen)
    return False


def _task_has_review_notes(
    db: Session,
    run: conversation_models.TeamRunModel,
    task_id: uuid.UUID,
) -> bool:
    if not run.conversation_id:
        return False
    conv_svc = ConversationService(db)
    artifacts = conv_svc.list_artifacts(run.conversation_id, limit=120)
    return any(
        artifact.task_id == task_id and artifact.artifact_type == "review_notes"
        for artifact in artifacts
    )


def _task_is_ready(team_svc: TeamRunService, run_id: uuid.UUID, task_id: uuid.UUID, db: Session) -> bool:
    run = team_svc.get_run(run_id)
    if not run:
        return False
    conv_svc = ConversationService(db)
    artifacts = (
        list(reversed(conv_svc.list_artifacts(run.conversation_id, limit=120)))
        if run.conversation_id
        else []
    )
    artifacts_by_task: dict[str, list[conversation_models.ConversationArtifactModel]] = {}
    for artifact in artifacts:
        if artifact.task_id:
            artifacts_by_task.setdefault(str(artifact.task_id), []).append(artifact)
    activity = team_svc.list_activity(run.id, limit=240)
    activity_by_task: dict[str, list[conversation_models.TeamActivityEventModel]] = {}
    for event in activity:
        if event.task_id:
            activity_by_task.setdefault(str(event.task_id), []).append(event)
    return any(task.id == task_id for task in _eligible_ready_tasks(team_svc, run, artifacts_by_task, activity_by_task))


def _reset_team_task_branch(
    *,
    team_svc: TeamRunService,
    run_id: uuid.UUID,
    task_id: uuid.UUID,
    include_descendants: bool,
) -> list[conversation_models.TeamTaskModel]:
    tasks = team_svc.list_tasks(run_id)
    tasks_by_id = {task.id: task for task in tasks}
    target_ids = {task_id}
    if include_descendants:
        children_by_parent: dict[uuid.UUID, set[uuid.UUID]] = {}
        for dep in team_svc.list_dependencies(run_id):
            children_by_parent.setdefault(dep.depends_on_task_id, set()).add(dep.team_task_id)
        queue = [task_id]
        while queue:
            current = queue.pop(0)
            for child_id in children_by_parent.get(current, set()):
                if child_id not in target_ids:
                    target_ids.add(child_id)
                    queue.append(child_id)

    reopened = []
    for current_id in target_ids:
        task = tasks_by_id.get(current_id)
        if not task:
            continue
        if task.claim_status == "claimed":
            team_svc.release_task_claim(current_id, reset_status="open")
        updated = team_svc.update_task(current_id, status="todo", claim_status="open")
        if updated:
            reopened.append(updated)
    reopened.sort(key=lambda item: (item.priority, item.created_at))
    return reopened


def _supersede_reopened_task_artifacts(
    db: Session,
    *,
    run: conversation_models.TeamRunModel,
    tasks: list[conversation_models.TeamTaskModel],
) -> None:
    if not run.conversation_id or not tasks:
        return
    task_ids = [task.id for task in tasks]
    (
        db.query(conversation_models.ConversationArtifactModel)
        .filter(
            conversation_models.ConversationArtifactModel.conversation_id == run.conversation_id,
            conversation_models.ConversationArtifactModel.task_id.in_(task_ids),
            conversation_models.ConversationArtifactModel.status.in_(("active", "final")),
        )
        .update({"status": "superseded"}, synchronize_session=False)
    )


def _reopen_review_branch(
    *,
    team_svc: TeamRunService,
    run_id: uuid.UUID,
    review_task_id: uuid.UUID,
) -> list[conversation_models.TeamTaskModel]:
    dependencies = team_svc.list_dependencies(run_id)
    upstream_ids = [
        dep.depends_on_task_id
        for dep in dependencies
        if dep.team_task_id == review_task_id
    ]
    upstream_tasks = [team_svc.get_task(task_id) for task_id in upstream_ids]
    targets = [
        task
        for task in upstream_tasks
        if task and task.artifact_goal != "brief"
    ]
    if not targets:
        review_task = team_svc.get_task(review_task_id)
        targets = [review_task] if review_task else []

    reopened_by_id: dict[uuid.UUID, conversation_models.TeamTaskModel] = {}
    for target in targets:
        for item in _reset_team_task_branch(
            team_svc=team_svc,
            run_id=run_id,
            task_id=target.id,
            include_descendants=True,
        ):
            reopened_by_id[item.id] = item
    reopened = sorted(
        reopened_by_id.values(),
        key=lambda item: (item.priority, item.created_at),
    )
    return reopened


def _progress_summary_for_message(msg: conversation_models.MessageModel) -> str:
    actor = (msg.speaker_role or "").strip().lower()
    target = (msg.approved_next_agent or msg.suggested_next_agent or "").strip().lower() or None
    mapped = {
        ("planner", "writer"): "작업 기준을 정리하고 초안 작성을 요청",
        ("writer", "critic"): "초안을 작성하고 검토를 요청",
        ("critic", "manager"): "검토 의견을 정리하고 최종 판단을 요청",
        ("manager", None): "최종 결과물을 정리",
        ("planner", None): "작업 기준을 정리",
        ("writer", None): "초안을 작성",
        ("critic", None): "검토 의견을 작성",
    }.get((actor, target))
    if mapped:
        return mapped
    return _compact_progress_text(msg.task_status or msg.visible_message or msg.raw_text)


def _format_progress_label(
    actor: str | None,
    target: str | None,
    summary: str,
) -> str:
    actor_label = (actor or "system").strip().lower() or "system"
    target_label = (target or "").strip().lower() or None
    if target_label:
        return f"{actor_label} -> {target_label} · {summary}"
    return f"{actor_label} · {summary}"


def _build_progress_steps(
    conv: conversation_models.ConversationModel,
    messages: list[conversation_models.MessageModel],
) -> list[dict]:
    steps: list[dict] = []
    for msg in messages:
        if msg.message_type != "agent":
            continue
        actor = (msg.speaker_role or "").strip().lower() or "agent"
        target = (msg.approved_next_agent or msg.suggested_next_agent or "").strip().lower() or None
        steps.append(
            {
                "actor_handle": actor,
                "target_handle": target,
                "summary": _progress_summary_for_message(msg),
                "status": "waiting_user" if msg.needs_user_input else ("done" if msg.done else "in_progress"),
                "created_at": msg.created_at.isoformat(),
                "label": _format_progress_label(actor, target, _progress_summary_for_message(msg)),
            }
        )

    if conv.approved_next_agent and not conv.done:
        selected = set(conv.selected_agents or [])
        if conv.approved_next_agent not in selected:
            summary = f"필수 역할({conv.approved_next_agent})이 팀에 없어 진행이 중단됨. 팀 구성 수정 필요"
            status = "blocked"
        else:
            summary = _compact_progress_text(conv.task_status) if conv.task_status else "다음 단계 진행 대기"
            status = "pending"
        steps.append(
            {
                "actor_handle": conv.current_agent or "system",
                "target_handle": conv.approved_next_agent,
                "summary": summary,
                "status": status,
                "created_at": conv.updated_at.isoformat(),
                "label": _format_progress_label(conv.current_agent or "system", conv.approved_next_agent, summary),
            }
        )

    return steps[-12:]


def _build_workspace_snapshot(db: Session, conv: conversation_models.ConversationModel) -> dict:
    svc = ConversationService(db)
    messages = svc.list_messages(conv.id, limit=200)
    artifacts = list(reversed(svc.list_artifacts(conv.id, limit=30)))
    return {
        "conversation": serialize_conversation(conv),
        "items": [serialize_message(m) for m in messages],
        "progress_steps": _build_progress_steps(conv, messages),
        "artifacts": [serialize_artifact(a) for a in artifacts],
        "deliverable": _extract_web_deliverable(db, conv.id),
    }


@router.post("/web/chats", status_code=201)
def create_web_chat(
    payload: dict,
    db: Session = Depends(get_db),
):
    title = str(payload.get("title") or "Web Team Chat").strip()[:120]
    mode = str(payload.get("mode") or settings.orchestrator_default_mode).strip().lower()
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    raw_selected = payload.get("selected_agents")
    if not isinstance(raw_selected, list):
        raw_selected = ["planner", "writer", "critic", "manager"]
    selected = _normalize_web_selected_agents(
        selected=raw_selected,
        valid_handles=valid,
        mode=mode,
    )

    conv = conversation_models.ConversationModel(
        platform="web",
        chat_id=f"web:{uuid.uuid4().hex}",
        topic_id=None,
        title=title or "Web Team Chat",
        mode=mode,
        autonomy_level=mode,
        selected_agents=selected,
        status="idle",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return serialize_conversation(conv)


@router.put("/web/chats/{conversation_id}/agents", status_code=200)
def update_web_chat_agents(
    conversation_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")

    selected = payload.get("selected_agents")
    if not isinstance(selected, list):
        raise HTTPException(status_code=400, detail="selected_agents must be a list")
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    selected = _normalize_web_selected_agents(
        selected=selected,
        valid_handles=valid,
        mode=conv.mode,
    )

    conv.selected_agents = selected
    conv.updated_at = now_utc()
    db.commit()
    db.refresh(conv)
    return {"ok": True, "conversation": serialize_conversation(conv)}


@router.get("/web/chats/{conversation_id}/deliverable", status_code=200)
def get_web_chat_deliverable(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")
    return {"item": _extract_web_deliverable(db, conversation_id)}


@router.get("/web/chats/{conversation_id}/workspace", status_code=200)
def get_web_chat_workspace(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")
    return _build_workspace_snapshot(db, conv)


@router.post("/web/chats/{conversation_id}/messages", status_code=202)
async def send_web_chat_message(
    conversation_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    text = str(payload.get("text") or "").strip()
    sender_name = str(payload.get("sender_name") or "web_user").strip() or "web_user"
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")

    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    normalized_selected = _normalize_web_selected_agents(
        selected=list(conv.selected_agents or []),
        valid_handles=valid,
        mode=conv.mode,
    )
    if normalized_selected != list(conv.selected_agents or []):
        conv.selected_agents = normalized_selected

    conv.title = conv.title or text[:100]
    db.commit()

    base_dispatcher = orchestrator._get_dispatcher()
    web_dispatcher = _WebDispatcher(base_dispatcher)
    await orchestrator.process_message(
        db=db,
        chat_id=conv.chat_id,
        text=text,
        sender_name=sender_name,
        telegram_message_id=None,
        send_fn=None,
        topic_id=conv.topic_id,
        inbound_identity="pm",
        chat_type="web",
        dispatcher_override=web_dispatcher,
        available_handles=list(conv.selected_agents or []),
    )

    refreshed = (
        db.query(conversation_models.ConversationModel)
        .filter(
            conversation_models.ConversationModel.chat_id == conv.chat_id,
            conversation_models.ConversationModel.platform == "web",
        )
        .order_by(conversation_models.ConversationModel.updated_at.desc())
        .first()
    )
    if not refreshed:
        raise HTTPException(status_code=500, detail="Failed to refresh conversation")
    return _build_workspace_snapshot(db, refreshed)


# ── Agents ────────────────────────────────────────────────────────────────────

@router.get("/agents")
def list_agents():
    """List all configured agents."""
    return {"agents": orchestrator.list_agents_info()}


@router.post("/agents/reload-config", status_code=200)
def reload_agent_config():
    """Hot-reload agents.yaml without restart."""
    orchestrator.reload_agents()
    return {"ok": True, "agents": orchestrator.list_agents_info()}
