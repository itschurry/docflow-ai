from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import JobModel, TaskModel
from app.schemas.request_response import (
    AgentStepResponse,
    JobDetailResponse,
)

router = APIRouter()


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
        progress=job.progress,
        created_by=job.created_by,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/api/jobs/{job_id}/steps", response_model=list[AgentStepResponse])
def get_job_steps(job_id: UUID, db: Session = Depends(get_db)) -> list[AgentStepResponse]:
    job = db.get(JobModel, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    tasks = db.execute(
        select(TaskModel)
        .where(TaskModel.job_id == job_id)
        .order_by(TaskModel.started_at.asc().nullsfirst(), TaskModel.id.asc())
    ).scalars().all()
    return [
        AgentStepResponse(
            id=t.id,
            job_id=t.job_id,
            step_name=t.task_type,
            status=t.status.lower(),
            started_at=t.started_at,
            finished_at=t.finished_at,
            output=t.output_payload_json or None,
            error=t.error_message,
        )
        for t in tasks
    ]
