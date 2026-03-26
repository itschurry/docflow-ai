from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import DocumentChunkModel, FileModel
from ._shared import _ensure_web_upload_project

router = APIRouter()


@router.get("/web/knowledge", status_code=200)
def list_web_knowledge(db: Session = Depends(get_db)):
    """List all files in the web upload project with chunk counts and index status."""
    project = _ensure_web_upload_project(db)
    files = db.execute(
        select(FileModel)
        .where(FileModel.project_id == project.id)
        .order_by(FileModel.created_at.desc())
    ).scalars().all()

    result = []
    for f in files:
        chunks = db.execute(
            select(DocumentChunkModel)
            .where(DocumentChunkModel.file_id == f.id)
        ).scalars().all()
        chunk_count = len(chunks)
        failed_count = sum(1 for c in chunks if c.index_status == "failed")
        if chunk_count == 0:
            index_status = "not_indexed"
        elif failed_count == chunk_count:
            index_status = "failed"
        else:
            index_status = "indexed"
        result.append({
            "id": str(f.id),
            "original_name": f.original_name,
            "mime_type": f.mime_type,
            "document_type": f.document_type or "",
            "document_summary": f.document_summary or "",
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "chunk_count": chunk_count,
            "index_status": index_status,
        })
    return {"items": result}


@router.get("/web/knowledge/{file_id}/chunks", status_code=200)
def list_web_knowledge_chunks(file_id: UUID, db: Session = Depends(get_db)):
    """List document chunks for a specific file (for evidence panel)."""
    chunks = db.execute(
        select(DocumentChunkModel)
        .where(DocumentChunkModel.file_id == file_id)
        .order_by(DocumentChunkModel.chunk_index)
    ).scalars().all()
    return {
        "items": [
            {
                "id": str(c.id),
                "section": c.section or "",
                "chunk_index": c.chunk_index,
                "content": c.content[:500],
                "index_status": c.index_status,
            }
            for c in chunks
        ]
    }


@router.delete("/web/knowledge/{file_id}", status_code=200)
def delete_web_knowledge_file(file_id: UUID, db: Session = Depends(get_db)):
    """Delete a knowledge file and all its chunks from the web upload project."""
    file_row = db.get(FileModel, file_id)
    if not file_row:
        raise HTTPException(status_code=404, detail="File not found")
    # Delete associated chunks first
    chunks = db.execute(
        select(DocumentChunkModel).where(DocumentChunkModel.file_id == file_id)
    ).scalars().all()
    for chunk in chunks:
        db.delete(chunk)
    db.delete(file_row)
    db.commit()
    return {"ok": True, "deleted_id": str(file_id)}
