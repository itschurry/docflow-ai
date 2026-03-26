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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.state_machine import JobStatus
from app.core.time_utils import now_utc
from app import conversation_models
from app.models import DocumentChunkModel, FileModel, JobModel, ProjectModel, PromptLogModel, TaskModel
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
from app.services.openai_document_generator import OpenAIDocumentIRGenerator
from app.services.openai_document_generator import openai_document_generation_available
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
from app.services.indexing_service import index_file



AUTO_REVIEW_MAX_ROUNDS = 2
AUTO_REVIEW_MAX_ROUNDS_MIN = 1
AUTO_REVIEW_MAX_ROUNDS_MAX = 6
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


def _file_analysis_payload(file_row: FileModel) -> dict:
    file_ir = parse_document_to_ir(file_row.stored_path, file_row.mime_type)
    summary = summarize_document_ir(file_ir)
    return {
        "document_type": str(file_ir.get("document_type") or file_row.document_type or ""),
        "document_summary": summary or file_row.document_summary or "",
        "document_ir": file_ir,
    }


def _required_workflow_agents(mode: str | None) -> list[str]:
    normalized = (mode or "").strip().lower()
    if normalized in {"autonomous", "autonomous-lite", "team-autonomous"}:
        return ["planner", "writer", "critic", "qa", "manager"]
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
