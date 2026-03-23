from pathlib import Path
import threading
import hashlib
import hmac
import json
import shutil
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
from app.services.document_parser import extract_text
from app.services.job_dispatcher import dispatch_job
from app.services.llm_router import get_llm_provider
from app.services.planner_agent import PlannerAgent
from app.adapters.telegram.handlers import process_update
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_agent_run,
    serialize_conversation,
    serialize_message,
)
from app.orchestrator.engine import orchestrator

router = APIRouter()


@router.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


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
    extracted_text = extract_text(str(stored_path), uploaded_file.content_type)

    file_row = FileModel(
        project_id=project_id,
        job_id=None,
        original_name=filename,
        stored_path=str(stored_path),
        mime_type=uploaded_file.content_type or "application/octet-stream",
        size=size,
        source_type="upload",
        extracted_text=extracted_text,
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
        created_at=file_row.created_at,
    )


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
            ).model_dump()
            for item in artifacts
        ],
        "tasks": [
            TaskSummary(id=task.id, task_type=task.task_type,
                        status=task.status).model_dump()
            for task in tasks
        ],
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
