"""RAG 검색 및 컨텍스트 빌더.

공개 API:
  build_rag_context(query, project_id, source_file_ids, top_k, db)
    -> RagResult(context_text, retrieval_status, sources)

RetrievalStatus:
  OK      – 충분한 검색 결과 (top_k 중 절반 이상, 점수 ≥ WEAK_THRESHOLD)
  WEAK    – 결과는 있으나 점수가 낮거나 개수 부족
  EMPTY   – 검색 결과 없음
  CONFLICT – 동일 쿼리에 대해 서로 모순된 내용의 청크가 감지됨
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from sqlalchemy.orm import Session

from app.services.vector_store import ScoredChunk, get_vector_store

# 점수 임계값
WEAK_THRESHOLD = 0.15
MIN_OK_RATIO = 0.5      # top_k 중 이 비율 이상이 WEAK_THRESHOLD 초과여야 OK
CONFLICT_MIN_SCORE = 0.2  # conflict 감지 대상 최소 점수


class RetrievalStatus(str, Enum):
    OK = "OK"
    WEAK = "WEAK"
    EMPTY = "EMPTY"
    CONFLICT = "CONFLICT"


@dataclass
class RagSource:
    file_name: str
    section: str
    chunk_index: int
    score: float


@dataclass
class RagResult:
    context_text: str
    retrieval_status: RetrievalStatus
    sources: list[RagSource] = field(default_factory=list)
    chunk_count: int = 0


def _detect_conflict(hits: list[ScoredChunk]) -> bool:
    """상위 청크 중 서로 다른 파일에서 동일 섹션 내용이 상충하는지 감지.

    단순 휴리스틱: 동일 section 이름을 가진 청크가 서로 다른 파일에서
    왔고 내용 길이 비율이 크게 다를 때 CONFLICT로 표시한다.
    """
    eligible = [h for h in hits if h.score >= CONFLICT_MIN_SCORE]
    by_section: dict[str, list[ScoredChunk]] = {}
    for h in eligible:
        section = h.chunk.section or ""
        by_section.setdefault(section, []).append(h)

    for section, items in by_section.items():
        if not section:
            continue
        file_ids = {str(item.chunk.file_id) for item in items}
        if len(file_ids) > 1:
            lengths = [len(item.chunk.content) for item in items]
            if max(lengths) > 0 and min(lengths) / max(lengths) < 0.3:
                return True
    return False


def _compute_status(hits: list[ScoredChunk], top_k: int) -> RetrievalStatus:
    if not hits:
        return RetrievalStatus.EMPTY
    if _detect_conflict(hits):
        return RetrievalStatus.CONFLICT
    strong = [h for h in hits if h.score >= WEAK_THRESHOLD]
    if len(strong) / max(top_k, 1) >= MIN_OK_RATIO:
        return RetrievalStatus.OK
    return RetrievalStatus.WEAK


def build_rag_context(
    query: str,
    *,
    project_id: uuid.UUID,
    source_file_ids: list[uuid.UUID] | None = None,
    top_k: int = 5,
    db: Session,
) -> RagResult:
    """RAG 파이프라인 진입점.

    Args:
        query: 검색 쿼리 (사용자 요청 또는 task 지시)
        project_id: 프로젝트 스코프
        source_file_ids: None이면 전체 프로젝트 범위
        top_k: 최대 청크 수
        db: SQLAlchemy 세션

    Returns:
        RagResult (context_text, retrieval_status, sources)
    """
    store = get_vector_store()
    hits = store.search(
        query,
        project_id=project_id,
        source_file_ids=source_file_ids,
        top_k=top_k,
        db=db,
    )

    status = _compute_status(hits, top_k)
    sources: list[RagSource] = []
    context_parts: list[str] = []

    for i, hit in enumerate(hits, 1):
        chunk = hit.chunk
        label = f"[출처 {i}: {chunk.file_name or '문서'}"
        if chunk.section:
            label += f" / {chunk.section}"
        label += "]"
        context_parts.append(f"{label}\n{chunk.content}")
        sources.append(RagSource(
            file_name=chunk.file_name,
            section=chunk.section,
            chunk_index=chunk.chunk_index,
            score=hit.score,
        ))

    context_text = "\n\n".join(context_parts)
    return RagResult(
        context_text=context_text,
        retrieval_status=status,
        sources=sources,
        chunk_count=len(hits),
    )
