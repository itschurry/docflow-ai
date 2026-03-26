from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""


class CreateProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str
    created_at: datetime


class CreateJobRequest(BaseModel):
    request: str
    output_types: list[Literal["report", "excel", "ppt", "slide", "pptx", "budget", "xlsx"]
                       ] = Field(default_factory=list)


class CreateJobResponse(BaseModel):
    job_id: UUID
    project_id: UUID
    status: str
    job_type: str


class JobDetailResponse(BaseModel):
    id: UUID
    project_id: UUID
    job_type: str
    request_text: str
    status: str
    progress: int = 0
    created_by: str
    created_at: datetime
    updated_at: datetime


class AgentStepResponse(BaseModel):
    id: UUID
    job_id: UUID
    step_name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output: dict | None = None
    error: str | None = None


class UploadFileResponse(BaseModel):
    id: UUID
    project_id: UUID
    original_name: str
    mime_type: str
    size: int
    source_type: str
    document_type: str = ""
    document_summary: str = ""
    document_ir: dict = Field(default_factory=dict)
    created_at: datetime


class TaskSummary(BaseModel):
    id: UUID
    task_type: str
    status: str


class ArtifactSummary(BaseModel):
    id: UUID
    original_name: str
    stored_path: str
    source_type: str
    document_type: str = ""
    document_summary: str = ""


class PromptLogSummary(BaseModel):
    id: UUID
    task_id: UUID
    provider: str
    model: str
    created_at: datetime


class JobHistoryItem(BaseModel):
    id: UUID
    job_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectJobsResponse(BaseModel):
    project_id: UUID
    jobs: list[JobHistoryItem]


class DeadLetterItem(BaseModel):
    file_name: str
    path: str
    job_id: str
    reason: str
    retries: int
    created_at: str


class DeadLetterListResponse(BaseModel):
    items: list[DeadLetterItem]


class DeadLetterReplayRequest(BaseModel):
    file_name: str
    requeue: bool = False
    delete_on_success: bool = False
    force_requeue: bool = False


class DeadLetterReplayResponse(BaseModel):
    file_name: str
    job_id: str
    requeued: bool
    deleted: bool
    status: str
    message: str


class CreateOpsApiKeyRequest(BaseModel):
    key_id: str
    key_secret: str
    role: str = "ops"


class CreateOpsApiKeyResponse(BaseModel):
    key_id: str
    role: str
    is_active: bool


class ReplayAuditItem(BaseModel):
    at: str
    action: str
    file_name: str
    job_id: str
    actor: str
    result: str


class ReplayAuditListResponse(BaseModel):
    items: list[ReplayAuditItem]
