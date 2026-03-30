import re
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.storage_paths import absolute_storage_path
from app.models import FileModel, ProjectModel
from app.services.document_ir import parse_document_to_ir, summarize_document_ir

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
    file_ir = parse_document_to_ir(str(absolute_storage_path(file_row.stored_path)), file_row.mime_type)
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
