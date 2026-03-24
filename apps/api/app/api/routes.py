from pathlib import Path
import threading
import hashlib
import hmac
import json
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
from app.services.document_parser import extract_text
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
    serialize_team_run,
    serialize_team_task,
)
from app.orchestrator.engine import orchestrator
from app.team_runtime.service import TeamRunService

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
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    raw_selected = payload.get("selected_agents")
    if not isinstance(raw_selected, list):
        raw_selected = ["planner", "writer", "critic", "manager", "coder"]
    selected = _normalize_web_selected_agents(
        selected=raw_selected,
        valid_handles=valid,
        mode=mode,
    )

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
        requested_by=requested_by,
        selected_agents=selected,
        status="idle",
    )
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

    if _task_is_ready(team_svc, run.id, task.id):
        team_svc.update_run(run.id, status="active")
        db.commit()
        await _run_team_scheduler(db, run.id)
    else:
        _refresh_team_run_status(team_svc, run.id)
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
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    team_svc = TeamRunService(db)
    conv_svc = ConversationService(db)
    run = team_svc.get_run(team_run_id)
    if not run or not run.conversation_id:
        raise HTTPException(status_code=404, detail="Team run not found")

    conv = db.get(conversation_models.ConversationModel, run.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Linked conversation not found")

    selected = _normalize_web_selected_agents(
        selected=list(run.selected_agents or conv.selected_agents or []),
        valid_handles={a["handle"] for a in orchestrator.list_agents_info()},
        mode=run.mode,
    )
    run.selected_agents = selected
    conv.selected_agents = selected
    conv.updated_at = now_utc()

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
        )
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=followup.id,
            event_type="task_created",
            actor_handle=sender_name,
            target_handle="planner",
            summary="사용자가 후속 요청을 남겼고 PM이 반영 작업을 추가했습니다.",
        )
        team_svc.update_run(run.id, status="active")
        db.commit()
        await _run_team_scheduler(db, run.id)
        db.refresh(run)
        return _build_team_board_snapshot(db, run)

    team_svc.update_run(run.id, request_text=text, status="planning")
    brief, task_defs = await _decompose_team_request(text, selected)

    planner_task = team_svc.create_task(
        team_run_id=run.id,
        title="요청 정리 및 작업 구조화",
        description="PM이 요청을 해석하고 팀 작업 구조를 확정합니다.",
        owner_handle="planner",
        artifact_goal="brief",
        created_by_handle="planner",
        review_required=False,
        status="done",
        priority=10,
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
            priority=int(task_def.get("priority", 50)),
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

    team_svc.update_run(run.id, status="active")
    db.commit()
    await _run_team_scheduler(db, run.id)
    db.refresh(run)
    return _build_team_board_snapshot(db, run)


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

    if owner_changed:
        team_svc.create_activity(
            team_run_id=updated.team_run_id,
            task_id=updated.id,
            event_type="task_assigned",
            actor_handle=actor_handle,
            target_handle=updated.owner_handle,
            summary=f"{actor_handle}가 '{updated.title}' 작업 담당자를 {previous_owner}에서 {updated.owner_handle}로 변경했습니다.",
        )

    if action in {"rerun", "unblock"}:
        reopened = _reset_team_task_branch(
            team_svc=team_svc,
            run_id=updated.team_run_id,
            task_id=updated.id,
            include_descendants=action == "rerun",
        )
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
            db.commit()
            refreshed_run = team_svc.get_run(updated.team_run_id)
            if not refreshed_run:
                raise HTTPException(status_code=500, detail="Failed to refresh team run")
            return _build_team_board_snapshot(db, refreshed_run)

        reopened = _reopen_review_branch(
            team_svc=team_svc,
            run_id=run.id,
            review_task_id=updated.id,
        )
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
        if _task_is_ready(team_svc, run.id, updated.id):
            team_svc.update_run(updated.team_run_id, status="active")
            db.commit()
            await _run_team_scheduler(db, updated.team_run_id)
        else:
            _refresh_team_run_status(team_svc, updated.team_run_id)
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

    _refresh_team_run_status(team_svc, updated.team_run_id)

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
        team_svc.create_activity(
            team_run_id=run.id,
            task_id=task.id,
            event_type="task_reopened",
            actor_handle=actor_handle,
            target_handle=task.owner_handle,
            summary=f"{actor_handle}가 '{task.title}' 작업을 다시 열고 후속 작업 {max(len(reopened) - 1, 0)}개를 함께 재개했습니다.",
            payload={"reopened_task_ids": [str(item.id) for item in reopened]},
        )

    if _task_is_ready(team_svc, run.id, task.id):
        team_svc.update_run(run.id, status="active")
        db.commit()
        await _run_team_scheduler(db, run.id)
    else:
        _refresh_team_run_status(team_svc, run.id)
        db.commit()

    refreshed_run = team_svc.get_run(run.id)
    if not refreshed_run:
        raise HTTPException(status_code=500, detail="Failed to refresh team run")
    return _build_team_board_snapshot(db, refreshed_run)


def _extract_web_deliverable(db: Session, conversation_id: UUID) -> dict | None:
    svc = ConversationService(db)
    for artifact_type in ("final", "decision", "draft"):
        artifact = svc.get_latest_artifact(conversation_id, artifact_type)
        if artifact and artifact.content.strip():
            return {
                "artifact_type": artifact.artifact_type,
                "version": artifact.version,
                "status": artifact.status,
                "created_by_handle": artifact.created_by_handle,
                "created_at": artifact.created_at.isoformat(),
                "content": artifact.content,
            }

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

    priorities = ("manager", "writer", "coder", "planner", "critic", "reviewer")
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


def _compact_progress_text(text: str | None, max_chars: int = 90) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return "작업 진행 중"
    if len(compact) > max_chars:
        return compact[: max_chars - 1] + "…"
    return compact


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


def _default_team_tasks(
    request_text: str,
    selected_agents: list[str],
) -> list[dict]:
    lowered = request_text.lower()
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
            "title": "초안 작성",
            "description": "요청에 대한 1차 초안 또는 핵심 본문을 작성합니다.",
            "owner_handle": "writer",
            "artifact_goal": "draft",
            "depends_on_titles": ["요청 정리 및 작업 구조화"],
            "review_required": True,
            "status": "todo",
            "priority": 30,
        },
    ]
    if "coder" in selected_agents and any(keyword in lowered for keyword in ("코드", "구현", "api", "설계", "architecture", "system")):
        tasks.append(
            {
                "title": "기술 구현 포인트 정리",
                "description": "기술 구조, 구현 포인트, 코드/시스템 관점을 보강합니다.",
                "owner_handle": "coder",
                "artifact_goal": "draft",
                "depends_on_titles": ["요청 정리 및 작업 구조화"],
                "review_required": False,
                "status": "todo",
                "priority": 35,
            }
        )
    review_deps = ["초안 작성"]
    if any(task["owner_handle"] == "coder" for task in tasks):
        review_deps.append("기술 구현 포인트 정리")
    tasks.extend(
        [
            {
                "title": "내용 검토",
                "description": "초안의 품질, 누락, 근거, 리스크를 검토합니다.",
                "owner_handle": "critic",
                "artifact_goal": "review_notes",
                "depends_on_titles": review_deps,
                "review_required": False,
                "status": "todo",
                "priority": 60,
            },
            {
                "title": "최종본 정리",
                "description": "검토 내용을 반영해 최종 결과물을 정리합니다.",
                "owner_handle": "manager",
                "artifact_goal": "final",
                "depends_on_titles": ["내용 검토"],
                "review_required": False,
                "status": "todo",
                "priority": 90,
            },
        ]
    )
    return tasks


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
    return tasks


async def _decompose_team_request(
    request_text: str,
    selected_agents: list[str],
) -> tuple[str, list[dict]]:
    orchestrator._ensure_loaded()
    planner = orchestrator._agents.get("planner")
    if not planner:
        brief = f"요청 요약: {request_text[:500]}"
        return brief, _default_team_tasks(request_text, selected_agents)

    prompt = (
        "다음 요청을 실무형 팀 작업으로 분해하세요.\n\n"
        f"요청:\n{request_text}\n\n"
        f"허용 담당자: {', '.join(selected_agents)}\n"
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
    if not tasks:
        tasks = _default_team_tasks(request_text, selected_agents)
    return brief, tasks


def _build_team_board_snapshot(db: Session, run: conversation_models.TeamRunModel) -> dict:
    team_svc = TeamRunService(db)
    conv_svc = ConversationService(db)
    tasks = team_svc.list_tasks(run.id)
    dependencies = team_svc.list_dependencies(run.id)
    deps_by_task: dict[str, list[str]] = {}
    for dep in dependencies:
        deps_by_task.setdefault(str(dep.team_task_id), []).append(str(dep.depends_on_task_id))
    tasks_by_id = {str(task.id): task for task in tasks}
    done_ids = {str(task.id) for task in tasks if task.status == "done"}
    conv = conv_svc.get_conversation(run.conversation_id) if run.conversation_id else None
    messages = conv_svc.list_messages(run.conversation_id, limit=200) if run.conversation_id else []
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
    serialized_tasks = []
    for task in tasks:
        serialized_tasks.append(
            _serialize_team_task_snapshot(
                task=task,
                deps_by_task=deps_by_task,
                tasks_by_id=tasks_by_id,
                done_ids=done_ids,
                artifacts_by_task=artifacts_by_task,
                activity_by_task=activity_by_task,
            )
        )
    deliverable = _extract_web_deliverable(db, run.conversation_id) if run.conversation_id else None
    return {
        "run": serialize_team_run(run),
        "conversation": serialize_conversation(conv) if conv else None,
        "items": [serialize_message(msg) for msg in messages],
        "tasks": serialized_tasks,
        "dependencies": [serialize_team_dependency(dep) for dep in dependencies],
        "activity": [serialize_team_activity(event) for event in activity],
        "artifacts": [serialize_artifact(artifact) for artifact in artifacts],
        "deliverable": deliverable,
    }


def _serialize_team_task_snapshot(
    *,
    task: conversation_models.TeamTaskModel,
    deps_by_task: dict[str, list[str]],
    tasks_by_id: dict[str, conversation_models.TeamTaskModel],
    done_ids: set[str],
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
    has_review_notes = any(artifact.artifact_type == "review_notes" for artifact in task_artifacts)
    latest_review_event = next(
        (
            event
            for event in reversed(task_events)
            if event.event_type in {"review_approved", "review_rejected"}
        ),
        None,
    )

    item["depends_on_task_ids"] = dep_ids
    item["depends_on_titles"] = [
        tasks_by_id[dep_id].title
        for dep_id in dep_ids
        if dep_id in tasks_by_id
    ]
    item["ready"] = task.status == "todo" and set(dep_ids).issubset(done_ids)
    item["artifact_count"] = len(task_artifacts)
    item["latest_artifact_type"] = latest_artifact.artifact_type if latest_artifact else None
    item["latest_artifact_created_at"] = latest_artifact.created_at.isoformat() if latest_artifact else None
    item["latest_activity_type"] = latest_event.event_type if latest_event else None
    item["latest_activity_at"] = latest_event.created_at.isoformat() if latest_event else None
    item["has_review_notes"] = has_review_notes
    if latest_review_event and latest_review_event.event_type == "review_approved":
        item["review_state"] = "approved"
    elif latest_review_event and latest_review_event.event_type == "review_rejected":
        item["review_state"] = "rejected"
    elif has_review_notes:
        item["review_state"] = "reviewed"
    elif task.review_required:
        item["review_state"] = "required"
    else:
        item["review_state"] = "not_required"
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
        task=task,
        deps_by_task=deps_by_task,
        tasks_by_id=tasks_by_id,
        done_ids=done_ids,
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
    if result is not None:
        visible = str(getattr(result, "visible_message", "") or "").strip()
    if task.artifact_goal == "brief":
        return (
            f"요청 개요\n"
            f"- 제목: {run.title}\n"
            f"- 요청자: {run.requested_by}\n"
            f"- 요청: {run.request_text}\n\n"
            f"작업 메모\n{visible or task.description}"
        ).strip()
    if task.artifact_goal == "review_notes":
        base = visible or task.description
        return f"검토 메모\n- 작업: {task.title}\n- 담당: {task.owner_handle}\n- 메모: {base}"
    if task.artifact_goal == "final":
        draft_text = "\n\n".join(draft_bodies) if draft_bodies else "- 초안 없음"
        review_text = "\n\n".join(review_bodies) if review_bodies else "- 검토 메모 없음"
        return (
            f"# {run.title or '최종 결과물'}\n\n"
            f"요청\n{run.request_text}\n\n"
            f"핵심 결과\n{draft_text}\n\n"
            f"검토 요약\n{review_text}\n"
        ).strip()
    return (
        f"{task.title}\n\n"
        f"{visible or task.description}\n\n"
        f"원요청: {run.request_text}"
    ).strip()


async def _execute_team_task(
    db: Session,
    run: conversation_models.TeamRunModel,
    task: conversation_models.TeamTaskModel,
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
    context = "\n\n".join(
        part
        for part in (
            f"팀 실행 제목: {run.title}",
            f"원요청: {run.request_text}",
            f"현재 작업: {task.title}",
            f"작업 설명: {task.description}",
            "기존 작업공간:\n" + "\n\n".join(artifact_lines) if artifact_lines else "",
        )
        if part
    )
    user_request = (
        f"팀 작업을 수행하세요.\n"
        f"- 작업명: {task.title}\n"
        f"- 담당자: {task.owner_handle}\n"
        f"- 목표 산출물: {task.artifact_goal}\n"
        f"- 작업 설명: {task.description}\n"
        f"- 원요청: {run.request_text}"
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

    artifact_update = result.artifact_update or {}
    artifact_type = str(artifact_update.get("type") or task.artifact_goal or "draft").strip().lower()
    artifact_content = str(artifact_update.get("content") or "").strip()
    if not artifact_content:
        artifact_content = _fallback_task_artifact_content(
            task=task,
            run=run,
            result=result,
            draft_bodies=draft_bodies,
            review_bodies=review_bodies,
        )
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
        output=visible_message,
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
    if artifact.artifact_type == "final":
        team_svc.update_run(run.id, final_artifact_id=artifact.id)
        conv.export_ready = True
        conv.done = True
        conv.status = "idle"
        conv.updated_at = now_utc()
    db.commit()
    return artifact, None


async def _run_team_scheduler(
    db: Session,
    team_run_id: uuid.UUID,
) -> conversation_models.TeamRunModel | None:
    team_svc = TeamRunService(db)
    run = team_svc.get_run(team_run_id)
    if not run:
        return None

    team_svc.update_run(run.id, status="running")
    db.commit()

    while True:
        run = team_svc.get_run(team_run_id)
        if not run:
            return None
        ready = team_svc.ready_tasks(run.id)
        if not ready:
            break
        for task in ready:
            team_svc.update_task(task.id, status="in_progress")
            team_svc.create_activity(
                team_run_id=run.id,
                task_id=task.id,
                event_type="task_started",
                actor_handle=task.owner_handle,
                summary=f"{task.owner_handle}가 '{task.title}' 작업을 시작했습니다.",
            )
            db.commit()

            artifact, error = await _execute_team_task(db, run, task)
            if error:
                team_svc.update_task(task.id, status="blocked")
                team_svc.update_run(run.id, status="blocked")
                team_svc.create_activity(
                    team_run_id=run.id,
                    task_id=task.id,
                    event_type="task_blocked",
                    actor_handle=task.owner_handle,
                    summary=f"{task.owner_handle}가 '{task.title}' 작업 중 오류로 중단되었습니다: {error}",
                )
                db.commit()
                return team_svc.get_run(team_run_id)

            completed = team_svc.update_task(task.id, status="done")
            summary = f"{task.owner_handle}가 '{task.title}' 작업을 완료했습니다."
            if artifact and artifact.artifact_type == "review_notes":
                summary = f"{task.owner_handle}가 '{task.title}' 검토를 완료했습니다."
            if artifact and artifact.artifact_type == "final":
                summary = f"{task.owner_handle}가 최종 결과물을 정리했습니다."
            team_svc.create_activity(
                team_run_id=run.id,
                task_id=task.id,
                event_type="final_published" if artifact and artifact.artifact_type == "final" else "task_completed",
                actor_handle=task.owner_handle,
                summary=summary,
                payload={"artifact_id": str(artifact.id)} if artifact else None,
            )
            if completed:
                db.commit()

    run = team_svc.get_run(team_run_id)
    if not run:
        return None
    _refresh_team_run_status(team_svc, run.id)
    db.commit()
    return team_svc.get_run(team_run_id)


def _refresh_team_run_status(team_svc: TeamRunService, team_run_id: uuid.UUID) -> None:
    run = team_svc.get_run(team_run_id)
    if not run:
        return
    tasks = team_svc.list_tasks(run.id)
    if tasks and all(task.status == "done" for task in tasks):
        team_svc.update_run(run.id, status="done")
    elif any(task.status == "blocked" for task in tasks):
        team_svc.update_run(run.id, status="blocked")
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


def _task_is_ready(team_svc: TeamRunService, run_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    return any(task.id == task_id for task in team_svc.ready_tasks(run_id))


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
        updated = team_svc.update_task(current_id, status="todo")
        if updated:
            reopened.append(updated)
    reopened.sort(key=lambda item: (item.priority, item.created_at))
    return reopened


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
        raw_selected = ["planner", "writer", "critic", "manager", "coder"]
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
