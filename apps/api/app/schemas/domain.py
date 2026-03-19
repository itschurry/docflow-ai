from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.core.state_machine import JobStatus


class Project(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    job_type: str
    request_text: str
    status: JobStatus = JobStatus.DRAFT
    requested_by: str = "system"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    task_type: str
    status: str = "PENDING"
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FileAsset(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    job_id: UUID | None = None
    file_type: str
    path: str
    source_type: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PromptLog(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    provider: str
    model: str
    prompt: str
    response: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
