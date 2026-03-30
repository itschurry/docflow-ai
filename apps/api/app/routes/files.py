from pathlib import Path
import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.storage_paths import absolute_storage_path, storage_path_for_db
from app.models import FileModel
from app.schemas.request_response import UploadFileResponse
from app.services.document_ir import extract_text_from_ir, parse_document_to_ir, summarize_document_ir
from app.services.indexing_service import index_file
from app.routes._shared import _ensure_web_upload_project, _file_analysis_payload

router = APIRouter()


def _store_uploaded_file(
    project_id: UUID,
    uploaded_file: UploadFile,
    db: Session,
) -> UploadFileResponse:
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
        stored_path=storage_path_for_db(stored_path),
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

    index_result = index_file(file_row, db)
    if index_result["chunk_count"] > 0:
        db.commit()

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
    return _store_uploaded_file(project.id, uploaded_file, db)


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


@router.get("/api/files/{file_id}/download")
def download_file(file_id: UUID, db: Session = Depends(get_db)) -> FileResponse:
    file_row = db.get(FileModel, file_id)
    if not file_row:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = absolute_storage_path(file_row.stored_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file not found")

    return FileResponse(
        path=str(file_path),
        media_type=file_row.mime_type,
        filename=file_row.original_name,
    )
