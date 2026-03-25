"""Style extraction pipeline (TASK_04).

문서에서 문체 패턴을 추출해 style_patterns 테이블에 저장한다.
초기 구현은 LLM 호출 없이 규칙 기반으로 동작하며,
LLM 기반 배치 추출 확장 포인트를 남긴다.
"""
from __future__ import annotations

import re
import uuid
from typing import Sequence

from sqlalchemy.orm import Session

from app.models import DocumentChunkModel, StylePatternModel
from app.services.chunking_service import Chunk

# 패턴 추출 시 청크 최소 길이
MIN_CHUNK_LENGTH = 80

# 전환 표현 (transition phrases) 후보 패턴
_TRANSITION_PATTERNS = re.compile(
    r"(?:따라서|그러므로|반면에|한편|이에 따라|결론적으로|구체적으로|이를 위해)"
)

# 격식 문체 지표
_FORMAL_MARKERS = re.compile(
    r"(?:하였습니다|되었습니다|됩니다|합니다|있습니다|했습니다)"
)

# 단락 길이 분포 (문장 수 기반)
_SENTENCE_END = re.compile(r"[.。!?。！？]+\s*")


def _count_sentences(text: str) -> int:
    return max(len(_SENTENCE_END.split(text.strip())), 1)


def _extract_patterns_from_chunk(chunk: Chunk) -> list[dict]:
    """단일 청크에서 패턴 목록 추출."""
    text = chunk.content.strip()
    if len(text) < MIN_CHUNK_LENGTH:
        return []

    patterns: list[dict] = []

    # 1. 문장 구조 패턴: 평균 문장 길이
    n_sentences = _count_sentences(text)
    avg_len = len(text) // max(n_sentences, 1)
    if 40 <= avg_len <= 200:
        patterns.append({
            "pattern_type": "sentence_structure",
            "content": f"평균 문장 길이: {avg_len}자 ({n_sentences}문장)",
            "section": chunk.section,
        })

    # 2. 전환 표현
    transitions = _TRANSITION_PATTERNS.findall(text)
    if transitions:
        patterns.append({
            "pattern_type": "transition",
            "content": ", ".join(set(transitions)),
            "section": chunk.section,
        })

    # 3. 격식체 비율
    formal_hits = _FORMAL_MARKERS.findall(text)
    if formal_hits:
        ratio = len(formal_hits) / max(n_sentences, 1)
        if ratio >= 0.5:
            patterns.append({
                "pattern_type": "tone",
                "content": f"격식체 문장 비율 {ratio:.0%} — 예: {'・'.join(set(formal_hits[:3]))}",
                "section": chunk.section,
            })

    # 4. 표현 패턴: 자주 쓰이는 어절 상위 3개
    words = [w for w in re.split(r"\s+", text) if len(w) >= 3]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=freq.__getitem__, reverse=True)[:3]
    if top:
        patterns.append({
            "pattern_type": "expression",
            "content": "자주 쓰는 표현: " + ", ".join(top),
            "section": chunk.section,
        })

    return patterns


def extract_and_store_style_patterns(
    chunks: Sequence[Chunk],
    *,
    project_id: uuid.UUID,
    source_file_id: uuid.UUID | None,
    db: Session,
) -> int:
    """청크 목록에서 패턴을 추출해 style_patterns 테이블에 저장한다."""
    stored = 0
    for chunk in chunks:
        for pat in _extract_patterns_from_chunk(chunk):
            row = StylePatternModel(
                project_id=project_id,
                source_file_id=source_file_id,
                section=pat["section"],
                pattern_type=pat["pattern_type"],
                content=pat["content"],
            )
            db.add(row)
            stored += 1
    if stored:
        db.flush()
    return stored
