import threading
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
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
    JobDetailResponse,
    PromptLogSummary,
    TaskSummary,
)
from app.services.job_dispatcher import dispatch_job
from app.services.llm_router import get_llm_provider
from app.services.planner_agent import PlannerAgent

router = APIRouter()


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
    try:
        plan: PlanResult = await planner.plan(payload.request, payload.output_types)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"문서 계획 생성에 실패했습니다. 잠시 후 다시 시도해 주세요. ({exc})") from exc
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
