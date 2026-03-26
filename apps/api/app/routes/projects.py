from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import JobModel, ProjectModel
from app.schemas.request_response import (
    CreateProjectRequest,
    CreateProjectResponse,
    JobHistoryItem,
    ProjectJobsResponse,
)

router = APIRouter()


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
