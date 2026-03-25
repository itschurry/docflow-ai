"""Vector store 추상화 및 SQLite 기반 구현.

인터페이스:
  VectorStore.add(chunks, file_id, project_id, db)
  VectorStore.search(query, project_id, source_file_ids, top_k, db) -> list[ScoredChunk]

구현체:
  SQLiteKeywordStore – SQLite document_chunks 테이블에서
                       TF 기반 키워드 매칭으로 유사 청크를 검색한다.
                       (외부 embedding API 없이 동작하는 최소 구현)
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunkModel
from app.services.chunking_service import Chunk


@dataclass
class ScoredChunk:
    chunk: DocumentChunkModel
    score: float


class VectorStore(Protocol):
    def add(
        self,
        chunks: list[Chunk],
        *,
        file_id: uuid.UUID,
        project_id: uuid.UUID,
        db: Session,
    ) -> int: ...

    def search(
        self,
        query: str,
        *,
        project_id: uuid.UUID,
        source_file_ids: list[uuid.UUID] | None,
        top_k: int,
        db: Session,
    ) -> list[ScoredChunk]: ...


def _tokenize(text: str) -> list[str]:
    """간단한 토큰화: 2글자 이상 어절/단어로 분리."""
    return [t for t in re.split(r"[\s\|\,\.\!\?\;\:\n]+", text.lower()) if len(t) >= 2]


def _score(query_tokens: list[str], content: str) -> float:
    """쿼리 토큰이 청크 내용에 몇 개 포함되는지 정규화 점수."""
    if not query_tokens or not content:
        return 0.0
    content_lower = content.lower()
    hits = sum(1 for t in query_tokens if t in content_lower)
    return hits / len(query_tokens)


class SQLiteKeywordStore:
    """SQLite document_chunks 테이블 기반 키워드 검색 구현."""

    def add(
        self,
        chunks: list[Chunk],
        *,
        file_id: uuid.UUID,
        project_id: uuid.UUID,
        db: Session,
    ) -> int:
        """청크 목록을 DB에 저장하고 저장된 개수를 반환한다."""
        rows = [
            DocumentChunkModel(
                file_id=file_id,
                project_id=project_id,
                file_name=c.file_name,
                document_type=c.document_type,
                section=c.section,
                chunk_index=c.chunk_index,
                page_hint=c.page_hint,
                content=c.content,
                index_status="indexed",
            )
            for c in chunks
        ]
        db.add_all(rows)
        db.flush()
        return len(rows)

    def search(
        self,
        query: str,
        *,
        project_id: uuid.UUID,
        source_file_ids: list[uuid.UUID] | None,
        top_k: int,
        db: Session,
    ) -> list[ScoredChunk]:
        """키워드 매칭으로 상위 top_k 청크를 반환한다."""
        stmt = select(DocumentChunkModel).where(
            DocumentChunkModel.project_id == project_id,
            DocumentChunkModel.index_status == "indexed",
        )
        if source_file_ids:
            stmt = stmt.where(DocumentChunkModel.file_id.in_(source_file_ids))

        rows = db.execute(stmt).scalars().all()
        query_tokens = _tokenize(query)
        scored = [
            ScoredChunk(chunk=row, score=_score(query_tokens, row.content))
            for row in rows
        ]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


# 기본 싱글턴 인스턴스 (교체 가능)
_default_store: VectorStore = SQLiteKeywordStore()


def get_vector_store() -> VectorStore:
    return _default_store


def set_vector_store(store: VectorStore) -> None:
    global _default_store
    _default_store = store
