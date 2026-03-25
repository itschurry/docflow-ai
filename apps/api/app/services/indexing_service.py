"""파일 인덱싱 파이프라인.

파일이 업로드될 때 호출되어 문서를 청킹하고 document_chunks에 저장한다.
파싱/청킹 실패가 업로드 전체를 막지 않도록 try/except로 분리한다.
style_extractor를 통해 style_patterns도 함께 추출한다.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import DocumentChunkModel, FileModel, StylePatternModel
from app.services.chunking_service import chunk_from_ir, chunk_from_text
from app.services.document_ir import extract_text_from_ir, parse_document_to_ir
from app.services.style_extractor import extract_and_store_style_patterns
from app.services.vector_store import get_vector_store

log = logging.getLogger(__name__)


def index_file(file_row: FileModel, db: Session) -> dict:
    """FileModel을 청킹하고 document_chunks에 저장한다.

    Returns:
        {"status": "indexed"|"failed"|"skipped", "chunk_count": int, "error": str|None}
    """
    try:
        # 기존 청크 삭제 (재인덱싱 지원)
        db.execute(
            delete(DocumentChunkModel).where(
                DocumentChunkModel.file_id == file_row.id
            )
        )

        # 문서 파싱 → IR
        if file_row.stored_path:
            try:
                ir = parse_document_to_ir(file_row.stored_path, file_row.mime_type)
                chunks = chunk_from_ir(ir, file_name=file_row.original_name)
            except Exception as parse_err:
                log.warning("IR parsing failed for %s: %s", file_row.id, parse_err)
                chunks = []

        # IR 청킹 실패 시 extracted_text fallback
        if not chunks:
            text = file_row.extracted_text or ""
            if text:
                chunks = chunk_from_text(
                    text,
                    file_name=file_row.original_name,
                    document_type=file_row.document_type or "",
                )

        if not chunks:
            return {"status": "skipped", "chunk_count": 0, "error": None}

        store = get_vector_store()
        count = store.add(
            chunks,
            file_id=file_row.id,
            project_id=file_row.project_id,
            db=db,
        )

        # style pattern 추출 (업로드 파일이 source 문서인 경우)
        try:
            extract_and_store_style_patterns(
                chunks,
                project_id=file_row.project_id,
                source_file_id=file_row.id,
                db=db,
            )
        except Exception as style_err:
            log.warning("Style extraction failed for %s: %s", file_row.id, style_err)

        return {"status": "indexed", "chunk_count": count, "error": None}

    except Exception as exc:
        log.error("Indexing failed for file %s: %s", file_row.id, exc)
        return {"status": "failed", "chunk_count": 0, "error": str(exc)}
